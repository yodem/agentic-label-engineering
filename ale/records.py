"""Shared primitives for ALE's record files under ``$ALE_HOME/.ale`` (run index, eval ledger,
fix records) and the session bindings: the home resolver, one tolerant JSONL reader and one ISO
timestamp format."""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional


def ale_home(env=None) -> str:
    """``$ALE_HOME``, else the user's home directory (ALE's files live in ``<home>/.ale``)."""
    env = os.environ if env is None else env
    return env.get("ALE_HOME") or os.path.expanduser("~")


def iso(epoch: Optional[float] = None) -> str:
    """UTC ISO 8601 to the second (``2026-10-04T12:00:00Z``); now when ``epoch`` is None."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def read_jsonl(path: str) -> List[dict]:
    """The JSON objects in a JSONL file. Lines that do not decode or parse, and values that are
    not objects, are skipped; a missing or unreadable file reads as empty."""
    rows = []
    try:
        with open(path, "rb") as handle:
            for line in handle:
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows
