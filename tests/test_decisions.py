"""Registry, evidence question keys, rule tables and the uncertain band (pure)."""

import json
import os

import pytest

from ale import decisions as D
from ale.decisions import DECISIONS, compute_decision, decision_ids, options_for_decision
from ale.roster import load_roster

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(ROOT, "ale", "example_roster.json")


def test_registry_has_all_stable_decisions(roster):
    expected = {"lane", "role", "sub", "phase", "model_tier", "risk", "effort", "locality",
                "executor", "rejection_action", "monitor_verdict", "needs_monitor"}
    assert set(decision_ids()) == expected
    assert all(DECISIONS[name]["options_source"] and DECISIONS[name]["fires_at"] for name in expected)
    assert "lane_reason" in DECISIONS["lane"]["requires"]
    assert options_for_decision("rejection_action", roster) == ["fix", "reopen", "escalate"]


def test_executor_is_deterministic_and_excluded_from_the_judged_eleven():
    assert DECISIONS["executor"]["deterministic"] is True
    assert not D.is_judged("executor")
    assert len(D.judged_decision_ids()) == 11
    assert "executor" not in D.judged_decision_ids()
    assert "executor" not in D.required_question_keys()


def test_locality_options_are_the_label_field_values(roster):
    assert options_for_decision("locality", roster) == ["any", "local"]
    assert set(options_for_decision("locality", roster)) == set(roster["vocab"]["locality"])


def test_verdict_decisions_are_asked_as_evidence_not_as_one_choice():
    for decision in ("rejection_action", "monitor_verdict", "needs_monitor", "lane"):
        keys = D.EVIDENCE_QUESTIONS[decision]
        assert (len(keys) == 0) if decision == "lane" else (2 <= len(keys) <= 4)
        assert decision not in D.CHOICE_QUESTION_KEYS
        assert decision in D.RULE_TABLES
    assert D.EVIDENCE_QUESTIONS["locality"] == ["locality"]


def test_shipped_roster_has_an_explicit_question_for_every_judged_decision():
    roster = load_roster(SHIPPED)
    questions = roster["judge"]["questions"]
    for decision in D.judged_decision_ids():
        for key in D.question_keys(decision):
            assert questions.get(key), (decision, key)
    assert "lane" not in questions
    assert roster["judge"]["default"] == "off"


def test_no_generic_fallback_question_exists_in_the_code():
    for relative in ("ale/cli.py", "ale/labeling/evidence.py", "ale/labeling/cascade.py"):
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            assert "planner-independent" not in handle.read(), relative


def test_verdict_questions_are_evidence_not_verdicts():
    """Rule 9.9: no question may start with should / decide whether, or offer actions."""
    with open(SHIPPED, encoding="utf-8") as handle:
        questions = json.load(handle)["judge"]["questions"]
    for key in ("large_change", "needs_person", "external_side_effects", "locality",
                "rejection_environment", "rejection_spec_conflict", "rejection_needs_human",
                "monitor_reports_specific_failure", "monitor_agent_blocked", "monitor_unsafe"):
        text = questions[key].lower()
        assert not text.startswith(("should", "what should", "decide whether"))
        assert text.startswith(("does", "do ", "will", "can", "is ")), key
        assert "\u2014" not in questions[key]


# Lane derives large effort and run structure from facts, with no Jev evidence.
@pytest.mark.parametrize("answers,facts,expected,rule", [
    ({}, {"role": "docs", "effort": "S", "independent_tasks": 0}, "inline", "default"),
    ({}, {"role": "docs", "effort": "L", "independent_tasks": 0}, "pane", "effort_large"),
    ({}, {"role": "docs", "effort": "XL", "independent_tasks": 0}, "pane", "effort_large"),
    ({}, {"role": "docs", "effort": "S", "independent_tasks": 0, "unattended": True}, "pane", "unattended"),
    ({}, {"role": "test", "effort": "S", "independent_tasks": 0}, "workflow",
     "verification_role"),
    ({}, {"role": "review", "effort": "S", "independent_tasks": 5}, "workflow",
     "verification_role"),
    ({}, {"role": "backend", "effort": "S", "independent_tasks": 2}, "workflow",
     "independent_tasks"),
])
def test_lane_rule_table(answers, facts, expected, rule):
    result = compute_decision("lane", answers, facts)
    assert (result["choice"], result["rule"]) == (expected, rule)


def test_lane_asks_no_evidence():
    from ale.labeling import evidence as EV

    class FakeJudge:
        def __init__(self):
            self.asked = []

        def noul(self, key, question, state):
            self.asked.append(key)
            return {"p": 0.01, "model": "fake", "detail": {"latency_ms": 1}}

    judge = FakeJudge()
    vote = EV.evidence_vote(judge, "lane", {"judge": {"questions": {
        "large_change": "Is this large?"}}}, "{}",
        {"effort": "S", "role": "docs", "independent_tasks": 0})
    assert D.EVIDENCE_QUESTIONS["lane"] == []
    assert judge.asked == []
    assert vote["choice"] == "inline" and vote["rule"] == "default"
    assert vote["facts"]["effort"] == "S"
    assert vote["answers"] == {} and vote["confidence"] == 1.0 and vote["uncertain"] is False


