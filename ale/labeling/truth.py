from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .cascade import FIELDS


def _authority(ev: dict) -> bool:
    return ev.get("agent_id") is None


def _key(ev: dict) -> Optional[Tuple[str, str]]:
    task_id = ev.get("task_id")
    field = ev.get("field")
    if not isinstance(task_id, str) or field not in FIELDS:
        return None
    return task_id, field


def truth_for(events: List[dict], labels: Dict[str, dict]) -> Dict[Tuple[str, str], dict]:
    accepted_at: Dict[str, int] = {}
    adjudicated: Dict[Tuple[str, str], str] = {}
    relabeled_before_accept: Dict[Tuple[str, str], str] = {}
    planner_votes: Dict[Tuple[str, str], Optional[str]] = {}
    judge_votes: Dict[Tuple[str, str], Optional[str]] = {}

    for index, ev in enumerate(events):
        task_id = ev.get("task_id")
        if task_id not in labels:
            continue
        kind = ev.get("type")
        if kind == "accepted" and _authority(ev):
            accepted_at[task_id] = index
        elif kind == "adjudicated" and _authority(ev):
            key = _key(ev)
            if key is not None:
                adjudicated[key] = ev.get("value")
        elif kind == "label_vote":
            key = _key(ev)
            if key is None:
                continue
            by = ev.get("by")
            if by == "planner":
                planner_votes[key] = ev.get("value")
            elif isinstance(by, str) and by.startswith("judge:"):
                judge_votes[key] = ev.get("value")

    for index, ev in enumerate(events):
        if ev.get("type") != "relabeled" or not _authority(ev):
            continue
        key = _key(ev)
        if key is None:
            continue
        task_id, _field = key
        accept_index = accepted_at.get(task_id)
        if accept_index is not None and index < accept_index:
            relabeled_before_accept[key] = ev.get("new")

    out: Dict[Tuple[str, str], dict] = {}
    for key in sorted(adjudicated):
        out[key] = {"value": adjudicated[key], "basis": "adjudicated"}

    for task_id in sorted(labels):
        if task_id not in accepted_at:
            continue
        for field in FIELDS:
            key = (task_id, field)
            if key in out:
                continue
            if key in relabeled_before_accept:
                out[key] = {"value": relabeled_before_accept[key], "basis": "relabeled_then_accepted"}
                continue
            if key in planner_votes and key in judge_votes:
                value = planner_votes[key]
                if value == judge_votes[key] and value is not None and value != "other":
                    out[key] = {"value": value, "basis": "agreement_then_accepted"}
    return out


def adjudication_queue(events: List[dict], labels: Dict[str, dict]) -> List[dict]:
    planner_votes: Dict[Tuple[str, str], Optional[str]] = {}
    judge_votes: Dict[Tuple[str, str], Optional[str]] = {}
    adjudicated = set()

    for ev in events:
        if ev.get("task_id") not in labels:
            continue
        key = _key(ev)
        if key is None:
            continue
        kind = ev.get("type")
        if kind == "adjudicated" and _authority(ev):
            adjudicated.add(key)
        elif kind == "label_vote":
            by = ev.get("by")
            if by == "planner":
                planner_votes[key] = ev.get("value")
            elif isinstance(by, str) and by.startswith("judge:"):
                judge_votes[key] = ev.get("value")

    out: List[dict] = []
    for key in sorted(planner_votes):
        if key in adjudicated or key not in judge_votes:
            continue
        planner = planner_votes[key]
        judge = judge_votes[key]
        if judge is None or planner == judge:
            continue
        task_id, field = key
        out.append({"task_id": task_id, "field": field, "planner": planner, "judge": judge})
    return out
