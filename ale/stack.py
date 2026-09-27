"""Stacked tasks: a dependent task starts from its dependency's accepted commit.

Everything here is computed from labels, reduced run state and the event log;
nothing writes events. The relation comes from the label (``stack: true`` and
exactly one ``depends_on``); the base a child was built on comes from the log.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def is_stacked(label: dict) -> bool:
    worktree = (label.get("context") or {}).get("worktree") or {}
    return worktree.get("stack") is True


def stack_parent(label: dict) -> Optional[str]:
    """The single dependency of a stacked label, or None."""
    if not is_stacked(label):
        return None
    depends_on = (label.get("context") or {}).get("depends_on") or []
    return depends_on[0] if len(depends_on) == 1 else None


def accepted_commit(task_state: dict) -> Optional[str]:
    if task_state.get("state") != "accepted":
        return None
    commit = (task_state.get("evidence") or {}).get("commit")
    return commit if isinstance(commit, str) and commit else None


def stack_base(label: dict, run_state: dict) -> Optional[dict]:
    """``{"parent", "commit"}`` when the label can start from its parent's accepted commit.

    None when the task is not stacked, when the parent is already integrated
    (the task then starts from the integrated HEAD, as unstacked tasks do), or
    when the parent has no accepted commit yet.
    """
    parent = stack_parent(label)
    if parent is None:
        return None
    parent_state = (run_state.get("tasks") or {}).get(parent) or {}
    if parent_state.get("integrated"):
        return None
    commit = accepted_commit(parent_state)
    if commit is None:
        return None
    return {"parent": parent, "commit": commit}


def current_base(events: List[dict], task_id: str) -> Optional[str]:
    """The commit a stacked task's branch is built on: the last restack, else the last stacked spawn."""
    base = None
    for event in events:
        if event.get("task_id") != task_id:
            continue
        if event.get("type") == "spawned" and event.get("base_commit"):
            base = event["base_commit"]
        elif event.get("type") == "restacked" and event.get("new_base"):
            base = event["new_base"]
    return base


def latest_accepted_commit(events: List[dict], task_id: str) -> Optional[str]:
    commit = None
    for event in events:
        if event.get("task_id") == task_id and event.get("type") == "accepted":
            value = (event.get("evidence") or {}).get("commit")
            if value:
                commit = value
    return commit


def stacked_children(labels: Dict[str, dict], run_state: dict, events: List[dict], parent: str) -> List[str]:
    """Unintegrated tasks stacked on ``parent`` that were built on one of its commits."""
    tasks = run_state.get("tasks") or {}
    return [task_id for task_id in sorted(labels)
            if stack_parent(labels[task_id]) == parent
            and not (tasks.get(task_id) or {}).get("integrated")
            and current_base(events, task_id) is not None]


def integrate_blocker(labels: Dict[str, dict], run_state: dict, events: List[dict], task_id: str) -> Optional[str]:
    """Why a stacked task cannot integrate yet, or None when it can."""
    parent = stack_parent(labels.get(task_id) or {})
    base = current_base(events, task_id)
    if parent is None or base is None:
        return None
    if not ((run_state.get("tasks") or {}).get(parent) or {}).get("integrated"):
        return "integrate %s first" % parent
    latest = latest_accepted_commit(events, parent)
    if latest and latest != base:
        return ("task %s is built on %s but %s was last accepted at %s; run ale restack --task %s"
                % (task_id, base[:12], parent, latest[:12], task_id))
    return None


def needs_restack(labels: Dict[str, dict], run_state: dict, events: List[dict], task_id: str) -> bool:
    """True when a stacked task's parent is accepted at a commit other than the task's base."""
    parent = stack_parent(labels.get(task_id) or {})
    base = current_base(events, task_id)
    if parent is None or base is None:
        return False
    if ((run_state.get("tasks") or {}).get(parent) or {}).get("state") != "accepted":
        return False
    latest = latest_accepted_commit(events, parent)
    return bool(latest) and latest != base
