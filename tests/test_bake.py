import hashlib
import json

import pytest

from ale.bake import BakeError, bake, compile_plan, extract_blocks, gaps, render_block, skeleton_label
from ale.labeling.merge import LaneVoteError


def _votes(roster, **values):
    result = {"roster": roster}
    for field, value in values.items():
        result[field] = {"value": value, "by": "rule:0", "confidence": 1.0, "votes": []}
    return result


def _task(task_id="T1", title="Add the todo model"):
    return {"task_id": task_id, "title": title, "body": "Use the model.",
            "files": ["todo/model.py", "tests/"], "commands": ["python -m pytest"],
            "depends_on": [], "line": 1}


def _label(roster, task_id="T1"):
    return skeleton_label(_task(task_id), "run-1", _votes(
        roster, role="backend", effort="M", risk="low", model_tier="standard"))


def test_skeleton_label_has_paths_acceptance_assignments_and_worktree(roster):
    label = _label(roster)

    assert label["context"]["allowed_paths"] == ["todo/model.py", "tests/*"]
    assert label["acceptance"] == [{"id": "A1", "cmd": "python -m pytest", "expect": "exit0"}]
    assert label["context"]["worktree"] == {
        "mode": "per_task", "branch": None, "base": None, "worktree_reason": None
    }
    assert label["assignments"] == [{
        "kind": "executor", "role": "backend", "model_tier": "standard",
        "executor": None, "trigger": "ready",
    }]


def test_skeleton_caps_acceptance_at_five_and_preserves_dependencies(roster):
    task = _task()
    task["commands"] = ["cmd-%d" % i for i in range(7)]
    task["depends_on"] = ["T0"]
    label = skeleton_label(task, "run-1", _votes(
        roster, role="backend", effort="M", risk="low", model_tier="standard"))

    assert [entry["id"] for entry in label["acceptance"]] == ["A1", "A2", "A3", "A4", "A5"]
    assert label["context"]["depends_on"] == ["T0"]


def test_render_block_sorts_keys_and_extracts_label():
    label = {"task_id": "T1", "title": "Todo", "labels": {"role": "backend"}}
    rendered = render_block(label)

    assert rendered.startswith("```ale-label\n{\n \"labels\":")
    assert extract_blocks("before\n" + rendered)[0] == (2, label)


def test_bake_is_idempotent_and_preserves_non_block_lines(roster):
    labels = {"T1": _label(roster, "T1"), "T2": _label(roster, "T2")}
    text = "# Todo app\n\n## Task 1: Model\nDetails\n\n## Task 2: Screen\nMore\n"
    baked = bake(text, labels)

    def non_blocks(value):
        return "\n".join(line for line in value.splitlines() if not line.startswith("```")
                         and not (line.startswith("{") or line.startswith(" ")
                                  or line.startswith("}")))

    assert bake(baked, labels) == baked
    assert hashlib.sha256(non_blocks(text).encode()).hexdigest() == hashlib.sha256(
        non_blocks(baked).encode()).hexdigest()


def test_bake_replaces_a_block_in_place(roster):
    old = _label(roster)
    new = dict(old)
    new["title"] = "Updated title"
    text = "## Task 1: Model\n" + render_block(old) + "Details\n## Task 2: Screen\n"

    baked = bake(text, {"T1": new, "T2": _label(roster, "T2")})

    assert baked.index("```ale-label") == text.index("```ale-label")
    assert baked.index("Updated title") < baked.index("Details")
    assert baked.count("```ale-label") == 2


def test_compile_round_trip_returns_input_labels(roster):
    labels = {"T1": _label(roster), "T2": _label(roster, "T2")}
    text = "## Task 1: Model\n## Task 2: Screen\n"
    provenance = {task_id: {"provenance": label["provenance"], "routing": label["routing"]}
                  for task_id, label in labels.items()}

    assert compile_plan(bake(text, labels), provenance=provenance) == labels


def test_malformed_block_reports_opening_line():
    with pytest.raises(BakeError, match="line 3"):
        extract_blocks("# Plan\n\n```ale-label\n{bad json}\n````\n")


def test_compile_requires_a_block_for_every_task(roster):
    with pytest.raises(BakeError, match="T2"):
        compile_plan("## Task 1: Model\n" + render_block(_label(roster)) +
                     "## Task 2: Screen\n")


def test_gaps_names_lane_lane_reason_acceptance_role_and_paths(roster):
    label = _label(roster)
    label["labels"]["lane"] = None
    label["provenance"]["lane_reason"] = None
    label["acceptance"] = []
    label["context"]["allowed_paths"] = []
    label["labels"]["role"] = None
    label["provenance"]["role"] = {"votes": [{"by": "rule:0", "value": None}]}

    missing = gaps(label)

    assert missing == ["lane", "lane_reason", "acceptance", "role", "allowed_paths"]


def test_gaps_do_not_list_worktree(roster):
    label = _label(roster)

    assert "worktree" not in gaps(label)


def test_lane_vote_is_never_filled(roster):
    with pytest.raises(LaneVoteError):
        skeleton_label(_task(), "run-1", dict(_votes(
            roster, role="backend", effort="M", risk="low", model_tier="standard"),
            lane={"value": "pane", "by": "rule:0"}))


def test_directory_entries_become_globs(roster):
    task = _task()
    task["files"] = ["src/auth", "README.md", "assets/"]

    assert skeleton_label(task, "run-1", _votes(
        roster, role="backend", effort="M", risk="low", model_tier="standard"
    ))["context"]["allowed_paths"] == ["src/auth/*", "README.md", "assets/*"]


def test_nested_label_fence_is_not_an_embedded_block(roster):
    nested = ('## Task 1: One\n~~~\n```ale-label\n{"task_id":"T1"}\n```\n~~~\n'
              '## Task 2: Two\n')
    assert extract_blocks(nested) == []
    with pytest.raises(BakeError, match="T1"):
        compile_plan(nested)
    baked = bake(nested, {"T1": _label(roster), "T2": _label(roster, "T2")})
    assert '~~~\n```ale-label\n{"task_id":"T1"}\n```\n~~~' in baked
    assert baked.count("```ale-label") == 3


def test_bake_uses_dominant_crlf_line_endings(roster):
    text = "## Task 1: One\r\n## Task 2: Two\r\n"
    baked = bake(text, {"T1": _label(roster), "T2": _label(roster, "T2")})
    assert all(line.endswith("\r\n") for line in baked.splitlines(keepends=True))


def test_block_lane_reason_beats_stale_sidecar():
    from ale import bake as B
    text = "# P\n\n### Task 1: A\n\nbody\n\n### Task 2: B\n\nbody\n"
    labels = {t["task_id"]: B.skeleton_label(t, "r", {}) for t in B.parse_plan(text)}
    baked = B.bake(text, labels)
    sidecar = {tid: {"lane_reason": None} for tid in labels}
    edited = baked.replace('"lane_reason": null', '"lane_reason": "planner answered"', 1)
    out = B.compile_plan(edited, provenance=sidecar)
    assert out["T1"]["provenance"]["lane_reason"] == "planner answered"
