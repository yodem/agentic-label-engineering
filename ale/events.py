from __future__ import annotations

import fcntl
import functools
import json
import os
from typing import Dict, List, Optional

from . import SCHEMA_VERSION
from .validate import load_schema, validate

MAX_EVENT_BYTES = 4096
LIVE = ("claimed", "working", "input-required")
TERMINAL = ("accepted", "failed", "canceled")
_OPEN = ("planned", "released", "rejected")
_NEEDS_EVIDENCE = ("verified", "accepted", "rejected")
_AUTHORITY = ("verified", "accepted", "rejected", "failed", "canceled", "lease_expired", "released", "input_answered",
              "task_added", "label_changed", "label_removed", "spawned", "integrated")
_DYNAMIC_EVENT_FIELDS = {
    "task_added": ("label_file", "reason"),
    "label_changed": ("field", "old", "new", "reason"),
    "label_removed": ("reason",),
    "spawned": ("agent_id_minted", "assignment_kind", "executor", "model"),
    "integrated": ("commit",),
}


class EventError(Exception):
    pass


@functools.lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    return load_schema(name)


def make_event(type: str, run_id: str, ts: float, task_id: Optional[str] = None,
               agent_id: Optional[str] = None, attempt: Optional[int] = None, **extra) -> dict:
    ev = {"schema_version": SCHEMA_VERSION, "ts": float(ts), "run_id": run_id, "task_id": task_id,
          "agent_id": agent_id, "attempt": attempt, "type": type}
    ev.update(extra)
    return ev


def check_event(ev: dict) -> List[str]:
    errs = validate(ev, _schema("event.schema.json"))
    if errs:
        return errs
    for key in _schema("event_types.json").get(ev["type"], []):
        if key not in ev:
            errs.append("event %s: missing key %r" % (ev["type"], key))
    for key in _DYNAMIC_EVENT_FIELDS.get(ev["type"], []):
        if key not in ev:
            errs.append("event %s: missing key %r" % (ev["type"], key))
    if ev["type"] in _NEEDS_EVIDENCE and not ev.get("evidence"):
        errs.append("event %s: evidence must be non-empty" % ev["type"])
    return errs


def append_event(path: str, ev: dict) -> None:
    errs = check_event(ev)
    if errs:
        raise EventError("; ".join(errs))
    data = (json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_EVENT_BYTES:
        raise EventError("event is %d bytes, limit %d" % (len(data), MAX_EVENT_BYTES))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data)
    finally:
        os.close(fd)


