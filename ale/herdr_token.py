from __future__ import annotations

from typing import Dict


def token_text(task_id: str, label: dict, st: dict) -> str:
    """Return the compact, plain-text token shown for a task."""
    labels = label.get("labels", label)
    role = str(labels.get("role", "?"))
    tier = str(labels.get("model_tier", "?"))
    state = str(st.get("state", "planned"))
    attempt = st.get("attempt", 1)
    watch = label.get("watch", {})
    maximum = watch.get("max_attempts", 3)
    stuck = state in ("stuck", "stale") or bool(st.get("breaches_seen"))

    suffix = " %s %s/%s" % (state, attempt, maximum)
    if stuck:
        suffix += " \u26a0stuck"
    prefix = "%s " % task_id
    middle = "\u00b7%s" % tier
    available = 48 - len(prefix) - len(middle) - len(suffix)
    if available < 0:
        # The role is the intentionally lossy portion of the token.  Keep the
        # status and attempt information readable even for unusual IDs.
        role = ""
    else:
        role = role[:available]
    text = prefix + role + middle + suffix
    return text[:48]
