"""Deep-reference pointers for agents, from a refs file outside the repository.

``ALE_REFS_FILE``, or the roster's ``refs_file``, names a JSON object keyed by
agent: the catalog key (``backend/api``, ``_cross/review``, ``general``), the
``<role>/<name>`` alias (``_cross/cross-review``), or a domain (``backend``)
that covers every agent in it. Each value is
``{"title", "how_to_read", "read_first"}``. Nothing is fetched: the prompt only
names the entry and the command that reads it.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

ENV = "ALE_REFS_FILE"
FIELDS = ("title", "how_to_read", "read_first")
HEADING = "Deep reference (read on demand; not pasted)"
_MAX = 300


def refs_path(roster_path: Optional[str] = None) -> Optional[str]:
    """The refs file: ``ALE_REFS_FILE`` first, then the roster's ``refs_file`` (relative to the roster)."""
    value = os.environ.get(ENV)
    if value:
        return os.path.expanduser(value)
    if not roster_path or not os.path.isfile(roster_path):
        return None
    try:
        with open(roster_path, encoding="utf-8") as handle:
            roster = json.load(handle)
    except (OSError, ValueError):
        return None
    value = roster.get("refs_file") if isinstance(roster, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    value = os.path.expanduser(value.strip())
    if not os.path.isabs(value):
        value = os.path.join(os.path.dirname(os.path.abspath(roster_path)), value)
    return value


def _clean(value: object) -> str:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    return text[:_MAX]


def load_refs(path: Optional[str]) -> Dict[str, dict]:
    """Entries with a title, keyed by agent; an unreadable or malformed file gives none."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        entry = {field: _clean(value.get(field)) for field in FIELDS}
        if entry["title"]:
            entries[key] = entry
    return entries


def agent_keys(agent: dict) -> List[str]:
    """Lookup order for an agent: catalog key, ``<role>/<name>`` alias, then its domain."""
    role, sub, name = agent.get("role"), agent.get("sub"), agent.get("name")
    keys = []
    if role == "general":
        keys.append("general")
    else:
        if role and sub:
            keys.append("%s/%s" % (role, sub))
        if role and name:
            keys.append("%s/%s" % (role, name))
        if role:
            keys.append(role)
    return list(dict.fromkeys(keys))


def resolve(refs: Dict[str, dict], agent: dict) -> Optional[Tuple[str, dict]]:
    for key in agent_keys(agent):
        if key in refs:
            return key, refs[key]
    return None


def render_section(refs: Dict[str, dict], agent: dict) -> str:
    found = resolve(refs, agent)
    if found is None:
        return "%s:\n- none configured" % HEADING
    key, entry = found
    lines = ["%s:" % HEADING, "- entry: %s (%s)" % (entry["title"], key)]
    if entry["read_first"]:
        lines.append("- read first: %s" % entry["read_first"])
    if entry["how_to_read"]:
        lines.append("- how to read: %s" % entry["how_to_read"])
    return "\n".join(lines)
