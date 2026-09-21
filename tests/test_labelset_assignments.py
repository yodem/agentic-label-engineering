import copy

from ale.labelset import check_label


def test_old_label_without_assignments_or_worktree_still_validates(roster, label_t01):
    assert check_label(label_t01, roster) == []


def test_check_label_applies_default_executor_assignment(roster, label_t01):
    label = copy.deepcopy(label_t01)
    label["labels"]["lane"] = "inline"
    label["provenance"]["lane_reason"] = "The human stays present for this work."

    assert check_label(label, roster) == []


def test_two_executors_are_rejected(roster, label_t01):
    label = copy.deepcopy(label_t01)
    label["assignments"] = [
        {"kind": "executor", "role": "backend", "model_tier": "standard", "executor": None, "trigger": "ready"},
        {"kind": "executor", "role": "backend", "model_tier": "standard", "executor": None, "trigger": "ready"},
    ]

    assert any("exactly one executor" in error for error in check_label(label, roster))


def test_monitor_ready_is_rejected(roster, label_t01):
    label = copy.deepcopy(label_t01)
    label["assignments"] = [
        {"kind": "executor", "role": "backend", "model_tier": "standard", "executor": None, "trigger": "ready"},
        {"kind": "monitor", "role": "test", "model_tier": "cheap", "executor": None, "trigger": "ready"},
    ]

    assert any("monitor" in error and "ready" in error for error in check_label(label, roster))


def test_shared_worktree_requires_reason(roster, label_t01):
    label = copy.deepcopy(label_t01)
    label["context"]["worktree"] = {
        "mode": "shared", "branch": None, "base": None, "worktree_reason": None
    }

    assert any("worktree_reason" in error for error in check_label(label, roster))


def test_fixer_is_a_valid_assignment_role(roster, label_t01):
    label = copy.deepcopy(label_t01)
    label["assignments"] = [{
        "kind": "executor", "role": "backend", "model_tier": "standard",
        "executor": None, "trigger": "ready",
    }, {
        "kind": "fixer", "role": "fixer", "model_tier": "standard",
        "executor": None, "trigger": "on_breach",
    }]

    assert check_label(label, roster) == []
