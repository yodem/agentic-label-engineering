"""Deep-reference pointers for agents, from a refs file outside the repository.

``ALE_REFS_FILE``, or the roster's ``refs_file``, names a JSON object keyed by
agent: the catalog key (``backend/api``, ``_cross/review``, ``general``), the
``<role>/<name>`` alias (``_cross/cross-review``), or a domain (``backend``)
that covers every agent in it. Each value is
``{"title", "how_to_read", "read_first"}``. The prompt names the entry and the
commands that read it; for a local dispatch, ``prefetch`` runs those commands (only
``ck``, never a shell) into a file whose first line is a content token, which the
executor echoes back with ``ale refs-ack``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from .handoff import write_atomic

ENV = "ALE_REFS_FILE"
FIELDS = ("title", "how_to_read", "read_first")
HEADING = "Deep reference (required: read before you edit)"
TOKEN_PREFIX = "ALE-REFS-TOKEN: "
PREFETCH_COMMANDS = ("read_first", "how_to_read")
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
    """Lookup order for an agent.

    ``<role>/<sub>``, ``<role>/<name>``, the name without its ``<role>-`` prefix
    (``backend-integration`` -> ``backend/integration``; ``cross-`` for ``_cross``),
    ``<role>/_default`` when the name ends with ``-default`` or there is no sub, then
    ``<role>``. ``general`` is its own key.
    """
    role, sub, name = agent.get("role"), agent.get("sub"), agent.get("name")
    keys = []
    if role == "general":
        keys.append("general")
    elif role:
        if sub:
            keys.append("%s/%s" % (role, sub))
        if name:
            keys.append("%s/%s" % (role, name))
            prefix = ("cross-" if role == "_cross" else "%s-" % role)
            if name.startswith(prefix) and len(name) > len(prefix):
                bare = name[len(prefix):]
                keys.append("%s/%s" % (role, "_default" if bare == "default" else bare))
        if not sub or (name or "").endswith("-default"):
            keys.append("%s/_default" % role)
        keys.append(role)
    return list(dict.fromkeys(keys))


def resolve(refs: Dict[str, dict], agent: dict) -> Optional[Tuple[str, dict]]:
    for key in agent_keys(agent):
        if key in refs:
            return key, refs[key]
    return None


def render_section(refs: Dict[str, dict], agent: dict, prefetched: Optional[str] = None) -> str:
    found = resolve(refs, agent)
    if found is None:
        return "%s:\n- none configured" % HEADING
    key, entry = found
    lines = ["%s:" % HEADING, "- entry: %s (%s)" % (entry["title"], key)]
    if prefetched:
        lines.append("- prefetched: %s (read this file first; its first line is the token)" % prefetched)
    if entry["read_first"]:
        lines.append("- read first: %s" % entry["read_first"])
    if entry["how_to_read"]:
        lines.append("- how to read: %s" % entry["how_to_read"])
    lines.append('- when read: $ALE_BIN refs-ack --token <the ALE-REFS-TOKEN line> --summary "<what applied>"')
    return "\n".join(lines)


def _failure(error: str) -> dict:
    return {"ok": False, "bytes": 0, "error": error}


def prefetch(entry: dict, out_path: str, timeout_s: int = 30) -> dict:
    """Run the entry's ``read_first`` then ``how_to_read`` commands into ``out_path``.

    Only commands whose argv[0] is ``ck`` run (shlex split, no shell). Any failure is
    soft: ``{"ok": False, "bytes": 0, "error": ...}`` and nothing is written. On success
    the file starts with ``ALE-REFS-TOKEN: <8 hex>`` (sha256 of the fetched pages) and
    the result carries ``token``.
    """
    commands = []
    for field in PREFETCH_COMMANDS:
        command = (entry.get(field) or "").strip()
        if not command:
            continue
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _failure("unparseable command %r: %s" % (command, exc))
        if not argv or argv[0] != "ck":
            return _failure("only ck commands are prefetched: %r" % command)
        commands.append((command, argv))
    if not commands:
        return _failure("no ck commands to prefetch")
    pages = []
    for command, argv in commands:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                                  stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return _failure("ck not found on PATH")
        except subprocess.TimeoutExpired:
            return _failure("%s timed out after %ss" % (command, timeout_s))
        except OSError as exc:
            return _failure("%s: %s" % (command, exc))
        if proc.returncode != 0:
            detail = " ".join((proc.stderr or proc.stdout or "").split())[:200]
            return _failure("%s exited %s%s" % (command, proc.returncode, ": " + detail if detail else ""))
        pages.append((command, proc.stdout))
    token = hashlib.sha256("".join(page for _, page in pages).encode("utf-8")).hexdigest()[:8]
    body = "\n\n".join("## %s\n\n%s" % (command, page.rstrip("\n")) for command, page in pages)
    text = "%s%s\n\n%s\n" % (TOKEN_PREFIX, token, body)
    try:
        write_atomic(out_path, text)
    except OSError as exc:
        return _failure("write %s: %s" % (out_path, exc))
    return {"ok": True, "bytes": len(text.encode("utf-8")), "error": None, "token": token}
