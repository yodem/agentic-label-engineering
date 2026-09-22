import copy
import json
import os

import pytest

from ale.labeling.cascade import label_task

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def _base_draft():
    return {
        "schema_version": "1.0",
        "run_id": "example-run",
        "task_id": "T-roster-role-vote",
        "title": "Change some files",
        "labels": {"role": "general", "model_tier": "standard", "lane": "pane", "risk": "low", "effort": "M"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "", "pointers": [], "allowed_paths": [], "depends_on": []},
        "acceptance": [],
        "provenance": {"lane_reason": "test"},
    }


CASES = [
    (["src/styles/app.css"], "frontend", "css"),
    (["Dockerfile", ".github/workflows/ci.yml"], "devops", "ci"),
    (["migrations/001.sql"], "backend", "data"),
    (["tests/unit/test_x.py"], "test", "unit"),
]


@pytest.mark.parametrize("roster_path", ["examples/roster.json", "ale/example_roster.json"])
@pytest.mark.parametrize("files,expected_role,expected_sub", CASES)
def test_role_scoped_sub_rule_also_votes_role(roster_path, files, expected_role, expected_sub):
    roster = _load(roster_path)
    draft = _base_draft()
    draft["context"]["pointers"] = files
    final, votes = label_task(draft, "", roster, judge=None)
    assert final["labels"]["role"] == expected_role
    assert final["labels"]["sub"] == expected_sub


@pytest.mark.parametrize("roster_path", ["examples/roster.json", "ale/example_roster.json"])
@pytest.mark.parametrize("files,expected_role,expected_sub", CASES)
def test_mutation_check_without_companion_role_rule_sub_is_dropped(roster_path, files, expected_role, expected_sub):
    """Removing the companion role: rule reproduces the bug this roster change fixes:
    the sub rule fires but the role never agrees, so the sub vote is rejected."""
    roster = _load(roster_path)
    target_value = "sub:%s/%s" % (expected_role, expected_sub)
    without_companions = copy.deepcopy(roster)
    kept = []
    for rule in without_companions["rules"]:
        if rule["field"] == "role" and rule["value"] == expected_role:
            # drop companion role rules that share a condition with the sub rule for this case
            sub_rules = [r for r in roster["rules"] if r["value"] == target_value]
            if any(r["when"] == rule["when"] for r in sub_rules):
                continue
        kept.append(rule)
    without_companions["rules"] = kept

    draft = _base_draft()
    draft["context"]["pointers"] = files
    with pytest.warns(RuntimeWarning):
        final, votes = label_task(draft, "", without_companions, judge=None)
    assert final["labels"].get("sub") != expected_sub
