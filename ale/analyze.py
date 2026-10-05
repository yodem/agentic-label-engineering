"""``ale analyze``: score every indexed run against pre-committed thresholds.

Scoring is deterministic: each check reads a run's labels and ``events.jsonl`` and returns
``None`` when its premise does not hold (n/a, no case result) or ``(score, reason)``. Bars live
in ``ale/schema/analyze_thresholds.json`` and were committed before the first measurement.
No model is called anywhere in this module.
"""
from __future__ import annotations

import copy
import datetime
import functools
import glob
import json
import math
import os
import re
import statistics
import warnings
from typing import Callable, Dict, List, Optional, Tuple

from . import decisions as DECISIONS
from . import dynamic as D
from . import events as E
from . import harness as HARNESS
from .dispatch import worktree_mode
from .labeling.shadow import summarize_shadow
from .records import iso
from .validate import load_schema

_HERE = os.path.dirname(os.path.abspath(__file__))
THRESHOLDS_PATH = os.path.join(_HERE, "schema", "analyze_thresholds.json")
EXAMPLE_ROSTER = os.path.join(_HERE, "example_roster.json")
CLOSED = ("accepted", "failed", "canceled", "removed", "superseded")
EFFORTS = ("S", "M", "L")
# Keys the reducer reads unconditionally for event types that are not in event_types.json.
_EXTRA_REQUIRED = {"reopened": ("reason",)}
_USAGE_NUMBERS = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")


