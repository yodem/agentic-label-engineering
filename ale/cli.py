from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import fnmatch
import json
import os
import re
import subprocess
import sys
import secrets
import signal
import tempfile
import time
import webbrowser
from typing import List, Optional

from . import events as E
from .herdr_token import token_text
from . import handoff as H
from . import labelset as L
from . import lifecycle as LC
from . import roster as R
from . import verify as V
from . import agentcat as AC
from .paths import plugin_root
from . import watchdog as W
from . import binding as B
from . import hooks as HK
from .dispatch import render_prompt
from . import usage_transcript as UT
from . import timeline as TL
from . import dynamic as D
from . import runner as RUNNER
from .labeling import cascade as CAS
from .labeling import truth as TRUTH
from .labeling.shadow import summarize_shadow
from .labeling import evidence as EV
from . import decisions as DECISIONS
from .labeling.judge import CommandJudge, is_mostly_english
from .evalharness import corpus as CORPUS
from .evalharness import goldset as GOLDSET
from .evalharness import jevrun as JEVRUN
from .evalharness import report as REPORT
from .board import BoardServer, build_snapshot
from .runs import list_runs


_HERDR_RUNNER = subprocess.run
_PANE_UNSET = object()

OK, FAIL, USAGE, CLAIM_LOST, LEASE_LOST, SIGNOFF, BREACH = 0, 1, 2, 3, 4, 5, 6
TEXT_MAX = 1000


def _judge_mode(roster: dict) -> str:
    """Return ``off``, ``legacy`` or ``shadow`` for this roster.

    * ``shadow``: ``judge.default`` is ``shadow`` and a preregistered ``judge.bar``
      exists. Every judged decision collects additive shadow votes.
    * ``off``: explicit ``judge.default: off`` (the shipped roster), a
      ``shadow`` default without a bar, or no plugin. No judge call is made.
    * ``legacy``: no ``judge.default`` key (a roster written before Part B).
      Only the pre-existing label-cascade judge keeps its old opt-in behavior;
      no Part B decision votes are collected.
    """
    judge = roster.get("judge") or {}
    if judge.get("plugin") is None:
        return "off"
    default = judge.get("default")
    if default is None:
        return "legacy"
    if default == "shadow" and judge.get("bar"):
        return "shadow"
    return "off"


def _make_judge(roster: dict):
    judge = roster["judge"]
    return CommandJudge(judge.get("command") or ["jev-ask"], timeout_s=judge.get("timeout_s", 30),
                        model=judge.get("model"))


def _shadow_judge(roster: dict):
    """A judge for Part B decision votes, or None unless the roster is in shadow mode."""
    return _make_judge(roster) if _judge_mode(roster) == "shadow" else None


def _vote_fields(vote: dict) -> dict:
    fields = {key: vote.get(key) for key in ("decision", "options", "choice", "confidence",
                                             "model", "latency_ms", "uncertain")}
    for key in ("answers", "rule", "facts", "error", "bake_id", "calls_latency_ms"):
        if vote.get(key) is not None:
            fields[key] = vote[key]
    return fields


def _emit_vote(c: "Ctx", task_id: str, vote: dict, source: str, attempt: Optional[int] = None) -> None:
    if attempt is None:
        attempt = c.state().get("tasks", {}).get(task_id, {}).get("attempt", 1)
    c.emit("shadow_vote", task_id, None, attempt, authority="lead", additive=True, source=source,
           **_vote_fields(vote))


def _emit_outcome(c: "Ctx", task_id: str, decision: str, choice, source: str) -> bool:
    """Record what actually happened, once, and only after a pending shadow vote."""
    if choice is None or _judge_mode(c.roster or {}) != "shadow":
        return False
    pending = False
    for event in E.read_events(c.events_path):
        if event.get("task_id") != task_id or event.get("decision") != decision:
            continue
        if event.get("type") == "shadow_vote":
            pending = True
        elif event.get("type") == "decision_outcome":
            pending = False
    if not pending:
        return False
    attempt = c.state().get("tasks", {}).get(task_id, {}).get("attempt", 1)
    c.emit("decision_outcome", task_id, None, attempt, decision=decision, choice=choice,
           authority="lead", additive=True, source=source)
    return True


def _needs_monitor_outcome(label: dict) -> str:
    return "yes" if any((item or {}).get("kind") == "monitor"
                        for item in label.get("assignments") or []) else "no"


def _bake_outcomes(label: dict) -> dict:
    """Authoritative values for the bake-time decisions, read from the final label."""
    labels = label.get("labels") or {}
    values = {field: labels.get(field) for field in ("role", "model_tier", "risk", "effort", "sub", "phase")}
    values["locality"] = labels.get("locality") or "any"
    return {decision: value for decision, value in values.items() if value is not None}


def _bake_extra_votes(judge, roster: dict, label: dict, task_text: str) -> List[dict]:
    """Shadow votes for the bake decisions the label cascade does not ask."""
    labels = label.get("labels") or {}
    state = EV.state_json(task_title=label.get("title", ""), task_text=task_text)
    votes = [EV.choice_vote(judge, "sub", roster, state, role=labels.get("role")),
             EV.choice_vote(judge, "phase", roster, state)]
    cache = {}
    return votes


def _init_run_monitor_votes(c: "Ctx", judge, task_texts: dict = None) -> None:
    """Vote needs_monitor once at run initialization using final planner labels."""
    task_texts = task_texts or {}
    for task_id, label in c.labels.items():
        labels = label.get("labels") or {}
        body = task_texts.get(task_id, label.get("task_text", label.get("description", "")))
        state = EV.state_json(task_title=label.get("title", ""), task_text=body)
        vote = EV.evidence_vote(judge, "needs_monitor", c.roster, state,
                                {field: labels.get(field) for field in ("risk", "effort", "role")}, {})
        _emit_vote(c, task_id, vote, "init_run", attempt=1)
        _emit_outcome(c, task_id, "needs_monitor", _needs_monitor_outcome(label), "planner")


def _cascade_votes(roster: dict, votes: List[dict]) -> List[dict]:
    """Shadow-vote records for the judge votes the label cascade collected."""
    out = []
    for vote in votes:
        if not str(vote.get("by", "")).startswith("judge:") or vote.get("field") not in CAS.FIELDS:
            continue
        field = vote["field"]
        keys = DECISIONS.options_for_decision(field, roster)
        out.append(EV.from_choice_answer(field, keys, vote))
    return out


def _rejection_vote(c: "Ctx", judge, task_id: str, evidence: dict, reason: str) -> dict:
    """Evidence Nouls about a rejection; the action is computed by the rule table."""
    label = c.labels[task_id]
    failing = [{"id": item.get("id"), "output": str(item.get("tail") or "")[-300:]}
               for item in evidence.get("results", []) if not item.get("ok")]
    failing.extend({"id": "required", "command": str(item.get("command"))[:120],
                    "output": str(item.get("output") or "")[-300:]}
                   for item in evidence.get("required_failures", []))
    state = EV.state_json(task_title=label.get("title", ""), rejection_reason=reason[:300],
                          failing_checks=failing[:5])
    facts = {"fix_count": sum(1 for other in c.labels.values() if other.get("fixes") == task_id),
             "is_fix_task": bool(label.get("fixes"))}
    return EV.evidence_vote(judge, "rejection_action", c.roster, state, facts)


_VERDICT_LINE = re.compile(r"(?i)^[#*\s]*(verdict\b.*|(continue|nudge|fix|escalate)\W*)$")


def _monitor_vote(c: "Ctx", judge, task_id: str, report: str, wrote_files: List[str]) -> dict:
    """Evidence Nouls about a monitor report. Liveness facts come from events, never from Jev."""
    events = E.read_events(c.events_path)
    breaches = [event for event in events if event.get("type") == "breach" and event.get("task_id") == task_id]
    latest = breaches[-1].get("breach") if breaches else None
    task_state = c.state().get("tasks", {}).get(task_id, {})
    notes = task_state.get("notes") or []
    last_note = notes[-1] if notes else (task_state.get("last_step") or "")
    stripped = "\n".join(line for line in report.splitlines() if not _VERDICT_LINE.match(line.strip()))
    state = EV.state_json(task_title=c.labels[task_id].get("title", ""), breach=latest,
                          last_agent_note=str(last_note)[:500], monitor_report=stripped[:1500])
    facts = {"monitor_wrote_files": bool(wrote_files),
             "attempts_exhausted": any(event.get("breach") == "attempts_exhausted" for event in breaches),
             "breach": latest}
    return EV.evidence_vote(judge, "monitor_verdict", c.roster, state, facts)


class CliError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _git_root(path: str) -> str:
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(path)
        current = parent


def _ale_dir(path: str = None) -> str:
    cwd = os.path.abspath(path or os.getcwd())
    root = _git_root(cwd)
    current = cwd
    while True:
        candidate = os.path.join(current, ".ale")
        if os.path.isdir(candidate):
            return candidate
        if current == root:
            break
        parent = os.path.dirname(current)
        if parent == current or not os.path.commonpath([root, parent]) == root:
            break
        current = parent
    return os.path.join(root, ".ale")


def _plan_run_id(path: str, supplied: Optional[str]) -> str:
    value = supplied
    sidecar = path + ".ale-provenance.json"
    if value is None and os.path.isfile(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                data = json.load(handle)
            value = data.get("run_id") if isinstance(data, dict) else None
        except (OSError, ValueError):
            value = None
    if not value:
        value = re.sub(r"[^A-Za-z0-9._-]", "-", os.path.splitext(os.path.basename(path))[0])
    if not H.is_safe_id(value):
        raise CliError(USAGE, "plan run id is unsafe: %s" % value)
    return value


def _resolve_run_dir(a, plan_path: str = None) -> str:
    explicit = getattr(a, "run_dir", None)
    if explicit:
        return explicit
    root = _ale_dir()
    explicit_run_id = getattr(a, "run_id", None)
    if explicit_run_id:
        if not H.is_safe_id(explicit_run_id):
            raise CliError(USAGE, "unsafe run_id: %s" % explicit_run_id)
        return os.path.join(root, "runs", explicit_run_id)
    env_dir = os.environ.get("ALE_RUN_DIR")
    if env_dir:
        return env_dir
    if plan_path:
        return os.path.join(root, "runs", _plan_run_id(plan_path, None))
    current = os.path.join(root, "runs", "current")
    try:
        with open(current, encoding="utf-8") as handle:
            run_id = handle.read().strip()
    except OSError:
        run_id = ""
    if not run_id:
        raise CliError(USAGE, "no current run; initialize a run with `ale init-run --plan PLAN.md`")
    if not H.is_safe_id(run_id):
        raise CliError(FAIL, "unsafe run_id in %s/current" % os.path.dirname(current))
    candidate = os.path.join(root, "runs", run_id)
    if os.path.isdir(candidate):
        return candidate
    # Older ALE versions stored the run_id rather than the run directory name.
    runs_dir = os.path.join(root, "runs")
    try:
        entries = os.listdir(runs_dir)
    except OSError:
        entries = []
    for name in entries:
        run_dir = os.path.join(runs_dir, name)
        if not os.path.isdir(run_dir):
            continue
        for event in E.read_events(os.path.join(run_dir, "events.jsonl")):
            if event.get("type") == "run_started" and event.get("run_id") == run_id:
                return run_dir
    return candidate


def _write_current_run(run_dir: str, run_id: str, set_current: bool = False) -> None:
    ale_root = _ale_dir()
    if os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(run_dir)))) == os.path.realpath(ale_root):
        runs_dir = os.path.join(ale_root, "runs")
        os.makedirs(runs_dir, exist_ok=True)
        current = os.path.join(runs_dir, "current")
        should_write = set_current or not os.path.isfile(current)
        if not should_write:
            try:
                with open(current, encoding="utf-8") as handle:
                    pointer = handle.read().strip()
                should_write = not os.path.isdir(os.path.join(runs_dir, pointer))
            except OSError:
                should_write = True
        if should_write:
            H.write_atomic(current, os.path.basename(os.path.abspath(run_dir)) + "\n")


def _resolve_roster(a) -> str:
    explicit = getattr(a, "roster", None) or os.environ.get("ALE_ROSTER")
    if explicit:
        return explicit
    run_dir = getattr(a, "run_dir", None)
    project_root = _git_root(run_dir) if run_dir else _git_root(os.getcwd())
    return os.path.join(project_root, ".ale", "roster.json")


def _agent_roots(project_root: Optional[str] = None) -> List[str]:
    root = _git_root(project_root or os.getcwd())
    return [os.path.join(root, ".ale", "agents"), os.path.join(plugin_root(), "agents")]


def effective_rules(label: dict, agent: Optional[dict]) -> dict:
    rules = (agent or {}).get("rules") or {}
    return {key: sorted(set(rules.get(key, []) or []))
            for key in ("deny_paths", "deny_tools", "require_before_submit")}


def _parse_agent_variants(values: Optional[List[str]]) -> dict:
    variants = {}
    for value in values or []:
        if "=" not in value:
            raise CliError(USAGE, "--agent-variant must be <role>/<sub>=<path>")
        key, path = value.split("=", 1)
        parts = key.split("/")
        if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts) or not path:
            raise CliError(USAGE, "--agent-variant must be <role>/<sub>=<path>")
        if key in variants:
            raise CliError(USAGE, "duplicate --agent-variant for %s" % key)
        variants[key] = os.path.abspath(path)
    return variants


def _load_variant_agent(key: str, path: str) -> dict:
    parts = key.split("/")
    if (len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts)
            or not os.path.isfile(path)):
        raise CliError(USAGE, "invalid agent variant %s=%s" % (key, path))
    try:
        with open(path, "rb") as source:
            contents = source.read()
    except OSError as exc:
        raise CliError(USAGE, "cannot read agent variant %s: %s" % (path, exc))
    with tempfile.TemporaryDirectory(prefix="ale-agent-variant-") as root:
        staged = os.path.join(root, parts[0], parts[1] + ".md")
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        with open(staged, "wb") as destination:
            destination.write(contents)
        try:
            catalog = AC.load_catalog([root])
        except (AC.CatalogError, OSError, UnicodeError) as exc:
            raise CliError(USAGE, "invalid agent variant %s: %s" % (path, exc))
        if key not in catalog:
            raise CliError(USAGE, "agent variant does not declare %s" % key)
        agent = dict(catalog[key])
        agent["path"] = os.path.realpath(path)
        return agent


def _resolve_run_agents(c: "Ctx", agent_variants: Optional[List[str]] = None) -> bool:
    catalog = AC.load_catalog(_agent_roots())
    variants = _parse_agent_variants(agent_variants)
    variant_agents = {key: _load_variant_agent(key, path) for key, path in variants.items()}
    catalog.update(variant_agents)
    unresolved = []
    for task_id, label in c.labels.items():
        labels = label.get("labels", {})
        role, sub = labels.get("role", "general"), labels.get("sub")
        phase = labels.get("phase") or "implement"
        ref = AC.resolve_agent(catalog, role, sub, phase)
        if ref.get("matched") == "general" and role in ("frontend", "backend", "devops") and sub:
            unresolved.append("%s: %s/%s has no specialized agent" % (task_id, role, sub))
            continue
        label["routing"] = dict(label.get("routing") or {})
        label["routing"]["agent"] = {key: ref[key] for key in ("key", "path", "name", "sha256", "version", "matched")}
        if ref["key"] in variant_agents:
            label["routing"]["agent"].update({"variant": True, "path": variant_agents[ref["key"]]["path"]})
        agent = catalog.get(ref.get("key"))
        rules = effective_rules(label, agent)
        label["effective_rules"] = rules
        tier = labels.get("model_tier")
        floor = (agent or {}).get("model_tier_min")
        raised = False
        if floor in AC.MODEL_TIERS and tier in AC.MODEL_TIERS and AC.MODEL_TIERS.index(tier) < AC.MODEL_TIERS.index(floor):
            labels["model_tier"] = floor
            raised = True
        if floor in AC.MODEL_TIERS:
            for assignment in label.get("assignments", []):
                if assignment.get("kind") != "executor":
                    continue
                assignment_tier = assignment.get("model_tier", labels.get("model_tier"))
                if assignment_tier in AC.MODEL_TIERS and AC.MODEL_TIERS.index(assignment_tier) < AC.MODEL_TIERS.index(floor):
                    assignment["model_tier"] = floor
                    raised = True
        if raised:
            label.setdefault("provenance", {}).setdefault("model_tier", {})["by"] = "agent-floor"
        H.write_atomic(_label_path(c.run_dir, task_id), json.dumps(label, indent=2, sort_keys=True))
    for message in unresolved:
        print(message, file=sys.stderr)
    return not unresolved


