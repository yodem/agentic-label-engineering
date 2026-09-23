"""Discovery helpers for ALE run directories."""
from __future__ import annotations

import json
import os


_TAIL_BYTES = 65536


def _tail_events(path):
    """Yield valid event objects from a bounded tail, newest first."""
    with open(path, "rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - _TAIL_BYTES))
        data = source.read(_TAIL_BYTES)
    if size > _TAIL_BYTES:
        data = data.split(b"\n", 1)[-1]
    for line in reversed(data.splitlines()):
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


def _last_ts(path):
    for event in _tail_events(path):
        try:
            return float(event["ts"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return None


def _run_id(path, fallback):
    # Initialization metadata is near the start of the append-only event log.
    with open(path, "rb") as source:
        data = source.read(_TAIL_BYTES)
    for line in data.splitlines():
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "run_started" and event.get("run_id"):
            return str(event["run_id"])
    return fallback


def list_runs(runs_dir):
    """Return valid runs ordered by newest parseable event, then directory."""
    runs_dir = os.path.abspath(os.fspath(runs_dir))
    try:
        current = open(os.path.join(runs_dir, "current"), encoding="utf-8").read().strip()
    except OSError:
        current = ""
    result = []
    try:
        entries = os.scandir(runs_dir)
    except OSError:
        return result
    with entries:
        for entry in entries:
            if entry.name == "current" or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            labels = os.path.join(entry.path, "labels")
            events = os.path.join(entry.path, "events.jsonl")
            if not os.path.isdir(labels) or not os.path.isfile(events):
                continue
            timestamp = _last_ts(events)
            if timestamp is None:
                continue
            run_id = _run_id(events, entry.name)
            result.append({"dir": entry.name, "path": os.path.abspath(entry.path),
                           "run_id": run_id, "last_event_ts": timestamp,
                           "is_current": current in (entry.name, run_id)})
    return sorted(result, key=lambda row: (-row["last_event_ts"], row["dir"]))