def read_events(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        raw = f.read()
    lines = raw.split(b"\n")
    if raw and not raw.endswith(b"\n"):
        lines = lines[:-1]
    return [json.loads(line) for line in lines if line.strip()]


def _new_task() -> dict:
    return {"state": "planned", "claimable": False, "attempt": 1, "owner": None, "started_ts": None,
            "last_heartbeat_ts": None, "submitted_ts": None, "last_step": None, "step_changed_ts": None, "steps": [],
            "files_modified": [], "pending": [], "next_steps": [], "waiting_on": None, "summary": None,
            "notes": [], "tokens": 0, "cost_usd": 0.0, "rejections": 0, "last_reject_reason": None,
            "evidence": None, "breaches_seen": [], "assignees": [], "integrated": False, "blocked_by": []}


def _deps_ok(task_id: str, tasks: Dict[str, dict], labels: Dict[str, dict]) -> bool:
    return all(tasks.get(d, {}).get("state") == "accepted" for d in labels[task_id]["context"]["depends_on"])


def _apply(st: dict, ev: dict, tasks: Dict[str, dict], labels: Dict[str, dict]) -> None:
    kind, agent, ts = ev["type"], ev.get("agent_id"), ev["ts"]
    if kind in _AUTHORITY and agent is not None:
        return
    is_owner = st["owner"] is not None and agent == st["owner"]
    if kind == "claimed":
        if (st["owner"] is None and st["state"] in _OPEN and ev.get("attempt") == st["attempt"]
                and _deps_ok(ev["task_id"], tasks, labels)):
            st.update(state="claimed", owner=agent, started_ts=ts, last_heartbeat_ts=ts, step_changed_ts=ts,
                      submitted_ts=None)
            if agent and agent not in st["assignees"]:
                st["assignees"].append(agent)
    elif kind == "heartbeat":
        if is_owner and st["state"] in LIVE:
            if st["state"] != "input-required":
                st["state"] = "working"
            st["last_heartbeat_ts"] = ts
            if ev["step"] != st["last_step"]:
                st["last_step"], st["step_changed_ts"] = ev["step"], ts
                st["steps"].append(ev["step"])
            for path in ev.get("files_modified") or []:
                if path not in st["files_modified"]:
                    st["files_modified"].append(path)
            for key in ("pending", "next_steps"):
                if ev.get(key) is not None:
                    st[key] = list(ev[key])
    elif kind == "note":
        if is_owner:
            st["notes"] = (st["notes"] + [ev["text"]])[-5:]
    elif kind == "input_required":
        if is_owner and st["state"] in LIVE:
            st["state"], st["waiting_on"] = "input-required", ev["question"]
    elif kind == "input_answered":
        if st["state"] == "input-required":
            st.update(state="working", waiting_on=None, last_heartbeat_ts=ts, step_changed_ts=ts)
    elif kind == "submitted":
        if is_owner and st["state"] in LIVE:
            st["state"], st["summary"], st["submitted_ts"] = "submitted", ev["summary"], ts
    elif kind == "verified":
        st["evidence"] = ev["evidence"]
    elif kind == "accepted":
        if st["state"] == "submitted":
            st.update(state="accepted", owner=None, evidence=ev["evidence"])
    elif kind == "rejected":
        if st["state"] == "submitted":
            st.update(state="rejected", owner=None, evidence=ev["evidence"], last_reject_reason=ev["reason"])
            st["rejections"] += 1
            st["attempt"] += 1
    elif kind in ("failed", "canceled"):
        if st["state"] not in TERMINAL:
            st.update(state=kind, owner=None)
    elif kind == "lease_expired":
        if st["state"] in LIVE:
            st.update(state="stale", owner=None)
    elif kind == "released":
        if st["state"] == "stale":
            st["state"] = "released"
    elif kind == "usage":
        st["tokens"] += ev["gen_ai.usage.input_tokens"] + ev["gen_ai.usage.output_tokens"]
        st["cost_usd"] += ev.get("cost_usd") or 0.0
    elif kind == "breach":
        st["breaches_seen"].append([ev["breach"], st["attempt"]])
    elif kind == "spawned":
        minted = ev.get("agent_id_minted")
        if minted and minted not in st["assignees"]:
            st["assignees"].append(minted)
    elif kind == "integrated":
        st["integrated"] = True


def reduce_run(events: List[dict], labels: Dict[str, dict]) -> dict:
    tasks = {tid: _new_task() for tid in labels}
    run = {"tokens": 0, "cost_usd": 0.0, "started_ts": None, "finished": False, "breaches_seen": []}
    for index, ev in enumerate(events):
        kind = ev["type"]
        if kind == "run_started":
            run["started_ts"] = ev["ts"]
            continue
        if kind == "run_finished":
            run["finished"] = True
            continue
        if kind == "usage":
            run["tokens"] += ev["gen_ai.usage.input_tokens"] + ev["gen_ai.usage.output_tokens"]
            run["cost_usd"] += ev.get("cost_usd") or 0.0
        if kind == "breach" and ev.get("task_id") is None:
            run["breaches_seen"].append(ev["breach"])
            continue
        st = tasks.get(ev.get("task_id"))
        if st is not None:
            if ev.get("type") == "accepted" and st.get("state") == "rejected":
                fixed = any(label.get("fixes") == ev.get("task_id") and earlier.get("type") == "accepted"
                            for fix_id, label in labels.items()
                            for earlier in events[:index] if fix_id == earlier.get("task_id"))
                if fixed:
                    st["state"] = "submitted"
            _apply(st, ev, tasks, labels)
    for tid, st in tasks.items():
        missing = [dep for dep in labels[tid].get("context", {}).get("depends_on", []) if dep not in tasks]
        st["blocked_by"] = missing
        if missing:
            st["claimable"] = False
            st["breaches_seen"].append(["orphaned_dependency", st["attempt"]])
        else:
            st["claimable"] = st["owner"] is None and st["state"] in _OPEN and _deps_ok(tid, tasks, labels)
        if st["state"] == "planned" and st["claimable"]:
            st["state"] = "ready"
    for tid, label in labels.items():
        parent = label.get("fixes")
        if parent in tasks and tasks[tid]["state"] != "accepted":
            tasks[parent]["state"] = "fixing"
        elif parent in tasks and tasks[tid]["state"] == "accepted":
            parent_rejections = [i for i, event in enumerate(events)
                                 if event.get("task_id") == parent and event.get("type") == "rejected"]
            fix_accepts = [i for i, event in enumerate(events)
                           if event.get("task_id") == tid and event.get("type") == "accepted"]
            if (tasks[parent]["state"] != "accepted" and fix_accepts
                    and (not parent_rejections or max(fix_accepts) > max(parent_rejections))):
                tasks[parent]["state"] = "submitted"
    return {"tasks": tasks, "run": run}
