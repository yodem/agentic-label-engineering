from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from typing import List, Optional

from . import events as E
from . import handoff as H
from . import labelset as L
from . import lifecycle as LC
from . import roster as R
from . import verify as V
from . import watchdog as W
from . import binding as B
from . import hooks as HK
from . import usage_transcript as UT
from .labeling import cascade as CAS
from .labeling import truth as TRUTH
from .labeling.judge import CommandJudge, is_mostly_english
from .evalharness import corpus as CORPUS
from .evalharness import goldset as GOLDSET
from .evalharness import jevrun as JEVRUN
from .evalharness import report as REPORT

OK, FAIL, USAGE, CLAIM_LOST, LEASE_LOST, SIGNOFF, BREACH = 0, 1, 2, 3, 4, 5, 6
TEXT_MAX = 1000


class CliError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class Ctx:
    def __init__(self, a: argparse.Namespace, need_roster: bool = True):
        self.run_dir = a.run_dir or os.environ.get("ALE_RUN_DIR")
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
        self.roster = R.load_roster(a.roster or os.environ.get("ALE_ROSTER") or "roster.json") if need_roster else None
        raw_now = a.now if a.now is not None else os.environ.get("ALE_NOW")
        if raw_now in (None, ""):
            self.now = time.time()
        else:
            try:
                self.now = float(raw_now)
            except (TypeError, ValueError):
                raise CliError(USAGE, "--now must be a number, got %r" % (raw_now,))

    def state(self) -> dict:
        return E.reduce_run(E.read_events(self.events_path), self.labels)

    def emit(self, kind: str, task_id: Optional[str] = None, agent_id: Optional[str] = None,
             attempt: Optional[int] = None, **extra) -> None:
        E.append_event(self.events_path, E.make_event(kind, self.run_id, self.now, task_id, agent_id, attempt, **extra))

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
    errs = L.check_labelset(c.labels, c.roster)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return FAIL
    if any(e["type"] == "run_started" for e in E.read_events(c.events_path)):
        raise CliError(FAIL, "run already initialised: %s" % c.events_path)
    c.emit("run_started")
    rhash = R.roster_hash(c.roster)
    for tid, label in c.labels.items():
        c.emit("labeled", tid, None, 1, labels=label["labels"], roster_hash=rhash)
    decisions = os.path.join(c.run_dir, "decisions.md")
    if not os.path.exists(decisions):
        H.write_atomic(decisions, "# Decisions for run %s\n\n" % c.run_id)
    return OK


def cmd_status(a) -> int:
    c = Ctx(a)
    state = c.state()
    if a.json:
        print(json.dumps(state, sort_keys=True))
    else:
        for tid in sorted(state["tasks"]):
            st = state["tasks"][tid]
            print("%-8s %-15s attempt=%d owner=%s tokens=%d step=%s" % (
                tid, st["state"], st["attempt"], st["owner"] or "-", st["tokens"], st["last_step"] or "-"))
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
    if not LC.try_claim(c.events_path, c.labels, c.run_id, a.task, a.agent, c.now, cap):
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
    return _owned(Ctx(a), a, "note", **extra)


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


