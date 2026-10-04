"""The eval ledger: append-only JSONL rows, one per scored case and evaluator.

Rows are never rewritten. ``ale analyze`` appends a row only when its ``(case_id, evaluator)``
is new or its score or verdict changed; readers still take the latest row per key.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .runindex import ale_home


def ledger_path(home=None) -> str:
    return os.path.join(home or ale_home(), ".ale", "eval-ledger.jsonl")


def iso_ts(epoch: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def make_row(run_id, tool_version, config_hash, case_id, case_kind, case_category,
             evaluator, score, passed, reason, metadata=None) -> dict:
    return {"run_id": run_id, "timestamp": iso_ts(), "tool": "ale", "tool_version": tool_version,
            "config_hash": config_hash, "case_id": case_id, "case_kind": case_kind,
            "case_category": case_category, "evaluator": evaluator, "score": float(score),
            "passed": bool(passed), "reason": reason, "metadata": dict(metadata or {})}


def append_rows(rows: List[dict], home=None) -> None:
    if not rows:
        return
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data)
    finally:
        os.close(fd)


def _changed(rows: List[dict], existing: List[dict]) -> List[dict]:
    """The rows whose ``(case_id, evaluator)`` has no row yet, or whose latest row differs in
    ``score`` or ``passed``."""
    latest = {key: (row.get("score"), row.get("passed")) for key, row in latest_by_case(existing).items()}
    out = []
    for row in rows:
        key = (row.get("case_id"), row.get("evaluator"))
        value = (row.get("score"), row.get("passed"))
        if latest.get(key) != value:
            out.append(row)
            latest[key] = value
    return out


def append_changed_rows(rows: List[dict], home=None) -> int:
    """Dedupe on write: append only the rows that change their case's latest score or verdict, so
    re-running an analysis over unchanged runs adds nothing. Returns the number appended."""
    if not rows:
        return 0
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)   # read and append under one lock
        fresh = _changed(rows, read_rows(home))
        if fresh:
            os.write(fd, "".join(json.dumps(row, sort_keys=True) + "\n" for row in fresh).encode("utf-8"))
    finally:
        os.close(fd)
    return len(fresh)


def read_rows(home=None) -> List[dict]:
    rows = []
    try:
        with open(ledger_path(home), "rb") as handle:
            for line in handle:
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("case_id") is not None and row.get("evaluator"):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _ordered(rows: List[dict]) -> List[dict]:
    # Stable sort: equal timestamps keep file order, so a later line still wins.
    return sorted(rows, key=lambda row: str(row.get("timestamp") or ""))


def latest_by_case(rows: List[dict]) -> Dict[Tuple[str, str], dict]:
    latest: Dict[Tuple[str, str], dict] = {}
    for row in _ordered(rows):
        latest[(row.get("case_id"), row.get("evaluator"))] = row
    return latest


def regressions(rows: List[dict]) -> List[dict]:
    """Cases whose latest score is below the best score recorded before it."""
    history: Dict[Tuple[str, str], List[dict]] = {}
    for row in _ordered(rows):
        history.setdefault((row.get("case_id"), row.get("evaluator")), []).append(row)
    found = []
    for key in sorted(history, key=lambda k: (str(k[0]), str(k[1]))):
        items = history[key]
        if len(items) < 2:
            continue
        best = max(float(item.get("score") or 0.0) for item in items[:-1])
        latest = items[-1]
        if float(latest.get("score") or 0.0) < best:
            found.append(dict(latest, best_before=best))
    return found