def load_thresholds(path=None) -> dict:
    with open(path or THRESHOLDS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _epoch(value) -> Optional[float]:
    """Epoch seconds from a number or an ISO 8601 string (``Z`` or ``+HH:MM`` offset, 3- or
    6-digit fractions, or a bare date); naive values are UTC. None when it does not parse."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


# --- loading ---------------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _required_keys() -> dict:
    return load_schema("event_types.json")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_event(event) -> bool:
    """An event the reducer can apply without raising: the lines that fail are skipped."""
    if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not _number(event.get("ts")):
        return False
    task_id = event.get("task_id")
    if task_id is not None and not isinstance(task_id, str):
        return False
    kind = event["type"]
    for key in list(_required_keys().get(kind, [])) + list(_EXTRA_REQUIRED.get(kind, ())):
        if key not in event:
            return False
    if kind == "usage" and not all(_number(event.get(key)) for key in _USAGE_NUMBERS):
        return False
    if kind == "usage" and not _number(event.get("gen_ai.usage.cache_read_input_tokens", 0)):
        return False
    return True


def run_key(run_dir: str) -> str:
    """The identity of a run: the realpath of its run dir.

    Run ids repeat (every plan compiled from ``plan.md`` is run ``plan``) and so do repository
    and run dir names (``/srv/a/project/.ale/runs/plan`` and ``/srv/b/project/.ale/runs/plan``),
    so only the directory itself identifies a run in case ids, the ledger and promotions."""
    return os.path.realpath(run_dir)


def run_label(run_dir: str) -> str:
    """The display label of a run in reports: ``<repo>:<run dir name>`` for ``<repo>/.ale/runs/<dir>``."""
    path = os.path.normpath(run_dir)
    runs = os.path.dirname(path)
    if os.path.basename(runs) == "runs" and os.path.basename(os.path.dirname(runs)) == ".ale":
        return "%s:%s" % (os.path.basename(os.path.dirname(os.path.dirname(runs))), os.path.basename(path))
    return os.path.basename(path)


def load_run(entry: dict) -> Optional[dict]:
    """Read one indexed run; None when its directory or event log is gone or unreadable.

    Lines of ``events.jsonl`` and label files that do not parse are skipped and counted in
    ``skipped_lines``; the run is scored on what does parse."""
    run_dir = entry.get("run_dir")
    if not isinstance(run_dir, str) or not os.path.isdir(run_dir):
        return None
    events_path = os.path.join(run_dir, "events.jsonl")
    if not os.path.isfile(events_path):
        return None
    skipped = 0
    labels: Dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "labels", "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                item = json.load(handle)
        except (OSError, ValueError, UnicodeDecodeError):
            skipped += 1
            continue
        if not isinstance(item, dict) or not isinstance(item.get("labels", {}), dict):
            skipped += 1
            continue
        context = item.get("context")
        if not isinstance(context, dict):
            context = item["context"] = {}
        if not isinstance(context.get("depends_on"), list):
            context["depends_on"] = []
        task_id = item.get("task_id")
        labels[task_id if isinstance(task_id, str) else os.path.basename(path)[:-5]] = item
    events: List[dict] = []
    try:
        with open(events_path, "rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    skipped += 1
                    continue
                if not _valid_event(event):
                    skipped += 1
                    continue
                events.append(event)
    except OSError:
        return None   # unreadable (permissions, I/O): the caller skips it like a missing run
    run_id = next((e["run_id"] for e in events if isinstance(e.get("run_id"), str) and e["run_id"]),
                  None) or entry.get("run_id") or os.path.basename(run_dir)
    # The run's own record wins; the index entry is the fallback for runs that lack one.
    roster_hash = next((str(e["roster_hash"]) for e in events
                        if e["type"] == "labeled" and e.get("roster_hash")), None) or entry.get("roster_hash")
    ale_version = next((str(e["ale_version"]) for e in events
                        if e["type"] in ("run_started", "labeled") and e.get("ale_version")),
                       None) or entry.get("ale_version")
    return {"run_id": run_id, "run_key": run_key(run_dir), "run_label": run_label(run_dir), "run_dir": run_dir,
            "repo_root": entry.get("repo_root"), "labels": labels, "events": events,
            "skipped_lines": skipped, "roster_hash": roster_hash, "ale_version": ale_version}


# --- per-task outcome rows -------------------------------------------------------------------

def _tokens(event: dict) -> int:
    return (max(0, int(event["gen_ai.usage.input_tokens"])
                - int(event.get("gen_ai.usage.cache_read_input_tokens") or 0))
            + max(0, int(event["gen_ai.usage.output_tokens"])))


def _lead(event: dict) -> bool:
    return event.get("agent_id") is None


def _effective_labels(run: dict) -> Dict[str, dict]:
    labels_dir = os.path.join(run["run_dir"], "labels")

    def load_added(name: str) -> dict:
        if os.path.basename(name) != name:
            raise ValueError("unsafe label_file")
        with open(os.path.join(labels_dir, name), encoding="utf-8") as handle:
            return json.load(handle)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            labels = D.effective_labels(run["labels"], run["events"], load_added)
        except Exception:
            labels = copy.deepcopy(run["labels"])
    for item in labels.values():
        context = item.setdefault("context", {}) if isinstance(item, dict) else {}
        if not isinstance(context.get("depends_on"), list):
            context["depends_on"] = []
    return labels


def _run_started_ts(run: dict) -> Optional[float]:
    return next((e["ts"] for e in run["events"] if e["type"] == "run_started"), None)


def _first_ts(run: dict) -> Optional[float]:
    return _run_started_ts(run) or (run["events"][0]["ts"] if run["events"] else None)


def _last_ts(run: dict) -> Optional[float]:
    return max((e["ts"] for e in run["events"]), default=None)


def _row(run: dict, task_id: str, label: dict, task_events: List[dict], st: Optional[dict],
         removed: bool, started: Optional[float]) -> dict:
    values = label.get("labels") or {}
    routing = label.get("routing") or {}
    spawns = [e for e in task_events if e["type"] == "spawned"]
    executor_spawns = [e for e in spawns if e.get("assignment_kind") in (None, "executor")] or spawns
    first_spawn = executor_spawns[0] if executor_spawns else None
    # A dispatch --no-exec spawn launched nothing: the lead works the task in its own session.
    lead_work = bool(first_spawn and first_spawn.get("no_exec"))
    if first_spawn is None:
        spawned_by = "none"
    elif lead_work:
        spawned_by = "dispatch"
    elif first_spawn.get("executor") == "external" and not first_spawn.get("pane"):
        spawned_by = "register"
    else:
        spawned_by = "dispatch"
    claims = [e for e in task_events if e["type"] == "claimed"]
    deviations = [e["deviation"].get("code") for e in claims if isinstance(e.get("deviation"), dict)]
    verdicts = [e for e in task_events if e["type"] in ("accepted", "rejected") and _lead(e)]
    if verdicts:
        first_verify_passed = verdicts[0]["type"] == "accepted"
    else:
        verified = [e for e in task_events if e["type"] == "verified" and _lead(e)]
        first_verify_passed = (bool((verified[0].get("evidence") or {}).get("passed"))
                               if verified and isinstance(verified[0].get("evidence"), dict) else None)
    accepts = [e for e in verdicts if e["type"] == "accepted"]
    accepting = accepts[-1] if accepts else None
    evidence = (accepting or {}).get("evidence")
    verified_files = (list(evidence["files"]) if isinstance(evidence, dict)
                      and isinstance(evidence.get("files"), list) else None)
    verified_count = _evidence_files_count(evidence)
    # Any verification counts as proof the task changed files, a rejected one too: the
    # executor wrote them whether or not the acceptance commands then passed.
    verify_counts = [_evidence_files_count(e.get("evidence")) for e in task_events
                     if e["type"] in ("verified", "accepted", "rejected") and _lead(e)]
    any_verify_count = max([c for c in verify_counts if c is not None], default=None)
    integrations = [e for e in task_events if e["type"] == "integrated" and _lead(e)]
    if integrations and isinstance(integrations[-1].get("files"), list):
        files_changed = len(integrations[-1]["files"])
    else:
        files_changed = verified_count
    usage = [e for e in task_events if e["type"] == "usage"]
    asks: Dict[object, int] = {}
    for event in task_events:
        if event["type"] == "input_required":
            asks[event.get("attempt")] = asks.get(event.get("attempt"), 0) + 1
    changes = [e for e in task_events if e["type"] in ("label_changed", "relabeled") and _lead(e)]
    acceptance_relabeled = started is not None and any(
        e.get("field") == "acceptance" and e["ts"] >= started for e in changes)
    if lead_work:
        # The harness and model are the label's, not the spawn's (which names only the lead).
        assignment = next((item for item in label.get("assignments") or []
                           if isinstance(item, dict) and item.get("kind", "executor") == "executor"), {})
        executor, spawn_model = assignment.get("executor"), assignment.get("model")
        headless = False
    else:
        executor, spawn_model = (first_spawn or {}).get("executor"), (first_spawn or {}).get("model")
        headless = bool(first_spawn) and (HARNESS.mode_hint(executor) == "headless"
                                          or first_spawn.get("mode") == "headless")
    attempts = (st or {}).get("attempt") or max(
        [e.get("attempt") for e in task_events if isinstance(e.get("attempt"), int)] or [1])
    if removed:
        state = "removed"
    else:
        state = (st or {}).get("state", "unknown")
    integrated = bool((st or {}).get("integrated")) or bool(integrations)
    return {
        "run_key": run["run_key"], "run_label": run.get("run_label"), "task_id": task_id, "role": values.get("role"),
        "effort": values.get("effort"), "risk": values.get("risk"), "model_tier": values.get("model_tier"),
        "harness": HARNESS.normalize(routing.get("executor")) or HARNESS.normalize(executor),
        "model": routing.get("model") or spawn_model,
        "worktree_mode": worktree_mode(label), "spawned_by": spawned_by, "claimed": bool(claims),
        "claim_deviations": deviations,
        "claimed_by_lead": any(str(e.get("agent_id") or "").startswith("lead") for e in claims),
        "first_verify_passed": first_verify_passed, "attempts": attempts,
        "rejects": sum(1 for e in verdicts if e["type"] == "rejected"),
        "verified_files": verified_files, "verified_files_count": verified_count,
        "any_verify_files_count": any_verify_count,
        "acceptance_relabeled": acceptance_relabeled,
        "claim_to_accept_s": (accepting["ts"] - claims[0]["ts"]) if claims and accepting else None,
        "files_changed": files_changed, "usage_tokens": sum(_tokens(e) for e in usage) if usage else None,
        "headless": headless, "integrated": integrated,
        "input_required_max_per_attempt": max(asks.values()) if asks else 0,
        "state": state, "removed": removed,
    }


def _mark_superseded(tasks: Dict[str, dict], labels: Dict[str, dict]) -> None:
    """The board's rule (ale/board.py build_snapshot, health_verdict): a rejected fix task
    whose ``fixes`` parent is accepted is ``superseded``, a closed state, not an open one."""
    for task_id, st in tasks.items():
        parent = (labels.get(task_id) or {}).get("fixes")
        if st.get("state") == "rejected" and parent and (tasks.get(parent) or {}).get("state") == "accepted":
            st["state"] = "superseded"


def _analysis(run: dict) -> dict:
    cached = run.get("_analysis")
    if cached is not None:
        return cached
    labels = _effective_labels(run)
    state = E.reduce_run(run["events"], labels)
    removed = {e["task_id"] for e in run["events"]
               if e["type"] == "label_removed" and _lead(e) and e.get("task_id") in run["labels"]}
    removed -= set(labels)
    by_task: Dict[str, List[dict]] = {}
    for event in run["events"]:
        if event.get("task_id") is not None:
            by_task.setdefault(event["task_id"], []).append(event)
    _mark_superseded(state["tasks"], labels)
    started = _run_started_ts(run)
    rows = []
    for task_id in sorted(set(labels) | removed):
        label = labels.get(task_id) or run["labels"][task_id]
        rows.append(_row(run, task_id, label, by_task.get(task_id, []), state["tasks"].get(task_id),
                         task_id in removed, started))
    run["_analysis"] = {"rows": rows}
    return run["_analysis"]


def _evidence_files_count(evidence) -> Optional[int]:
    """Files a verify evidence record stands for; None when it records no file list.

    verify trims a long file list to fit the event (files_truncated) and keeps the full count
    in files_count, so a truncated list, even an empty one, still stands for files_count files."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("files"), list):
        return None
    files = evidence["files"]
    count = evidence.get("files_count")
    return count if _number(count) and count >= len(files) else len(files)


def task_rows(run: dict) -> List[dict]:
    """One outcome row per task, reduced with the same reducer ``ale status`` uses."""
    return _analysis(run)["rows"]


def run_status(run: dict, now: float, stale_after_s: float) -> str:
    """``done`` when no task is open; else ``active`` with an event in the last
    ``stale_after_s`` seconds, ``stale`` without one."""
    if all(row["state"] in CLOSED for row in task_rows(run)):
        return "done"
    last = _last_ts(run)
    if last is not None and now - last < stale_after_s:
        return "active"
    return "stale"


# --- checks ----------------------------------------------------------------------------------

def _verdict(ok: bool, good: str, bad: str) -> Tuple[float, str]:
    return (1.0, good) if ok else (0.0, bad)


def _first_pass(row, ctx):
    if row["first_verify_passed"] is None:
        return None
    return _verdict(row["first_verify_passed"], "accepted on its first verify",
                    "first verify rejected (%d attempts)" % row["attempts"])


def _path_scope_checked(row, ctx):
    if row["state"] != "accepted" or row["worktree_mode"] == "none" or row["verified_files"] is None:
        return None
    count = row["verified_files_count"]
    return _verdict(bool(count), "verified against %d files" % count,
                    "accepted with an empty verified file list")


def _write_has_worktree(row, ctx):
    if not (row["any_verify_files_count"] or row["integrated"] or row["spawned_by"] == "register"):
        return None
    return _verdict(row["worktree_mode"] != "none", "worktree.mode %s" % row["worktree_mode"],
                    "changed files with worktree.mode none")


def _dispatch_worktree(row, ctx):
    # A task with no worktree (think-only) needs none from dispatch.
    if row["worktree_mode"] == "none" or (row["spawned_by"] == "none" and not row["claimed"]):
        return None
    return _verdict(row["spawned_by"] == "dispatch", "worktree from dispatch",
                    "worktree from %s" % ("register-worktree" if row["spawned_by"] == "register"
                                          else "no spawn (claimed%s)" % (" by lead" if row["claimed_by_lead"] else "")))


def _resolved(row, ctx):
    if ctx["status"] not in ("stale", "done"):
        return None
    return _verdict(row["state"] in ("accepted", "removed", "superseded"), "task %s" % row["state"],
                    "%s run left the task %s" % (ctx["status"], row["state"]))


def _input_required_bounded(row, ctx):
    most = row["input_required_max_per_attempt"]
    return _verdict(most <= 3, "%d input_required per attempt at most" % most,
                    "%d input_required events in one attempt" % most)


def _usage_recorded(row, ctx):
    # A lead-claimed task runs in the lead's session even when its spawn names a headless executor.
    if not row["headless"] or row["claimed_by_lead"]:
        return None
    return _verdict(row["usage_tokens"] is not None, "usage recorded",
                    "headless task without a usage event")


def _acceptance_held(row, ctx):
    if not ctx["run_started"]:
        return None
    return _verdict(not row["acceptance_relabeled"], "acceptance unchanged since init-run",
                    "acceptance relabeled after init-run")


def _absolute_paths(ctx):
    paths = [e["worktree"] for e in ctx["run"]["events"] if e["type"] == "spawned" and e.get("worktree")]
    if not paths:
        return None
    relative = [p for p in paths if not (isinstance(p, str) and os.path.isabs(p))]
    return _verdict(not relative, "%d spawned worktrees, all absolute" % len(paths),
                    "relative spawned worktree: %s" % (relative[0] if relative else ""))


def _no_stale_open(ctx):
    return _verdict(ctx["status"] != "stale", "run is %s" % ctx["status"], "stale run with open tasks")


CHECKS: List[Tuple[str, str, Callable]] = [
    ("ale.task.first_pass", "task", _first_pass),
    ("ale.task.path_scope_checked", "task", _path_scope_checked),
    ("ale.task.write_has_worktree", "task", _write_has_worktree),
    ("ale.task.dispatch_worktree", "task", _dispatch_worktree),
    ("ale.task.resolved", "task", _resolved),
    ("ale.task.input_required_bounded", "task", _input_required_bounded),
    ("ale.task.usage_recorded", "task", _usage_recorded),
    ("ale.task.acceptance_held", "task", _acceptance_held),
    ("ale.run.absolute_paths", "run", _absolute_paths),
    ("ale.run.no_stale_open", "run", _no_stale_open),
]


# --- evaluation ------------------------------------------------------------------------------

def _mean(values: List[float]) -> Optional[float]:
    return (sum(values) / float(len(values))) if values else None


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _p90(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(0, int(math.ceil(0.9 * len(ordered))) - 1)])