def cmd_verify(a) -> int:
    c = Ctx(a)
    st = c.task(a.task)
    if st["state"] != "submitted":
        raise CliError(FAIL, "task %s is %s, not submitted" % (a.task, st["state"]))
    label, owner, attempt = c.labels[a.task], st["owner"], st["attempt"]
    cwd = a.cwd or os.getcwd()
    evidence = V.run_acceptance(label, cwd)
    reason = None
    if a.base:
        bad = V.paths_within(_changed_files(cwd, a.base), label["context"]["allowed_paths"])
        evidence["path_violations"] = bad[:20]
        if bad:
            reason = "path_violation: %s" % ", ".join(bad[:5])
    if reason is None and not evidence["passed"]:
        reason = "acceptance failed: %s" % ", ".join(r["id"] for r in evidence["results"] if not r["ok"])
    _fit(evidence)
    c.emit("verified", a.task, None, attempt, evidence=evidence)
    if reason is not None:
        c.emit("rejected", a.task, None, attempt, evidence=evidence, reason=reason)
        c.render(a.task, owner)
        print(reason, file=sys.stderr)
        return FAIL
    if (evidence["manual"] or label["labels"]["risk"] == "high") and not a.signoff:
        print("needs sign-off: manual=%s risk=%s" % (evidence["manual"], label["labels"]["risk"]), file=sys.stderr)
        return SIGNOFF
    if a.signoff:
        evidence["signoff"] = a.signoff
    c.emit("accepted", a.task, None, attempt, evidence=evidence)
    c.render(a.task, owner)
    return OK


def cmd_check(a) -> int:
    c = Ctx(a)
    c.task(a.task)
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
             "gen_ai.usage.output_tokens": a.output_tokens, "usage_source": a.source}
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
    roster = os.environ.get("ALE_ROSTER") or "roster.json"
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
            watch = L.effective_watch(label, c.roster)
            if not LC.try_claim(c.events_path, c.labels, c.run_id, binding["task_id"], binding["agent_id"], c.now, watch["max_attempts"]):
                print("STOP: task claim was lost; stop working on this task")
                return OK
            handoff = _read_optional(H.handoff_path(c.run_dir, binding["task_id"], binding["agent_id"]))
            decisions = _read_optional(os.path.join(c.run_dir, "decisions.md"))
            print(HK.session_context(label, handoff, decisions))
            return OK
        if event == "pre-tool":
            project_root = data.get("cwd") or os.getcwd()
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
            evidence = V.run_acceptance(label, data.get("cwd") or os.getcwd())
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
    root = os.path.abspath(a.project_root or os.getcwd())
    path = a.path
    relative = os.path.relpath(os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path)), root)
    return OK if not V.paths_within([relative], label["context"]["allowed_paths"]) else FAIL


def cmd_decide(a) -> int:
    c = Ctx(a)
    text = a.text[:TEXT_MAX]
    c.emit("decision", text=text)
    with open(os.path.join(c.run_dir, "decisions.md"), "a", encoding="utf-8") as f:
        f.write("- [%d] %s\n" % (int(c.now), text))
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
    for p in problems:
        print(p, file=sys.stderr)
    return FAIL if problems else OK


def _binding_home(a) -> str:
    return a.home or os.environ.get("ALE_HOME") or os.path.expanduser("~")


