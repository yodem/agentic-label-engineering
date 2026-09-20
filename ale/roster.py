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
    errs = validate(roster, load_schema("roster.schema.json"))
    if errs:
        raise RosterError("invalid roster %s:\n  %s" % (path, "\n  ".join(errs)))
    return roster


def roster_hash(roster: dict) -> str:
    blob = json.dumps(roster, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def resolve(roster: dict, role: str, tier: str) -> Dict[str, str]:
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