def _score(runs: List[dict], thresholds: dict, now: float) -> tuple:
    """Case results, task rows and status counts for ``runs`` (already window-filtered)."""
    stale_after = float(thresholds.get("stale_after_hours", 72)) * 3600.0
    bars = thresholds.get("checks") or {}
    counts = {"active": 0, "stale": 0, "done": 0, "skipped": 0}
    cases: List[dict] = []
    rows_out: List[dict] = []
    unreadable: List[str] = []
    for run in runs:
        try:
            rows = task_rows(run)
            status = run_status(run, now, stale_after)
        except Exception as exc:   # one unreadable run never stops the report
            counts["skipped"] += 1
            unreadable.append("%s (%s)" % (run.get("run_dir"), type(exc).__name__))
            continue
        counts[status] += 1
        ctx = {"run": run, "status": status, "run_started": _run_started_ts(run) is not None}
        base = {"run_id": run["run_id"], "case_kind": "online", "config_hash": run.get("roster_hash"),
                "tool_version": run.get("ale_version"), "ts": _first_ts(run),
                "metadata": {"run_dir": run["run_dir"], "run_status": status}}

        label = run.get("run_label") or run_label(run["run_dir"])

        def record(check_id, case_id, category, result, task_id=None):
            if result is None:
                return
            score, reason = result
            bar = bars.get(check_id)
            item = dict(base, case_id=case_id, case_category=category, evaluator=check_id,
                        score=float(score), passed=bool(bar is None or score >= bar), reason=reason)
            item["metadata"] = dict(base["metadata"], label="%s/%s" % (label, task_id) if task_id else label,
                                    **({"task_id": task_id} if task_id else {}))
            cases.append(item)

        for row in rows:
            rows_out.append(row)
            category = "%s/%s" % (row["role"], row["effort"])
            for check_id, kind, fn in CHECKS:
                if kind == "task":
                    record(check_id, "%s/%s" % (run["run_key"], row["task_id"]), category, fn(row, ctx),
                           row["task_id"])
        for check_id, kind, fn in CHECKS:
            if kind == "run":
                record(check_id, run["run_key"], "run", fn(ctx))
    return cases, rows_out, counts, unreadable


