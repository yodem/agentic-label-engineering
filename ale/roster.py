from __future__ import annotations

import hashlib
import json
from typing import Dict

from .validate import load_schema, validate


class RosterError(Exception):
    pass


def load_roster(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            roster = json.load(f)
    except (OSError, ValueError) as exc:
        raise RosterError("cannot read roster %s: %s" % (path, exc))
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
