from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set, Tuple

from .handoff import is_safe_id
from .labelset import globs_overlap
from .roster import resolve


SPAWN_EXECUTORS = ("herdr-pane", "claude-headless", "codex-exec", "pi-print", "claude-subagent")


def _assignments(label: dict) -> List[dict]:
    assignments = label.get("assignments")
    if assignments:
        return list(assignments)
    labels = label.get("labels", {})
    return [{"kind": "executor", "role": labels.get("role"),
             "model_tier": labels.get("model_tier"), "executor": None, "trigger": "ready"}]


def _spawned(run_state: dict) -> Set[Tuple[str, str, str]]:
    found = set()
    values = run_state.get("spawned", run_state.get("spawned_keys", set()))
    if isinstance(values, dict):
        values = values.keys()
    if isinstance(values, (list, tuple, set)):
        for value in values:
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                found.add((str(value[0]), str(value[1]), str(value[2])))
            elif isinstance(value, dict):
                found.add((str(value.get("task_id")), str(value.get("kind")), str(value.get("trigger_instance"))))
    for task_id, state in run_state.get("tasks", {}).items():
        for value in state.get("spawned", []) if isinstance(state, dict) else []:
            if isinstance(value, dict):
                found.add((str(task_id), str(value.get("kind")), str(value.get("trigger_instance"))))
    return found


def _breaches(run_state: dict) -> List[dict]:
    for key in ("breaches", "watchdog_breaches"):
        if isinstance(run_state.get(key), list):
            return run_state[key]
    if isinstance(run_state.get("run"), dict) and isinstance(run_state["run"].get("breaches"), list):
        return run_state["run"]["breaches"]
    return []


def _trigger_instance(assignment: dict, task_id: str, st: dict, breaches: List[dict]) -> Optional[str]:
    trigger = assignment.get("trigger", "ready")
    if trigger == "ready":
        return "ready"
    if trigger == "on_submit":
        return "submit:%s" % st.get("attempt", 1) if st.get("state") == "submitted" else None
    if trigger == "on_breach":
        matches = [b for b in breaches if b.get("task_id") == task_id]
        if not matches:
            return None
        breach = matches[-1]
        return "breach:%s:%s" % (breach.get("breach", "unknown"), breach.get("attempt", st.get("attempt", 1)))
    if trigger == "milestone":
        milestone = assignment.get("milestone")
        if not milestone:
            return None
        return "milestone:%s" % milestone
    return None


def _milestone_ready(task_id: str, assignment: dict, labels: Dict[str, dict], tasks: Dict[str, dict]) -> bool:
    milestone = assignment.get("milestone")
    if not milestone:
        return False
    members = []
    for tid, label in labels.items():
        for candidate in _assignments(label):
            if candidate.get("kind") == assignment.get("kind") and candidate.get("trigger") == "milestone" \
                    and candidate.get("milestone") == milestone:
                members.append(tid)
                break
    return bool(members) and all(tasks.get(tid, {}).get("state") in
                                 ("submitted", "verified", "accepted", "rejected", "failed", "canceled")
                                 for tid in members)


def _paths_overlap(a: dict, b: dict) -> bool:
    paths_a = a.get("context", {}).get("allowed_paths", [])
    paths_b = b.get("context", {}).get("allowed_paths", [])
    return any(globs_overlap(x, y) for x in paths_a for y in paths_b)