@pytest.mark.parametrize("answers,facts,expected,rule", [
    ({"rejection_environment": .9, "rejection_spec_conflict": .9, "rejection_needs_human": .9},
     {"fix_count": 2, "is_fix_task": False}, "escalate", "fixes_exhausted"),
    ({"rejection_environment": .1, "rejection_spec_conflict": .1, "rejection_needs_human": .1},
     {"fix_count": 0, "is_fix_task": True}, "escalate", "fixes_exhausted"),
    ({"rejection_environment": .1, "rejection_spec_conflict": .1, "rejection_needs_human": .8},
     {"fix_count": 0, "is_fix_task": False}, "escalate", "needs_human"),
    ({"rejection_environment": .8, "rejection_spec_conflict": .1, "rejection_needs_human": .1},
     {"fix_count": 1, "is_fix_task": False}, "reopen", "environment"),
    ({"rejection_environment": .1, "rejection_spec_conflict": .8, "rejection_needs_human": .1},
     {"fix_count": 0, "is_fix_task": False}, "reopen", "spec_conflict"),
    ({"rejection_environment": .1, "rejection_spec_conflict": .1, "rejection_needs_human": .1},
     {"fix_count": 0, "is_fix_task": False}, "fix", "default"),
])
def test_rejection_action_rule_table(answers, facts, expected, rule):
    result = compute_decision("rejection_action", answers, facts)
    assert (result["choice"], result["rule"]) == (expected, rule)


@pytest.mark.parametrize("answers,facts,expected,rule", [
    ({"monitor_reports_specific_failure": .1, "monitor_agent_blocked": .1, "monitor_unsafe": .1},
     {"monitor_wrote_files": True}, "escalate", "monitor_wrote_files"),
    ({"monitor_reports_specific_failure": .1, "monitor_agent_blocked": .1, "monitor_unsafe": .1},
     {"attempts_exhausted": True}, "escalate", "attempts_exhausted"),
    ({"monitor_reports_specific_failure": .9, "monitor_agent_blocked": .1, "monitor_unsafe": .9}, {}, "escalate", "unsafe"),
    ({"monitor_reports_specific_failure": .9, "monitor_agent_blocked": .9, "monitor_unsafe": .1}, {}, "fix", "defect"),
    ({"monitor_reports_specific_failure": .1, "monitor_agent_blocked": .9, "monitor_unsafe": .1}, {}, "nudge",
     "agent_blocked"),
    ({"monitor_reports_specific_failure": .1, "monitor_agent_blocked": .1, "monitor_unsafe": .1},
     {"breach": "lease_expired"}, "nudge", "liveness_breach"),
    ({"monitor_reports_specific_failure": .1, "monitor_agent_blocked": .1, "monitor_unsafe": .1},
     {"breach": "over_budget"}, "continue", "default"),
])
def test_monitor_verdict_rule_table(answers, facts, expected, rule):
    result = compute_decision("monitor_verdict", answers, facts)
    assert (result["choice"], result["rule"]) == (expected, rule)


@pytest.mark.parametrize("answers,facts,expected,rule", [
    ({"large_change": .1, "needs_person": .1, "external_side_effects": .1}, {"risk": "high"}, "yes", "high_risk"),
    ({"large_change": .9, "needs_person": .1, "external_side_effects": .1}, {"risk": "low"}, "yes", "large_unattended"),
    ({"large_change": .9, "needs_person": .9, "external_side_effects": .1}, {"risk": "low"}, "no", "default"),
    ({"large_change": .1, "needs_person": .1, "external_side_effects": .9}, {"risk": "low"}, "yes", "external_side_effects"),
    ({"large_change": .1, "needs_person": .1, "external_side_effects": .1}, {"risk": "low"}, "no", "default"),
])
def test_needs_monitor_rule_table(answers, facts, expected, rule):
    result = compute_decision("needs_monitor", answers, facts)
    assert (result["choice"], result["rule"]) == (expected, rule)


def test_locality_noul_maps_to_label_values():
    assert compute_decision("locality", {"locality": .9}, {})["choice"] == "local"
    assert compute_decision("locality", {"locality": .2}, {})["choice"] == "any"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 1.5, -0.1, True, "0.9"])
def test_missing_or_invalid_evidence_refuses_never_fails_open(bad):
    result = compute_decision("rejection_action", {"rejection_environment": .1,
                                                   "rejection_spec_conflict": .1,
                                                   "rejection_needs_human": bad},
                              {"fix_count": 0, "is_fix_task": False})
    assert result["choice"] is None and result["rule"] == "missing_evidence"


def test_facts_decide_without_needing_evidence():
    result = compute_decision("rejection_action", {}, {"fix_count": 2})
    assert result["choice"] == "escalate" and result["consulted"] == [] and result["confidence"] == 1.0


def test_uncertain_band_follows_the_book():
    assert D.noul_uncertain(0.35) and D.noul_uncertain(0.5) and D.noul_uncertain(0.65)
    assert not D.noul_uncertain(0.34) and not D.noul_uncertain(0.66)
    assert D.choice_uncertain(0.49) and not D.choice_uncertain(0.5)
    inside = compute_decision("locality", {"locality": .6}, {})
    outside = compute_decision("locality", {"locality": .95}, {})
    assert inside["uncertain"] is True and outside["uncertain"] is False
    assert inside["confidence"] == pytest.approx(0.6) and outside["confidence"] == pytest.approx(0.95)


def test_independent_task_count_matches_flow_graph_rule():
    labels = {"T1": {"context": {"depends_on": []}}, "T2": {"context": {"depends_on": ["T1"]}},
              "T3": {"context": {"depends_on": []}}, "T4": {"context": {"depends_on": []}}}
    assert D.independent_task_count("T1", labels) == 2   # T3, T4 (T2 depends on T1)
    assert D.independent_task_count("T2", labels) == 2   # T3, T4 (T2 reaches T1)
    assert D.independent_task_count("T3", labels) == 3
