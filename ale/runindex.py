"""The run index: one JSONL row per ALE run directory, so ``ale analyze`` can find every run.

``ale init-run`` appends an entry; ``ale analyze --backfill DIR`` adds run directories that
predate the index. Paths are stored resolved (absolute, symlinks followed) so the same run is
never indexed twice under two spellings.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from .events import locked_append
from .records import ale_home, read_jsonl

_PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache"}
_HEAD_BYTES = 1 << 20


def index_path(home=None) -> str:
    return os.path.join(home or ale_home(), ".ale", "index", "runs.jsonl")


def _path(home, index) -> str:
    return index or index_path(home)


def _read_rows(path: str) -> List[dict]:
    return [row for row in read_jsonl(path) if isinstance(row.get("run_dir"), str)]


def read_index(home=None, index: Optional[str] = None) -> List[dict]:
    """One entry per run directory; when a directory appears twice the later row wins."""
    latest: Dict[str, dict] = {}
    for row in _read_rows(_path(home, index)):
        latest.pop(row["run_dir"], None)
        latest[row["run_dir"]] = row
    return list(latest.values())


def append_run(run_dir: str, repo_root: str, run_id: str, ale_version: str,
               roster_hash: Optional[str], home=None, index: Optional[str] = None) -> bool:
    """Append ``run_dir`` to the index; False (and no write) when it is already indexed."""
    path = _path(home, index)
    resolved = os.path.realpath(run_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"ts": time.time(), "run_id": run_id, "run_dir": resolved,
           "repo_root": os.path.realpath(repo_root) if repo_root else None,
           "ale_version": ale_version, "roster_hash": roster_hash}
    added = []

    def payload() -> bytes:   # the duplicate check and the append share one lock
        if any(entry["run_dir"] == resolved for entry in _read_rows(path)):
            return b""
        added.append(True)
        return (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")

    locked_append(path, payload)
    return bool(added)


def _run_facts(events_path: str, fallback: str) -> tuple:
    """The run id (first event carrying one) and roster hash (first ``labeled`` event)."""
    run_id, roster_hash = None, None
    try:
        with open(events_path, "rb") as handle:
            data = handle.read(_HEAD_BYTES)
    except OSError:
        return fallback, None
    for line in data.splitlines():
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if run_id is None and isinstance(event.get("run_id"), str) and event["run_id"]:
            run_id = event["run_id"]
        if roster_hash is None and event.get("type") == "labeled" and event.get("roster_hash"):
            roster_hash = str(event["roster_hash"])
        if run_id is not None and roster_hash is not None:
            break
    return run_id or fallback, roster_hash


def _is_run_dir(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "labels")) and os.path.isfile(os.path.join(path, "events.jsonl"))


def _repo_root(run_dir: str) -> str:
    # <repo>/.ale/runs/<id>
    return os.path.dirname(os.path.dirname(os.path.dirname(run_dir)))


def backfill(roots: List[str], home=None, max_depth: int = 7, index: Optional[str] = None) -> int:
    """Index every ``.ale/runs/<id>/`` (holding ``labels/`` and ``events.jsonl``) under ``roots``.

    Skips ``.git``, ``node_modules`` and a run directory's own ``wt/`` worktrees. Returns the
    number of entries added; already-indexed runs are left alone, so it is idempotent."""
    added = 0
    for root in roots:
        root = os.path.realpath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)
        for current, dirnames, _files in os.walk(root):
            depth = current.rstrip(os.sep).count(os.sep) - base_depth
            parent = os.path.dirname(current)
            in_runs = os.path.basename(current) == "runs" and os.path.basename(parent) == ".ale"
            if in_runs:
                for name in sorted(dirnames):
                    run_dir = os.path.join(current, name)
                    if not _is_run_dir(run_dir):
                        continue
                    run_id, roster_hash = _run_facts(os.path.join(run_dir, "events.jsonl"), name)
                    if append_run(run_dir, _repo_root(run_dir), run_id, None, roster_hash,
                                  home=home, index=index):
                        added += 1
                # A run directory holds its own worktrees (wt/), never further runs worth walking.
                dirnames[:] = []
                continue
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE)
    return added
