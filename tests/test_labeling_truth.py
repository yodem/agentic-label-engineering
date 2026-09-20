from __future__ import annotations

from ale.labeling.truth import adjudication_queue, truth_for


def _ev(type_, task_id="T1", agent_id=None, **extra):
    ev = {"type": type_, "task_id": task_id, "agent_id": agent_id}
    ev.update(extra)
    return ev


def _labels(*task_ids):
    return {
        task_id: {"labels": {"role": "backend", "model_tier": "standard", "risk": "low", "effort": "M"}}
        for task_id in task_ids
    }


def test_truth_last_adjudicated_wins():
    events = [
        _ev("adjudicated", field="role", value="backend", by="human"),
        _ev("adjudicated", field="role", value="frontend", by="human"),
    ]
    assert truth_for(events, _labels("T1")) == {
        ("T1", "role"): {"value": "frontend", "basis": "adjudicated"}
    }


def test_truth_adjudicated_beats_later_relabel():
    events = [
        _ev("adjudicated", field="role", value="backend", by="human"),
        _ev("relabeled", field="role", old="backend", new="docs", reason="fix"),
        _ev("accepted", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1"))[("T1", "role")] == {
        "value": "backend",
        "basis": "adjudicated",
    }


def test_truth_relabeled_then_accepted_uses_last_relabel_before_acceptance():
    events = [
        _ev("relabeled", field="risk", old="low", new="medium", reason="shared"),
        _ev("relabeled", field="risk", old="medium", new="high", reason="auth"),
        _ev("accepted", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {
        ("T1", "risk"): {"value": "high", "basis": "relabeled_then_accepted"}
    }


def test_truth_relabel_after_acceptance_is_ignored():
    events = [
        _ev("accepted", evidence={"passed": True}),
        _ev("relabeled", field="risk", old="low", new="medium", reason="late"),
    ]
    assert truth_for(events, _labels("T1")) == {}


def test_truth_agreement_then_accepted_when_planner_and_judge_match():
    events = [
        _ev("label_vote", field="effort", by="planner", value="M", confidence=None),
        _ev("label_vote", field="effort", by="judge:cmd", value="M", confidence=0.8),
        _ev("accepted", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {
        ("T1", "effort"): {"value": "M", "basis": "agreement_then_accepted"}
    }


def test_truth_accepted_with_agent_id_is_ignored():
    events = [
        _ev("label_vote", field="role", by="planner", value="backend", confidence=None),
        _ev("label_vote", field="role", by="judge:cmd", value="backend", confidence=0.9),
        _ev("accepted", agent_id="worker", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {}


def test_truth_judge_other_never_creates_agreement_truth():
    events = [
        _ev("label_vote", field="role", by="planner", value="other", confidence=None),
        _ev("label_vote", field="role", by="judge:cmd", value="other", confidence=0.9),
        _ev("accepted", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {}


def test_truth_judge_null_never_creates_agreement_truth():
    events = [
        _ev("label_vote", field="role", by="planner", value=None, confidence=None),
        _ev("label_vote", field="role", by="judge:cmd", value=None, confidence=None),
        _ev("accepted", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {}


def test_truth_ignores_pairs_for_tasks_not_in_labels():
    events = [
        _ev("label_vote", task_id="T2", field="effort", by="planner", value="S", confidence=None),
        _ev("label_vote", task_id="T2", field="effort", by="judge:cmd", value="S", confidence=0.8),
        _ev("accepted", task_id="T2", evidence={"passed": True}),
    ]
    assert truth_for(events, _labels("T1")) == {}


def test_adjudication_queue_sorted_and_contains_disagreements():
    events = [
        _ev("label_vote", task_id="T2", field="risk", by="planner", value="low", confidence=None),
        _ev("label_vote", task_id="T2", field="risk", by="judge:cmd", value="high", confidence=0.8),
        _ev("label_vote", task_id="T1", field="role", by="planner", value="backend", confidence=None),
        _ev("label_vote", task_id="T1", field="role", by="judge:cmd", value="frontend", confidence=0.8),
    ]
    assert adjudication_queue(events, _labels("T1", "T2")) == [
        {"task_id": "T1", "field": "role", "planner": "backend", "judge": "frontend"},
        {"task_id": "T2", "field": "risk", "planner": "low", "judge": "high"},
    ]


def test_adjudication_queue_excludes_adjudicated_pairs():
    events = [
        _ev("label_vote", field="role", by="planner", value="backend", confidence=None),
        _ev("label_vote", field="role", by="judge:cmd", value="frontend", confidence=0.8),
        _ev("adjudicated", field="role", value="frontend", by="human"),
    ]
    assert adjudication_queue(events, _labels("T1")) == []


def test_adjudication_queue_excludes_null_judge_votes():
    events = [
        _ev("label_vote", field="role", by="planner", value="backend", confidence=None),
        _ev("label_vote", field="role", by="judge:cmd", value=None, confidence=None),
    ]
    assert adjudication_queue(events, _labels("T1")) == []
