"""``ale setup`` layers: harnesses, models, CandleKeep refs, remote host, judge and project notes.

Detection is local and deterministic (``PATH`` lookups, ``<binary> --version``, the hosts
file); the ssh probe runs only when asked. Setup writes only ``.ale/roster.json`` (the caller
does that) and never an API key, token or password: a secret-looking answer is refused.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from . import harness as HARNESS

TIERS = ("cheap", "standard", "frontier")
NOTES_MAX = 2000
NO_HOST = "none"
LOGIN_COMMANDS = {"codex": "codex login", "pi": "pi /login", "claude": "claude /login"}

# The fixed questions; ``questions(detected)`` adds one ``models.<harness>.<tier>`` per
# detected harness and fills the defaults.
QUESTIONS: List[dict] = [
    {"id": "harness.default", "layer": "harness", "kind": "choice",
     "prompt": "Which harness runs executors by default?", "options": list(HARNESS.BUILTIN),
     "default": "claude"},
    {"id": "refs.book", "layer": "refs", "kind": "text",
     "prompt": "CandleKeep handbook book id for agent deep references ('build' to build one now, empty for none)",
     "default": ""},
    {"id": "refs.file", "layer": "refs", "kind": "text",
     "prompt": "Refs file that maps each agent to its handbook chapter (empty for none)", "default": ""},
    {"id": "remote.host", "layer": "remote", "kind": "choice",
     "prompt": "Remote host for work that does not run in-session ('none' keeps it local)",
     "options": [NO_HOST], "default": NO_HOST},
    {"id": "project.private", "layer": "judge", "kind": "bool",
     "prompt": "Is this repository private (no public remote)?", "default": True},
    {"id": "judge.mode", "layer": "judge", "kind": "choice",
     "prompt": "Label judge mode ('off' for private repositories)", "options": ["off", "shadow"],
     "default": "off"},
    {"id": "notes.project", "layer": "notes", "kind": "text",
     "prompt": "Anything else executors should know about this project?", "default": ""},
]

_SECRET = re.compile(r"(?i)(sk-|key|token|secret|password)")
_OPAQUE = re.compile(r"^(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_\-+=]{16,}$")


class SecretAnswer(ValueError):
    pass


def config_dir(env: Optional[dict] = None) -> Optional[str]:
    """``${XDG_CONFIG_HOME:-~/.config}`` from ``env``; None when neither is known."""
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        return env["XDG_CONFIG_HOME"]
    if env.get("HOME"):
        return os.path.join(env["HOME"], ".config")
    return None


def default_refs_file(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        return os.path.join(env["XDG_CONFIG_HOME"], "ale", "refs.json")
    return "~/.config/ale/refs.json"


def hosts_file(env: dict) -> Optional[str]:
    if env.get("ALE_HOSTS_FILE"):
        return env["ALE_HOSTS_FILE"]
    base = config_dir(env)
    return os.path.join(base, "herdr-exec.toml") if base else None


def _toml_string(raw: str) -> Optional[str]:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) < 2 or raw[0] != '"':
        return None
    out, index = [], 1
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            out.append(escapes.get(raw[index + 1], raw[index + 1]))
            index += 2
            continue
        if char == '"':
            rest = raw[index + 1:].strip()
            return "".join(out) if not rest or rest.startswith("#") else None
        out.append(char)
        index += 1
    return None


def parse_hosts_toml(text: str) -> List[dict]:
    """``[hosts.<name>]`` tables with string ``ssh`` and ``repo_root`` keys; everything else is ignored."""
    hosts: List[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            match = re.match(r'^\[\s*hosts\.(?:"([^"]+)"|([A-Za-z0-9_\-]+))\s*\]\s*(#.*)?$', stripped)
            current = None
            if match:
                current = {"name": match.group(1) or match.group(2)}
                hosts.append(current)
            continue
        if current is None or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in ("ssh", "repo_root"):
            parsed = _toml_string(value)
            if parsed is not None:
                current[key] = parsed
    return hosts


def _first_line(argv: List[str], env: dict, timeout: int = 5) -> Tuple[Optional[int], Optional[str]]:
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None, None
    text = (proc.stdout or b"").decode("utf-8", "replace").strip() or \
        (proc.stderr or b"").decode("utf-8", "replace").strip()
    return proc.returncode, (text.splitlines()[0].strip()[:200] if text else None)


def probe_host(ssh: str, env: dict) -> Tuple[Optional[bool], Optional[str]]:
    """``(reachable, ale_version)``; ssh exits 255 when it cannot connect."""
    code, line = _first_line(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh,
                              "ale --version"], env, timeout=15)
    if code is None or code == 255:
        return False, None
    return True, (line if code == 0 else None)


def _is_private(cwd: str, env: dict) -> bool:
    try:
        proc = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    return proc.returncode != 0 or not proc.stdout.strip()


def detect(env: dict, probe: bool = False, cwd: Optional[str] = None) -> dict:
    """What this machine has: harness binaries (and versions), ``ck``, remote hosts."""
    path = env.get("PATH", "")
    found, versions = {}, {}
    for name, entry in HARNESS.BUILTIN.items():
        binary = entry["headless"][0]
        found[name] = shutil.which(binary, path=path) is not None
        if found[name]:
            _, versions[name] = _first_line([shutil.which(binary, path=path), "--version"], env)
    hosts = []
    source = hosts_file(env)
    if source and os.path.isfile(source):
        try:
            with open(source, encoding="utf-8") as handle:
                parsed = parse_hosts_toml(handle.read())
        except (OSError, UnicodeError):
            parsed = []
        for host in parsed:
            ssh = host.get("ssh", "")
            item = {"name": host["name"], "ssh": ssh, "repo_root": host.get("repo_root"),
                    "reachable": None, "ale_version": None}
            if probe and ssh:
                item["reachable"], item["ale_version"] = probe_host(ssh, env)
            hosts.append(item)
    return {"harnesses": found, "versions": versions, "ck": shutil.which("ck", path=path) is not None,
            "hosts": hosts, "private": _is_private(cwd or os.getcwd(), env)}


def _row_model(roster: dict, name: str, tier: str) -> str:
    rows = [row for row in roster.get("routing", [])
            if row.get("model_tier") == tier and HARNESS.normalize(row.get("executor")) == name]
    rows.sort(key=lambda row: row.get("role") != "*")
    return rows[0]["model"] if rows else ""


def _default_harness(roster: dict) -> str:
    for row in roster.get("routing", []):
        if row.get("role") == "*" and row.get("model_tier") == "standard":
            return HARNESS.normalize(row.get("executor")) or "claude"
    return "claude"


def questions(detected: dict, roster: Optional[dict] = None, env: Optional[dict] = None) -> List[dict]:
    """QUESTIONS with defaults filled from detection and the current roster."""
    roster = roster or {}
    env = os.environ if env is None else env
    present = [name for name, ok in (detected.get("harnesses") or {}).items() if ok]
    out = []
    for base in QUESTIONS:
        question = copy.deepcopy(base)
        qid = question["id"]
        if qid == "harness.default":
            options = present or list(HARNESS.BUILTIN)
            current = _default_harness(roster)
            question["options"] = options
            question["default"] = current if current in options else options[0]
            out.append(question)
            for name in present:
                for tier in TIERS:
                    out.append({"id": "models.%s.%s" % (name, tier), "layer": "models", "kind": "text",
                                "prompt": "Model for %s at tier %s (empty keeps the roster)" % (name, tier),
                                "default": _row_model(roster, name, tier)})
            continue
        if qid == "refs.book":
            base = config_dir(env)
            built = bool(base) and os.path.isfile(os.path.join(base, "ale", "refs.json"))
            question["default"] = "build" if detected.get("ck") and not roster.get("refs_file") and not built else ""
        elif qid == "refs.file":
            default = default_refs_file(env)
            base = config_dir(env)
            exists = bool(base) and os.path.isfile(os.path.join(base, "ale", "refs.json"))
            question["default"] = roster.get("refs_file") or (default if detected.get("ck") or exists else "")
        elif qid == "remote.host":
            names = [host["name"] for host in detected.get("hosts") or [] if host.get("ssh")]
            question["options"] = [NO_HOST] + names
            question["default"] = roster.get("remote_host") or NO_HOST
        elif qid == "project.private":
            question["default"] = bool(detected.get("private", True))
        elif qid == "judge.mode":
            current = (roster.get("judge") or {}).get("default", "off")
            question["default"] = "off" if detected.get("private", True) else current
        elif qid == "notes.project":
            question["default"] = roster.get("notes") or ""
        out.append(question)
    return out


def refuse_secret(qid: str, value: object) -> None:
    """Raise when an answer looks like a credential; setup never stores one."""
    if not isinstance(value, str):
        return
    words = [word.strip("'\"`,;:()[]{}") for word in value.split()]
    has_keyword = _SECRET.search(value) is not None
    for word in words:
        # A long word that carries a credential marker (sk-..., api_key=...), or an opaque
        # letters-and-digits run in an answer that talks about a key, token or password.
        if (len(word) > 20 and _SECRET.search(word) and re.search(r"[0-9]", word)) or (has_keyword and _OPAQUE.match(word)):
            raise SecretAnswer("answer to %s looks like a credential; setup never stores one. "
                               "export it in your shell instead (for example in your shell profile)" % qid)


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("y", "yes", "true", "1")
    return bool(value)


def _agents_without_refs(refs_file: Optional[str], catalog: Optional[dict]) -> Tuple[List[str], int]:
    from . import refs as REFS
    if catalog is None:
        from . import agentcat, paths
        try:
            catalog = agentcat.load_catalog([os.path.join(paths.plugin_root(), "agents")])
        except Exception:  # a broken catalog must not break setup
            catalog = {}
    entries = REFS.load_refs(os.path.expanduser(refs_file)) if refs_file else {}
    missing = sorted(key for key, agent in catalog.items() if REFS.resolve(entries, agent) is None)
    return missing, len(catalog)


def apply(roster: dict, answers: dict, detected: dict,
          catalog: Optional[dict] = None) -> Tuple[dict, List[str]]:
    """The roster with the answers applied, and report lines. Raises ValueError on a secret."""
    for qid, value in answers.items():
        refuse_secret(qid, value)
    roster = copy.deepcopy(roster)
    report: List[str] = []
    routing = roster.setdefault("routing", [])

    chosen = answers.get("harness.default")
    if chosen:
        chosen = HARNESS.normalize(str(chosen))
        for row in routing:
            if row.get("role") == "*" and HARNESS.normalize(row.get("executor")) != chosen:
                fallback = _row_model(roster, chosen, row.get("model_tier"))
                row["executor"] = chosen
                if fallback:
                    row["model"] = fallback
                elif not answers.get("models.%s.%s" % (chosen, row.get("model_tier"))):
                    report.append("models: %s at tier %s still uses %s; answer models.%s.%s" %
                                  (chosen, row.get("model_tier"), row.get("model"), chosen, row.get("model_tier")))
        report.append("harness: default executor is %s" % chosen)

    for qid, value in sorted(answers.items()):
        if not qid.startswith("models.") or not isinstance(value, str) or not value.strip():
            continue
        parts = qid.split(".")
        if len(parts) != 3 or parts[2] not in TIERS:
            continue
        name, tier, model = parts[1], parts[2], value.strip()
        rows = [row for row in routing
                if row.get("model_tier") == tier and HARNESS.normalize(row.get("executor")) == name]
        for row in rows:
            row["model"] = model
        if not rows:
            routing.append({"role": "*", "model_tier": tier, "executor": name, "model": model})

    versions = detected.get("versions") or {}
    for name, ok in sorted((detected.get("harnesses") or {}).items()):
        if ok and versions.get(name):
            entry = roster.setdefault("harnesses", {}).setdefault(name, {})
            entry["version"] = versions[name]

    if "refs.file" in answers:
        value = (answers.get("refs.file") or "").strip()
        if value:
            roster["refs_file"] = value
        else:
            roster.pop("refs_file", None)
    book = (answers.get("refs.book") or "").strip() if isinstance(answers.get("refs.book"), str) else ""
    if book == "build":
        report.append("refs: run /ale:agent-handbook to build the handbook and refs file, then ale setup --check")
    elif book:
        report.append("refs: handbook book %s; run /ale:agent-handbook with it if the refs file is missing" % book)

    if "remote.host" in answers:
        host = (answers.get("remote.host") or "").strip()
        roster["remote_host"] = None if host in ("", NO_HOST) else host
        known = [item["name"] for item in detected.get("hosts") or []]
        if roster["remote_host"] and known and roster["remote_host"] not in known:
            report.append("remote: %s is not in the hosts file" % roster["remote_host"])

    private = _truthy(answers["project.private"]) if "project.private" in answers else detected.get("private")
    mode = answers.get("judge.mode")
    if mode in ("off", "shadow"):
        roster.setdefault("judge", {})["default"] = mode
    elif private:
        roster.setdefault("judge", {})["default"] = "off"
    if private and roster.get("judge", {}).get("default") == "shadow":
        report.append("judge: shadow on a private repository sends task text to the judge command")

    if "notes.project" in answers:
        notes = (answers.get("notes.project") or "").strip()
        if len(notes) > NOTES_MAX:
            notes = notes[:NOTES_MAX]
            report.append("notes: cut to %d characters" % NOTES_MAX)
        if notes:
            roster["notes"] = notes
        else:
            roster.pop("notes", None)

    missing, total = _agents_without_refs(roster.get("refs_file"), catalog)
    if not roster.get("refs_file"):
        report.append("refs: no refs file configured; %d of %d agents have no deep reference" %
                      (len(missing), total))
    else:
        report.append("refs: %d of %d agents have no refs entry%s" %
                      (len(missing), total, (": " + ", ".join(missing[:10])) if missing else ""))
    return roster, report


def check(roster: dict, detected: dict, catalog: Optional[dict] = None) -> Tuple[List[str], bool]:
    """Report lines and whether a configured layer is broken (refs file missing, remote unreachable)."""
    lines, broken = [], False
    versions = detected.get("versions") or {}
    for name, ok in sorted((detected.get("harnesses") or {}).items()):
        lines.append("harness %s: %s" % (name, ("found " + (versions.get(name) or "(no version)")) if ok else "not found"))
    for name, ok in sorted((detected.get("harnesses") or {}).items()):
        if ok and name in LOGIN_COMMANDS:
            lines.append("login %s: if it is not signed in, run `%s` yourself" % (name, LOGIN_COMMANDS[name]))
    lines.append("ck: %s" % ("found" if detected.get("ck") else "not found"))
    refs_file = roster.get("refs_file")
    if refs_file:
        path = os.path.expanduser(refs_file)
        if not os.path.isfile(path):
            broken = True
            lines.append("refs: BROKEN refs file %s is missing; run /ale:agent-handbook" % refs_file)
        missing, total = _agents_without_refs(refs_file, catalog)
        lines.append("refs: %d of %d agents have no refs entry%s" %
                     (len(missing), total, (": " + ", ".join(missing[:10])) if missing else ""))
    else:
        lines.append("refs: not configured")
    remote = roster.get("remote_host")
    hosts = {item["name"]: item for item in detected.get("hosts") or []}
    for name, item in sorted(hosts.items()):
        state = {True: "reachable", False: "unreachable", None: "not probed"}[item.get("reachable")]
        lines.append("host %s: %s%s" % (name, state,
                                         (", ale " + item["ale_version"]) if item.get("ale_version") else ""))
    if remote:
        item = hosts.get(remote)
        if item is None:
            broken = True
            lines.append("remote: BROKEN remote_host %s is not in the hosts file" % remote)
        elif item.get("reachable") is False:
            broken = True
            lines.append("remote: BROKEN remote_host %s is unreachable over ssh" % remote)
        elif item.get("reachable") and not item.get("ale_version"):
            lines.append("remote: %s is reachable but ale is not on its PATH" % remote)
    else:
        lines.append("remote: not configured (work runs locally)")
    lines.append("judge: %s" % (roster.get("judge") or {}).get("default", "off"))
    lines.append("notes: %s" % ("%d characters" % len(roster["notes"]) if roster.get("notes") else "none"))
    return lines, broken


def ask(question: dict, read=input, write=print) -> object:
    """One interactive question on stdin; an empty answer keeps the default."""
    default = question.get("default")
    shown = ("yes" if default else "no") if question["kind"] == "bool" else (default or "")
    options = question.get("options")
    suffix = (" (%s)" % "/".join(options)) if options else ""
    while True:
        try:
            raw = read("%s%s [%s]: " % (question["prompt"], suffix, shown)).strip()
        except EOFError:
            return default
        if not raw:
            return default
        if question["kind"] == "bool":
            if raw.lower() in ("y", "yes", "n", "no", "true", "false"):
                return raw.lower() in ("y", "yes", "true")
            write("answer yes or no")
            continue
        if question["kind"] == "choice" and options and raw not in options and question["id"] != "remote.host":
            write("choose one of: %s" % ", ".join(options))
            continue
        try:
            refuse_secret(question["id"], raw)
        except SecretAnswer as exc:
            write(str(exc))
            continue
        return raw
