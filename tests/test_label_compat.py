import copy
import json

from ale.hooks import decide_pre_tool
from ale.labelset import load_labels
from ale.validate import load_schema, validate
from ale.verify import run_required


def _intermediate_label(label):
    label = copy.deepcopy(label)
    label["routing"]["agent"] = {
        "key": "backend/auth",
        "name": "backend-auth",
        "path": ".ale/agents/backend/auth.md",
        "sha256": "a" * 64,
        "version": 1,
        "matched": "exact",
    }
    label["effective_rules"] = {
        "deny_paths": ["private/**"],
        "deny_tools": ["Bash"],
        "require_before_submit": ["true"],
    }
    return label


def test_schema_accepts_intermediate_agent_shape_and_effective_rules(label_t01):
    assert validate(_intermediate_label(label_t01), load_schema("label.schema.json")) == []


def test_load_labels_normalizes_in_memory_without_rewriting(tmp_path, label_t01):
    run_dir = tmp_path / "run"
    labels_dir = run_dir / "labels"
    labels_dir.mkdir(parents=True)
    original = _intermediate_label(label_t01)
    path = labels_dir / "T01.json"
    path.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_labels(str(run_dir))["T01"]

    assert loaded["routing"]["agent"] == {
        **original["routing"]["agent"],
        "model_tier_min": None,
    }
    assert loaded["effective_rules"] == original["effective_rules"]
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_hook_and_verify_enforce_rules_equally_for_intermediate_and_current(
        tmp_path, label_t01):
    legacy = _intermediate_label(label_t01)
    current = copy.deepcopy(legacy)
    current["routing"]["agent"]["model_tier_min"] = None
    labels_dir = tmp_path / "run" / "labels"
    labels_dir.mkdir(parents=True)
    for task_id, label in (("T01", legacy), ("T02", current)):
        label["task_id"] = task_id
        (labels_dir / (task_id + ".json")).write_text(json.dumps(label), encoding="utf-8")

    loaded = load_labels(str(labels_dir.parent))
    decisions = [
        decide_pre_tool({"agent_id": "a"}, loaded[task], {"owner": "a"}, "Bash", {}, ".")["action"]
        for task in ("T01", "T02")
    ]
    verify_results = [
        run_required(loaded[task]["effective_rules"]["require_before_submit"], str(tmp_path))
        for task in ("T01", "T02")
    ]

    assert decisions == ["deny", "deny"]
    assert verify_results[0] == verify_results[1]
    assert verify_results[0][0]["ok"] is True
