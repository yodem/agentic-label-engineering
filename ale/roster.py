from __future__ import annotations

import hashlib
import json
import copy
from typing import Dict

from .validate import load_schema, validate


_DEFAULT_SUB_NAMES = {
    "frontend": ("css", "ux", "design", "components", "performance", "accessibility"),
    "backend": ("architecture", "api", "data", "integration", "performance"),
    "devops": ("ci", "deploy", "infra", "monitor", "release"),
    "test": ("unit", "integration", "e2e", "coverage"),
    "docs": ("reference", "guide", "changelog"),
}
_DEFAULT_CROSS_SUBS = ["debugging", "review", "security"]
_DEFAULT_PHASES = ["plan", "design", "implement", "test", "review", "deploy", "operate", "maintain"]
_DEFAULT_LOCALITY = {
    "any": "No planner-machine-only resources are required.",
    "local": "Requires the planner's own machine or local-only resources.",
}


def _default_sub_vocab() -> dict:
    return {role: {sub: "%s-focused work." % sub for sub in subs}
            for role, subs in _DEFAULT_SUB_NAMES.items()}


def _apply_vocab_defaults(roster: dict) -> None:
    vocab = roster.setdefault("vocab", {})
    locality = dict(_DEFAULT_LOCALITY)
    locality.update(vocab.get("locality", {}))
    vocab["locality"] = locality
    subs = _default_sub_vocab()
    for role, entries in vocab.get("sub", {}).items():
        subs.setdefault(role, {}).update(entries)
    vocab["sub"] = subs
    vocab.setdefault("cross_sub", list(_DEFAULT_CROSS_SUBS))
    vocab.setdefault("phase", list(_DEFAULT_PHASES))
    judge = roster.setdefault("judge", {})
    judge.setdefault("plugin", None)
    judge.setdefault("threshold", 0.75)
    judge.setdefault("modes", {})
    judge.setdefault("command", ["jev-ask"])
    judge.setdefault("questions", {})
    modes = judge.setdefault("modes", {})
    for field in ("sub", "phase", "locality"):
        modes.setdefault(field, "shadow")
    roster.setdefault("worktree_setup_defaults", [])


class RosterError(Exception):
    pass


def load_roster(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            roster = json.load(f)
    except (OSError, ValueError) as exc:
        raise RosterError("cannot read roster %s: %s" % (path, exc))
    _apply_vocab_defaults(roster)
    for row in roster.get("routing", []):
        if row.get("executor") == "claude_code":
            row["executor"] = "claude-subagent"
    errs = validate(roster, load_schema("roster.schema.json"))
    if errs:
        raise RosterError("invalid roster %s:\n  %s" % (path, "\n  ".join(errs)))
    from .dispatch import SPAWN_EXECUTORS
    roster["vocab"]["role"].update({
        "fixer": "Focused repair work after an acceptance failure.",
        "monitor": "Read-only monitoring and escalation.",
        "general": "Catch-all work not covered by another role.",
    })
    roster["routing"].extend([
        {"role": "fixer", "model_tier": "standard", "executor": "claude-headless", "model": "claude-sonnet-5"},
        {"role": "monitor", "model_tier": "standard", "executor": "claude-headless", "model": "claude-sonnet-5"},
    ])
    invalid = [row.get("executor") for row in roster.get("routing", [])
               if row.get("executor") not in SPAWN_EXECUTORS]
    if invalid:
        raise RosterError("unsupported executor(s): %s" % ", ".join(sorted(set(invalid))))
    return roster


def roster_hash(roster: dict) -> str:
    roster = copy.deepcopy(roster)
    _apply_vocab_defaults(roster)
    blob = json.dumps(roster, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def resolve(roster: dict, role: str, tier: str, executor: str = None) -> Dict[str, str]:
    if executor is not None:
        wildcard = None
        for row in roster["routing"]:
            if row["model_tier"] != tier or row["executor"] != executor:
                continue
            if row["role"] == role:
                return {"executor": executor, "model": row["model"]}
            if row["role"] == "*" and wildcard is None:
                wildcard = row
        return {"executor": executor, "model": wildcard["model"] if wildcard else None}
    wildcard = None
    for row in roster["routing"]:
        if row["model_tier"] != tier:
            continue
        if row["role"] == role:
            return {"executor": row["executor"], "model": row["model"]}
        if row["role"] == "*" and wildcard is None:
            wildcard = row
    if wildcard is not None:
        return {"executor": wildcard["executor"], "model": wildcard["model"]}
    raise RosterError("no routing row for role=%s model_tier=%s" % (role, tier))
