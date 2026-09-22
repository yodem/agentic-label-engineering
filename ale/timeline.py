from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple


_META_KEYS = {"schema_version", "ts", "run_id", "task_id", "agent_id", "attempt", "type"}
_CACHE_READ_KEYS = ("gen_ai.usage.cache_read_input_tokens", "cache_read_input_tokens")
_CACHE_WRITE_KEYS = (
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.usage.cache_write_input_tokens",
    "cache_write_input_tokens",
)


def _value(event: dict, keys: Tuple[str, ...]) -> int:
    for key in keys:
        if key in event:
            return int(event.get(key) or 0)
    return 0


def _changed(event: dict) -> str:
    kind = event.get("type", "")
    if kind in ("verified", "accepted", "rejected"):
        results = (event.get("evidence") or {}).get("results", [])
        if results:
            return ", ".join("%s %s" % (item.get("id", "?"), "ok" if item.get("ok") else "FAIL")
                              for item in results)
    if kind == "task_added":
        return "added %s" % event.get("label_file", "")
    if kind == "label_changed":
        return "%s: %s -> %s" % (event.get("field", ""), event.get("old", ""), event.get("new", ""))
    if kind == "label_removed":
        return "removed: %s" % event.get("reason", "")
    if kind == "spawned":
        return "assigned %s/%s via %s" % (
            event.get("model", ""), event.get("assignment_kind", ""), event.get("executor", "")
        )
    if kind == "integrated":
        commit = event.get("commit", "")
        return "integrated: %s" % (commit[:7] if commit else event.get("branch", event.get("result", "")))
    if kind == "monitor_verdict":
        return "%s: %s" % (event.get("verdict", ""), event.get("text", "")[:100])
    if kind == "labeled":
        labels = event.get("labels") or {}
        return "labels: %s" % ", ".join(str(labels.get(key, "")) for key in
                                           ("role", "model_tier", "lane", "risk", "effort"))
    if kind == "usage":
        return "in=%s out=%s model=%s" % (
            event.get("gen_ai.usage.input_tokens", 0), event.get("gen_ai.usage.output_tokens", 0),
            event.get("gen_ai.request.model", event.get("model", "")))
    changed = [(key, value) for key, value in event.items() if key not in _META_KEYS]
    if not changed:
        return ""
    return ", ".join("%s=%s" % (key, value) for key, value in changed)


def timeline(events: List[dict], labels: Dict[str, dict]) -> List[dict]:
    """Convert events to stable, chronologically ordered display rows."""
    ordered = sorted(enumerate(events), key=lambda pair: (float(pair[1].get("ts", 0)), pair[0]))
    if not ordered:
        return []
    start = float(ordered[0][1].get("ts", 0))
    rows = []
    for _, event in ordered:
        elapsed = float(event.get("ts", 0)) - start
        rows.append({
            "relative_time": elapsed,
            "type": event.get("type"),
            "task": event.get("task_id"),
            "agent": event.get("agent_id") or event.get("agent_id_minted"),
            "changed": _changed(event),
        })
    return rows


def format_timeline(rows: List[dict]) -> List[str]:
    lines = []
    for row in rows:
        elapsed = row.get("relative_time", row.get("ts", 0))
        if isinstance(elapsed, (float, int)):
            elapsed = ("%s" % int(elapsed)) if float(elapsed).is_integer() else ("%.3f" % elapsed).rstrip("0").rstrip(".")
        line = ("+%ss %s %s %s %s" % (
            elapsed,
            row.get("type", ""),
            row.get("task") or "-",
            row.get("agent") or "-",
            row.get("changed", ""),
        )).rstrip()
        lines.append(line[:160])
    return lines


def _blank_tokens() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _new_summary() -> dict:
    return {
        "tokens": _blank_tokens(), "wall_seconds": {}, "attempts": 0,
        "breaches": [], "model": None, "executor": None, "files_touched": [], "cost": None,
    }


def _add_usage(summary: dict, event: dict) -> None:
    summary["tokens"]["input"] += int(event.get("gen_ai.usage.input_tokens") or 0)
    summary["tokens"]["output"] += int(event.get("gen_ai.usage.output_tokens") or 0)
    summary["tokens"]["cache_read"] += _value(event, _CACHE_READ_KEYS)
    summary["tokens"]["cache_write"] += _value(event, _CACHE_WRITE_KEYS)


