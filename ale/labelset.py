from __future__ import annotations

import glob
import json
import os
import re
from typing import Dict, List, Set

from .roster import RosterError, resolve
from .validate import load_schema, validate

_VOCAB_FIELDS = ("role", "model_tier", "risk", "effort")


def load_labels(run_dir: str) -> Dict[str, dict]:
    labels: Dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "labels", "*.json"))):
        with open(path, encoding="utf-8") as f:
            label = json.load(f)
        labels[label.get("task_id", os.path.basename(path))] = label
    return labels


def effective_watch(label: dict, roster: dict) -> Dict[str, int]:
    role, effort = label["labels"]["role"], label["labels"]["effort"]
    defaults = roster["watch_defaults"]
    watch = dict(defaults.get("%s:%s" % (role, effort)) or defaults.get("*:%s" % effort) or {})
    watch.update(label.get("watch", {}))
    limit = roster["uncertain_below"]
    for value in label.get("provenance", {}).values():
        conf = value.get("confidence") if isinstance(value, dict) else None
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf < limit:
            watch["stuck_after_s"] = max(30, watch["stuck_after_s"] // 2)
            break
    return watch


def _static_prefix(pattern: str) -> str:
    m = re.search(r"[*?\[]", pattern)
    return pattern if m is None else pattern[: m.start()]


def globs_overlap(a: str, b: str) -> bool:
    pa, pb = _static_prefix(a), _static_prefix(b)
    return pa.startswith(pb) or pb.startswith(pa)


def check_label(label: dict, roster: dict) -> List[str]:
    errs = validate(label, load_schema("label.schema.json"))
    if errs:
        return errs
    tid = label["task_id"]
    for field in _VOCAB_FIELDS:
        value = label["labels"][field]
        if value not in roster["vocab"][field]:
            errs.append("%s: labels.%s=%r is not in the roster vocabulary" % (tid, field, value))
    if not errs:
        try:
            resolve(roster, label["labels"]["role"], label["labels"]["model_tier"])
        except RosterError as exc:
            errs.append("%s: routing: %s" % (tid, exc))
        watch = effective_watch(label, roster)
        for key in ("heartbeat_timeout_s", "stuck_after_s", "max_duration_s", "budget_tokens", "max_attempts"):
            if key not in watch:
                errs.append("%s: watch.%s has no value and no roster default" % (tid, key))
    return errs


def _ancestors(labels: Dict[str, dict]) -> Dict[str, Set[str]]:
    memo: Dict[str, Set[str]] = {}

    def walk(tid: str, stack: List[str]) -> Set[str]:
        if tid in memo:
            return memo[tid]
        if tid in stack:
            raise ValueError("cycle: %s" % " -> ".join(stack[stack.index(tid):] + [tid]))
        out: Set[str] = set()
        for dep in labels[tid]["context"]["depends_on"]:
            if dep in labels:
                out.add(dep)
                out |= walk(dep, stack + [tid])
        memo[tid] = out
        return out

    for tid in labels:
        walk(tid, [])
    return memo


def check_labelset(labels: Dict[str, dict], roster: dict) -> List[str]:
    errs: List[str] = []
    for label in labels.values():
        errs.extend(check_label(label, roster))
    if errs:
        return errs
    if len({l["run_id"] for l in labels.values()}) > 1:
        errs.append("labels carry more than one run_id")
    cap = roster["cost_gate"]["max_tasks_per_run"]
    if len(labels) > cap:
        errs.append("%d tasks exceeds cost_gate.max_tasks_per_run=%d" % (len(labels), cap))
    for tid, label in labels.items():
        for dep in label["context"]["depends_on"]:
            if dep not in labels:
                errs.append("%s: depends_on unknown task %s" % (tid, dep))
    if errs:
        return errs
    try:
        anc = _ancestors(labels)
    except ValueError as exc:
        return [str(exc)]
    ids = sorted(labels)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a in anc[b] or b in anc[a]:
                continue
            for ga in labels[a]["context"]["allowed_paths"]:
                for gb in labels[b]["context"]["allowed_paths"]:
                    if globs_overlap(ga, gb):
                        errs.append("%s and %s may run concurrently but allowed_paths overlap: %s vs %s" % (a, b, ga, gb))
    return errs
