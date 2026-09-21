from __future__ import annotations

import copy
import warnings
from typing import Callable, Dict, List

from .labelset import check_labelset, globs_overlap


_ALLOWED = ("labels.", "assignments", "watch.", "context.allowed_paths", "context.depends_on")


def _set_field(label: dict, field: str, value) -> bool:
    if field.startswith("labels."):
        label.setdefault("labels", {})[field.split(".", 1)[1]] = value
    elif field == "assignments":
        label["assignments"] = value
    elif field.startswith("watch."):
        label.setdefault("watch", {})[field.split(".", 1)[1]] = value
    elif field == "context.allowed_paths":
        label.setdefault("context", {})["allowed_paths"] = value
    elif field == "context.depends_on":
        label.setdefault("context", {})["depends_on"] = value
    else:
        return False
    return True


def _would_overlap(labels: Dict[str, dict]) -> bool:
    ids = sorted(labels)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            if set(labels[left].get("context", {}).get("depends_on", [])) & {right}:
                continue
            if set(labels[right].get("context", {}).get("depends_on", [])) & {left}:
                continue
            for a in labels[left].get("context", {}).get("allowed_paths", []):
                for b in labels[right].get("context", {}).get("allowed_paths", []):
                    if globs_overlap(a, b):
                        return True
    return False


def _has_cycle(labels: Dict[str, dict]) -> bool:
    visiting = set()
    visited = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for dep in labels.get(task_id, {}).get("context", {}).get("depends_on", []):
            if dep in labels and visit(dep):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    return any(visit(task_id) for task_id in labels)


def effective_labels(frozen: Dict[str, dict], events: List[dict], load_added: Callable[[str], dict]) -> Dict[str, dict]:
    labels = copy.deepcopy(frozen)
    submitted = set()
    states = {}
    for event in events:
        event_task = event.get("task_id")
        event_kind = event.get("type")
        if event_task:
            states[event_task] = {
                "claimed": "claimed", "heartbeat": "working", "input_required": "input-required",
                "input_answered": "working", "submitted": "submitted", "accepted": "accepted",
                "rejected": "rejected", "failed": "failed", "canceled": "canceled",
                "lease_expired": "stale", "released": "released"}.get(event_kind, states.get(event_task, "planned"))
        if event.get("type") == "submitted" and event.get("task_id"):
            submitted.add(event["task_id"])
        if event.get("agent_id") is not None:
            continue
        kind, task_id = event.get("type"), event.get("task_id")
        if kind == "task_added":
            try:
                added = load_added(event["label_file"])
            except Exception as exc:
                warnings.warn("task_added %s could not be loaded: %s" % (task_id, exc), RuntimeWarning)
                continue
            if isinstance(added, dict):
                labels[task_id] = copy.deepcopy(added)
            continue
        if kind == "label_removed":
            if states.get(task_id, "planned") not in ("planned", "released", "rejected"):
                warnings.warn("ignored removal of non-open task %s" % task_id, RuntimeWarning)
                continue
            labels.pop(task_id, None)
            continue
        if kind != "label_changed" or task_id not in labels:
            continue
        field = event.get("field", "")
        if field == "lane":
            field = "labels.lane"
        if not any(field == allowed or field.startswith(allowed) for allowed in _ALLOWED):
            warnings.warn("ignored label change to %s" % field, RuntimeWarning)
            continue
        if field == "acceptance" and task_id in submitted:
            warnings.warn("ignored acceptance change after submit for %s" % task_id, RuntimeWarning)
            continue
        if field == "labels.lane" and not event.get("reason"):
            warnings.warn("ignored lane change without reason for %s" % task_id, RuntimeWarning)
            continue
        candidate = copy.deepcopy(labels)
        if not _set_field(candidate[task_id], field, event.get("new")):
            warnings.warn("ignored label change to %s" % field, RuntimeWarning)
            continue
        roster = frozen.get("__roster__") if isinstance(frozen, dict) else None
        if _has_cycle(candidate):
            warnings.warn("ignored label change creating a dependency cycle for %s" % task_id, RuntimeWarning)
            continue
        if _would_overlap(candidate):
            warnings.warn("ignored label change creating ownership overlap for %s" % task_id, RuntimeWarning)
            continue
        if roster is not None and check_labelset(candidate, roster):
            warnings.warn("ignored invalid label change for %s" % task_id, RuntimeWarning)
            continue
        labels = candidate
    labels.pop("__roster__", None)
    return labels


def fix_label(parent: dict, failed_entries: List[dict], n: int) -> dict:
    label = copy.deepcopy(parent)
    parent_id = parent["task_id"]
    label["task_id"] = "%s.fix%d" % (parent_id, n)
    label["title"] = "Fix %s" % parent.get("title", parent_id)
    label["fixes"] = parent_id
    label.setdefault("labels", {})["role"] = "fixer"
    label["routing"] = {"executor": None, "model": None, "resolved_from": None}
    entries = copy.deepcopy(failed_entries)
    if len(entries) < 2:
        passing = next((copy.deepcopy(x) for x in parent.get("acceptance", []) if x not in failed_entries), None)
        if passing is not None:
            entries.append(passing)
    label["acceptance"] = entries[:5]
    context = label.setdefault("context", {})
    context["depends_on"] = []
    label["assignments"] = [{"kind": "executor", "role": "fixer", "model_tier": parent.get("labels", {}).get("model_tier"),
                              "executor": None, "trigger": "ready"}]
    provenance = label.setdefault("provenance", {})
    reason = provenance.get("lane_reason") or "fix task"
    provenance["lane_reason"] = "%s (fix of %s)" % (reason, parent_id)
    return label
