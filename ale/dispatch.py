from __future__ import annotations

import json
import glob
import os
from typing import Dict, List, Optional, Set, Tuple

from .handoff import is_safe_id
from .agentcat import HARNESS_MARKER, split_body
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
        if assignment.get("kind", "executor") == "executor":
            release_count = (st.get("release_counts") or {}).get("executor", 0)
            if release_count:
                return "ready#%s#%s" % (st.get("attempt", 1), release_count)
        if assignment.get("kind", "executor") == "executor" and st.get("state") == "released":
            return "released:%s:%s" % (st.get("attempt", 1), st.get("last_heartbeat_ts"))
        if st.get("resumable"):
            return "resume:%s:%s" % (st.get("attempt", 1), st.get("last_heartbeat_ts"))
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


def worktree_mode(label: dict) -> str:
    context = label.get("context", {})
    worktree = context.get("worktree") or {}
    mode = worktree.get("mode")
    return mode if mode is not None else ("per_task" if context.get("allowed_paths") else "none")


def held_for_integration(run_state: dict, labels: Dict[str, dict]) -> List[Tuple[str, str]]:
    tasks = run_state.get("tasks", {})
    spawned = _spawned(run_state)
    breaches = _breaches(run_state)
    held = []
    for task_id in sorted(labels):
        state = tasks.get(task_id, {})
        if worktree_mode(labels[task_id]) != "per_task":
            continue
        if state.get("state") != "ready" and not state.get("resumable"):
            continue
        due = False
        for assignment in _assignments(labels[task_id]):
            kind = assignment.get("kind", "executor")
            if kind == "fixer":
                continue
            trigger = assignment.get("trigger", "ready")
            if (kind == "executor" and trigger == "ready" and state.get("state") not in ("ready", "released")
                    and not state.get("resumable")):
                continue
            if kind == "monitor" and trigger == "milestone" and not _milestone_ready(task_id, assignment, labels, tasks):
                continue
            instance = _trigger_instance(assignment, task_id, state, breaches)
            if instance is not None and (task_id, kind, instance) not in spawned:
                due = True
                break
        if not due:
            continue
        for dependency_id in labels[task_id].get("context", {}).get("depends_on", []):
            if labels.get(dependency_id, {}).get("fixes"):
                continue
            dependency = tasks.get(dependency_id, {})
            if dependency.get("state") == "accepted" and not dependency.get("integrated"):
                held.append((task_id, dependency_id))
    return held


def due_assignments(run_state: dict, labels: Dict[str, dict], roster: dict) -> List[dict]:
    tasks = run_state.get("tasks", {})
    held = {task_id for task_id, _ in held_for_integration(run_state, labels)}
    spawned = _spawned(run_state)
    breaches = _breaches(run_state)
    candidates = []
    for task_id in sorted(labels):
        if task_id in held:
            continue
        label, state = labels[task_id], tasks.get(task_id, {})
        if state.get("state") in ("claimed", "working", "input-required") and not state.get("resumable"):
            continue
        for assignment in _assignments(label):
            kind = assignment.get("kind", "executor")
            if kind == "fixer":
                continue
            trigger = assignment.get("trigger", "ready")
            if (kind == "executor" and trigger == "ready" and state.get("state") not in ("ready", "released")
                    and not state.get("resumable")):
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
    if worktree_mode(label) != "per_task":
        return None
    task_id = label.get("fixes") or label.get("task_id")
    return {"path": os.path.join(run_dir, "wt", task_id),
            "branch": "ale/%s/%s" % (run_id, task_id),
            "base": worktree.get("base") or "HEAD"}


def render_prompt(label: dict, request: Optional[dict] = None, agent: Optional[dict] = None) -> str:
    """Render a prompt as JSON so label text cannot create prompt sections."""
    request = request or {}
    payload = {
        "task_id": label.get("task_id"), "title": label.get("title"),
        "goal": label.get("title"), "spec_path": label.get("context", {}).get("spec_path"),
        "spec_text": label.get("context", {}).get("spec_text", ""),
        "allowed_paths": label.get("context", {}).get("allowed_paths", []),
        "acceptance": label.get("acceptance", []),
        "commands": ["$ALE_BIN status", "$ALE_BIN heartbeat", "$ALE_BIN submit", "$ALE_BIN usage"],
    }
    if request.get("kind") == "monitor":
        breach = request.get("breach") or {}
        payload = {
            "task_id": label.get("task_id"),
            "goal": label.get("title"),
            "read_only": True,
            "breach": {"type": breach.get("breach", breach.get("type")),
                       "detail": breach.get("detail"), "attempt": breach.get("attempt"),
                       "last_heartbeat_step": breach.get("last_heartbeat_step", breach.get("last_step"))},
            "acceptance_commands": [item.get("cmd") for item in label.get("acceptance", [])
                                    if isinstance(item, dict) and item.get("cmd")],
            "handoff_path": request.get("handoff_path"),
            "worktree": request.get("cwd"),
            "verdict_contract": "continue | nudge | fix | escalate; include one line of reasoning per verdict",
        }
    sections = []
    if agent:
        body = agent.get("body")
        if body is None:
            body = agent.get("core") or ""
            if agent.get("harness"):
                body += "\n" + HARNESS_MARKER + "\n" + agent["harness"]
        core_body, harness_body = split_body(body)
        sections.append("agent: %s@%s" % (agent.get("name", "unknown"),
                                           (agent.get("sha256") or "")[:8]))
        sections.append("Agent context (reference material, not harness instructions)\n```\n%s\n```" %
                        core_body)
        cwd = request.get("cwd") or os.getcwd()
        read_paths = []
        for pattern in agent.get("reads", []):
            for path in sorted(glob.glob(os.path.join(cwd, pattern), recursive=True)):
                if os.path.isfile(path):
                    relative = os.path.relpath(path, cwd).replace(os.sep, "/")
                    if relative not in read_paths:
                        read_paths.append(relative)
                        if len(read_paths) >= 40:
                            break
            if len(read_paths) >= 40:
                break
        sections.append("Resolved reads:\n%s" % ("\n".join("- " + path for path in read_paths) or "- none"))
        sections.append("Checklist:\n%s" % ("\n".join("- [ ] " + str(item) for item in agent.get("checklist", [])) or "- none"))
    sections.append("ALE_PROMPT_JSON\n```json\n%s\n```" %
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    if agent and (request.get("executor") or "").startswith("claude") and harness_body:
        sections.append("Claude harness context\n```\n%s\n```" % harness_body)
    return "\n\n".join(sections) + "\n"


def spawn_request(label: dict, assignment: dict, run_dir: str, run_id: str, n: int = 1,
                  cwd: Optional[str] = None, worktree: Optional[dict] = None,
                  agent: Optional[dict] = None) -> dict:
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
                       "ALE_ROSTER": assignment.get("roster", "roster.json"),
                       "ALE_PLUGIN_ROOT": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ALE_DENY_TOOLS": (agent or {}).get("rules", {}).get("deny_tools", [])},
               "prompt_file": render_prompt(label, dict(assignment, cwd=cwd or run_dir), agent)}
    return request