def cmd_bind(a) -> int:
    c = Ctx(a)
    c.task(a.task)
    path = B.binding_path(_binding_home(a), a.session, a.subagent)
    roster = a.roster or os.environ.get("ALE_ROSTER") or "roster.json"
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
    run_dir = a.run_dir or os.environ.get("ALE_RUN_DIR")
    if not run_dir:
        raise CliError(USAGE, "--run-dir or ALE_RUN_DIR is required")
    roster = R.load_roster(a.roster or os.environ.get("ALE_ROSTER") or "roster.json")
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

    jconf = roster.get("judge") or {}
    judge = None
    if not a.no_judge and jconf.get("plugin") is not None:
        judge = CommandJudge(jconf["command"], timeout_s=jconf.get("timeout_s", 30))

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
        run_id = draft.get("run_id", "")
        for v in votes:
            extra = {"field": v["field"], "by": v["by"], "value": v["value"], "confidence": v["confidence"]}
            err = (v.get("detail") or {}).get("error")
            if err:
                extra["error"] = str(err)[:200]
            E.append_event(events_path, E.make_event("label_vote", run_id, now, tid, None, 1, **extra))
            total_votes += 1
            if v["by"].startswith("judge:"):
                if v["value"] is None:
                    judge_abstains += 1
                elif v["value"] != draft["labels"][v["field"]]:
                    disagreements.append({"task": tid, "field": v["field"],
                                           "planner": draft["labels"][v["field"]], "judge": v["value"]})
        for field in CAS.FIELDS:
            if final["provenance"][field]["conflict"]:
                conflicts += 1

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
    if a.field == "lane" or a.field not in CAS.FIELDS:
        raise CliError(USAGE, "field %s cannot be relabeled or adjudicated" % a.field)
    c = Ctx(a)
    _vocab_value(c.roster, a.field, a.value)
    st = c.task(a.task)
    if st["state"] in E.TERMINAL:
        raise CliError(FAIL, "task %s is terminal: %s" % (a.task, st["state"]))

    label = dict(c.labels[a.task])
    labels = dict(label["labels"])
    old = labels[a.field]
    labels[a.field] = a.value
    label["labels"] = labels
    provenance = dict(label.get("provenance") or {})
    field_provenance = provenance.get(a.field)
    if not isinstance(field_provenance, dict):
        field_provenance = {}
    else:
        field_provenance = dict(field_provenance)
    field_provenance["relabeled_from"] = old
    provenance[a.field] = field_provenance
    label["provenance"] = provenance

    H.write_atomic(_label_path(c.run_dir, a.task), json.dumps(label, indent=2, sort_keys=True))
    c.emit("relabeled", a.task, None, st["attempt"], field=a.field, old=old, new=a.value,
           reason=a.reason[:TEXT_MAX])
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
    jconf = roster.get("judge") or {}
    judge = CommandJudge(jconf.get("command") or ["jev-ask"], timeout_s=jconf.get("timeout_s", 30))
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


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir")
    common.add_argument("--roster")
    common.add_argument("--now")
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
    add("init-run", cmd_init_run)
    add("status", cmd_status).add_argument("--json", action="store_true")
    add("ready", cmd_ready)
    add("claim", cmd_claim, task=True, agent=True)
    hb = add("heartbeat", cmd_heartbeat, task=True, agent=True)
    hb.add_argument("--step", required=True)
    hb.add_argument("--files")
    hb.add_argument("--pending", action="append")
    hb.add_argument("--next", action="append")
    hb.add_argument("--auto", action="store_true")
    hb.add_argument("--throttle-s", type=float)
    nt = add("note", cmd_note, task=True, agent=True)
    nt.add_argument("--text", required=True)
    nt.add_argument("--to")
    add("input-required", cmd_input_required, task=True, agent=True).add_argument("--question", required=True)
    add("answer", cmd_answer, task=True).add_argument("--text", required=True)
    add("submit", cmd_submit, task=True, agent=True).add_argument("--summary", required=True)
    vf = add("verify", cmd_verify, task=True)
    vf.add_argument("--cwd")
    vf.add_argument("--base")
    vf.add_argument("--signoff")
    ch = add("check", cmd_check, task=True)
    ch.add_argument("--cwd")
    ch.add_argument("--json", action="store_true")
    add("watchdog", cmd_watchdog)
    us = add("usage", cmd_usage, task=True)
    us.add_argument("--agent")
    us.add_argument("--model", required=True)
    us.add_argument("--input-tokens", type=int, required=True)
    us.add_argument("--output-tokens", type=int, required=True)
    us.add_argument("--cost-usd", type=float)
    us.add_argument("--source", default="self_report", choices=["adapter", "self_report", "unknown"])
    add("decide", cmd_decide).add_argument("--text", required=True)
    add("label", cmd_label).add_argument("--no-judge", action="store_true")
    rl = add("relabel", cmd_relabel, task=True)
    rl.add_argument("--field", required=True)
    rl.add_argument("--value", required=True)
    rl.add_argument("--reason", required=True)
    adj = add("adjudicate", cmd_adjudicate)
    adj.add_argument("--list", action="store_true")
    adj.add_argument("--task")
    adj.add_argument("--field")
    adj.add_argument("--value")
    adj.add_argument("--by", default="human")
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
