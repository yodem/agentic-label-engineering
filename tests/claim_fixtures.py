"""Fixture labels for tests that exercise claim/submit/verify without an ALE worktree.

``ale claim`` gates on dispatch's effective worktree mode, where ``allowed_paths`` with no
``context.worktree`` block means ``per_task``. Hand-written fixture labels (and copies of
``examples/run``) that are claimed directly, with no dispatch, declare ``worktree: none``
here, which is what they always meant: work in the shared checkout.
"""
import json
import os

NO_WORKTREE = {"mode": "none", "branch": None, "base": None, "worktree_reason": "test fixture: claimed in the shared checkout"}


def no_worktree(label: dict) -> dict:
    """Declare ``worktree: none`` on a label that has no worktree block; returns the label."""
    context = label.setdefault("context", {})
    if not context.get("worktree"):
        context["worktree"] = dict(NO_WORKTREE)
    return label


def declare_no_worktree(run_dir) -> None:
    """Rewrite every ``labels/*.json`` under ``run_dir`` with no worktree block to ``none``."""
    labels = os.path.join(str(run_dir), "labels")
    for name in sorted(os.listdir(labels)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(labels, name)
        with open(path, encoding="utf-8") as handle:
            label = json.load(handle)
        if (label.get("context") or {}).get("worktree"):
            continue
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(no_worktree(label), handle)
