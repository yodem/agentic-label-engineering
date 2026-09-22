import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys

import ale.cli as cli
from ale.bake import bake, skeleton_label
from ale.cli import main
from ale.events import read_events
from ale.planparse import parse_plan


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "tests", "fixtures", "plans", "superpowers.md")


def _roster(tmp_path, judge_command=None):
    path = tmp_path / "roster.json"
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    if judge_command is not None:
        roster["judge"]["command"] = judge_command
    path.write_text(json.dumps(roster))
    return str(path)


def _valid_plan(tmp_path, roster_path):
    source = (tmp_path / "plan.md")
    source.write_text(open(PLAN, encoding="utf-8").read())
    with open(roster_path, encoding="utf-8") as handle:
        roster = json.load(handle)
    labels = {}
    for task in parse_plan(source.read_text()):
        votes = {"roster": roster}
        for field, value in (("role", "backend"), ("model_tier", "standard"),
                             ("risk", "low"), ("effort", "M")):
            votes[field] = {"value": value, "by": "planner", "confidence": None, "votes": []}
        label = skeleton_label(task, "plan-run", votes)
        label["labels"]["lane"] = "inline"
        label["provenance"]["lane_reason"] = "The human remains available for this task."
        label["acceptance"].append({"id": "A2", "cmd": "true", "expect": "exit0"})
        labels[task["task_id"]] = label
    source.write_text(bake(source.read_text(), labels))
    return source