def _checks(cases: List[dict], thresholds: dict) -> dict:
    bars = thresholds.get("checks") or {}
    min_n = int(thresholds.get("min_n", 3))
    out = {}
    for check_id, _kind, _fn in CHECKS:
        mine = [c for c in cases if c["evaluator"] == check_id]
        rate = _mean([c["score"] for c in mine])
        bar = bars.get(check_id)
        out[check_id] = {
            "n": len(mine), "passed": sum(1 for c in mine if c["passed"]), "rate": rate, "bar": bar,
            "breached": bool(bar is not None and rate is not None and len(mine) >= min_n and rate < bar),
            "examples": [c["case_id"] for c in mine if not c["passed"]][:5],
            "example_labels": [(c.get("metadata") or {}).get("label") or c["case_id"]
                               for c in mine if not c["passed"]][:5]}
    return out


def _metrics(rows: List[dict]) -> dict:
    groups: Dict[str, List[dict]] = {}
    for row in rows:
        if row["removed"]:
            continue
        key = "|".join(str(row.get(k)) for k in ("role", "model_tier", "harness", "model"))
        groups.setdefault(key, []).append(row)
    out = {}
    for key in sorted(groups):
        group = groups[key]
        accepted = [r for r in group if r["state"] == "accepted"]
        verified = [r for r in group if r["first_verify_passed"] is not None]
        durations = [r["claim_to_accept_s"] for r in accepted if r["claim_to_accept_s"] is not None]
        tokens = [r["usage_tokens"] for r in accepted if r["usage_tokens"] is not None]
        out[key] = {
            "tasks": len(group),
            "acceptance_rate": len(accepted) / float(len(group)),
            "first_pass_rate": _mean([1.0 if r["first_verify_passed"] else 0.0 for r in verified]),
            "attempts_per_accepted": _mean([float(r["attempts"]) for r in accepted]),
            "claim_to_accept_p50_s": float(_median(durations)) if durations else None,
            "claim_to_accept_p90_s": _p90(durations),
            "tokens_per_accepted": _mean([float(t) for t in tokens]),
            "unknown_usage_share": sum(1 for r in group if r["usage_tokens"] is None) / float(len(group)),
        }
    return out


