"""Harnesses (claude, codex, pi, roster-declared), the run mode and the host, decided by code.

A label's executor names a harness. How it runs is derived: an in-session harness on an
inline or workflow lane runs in the lead's session, a pane lane runs in a herdr pane, and any
other combination runs headless. The legacy executor ids (``claude-headless``, ``codex-exec``,
``pi-print``, ``herdr-pane``, ``claude-subagent``) name a harness *and* a mode; that explicit
mode wins over the lane. Work that does not run in-session goes to the roster's
``remote_host`` unless the label's locality is ``local``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

MODES = ("in-session", "headless", "pane")
LOCAL = "local"
BUILTIN: Dict[str, dict] = {
    "claude": {"headless": ["claude", "--model", "{model}", "-p", "{prompt}"], "usage_from": None,
               "herdr_kind": "claude", "in_session": True, "family": "anthropic"},
    "codex": {"headless": ["codex", "exec", "--json", "--skip-git-repo-check", "--model", "{model}", "{prompt}"],
              "usage_from": "codex-json", "herdr_kind": "codex", "in_session": False, "family": "openai"},
    "pi": {"headless": ["pi", "--mode", "json", "--model", "{model}", "-p", "{prompt}"],
           "usage_from": "pi-json", "herdr_kind": "pi", "in_session": False},
}
LEGACY: Dict[str, Tuple[str, str]] = {
    "claude-headless": ("claude", "headless"),
    "claude-subagent": ("claude", "in-session"),
    "claude_code": ("claude", "in-session"),
    "codex-exec": ("codex", "headless"),
    "pi-print": ("pi", "headless"),
    "herdr-pane": ("claude", "pane"),
}
# The spawn id bin/ale-spawn understands for a (harness, mode) pair.
_SPAWN_IDS = {("claude", "headless"): "claude-headless", ("claude", "in-session"): "claude-subagent",
              ("codex", "headless"): "codex-exec", ("pi", "headless"): "pi-print"}


def normalize(name: Optional[str]) -> Optional[str]:
    """The harness a (possibly legacy) executor id names; unknown names come back unchanged."""
    if name is None:
        return None
    return LEGACY[name][0] if name in LEGACY else name


def mode_hint(name: Optional[str]) -> Optional[str]:
    """The mode a legacy executor id fixes, or None for a bare harness name."""
    return LEGACY[name][1] if name in LEGACY else None


def registry(roster: dict) -> Dict[str, dict]:
    """Built-in harnesses overlaid with the roster's ``harnesses`` map."""
    merged = {key: dict(value) for key, value in BUILTIN.items()}
    for key, value in (roster.get("harnesses") or {}).items():
        if not isinstance(value, dict):
            continue
        base = merged.get(key, {"usage_from": None, "herdr_kind": key, "in_session": False})
        merged[key] = dict(base, **value)
    return merged


def check(roster: dict) -> List[str]:
    errs = []
    known = registry(roster)
    for key in sorted(known):
        argv = known[key].get("headless")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            errs.append("harness %s: headless must be a non-empty list of strings" % key)
        elif not any("{prompt}" in arg for arg in argv):
            errs.append("harness %s: headless argv needs a {prompt} placeholder" % key)
    try:
        claude_permission_mode(roster)
    except ValueError as err:
        errs.append(str(err))
    unknown = sorted({str(row.get("executor")) for row in roster.get("routing", [])
                      if normalize(row.get("executor")) not in known})
    if unknown:
        errs.append("unsupported executor(s): %s (known harnesses: %s)" %
                    (", ".join(unknown), ", ".join(sorted(known))))
    return errs


def derive_mode(lane: Optional[str], harness_name: Optional[str], roster: dict,
                hint: Optional[str] = None, model: Optional[str] = None) -> str:
    """Pane lane -> pane; inline/workflow -> in-session when the harness can run in the lead's
    session, else headless. A legacy id's mode wins. The in-session Task tool only takes
    Anthropic models, so a model of another family never runs in-session."""
    name = normalize(harness_name) or "claude"
    in_session_ok = registry(roster).get(name, {}).get("in_session") and \
        family(name, model, roster) in ("anthropic", None)
    if hint in MODES and (hint != "in-session" or in_session_ok):
        return hint
    if lane == "pane":
        return "pane"
    if lane in ("inline", "workflow") and in_session_ok:
        return "in-session"
    return "headless"


def derive_host(mode: str, locality: Optional[str], roster: dict) -> str:
    remote = roster.get("remote_host")
    if mode == "in-session" or (locality or "any") == "local" or not remote:
        return LOCAL
    return remote