def test_plan_parse_json_prints_skeletons(tmp_path, capsys):
    assert main(["plan", "parse", PLAN, "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert [task["task_id"] for task in parsed] == ["T1", "T2", "T3"]


def test_bake_diff_mode_writes_nothing_and_reports_gaps(tmp_path, capsys):
    plan = tmp_path / "plan.md"
    original = open(PLAN, encoding="utf-8").read()
    plan.write_text(original)
    roster = _roster(tmp_path)

    assert main(["plan", "bake", str(plan), "--no-judge", "--roster", roster]) == 1
    assert plan.read_text() == original
    output = capsys.readouterr().out
    assert "---" in output and "lane" in output


def test_bake_write_is_idempotent(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    first = plan.read_text()
    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    assert plan.read_text() == first


def test_compile_refuses_gaps_without_partial_labels(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)
    run_dir = tmp_path / "run"

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 1
    assert not (run_dir / "labels").exists()


def test_compile_writes_all_labels_after_checks(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 0
    assert sorted(path.name for path in (run_dir / "labels").glob("*.json")) == ["T1.json", "T2.json", "T3.json"]


def test_init_run_plan_records_plan_hash(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"

    assert main(["init-run", "--plan", str(plan), "--run-dir", str(run_dir),
                 "--roster", roster, "--now", "7"]) == 0
    started = read_events(str(run_dir / "events.jsonl"))[0]
    assert started["type"] == "run_started"
    assert started["plan_sha256"] == hashlib.sha256(plan.read_bytes()).hexdigest()


def test_shadow_file_is_allowed_only_when_file_is_ignored(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    marker = repo / "judge-input.txt"
    judge = repo / "fake-judge.py"
    judge.write_text("""import json, sys
state = sys.stdin.read()
with open(sys.argv[1], 'a', encoding='utf-8') as handle:
    handle.write(state + '\\n')
options = sys.argv[4:]
print(json.dumps({'choice': options[0], 'confidence': 0.9,
                  'probabilities': {option: 0.9 if index == 0 else 0.1
                                    for index, option in enumerate(options)}}))
""")
    roster = _roster(tmp_path, [sys.executable, str(judge), str(marker)])
    plan = _valid_plan(repo, roster)
    monkeypatch.chdir(repo)
    shadow_root = repo / ".ale" / "shadow"
    expected_shadow = shadow_root / ("%s-%s.jsonl" % (
        plan.stem, hashlib.sha256(os.path.abspath(str(plan)).encode("utf-8")).hexdigest()[:8]))

    assert main(["plan", "bake", str(plan), "--judge", "--roster", roster]) == 1
    assert "git-ignored" in capsys.readouterr().err
    assert not marker.exists()
    assert not expected_shadow.exists()

    assert main(["setup"]) == 0
    capsys.readouterr()
    assert main(["plan", "bake", str(plan), "--judge", "--write", "--roster", roster]) == 0
    assert expected_shadow.exists()
    assert "Add the todo model" in marker.read_text(encoding="utf-8")
    monkeypatch.undo()


def test_plan_bake_is_deterministic_and_judge_is_opt_in(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    roster = _roster(tmp_path, ["false"])
    plan = _valid_plan(repo, roster)
    monkeypatch.setattr(cli, "CommandJudge", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("external judge must be opt-in")))

    assert main(["plan", "bake", str(plan), "--write", "--roster", roster]) == 0
    assert not (repo / ".ale" / "shadow").exists()
    sidecar = plan.with_name(plan.name + ".ale-provenance.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))

    def strings(value):
        if isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
        elif isinstance(value, str):
            yield value

    assert not [value for value in strings(payload) if os.path.isabs(value)]


def test_compile_preserves_existing_plan_labels(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    before = plan.read_bytes()
    run_dir = tmp_path / "run"

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 0
    assert plan.read_bytes() == before


def test_plan_compile_rejects_unsafe_run_id(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "../unsafe",
                 "--roster", roster]) == 2
    assert not (run_dir / "labels").exists()
    plan.write_text(plan.read_text().replace('"task_id": "T1"',
                                             '"run_id": "../unsafe",\n "task_id": "T1"', 1))
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "safe-run",
                 "--roster", roster]) == 1


def test_bake_accepts_run_id(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    for command, run_dir in (("plan compile", tmp_path / "compile"), ("init-run", tmp_path / "init")):
        args = (["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "custom-run",
                 "--roster", roster] if command == "plan compile" else
                ["init-run", "--plan", str(plan), "--run-dir", str(run_dir), "--run-id", "custom-run",
                 "--roster", roster, "--now", "7"])
        assert main(args) == 0
        assert {json.loads(path.read_text())["run_id"] for path in (run_dir / "labels").glob("*.json")} == {"custom-run"}


def test_plan_compile_failure_does_not_replace_existing_label_directory(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir / "labels").mkdir(parents=True)
    marker = run_dir / "labels" / "keep.json"
    marker.write_text("keep")

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 1
    assert marker.read_text() == "keep"


def _add_unknown_context_key(plan):
    text = plan.read_text()
    plan.write_text(text.replace(' "task_id": "T1",', ' "context": {},\n "task_id": "T1",', 1))


def test_plan_bake_diff_reports_unknown_compact_key(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    _add_unknown_context_key(plan)

    assert main(["plan", "bake", str(plan), "--no-judge", "--roster", roster]) == 1
    assert "T1: unknown key 'context'" in capsys.readouterr().err


def test_plan_bake_write_reports_unknown_compact_key_without_rewriting(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    _add_unknown_context_key(plan)
    before = plan.read_bytes()

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    assert "T1: unknown key 'context'" in capsys.readouterr().err
    assert plan.read_bytes() == before


def test_plan_bake_write_preserves_handwritten_dependency(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    text = plan.read_text()
    text = text.replace("Consumes: Task 1", "Consumes: no earlier task")
    text = text.replace("After Task 2", "Update the guide")
    text = text.replace('"depends_on": [],', '"depends_on": ["T3"],', 1)
    plan.write_text(text)

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 0
    from ale.bake import extract_blocks
    blocks = dict((label["task_id"], label) for _, label in extract_blocks(plan.read_text()))
    assert blocks["T1"]["depends_on"] == ["T3"]


def test_plan_bake_write_adds_dependency_found_in_prose(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    text = plan.read_text()
    text = text.replace("Consumes: Task 1", "Consumes: no earlier task")
    text = text.replace("After Task 2", "Update the guide")
    text = text.replace("Create the model and its tests.", "Create the model and its tests.\nDepends on Task 2")
    plan.write_text(text)
    assert parse_plan(text)[0]["depends_on"] == ["T2"]

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 0
    from ale.bake import extract_blocks
    blocks = dict((label["task_id"], label) for _, label in extract_blocks(plan.read_text()))
    assert blocks["T1"]["depends_on"] == ["T2"]


def test_plan_compile_reports_unknown_compact_key(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    _add_unknown_context_key(plan)
    run_dir = tmp_path / "run"

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 1
    assert "T1: unknown key 'context'" in capsys.readouterr().err
    assert not (run_dir / "labels").exists()