def _deviations(rows: List[dict]) -> Dict[str, int]:
    """Declared claim deviations (``claimed.deviation.code``, e.g. ``ale claim --no-worktree``)
    counted by code. A count, not a check: the lead declared each one."""
    out: Dict[str, int] = {}
    for row in rows:
        for code in row.get("claim_deviations") or []:
            out[str(code)] = out.get(str(code), 0) + 1
    return dict(sorted(out.items()))


def _calibration(rows: List[dict], config: dict) -> dict:
    bucket_min = int(config.get("bucket_min_n", 5))
    group_min = int(config.get("group_min_n", 3))
    outlier = float(config.get("outlier_ratio", 2.0))
    live = [r for r in rows if not r["removed"]]
    timed = [r for r in live if r["state"] == "accepted" and r["claim_to_accept_s"] is not None]
    buckets = {}
    for effort in EFFORTS:
        mine = [r for r in timed if r["effort"] == effort]
        files = [r["files_changed"] for r in mine if r["files_changed"] is not None]
        buckets[effort] = {"n": len(mine), "median_s": float(_median([r["claim_to_accept_s"] for r in mine]))
                           if mine else None, "median_files": _median(files)}
    eligible = [buckets[e] for e in EFFORTS if buckets[e]["n"] >= bucket_min]
    monotonic = None
    if len(eligible) >= 2:
        monotonic = True
        for low, high in zip(eligible, eligible[1:]):
            if high["median_s"] < low["median_s"]:
                monotonic = False
            if (low["median_files"] is not None and high["median_files"] is not None
                    and high["median_files"] < low["median_files"]):
                monotonic = False
    groups: Dict[str, List[dict]] = {}
    for row in live:
        groups.setdefault("%s/%s/%s" % (row["role"], row["effort"], row["risk"]), []).append(row)
    stats = []
    for name, group in sorted(groups.items()):
        if len(group) < group_min:
            continue
        durations = [r["claim_to_accept_s"] for r in group if r["claim_to_accept_s"] is not None]
        stats.append({"group": name, "n": len(group),
                      "reject_rate": sum(1 for r in group if r["rejects"]) / float(len(group)),
                      "median_s": float(_median(durations)) if durations else None})
    base_reject = _median([s["reject_rate"] for s in stats])
    base_s = _median([s["median_s"] for s in stats if s["median_s"] is not None])
    outliers = []
    for item in stats:
        ratios = []
        if base_reject:
            ratios.append(item["reject_rate"] / base_reject)
        if base_s and item["median_s"] is not None:
            ratios.append(item["median_s"] / base_s)
        ratio = max(ratios) if ratios else None
        if ratio is not None and ratio >= outlier:
            outliers.append(dict(item, ratio=ratio))
    return {"effort_buckets": buckets, "effort_monotonic": monotonic, "outlier_groups": outliers}


