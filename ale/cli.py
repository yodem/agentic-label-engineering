from __future__ import annotations

import argparse
import json
import os
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
    if E.read_events(c.events_path):
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
    extra = {"step": a.step[:TEXT_MAX]}
    if a.files:
        extra["files_modified"] = [p for p in a.files.split(",") if p]
    if a.pending:
        extra["pending"] = a.pending
    if a.next:
        extra["next_steps"] = a.next
    return _owned(Ctx(a), a, "heartbeat", **extra)


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

    add("validate", cmd_validate)
    add("init-run", cmd_init_run)
    add("status", cmd_status).add_argument("--json", action="store_true")
    add("ready", cmd_ready)
    add("claim", cmd_claim, task=True, agent=True)
    hb = add("heartbeat", cmd_heartbeat, task=True, agent=True)
    hb.add_argument("--step", required=True)
    hb.add_argument("--files")
    hb.add_argument("--pending", action="append")
    hb.add_argument("--next", action="append")
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
    add("watchdog", cmd_watchdog)
    us = add("usage", cmd_usage, task=True)
    us.add_argument("--agent")
    us.add_argument("--model", required=True)
    us.add_argument("--input-tokens", type=int, required=True)
    us.add_argument("--output-tokens", type=int, required=True)
    us.add_argument("--cost-usd", type=float)
    us.add_argument("--source", default="self_report", choices=["adapter", "self_report", "unknown"])
    add("decide", cmd_decide).add_argument("--text", required=True)
    add("paths-within", cmd_paths_within).add_argument("task_id")
    add("doctor", cmd_doctor)
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
