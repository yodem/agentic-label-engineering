import json
from pathlib import Path

import pytest

from ale.bake import bake, compile_plan, extract_blocks, skeleton_label
from ale.cli import main
from ale.planparse import PlanParseError, parse_plan


def _label(task_id="T1", role="backend", tier="standard", title=None):
    return skeleton_label(
        {"task_id": task_id, "title": title or "Task " + task_id, "body": "", "files": [],
         "commands": [], "depends_on": [], "line": 1},
        "run-1",
        {"role": {"value": role}, "model_tier": {"value": tier},
         "effort": {"value": "M"}, "risk": {"value": "low"}},
    )


def test_one_task_plan_bakes_and_compiles():
    text = "## Task 1: Only task\nDetails.\n"
    label = _label(title="Only task")
    baked = bake(text, {"T1": label})

    assert baked.count("```ale-label") == 1
    assert compile_plan(baked)["T1"]["title"] == "Only task"


def test_task_heading_prevents_fallback_to_checklist():
    text = "## Task 1: Real task\n- [ ] not a task\n- [ ] also not a task\n"
    result = bake(text, {"T1": _label(title="Real task")})

    assert result.count("```ale-label") == 1
    assert '"task_id": "T1"' in result


def test_label_block_without_task_heading_reports_expected_form():
    text = "Some plan\n```ale-label\n{\"task_id\":\"T1\"}\n```\n"
    with pytest.raises(PlanParseError, match=r"## Task N:"):
        compile_plan(text)


def test_checkbox_fallback_parses_titles_without_index_error():
    assert [task["title"] for task in parse_plan("- [ ] first\n- [ ] second\n")] == [
        "first", "second"
    ]


def test_write_merges_missing_fields_into_existing_handwritten_block():
    handwritten = {"task_id": "T1", "title": "Human title", "labels": {"role": "docs"},
                   "lane_reason": "Human chose docs", "custom": "keep me"}
    # Existing compact blocks can carry only supported fields.
    handwritten.pop("custom")
    text = "## Task 1: Original\n```ale-label\n%s\n```\n" % json.dumps(handwritten)
    result = bake(text, {"T1": _label()})
    blocks = [block for _, block in extract_blocks(result)]

    assert len(blocks) == 1
    assert blocks[0]["title"] == "Human title"
    assert blocks[0]["labels"]["role"] == "docs"
    assert blocks[0]["labels"]["model_tier"] == "standard"
    assert blocks[0]["lane_reason"] == "Human chose docs"


def test_executor_assignment_matches_label_role_and_tier():
    task = {"task_id": "T1", "title": "Task", "body": "", "files": [], "commands": [],
            "depends_on": [], "line": 1}
    label = skeleton_label(task, "run-1", {"labels": {"role": "backend", "model_tier": "frontier"},
                                             "role": "test", "model_tier": "cheap"})

    assert label["assignments"][0]["role"] == label["labels"]["role"] == "backend"
    assert label["assignments"][0]["model_tier"] == label["labels"]["model_tier"] == "frontier"


def test_hand_written_block_then_init_run(tmp_path):
    plan = tmp_path / "plan.md"
    run_dir = tmp_path / "run"
    roster = tmp_path / "roster.json"
    source_roster = Path(__file__).parents[1] / "examples" / "roster.json"
    roster.write_text(source_roster.read_text())
    handwritten = {
        "task_id": "T1",
        "title": "Hand written task",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "M", "locality": "any", "phase": "implement"},
        "lane_reason": "This task is small and straightforward.",
        "allowed_paths": ["src/example.py"],
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "true", "expect": "exit0"}],
    }
    plan.write_text("## Task 1: Hand written task\nDetails.\n```ale-label\n%s\n```\n" %
                    json.dumps(handwritten))

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", str(roster)]) == 0
    assert len(extract_blocks(plan.read_text())) == 1
    assert main(["init-run", "--plan", str(plan), "--run-dir", str(run_dir),
                 "--roster", str(roster), "--now", "7"]) == 0
    compiled = json.loads((run_dir / "labels" / "T1.json").read_text())

    assert compiled["assignments"][0]["role"] == compiled["labels"]["role"] == "backend"
    assert compiled["assignments"][0]["model_tier"] == compiled["labels"]["model_tier"] == "standard"
