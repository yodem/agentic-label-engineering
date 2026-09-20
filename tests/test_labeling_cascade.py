import copy

import pytest

from ale.labeling.cascade import FIELDS, label_task
from ale.labelset import check_label


class FakeJudge:
    """Fake judge with an .ask(field, question, options, state) method, matching CommandJudge's interface."""

    def __init__(self, answers=None, raises_for=None):
        self.answers = answers or {}
        self.raises_for = raises_for or set()
        self.calls = []

    def ask(self, field, question, options, state):
        self.calls.append(field)
        if field in self.raises_for:
            raise RuntimeError("judge blew up on %s" % field)
        if field in self.answers:
            value, confidence = self.answers[field]
            return {"field": field, "value": value, "by": "judge:command", "confidence": confidence, "detail": {}}
        return {"field": field, "value": None, "by": "judge:command", "confidence": None, "detail": {"error": "no_answer"}}


def _one_rule_roster(roster):
    r = copy.deepcopy(roster)
    r["rules"] = [{"field": "role", "when": {"keyword": "zzz-never-matches"}, "value": "docs"}]
    return r


def test_shadow_judge_disagreeing_leaves_planner_and_records_three_votes(roster, label_t01):
    r = _one_rule_roster(roster)
    judge = FakeJudge(answers={"role": ("frontend", 0.9)})
    final, votes = label_task(label_t01, "", r, judge=judge)
    assert final["labels"]["role"] == label_t01["labels"]["role"]
    assert final["provenance"]["role"]["by"] == "planner"
    assert len(final["provenance"]["role"]["votes"]) == 3


def test_firing_rule_overrides_planner(roster, label_t01):
    r = copy.deepcopy(roster)
    r["rules"] = [{"field": "role", "when": {"keyword": "(?i)refresh"}, "value": "backend"}]
    final, votes = label_task(label_t01, "Add pytest coverage for the refresh path", r, judge=None)
    assert final["provenance"]["role"]["by"] == "rule:0"
    assert final["labels"]["role"] == "backend"


def test_no_judge_means_no_judge_votes(roster, label_t01):
    final, votes = label_task(label_t01, "", roster, judge=None)
    assert not any(v["by"].startswith("judge:") for v in votes)


def test_off_mode_field_never_asks_judge(roster, label_t01):
    r = copy.deepcopy(roster)
    r["judge"]["modes"]["role"] = "off"
    judge = FakeJudge(answers={"role": ("frontend", 0.9), "model_tier": ("standard", 0.9),
                                "risk": ("low", 0.9), "effort": ("M", 0.9)})
    label_task(label_t01, "", r, judge=judge)
    assert "role" not in judge.calls
    assert "model_tier" in judge.calls


def test_authoritative_above_threshold_takes_judge_value(roster, label_t01):
    r = copy.deepcopy(roster)
    r["rules"] = []
    r["judge"]["modes"]["role"] = "authoritative"
    r["judge"]["threshold"] = 0.75
    judge = FakeJudge(answers={"role": ("frontend", 0.9)})
    final, votes = label_task(label_t01, "", r, judge=judge)
    assert final["labels"]["role"] == "frontend"
    assert final["provenance"]["role"]["by"] == "judge:command"


def test_draft_is_not_mutated(roster, label_t01):
    before = copy.deepcopy(label_t01)
    judge = FakeJudge(answers={"role": ("frontend", 0.9)})
    label_task(label_t01, "some text", roster, judge=judge)
    assert label_t01 == before


def test_lane_and_lane_reason_copied_untouched(roster, label_t01):
    final, votes = label_task(label_t01, "", roster, judge=None)
    assert final["labels"]["lane"] == label_t01["labels"]["lane"]
    assert final["provenance"]["lane_reason"] == label_t01["provenance"]["lane_reason"]


def test_result_passes_check_label(roster, label_t01):
    judge = FakeJudge(answers={"role": ("frontend", 0.9)})
    final, votes = label_task(label_t01, "", roster, judge=judge)
    assert check_label(final, roster) == []


def test_judge_exception_is_an_abstain_not_raised(roster, label_t01):
    r = copy.deepcopy(roster)
    judge = FakeJudge(raises_for={"role", "model_tier", "risk", "effort"})
    final, votes = label_task(label_t01, "", r, judge=judge)
    judge_votes = [v for v in votes if v["by"].startswith("judge:")]
    assert len(judge_votes) == 4
    assert all(v["value"] is None for v in judge_votes)
    # planner value preserved since judge abstained (shadow mode by default)
    assert final["labels"]["role"] == label_t01["labels"]["role"]


def test_fields_constant():
    assert FIELDS == ("role", "model_tier", "risk", "effort")


def test_votes_returned_include_every_field(roster, label_t01):
    final, votes = label_task(label_t01, "", roster, judge=None)
    fields_seen = {v["field"] for v in votes}
    assert fields_seen == set(FIELDS)