_MODEL_FAMILIES = (("claude", "anthropic"), ("anthropic/", "anthropic"), ("gpt", "openai"),
                   ("openai/", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
                   ("gemini", "google"), ("google/", "google"))


def family(harness_name: Optional[str], model: Optional[str], roster: dict) -> Optional[str]:
    """The model family, for cross-family review gates: from the model id, else the harness."""
    lowered = (model or "").lower()
    for prefix, name in _MODEL_FAMILIES:
        if lowered.startswith(prefix):
            return name
    return registry(roster).get(normalize(harness_name) or "", {}).get("family")


def render_argv(template: List[str], model: Optional[str], prompt: str) -> List[str]:
    """A headless argv with ``{model}`` and ``{prompt}`` filled in. With no model, an argument
    that names ``{model}`` is dropped, together with the flag right before it (``--model``)."""
    out: List[str] = []
    for index, arg in enumerate(template):
        if "{model}" in arg and model is None:
            if out and out[-1].startswith("-") and index > 0 and template[index - 1] == out[-1]:
                out.pop()
            continue
        out.append(arg.replace("{model}", model or "").replace("{prompt}", prompt))
    return out


# Headless Claude permission grant (docs/harness-facts.md item 16, SECURITY.md). Only
# acceptEdits or null is accepted; bypassPermissions is never emitted.
PERMISSION_MODES = ("acceptEdits",)
GIT_ALLOW = ("git add", "git commit", "git status", "git diff", "git log")
# The ALE subcommands an executor is told to run (EXECUTOR.md, the dispatch prompt). Lead-side
# commands (rescope, verify, accept, integrate, relabel ...) are deliberately not granted.
ALE_PROTOCOL = ("claim", "heartbeat", "status", "submit", "usage", "note", "input-required", "refs-ack")
# A rule value cannot hold these: "," splits the --allowedTools list and parentheses close the rule.
_RULE_UNSAFE = (",", "(", ")")


def claude_permission_mode(roster: dict) -> Optional[str]:
    """``harnesses.claude.permission_mode`` (default ``acceptEdits``; null opts out).
    Raises ValueError for any other value."""
    entry = ((roster or {}).get("harnesses") or {}).get("claude") or {}
    mode = entry.get("permission_mode", "acceptEdits") if isinstance(entry, dict) else "acceptEdits"
    if mode is not None and mode not in PERMISSION_MODES:
        raise ValueError("harness claude: permission_mode must be acceptEdits or null, not %r" % (mode,))
    return mode


def claude_headless_permissions(roster: dict, acceptance: list, ale_commands: List[str],
                                read_only: bool = False) -> Tuple[Optional[str], List[str], List[str]]:
    """(permission mode, allowedTools rules, skipped commands) for a headless Claude executor.

    ``ale_commands`` are the command prefixes the executor runs ALE with (``python3 -m ale``,
    ``<ALE_PYTHON> -m ale``, the ``bin/ale-py`` path, the literal ``$ALE_BIN``); each gets one
    ``Bash(<prefix> <subcommand>:*)`` rule per ``ALE_PROTOCOL`` subcommand. Each acceptance command becomes an exact ``Bash(<cmd>)`` rule. A
    command holding ``,``, ``(`` or ``)`` cannot be written as one rule and is returned in
    ``skipped`` instead. A read-only monitor, or a roster ``permission_mode: null``, gets nothing.
    """
    mode = claude_permission_mode(roster)
    if read_only or mode is None:
        return None, [], []
    allowed = ["Bash(%s:*)" % command for command in GIT_ALLOW]
    skipped: List[str] = []
    for prefix in ale_commands:
        if not prefix:
            continue
        if any(char in prefix for char in _RULE_UNSAFE):
            skipped.append(prefix)
            continue
        for subcommand in ALE_PROTOCOL:
            rule = "Bash(%s %s:*)" % (prefix, subcommand)
            if rule not in allowed:
                allowed.append(rule)
    for item in acceptance if isinstance(acceptance, list) else []:
        command = item.get("cmd") if isinstance(item, dict) else None
        if not isinstance(command, str) or not command.strip():
            continue
        if any(char in command for char in _RULE_UNSAFE) or "\n" in command:
            skipped.append(command)
            continue
        rule = "Bash(%s)" % command
        if rule not in allowed:
            allowed.append(rule)
    return mode, allowed, skipped


def spawn_id(harness_name: str, mode: str) -> str:
    """The executor id bin/ale-spawn dispatches on."""
    if mode == "pane":
        return "herdr-pane"
    return _SPAWN_IDS.get((harness_name, mode), harness_name)


def route(label: dict, roster: dict, lane: Optional[str] = None,
          assignment: Optional[dict] = None) -> dict:
    """Harness, model, mode and host for a label's executor assignment (or ``assignment``)."""
    from .roster import resolve

    labels = label.get("labels", {})
    if assignment is None:
        assignment = next((item for item in label.get("assignments") or []
                           if item.get("kind", "executor") == "executor"), {})
    pinned = assignment.get("executor")
    resolved = resolve(roster, assignment.get("role") or labels.get("role"),
                       assignment.get("model_tier") or labels.get("model_tier"), executor=pinned)
    chosen = pinned or resolved["executor"]
    name = normalize(chosen)
    model = assignment.get("model") or resolved["model"]
    mode = derive_mode(lane if lane is not None else labels.get("lane"), name, roster, mode_hint(chosen), model)
    host = derive_host(mode, labels.get("locality"), roster)
    if host != LOCAL and mode == "headless":
        # The only remote path is a herdr pane on that host; a headless argv would run locally.
        mode = "pane"
    return {"harness": name, "model": model, "mode": mode, "host": host,
            "resolved_from": "pin" if (pinned or assignment.get("model")) else "roster"}