class Ctx:
    def __init__(self, a: argparse.Namespace, need_roster: bool = True):
        self.run_dir = _resolve_run_dir(a)
        if not self.run_dir:
            raise CliError(USAGE, "--run-dir or ALE_RUN_DIR is required")
        self.events_path = os.path.join(self.run_dir, "events.jsonl")
        self.labels = L.load_labels(self.run_dir)
        if not self.labels:
            raise CliError(FAIL, "no labels under %s/labels" % self.run_dir)
        for tid in self.labels:
            if not H.is_safe_id(tid):
                raise CliError(FAIL, "unsafe task_id in label file: %r" % (tid,))
        self.run_id = next(iter(self.labels.values())).get("run_id", "")
        self.roster = R.load_roster(_resolve_roster(a)) if need_roster else None
        if need_roster:
            try:
                event_rows = E.read_events(self.events_path)
            except (ValueError, TypeError):
                event_rows = []
            self.labels = D.effective_labels(self.labels, event_rows, self._load_added)
        raw_now = a.now if a.now is not None else os.environ.get("ALE_NOW")
        if raw_now in (None, ""):
            self.now = time.time()
        else:
            try:
                self.now = float(raw_now)
            except (TypeError, ValueError):
                raise CliError(USAGE, "--now must be a number, got %r" % (raw_now,))

    def _load_added(self, label_file: str) -> dict:
        if (not isinstance(label_file, str) or os.path.basename(label_file) != label_file
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json", label_file)):
            raise ValueError("unsafe label_file")
        labels_dir = os.path.realpath(os.path.join(self.run_dir, "labels"))
        path = os.path.join(labels_dir, label_file)
        resolved = os.path.realpath(path)
        if os.path.commonpath([labels_dir, resolved]) != labels_dir:
            raise ValueError("label_file escapes labels directory")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def state(self) -> dict:
        return E.reduce_run(E.read_events(self.events_path), self.labels)

    def emit(self, kind: str, task_id: Optional[str] = None, agent_id: Optional[str] = None,
             attempt: Optional[int] = None, pane: Optional[str] = _PANE_UNSET,
             bind_claim_pane: bool = True, **extra) -> None:
        if kind == "spawned" and pane is _PANE_UNSET:
            # Keep the low-level helper's old default for direct callers. Dispatch
            # always passes pane explicitly (including None), so its pane cannot
            # leak into a spawned executor's event.
            pane = os.environ.get("HERDR_PANE_ID")
        elif pane is _PANE_UNSET or (pane is None and kind != "spawned"):
            pane = os.environ.get("HERDR_PANE_ID")
        if kind == "spawned" and pane:
            extra["pane"] = pane
        elif kind == "claimed":
            if pane and bind_claim_pane:
                extra["pane"] = pane
            else:
                extra.pop("pane", None)
            extra.pop("explicit_pane", None)
        E.append_event(self.events_path, E.make_event(kind, self.run_id, self.now, task_id, agent_id, attempt, **extra))
        self._publish(task_id)

    def _publish(self, task_id: Optional[str]) -> None:
        herdr_enabled = os.environ.get("ALE_HERDR")
        if not task_id or not (herdr_enabled == "1" or (herdr_enabled is None and os.environ.get("HERDR_PANE_ID"))):
            return
        events = E.read_events(self.events_path)
        pane = None
        for event in events:
            if event.get("task_id") == task_id and event.get("pane"):
                pane = event["pane"]
        if not pane or task_id not in self.labels:
            return
        try:
            task_state = self.state()["tasks"][task_id]
            text = token_text(task_id, self.labels[task_id], task_state)
            terminal = (task_state["state"] in ("accepted", "rejected", "canceled", "failed") or
                        (task_state["state"] == "released" and not task_state["claimable"]))
            result = _HERDR_RUNNER(
                ["herdr", "pane", "report-metadata", pane, "--source", "ale",
                 "--token", "ale=" + text, "--ttl-ms", "60000" if terminal else "900000"],
                timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError("herdr exited with %s" % result.returncode)
        except Exception:
            path = os.path.join(self.run_dir, "herdr-failures.count")
            try:
                with open(path, encoding="utf-8") as f:
                    count = int(f.read().strip() or "0")
            except (OSError, ValueError):
                count = 0
            H.write_atomic(path, str(count + 1) + "\n")

    def task(self, task_id: str) -> dict:
        if task_id not in self.labels:
            raise CliError(FAIL, "unknown task %s" % task_id)
        return self.state()["tasks"][task_id]

    def render(self, task_id: str, agent_id: Optional[str]) -> None:
        if agent_id:
            st = self.state()["tasks"][task_id]
            H.write_atomic(H.handoff_path(self.run_dir, task_id, agent_id),
                           H.render(task_id, agent_id, self.labels[task_id], st))


def _owned(c: Ctx, a: argparse.Namespace, kind: str, **extra) -> int:
    if a.task not in c.labels:
        raise CliError(FAIL, "unknown task %s" % a.task)
    state = c.state()
    LC.owner_guard(state, a.task, a.agent)
    c.emit(kind, a.task, a.agent, state["tasks"][a.task]["attempt"], **extra)
    c.render(a.task, a.agent)
    return OK


def cmd_validate(a) -> int:
    c = Ctx(a)
    errs = L.check_labelset(c.labels, c.roster)
    cwd = os.path.abspath(a.cwd or os.getcwd())
    for label in c.labels.values():
        for entry in label["context"]["allowed_paths"]:
            if entry.endswith("/") or (not re.search(r"[*?\[]", entry)
                                        and os.path.isdir(os.path.join(cwd, entry))):
                errs.append('allowed_paths entry "%s" is a directory: write "%s/*"' % (entry, entry))
    for e in errs:
        print(e, file=sys.stderr)
    return FAIL if errs else OK


def cmd_init_run(a) -> int:
    c = Ctx(a)
    if not H.is_safe_id(c.run_id):
        raise CliError(USAGE, "unsafe run_id: %s" % c.run_id)
    errs = L.check_labelset(c.labels, c.roster)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return FAIL
    if not _resolve_run_agents(c, getattr(a, "agent_variant", None)):
        return FAIL
    if any(e["type"] == "run_started" for e in E.read_events(c.events_path)):
        raise CliError(FAIL, "run already initialised: %s" % c.events_path)
    c.emit("run_started")
    rhash = R.roster_hash(c.roster)
    for tid, label in c.labels.items():
        c.emit("labeled", tid, None, 1, labels=label["labels"], roster_hash=rhash)
    judge = _shadow_judge(c.roster)
    if judge is not None:
        _init_run_monitor_votes(c, judge)
    decisions = os.path.join(c.run_dir, "decisions.md")
    if not os.path.exists(decisions):
        H.write_atomic(decisions, "# Decisions for run %s\n\n" % c.run_id)
    _write_current_run(c.run_dir, c.run_id, getattr(a, "set_current", False))
    return OK


def cmd_setup(a) -> int:
    ale_root = _ale_dir()
    roster_path = os.path.join(ale_root, "roster.json")
    if os.path.exists(roster_path) and getattr(a, "judge", None) is not None:
        try:
            with open(roster_path, encoding="utf-8") as handle:
                roster = json.load(handle)
            roster.setdefault("judge", {})["default"] = a.judge
            H.write_atomic(roster_path, json.dumps(roster, indent=2, sort_keys=True) + "\n")
        except (OSError, ValueError) as exc:
            raise CliError(FAIL, "cannot update %s: %s" % (roster_path, exc))
        print("Updated %s" % roster_path)
        return OK
    if os.path.exists(roster_path) and not a.force:
        raise CliError(FAIL, "refusing to overwrite %s without --force" % roster_path)
    os.makedirs(ale_root, exist_ok=True)
    example = os.path.join(os.path.dirname(__file__), "example_roster.json")
    with open(example, encoding="utf-8") as source:
        roster_text = source.read()
    if getattr(a, "judge", None) is not None:
        roster = json.loads(roster_text)
        roster.setdefault("judge", {})["default"] = a.judge
        roster_text = json.dumps(roster, indent=2, sort_keys=True) + "\n"
    H.write_atomic(roster_path, roster_text)
    root = _git_root(os.getcwd())
    git_dir = os.path.join(root, ".git")
    if os.path.isdir(git_dir):
        exclude = os.path.join(git_dir, "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        try:
            with open(exclude, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            content = ""
        if not any(line.strip() in (".ale", ".ale/") for line in content.splitlines()):
            with open(exclude, "a", encoding="utf-8") as handle:
                if content and not content.endswith("\n"):
                    handle.write("\n")
                handle.write(".ale/\n")
    print("Created %s" % roster_path)
    print("Next: /label-layer PLAN.md")
    return OK


def cmd_run(a) -> int:
    from .bake import compile_plan, extract_blocks, gaps, validate_compact_blocks

    if a.max_cycles < 1:
        raise CliError(USAGE, "--max-cycles must be at least 1")

    with open(a.plan_path, encoding="utf-8") as handle:
        plan_text = handle.read()
    variants = _parse_agent_variants(getattr(a, "agent_variant", None))
    variant_agents = {key: _load_variant_agent(key, path) for key, path in variants.items()}
    run_id = _plan_run_id(a.plan_path, a.run_id)
    if variants and a.run_id is None:
        variant_hashes = [(key, variant_agents[key]["sha256"]) for key in sorted(variants)]
        digest = variant_hashes[0][1] if len(variant_hashes) == 1 else hashlib.sha256(
            json.dumps(variant_hashes, separators=(",", ":")).encode("utf-8")).hexdigest()
        run_id += "-v%s" % digest[:8]
    a.run_id = run_id
    roster = _resolve_roster(a)
    missing = []
    try:
        validate_compact_blocks(plan_text)
        blocks = extract_blocks(plan_text)
        if not blocks:
            print("plan has no label blocks")
            print("run /label-layer %s to fill them" % a.plan_path)
            return SIGNOFF
        provenance = {}
        sidecar_path = a.plan_path + ".ale-provenance.json"
        if os.path.isfile(sidecar_path):
            with open(sidecar_path, encoding="utf-8") as handle:
                sidecar = json.load(handle)
            provenance = (sidecar.get("tasks") or {}) if isinstance(sidecar, dict) and "tasks" in sidecar else sidecar
        labels = compile_plan(plan_text, run_id, provenance=provenance)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    for task_id in sorted(labels):
        missing.extend("%s: %s" % (task_id, issue) for issue in gaps(labels[task_id]))
    if not labels:
        missing.append("plan has no label blocks")
    if missing:
        for item in missing:
            print(item)
        print("run /label-layer %s to fill them" % a.plan_path)
        return SIGNOFF

    run_dir = _resolve_run_dir(a, a.plan_path)
    events_path = os.path.join(run_dir, "events.jsonl")
    already_started = any(item.get("type") == "run_started" for item in E.read_events(events_path))
    if not already_started and not a.dry_run:
        init_args = ["init-run", "--plan", a.plan_path, "--run-id", run_id,
                     "--run-dir", run_dir, "--roster", roster]
        for variant in getattr(a, "agent_variant", None) or []:
            init_args.extend(["--agent-variant", variant])
        result = main(init_args)
        if result:
            return result
    if a.dry_run:
        actions = {"run_id": run_id, "run_dir": run_dir,
                   "start": "resume" if already_started else "init-run",
                   "tasks": [{"task_id": task_id,
                              "depends_on": labels[task_id].get("context", {}).get("depends_on", []),
                              "acceptance": [item.get("cmd") or "manual: %s" % item.get("manual", "")
                                             for item in labels[task_id].get("acceptance", [])]}
                             for task_id in sorted(labels)],
                   "loop": ["dispatch", "verify", "integrate", "fix", "watchdog"],
                   "max_cycles": a.max_cycles}
        if a.json:
            print(json.dumps(actions, sort_keys=True))
        else:
            print("%s run %s in %s" % (actions["start"], run_id, run_dir))
            for task in actions["tasks"]:
                print("task %s: dependencies=%s acceptance=%s" % (
                    task["task_id"], ",".join(task["depends_on"]) or "none",
                    ",".join(task["acceptance"])))
            print("cycles: %d; actions: dispatch, verify, integrate, fix, watchdog" % a.max_cycles)
        return OK

    c_args = argparse.Namespace(run_dir=run_dir, roster=roster, now=None, run_id=run_id)
    context = Ctx(c_args)
    context.roster_path = roster
    monitor_fixes_applied = set()
    for cycle in range(a.max_cycles):
        context = Ctx(c_args)
        context.roster_path = roster
        cycle_events = E.read_events(context.events_path)
        actions = RUNNER.next_actions(context.state(), context.labels, cycle_events)
        if any(kind == "exhausted" for kind, _ in actions):
            for action_kind, task_id in actions:
                if action_kind == "exhausted":
                    context.emit("breach", task_id, None, context.state()["tasks"][task_id]["attempt"],
                                 breach="attempts_exhausted", detail="two fixes already exist")
                    _emit_outcome(context, task_id, "rejection_action", "escalate", "run_loop")
            main(["status", "--run-dir", run_dir, "--roster", roster]
                 + (["--json"] if a.json else []))
            return _finish_run(context, BREACH)
        for kind, task_id in actions:
            if kind == "exhausted":
                continue
            args = [kind, "--task", task_id, "--run-dir", run_dir, "--roster", roster]
            if kind == "fix":
                result = main(args)
                if result:
                    return _finish_run(context, FAIL)
            else:
                result = main(args)
                if result:
                    if kind == "verify" and result == FAIL:
                        events_now = E.read_events(context.events_path)
                        submits = [index for index, event in enumerate(events_now)
                                   if event.get("task_id") == task_id and event.get("type") == "submitted"]
                        has_rejection = bool(submits) and any(
                            event.get("task_id") == task_id and event.get("type") == "rejected"
                            for event in events_now[submits[-1] + 1:])
                        if has_rejection:
                            continue
                    return _finish_run(context, FAIL)
            context = Ctx(c_args)
            context.roster_path = roster
        fresh_actions = RUNNER.next_actions(
            context.state(), context.labels, E.read_events(context.events_path))
        for kind, task_id in fresh_actions:
            if kind != "integrate":
                continue
            result = main([kind, "--task", task_id, "--run-dir", run_dir,
                           "--roster", roster, "--cwd", os.getcwd()])
            if result:
                return _finish_run(context, FAIL)
            context = Ctx(c_args)
            context.roster_path = roster
        result = main(["dispatch", "--spawn", "--cwd", os.getcwd(), "--run-dir", run_dir, "--roster", roster])
        if result:
            return _finish_run(context, result)
        result = main(["watchdog", "--run-dir", run_dir, "--roster", roster])
        latest = E.read_events(context.events_path)
        state = context.state()
        print_status = main(["status", "--run-dir", run_dir, "--roster", roster]
                            + (["--json"] if a.json else []))
        if print_status:
            return print_status
        if any(event.get("type") == "monitor_verdict" and event.get("verdict") == "escalate" for event in latest):
            return _finish_run(context, BREACH)
        for verdict_event in [event for event in latest if event.get("type") == "monitor_verdict"
                              and event.get("verdict") == "fix"]:
            task_id = verdict_event.get("task_id")
            if task_id in monitor_fixes_applied:
                continue
            refreshed = Ctx(c_args)
            if task_id in refreshed.labels and refreshed.state()["tasks"][task_id]["state"] == "rejected":
                result = main(["fix", "--task", task_id, "--run-dir", run_dir, "--roster", roster])
                monitor_fixes_applied.add(task_id)
                if result:
                    return _finish_run(context, FAIL)
        state = context.state()
        if RUNNER.is_complete(state, context.labels):
            return _finish_run(context, OK)
        pending = RUNNER.next_actions(state, context.labels, latest)
        dispatch_state = _dispatch_state(context)
        due = __import__("ale.dispatch", fromlist=["due_assignments"]).due_assignments(
            dispatch_state, context.labels, context.roster)
        if not pending and not due:
            break
        liveness_breaches = {"stuck", "lease_expired"}
        released_retries = sorted({item["task_id"] for item in due
                                   if state["tasks"].get(item["task_id"], {}).get("state") == "released"
                                   and any(event.get("task_id") == item["task_id"]
                                           and event.get("type") == "breach"
                                           and event.get("breach") in liveness_breaches
                                           for event in latest)})
        for task_id in released_retries:
            print("redispatching released task %s after liveness breach" % task_id)
    state = context.state()
    return _finish_run(context, OK if RUNNER.is_complete(state, context.labels) else BREACH)


def _finish_run(context, code: int) -> int:
    roster = getattr(context, "roster_path", _resolve_roster(argparse.Namespace()))
    main(["timeline", "--tail", "20", "--run-dir", context.run_dir, "--roster", roster])
    main(["meta", "--run-dir", context.run_dir, "--roster", roster])
    breaches = [event for event in E.read_events(context.events_path) if event.get("type") == "breach"]
    by_task = {}
    for event in breaches:
        task_id = event.get("task_id") or "run"
        by_task.setdefault(task_id, []).append(event.get("breach", "unknown"))
    for task_id in sorted(by_task):
        print("%s breaches: %s" % (task_id, ", ".join(by_task[task_id])))
    print("breaches: %d" % len(breaches))
    return code


def cmd_status(a) -> int:
    c = Ctx(a)
    state = c.state()
    try:
        catalog = AC.load_catalog(_agent_roots())
    except Exception:
        catalog = None
    for tid, label in c.labels.items():
        if label.get("fixes") and state["tasks"].get(label["fixes"], {}).get("integrated"):
            state["tasks"][tid]["integrated"] = True
    if a.json:
        events = E.read_events(c.events_path)
        for tid, st in state["tasks"].items():
            label = c.labels[tid]
            frozen = (label.get("routing") or {}).get("agent") or {}
            current = (catalog or {}).get(frozen.get("key")) if frozen else None
            agent_status = "agent: unknown"
            if current and frozen:
                agent_status = "agent=%s@%s" % (
                    frozen.get("name", "unknown"), (frozen.get("sha256") or "")[:8])
                if current.get("sha256") != frozen.get("sha256"):
                    agent_status += " agent: stale"
            st["agent"] = agent_status
            st["blocked_by"] = list(st.get("blocked_by", []))
            st["assignees"] = list(st.get("assignees", []))
            st["attempt"] = st.get("attempt", 1)
            st["breaches"] = [x[0] if isinstance(x, list) else x for x in st.get("breaches_seen", [])]
            watch = L.effective_watch(label, c.roster)
            base_ts = st.get("last_heartbeat_ts") or st.get("started_ts")
            st["lease_expires_ts"] = (base_ts + watch["heartbeat_timeout_s"]) if base_ts is not None else None
            st["fixes"] = sorted(x for x, item in c.labels.items() if item.get("fixes") == tid)
            st["fixed_by"] = sorted(x for x, item in c.labels.items() if item.get("fixes") == tid and
                                     state["tasks"].get(x, {}).get("state") == "accepted")
            for event in events:
                if event.get("task_id") == tid and event.get("type") == "spawned" and event.get("agent_id_minted"):
                    if event["agent_id_minted"] not in st["assignees"]:
                        st["assignees"].append(event["agent_id_minted"])
        print(json.dumps(state, sort_keys=True))
    else:
        events = E.read_events(c.events_path)
        for tid in sorted(state["tasks"]):
            st = state["tasks"][tid]
            spawn = _latest_spawn(c, tid)
            worktree = (spawn or {}).get("worktree", "-")
            if worktree != "-":
                worktree = os.path.relpath(worktree, c.run_dir)
            step = (st["last_step"] or "-")[:24]
            frozen = (c.labels[tid].get("routing") or {}).get("agent") or {}
            current = (catalog or {}).get(frozen.get("key")) if frozen else None
            if not current or not frozen:
                agent_status = "agent: unknown"
            else:
                agent_status = "agent=%s@%s" % (frozen.get("name", "unknown"), (frozen.get("sha256") or "")[:8])
                if current.get("sha256") != frozen.get("sha256"):
                    agent_status += " agent: stale"
            print("%-8s %-15s attempt=%d owner=%s tokens=%d step=%s wt=%s branch=%s integrated=%s %s" % (
                tid, st["state"], st["attempt"], st["owner"] or "-", st["tokens"],
                step, worktree,
                (spawn or {}).get("branch", "-"), "yes" if st.get("integrated") else "no", agent_status))
    return OK


def cmd_board(a) -> int:
    """Serve the read-only board for one run until interrupted."""
    runs_dir = _default_runs_dir()
    if not getattr(a, "run_dir", None) and not os.environ.get("ALE_RUN_DIR") and not getattr(a, "run_id", None):
        run_dir = _choose_board_run(runs_dir)
        if not run_dir:
            raise CliError(FAIL, "no run found under %s" % runs_dir)
        print("ale board: chose run %s" % run_dir, file=sys.stderr)
    else:
        run_dir = _resolve_run_dir(a)
    events_path = os.path.join(run_dir, "events.jsonl")
    labels_dir = os.path.join(run_dir, "labels")
    if not os.path.isdir(run_dir) or not os.path.isdir(labels_dir) or not os.path.isfile(events_path):
        raise CliError(FAIL, "run directory must contain labels/ and events.jsonl: %s" % run_dir)

    def provider():
        status_args = [sys.executable, "-m", "ale", "status", "--json", "--run-dir", run_dir]
        if getattr(a, "roster", None):
            status_args += ["--roster", a.roster]
        result = subprocess.run(
            status_args,
            shell=False, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "status failed")
        status = json.loads(result.stdout)
        labels = L.load_labels(run_dir)
        try:
            events = E.read_events(events_path)
        except (ValueError, TypeError):
            events = []
        return build_snapshot(run_dir, status, labels, events, {},
                              runs_dir=os.path.dirname(os.path.abspath(run_dir)))

    token = secrets.token_urlsafe(32)
    server = BoardServer(run_dir, provider, token=token, port=0)
    try:
        server.start()
    except OSError as exc:
        raise CliError(FAIL, "cannot bind board listener: %s" % exc)

    def stop_board(_signum, _frame):
        server.close()
        raise SystemExit(OK)

    signal.signal(signal.SIGTERM, stop_board)
    signal.signal(signal.SIGINT, stop_board)
    print(server.url, flush=True)
    if a.open:
        webbrowser.open(server.url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return OK
    finally:
        server.close()


def _choose_board_run(runs_dir: str) -> Optional[str]:
    rows = list_runs(runs_dir)
    return rows[0]["path"] if rows else None


def _default_runs_dir() -> str:
    return os.path.join(_ale_dir(), "runs")


def cmd_runs(a) -> int:
    runs_dir = a.runs_dir or _default_runs_dir()
    rows = list_runs(runs_dir)
    if a.json:
        print(json.dumps(rows, sort_keys=True))
        return OK
    now = time.time()
    for row in rows:
        age = max(0, int(now - row["last_event_ts"]))
        if age < 60:
            age_text = "%ds ago" % age
        elif age < 3600:
            age_text = "%dm ago" % (age // 60)
        elif age < 86400:
            age_text = "%dh ago" % (age // 3600)
        else:
            age_text = "%dd ago" % (age // 86400)
        print("%s\t%s%s" % (row["dir"], age_text, "\tcurrent" if row["is_current"] else ""))
    return OK


def cmd_ready(a) -> int:
    c = Ctx(a)
    for tid, st in sorted(c.state()["tasks"].items()):
        if st["claimable"]:
            print(tid)
    return OK


def cmd_claim(a) -> int:
    c = Ctx(a)
    if a.task not in c.labels:
        raise CliError(FAIL, "unknown task %s" % a.task)
    cap = L.effective_watch(c.labels[a.task], c.roster)["max_attempts"]
    state = c.state()
    if not state["tasks"][a.task]["claimable"] or state["tasks"][a.task]["attempt"] > cap:
        print("claim lost: %s" % a.task, file=sys.stderr)
        return CLAIM_LOST
    executor_id = os.environ.get("ALE_AGENT_ID") or os.environ.get("ALE_AGENT")
    c.emit("claimed", a.task, a.agent, state["tasks"][a.task]["attempt"], pane=a.pane,
           bind_claim_pane=bool(a.pane) or bool(executor_id and a.agent == executor_id))
    if c.state()["tasks"][a.task]["owner"] != a.agent:
        print("claim lost: %s" % a.task, file=sys.stderr)
        return CLAIM_LOST
    c.render(a.task, a.agent)
    return OK


def cmd_heartbeat(a) -> int:
    c = Ctx(a)
    if a.throttle_s is not None:
        state = c.state()["tasks"].get(a.task)
        if state is not None and state.get("last_heartbeat_ts") is not None and c.now - state["last_heartbeat_ts"] < a.throttle_s:
            return OK
    extra = {"step": a.step[:TEXT_MAX]}
    if a.auto:
        extra["auto"] = True
    if a.files:
        extra["files_modified"] = [p for p in a.files.split(",") if p]
    if a.pending:
        extra["pending"] = a.pending
    if a.next:
        extra["next_steps"] = a.next
    return _owned(c, a, "heartbeat", **extra)


def cmd_note(a) -> int:
    extra = {"text": a.text[:TEXT_MAX]}
    if a.to:
        extra["to"] = a.to
    c = Ctx(a)
    if a.agent:
        return _owned(c, a, "note", **extra)
    if a.task not in c.labels:
        raise CliError(FAIL, "unknown task %s" % a.task)
    state = c.state()
    c.emit("note", a.task, None, state["tasks"][a.task]["attempt"], lead=True, **extra)
    owner = state["tasks"][a.task].get("owner")
    if owner:
        c.render(a.task, owner)
    return OK


def cmd_input_required(a) -> int:
    return _owned(Ctx(a), a, "input_required", question=a.question[:TEXT_MAX])


def cmd_submit(a) -> int:
    return _owned(Ctx(a), a, "submitted", summary=a.summary[:TEXT_MAX])


def cmd_answer(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    if st["state"] != "input-required":
        raise CliError(FAIL, "task %s is %s, not input-required" % (a.task, st["state"]))
    c.emit("input_answered", a.task, None, st["attempt"], text=a.text[:TEXT_MAX])
    c.render(a.task, st["owner"])
    return OK


def _changed_files(cwd: str, base: str) -> List[str]:
    out: List[str] = []
    for cmd in (["git", "diff", "--name-only", base], ["git", "ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise CliError(FAIL, "%s failed: %s" % (" ".join(cmd), proc.stderr.strip()))
        out.extend(line for line in proc.stdout.splitlines() if line)
    return sorted(set(out))


def _fit(evidence: dict) -> None:
    while len(json.dumps(evidence)) > 3000 and any(r["tail"] for r in evidence["results"]):
        for r in evidence["results"]:
            half = len(r["tail"]) // 2
            r["tail"] = r["tail"][-half:] if half else ""
    files = evidence.get("files")
    if files:
        evidence.setdefault("files_count", len(files))
        limit = len(files)
        while len(json.dumps(evidence)) > 3000 and limit > 0:
            limit = max(0, limit - max(1, limit // 4))
            evidence["files"] = files[:limit]
            evidence["files_truncated"] = limit < evidence["files_count"]
    violations = evidence.get("path_violations")
    if violations:
        limit = len(violations)
        while len(json.dumps(evidence)) > 3000 and limit > 0:
            limit = max(0, limit - max(1, limit // 4))
            evidence["path_violations"] = violations[:limit]


def cmd_verify(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    if st["state"] != "submitted":
        raise CliError(FAIL, "task %s is %s, not submitted" % (a.task, st["state"]))
    label, owner, attempt = c.labels[a.task], st["owner"], st["attempt"]
    cwd = _task_project_root(c, a.task, a.cwd)
    if a.reject is not None:
        reason = a.reject[:TEXT_MAX]
        evidence = {"passed": False, "manual": [], "results": [], "required": [],
                    "required_failures": [], "files": [], "manual_rejection": reason}
        _fit(evidence)
        c.emit("verified", a.task, None, attempt, evidence=evidence)
        c.emit("rejected", a.task, None, attempt, evidence=evidence, reason=reason)
        c.render(a.task, owner)
        print(reason, file=sys.stderr)
        return FAIL
    required = V.run_required((label.get("effective_rules") or {}).get("require_before_submit", []), cwd)
    evidence = V.run_acceptance(label, cwd)
    evidence["required"] = required
    evidence["required_failures"] = [item for item in required if not item["ok"]]
    if evidence["required_failures"]:
        evidence["passed"] = False
    evidence.setdefault("files", [])
    reason = None
    if a.base:
        changed = [path for path in _changed_files(cwd, a.base) if path != ".ale-setup-done"]
        evidence["files"] = changed
        bad = V.paths_within(changed, label["context"]["allowed_paths"],
                             (label.get("effective_rules") or {}).get("deny_paths", []))
        evidence["path_violations"] = bad[:20]
        if bad:
            reason = "path_violation: %s" % ", ".join(bad[:5])
    if reason is None and evidence["required_failures"]:
        reason = "required command failed: %s" % evidence["required_failures"][0]["command"]
    if reason is None and not evidence["passed"]:
        reason = "acceptance failed: %s" % ", ".join(r["id"] for r in evidence["results"] if not r["ok"])
    _fit(evidence)
    c.emit("verified", a.task, None, attempt, evidence=evidence)
    if reason is not None:
        c.emit("rejected", a.task, None, attempt, evidence=evidence, reason=reason)
        shadow_judge = _shadow_judge(c.roster)
        if shadow_judge is not None:
            _emit_vote(c, a.task, _rejection_vote(c, shadow_judge, a.task, evidence, reason), "run_loop")
        c.render(a.task, owner)
        print(reason, file=sys.stderr)
        return FAIL
    if (evidence["manual"] or label["labels"]["risk"] == "high") and not a.signoff:
        print("needs sign-off: manual=%s risk=%s" % (evidence["manual"], label["labels"]["risk"]), file=sys.stderr)
        return SIGNOFF
    if a.signoff:
        evidence["signoff"] = a.signoff
    c.emit("accepted", a.task, None, attempt, evidence=evidence)
    _adjudicate_shadow_acceptance(c, a.task)
    c.render(a.task, owner)
    return OK


def _adjudicate_shadow_acceptance(c: Ctx, task_id: str) -> None:
    if _judge_mode(c.roster or {}) != "shadow":
        return
    fields = ("role", "sub", "phase", "model_tier", "risk", "effort", "locality")
    votes = {}
    already = set()
    for event in E.read_events(c.events_path):
        if event.get("task_id") != task_id:
            continue
        if event.get("type") == "shadow_vote" and event.get("decision") in fields:
            votes[event["decision"]] = event.get("choice")
        elif event.get("type") == "adjudicated":
            decision = event.get("decision") or event.get("field")
            if decision in fields:
                already.add(decision)
    label = c.labels[task_id]
    labels = label.get("labels") or {}
    attempt = c.state().get("tasks", {}).get(task_id, {}).get("attempt", 1)
    for decision in fields:
        if decision not in votes or decision in already:
            continue
        final = labels.get(decision)
        choice = votes[decision]
        if choice is None:
            continue
        if choice == final and final is not None:
            c.emit("adjudicated", task_id, None, attempt, field=decision, decision=decision,
                   choice=final, value=final, by="agreement_then_accepted",
                   authority="lead", additive=True)
        elif choice != final:
            if final is None:
                options = DECISIONS.options_for_decision(decision, c.roster, label=label)
                print("adjudicate %s %s: planner=unset jev=%s -> ale adjudicate --task %s --decision %s --value <one of: %s>" %
                      (task_id, decision, choice, task_id, decision, "|".join(options)), file=sys.stderr)
            else:
                print("adjudicate %s %s: planner=%s jev=%s -> ale adjudicate --task %s --decision %s --value %s (or --value %s to side with Jev)" %
                      (task_id, decision, final, choice, task_id, decision, final, choice), file=sys.stderr)


def cmd_check(a) -> int:
    c = Ctx(a)
    c.task(a.task)
    if getattr(a, "required", False):
        results = V.run_required((c.labels[a.task].get("effective_rules") or {}).get("require_before_submit", []),
                                 a.cwd or os.getcwd())
        failed = [item for item in results if not item["ok"]]
        print(json.dumps(results, sort_keys=True) if a.json else "\n".join(
            "%s %s" % ("ok" if item["ok"] else "FAIL", item["command"]) for item in results))
        return FAIL if failed else OK
    evidence = V.run_acceptance(c.labels[a.task], a.cwd or os.getcwd())
    if a.json:
        print(json.dumps(evidence, sort_keys=True))
    else:
        for result in evidence["results"]:
            print("%s %s exit=%d" % (result["id"], "ok" if result["ok"] else "FAIL", result["exit"]))
        if evidence["manual"]:
            print("manual: %s" % ", ".join(evidence["manual"]))
    return OK if evidence["passed"] else FAIL


def cmd_watchdog(a) -> int:
    c = Ctx(a)
    breaches = W.check(c.state(), c.labels, c.roster, c.now)
    for b in breaches:
        c.emit("breach", b["task_id"], None, b["attempt"], breach=b["breach"], detail=b["detail"][:300])
        if b["breach"] == "lease_expired":
            c.emit("lease_expired", b["task_id"], None, b["attempt"])
            c.emit("released", b["task_id"], None, b["attempt"])
        elif b["breach"] == "attempts_exhausted":
            c.emit("failed", b["task_id"], None, b["attempt"], reason="attempts_exhausted")
    print(json.dumps(breaches))
    return BREACH if breaches else OK


def cmd_usage(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    extra = {"gen_ai.request.model": a.model, "gen_ai.usage.input_tokens": a.input_tokens,
             "gen_ai.usage.output_tokens": a.output_tokens,
             "gen_ai.usage.cache_read_input_tokens": a.cache_read_tokens,
             "gen_ai.usage.cache_creation_input_tokens": a.cache_write_tokens,
             "usage_source": a.source}
    if a.cost_usd is not None:
        extra["cost_usd"] = a.cost_usd
    c.emit("usage", a.task, a.agent, st["attempt"], **extra)
    return OK


def _hook_home() -> str:
    return os.environ.get("ALE_HOME") or os.path.expanduser("~")


def _hook_ctx(binding: dict) -> Ctx:
    return Ctx(argparse.Namespace(run_dir=binding["run_dir"], roster=binding["roster"], now=None))


def _read_optional(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _hook_debug(binding: dict, event: str) -> None:
    if os.environ.get("ALE_HOOK_DEBUG") == "1":
        path = os.path.join(binding["run_dir"], "hook-debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s\n" % event)


def _hook_binding(data: dict) -> Optional[dict]:
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    return B.resolve(os.environ, _hook_home(), session_id, data.get("agent_id"))


def _resolved_hook_input(tool_name: str, tool_input: dict, project_root: str) -> dict:
    result = dict(tool_input)
    root = os.path.realpath(project_root)

    def resolve_path(path: str) -> str:
        raw = path if os.path.isabs(path) else os.path.join(project_root, path)
        candidate = os.path.join(os.path.realpath(os.path.dirname(raw)), os.path.basename(raw))
        if os.path.exists(raw):
            candidate = os.path.realpath(raw)
        return os.path.relpath(candidate, root)

    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    if isinstance(result.get(key), str):
        result[key] = resolve_path(result[key])
    edits = result.get("edits")
    if isinstance(edits, list):
        copied = []
        for edit in edits:
            if not isinstance(edit, dict):
                copied.append(edit)
                continue
            item = dict(edit)
            if isinstance(item.get(key), str):
                item[key] = resolve_path(item[key])
            copied.append(item)
        result["edits"] = copied
    return result


def _hook_orchestrator(data: dict) -> int:
    run_dir = os.environ.get("ALE_ORCHESTRATOR_RUN_DIR")
    if not run_dir:
        return OK
    roster = os.environ.get("ALE_ROSTER") or os.path.join(_ale_dir(), "roster.json")
    c = Ctx(argparse.Namespace(run_dir=run_dir, roster=roster, now=None))
    breaches = W.check(c.state(), c.labels, c.roster, c.now)
    for breach in breaches:
        c.emit("breach", breach["task_id"], None, breach["attempt"], breach=breach["breach"], detail=breach["detail"][:300])
    state = c.state()
    counts = {}
    for task in state["tasks"].values():
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    for name in sorted(counts):
        print("%s: %d" % (name, counts[name]))
    for breach in breaches:
        print("breach: %s %s" % (breach["task_id"], breach["breach"]))
    waiting = [tid for tid, task in state["tasks"].items() if task["state"] == "submitted"]
    if waiting:
        print("awaiting verify: %s" % ", ".join(sorted(waiting)))
    return OK


def _usage_for_hook(c: Ctx, binding: dict, data: dict, attempt: int) -> None:
    path = data.get("agent_transcript_path") or data.get("transcript_path")
    if not path or not os.path.exists(path):
        return
    seen_path = os.path.join(c.run_dir, "usage-seen", "%s.%s.json" % (binding["task_id"], binding["agent_id"]))
    try:
        with open(seen_path, encoding="utf-8") as f:
            already = set(json.load(f))
    except (OSError, ValueError, TypeError):
        already = set()
    with open(path, encoding="utf-8") as f:
        usage = UT.sum_usage(f, already)
    H.write_atomic(seen_path, json.dumps(sorted(already)))
    if not usage["message_ids"]:
        return
    c.emit("usage", binding["task_id"], binding["agent_id"], attempt,
           **{"gen_ai.request.model": usage["model"],
              "gen_ai.usage.input_tokens": usage["input_tokens"],
              "gen_ai.usage.output_tokens": usage["output_tokens"],
              "usage_source": "adapter"})


def cmd_hook(a) -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return OK
    if not isinstance(data, dict):
        return OK
    event = a.event
    if (event == "pre-tool" and os.environ.get("ALE_READ_ONLY") == "1"
            and data.get("tool_name") in HK.EDIT_TOOLS):
        print("monitor is read-only", file=sys.stderr)
        return 2
    if event == "prompt-submit" and os.environ.get("ALE_ORCHESTRATOR_RUN_DIR"):
        try:
            return _hook_orchestrator(data)
        except Exception as exc:
            print("ale hook warning: %s" % exc, file=sys.stderr)
            return OK
    binding = _hook_binding(data)
    if binding is None:
        return OK
    try:
        _hook_debug(binding, event)
        c = _hook_ctx(binding)
        if binding["task_id"] not in c.labels:
            raise CliError(FAIL, "bound task is missing")
        label = c.labels[binding["task_id"]]
        state = c.state()["tasks"][binding["task_id"]]
        if event == "session-start":
            if state["state"] in E.LIVE and state.get("owner") == binding["agent_id"]:
                pass
            elif state.get("claimable"):
                watch = L.effective_watch(label, c.roster)
                if not LC.try_claim(c.events_path, c.labels, c.run_id, binding["task_id"], binding["agent_id"], c.now, watch["max_attempts"]):
                    print("STOP: task claim was lost; stop working on this task")
                    return OK
            elif state.get("owner") is not None or state["state"] in E.TERMINAL:
                print("STOP: task claim was lost; stop working on this task")
                return OK
            handoff = _read_optional(H.handoff_path(c.run_dir, binding["task_id"], binding["agent_id"]))
            if not handoff:
                for assignment in label.get("assignments", []):
                    role = assignment.get("role")
                    if role:
                        candidate = H.handoff_path(c.run_dir, binding["task_id"], binding["agent_id"], role)
                        handoff = _read_optional(candidate)
                        if handoff:
                            break
            decisions = _read_optional(os.path.join(c.run_dir, "decisions.md"))
            print(HK.session_context(label, handoff, decisions))
            return OK
        if event == "pre-tool":
            project_root = _task_project_root(c, binding["task_id"], data.get("cwd"))
            tool_name = data.get("tool_name", "")
            tool_input = _resolved_hook_input(tool_name, data.get("tool_input") or {}, project_root)
            result = HK.decide_pre_tool(binding, label, state, tool_name, tool_input, project_root)
            if result["action"] == "deny":
                print(result["reason"], file=sys.stderr)
                return 2
            return OK
        if event == "post-tool":
            decision = HK.decide_heartbeat(state, c.now, 60, data.get("tool_name", ""), data.get("tool_input") or {})
            if decision is not None and state.get("owner") == binding["agent_id"]:
                c.emit("heartbeat", binding["task_id"], binding["agent_id"], state["attempt"], step=decision["step"], files_modified=decision["files"], auto=True)
                c.render(binding["task_id"], binding["agent_id"])
            return OK
        if event == "stop":
            if data.get("stop_hook_active"):
                return OK
            cwd = data.get("cwd") or os.getcwd()
            required = V.run_required((label.get("effective_rules") or {}).get("require_before_submit", []), cwd)
            evidence = V.run_acceptance(label, cwd)
            evidence["required_failures"] = [item for item in required if not item["ok"]]
            if evidence["required_failures"]:
                evidence["passed"] = False
            blocks = sum(1 for note in state.get("notes", []) if note.startswith("auto-stop-block") and state.get("attempt") is not None)
            decision = HK.decide_stop(label, state, evidence, blocks, 2)
            if decision["action"] == "block":
                c.emit("note", binding["task_id"], binding["agent_id"], state["attempt"], text="auto-stop-block: " + decision["reason"])
                print(json.dumps({"decision": "block", "reason": decision["reason"]}))
            elif decision["action"] == "input_required":
                c.emit("input_required", binding["task_id"], binding["agent_id"], state["attempt"], question=decision["question"])
                print(json.dumps({"decision": "block", "reason": decision["question"]}))
            elif decision["action"] == "submit":
                c.emit("submitted", binding["task_id"], binding["agent_id"], state["attempt"], summary=decision["summary"])
                _usage_for_hook(c, binding, data, state["attempt"])
            return OK
        return OK
    except Exception as exc:
        if event == "pre-tool":
            print("ale hook denied: bound executor state could not be read: %s" % exc, file=sys.stderr)
            return 2
        print("ale hook warning: %s" % exc, file=sys.stderr)
        return OK


def cmd_guard_path(a) -> int:
    c = Ctx(a)
    c.task(a.task)
    label = c.labels[a.task]
    root = a.project_root or os.getcwd()
    relative = _resolved_hook_input("Write", {"file_path": a.path}, root)["file_path"]
    return OK if not V.paths_within([relative], label["context"]["allowed_paths"]) else FAIL


def _record_decision(c: Ctx, text: str) -> None:
    c.emit("decision", text=text)
    with open(os.path.join(c.run_dir, "decisions.md"), "a", encoding="utf-8") as f:
        f.write("- [%d] %s\n" % (int(c.now), text))


def cmd_decide(a) -> int:
    c = Ctx(a)
    _record_decision(c, a.text[:TEXT_MAX])
    return OK


def cmd_reopen(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    state = "integrated" if st.get("integrated") else st["state"]
    if state not in ("rejected", "failed", "fixing"):
        raise CliError(FAIL, "task %s is %s and cannot be reopened" % (a.task, state))
    reason = a.reason[:TEXT_MAX]
    _emit_outcome(c, a.task, "rejection_action", "reopen", "lead")
    c.emit("reopened", a.task, None, st["attempt"], reason=reason)
    _record_decision(c, "Reopened %s: %s" % (a.task, reason))
    return OK


def _dispatch_state(c: Ctx) -> dict:
    state = c.state()
    spawned = {}
    released = set()
    breaches = []
    release_counts = {}
    last_spawned_kind = {}
    for event in E.read_events(c.events_path):
        if event.get("type") == "spawned":
            key = (event.get("task_id"), event.get("assignment_kind"),
                   event.get("trigger_instance", event.get("trigger", "ready")))
            spawned[key] = {"task_id": key[0], "kind": key[1], "trigger_instance": key[2]}
            last_spawned_kind[key[0]] = key[1]
        elif event.get("type") == "released" and event.get("spawn_key"):
            released.add(tuple(event["spawn_key"]))
            task_id, kind = event["spawn_key"][:2]
            if kind == "executor":
                release_counts[task_id] = release_counts.get(task_id, 0) + 1
        elif event.get("type") == "released":
            task_id = event.get("task_id")
            if last_spawned_kind.get(task_id) == "executor":
                release_counts[task_id] = release_counts.get(task_id, 0) + 1
        elif event.get("type") == "breach":
            breaches.append(event)
    for task_id, count in release_counts.items():
        if task_id in state["tasks"]:
            state["tasks"][task_id]["release_counts"] = {"executor": count}
    state["spawned"] = [value for key, value in spawned.items() if key not in released]
    state["breaches"] = breaches
    return state


def _latest_spawn(c: Ctx, task_id: str) -> Optional[dict]:
    latest = None
    for event in E.read_events(c.events_path):
        if event.get("type") == "spawned" and event.get("task_id") == task_id:
            latest = event
    if latest is None:
        label = c.labels.get(task_id, {})
        parent = label.get("fixes")
        if parent:
            return _latest_spawn(c, parent)
    return latest


def _task_project_root(c: Ctx, task_id: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    event = _latest_spawn(c, task_id)
    return event.get("worktree") if event and event.get("worktree") else os.getcwd()


def _dispatch_assignment(label: dict, due: dict) -> dict:
    for assignment in label.get("assignments", []):
        if assignment.get("kind") == due["kind"] and assignment.get("trigger", "ready") == due["trigger"]:
            result = dict(assignment)
            result.update({"model_tier": due["model_tier"], "executor": due["executor"], "model": due["model"],
                           "roster": due.get("roster", "roster.json")})
            return result
    return {"kind": due["kind"], "role": due["role"], "model_tier": due["model_tier"],
            "executor": due["executor"], "model": due["model"], "trigger": due["trigger"]}


def _make_dispatch_request(c: Ctx, due: dict, project_cwd: str, n: int) -> dict:
    from .dispatch import spawn_request, worktree_plan

    label = c.labels[due["task_id"]]
    ref = (label.get("routing") or {}).get("agent") or {}
    agent_catalog = AC.load_catalog(_agent_roots())
    if ref.get("variant") and ref.get("key") and ref.get("path"):
        agent_catalog[ref["key"]] = _load_variant_agent(ref["key"], ref["path"])
    routed_agent = agent_catalog.get(ref.get("key"))
    assignment = _dispatch_assignment(label, due)
    assignment["roster"] = c.roster_path if hasattr(c, "roster_path") else _resolve_roster(argparse.Namespace())
    plan = worktree_plan(label, c.run_dir, c.run_id)
    request_cwd = project_cwd
    if plan:
        plan["base"] = (label.get("context", {}).get("worktree") or {}).get("base") or "HEAD"
        request_cwd = plan["path"]
    request = spawn_request(label, assignment, c.run_dir, c.run_id, n=n,
                            cwd=request_cwd, worktree=plan, agent=routed_agent)
    request["executor"] = due["executor"]
    request["model"] = due["model"]
    request["trigger_instance"] = due["trigger_instance"]
    request["env"]["ALE_ROSTER"] = c.roster_path if hasattr(c, "roster_path") else "roster.json"
    if due["kind"] == "monitor":
        request["env"].pop("ALE_TASK", None)
        request["env"]["ALE_READ_ONLY"] = "1"
        breach = next((event for event in reversed(E.read_events(c.events_path))
                       if event.get("type") == "breach" and event.get("task_id") == due["task_id"]), None)
        if breach:
            breach = dict(breach)
            breach["last_heartbeat_step"] = c.task(due["task_id"]).get("last_step")
        request["breach"] = breach
        request["prompt_file"] = render_prompt(
            label, dict(assignment, breach=breach, handoff_path=request["handoff_path"], cwd=request["cwd"]), routed_agent)
    return request


def _create_worktree(plan: dict, project_cwd: str) -> None:
    base = plan.get("base") or "HEAD"
    path = os.path.abspath(plan["path"])
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=project_cwd,
                            capture_output=True, text=True)
    if listed.returncode != 0:
        message = (listed.stderr or listed.stdout).strip()
        raise CliError(FAIL, message or "git worktree list failed")
    registered = {}
    current_path = None
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = os.path.realpath(line[9:])
            registered[current_path] = None
        elif line.startswith("branch ") and current_path is not None:
            registered[current_path] = line[7:].removeprefix("refs/heads/")
    real_path = os.path.realpath(path)
    if os.path.isdir(path) and real_path in registered:
        if registered[real_path] != plan["branch"]:
            raise CliError(FAIL, "worktree %s is registered on branch %s" %
                           (path, registered[real_path] or "detached"))
        return
    if os.path.isdir(path):
        subprocess.run(["git", "worktree", "prune"], cwd=project_cwd, check=True,
                       capture_output=True, text=True)
    elif real_path in registered:
        subprocess.run(["git", "worktree", "prune"], cwd=project_cwd, check=True,
                       capture_output=True, text=True)
    branch = subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/" + plan["branch"]],
                            cwd=project_cwd)
    command = (["git", "worktree", "add", path, plan["branch"]] if branch.returncode == 0
               else ["git", "worktree", "add", path, "-b", plan["branch"], base])
    proc = subprocess.run(command, cwd=project_cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip()
        raise CliError(FAIL, message or "git worktree add failed")


def _run_worktree_setup(c: Ctx, label: dict, worktree: str, project_cwd: str) -> None:
    marker = os.path.join(worktree, ".ale-setup-done")
    if os.path.exists(marker):
        return
    worktree_config = (label.get("context", {}).get("worktree") or {})
    commands = worktree_config.get("setup")
    if commands is None:
        commands = c.roster.get("worktree_setup_defaults", [])
    for command in commands or []:
        try:
            proc = subprocess.run(command, cwd=worktree, env=dict(os.environ, ALE_WORKTREE=worktree,
                                    ALE_CHECKOUT=project_cwd), shell=True, text=True,
                                  capture_output=True, timeout=600)
        except subprocess.TimeoutExpired as exc:
            proc = exc
            code = "timeout"
        else:
            code = proc.returncode
        if isinstance(proc, subprocess.TimeoutExpired) or proc.returncode != 0:
            detail = "command=%s exit_code=%s" % (command, code)
            c.emit("breach", label["task_id"], None, c.state()["tasks"][label["task_id"]]["attempt"],
                   breach="worktree_setup_failed", detail=detail)
            raise CliError(FAIL, "worktree setup failed: %s (exit code %s)" % (command, code))
    H.write_atomic(marker, "completed\n")


def _write_spawn_request(c: Ctx, request: dict) -> str:
    prompt = request.get("prompt_file", "")
    prompt_path = os.path.join(c.run_dir, "prompts", "%s.md" % request["agent_id"])
    H.write_atomic(prompt_path, prompt)
    request["prompt_file"] = prompt_path
    request["prompt_file_is_path"] = True
    path = os.path.join(c.run_dir, "requests", "%s.json" % request["agent_id"])
    H.write_atomic(path, json.dumps(request, sort_keys=True))
    return path


def _dispatch_request_json(request: dict) -> str:
    printable = dict(request)
    if "prompt_file" in printable and not printable.get("prompt_file_is_path"):
        printable["prompt"] = printable.pop("prompt_file")
    return json.dumps(printable, sort_keys=True)


def _append_spawned(c: Ctx, due: dict, request: dict, plan: Optional[dict]) -> None:
    extra = {"agent_id_minted": request["agent_id"], "assignment_kind": due["kind"],
             "executor": due["executor"], "model": due["model"],
             "trigger_instance": due["trigger_instance"]}
    if plan:
        extra.update({"worktree": plan["path"], "branch": plan["branch"]})
    c.emit("spawned", due["task_id"], None, c.state()["tasks"][due["task_id"]]["attempt"],
           pane=request.get("executor_pane"), **extra)


def cmd_dispatch(a) -> int:
    import fcntl
    from .dispatch import due_assignments, held_for_integration, worktree_plan

    c = Ctx(a)
    c.roster_path = _resolve_roster(a)
    # Executor routing is deterministic from tier and is never judged.
    shadow_judge = (_shadow_judge(c.roster)
                    if any(event.get("type") == "run_started" for event in E.read_events(c.events_path))
                    else None)
    project_cwd = os.path.abspath(a.cwd or os.getcwd())
    lock_path = os.path.join(c.run_dir, "dispatch.lock")
    requests = []
    spawned_requests = []
    try:
        lock = open(lock_path, "a+")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            dispatch_state = _dispatch_state(c)
            held = held_for_integration(dispatch_state, c.labels)
            for task_id, dependency_id in held:
                print("holding %s: dependency %s is accepted but not integrated" %
                      (task_id, dependency_id), file=sys.stderr)
            due = due_assignments(dispatch_state, c.labels, c.roster)
            for index, item in enumerate(due, 1):
                request = _make_dispatch_request(c, item, project_cwd, index)
                requests.append(request)
                if a.json or a.dry_run:
                    continue
                if not a.spawn and not a.no_exec:
                    continue
                if item["executor"] == "claude-subagent" and not a.no_exec:
                    label = c.labels[item["task_id"]]
                    plan = worktree_plan(label, c.run_dir, c.run_id)
                    if plan:
                        plan["base"] = (label.get("context", {}).get("worktree") or {}).get("base") or "HEAD"
                        parent_spawn = _latest_spawn(c, item["task_id"])
                        reuses_parent = bool(label.get("fixes") and parent_spawn and parent_spawn.get("worktree"))
                        if not reuses_parent:
                            _create_worktree(plan, project_cwd)
                        _run_worktree_setup(c, label, plan["path"], project_cwd)
                    continue
                label = c.labels[item["task_id"]]
                plan = worktree_plan(label, c.run_dir, c.run_id)
                if plan:
                    plan["base"] = (label.get("context", {}).get("worktree") or {}).get("base") or "HEAD"
                    parent_spawn = _latest_spawn(c, item["task_id"])
                    reuses_parent = bool(label.get("fixes") and parent_spawn and parent_spawn.get("worktree"))
                    if not reuses_parent:
                        _create_worktree(plan, project_cwd)
                    _run_worktree_setup(c, label, plan["path"], project_cwd)
                if a.no_exec:
                    _append_spawned(c, item, request, plan)
                    continue
                _append_spawned(c, item, request, plan)
                request_path = _write_spawn_request(c, request)
                spawned_requests.append((item, request, request_path))
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
    except CliError:
        raise
    if a.json or a.dry_run or not a.spawn:
        for request in requests:
            print(_dispatch_request_json(request))
        return OK
    for item, request, request_path in spawned_requests:
        spawn_bin = os.environ.get("ALE_SPAWN_BIN") or os.path.join(plugin_root(), "bin", "ale-spawn")
        before = _monitor_worktree_snapshot(request["cwd"]) if item["kind"] == "monitor" else None
        proc = subprocess.run([spawn_bin, request_path],
                              cwd=request["cwd"], text=True, capture_output=True,
                              env=os.environ.copy())
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            c.emit("released", item["task_id"], None,
                   c.state()["tasks"][item["task_id"]]["attempt"],
                   reason="spawn failed: %s" % (proc.stderr.strip() or proc.returncode),
                   spawn_key=[item["task_id"], item["kind"], item["trigger_instance"]])
        if item["kind"] == "monitor":
            after = _monitor_worktree_snapshot(request["cwd"])
            wrote_files = _monitor_worktree_changes(before, after)
            if wrote_files:
                _revert_monitor_worktree_changes(request["cwd"], wrote_files, after)
            verdict = _extract_monitor_verdict(proc.stdout or "")
            if wrote_files:
                verdict = "escalate"
                verdict_text = "monitor wrote files: %s" % ", ".join(wrote_files)
            else:
                verdict_text = (proc.stdout or "")[:1500]
            if verdict:
                c.emit("monitor_verdict", item["task_id"], None,
                       c.state()["tasks"][item["task_id"]]["attempt"],
                       agent_id_minted=request["agent_id"], verdict=verdict,
                       text=verdict_text, **({"wrote_files": wrote_files} if wrote_files else {}))
                if shadow_judge is not None:
                    _emit_vote(c, item["task_id"], _monitor_vote(c, shadow_judge, item["task_id"],
                                                                 proc.stdout or "", wrote_files), "monitor")
                    _emit_outcome(c, item["task_id"], "monitor_verdict", verdict, "monitor")
    for request in requests:
        if request.get("executor") == "claude-subagent":
            print(json.dumps(request, sort_keys=True))
    return OK


def _extract_monitor_verdict(text: str) -> Optional[str]:
    import re

    verdict_line = re.compile(r"(?i)^(?:\*\*)?(?:verdict:[ \t]*)?(?:\*\*)?(continue|nudge|fix|escalate)(?:\*\*)?(?:\b|$)")
    heading = re.compile(r"(?i)^##[ \t]+verdict[ \t]*#*[ \t]*$")
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        candidate = line.strip()
        if heading.match(candidate):
            following = next(((line_index, value.strip()) for line_index, value in enumerate(lines[index + 1:], index + 1)
                              if value.strip()), None)
            if following:
                match = verdict_line.match(following[1])
                if match:
                    found.append((following[0], match.group(1).lower()))
            continue
        match = verdict_line.match(candidate)
        if match and (candidate.lower().startswith("verdict:") or candidate.lower().startswith("**")
                      or candidate.lower() in ("continue", "nudge", "fix", "escalate")):
            found.append((index, match.group(1).lower()))
    return max(found, default=(None, None), key=lambda item: item[0])[1]


def _monitor_worktree_snapshot(worktree: str) -> Optional[dict]:
    proc = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                          cwd=worktree, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    records = proc.stdout.split("\0")
    snapshot = {}
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status, path = record[:2], record[3:]
        full_path = os.path.join(worktree, path)
        fingerprint = None
        try:
            info = os.lstat(full_path)
            if os.path.islink(full_path):
                fingerprint = "link:" + os.readlink(full_path)
            elif os.path.isfile(full_path):
                digest = hashlib.sha256()
                with open(full_path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
                fingerprint = "file:" + digest.hexdigest()
            else:
                fingerprint = "other:%s:%s" % (info.st_mode, info.st_mtime_ns)
        except OSError:
            fingerprint = "missing"
        snapshot[path] = (status, fingerprint)
        if "R" in status or "C" in status:
            if index < len(records):
                snapshot[records[index]] = (status, None)
                index += 1
    return snapshot


def _monitor_worktree_changes(before: Optional[dict], after: Optional[dict]) -> List[str]:
    if before is None or after is None:
        return []
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _revert_monitor_worktree_changes(worktree: str, paths: List[str], snapshot: Optional[dict]) -> None:
    tracked = []
    untracked = []
    for path in paths:
        status = (snapshot or {}).get(path, ("??", None))[0]
        pathspec = ":(literal)" + path
        if status == "??":
            untracked.append(pathspec)
        else:
            tracked.append(pathspec)
    if tracked:
        subprocess.run(["git", "checkout", "--"] + tracked, cwd=worktree,
                       capture_output=True, text=True)
    if untracked:
        subprocess.run(["git", "clean", "-fd", "--"] + untracked, cwd=worktree,
                       capture_output=True, text=True)


def cmd_integrate(a) -> int:
    c = Ctx(a)
    if c.labels.get(a.task, {}).get("fixes"):
        raise CliError(FAIL, "cannot integrate a fix task; integrate its parent task")
    state = c.task(a.task)
    if state.get("integrated"):
        integrated_event = next((item for item in reversed(E.read_events(c.events_path))
                                 if item.get("type") == "integrated" and item.get("task_id") == a.task), {})
        commit = integrated_event.get("commit", "")
        raise CliError(FAIL, "task %s is already integrated (%s)" %
                       (a.task, commit[:7] if commit else "unknown commit"))
    if state.get("state") != "accepted":
        raise CliError(FAIL, "task %s is %s, not accepted" % (a.task, state.get("state")))
    event = _latest_spawn(c, a.task)
    if event is None or not event.get("branch"):
        raise CliError(FAIL, "task %s has no recorded branch" % a.task)
    worktree = event.get("worktree")
    if not worktree:
        raise CliError(FAIL, "task %s has no recorded worktree" % a.task)
    if not os.path.isdir(worktree):
        raise CliError(FAIL, "task %s worktree does not exist: %s" % (a.task, worktree))
    status = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                            cwd=worktree, capture_output=True)
    if status.returncode != 0:
        raise CliError(FAIL, "cannot inspect task worktree")
    changed = []
    worktree_config = c.labels[a.task].get("context", {}).get("worktree") or {}
    setup_outputs = worktree_config.get("setup_outputs", [])
    records = status.stdout.decode("utf-8", "replace").split("\0")
    for record in records:
        if not record:
            continue
        path = record[3:] if len(record) >= 4 else ""
        if (not path or "__pycache__" in path or path.endswith(".pyc")
                or path == ".ale-setup-done"
                or any(fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("/") + "/")
                       for pattern in setup_outputs)):
            continue
        if path not in changed:
            changed.append(path)
    outside = V.paths_within(changed, c.labels[a.task].get("context", {}).get("allowed_paths", []))
    if outside:
        raise CliError(FAIL, "changed path outside allowed_paths: %s" % outside[0])
    if changed:
        added = subprocess.run(["git", "add", "--"] + changed, cwd=worktree,
                               capture_output=True, text=True)
        if added.returncode != 0:
            raise CliError(FAIL, added.stderr.strip() or "git add failed")
        identity_name = subprocess.run(["git", "config", "--get", "user.name"], cwd=worktree,
                                       capture_output=True, text=True)
        identity_email = subprocess.run(["git", "config", "--get", "user.email"], cwd=worktree,
                                        capture_output=True, text=True)
        commit_command = ["git"]
        if not identity_name.stdout.strip():
            commit_command += ["-c", "user.name=ale"]
        if not identity_email.stdout.strip():
            commit_command += ["-c", "user.email=ale@localhost"]
        commit_command += ["commit", "-m", "ale: %s %s" % (a.task, c.labels[a.task].get("title", a.task))]
        committed = subprocess.run(commit_command, cwd=worktree, capture_output=True, text=True)
        if committed.returncode != 0:
            raise CliError(FAIL, committed.stderr.strip() or "git commit failed")
    checkout = os.path.abspath(a.cwd or os.getcwd())
    base_status = subprocess.run(["git", "status", "--porcelain"], cwd=checkout,
                                 capture_output=True, text=True)
    if base_status.returncode != 0:
        raise CliError(FAIL, base_status.stderr.strip() or "git status failed")
    if base_status.stdout.strip():
        paths = [record[3:] for record in base_status.stdout.splitlines() if len(record) >= 4]
        listed = paths[:5]
        message = "checkout %s has uncommitted changes: %s" % (checkout, ", ".join(listed))
        if len(paths) > 5:
            message += " (and %d more)" % (len(paths) - 5)
        raise CliError(FAIL, message)
    merge = subprocess.run(["git", "merge", "--no-ff", "--no-edit", event["branch"]],
                           cwd=checkout, capture_output=True, text=True)
    if merge.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=checkout, capture_output=True, text=True)
        message = (merge.stderr or merge.stdout).strip()
        if message:
            print(message, file=sys.stderr)
        return FAIL
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    c.emit("integrated", a.task, None, state.get("attempt", 1), commit=commit, files=changed)
    removed = subprocess.run(["git", "worktree", "remove", "--force", worktree],
                             cwd=checkout, capture_output=True, text=True)
    if removed.returncode != 0:
        print(removed.stderr.strip() or "git worktree remove failed", file=sys.stderr)
    deleted = subprocess.run(["git", "branch", "-d", event["branch"]], cwd=checkout,
                             capture_output=True, text=True)
    if deleted.returncode != 0:
        print(deleted.stderr.strip() or "git branch delete failed", file=sys.stderr)
    return OK


def _failed_acceptance(label: dict, evidence: dict) -> List[dict]:
    failed = {item.get("id") for item in evidence.get("results", []) if not item.get("ok")}
    return [item for item in label.get("acceptance", []) if item.get("id") in failed]


def cmd_fix(a) -> int:
    c = Ctx(a)
    parent = c.task(a.task)
    existing = sorted(tid for tid, label in c.labels.items() if label.get("fixes") == a.task)
    rejected_child = any(c.state()["tasks"].get(tid, {}).get("state") == "rejected" for tid in existing)
    if parent.get("state") != "rejected" and not (parent.get("state") == "fixing" and rejected_child):
        raise CliError(FAIL, "task %s is %s, not rejected" % (a.task, parent.get("state")))
    if c.labels[a.task].get("fixes"):
        c.emit("breach", a.task, None, parent.get("attempt"), breach="attempts_exhausted",
               detail="fix tasks cannot create another fix task")
        _emit_outcome(c, a.task, "rejection_action", "escalate", "run_loop")
        raise CliError(BREACH, "attempts_exhausted")
    if len(existing) >= 2:
        c.emit("breach", a.task, None, parent.get("attempt"), breach="attempts_exhausted", detail="two fix tasks already exist")
        _emit_outcome(c, a.task, "rejection_action", "escalate", "run_loop")
        raise CliError(BREACH, "attempts_exhausted")
    failed = _failed_acceptance(c.labels[a.task], parent.get("evidence") or {})
    if not failed:
        raise CliError(FAIL, "task %s has no failed acceptance entries" % a.task)
    fix = D.fix_label(c.labels[a.task], failed, len(existing) + 1)
    path = _label_path(c.run_dir, fix["task_id"])
    H.write_atomic(path, json.dumps(fix, indent=2, sort_keys=True))
    c.emit("task_added", fix["task_id"], None, 1, label_file=os.path.basename(path),
           reason=(a.reason or "acceptance failure")[:TEXT_MAX])
    _emit_outcome(c, a.task, "rejection_action", "fix", "run_loop")
    return OK


def cmd_remove(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    if st.get("state") not in ("planned", "ready", "released", "rejected"):
        raise CliError(FAIL, "task %s is %s and cannot be removed" % (a.task, st.get("state")))
    c.emit("label_removed", a.task, None, st.get("attempt"), reason=a.reason[:TEXT_MAX])
    return OK


def cmd_paths_within(a) -> int:
    c = Ctx(a, need_roster=False)
    if a.task_id not in c.labels:
        raise CliError(FAIL, "unknown task %s" % a.task_id)
    changed = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    bad = V.paths_within(changed, c.labels[a.task_id]["context"]["allowed_paths"])
    for p in bad:
        print(p)
    return FAIL if bad else OK


def cmd_doctor(a) -> int:
    print("roster: %s" % _resolve_roster(a))
    print("run_dir: %s" % _resolve_run_dir(a))
    c = Ctx(a)
    problems: List[str] = []
    if os.path.exists(c.events_path):
        with open(c.events_path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    errs = E.check_event(json.loads(line))
                except ValueError:
                    errs = ["not JSON"]
                problems.extend("events.jsonl:%d: %s" % (n, e) for e in errs)
    if not problems:
        for tid, st in sorted(c.state()["tasks"].items()):
            if st["state"] not in E.LIVE:
                continue
            cap = L.effective_watch(c.labels[tid], c.roster)["max_duration_s"]
            if c.now - st["started_ts"] > cap and ["overrun", st["attempt"]] not in st["breaches_seen"]:
                problems.append("%s: live past max_duration_s with no overrun breach recorded. Is the watchdog running?" % tid)
        labeled_ids = set()
        if os.path.exists(c.events_path):
            for ev in E.read_events(c.events_path):
                if ev.get("type") == "labeled" and ev.get("task_id"):
                    labeled_ids.add(ev["task_id"])
        for tid in sorted(labeled_ids):
            if tid not in c.labels:
                problems.append("%s: labeled in the log but its label file is missing" % tid)
    failure_path = os.path.join(c.run_dir, "herdr-failures.count")
    try:
        with open(failure_path, encoding="utf-8") as f:
            failure_count = int(f.read().strip() or "0")
    except (OSError, ValueError):
        failure_count = 0
    if failure_count:
        problems.append("herdr metadata failures: %d" % failure_count)
    for p in problems:
        print(p, file=sys.stderr)
    return FAIL if problems else OK


def cmd_timeline(a) -> int:
    c = Ctx(a)
    events = E.read_events(c.events_path)
    if a.task:
        events = [event for event in events if event.get("task_id") == a.task]
    rows = TL.timeline(events, c.labels)
    if getattr(a, "tail", None):
        rows = rows[-a.tail:]
    if a.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        for line in TL.format_timeline(rows):
            print(line)
    return OK


def _meta_prices(roster: dict) -> Optional[dict]:
    prices = roster.get("prices") if isinstance(roster, dict) else None
    return prices if isinstance(prices, dict) else None


def _meta_csv(meta: dict) -> None:
    fields = ["kind", "id", "input", "output", "cache_read", "cache_write",
              "wall_seconds", "attempts", "breaches", "model", "executor",
              "files_touched", "cost", "accepted_first_verify_count",
              "accepted_first_verify_rate", "fix_tasks",
              "billable_tokens", "wall_seconds_median", "rewrite_candidate"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for kind, section in (("task", "tasks"), ("agent_instance", "agent_instances")):
        for ident, summary in sorted(meta.get(section, {}).items()):
            row = {"kind": kind, "id": ident}
            row.update({key: summary.get(key) for key in fields if key in summary})
            row["wall_seconds"] = json.dumps(summary.get("wall_seconds", {}), sort_keys=True)
            row["breaches"] = json.dumps(summary.get("breaches", []), sort_keys=True)
            row["files_touched"] = json.dumps(summary.get("files_touched", []), sort_keys=True)
            row["input"] = summary["tokens"]["input"]
            row["output"] = summary["tokens"]["output"]
            row["cache_read"] = summary["tokens"]["cache_read"]
            row["cache_write"] = summary["tokens"]["cache_write"]
            writer.writerow(row)
    for ident, summary in sorted(meta.get("agents", {}).items()):
        row = {"kind": "agent", "id": ident}
        row.update({key: summary.get(key) for key in fields if key in summary})
        row["accepted_first_verify_count"] = summary["accepted_first_verify"]["count"]
        row["accepted_first_verify_rate"] = summary["accepted_first_verify"]["rate"]
        writer.writerow(row)
    summary = meta["totals"]
    row = {"kind": "total", "id": "totals"}
    row["input"] = summary["tokens"]["input"]
    row["output"] = summary["tokens"]["output"]
    row["cache_read"] = summary["tokens"]["cache_read"]
    row["cache_write"] = summary["tokens"]["cache_write"]
    row["cost"] = summary.get("cost")
    writer.writerow(row)


def cmd_meta(a) -> int:
    c = Ctx(a)
    meta = TL.task_metadata(E.read_events(c.events_path), c.labels, c.roster,
                            _meta_prices(c.roster))
    meta["agent_instances"] = meta.pop("agents")
    meta["agents"] = TL.agent_metadata(E.read_events(c.events_path), c.labels)
    if a.csv:
        _meta_csv(meta)
    elif a.json:
        print(json.dumps(meta, sort_keys=True))
    else:
        print(json.dumps(meta, sort_keys=True, indent=2))
    return OK


def _binding_home(a) -> str:
    return a.home or os.environ.get("ALE_HOME") or os.path.expanduser("~")


def cmd_bind(a) -> int:
    c = Ctx(a)
    c.task(a.task)
    path = B.binding_path(_binding_home(a), a.session, a.subagent)
    roster = _resolve_roster(a)
    value = {"run_dir": c.run_dir, "roster": roster, "task_id": a.task,
             "agent_id": a.agent, "source": "file"}
    H.write_atomic(path, json.dumps(value, sort_keys=True))
    return OK


def cmd_unbind(a) -> int:
    path = B.binding_path(_binding_home(a), a.session, a.subagent)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    return OK


def _resolve_now(a: argparse.Namespace) -> float:
    raw_now = a.now if a.now is not None else os.environ.get("ALE_NOW")
    if raw_now in (None, ""):
        return time.time()
    try:
        return float(raw_now)
    except (TypeError, ValueError):
        raise CliError(USAGE, "--now must be a number, got %r" % (raw_now,))


def cmd_label(a) -> int:
    run_dir = _resolve_run_dir(a)
    roster = R.load_roster(_resolve_roster(a))
    now = _resolve_now(a)

    events_path = os.path.join(run_dir, "events.jsonl")
    if any(e["type"] == "run_started" for e in E.read_events(events_path)):
        raise CliError(FAIL, "run already started: labels are frozen, use `ale relabel`")

    drafts: "dict[str, dict]" = {}
    errs: List[str] = []
    for path in sorted(glob.glob(os.path.join(run_dir, "drafts", "*.json"))):
        with open(path, encoding="utf-8") as f:
            draft = json.load(f)
        tid = draft.get("task_id", os.path.basename(path))
        if not H.is_safe_id(tid):
            errs.append("%s: unsafe task_id %r" % (path, tid))
            continue
        drafts[tid] = draft

    for tid in sorted(drafts):
        errs.extend(L.check_label(drafts[tid], roster))
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return FAIL

    mode = "off" if a.no_judge else _judge_mode(roster)
    # legacy keeps the pre-Part-B cascade judge; only shadow mode records decision votes.
    judge = _make_judge(roster) if mode in ("legacy", "shadow") else None
    collect = mode == "shadow"

    total_votes = 0
    judge_abstains = 0
    conflicts = 0
    disagreements: List[dict] = []

    for tid in sorted(drafts):
        draft = drafts[tid]
        spec_path = draft["context"]["spec_path"]
        text, warning = CAS.read_spec_text(spec_path, [os.getcwd(), run_dir])
        if warning:
            print(warning, file=sys.stderr)
        final, votes = CAS.label_task(draft, text, roster, judge=judge)
        H.write_atomic(os.path.join(run_dir, "labels", "%s.json" % tid),
                       json.dumps(final, indent=2, sort_keys=True))
        label_ctx = Ctx(a)
        for v in votes:
            extra = {"field": v["field"], "by": v["by"], "value": v["value"], "confidence": v["confidence"]}
            err = (v.get("detail") or {}).get("error")
            if err:
                extra["error"] = str(err)[:200]
            label_ctx.emit("label_vote", tid, None, 1, **extra)
            total_votes += 1
            if v["by"].startswith("judge:"):
                if v["value"] is None:
                    judge_abstains += 1
                elif v["value"] != draft["labels"].get(
                        v["field"], "any" if v["field"] == "locality" else None):
                    disagreements.append({"task": tid, "field": v["field"],
                                           "planner": draft["labels"].get(
                                               v["field"], "any" if v["field"] == "locality" else None),
                                           "judge": v["value"]})
        for field in CAS.FIELDS:
            if final["provenance"][field]["conflict"]:
                conflicts += 1
        if collect:
            shadow_votes = _cascade_votes(roster, votes) + _bake_extra_votes(judge, roster, final, text)
            for vote in shadow_votes:
                _emit_vote(label_ctx, tid, vote, "label", attempt=1)
            outcomes = _bake_outcomes(final)
            for vote in shadow_votes:
                if vote["decision"] in outcomes:
                    _emit_outcome(label_ctx, tid, vote["decision"], outcomes[vote["decision"]], "planner")

    print(json.dumps({"tasks": len(drafts), "votes": total_votes, "judge_abstains": judge_abstains,
                       "conflicts": conflicts, "disagreements": disagreements}, sort_keys=True))
    return OK


def _vocab_value(roster: dict, field: str, value: str) -> None:
    if field == "lane" or field not in CAS.FIELDS:
        raise CliError(USAGE, "field %s cannot be relabeled or adjudicated" % field)
    if value not in roster["vocab"][field]:
        raise CliError(FAIL, "%s=%r is not in the roster vocabulary" % (field, value))


def _label_path(run_dir: str, task_id: str) -> str:
    return os.path.join(run_dir, "labels", "%s.json" % task_id)


def cmd_relabel(a) -> int:
    if a.field == "lane" or (a.field not in CAS.FIELDS and a.field not in ("assignments", "sub", "phase", "acceptance")):
        raise CliError(USAGE, "field %s cannot be relabeled or adjudicated" % a.field)
    c = Ctx(a)
    st = c.task(a.task)
    if st["state"] in E.TERMINAL:
        raise CliError(FAIL, "task %s is terminal: %s" % (a.task, st["state"]))

    label = dict(c.labels[a.task])
    if a.field == "assignments":
        if not a.json:
            raise CliError(USAGE, "--field assignments requires --json")
        try:
            new_value = json.loads(a.value)
        except ValueError as exc:
            raise CliError(USAGE, "assignments must be JSON: %s" % exc)
        if not isinstance(new_value, list) or sum(1 for item in new_value
                                                 if isinstance(item, dict) and item.get("kind") == "executor") != 1:
            raise CliError(FAIL, "assignments must contain exactly one executor")
        old = label.get("assignments", [])
        label["assignments"] = new_value
    elif a.field == "acceptance":
        if not a.json:
            raise CliError(USAGE, "--field acceptance requires --json")
        try:
            new_value = json.loads(a.value)
        except ValueError as exc:
            raise CliError(USAGE, "acceptance must be JSON: %s" % exc)
        if not isinstance(new_value, list) or not 2 <= len(new_value) <= 5:
            raise CliError(FAIL, "acceptance must contain 2 to 5 checks")
        for item in new_value:
            if not isinstance(item, dict):
                raise CliError(FAIL, "acceptance checks must be objects")
            is_command = (set(item) == {"id", "cmd", "expect"} and isinstance(item.get("cmd"), str)
                          and bool(item["cmd"]) and isinstance(item.get("expect"), str)
                          and re.match(r"^exit(0|:[0-9]{1,3})$", item["expect"]))
            is_manual = (set(item) == {"id", "manual"} and isinstance(item.get("manual"), str)
                         and len(item["manual"]) >= 5)
            if not (is_command or is_manual) or not isinstance(item.get("id"), str) or not re.match(r"^A[0-9]+$", item["id"]):
                raise CliError(FAIL, "invalid acceptance check")
        old = label.get("acceptance", [])
        label["acceptance"] = new_value
    else:
        _vocab_value(c.roster, a.field, a.value)
        labels = dict(label["labels"])
        old = labels[a.field]
        labels[a.field] = a.value
        label["labels"] = labels
        if a.field in ("sub", "phase"):
            catalog = AC.load_catalog(_agent_roots())
            ref = AC.resolve_agent(catalog, labels.get("role", "general"), labels.get("sub"), labels.get("phase") or "implement")
            if ref.get("matched") == "general" and labels.get("role") in ("frontend", "backend", "devops") and labels.get("sub"):
                raise CliError(FAIL, "no specialized agent resolves for %s/%s" % (labels.get("role"), labels.get("sub")))
            label["routing"] = dict(label.get("routing") or {})
            label["routing"]["agent"] = {key: ref[key] for key in ("key", "path", "name", "sha256", "version", "matched")}
            agent = catalog.get(ref["key"])
            label["effective_rules"] = effective_rules(label, agent)
    provenance = dict(label.get("provenance") or {})
    field_provenance = provenance.get(a.field)
    if not isinstance(field_provenance, dict):
        field_provenance = {}
    else:
        field_provenance = dict(field_provenance)
    field_provenance["relabeled_from"] = old
    provenance[a.field] = field_provenance
    label["provenance"] = provenance

    routing_old = (c.labels[a.task].get("routing") or {}).get("agent")
    routing_new = (label.get("routing") or {}).get("agent")
    H.write_atomic(_label_path(c.run_dir, a.task), json.dumps(label, indent=2, sort_keys=True))
    c.emit("label_changed", a.task, None, st["attempt"],
           field=a.field if a.field in ("assignments", "acceptance") else "labels.%s" % a.field,
           old=old, new=label.get(a.field) if a.field in ("assignments", "acceptance") else a.value,
           routing_agent_old=routing_old, routing_agent_new=routing_new, reason=a.reason[:TEXT_MAX])
    c.emit("relabeled", a.task, None, st["attempt"], field=a.field, old=old,
           new=label.get(a.field) if a.field in ("assignments", "acceptance") else a.value,
           reason=a.reason[:TEXT_MAX])
    return OK


def cmd_agents_list(a) -> int:
    roots = _agent_roots()
    catalog = AC.load_catalog(roots)
    rows = []
    for key, agent in sorted(catalog.items()):
        source_root = next((root for root in roots if os.path.commonpath(
            [os.path.realpath(root), os.path.realpath(agent["path"])]) == os.path.realpath(root)), "")
        rows.append({"key": key, "name": agent["name"], "version": agent["version"],
                     "source_root": source_root, "sha8": agent["sha256"][:8]})
    if a.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        for row in rows:
            print("{key} {name} v{version} {sha8} [{source_root}]".format(**row))
    return OK


def cmd_agents_show(a) -> int:
    agent = AC.load_catalog(_agent_roots()).get(a.key)
    if agent is None:
        raise CliError(FAIL, "unknown agent %s" % a.key)
    frontmatter = {key: value for key, value in agent.items()
                   if key not in ("path", "sha256", "core", "harness", "key")}
    print("file: %s" % agent["path"])
    print("sha256: %s" % agent["sha256"])
    print("frontmatter:")
    print(json.dumps(frontmatter, sort_keys=True, indent=2))
    print("core:")
    print(agent["core"], end="" if agent["core"].endswith("\n") else "\n")
    return OK


def _read_spec_snippet(run_dir: str, label: dict) -> str:
    path = label["context"]["spec_path"]
    text, warning = CAS.read_spec_text(path, [os.getcwd(), run_dir], limit=300)
    if warning:
        print(warning, file=sys.stderr)
    return text


def _emit_adjudicated(c: Ctx, task_id: str, field: str, value: str, by: str) -> None:
    _vocab_value(c.roster, field, value)
    st = c.task(task_id)
    c.emit("adjudicated", task_id, None, st["attempt"], field=field, value=value, by=by)


def _interactive_adjudicate(c: Ctx) -> int:
    for item in TRUTH.adjudication_queue(E.read_events(c.events_path), c.labels):
        task_id = item["task_id"]
        label = c.labels[task_id]
        print("%s: %s" % (task_id, label["title"]))
        snippet = _read_spec_snippet(c.run_dir, label)
        if snippet:
            print(snippet)
        print("1 %s" % item["planner"])
        print("2 %s" % item["judge"])
        print("3 other value")
        print("s skip")
        print("q quit")
        choice = sys.stdin.readline()
        if choice == "":
            break
        choice = choice.strip()
        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "1":
            value = item["planner"]
        elif choice == "2":
            value = item["judge"]
        elif choice == "3":
            print("value:")
            value = sys.stdin.readline().strip()
            if value == "":
                break
        else:
            continue
        _emit_adjudicated(c, task_id, item["field"], value, "human")
    return OK


def cmd_adjudicate(a) -> int:
    if getattr(a, "decision", None) is not None:
        if a.decision not in DECISIONS.DECISIONS:
            raise CliError(USAGE, "unknown decision %s" % a.decision)
        if not DECISIONS.is_judged(a.decision):
            raise CliError(USAGE, "decision %s is deterministic and is not judged" % a.decision)
        if a.task is None or a.value is None:
            raise CliError(USAGE, "--task and --value are required with --decision")
        c = Ctx(a)
        st = c.task(a.task)
        already = [event for event in E.read_events(c.events_path)
                    if event.get("type") == "adjudicated"
                    and event.get("task_id") == a.task
                    and (event.get("decision") or event.get("field")) == a.decision]
        if already:
            raise CliError(FAIL, "decision %s for task %s was already adjudicated" % (a.decision, a.task))
        options = DECISIONS.options_for_decision(a.decision, c.roster, label=c.labels[a.task])
        if a.value not in options:
            raise CliError(FAIL, "%s=%r is not in the decision vocabulary" % (a.decision, a.value))
        c.emit("adjudicated", a.task, None, st["attempt"], field=a.decision,
               decision=a.decision, choice=a.value, value=a.value, by=a.by,
               authority="lead", additive=True)
        return OK
    if a.field is not None and (a.field == "lane" or a.field not in CAS.FIELDS):
        raise CliError(USAGE, "field %s cannot be relabeled or adjudicated" % a.field)
    c = Ctx(a)
    if a.list:
        print(json.dumps(TRUTH.adjudication_queue(E.read_events(c.events_path), c.labels), sort_keys=True))
        return OK
    if a.task is None and a.field is None and a.value is None:
        return _interactive_adjudicate(c)
    if a.task is None or a.field is None or a.value is None:
        raise CliError(USAGE, "--task, --field and --value are required unless --list is used")
    _emit_adjudicated(c, a.task, a.field, a.value, a.by)
    return OK


def cmd_judge_stats(a) -> int:
    """Print evidence only; this command never changes labels or promotion state.

    The run's events.jsonl is the single source: bake-time votes are imported
    into it by ``init-run --plan``, and every later firing site appends there.
    """
    run_dir = _resolve_run_dir(a)
    events = E.read_events(os.path.join(run_dir, "events.jsonl"))
    judged = set(DECISIONS.judged_decision_ids())
    votes = [event for event in events if event.get("type") == "shadow_vote" and event.get("decision") in judged]
    outcomes = [event for event in events
                if event.get("type") == "decision_outcome" and event.get("decision") in judged]
    adjudications = [event for event in events if event.get("type") == "adjudicated"
                     and (event.get("decision") or event.get("field")) in judged]
    accepted_tasks = {event.get("task_id") for event in events if event.get("type") == "accepted"}
    roster = R.load_roster(_resolve_roster(a))
    stats = summarize_shadow(votes, outcomes, adjudications, (roster.get("judge") or {}).get("bar", {}),
                             accepted_tasks)
    print(json.dumps(stats, sort_keys=True, indent=2))
    return OK


def _nearest_existing_parent(path: str) -> str:
    cur = os.path.realpath(os.path.abspath(path))
    while not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if os.path.isfile(cur):
        return os.path.dirname(cur)
    return cur


def _refuse_tracked_out_dir(out_dir: str) -> None:
    real_out = os.path.realpath(os.path.abspath(out_dir))
    cur = _nearest_existing_parent(real_out)
    repo_root = None
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            repo_root = cur
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if repo_root is None:
        return
    git_path = os.path.relpath(real_out, repo_root).rstrip(os.sep) + os.sep
    try:
        proc = subprocess.run(["git", "check-ignore", "-q", "--", git_path], cwd=repo_root, timeout=10)
    except Exception:
        proc = None
    if proc is not None and proc.returncode == 0:
        return
    raise CliError(FAIL, "output directory could not be confirmed git-ignored: %s" % out_dir)


def _read_jsonl(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise CliError(FAIL, "%s:%d: invalid JSON: %s" % (path, line_no, exc))
                if not isinstance(row, dict):
                    raise CliError(FAIL, "%s:%d: expected JSON object" % (path, line_no))
                rows.append(row)
    return rows


def _truncate_incomplete_jsonl_tail(path: str) -> None:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "rb+") as f:
        data = f.read()
        if data.endswith(b"\n"):
            return
        last_newline = data.rfind(b"\n")
        f.truncate(0 if last_newline < 0 else last_newline + 1)


def _write_jsonl(path: str, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def cmd_eval_corpus(a) -> int:
    _refuse_tracked_out_dir(a.out)
    try:
        deny = re.compile(a.deny_regex) if a.deny_regex else None
    except re.error as exc:
        raise CliError(USAGE, "bad --deny-regex: %s" % exc)
    try:
        exclude_task_id = re.compile(a.exclude_task_id_regex) if a.exclude_task_id_regex else None
    except re.error as exc:
        raise CliError(USAGE, "bad --exclude-task-id-regex: %s" % exc)
    if not (0 < a.full_share <= 1):
        raise CliError(USAGE, "--full-share must be > 0 and <= 1")
    extra_redactions = []
    for raw in a.redact_regex or []:
        if "=>" not in raw:
            raise CliError(USAGE, "--redact-regex must be RE=>REPLACEMENT")
        pattern, replacement = raw.split("=>", 1)
        try:
            extra_redactions.append((re.compile(pattern), replacement))
        except re.error as exc:
            raise CliError(USAGE, "bad --redact-regex: %s" % exc)

    ledger_rows = _read_jsonl(a.ledger)
    short_rows, ledger_stats = CORPUS.from_task_ledger(ledger_rows, deny, exclude_task_id=exclude_task_id)
    plan_inputs = []
    seen_plan_paths = set()
    for pattern in a.plans or []:
        for path in sorted(glob.glob(pattern)):
            real_path = os.path.realpath(path)
            if real_path in seen_plan_paths:
                continue
            seen_plan_paths.add(real_path)
            with open(path, encoding="utf-8") as f:
                plan_inputs.append((os.path.basename(path), f.read()))
    full_rows, plan_stats = CORPUS.from_plan_files(plan_inputs, deny)
    all_rows = full_rows + short_rows
    dropped_non_english = 0
    if a.english_only:
        kept_rows = []
        for row in all_rows:
            if is_mostly_english(str(row.get("text") or "")):
                kept_rows.append(row)
            else:
                dropped_non_english += 1
        all_rows = kept_rows
    sampled = CORPUS.stratified_sample(all_rows, a.n, a.seed, full_share=a.full_share)
    redactions = {}
    if a.redact or extra_redactions:
        sampled, redactions = CORPUS.redact(sampled, extra_redactions)

    os.makedirs(a.out, exist_ok=True)
    corpus_path = os.path.join(a.out, "corpus.jsonl")
    with open(corpus_path, "w", encoding="utf-8") as f:
        for row in sampled:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    stats = {"ledger": ledger_stats, "plans": plan_stats, "rows": len(sampled), "available": len(full_rows) + len(short_rows)}
    if a.english_only:
        stats["dropped_non_english"] = dropped_non_english
    if redactions:
        stats["redactions"] = redactions
    with open(os.path.join(a.out, "corpus-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
    return OK


def cmd_eval_gold(a) -> int:
    _refuse_tracked_out_dir(a.out)
    labels_a = _read_jsonl(a.a)
    labels_b = _read_jsonl(a.b)
    rulings = _read_jsonl(a.rulings) if a.rulings else []
    built = GOLDSET.build(labels_a, labels_b, rulings)

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "gold.json"), "w", encoding="utf-8") as f:
        json.dump(built, f, indent=2, sort_keys=True)
    _write_jsonl(os.path.join(a.out, "gold-queue.jsonl"), built["queue"])
    return OK


def _done_judge_rows(path: str) -> "set[tuple[str, str, int]]":
    done = set()
    if not os.path.exists(path):
        return done
    _truncate_incomplete_jsonl_tail(path)
    for row in _read_jsonl(path):
        try:
            done.add((str(row["id"]), str(row["field"]), int(row["perm"])))
        except (KeyError, TypeError, ValueError):
            continue
    return done


def cmd_eval_judge(a) -> int:
    _refuse_tracked_out_dir(a.out)
    corpus_rows = _read_jsonl(a.corpus)
    roster = R.load_roster(a.roster)
    judge = _make_judge(roster)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "judge.jsonl")
    done = _done_judge_rows(path)
    fields = [f for f in CAS.FIELDS if f in roster.get("vocab", {})]
    calls = 0
    with open(path, "a", encoding="utf-8") as f:
        for row in JEVRUN.run(corpus_rows, roster, judge, fields, a.perms, a.seed, a.sensitivity_sample, done=done):
            if a.max_calls is not None and calls >= a.max_calls:
                break
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            calls += 1
    print(json.dumps({"calls": calls}, sort_keys=True))
    return OK


def _read_report_gold(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_arm(raw: str) -> tuple:
    if "=" not in raw:
        raise CliError(USAGE, "--arm must be NAME=PATH")
    name, path = raw.split("=", 1)
    if not name or not path:
        raise CliError(USAGE, "--arm must be NAME=PATH")
    return name, path


def cmd_eval_report(a) -> int:
    out_path = a.out if a.out.endswith(".md") else os.path.join(a.out, "report.md")
    _refuse_tracked_out_dir(os.path.dirname(out_path) or ".")
    roster = R.load_roster(a.roster)
    arms = {}
    for raw in a.arm or []:
        name, path = _parse_arm(raw)
        arms[name] = _read_jsonl(path)
    if a.incumbent_arm is not None and a.incumbent_arm not in arms:
        supplied = ", ".join(sorted(arms)) if arms else "none"
        raise CliError(FAIL, "unknown incumbent arm %s; supplied arms: %s" % (a.incumbent_arm, supplied))
    stats = {
        "roster": roster,
        "fields": [f for f in CAS.FIELDS if f in roster.get("vocab", {})],
        "kinds": ["short", "full"],
        "corpus": _read_jsonl(a.corpus),
        "gold": _read_report_gold(a.gold),
        "judge": _read_jsonl(a.judge),
        "labeler_a": _read_jsonl(a.a),
        "labeler_b": _read_jsonl(a.b) if a.b else [],
        "arms": arms,
        "incumbent_arm": a.incumbent_arm,
        "max_instability": a.max_instability,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(REPORT.render(stats))
    return OK


def _plan_roster(a):
    return R.load_roster(_resolve_roster(a))


def _plan_existing_labels(text: str) -> dict:
    from .bake import extract_blocks
    return {label["task_id"]: label for _, label in extract_blocks(text)}


def _plan_labels(text: str, path: str, run_id: str, roster: dict, no_judge: bool, collect: bool = False):
    """Bake labels. ``collect`` (shadow mode only) adds the Part B decision votes."""
    from .bake import skeleton_label
    from .planparse import parse_plan

    tasks = parse_plan(text)
    existing = _plan_existing_labels(text)
    judge = None
    if not no_judge and (roster.get("judge") or {}).get("plugin") is not None:
        judge = _make_judge(roster)
    collect = collect and judge is not None
    bake_id = hashlib.sha256(("%s:%s:%s" % (os.path.abspath(path), time.time(), os.getpid()))
                             .encode("utf-8")).hexdigest()[:12]
    task_text = {}
    labels = {}
    shadow = []
    vocab = roster["vocab"]
    for task in tasks:
        draft = {
            "schema_version": "1.0",
            "run_id": run_id,
            "task_id": task["task_id"],
            "title": task["title"],
            "labels": {"role": next(iter(vocab["role"])),
                       "model_tier": next(iter(vocab["model_tier"])),
                       "lane": None, "risk": next(iter(vocab["risk"])),
                       "effort": next(iter(vocab["effort"]))},
            "routing": {"executor": None, "model": None, "resolved_from": None},
            "context": {"spec_path": "plan", "pointers": task.get("files", []),
                         "allowed_paths": task.get("files", []),
                         "depends_on": task.get("depends_on", [])},
            "acceptance": [],
            "provenance": {"lane_reason": "The planner has not selected a lane."},
        }
        final, votes = CAS.label_task(draft, task.get("body", ""), roster, judge=judge)
        for field in CAS.FIELDS:
            current = final["provenance"].get(field, {})
            # Only a rule vote can have produced a planner-attributed value here;
            # a shadow judge vote never decides, so it must not change attribution.
            real_vote = any(v.get("field") == field and v.get("value") is not None
                            and v.get("by", "").startswith("rule:") for v in votes)
            if current.get("by") == "planner" and not real_vote:
                final["provenance"][field] = {
                    "by": "default", "confidence": current.get("confidence"),
                    "conflict": current.get("conflict", False),
                    "votes": [{"by": "default", "value": final["labels"][field],
                                "confidence": current.get("confidence")}],
                }
        vote_values = {"roster": roster}
        for field in CAS.FIELDS:
            vote_values[field] = dict(final["provenance"][field])
            vote_values[field]["value"] = final["labels"][field]
        label = skeleton_label(task, run_id, vote_values)
        old = existing.get(task["task_id"])
        if old:
            known_task_ids = {item["task_id"] for item in tasks}
            block_dependencies = old.get("depends_on", [])
            if not isinstance(block_dependencies, list):
                block_dependencies = []
            label["context"]["depends_on"] = list(dict.fromkeys(
                dependency for dependency in label["context"]["depends_on"] + block_dependencies
                if isinstance(dependency, str) and dependency in known_task_ids
                and dependency != task["task_id"]))
            label["labels"]["lane"] = old.get("labels", {}).get("lane")
            label["provenance"]["lane_reason"] = old.get("lane_reason") or old.get("provenance", {}).get("lane_reason")
            label["acceptance"] = list(old.get("acceptance", []))
            label["context"]["allowed_paths"] = list(old.get("allowed_paths", []))
            for field, value in old.get("labels", {}).items():
                label["labels"][field] = value
                label["provenance"][field] = {
                    "by": "block", "confidence": None, "conflict": False,
                    "votes": [{"by": "block", "value": value, "confidence": None}],
                }
            old_worktree = old.get("worktree") or old.get("context", {}).get("worktree")
            if old_worktree is not None:
                if isinstance(old_worktree, str):
                    old_worktree = {"mode": old_worktree, "branch": None, "base": None,
                                    "worktree_reason": None}
                label["context"]["worktree"] = old_worktree
            for field in ("assignments", "fixes", "watch", "milestone"):
                if field in old:
                    label[field] = old[field]
            for field in ("spec_path", "pointers"):
                if field in old:
                    label["context"][field] = old[field]
        labels[task["task_id"]] = label
        task_text[task["task_id"]] = task.get("body", "")
        if collect:
            shadow.extend(dict(_vote_fields(vote), type="shadow_vote", run_id=run_id, task_id=task["task_id"],
                               bake_id=bake_id, source="bake")
                          for vote in _cascade_votes(roster, votes))
        else:
            shadow.extend({"task_id": task["task_id"], "field": vote["field"],
                           "by": vote["by"], "value": vote["value"],
                           "confidence": vote.get("confidence"), "detail": vote.get("detail", {})}
                          for vote in votes if vote["by"].startswith("judge:"))
    if collect:
        # Second pass: lane needs the whole plan graph (independent tasks).
        for task_id, label in labels.items():
            for vote in _bake_extra_votes(judge, roster, label, task_text[task_id]):
                shadow.append(dict(_vote_fields(vote), type="shadow_vote", run_id=run_id, task_id=task_id,
                                   bake_id=bake_id, source="bake"))
    return labels, shadow


def _plan_shadow_path(path: str) -> str:
    absolute_path = os.path.abspath(path)
    digest = hashlib.sha256(absolute_path.encode("utf-8")).hexdigest()[:8]
    filename = "%s-%s.jsonl" % (os.path.splitext(os.path.basename(absolute_path))[0], digest)
    return os.path.join(_ale_dir(os.path.dirname(absolute_path)), "shadow", filename)


def _plan_shadow_gate(path: str) -> None:
    shadow = _plan_shadow_path(path)
    try:
        _refuse_tracked_out_dir(os.path.dirname(shadow) or ".")
    except CliError as parent_error:
        repo_root = None
        cur = _nearest_existing_parent(shadow)
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                repo_root = cur
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        if repo_root is None:
            raise parent_error
        relative = os.path.relpath(os.path.realpath(shadow), repo_root)
        try:
            proc = subprocess.run(["git", "check-ignore", "-q", "--", relative],
                                  cwd=repo_root, timeout=10)
        except Exception:
            raise parent_error
        if proc.returncode != 0:
            raise parent_error


def _write_shadow(path: str, rows: List[dict]) -> None:
    if not rows:
        return
    shadow = _plan_shadow_path(path)
    os.makedirs(os.path.dirname(shadow), exist_ok=True)
    previous = ""
    if os.path.exists(shadow):
        with open(shadow, encoding="utf-8") as handle:
            previous = handle.read()
    content = previous + "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    H.write_atomic(shadow, content)


def _write_provenance(path: str, labels: dict) -> None:
    values = {task_id: {"provenance": label.get("provenance", {}),
                        "routing": label.get("routing")} for task_id, label in labels.items()}
    run_ids = {label.get("run_id") for label in labels.values()}
    payload = {"run_id": next(iter(run_ids)) if len(run_ids) == 1 else None, "tasks": values}
    H.write_atomic(path + ".ale-provenance.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _plan_print_gaps(labels: dict) -> List[str]:
    from .bake import gaps
    found = []
    for task_id in sorted(labels):
        for gap in gaps(labels[task_id]):
            found.append("%s: %s" % (task_id, gap))
    for item in found:
        print(item)
    return found


def cmd_plan_parse(a) -> int:
    from .planparse import parse_plan
    with open(a.plan_path, encoding="utf-8") as handle:
        tasks = parse_plan(handle.read())
    if a.json:
        print(json.dumps(tasks, indent=2, sort_keys=True))
    else:
        for task in tasks:
            print(json.dumps(task, sort_keys=True))
    return OK


def cmd_plan_bake(a) -> int:
    from .bake import bake, validate_compact_blocks
    from .planparse import PlanParseError
    with open(a.plan_path, encoding="utf-8") as handle:
        text = handle.read()
    roster = _plan_roster(a)
    shadow_error = None
    mode = _judge_mode(roster)
    # shadow: collection is on by roster default; legacy: the old --judge opt-in;
    # off: never, whatever the flags say. --no-judge always disables.
    judge_enabled = bool(not a.no_judge and (mode == "shadow" or (mode == "legacy" and a.judge)))
    if judge_enabled:
        try:
            _plan_shadow_gate(a.plan_path)
        except CliError as exc:
            shadow_error = exc
    try:
        validate_compact_blocks(text)
        labels, shadow = _plan_labels(text, a.plan_path, _plan_run_id(a.plan_path, a.run_id),
                                      roster, not judge_enabled or shadow_error is not None,
                                      collect=mode == "shadow")
        baked = bake(text, labels)
    except (PlanParseError, ValueError) as exc:
        raise CliError(FAIL, str(exc))
    if judge_enabled and shadow_error is None:
        _write_shadow(a.plan_path, shadow)
    if not a.write:
        import difflib
        sys.stdout.write("".join(difflib.unified_diff(
            text.splitlines(True), baked.splitlines(True),
            fromfile=a.plan_path, tofile=a.plan_path)))
    elif baked != text and shadow_error is None:
        H.write_atomic(a.plan_path, baked)
    if a.write and shadow_error is None:
        _write_provenance(a.plan_path, labels)
    found = _plan_print_gaps(labels)
    if shadow_error is not None:
        print(str(shadow_error), file=sys.stderr)
        return FAIL
    return FAIL if found else OK


def _plan_check_labels(labels: dict, roster: dict) -> List[str]:
    errors = L.check_labelset(labels, roster)
    cwd = os.getcwd()
    for label in labels.values():
        for entry in label.get("context", {}).get("allowed_paths", []):
            if entry.endswith("/") or (not re.search(r"[*?\[]", entry)
                                        and os.path.isdir(os.path.join(cwd, entry))):
                errors.append('allowed_paths entry "%s" is a directory: write "%s/*"' % (entry, entry))
    return errors


def _compile_plan_to_run(path: str, run_dir: str, roster: dict, run_id: Optional[str] = None) -> dict:
    from .bake import BakeError, compile_plan
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    provenance = {}
    sidecar_run_id = None
    sidecar = path + ".ale-provenance.json"
    if os.path.exists(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict) and "tasks" in payload:
                    sidecar_run_id = payload.get("run_id")
                    provenance = payload.get("tasks") or {}
                else:
                    provenance = payload
        except (OSError, ValueError) as exc:
            print("cannot read provenance sidecar: %s" % exc, file=sys.stderr)
            return None
    try:
        chosen_run_id = run_id if run_id is not None else sidecar_run_id
        labels = compile_plan(text, _plan_run_id(path, chosen_run_id), provenance=provenance)
    except (BakeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return None
    errors = _plan_check_labels(labels, roster)
    for task_id, label in labels.items():
        if not H.is_safe_id(label.get("run_id")):
            errors.append("unsafe run_id: %s" % label.get("run_id"))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return None
    labels_dir = os.path.join(run_dir, "labels")
    os.makedirs(labels_dir, exist_ok=True)
    for task_id, label in labels.items():
        H.write_atomic(os.path.join(labels_dir, "%s.json" % task_id),
                       json.dumps(label, indent=2, sort_keys=True))
    return labels


def cmd_plan_compile(a) -> int:
    roster = _plan_roster(a)
    run_dir = _resolve_run_dir(a, a.plan_path)
    return OK if _compile_plan_to_run(a.plan_path, run_dir, roster, a.run_id) is not None else FAIL


def _import_bake_votes(c: "Ctx", plan_path: str) -> int:
    """Copy the latest bake's shadow votes for this run into events.jsonl.

    Bake happens before the run exists, so its votes wait in the plan's
    git-ignored shadow file. At init-run they join the run's events (one source
    for ``judge-stats``), and each bake decision's outcome is recorded from the
    compiled label, which is what the planner actually shipped.
    """
    path = _plan_shadow_path(plan_path)
    if not os.path.isfile(path):
        return 0
    rows = [row for row in _read_jsonl(path)
            if row.get("type") == "shadow_vote" and row.get("run_id") == c.run_id
            and row.get("task_id") in c.labels and DECISIONS.is_judged(row.get("decision"))]
    if not rows:
        return 0
    latest = rows[-1].get("bake_id")
    rows = [row for row in rows if row.get("bake_id") == latest]
    for row in rows:
        _emit_vote(c, row["task_id"], row, "bake", attempt=1)
    for task_id in sorted({row["task_id"] for row in rows}):
        outcomes = _bake_outcomes(c.labels[task_id])
        for decision in sorted({row["decision"] for row in rows if row["task_id"] == task_id}):
            if decision in outcomes:
                _emit_outcome(c, task_id, decision, outcomes[decision], "planner")
    return len(rows)


def cmd_init_run_plan(a) -> int:
    if not a.plan:
        return cmd_init_run(a)
    roster = _plan_roster(a)
    run_dir = _resolve_run_dir(a, a.plan)
    a.run_dir = run_dir
    if _compile_plan_to_run(a.plan, run_dir, roster, a.run_id) is None:
        return FAIL
    c = Ctx(a)
    errors = L.check_labelset(c.labels, c.roster)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return FAIL
    if not _resolve_run_agents(c, getattr(a, "agent_variant", None)):
        return FAIL
    if any(event["type"] == "run_started" for event in E.read_events(c.events_path)):
        raise CliError(FAIL, "run already initialised: %s" % c.events_path)
    with open(a.plan, "rb") as handle:
        plan_sha256 = __import__("hashlib").sha256(handle.read()).hexdigest()
    run_id_from = "explicit" if a.run_id is not None else (
        "provenance" if os.path.exists(a.plan + ".ale-provenance.json") else "plan-stem")
    c.emit("run_started", plan_sha256=plan_sha256, run_id_from=run_id_from)
    rhash = R.roster_hash(c.roster)
    for task_id, label in c.labels.items():
        c.emit("labeled", task_id, None, 1, labels=label["labels"], roster_hash=rhash)
    if _judge_mode(c.roster) == "shadow":
        _import_bake_votes(c, a.plan)
        judge = _shadow_judge(c.roster)
        if judge is not None:
            from .planparse import parse_plan
            with open(a.plan, encoding="utf-8") as handle:
                tasks = parse_plan(handle.read())
            _init_run_monitor_votes(c, judge, {task["task_id"]: task.get("body", "") for task in tasks})
    decisions = os.path.join(c.run_dir, "decisions.md")
    if not os.path.exists(decisions):
        H.write_atomic(decisions, "# Decisions for run %s\n\n" % c.run_id)
    _write_current_run(c.run_dir, c.run_id, getattr(a, "set_current", False))
    return OK


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir")
    common.add_argument("--roster")
    common.add_argument("--now")
    common.add_argument("--run-id")
    p = argparse.ArgumentParser(prog="ale")
    sub = p.add_subparsers(dest="cmd")

    def add(name, fn, *, task=False, agent=False):
        sp = sub.add_parser(name, parents=[common])
        sp.set_defaults(fn=fn)
        if task:
            sp.add_argument("--task", required=True)
        if agent:
            sp.add_argument("--agent", required=True)
        return sp

    va = add("validate", cmd_validate)
    va.add_argument("--cwd")
    init_run = add("init-run", cmd_init_run)
    init_run.add_argument("--plan")
    init_run.add_argument("--set-current", action="store_true")
    init_run.add_argument("--agent-variant", action="append")
    init_run.set_defaults(fn=cmd_init_run_plan)
    setup = sub.add_parser("setup")
    setup.add_argument("--force", action="store_true")
    setup.add_argument("--judge", choices=["off", "shadow"])
    setup.set_defaults(fn=cmd_setup)
    run = sub.add_parser("run", parents=[common])
    run.set_defaults(fn=cmd_run)
    run.add_argument("plan_path")
    run.add_argument("--max-cycles", type=int, default=20)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--agent-variant", action="append", metavar="<role>/<sub>=<path>")
    plan = sub.add_parser("plan")
    plan_sub = plan.add_subparsers(dest="plan_cmd")
    plan_parse = plan_sub.add_parser("parse")
    plan_parse.set_defaults(fn=cmd_plan_parse)
    plan_parse.add_argument("plan_path")
    plan_parse.add_argument("--json", action="store_true")
    plan_bake = plan_sub.add_parser("bake")
    plan_bake.set_defaults(fn=cmd_plan_bake)
    plan_bake.add_argument("plan_path")
    plan_bake.add_argument("--run-id")
    plan_bake.add_argument("--judge", action="store_true")
    plan_bake.add_argument("--no-judge", action="store_true")
    plan_bake.add_argument("--write", action="store_true")
    plan_bake.add_argument("--roster")
    plan_compile = plan_sub.add_parser("compile")
    plan_compile.set_defaults(fn=cmd_plan_compile)
    plan_compile.add_argument("plan_path")
    plan_compile.add_argument("--run-dir")
    plan_compile.add_argument("--run-id")
    plan_compile.add_argument("--roster")
    dispatch = add("dispatch", cmd_dispatch)
    dispatch.add_argument("--json", action="store_true")
    dispatch.add_argument("--dry-run", action="store_true")
    dispatch.add_argument("--spawn", action="store_true")
    dispatch.add_argument("--no-exec", action="store_true")
    dispatch.add_argument("--cwd")
    integrate = add("integrate", cmd_integrate, task=True)
    integrate.add_argument("--cwd")
    add("status", cmd_status).add_argument("--json", action="store_true")
    board = add("board", cmd_board)
    board.add_argument("--open", action="store_true")
    runs = sub.add_parser("runs")
    runs.set_defaults(fn=cmd_runs)
    runs.add_argument("--json", action="store_true")
    runs.add_argument("--runs-dir")
    timeline = add("timeline", cmd_timeline)
    timeline.add_argument("--task")
    timeline.add_argument("--json", action="store_true")
    timeline.add_argument("--tail", type=int)
    meta = add("meta", cmd_meta)
    meta.add_argument("--json", action="store_true")
    meta.add_argument("--csv", action="store_true")
    add("ready", cmd_ready)
    add("claim", cmd_claim, task=True, agent=True).add_argument("--pane")
    hb = add("heartbeat", cmd_heartbeat, task=True, agent=True)
    hb.add_argument("--step", required=True)
    hb.add_argument("--files")
    hb.add_argument("--pending", action="append")
    hb.add_argument("--next", action="append")
    hb.add_argument("--auto", action="store_true")
    hb.add_argument("--throttle-s", type=float)
    nt = add("note", cmd_note, task=True)
    nt.add_argument("--agent")
    nt.add_argument("--text", required=True)
    nt.add_argument("--to")
    add("input-required", cmd_input_required, task=True, agent=True).add_argument("--question", required=True)
    add("answer", cmd_answer, task=True).add_argument("--text", required=True)
    add("reopen", cmd_reopen, task=True).add_argument("--reason", required=True)
    add("submit", cmd_submit, task=True, agent=True).add_argument("--summary", required=True)
    vf = add("verify", cmd_verify, task=True)
    vf.add_argument("--cwd")
    vf.add_argument("--base")
    vf.add_argument("--signoff")
    vf.add_argument("--reject", help="record a lead rejection after manual verification")
    ch = add("check", cmd_check, task=True)
    ch.add_argument("--cwd")
    ch.add_argument("--json", action="store_true")
    ch.add_argument("--required", action="store_true")
    add("watchdog", cmd_watchdog)
    us = add("usage", cmd_usage, task=True)
    us.add_argument("--agent")
    us.add_argument("--model", required=True)
    us.add_argument("--input-tokens", type=int, required=True)
    us.add_argument("--output-tokens", type=int, required=True)
    us.add_argument("--cache-read-tokens", type=int, default=0)
    us.add_argument("--cache-write-tokens", type=int, default=0)
    us.add_argument("--cost-usd", type=float)
    us.add_argument("--source", default="self_report", choices=["adapter", "self_report", "unknown"])
    add("decide", cmd_decide).add_argument("--text", required=True)
    add("label", cmd_label).add_argument("--no-judge", action="store_true")
    rl = add("relabel", cmd_relabel, task=True)
    rl.add_argument("--field", required=True)
    rl.add_argument("--value", required=True)
    rl.add_argument("--reason", required=True)
    rl.add_argument("--json", action="store_true")
    agents = sub.add_parser("agents")
    agent_commands = agents.add_subparsers(dest="agents_cmd")
    agent_list = agent_commands.add_parser("list")
    agent_list.set_defaults(fn=cmd_agents_list)
    agent_list.add_argument("--json", action="store_true")
    agent_show = agent_commands.add_parser("show")
    agent_show.set_defaults(fn=cmd_agents_show)
    agent_show.add_argument("key")
    fx = add("fix", cmd_fix, task=True)
    fx.add_argument("--reason")
    rm = add("remove", cmd_remove, task=True)
    rm.add_argument("--reason", required=True)
    adj = add("adjudicate", cmd_adjudicate)
    adj.add_argument("--list", action="store_true")
    adj.add_argument("--task")
    adj.add_argument("--field")
    adj.add_argument("--value")
    adj.add_argument("--by", default="human")
    adj.add_argument("--decision")
    add("judge-stats", cmd_judge_stats)
    add("paths-within", cmd_paths_within).add_argument("task_id")
    add("doctor", cmd_doctor)
    gp = add("guard-path", cmd_guard_path, task=True)
    gp.add_argument("--path", required=True)
    gp.add_argument("--project-root")
    hk = add("hook", cmd_hook)
    hk.add_argument("event", choices=["session-start", "pre-tool", "post-tool", "stop", "prompt-submit"])
    bd = add("bind", cmd_bind, task=True, agent=True)
    bd.add_argument("--session", required=True)
    bd.add_argument("--subagent")
    bd.add_argument("--home")
    ub = add("unbind", cmd_unbind)
    ub.add_argument("--session", required=True)
    ub.add_argument("--subagent")
    ub.add_argument("--home")
    ev = sub.add_parser("eval")
    evsub = ev.add_subparsers(dest="eval_cmd")
    ec = evsub.add_parser("corpus")
    ec.set_defaults(fn=cmd_eval_corpus)
    ec.add_argument("--ledger", required=True)
    ec.add_argument("--plans", action="append")
    ec.add_argument("--deny-regex")
    ec.add_argument("--exclude-task-id-regex")
    ec.add_argument("--n", type=int, default=150)
    ec.add_argument("--seed", type=int, default=7)
    ec.add_argument("--full-share", type=float, default=1 / 3)
    ec.add_argument("--redact", action="store_true")
    ec.add_argument("--redact-regex", action="append")
    ec.add_argument("--english-only", action="store_true")
    ec.add_argument("--out", required=True)
    eg = evsub.add_parser("gold")
    eg.set_defaults(fn=cmd_eval_gold)
    eg.add_argument("--a", required=True)
    eg.add_argument("--b", required=True)
    eg.add_argument("--rulings")
    eg.add_argument("--out", required=True)
    ej = evsub.add_parser("judge")
    ej.set_defaults(fn=cmd_eval_judge)
    ej.add_argument("--corpus", required=True)
    ej.add_argument("--roster", required=True)
    ej.add_argument("--out", required=True)
    ej.add_argument("--perms", type=int, default=3)
    ej.add_argument("--sensitivity-sample", type=int, default=30)
    ej.add_argument("--seed", type=int, default=7)
    ej.add_argument("--max-calls", type=int)
    er = evsub.add_parser("report")
    er.set_defaults(fn=cmd_eval_report)
    er.add_argument("--corpus", required=True)
    er.add_argument("--gold", required=True)
    er.add_argument("--judge", required=True)
    er.add_argument("--a", required=True)
    er.add_argument("--b")
    er.add_argument("--arm", action="append")
    er.add_argument("--incumbent-arm")
    er.add_argument("--max-instability", type=float, default=0.10)
    er.add_argument("--roster", required=True)
    er.add_argument("--out", required=True)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE if exc.code not in (0, None) else OK
    if not getattr(args, "fn", None):
        parser.print_usage(sys.stderr)
        return USAGE
    for name in ("task", "agent", "task_id", "to"):
        if hasattr(args, name):
            value = getattr(args, name)
            if value is not None and not H.is_safe_id(value):
                print("ale: unsafe id for --%s: %r" % (name, value), file=sys.stderr)
                return USAGE
    try:
        return args.fn(args)
    except CliError as exc:
        print("ale: %s" % exc, file=sys.stderr)
        return exc.code
    except LC.LeaseLost as exc:
        print("ale: lease lost: %s" % exc, file=sys.stderr)
        return LEASE_LOST
    except (R.RosterError, E.EventError) as exc:
        print("ale: %s" % exc, file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    sys.exit(main())
