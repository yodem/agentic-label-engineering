import copy
import warnings

import pytest

from ale.dynamic import effective_labels, fix_label
from ale.events import make_event, reduce_run


def lab(task="T1", deps=None, paths=None):
    return {"schema_version": "1.0", "run_id": "r", "task_id": task, "title": "Task %s" % task,
            "labels": {"role": "backend", "model_tier": "standard", "lane": "pane", "risk": "low", "effort": "S"},
            "routing": {"executor": None, "model": None, "resolved_from": None},
            "context": {"spec_path": "s/%s.md" % task, "pointers": [], "allowed_paths": paths or [task + "/**"], "depends_on": deps or []},
            "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}, {"id": "A2", "cmd": "true", "expect": "exit0"}],
            "provenance": {"lane_reason": "planner supplied lane"}}


def ev(kind, task="T1", agent=None, ts=1, **extra):
    return make_event(kind, "r", ts, task, agent, 1, **extra)


def test_replay_is_copy_and_deterministic():
    frozen = {"T1": lab()}
    events = [ev("label_changed", field="labels.role", old="backend", new="docs", reason="docs")]
    assert effective_labels(frozen, events, lambda path: lab()) == effective_labels(frozen, events, lambda path: lab())
    assert frozen["T1"]["labels"]["role"] == "backend"


def test_executor_task_added_is_ignored():
    result = effective_labels({"T1": lab()}, [ev("task_added", task="T2", agent="a", label_file="T2.json", reason="x")], lambda p: lab("T2"))
    assert "T2" not in result


def test_lead_task_added_is_loaded():
    result = effective_labels({"T1": lab()}, [ev("task_added", task="T2", label_file="T2.json", reason="x")], lambda p: lab("T2"))
    assert sorted(result) == ["T1", "T2"]


def test_executor_label_change_is_ignored():
    result = effective_labels({"T1": lab()}, [ev("label_changed", agent="a", field="labels.role", old="backend", new="docs", reason="x")], lambda p: lab())
    assert result["T1"]["labels"]["role"] == "backend"


def test_lead_label_change_applies():
    result = effective_labels({"T1": lab()}, [ev("label_changed", field="labels.role", old="backend", new="docs", reason="x")], lambda p: lab())
    assert result["T1"]["labels"]["role"] == "docs"


def test_removed_task_is_absent():
    result = effective_labels({"T1": lab()}, [ev("label_removed", reason="obsolete")], lambda p: lab())
    assert result == {}


def test_forbidden_field_is_ignored():
    with pytest.warns(RuntimeWarning):
        result = effective_labels({"T1": lab()}, [ev("label_changed", field="title", old="a", new="b", reason="x")], lambda p: lab())
    assert result["T1"]["title"] == "Task T1"


def test_acceptance_change_after_submit_is_ignored():
    events = [ev("submitted", ts=1), ev("label_changed", field="acceptance", old=[], new=[], reason="x")]
    with pytest.warns(RuntimeWarning):
        result = effective_labels({"T1": lab()}, events, lambda p: lab())
    assert len(result["T1"]["acceptance"]) == 2


def test_lead_acceptance_relabel_replays_without_warning():
    previous = lab()["acceptance"]
    updated = [{"id": "A1", "cmd": "pytest -q", "expect": "exit0"},
               {"id": "A2", "cmd": "python -m compileall .", "expect": "exit0"}]
    events = [ev("submitted", ts=1),
              ev("label_changed", ts=2, field="acceptance", old=previous, new=updated, reason="repair check"),
              ev("relabeled", ts=3, field="acceptance", old=previous, new=updated, reason="repair check")]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = effective_labels({"T1": lab()}, events, lambda path: lab())
    assert result["T1"]["acceptance"] == updated
    assert caught == []


def test_executor_authored_label_change_warns():
    event = ev("label_changed", agent="worker", field="labels.role", old="backend", new="docs")
    with pytest.warns(RuntimeWarning, match="executor-authored"):
        result = effective_labels({"T1": lab()}, [event], lambda path: lab())
    assert result["T1"]["labels"]["role"] == "backend"


def test_lane_change_requires_reason():
    with pytest.warns(RuntimeWarning):
        result = effective_labels({"T1": lab()}, [ev("label_changed", field="labels.lane", old="pane", new="inline", reason="")], lambda p: lab())
    assert result["T1"]["labels"]["lane"] == "pane"


def test_cycle_change_is_ignored():
    frozen = {"T1": lab("T1", ["T2"]), "T2": lab("T2")}
    with pytest.warns(RuntimeWarning, match="cycle"):
        result = effective_labels(frozen, [ev("label_changed", task="T2", field="context.depends_on", old=[], new=["T1"], reason="x")], lambda p: lab())
    assert result["T2"]["context"]["depends_on"] == []


def test_overlap_change_is_ignored():
    frozen = {"T1": lab("T1", paths=["a/**"]), "T2": lab("T2", paths=["b/**"])}
    with pytest.warns(RuntimeWarning, match="overlap"):
        result = effective_labels(frozen, [ev("label_changed", task="T2", field="context.allowed_paths", old=["b/**"], new=["a/**"], reason="x")], lambda p: lab())
    assert result["T2"]["context"]["allowed_paths"] == ["b/**"]


def test_allowed_assignment_change_applies():
    value = [{"kind": "executor"}]
    result = effective_labels({"T1": lab()}, [ev("label_changed", field="assignments", old=[], new=value, reason="x")], lambda p: lab())
    assert result["T1"]["assignments"] == value


def test_watch_change_applies():
    result = effective_labels({"T1": lab()}, [ev("label_changed", field="watch.max_attempts", old=3, new=2, reason="x")], lambda p: lab())
    assert result["T1"]["watch"]["max_attempts"] == 2


def test_fix_label_identity_and_parent():
    parent = lab()
    fix = fix_label(parent, [parent["acceptance"][0]], 2)
    assert fix["task_id"] == "T1.fix2" and fix["fixes"] == "T1" and fix["labels"]["role"] == "fixer"


def test_fix_label_pads_with_passing_entry():
    parent = lab()
    fix = fix_label(parent, [parent["acceptance"][0]], 1)
    assert len(fix["acceptance"]) == 2 and fix["acceptance"][1]["id"] == "A2"


def test_fix_label_clears_dependencies_and_copies_paths():
    parent = lab(deps=["P"], paths=["src/**"])
    fix = fix_label(parent, parent["acceptance"], 1)
    assert fix["context"]["depends_on"] == [] and fix["context"]["allowed_paths"] == ["src/**"]


def test_reduce_records_spawned_and_integrated():
    labels = {"T1": lab()}
    out = reduce_run([ev("spawned", agent_id_minted="a", assignment_kind="executor", executor="x", model="m"),
                      ev("integrated", commit="abc")], labels)
    assert out["tasks"]["T1"]["assignees"] == ["a"] and out["tasks"]["T1"]["integrated"] is True


def test_reduce_marks_orphaned_dependency():
    labels = {"T2": lab("T2", deps=["T1"])}
    out = reduce_run([], labels)["tasks"]["T2"]
    assert out["blocked_by"] == ["T1"] and ["orphaned_dependency", 1] in out["breaches_seen"]


def test_authority_events_require_lead_for_reducer_effects():
    labels = {"T1": lab()}
    out = reduce_run([ev("integrated", agent="worker", commit="abc")], labels)
    assert out["tasks"]["T1"]["integrated"] is False
