import copy

import pytest

from ale.labeling.judge import options_for
from ale.labeling.merge import LaneVoteError, merge
from ale.labeling.rules import rule_votes


@pytest.fixture
def draft():
    return {
        "title": "",
        "context": {"pointers": [], "allowed_paths": []},
    }


@pytest.fixture
def task_roster(roster):
    return copy.deepcopy(roster)


@pytest.mark.parametrize(
    "path,field,value",
    [
        ("src/app.css", "sub", "frontend/css"),
        ("src/components/Button.tsx", "sub", "frontend/components"),
        (".github/workflows/ci.yml", "sub", "devops/ci"),
        ("infra/terraform/main.tf", "sub", "devops/infra"),
        ("db/migrations/001.sql", "sub", "backend/data"),
        ("src/routes/users.py", "sub", "backend/api"),
        ("tests/unit/test_users.py", "sub", "test/unit"),
    ],
)
def test_path_rules_fire_and_unrelated_path_abstains(task_roster, draft, path, field, value):
    draft["context"]["pointers"] = [path]
    votes = rule_votes(draft, "", task_roster)
    matching = [vote for vote in votes if vote["field"] == field and vote["value"] == value]
    assert matching
    draft["context"]["pointers"] = ["unrelated/file.py"]
    votes = rule_votes(draft, "", task_roster)
    assert all(vote["value"] is None for vote in votes if vote["field"] in ("sub", "phase"))


@pytest.mark.parametrize(
    "words,field,value",
    [
        ("please review this", "phase", "review"),
        ("deploy the service", "phase", "deploy"),
        ("debug this failure", "sub", "debugging"),
        ("design a mock in Figma", "sub", "frontend/design"),
        ("design a mock in Figma", "phase", "design"),
    ],
)
def test_word_rules_fire(task_roster, draft, words, field, value):
    votes = rule_votes(draft, words, task_roster)
    assert any(vote["field"] == field and vote["value"] == value for vote in votes)


def test_word_rules_abstain_without_trigger(task_roster, draft):
    votes = rule_votes(draft, "Add a small feature", task_roster)
    assert all(vote["value"] is None for vote in votes if vote["field"] in ("sub", "phase"))


def test_conflicting_rules_abstain(task_roster, draft):
    result = merge("sub", [
        {"field": "sub", "value": "frontend/css", "by": "rule:0", "confidence": 1.0},
        {"field": "sub", "value": "backend/data", "by": "rule:1", "confidence": 1.0},
        {"field": "sub", "value": "planner-value", "by": "planner", "confidence": None},
    ], "shadow", 0.75)
    assert result == {"value": "planner-value", "by": "planner", "confidence": None, "conflict": True}


@pytest.mark.parametrize("field", ["sub", "phase"])
def test_merge_prefers_rule_over_planner(task_roster, field):
    result = merge(field, [
        {"field": field, "value": "planner-value", "by": "planner", "confidence": None},
        {"field": field, "value": "rule-value", "by": "rule:0", "confidence": 1.0},
    ], "shadow", 0.75)
    assert result["by"] == "rule:0"


def test_judge_sub_options_are_scoped_to_role(roster):
    options = options_for("sub", roster, role="frontend")
    assert any(option.startswith("css:") for option in options)
    assert not any(option.startswith("api:") for option in options)


def test_judge_phase_options_use_phase_vocabulary(roster):
    options = options_for("phase", roster)
    assert any(option.startswith("review:") for option in options)


def test_lane_still_raises():
    with pytest.raises(LaneVoteError):
        merge("lane", [], "shadow", 0.75)