def due_assignments(run_state: dict, labels: Dict[str, dict], roster: dict) -> List[dict]:
    tasks = run_state.get("tasks", {})
    spawned = _spawned(run_state)
    breaches = _breaches(run_state)
    candidates = []
    for task_id in sorted(labels):
        label, state = labels[task_id], tasks.get(task_id, {})
        if state.get("state") in ("claimed", "working", "input-required"):
            continue
        for assignment in _assignments(label):
            kind = assignment.get("kind", "executor")
            if kind == "fixer":
                continue
            trigger = assignment.get("trigger", "ready")
            if kind == "executor" and trigger == "ready" and state.get("state") != "ready":
                continue
            if kind == "monitor" and trigger == "milestone" and not _milestone_ready(task_id, assignment, labels, tasks):
                continue
            instance = _trigger_instance(assignment, task_id, state, breaches)
            if instance is None or (task_id, kind, instance) in spawned:
                continue
            requested_executor = assignment.get("executor")
            routing = resolve(roster, assignment.get("role", label.get("labels", {}).get("role")),
                              assignment.get("model_tier", label.get("labels", {}).get("model_tier")),
                              executor=requested_executor)
            candidates.append({"task_id": task_id, "kind": kind, "role": assignment.get("role", label.get("labels", {}).get("role")),
                              "model_tier": assignment.get("model_tier", label.get("labels", {}).get("model_tier")),
                              "executor": requested_executor or routing["executor"], "model": routing["model"],
                              "trigger": trigger, "trigger_instance": instance,
                              "breach": next((b for b in reversed(breaches) if b.get("task_id") == task_id), None) if trigger == "on_breach" else None})
    cap = roster.get("cost_gate", {}).get("max_parallel", roster.get("cost_gate", {}).get("max_concurrent", 3))
    chosen, chosen_ids = [], []
    for item in candidates:
        if len(chosen) >= cap:
            break
        if any(other != item["task_id"] and _paths_overlap(labels[item["task_id"]], labels[other]) for other in chosen_ids):
            continue
        chosen.append(item)
        chosen_ids.append(item["task_id"])
    return chosen


def mint_agent_id(task_id: str, kind: str, role: str, n: int) -> str:
    value = "%s-%s-%s-%s" % (task_id, kind, role, n)
    if len(value) > 64 or not is_safe_id(value):
        raise ValueError("unsafe agent id: %s" % value)
    return value


def worktree_plan(label: dict, run_dir: str, run_id: str) -> Optional[dict]:
    context = label.get("context", {})
    worktree = context.get("worktree") or {}
    mode = worktree.get("mode")
    if mode is None:
        mode = "per_task" if context.get("allowed_paths") else "none"
    if mode != "per_task":
        return None
    task_id = label.get("fixes") or label.get("task_id")
    return {"path": os.path.join(run_dir, "wt", task_id),
            "branch": "ale/%s/%s" % (run_id, task_id),
            "base": worktree.get("base") or run_dir}


def render_prompt(label: dict, request: Optional[dict] = None) -> str:
    """Render a prompt as JSON so label text cannot create prompt sections."""
    request = request or {}
    payload = {
        "task_id": label.get("task_id"), "title": label.get("title"),
        "goal": label.get("title"), "spec_path": label.get("context", {}).get("spec_path"),
        "spec_text": label.get("context", {}).get("spec_text", ""),
        "allowed_paths": label.get("context", {}).get("allowed_paths", []),
        "acceptance": label.get("acceptance", []),
        "commands": ["ale status", "ale heartbeat", "ale submit", "ale usage"],
    }
    if request.get("kind") == "monitor":
        payload["read_only"] = True
        payload["breach"] = request.get("breach")
        payload["verdict_contract"] = "continue | nudge | fix | escalate"
    return "ALE_PROMPT_JSON\n```json\n%s\n```\n" % json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def spawn_request(label: dict, assignment: dict, run_dir: str, run_id: str, n: int = 1,
                  cwd: Optional[str] = None, worktree: Optional[dict] = None) -> dict:
    task_id = label["task_id"]
    kind, role = assignment.get("kind", "executor"), assignment.get("role", label.get("labels", {}).get("role"))
    agent_id = mint_agent_id(task_id, kind, role, n)
    plan = worktree if worktree is not None else worktree_plan(label, run_dir, run_id)
    request = {"agent_id": agent_id, "task_id": task_id, "kind": kind, "role": role,
               "executor": assignment.get("executor"), "model": assignment.get("model"),
               "cwd": cwd or (plan["path"] if plan else run_dir),
               "spec_path": label.get("context", {}).get("spec_path"),
               "handoff_path": os.path.join(run_dir, "handoff", "%s-%s-handoff.md" % (agent_id, role)),
               "env": {"ALE_TASK": task_id, "ALE_AGENT": agent_id, "ALE_RUN_DIR": run_dir,
                       "ALE_ROSTER": assignment.get("roster", "roster.json")},
               "prompt_file": render_prompt(label, assignment)}
    return request
