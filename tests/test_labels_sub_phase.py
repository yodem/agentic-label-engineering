import copy
import json

import pytest

from ale.bake import compile_plan, gaps, render_block, skeleton_label
from ale.labelset import check_label
from ale.roster import load_roster, roster_hash


PHASES = ["plan", "design", "implement", "test", "review", "deploy", "operate", "maintain"]
SUBS = {
    "frontend": ["css", "ux", "design", "components", "performance", "accessibility"],
    "backend": ["architecture", "api", "data", "integration", "performance"],
    "devops": ["ci", "deploy", "infra", "monitor", "release"],
    "test": ["unit", "integration", "e2e", "coverage"],
    "docs": ["reference", "guide", "changelog"],
}


def _old_roster(roster, tmp_path):
    roster = copy.deepcopy(roster)
    for key in ("sub", "cross_sub", "phase"):
        roster["vocab"].pop(key, None)
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster), encoding="utf-8")
    return path


def _label(label_t01, role="test", sub=None, phase="implement", paths=None):
    label = copy.deepcopy(label_t01)
    label["labels"]["role"] = role
    label["labels"]["sub"] = sub
    label["labels"]["phase"] = phase
    for assignment in label.get("assignments", []):
        if assignment.get("kind") == "executor":
            assignment["role"] = role
    label["context"]["allowed_paths"] = ["src/**"] if paths is None else paths
    return label


def _catalog(rules=None):
    return {"general": {"rules": {"deny_paths": [], "deny_tools": [],
                                    "require_before_submit": []}, "phases": ["implement"],
                         "name": "general", "path": "agents/general.md", "sha256": "x",
                         "version": 1, "model_tier_min": "cheap"}}


def test_old_roster_gets_default_vocab_and_hash_includes_it(roster, tmp_path):
    loaded = load_roster(str(_old_roster(roster, tmp_path)))
    assert {role: list(loaded["vocab"]["sub"][role]) for role in SUBS} == SUBS
    assert loaded["vocab"]["cross_sub"] == ["debugging", "review", "security"]
    assert loaded["vocab"]["phase"] == PHASES
    assert roster_hash(loaded) == roster_hash(copy.deepcopy(loaded))


@pytest.mark.parametrize("role,sub", [(role, sub) for role, subs in SUBS.items() for sub in subs])
def test_default_sub_vocabulary_contains_each_declared_sub(roster, role, sub):
    assert sub in roster["vocab"].get("sub", {}).get(role, dict.fromkeys(SUBS[role]))


def test_unknown_sub_rejected(roster, label_t01):
    label = _label(label_t01, role="frontend", sub="made-up")
    assert any("sub" in error and "made-up" in error for error in check_label(label, roster))


@pytest.mark.parametrize("role", ["frontend", "backend", "devops", "test", "docs", "general"])
@pytest.mark.parametrize("sub", ["debugging", "review", "security"])
def test_cross_sub_is_valid_under_every_role(roster, label_t01, role, sub):
    assert check_label(_label(label_t01, role=role, sub=sub), roster) == []


@pytest.mark.parametrize("role,sub", [("frontend", "css"), ("backend", "api"), ("devops", "ci")])
def test_sub_without_agent_reports_expected_path(roster, label_t01, role, sub):
    errors = check_label(_label(label_t01, role=role, sub=sub), roster, catalog=_catalog())
    assert any("agents/%s/%s.md" % (role, sub) in error for error in errors)


def test_deny_path_covering_allowed_path_is_rejected(roster, label_t01):
    catalog = _catalog()
    catalog["frontend/css"] = {"rules": {"deny_paths": ["src/**"], "deny_tools": [],
                                           "require_before_submit": []}, "phases": ["implement"],
                               "name": "css", "path": "agents/frontend/css.md", "sha256": "x",
                               "version": 1, "model_tier_min": "cheap"}
    errors = check_label(_label(label_t01, role="frontend", sub="css", paths=["src/**"]),
                         roster, catalog=catalog)
    assert any("deny_paths" in error and "allowed_paths" in error for error in errors)


def test_phase_null_is_invalid_with_allowed_paths(roster, label_t01):
    errors = check_label(_label(label_t01, phase=None, paths=["src/**"]), roster)
    assert any("phase" in error for error in errors)


def test_null_phase_is_valid_without_allowed_paths(roster, label_t01):
    assert any("gap: phase" in error for error in check_label(
        _label(label_t01, phase=None, paths=[]), roster))


def test_skeleton_sets_implement_phase_for_nonempty_paths(roster):
    task = {"task_id": "T1", "title": "Task", "files": ["src/a.py"], "commands": []}
    label = skeleton_label(task, "run-1", {"roster": roster, "role": "test",
                                           "effort": "M", "risk": "low",
                                           "model_tier": "standard"})
    assert label["labels"]["phase"] == "implement"


def test_skeleton_phase_is_null_without_paths(roster):
    task = {"task_id": "T1", "title": "Task", "files": [], "commands": []}
    label = skeleton_label(task, "run-1", {"roster": roster, "role": "test",
                                           "effort": "M", "risk": "low",
                                           "model_tier": "standard"})
    assert label["labels"]["phase"] is None


def test_gaps_report_required_sub_and_phase(label_t01):
    label = _label(label_t01, role="frontend", sub=None, phase=None, paths=[])
    assert "sub" in gaps(label)
    assert "phase" in gaps(label)


def test_block_round_trip_preserves_sub_and_phase(roster):
    label = {"task_id": "T1", "title": "Task", "labels": {"role": "frontend",
             "model_tier": "standard", "risk": "low", "effort": "M", "lane": None,
             "sub": "css", "phase": "review"}, "acceptance": [], "context": {
             "allowed_paths": ["src/**"], "depends_on": [], "spec_path": "plan",
             "pointers": [], "worktree": {"mode": "per_task"}}, "assignments": [],
             "provenance": {}}
    second = copy.deepcopy(label)
    second.update({"task_id": "T2", "title": "Second"})
    text = ("## Task 1: Task\n" + render_block(label) +
            "## Task 2: Second\n" + render_block(second))
    compiled = compile_plan(text)["T1"]
    assert compiled["labels"]["sub"] == "css"
    assert compiled["labels"]["phase"] == "review"


def test_old_block_without_sub_phase_still_compiles():
    block = {"task_id": "T1", "title": "Task", "labels": {"role": "test",
             "model_tier": "standard", "risk": "low", "effort": "M", "lane": None},
             "acceptance": [], "allowed_paths": [], "depends_on": [], "worktree": "none",
             "assignments": []}
    second = copy.deepcopy(block)
    second.update({"task_id": "T2", "title": "Second"})
    text = ("## Task 1: Task\n```ale-label\n%s\n```\n"
            "## Task 2: Second\n```ale-label\n%s\n```\n" %
            (json.dumps(block), json.dumps(second)))
    compiled = compile_plan(text)["T1"]
    assert compiled["routing"]["agent"] is None
    assert "sub" not in compiled["labels"]