def _state_changes(events: List[dict], task_id: str) -> List[Tuple[float, str]]:
    state = "planned"
    out = [(0.0, state)]
    for event in sorted(events, key=lambda e: float(e.get("ts", 0))):
        if event.get("task_id") != task_id:
            continue
        kind = event.get("type")
        new = {"claimed": "claimed", "heartbeat": "working", "input_required": "input-required",
               "input_answered": "working", "submitted": "submitted", "accepted": "accepted",
               "rejected": "rejected", "failed": "failed", "canceled": "canceled",
               "lease_expired": "stale", "released": "released"}.get(kind)
        if new and new != state:
            state = new
            out.append((float(event.get("ts", 0)), state))
    return out


def _wall_seconds(events: List[dict], task_id: str) -> dict:
    changes = _state_changes(events, task_id)
    if not changes:
        return {}
    run_starts = [float(e.get("ts", 0)) for e in events if e.get("type") == "run_started"]
    start_ts = min(run_starts) if run_starts else changes[0][0]
    changes[0] = (start_ts, changes[0][1])
    end = max([float(e.get("ts", 0)) for e in events] or [changes[0][0]])
    out = defaultdict(float)
    for (start, state), (finish, _) in zip(changes, changes[1:] + [(end, None)]):
        out[state] += max(0.0, finish - start)
    return dict(out)


def _resolved(label: dict, events: List[dict], task_id: str) -> Tuple[Optional[str], Optional[str]]:
    routing = label.get("routing", {})
    model, executor = routing.get("model"), routing.get("executor")
    for event in events:
        if event.get("task_id") == task_id and event.get("type") in ("spawned", "usage"):
            model = event.get("model") or event.get("gen_ai.request.model") or model
            executor = event.get("executor") or executor
    return model, executor


def task_metadata(events: List[dict], labels: Dict[str, dict], roster: dict,
                  prices: Optional[dict] = None) -> dict:
    """Summarize usage and lifecycle data without reading external state."""
    tasks = {tid: _new_summary() for tid in labels}
    agents = {}
    totals = _new_summary()
    task_events = {tid: [e for e in events if e.get("task_id") == tid] for tid in labels}
    for tid, summary in tasks.items():
        summary["wall_seconds"] = _wall_seconds(events, tid)
        attempts = [e.get("attempt") for e in task_events[tid] if e.get("attempt") is not None]
        summary["attempts"] = max(attempts or [1])
        summary["breaches"] = [e.get("breach") for e in task_events[tid] if e.get("type") == "breach"]
        summary["model"], summary["executor"] = _resolved(labels[tid], events, tid)
        files = []
        for event in task_events[tid]:
            evidence = event.get("evidence") or {}
            paths = evidence.get("files", evidence.get("files_touched", evidence.get("files_modified", [])))
            for path in paths:
                if path not in files:
                    files.append(path)
            for path in event.get("files", []):
                if path not in files:
                    files.append(path)
        summary["files_touched"] = files
    for event in events:
        agent_id = event.get("agent_id") or event.get("agent_id_minted")
        if event.get("type") == "spawned" and agent_id:
            agent = agents.setdefault(agent_id, _new_summary())
            agent["model"] = event.get("model") or agent.get("model")
            agent["executor"] = event.get("executor") or agent.get("executor")
            agent["attempts"] = max(agent.get("attempts", 0), int(event.get("attempt") or 1))
        elif event.get("type") == "claimed" and agent_id:
            agent = agents.setdefault(agent_id, _new_summary())
            agent["attempts"] = max(agent.get("attempts", 0), int(event.get("attempt") or 1))
        if event.get("type") != "usage":
            continue
        tid, aid = event.get("task_id"), event.get("agent_id")
        destinations = [totals]
        if tid in tasks:
            destinations.append(tasks[tid])
        if aid is not None:
            agents.setdefault(aid, _new_summary())
            destinations.append(agents[aid])
        for destination in destinations:
            _add_usage(destination, event)
        cost = event.get("cost_usd")
        if cost is not None:
            for destination in destinations:
                destination["cost"] = (destination["cost"] or 0.0) + float(cost)
    if prices is not None:
        for summary in list(tasks.values()) + list(agents.values()) + [totals]:
            # Explicit event costs take precedence; otherwise price input/output.
            if summary["cost"] is None:
                model = summary.get("model")
                rate = prices.get(model, {}) if model else {}
                summary["cost"] = (summary["tokens"]["input"] * float(rate.get("input_per_mtok", 0))
                                    + summary["tokens"]["output"] * float(rate.get("output_per_mtok", 0))) / 1000000.0
    return {"tasks": tasks, "agents": agents, "totals": totals}
