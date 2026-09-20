import pytest

from ale.labeling.merge import LaneVoteError, MergeError, merge


def v(by, value, conf=None, field="role"):
    return {"field": field, "value": value, "by": by, "confidence": conf, "detail": {}}


def test_rule_beats_everything():
    got = merge("role", [v("planner", "backend"), v("judge:command", "frontend", 0.99), v("rule:0", "docs", 1.0)], "authoritative", 0.75)
    assert (got["value"], got["by"], got["conflict"]) == ("docs", "rule:0", False)


def test_conflicting_rules_abstain_and_flag():
    got = merge("role", [v("rule:0", "docs", 1.0), v("rule:1", "test", 1.0), v("planner", "backend")], "shadow", 0.75)
    assert (got["value"], got["by"], got["conflict"]) == ("backend", "planner", True)


def test_agreeing_rules_are_not_a_conflict():
    got = merge("role", [v("rule:0", "docs", 1.0), v("rule:1", "docs", 1.0), v("planner", "backend")], "shadow", 0.75)
    assert (got["value"], got["conflict"]) == ("docs", False)


def test_shadow_judge_never_wins():
    got = merge("role", [v("planner", "backend"), v("judge:command", "frontend", 1.0)], "shadow", 0.75)
    assert (got["value"], got["by"]) == ("backend", "planner")


def test_authoritative_judge_wins_only_above_threshold():
    votes = [v("planner", "backend"), v("judge:command", "frontend", 0.74)]
    assert merge("role", votes, "authoritative", 0.75)["by"] == "planner"
    votes[1]["confidence"] = 0.75
    assert merge("role", votes, "authoritative", 0.75) == {"value": "frontend", "by": "judge:command", "confidence": 0.75, "conflict": False}


def test_judge_other_or_abstain_falls_back():
    assert merge("role", [v("planner", "backend"), v("judge:command", "other", 1.0)], "authoritative", 0.5)["by"] == "planner"
    assert merge("role", [v("planner", "backend"), v("judge:command", None, None)], "authoritative", 0.5)["by"] == "planner"


def test_off_mode_ignores_judge():
    assert merge("role", [v("planner", "backend"), v("judge:command", "frontend", 1.0)], "off", 0.0)["by"] == "planner"


def test_lane_votes_are_structurally_refused():
    with pytest.raises(LaneVoteError):
        merge("lane", [v("planner", "pane", field="lane")], "shadow", 0.75)
    with pytest.raises(LaneVoteError):
        merge("role", [v("planner", "backend"), v("judge:command", "pane", 1.0, field="lane")], "shadow", 0.75)


def test_missing_planner_vote_is_an_error():
    with pytest.raises(MergeError):
        merge("role", [v("judge:command", "frontend", 0.5)], "shadow", 0.75)


def test_votes_for_other_fields_are_ignored():
    got = merge("role", [v("planner", "backend"), v("rule:0", "high", 1.0, field="risk")], "shadow", 0.75)
    assert got["value"] == "backend"