def _roster_for(runs: List[dict]) -> dict:
    for run in runs:
        root = run.get("repo_root")
        if not root:
            continue
        try:
            with open(os.path.join(root, ".ale", "roster.json"), encoding="utf-8") as handle:
                roster = json.load(handle)
            if isinstance(roster, dict):
                return roster
        except (OSError, ValueError):
            continue
    with open(EXAMPLE_ROSTER, encoding="utf-8") as handle:
        return json.load(handle)


def _promotions(runs: List[dict]) -> dict:
    """Propose-only: judge promotion progress per field, never applied."""
    roster = _roster_for(runs)
    judge = roster.get("judge") or {}
    min_cases = int((roster.get("promotion") or {}).get("min_cases", (judge.get("bar") or {}).get("min_cases", 100)))
    judged = set(DECISIONS.judged_decision_ids())
    # Cases are keyed by the run key, never the raw run_id: every run baked from plan.md is
    # run ``plan``, and keying by it would merge their votes into one case per task.
    events = [dict(e, run_id=run["run_key"]) for run in runs for e in run["events"]
              if e["type"] in ("shadow_vote", "decision_outcome", "adjudicated", "accepted")]
    votes = [e for e in events if e["type"] == "shadow_vote" and e.get("decision") in judged]
    outcomes = [e for e in events if e["type"] == "decision_outcome" and e.get("decision") in judged]
    adjudications = [e for e in events if e["type"] == "adjudicated"
                     and (e.get("decision") or e.get("field")) in judged]
    accepted = {(e.get("run_id") or "", e.get("task_id")) for e in events if e["type"] == "accepted"}
    try:
        stats = summarize_shadow(votes, outcomes, adjudications, judge.get("bar") or {}, accepted)["decisions"]
    except Exception:
        stats = {}
    out = {}
    for field, mode in sorted((judge.get("modes") or {}).items()):
        item = stats.get(field) or {}
        cases = int(item.get("adjudicated_count") or 0)
        if cases >= min_cases and item.get("bar_met"):
            status = "propose promoting %s from %s: %d/%d cases, bar met" % (field, mode, cases, min_cases)
        else:
            status = "%s: %d/%d cases" % (mode, cases, min_cases)
        out[field] = {"cases": cases, "min_cases": min_cases, "status": status}
    return out


def _in_window(run: dict, lo: Optional[float], hi: Optional[float]) -> bool:
    last = _last_ts(run)
    if lo is None and hi is None:
        return True
    if last is None:
        return False
    return (lo is None or last >= lo) and (hi is None or last < hi)


def evaluate(runs: List[dict], thresholds: dict, now: float, since_s: Optional[float]) -> dict:
    """Score the runs whose last event falls in the window (``since_s`` None = every run)."""
    pattern = thresholds.get("exclude_run_ids")
    exclude = re.compile(pattern) if pattern else None
    skipped = 0
    kept = []
    for run in runs:
        if run is None or (exclude and (exclude.search(str(run.get("run_id") or ""))
                                        or exclude.search(os.path.basename(os.path.normpath(
                                            str(run.get("run_dir") or "")))))):
            skipped += 1
            continue
        kept.append(run)
    lo = None if since_s is None else now - since_s
    current = [r for r in kept if _in_window(r, lo, None)]
    cases, rows, counts, unreadable = _score(current, thresholds, now)
    counts["skipped"] += skipped
    previous = {}
    if since_s is not None:
        before = [r for r in kept if _in_window(r, now - 2 * since_s, now - since_s)]
        prev_cases, _rows, _counts, _bad = _score(before, thresholds, now - since_s)
        previous = {k: v["rate"] for k, v in _checks(prev_cases, thresholds).items() if v["rate"] is not None}
    return {
        "generated": iso(now),
        "window": {"since_s": since_s, "from": iso(lo) if lo is not None else None, "to": iso(now)},
        "min_n": int(thresholds.get("min_n", 3)),
        "saturation_windows": int(thresholds.get("saturation_windows", 4)),
        "runs": counts, "unreadable": unreadable,
        "skipped_lines": sum(r["skipped_lines"] for r in current),
        "checks": _checks(cases, thresholds),
        "metrics": _metrics(rows),
        "deviations": _deviations(rows),
        "calibration": _calibration(rows, thresholds.get("calibration") or {}),
        "promotions": _promotions(kept),   # cumulative: promotion needs every judged case
        "previous": previous,
        "case_results": cases,
    }


