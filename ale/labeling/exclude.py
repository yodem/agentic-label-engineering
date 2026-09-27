"""Roster ``judge.exclude_paths``: project roots whose task text never reaches the judge.

A matching root gets an ``ExcludedJudge``. It answers every Choice and Noul with
an abstain whose error is ``excluded`` and never runs the judge command, so the
votes still record that a decision was skipped and why.
"""

from __future__ import annotations

import fnmatch
import os
from typing import Iterable, List, Optional

EXCLUDED = "excluded"


def _patterns(roster: dict) -> List[str]:
    values = (roster.get("judge") or {}).get("exclude_paths") or []
    return [os.path.normpath(os.path.expanduser(value.strip()))
            for value in values if isinstance(value, str) and value.strip()]


def root_excluded(roster: dict, root: str) -> bool:
    """True when ``root`` (or its real path) matches a glob, or sits under a matched directory."""
    patterns = _patterns(roster)
    if not patterns or not root:
        return False
    candidates = {os.path.normpath(os.path.abspath(root)), os.path.realpath(root)}
    for candidate in candidates:
        for pattern in patterns:
            if fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(candidate, pattern + "/*"):
                return True
            if not any(ch in pattern for ch in "*?["):
                real = os.path.realpath(pattern)
                if candidate == real or candidate.startswith(real + os.sep):
                    return True
    return False


def any_excluded(roster: dict, roots: Iterable[Optional[str]]) -> bool:
    return any(root_excluded(roster, root) for root in roots if root)


class ExcludedJudge:
    """A judge stand-in that abstains on everything without a call."""

    def __init__(self, model: Optional[str] = None):
        self.model = model
        self.command = []

    def ask(self, field: str, question: str, options: List[str], state: str) -> dict:
        return {"field": field, "value": None, "by": "judge:command", "confidence": None,
                "detail": {"error": EXCLUDED}}

    def noul(self, key: str, question: str, state: str) -> dict:
        return {"key": key, "p": None, "model": self.model, "detail": {"error": EXCLUDED}}
