from ale.labelset import check_label, check_labelset, effective_watch, globs_overlap


def _set(*labels):
    return {l["task_id"]: l for l in labels}


def test_examples_pass(roster, label_t01, label_t02):
    assert check_labelset(_set(label_t01, label_t02), roster) == []


def test_unknown_role_rejected(roster, label_t01):
    label_t01["labels"]["role"] = "wizard"
    assert any("role" in e and "wizard" in e for e in check_label(label_t01, roster))


def test_unroutable_pair_rejected(roster, label_t01):
    roster["routing"] = [r for r in roster["routing"] if r["model_tier"] != "standard"]
    assert any("routing" in e for e in check_label(label_t01, roster))


def test_missing_dependency_rejected(roster, label_t01, label_t02):
    label_t02["context"]["depends_on"] = ["T99"]
    assert any("T99" in e for e in check_labelset(_set(label_t01, label_t02), roster))


def test_cycle_rejected(roster, label_t01, label_t02):
    label_t01["context"]["depends_on"] = ["T02"]
    assert any("cycle" in e for e in check_labelset(_set(label_t01, label_t02), roster))


def test_overlapping_paths_between_independent_tasks_rejected(roster, label_t01, label_t02):
    label_t02["context"]["depends_on"] = []
    label_t02["context"]["allowed_paths"] = ["src/auth/handlers/**"]
    assert any("overlap" in e for e in check_labelset(_set(label_t01, label_t02), roster))


def test_overlapping_paths_between_dependent_tasks_allowed(roster, label_t01, label_t02):
    label_t02["context"]["allowed_paths"] = ["src/auth/handlers/**"]
    assert check_labelset(_set(label_t01, label_t02), roster) == []


def test_mixed_run_ids_rejected(roster, label_t01, label_t02):
    label_t02["run_id"] = "other-run"
    assert any("run_id" in e for e in check_labelset(_set(label_t01, label_t02), roster))


def test_too_many_tasks_rejected(roster, label_t01, label_t02):
    roster["cost_gate"]["max_tasks_per_run"] = 1
    assert any("max_tasks_per_run" in e for e in check_labelset(_set(label_t01, label_t02), roster))


def test_globs_overlap():
    assert globs_overlap("src/auth/**", "src/auth/handlers/*.py")
    assert not globs_overlap("src/auth/**", "docs/**")
    assert globs_overlap("**", "docs/**")


def test_effective_watch_defaults_and_override(roster, label_t01):
    assert effective_watch(label_t01, roster)["stuck_after_s"] == 1200
    label_t01["watch"] = {"max_attempts": 5}
    w = effective_watch(label_t01, roster)
    assert w["max_attempts"] == 5 and w["heartbeat_timeout_s"] == 900


def test_uncertain_label_halves_stuck_threshold(roster, label_t01):
    label_t01["provenance"]["model_tier"] = {"by": "planner", "confidence": 0.4}
    assert effective_watch(label_t01, roster)["stuck_after_s"] == 600