# --- findings, fixes, saturation -------------------------------------------------------------

def fix_statuses(report: dict, fixes: List[dict]) -> List[dict]:
    """Each fix record with the check's pass rate over runs started after it (its ``ts`` when
    that parses, else its ``date``): ``holding`` (n >= min_n, rate >= bar), ``regressed`` (n >= min_n, rate < bar),
    ``invalid`` (no parseable timestamp), else ``pending``."""
    min_n = int(report.get("min_n", 3))
    out = []
    def stamp(fix: dict) -> Optional[float]:
        # ``ts`` when it parses, else ``date``; None only when neither does.
        for key in ("ts", "date"):
            if fix.get(key) is not None:
                moment = _epoch(fix[key])
                if moment is not None:
                    return moment
        return None

    for fix in sorted((f for f in fixes if isinstance(f, dict)), key=lambda f: stamp(f) or 0.0):
        evaluator = fix.get("evaluator")
        since = stamp(fix)
        after = [c for c in report.get("case_results", [])
                 if c["evaluator"] == evaluator and since is not None and c.get("ts") is not None
                 and c["ts"] >= since]
        rate = _mean([c["score"] for c in after])
        bar = (report.get("checks", {}).get(evaluator) or {}).get("bar")
        if since is None:
            status = "invalid"   # no parseable ts/date: never silently pending forever
        elif len(after) < min_n or rate is None or bar is None:
            status = "pending"
        else:
            status = "holding" if rate >= bar else "regressed"
        out.append(dict(fix, status=status, n_after=len(after), rate_after=rate))
    return out


def findings(report: dict, previous: dict, fixes: List[dict]) -> dict:
    """Open findings keyed by check id; ``previous`` is the last ``findings.json`` and ``fixes``
    the fix records already scored by ``fix_statuses`` (``report["fixes"]``)."""
    today = report["generated"][:10]
    latest_fix = {}
    for item in fixes:
        latest_fix[item.get("evaluator")] = item
    out = {}
    for check_id, check in sorted(report["checks"].items()):
        if not check["breached"]:
            continue
        before = (previous or {}).get(check_id) or {}
        out[check_id] = {"value": check["rate"], "bar": check["bar"], "n": check["n"],
                         "first_seen": before.get("first_seen") or today, "last_seen": today,
                         "examples": list(check["examples"]),
                         "example_labels": list(check.get("example_labels") or check["examples"]),
                         "fix": latest_fix.get(check_id)}
    return out


def saturated(report: dict, windows: int) -> List[str]:
    """Checks at rate 1.0 now and in each of the previous ``windows - 1`` reports."""
    history = report.get("history") or []
    need = max(0, int(windows) - 1)
    if len(history) < need:
        return []
    recent = history[-need:] if need else []
    return [check_id for check_id, check in sorted(report["checks"].items())
            if check["rate"] == 1.0 and all((h.get("checks") or {}).get(check_id) == 1.0 for h in recent)]


# --- markdown --------------------------------------------------------------------------------

