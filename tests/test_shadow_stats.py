"""Pure statistics: uncertain-band buckets, agreement inside/outside, latency, bar."""

import pytest

from ale.labeling.shadow import band_of, summarize_shadow

BAR = {"min_cases": 100, "min_agreement": .8, "max_instability": .1}


def _vote(task, decision, choice, **extra):
    row = {"run_id": "r", "task_id": task, "decision": decision, "choice": choice}
    row.update(extra)
    return row


def _outcome(task, decision, choice):
    return {"run_id": "r", "task_id": task, "decision": decision, "choice": choice}


def test_band_of_uses_explicit_flag_then_choice_confidence():
    assert band_of({"choice": "pane", "uncertain": True, "confidence": 0.99}) == "inside"
    assert band_of({"choice": "pane", "uncertain": False, "confidence": 0.51}) == "outside"
    assert band_of({"choice": "backend", "confidence": 0.49}) == "inside"
    assert band_of({"choice": "backend", "confidence": 0.5}) == "outside"
    assert band_of({"choice": "backend", "confidence": None}) == "missing"
    assert band_of({"choice": None, "uncertain": False, "confidence": 0.9}) == "missing"


def test_agreement_is_reported_inside_and_outside_the_band():
    votes = [_vote("T1", "role", "backend", confidence=.9),     # outside, agrees
             _vote("T2", "role", "frontend", confidence=.9),    # outside, disagrees
             _vote("T3", "role", "backend", confidence=.3),     # inside, agrees
             _vote("T4", "role", None, confidence=None),        # missing
             _vote("T1", "lane", "pane", uncertain=True, confidence=.6),
             _vote("T2", "lane", "inline", uncertain=False, confidence=.9)]
    outcomes = [_outcome("T1", "role", "backend"), _outcome("T2", "role", "backend"),
                _outcome("T3", "role", "backend"), _outcome("T4", "role", "backend"),
                _outcome("T1", "lane", "inline"), _outcome("T2", "lane", "inline")]
    result = summarize_shadow(votes, outcomes, [], BAR)["decisions"]
    role = result["role"]
    assert role["uncertain_band"] == {"inside": 1, "outside": 2, "missing": 1}
    assert role["agreement_outside_band"] == 0.5
    assert role["agreement_inside_band"] == 1.0
    assert role["agreement"] == 0.5  # 2 of 4 votes with a known outcome
    lane = result["lane"]
    assert (lane["agreement_inside_band"], lane["agreement_outside_band"]) == (0.0, 1.0)


def test_votes_without_outcome_count_but_do_not_enter_agreement():
    result = summarize_shadow([_vote("T1", "sub", "css", confidence=.8)], [], [], BAR)["decisions"]["sub"]
    assert result["vote_count"] == 1 and result["agreement"] is None


def test_latency_median_and_effort_grey_zone():
    votes = [_vote("T%d" % n, "effort", "S", confidence=.9, latency_ms=ms)
             for n, ms in enumerate([300, 100, 200, 900])]
    votes.append(_vote("T9", "role", "backend", confidence=.9, latency_ms=50))
    result = summarize_shadow(votes, [], [], BAR)["decisions"]
    assert result["effort"]["latency_ms_median"] == 250
    assert result["effort"]["grey_zone"] is True and result["role"]["grey_zone"] is False


def test_bar_needs_100_adjudicated_cases_and_both_thresholds():
    def run(count, agree=True):
        votes = [_vote("T%d" % n, "risk", "low", confidence=.9) for n in range(count)]
        outcomes = [_outcome("T%d" % n, "risk", "low") for n in range(count)]
        # An adjudication is the case's truth, so disagreement is set there.
        adjudications = [_outcome("T%d" % n, "risk", "low" if agree else "high") for n in range(count)]
        return summarize_shadow(votes, outcomes, adjudications, BAR)["decisions"]["risk"]
    assert run(99)["bar_met"] is False and run(99)["progress"]["adjudicated"] == 99
    assert run(100)["bar_met"] is True and run(100)["progress"]["fraction"] == 1.0
    assert run(100, agree=False)["bar_met"] is False


def test_shadow_votes_alone_are_not_adjudications():
    votes = [_vote("T%d" % n, "risk", "low", confidence=.9) for n in range(120)]
    outcomes = [_outcome("T%d" % n, "risk", "low") for n in range(120)]
    result = summarize_shadow(votes, outcomes, [], BAR)["decisions"]["risk"]
    assert result["adjudicated_count"] == 0 and result["bar_met"] is False


def test_option_order_instability():
    votes = [_vote("T1", "role", "backend", options=["backend", "frontend"], confidence=.9),
             _vote("T1", "role", "frontend", options=["frontend", "backend"], confidence=.9),
             _vote("T2", "role", "backend", options=["backend", "frontend"], confidence=.9),
             _vote("T2", "role", "backend", options=["frontend", "backend"], confidence=.9)]
    assert summarize_shadow(votes, [], [], BAR)["decisions"]["role"]["instability"] == pytest.approx(0.5)


def test_output_shape_is_stable():
    result = summarize_shadow([_vote("T1", "role", "backend", confidence=.9)], [], [], BAR)
    assert set(result) == {"decisions", "bar", "band", "cases"}
    assert set(result["decisions"]["role"]) == {
        "vote_count", "adjudicated_count", "disagreement_count", "uncertain_band", "agreement", "agreement_inside_band",
        "agreement_outside_band", "latency_ms_median", "grey_zone", "progress", "instability", "bar_met"}
    assert result["band"] == {"noul_uncertain": [0.35, 0.65], "choice_uncertain_below": 0.5}
