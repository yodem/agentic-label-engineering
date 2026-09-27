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