def _fmt(value, digits=2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return ("%." + str(digits) + "f") % value
    return str(value)


def _ranked(cases: List[dict], key: str) -> List[Tuple[str, float, int]]:
    groups: Dict[str, List[float]] = {}
    for case in cases:
        groups.setdefault(str(case.get(key)), []).append(case["score"])
    return sorted(((name, sum(v) / len(v), len(v)) for name, v in groups.items()), key=lambda t: (t[1], t[0]))


def render_markdown(report: dict) -> str:
    lines = ["# ALE analyze %s" % report["generated"][:10], ""]
    window = report["window"]
    runs = report["runs"]
    lines.append("Window: %s to %s. Runs: %d active, %d stale, %d done, %d skipped. Skipped lines: %d." % (
        window["from"] or "all", window["to"], runs["active"], runs["stale"], runs["done"], runs["skipped"],
        report.get("skipped_lines", 0)))
    lines += ["", "| Check | n | passed | rate | bar | status | failing examples |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for check_id, check in report["checks"].items():
        status = "BREACHED" if check["breached"] else ("n/a" if not check["n"] else (
            "ok" if check["rate"] >= (check["bar"] or 0) else "below bar, n < min"))
        lines.append("| %s | %d | %d | %s | %s | %s | %s |" % (
            check_id, check["n"], check["passed"], _fmt(check["rate"]), _fmt(check["bar"]), status,
            ", ".join(check.get("example_labels") or check["examples"]) or "-"))
    cases = report.get("case_results", [])
    previous = report.get("previous") or {}

    lines += ["", "## What regressed", ""]
    regressed = [(k, previous[k], c["rate"]) for k, c in report["checks"].items()
                 if k in previous and c["rate"] is not None and c["bar"] is not None
                 and previous[k] >= c["bar"] > c["rate"]]
    for check_id, before, now in regressed:
        lines.append("- %s fell below its bar: %s -> %s" % (check_id, _fmt(before), _fmt(now)))
    for row in report.get("offline_regressions") or []:
        lines.append("- offline case %s (%s): %s, best before %s" % (
            row.get("case_id"), row.get("evaluator"), _fmt(row.get("score")), _fmt(row.get("best_before"))))
    if not regressed and not report.get("offline_regressions"):
        lines.append("Nothing regressed.")

    lines += ["", "## Weakest category", ""]
    for name, mean, n in _ranked(cases, "case_category")[:5]:
        lines.append("- %s: mean %s over %d cases" % (name, _fmt(mean), n))
    if not cases:
        lines.append("No case results.")
    lines += ["", "## Weakest evaluator", ""]
    for name, mean, n in _ranked(cases, "evaluator")[:5]:
        lines.append("- %s: mean %s over %d cases" % (name, _fmt(mean), n))
    if not cases:
        lines.append("No case results.")

    lines += ["", "## Trend", ""]
    for check_id, check in report["checks"].items():
        before, now = previous.get(check_id), check["rate"]
        if before is None or now is None:
            trend = "n/a"
        elif now > before + 0.01:
            trend = "improving"
        elif now < before - 0.01:
            trend = "degrading"
        else:
            trend = "stable"
        lines.append("- %s: %s -> %s (%s)" % (check_id, _fmt(before), _fmt(now), trend))
    by_config = _ranked(cases, "config_hash")
    if by_config:
        lines.append("- by config_hash: " + ", ".join("%s %s (n=%d)" % (name, _fmt(mean), n)
                                                       for name, mean, n in by_config))

    lines += ["", "## Saturated checks", ""]
    full = saturated(report, report.get("saturation_windows", 4))
    lines += ["- %s: 1.00 for %d reports; tighten or retire" % (c, report.get("saturation_windows", 4))
              for c in full] or ["None."]

    lines += ["", "## Fix status", ""]
    fixes = report.get("fixes") or []
    lines += ["- %s %s (%s): %s, n=%d after, rate %s" % (
        f.get("evaluator"), f.get("commit"), f.get("note") or "", f["status"], f["n_after"], _fmt(f["rate_after"]))
        for f in fixes] or ["No fix records."]

    lines += ["", "## Declared deviations", ""]
    lines += ["- %s: %d" % (code, count) for code, count in (report.get("deviations") or {}).items()] or [
        "No declared deviations."]

    lines += ["", "## Metrics", "",
              "| role / tier / harness / model | tasks | accepted | first pass | attempts/accepted | "
              "p50 s | p90 s | tokens/accepted | unknown usage |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, m in report["metrics"].items():
        lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            key.replace("|", " / "), m["tasks"], _fmt(m["acceptance_rate"]), _fmt(m["first_pass_rate"]),
            _fmt(m["attempts_per_accepted"]), _fmt(m["claim_to_accept_p50_s"], 0),
            _fmt(m["claim_to_accept_p90_s"], 0), _fmt(m["tokens_per_accepted"], 0),
            _fmt(m["unknown_usage_share"])))

    calibration = report["calibration"]
    lines += ["", "## Calibration", ""]
    for effort, bucket in calibration["effort_buckets"].items():
        lines.append("- effort %s: n=%d, median %s s, median %s files" % (
            effort, bucket["n"], _fmt(bucket["median_s"], 0), _fmt(bucket["median_files"], 1)))
    lines.append("- effort monotonic in duration and files: %s" % _fmt(calibration["effort_monotonic"]))
    for group in calibration["outlier_groups"]:
        lines.append("- outlier %s: n=%d, reject rate %s, median %s s (%sx the median)" % (
            group["group"], group["n"], _fmt(group["reject_rate"]), _fmt(group["median_s"], 0),
            _fmt(group["ratio"], 1)))

    lines += ["", "## Promotions", "", "Proposals only; nothing is applied.", ""]
    lines += ["- %s: %s" % (field, item["status"]) for field, item in report["promotions"].items()] or ["None."]
    if report.get("unreadable"):
        lines += ["", "Unreadable runs: " + ", ".join(report["unreadable"])]
    return "\n".join(lines) + "\n"
