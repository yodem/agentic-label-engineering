from __future__ import annotations

import os
import re
import tempfile
from typing import List

_STATUS = {"accepted": "done", "input-required": "blocked", "failed": "failed", "canceled": "failed"}

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_safe_id(value) -> bool:
    if not isinstance(value, str):
        return False
    return bool(SAFE_ID.fullmatch(value))


def handoff_path(run_dir: str, task_id: str, agent_id: str, role: str = None) -> str:
    if not is_safe_id(task_id):
        raise ValueError("unsafe id: %r" % (task_id,))
    if not is_safe_id(agent_id):
        raise ValueError("unsafe id: %r" % (agent_id,))
    if role is None:
        return os.path.join(run_dir, "handoff", "%s.%s.md" % (task_id, agent_id))
    if not is_safe_id(role):
        raise ValueError("unsafe id: %r" % (role,))
    return os.path.join(run_dir, "handoff", "%s-%s-handoff.md" % (agent_id, role))


def _bullets(items: List[str]) -> str:
    return "\n".join("- %s" % i for i in items) if items else "- none"


def render(task_id: str, agent_id: str, label: dict, st: dict) -> str:
    status = _STATUS.get(st["state"], "in_progress")
    parts = [
        "# Handoff %s (%s)" % (task_id, label["title"]),
        "",
        "agent: %s" % agent_id,
        "attempt: %d" % st["attempt"],
        "state: %s" % st["state"],
        "status: %s" % status,
        "",
        "## Summary", st["summary"] or st["last_step"] or "none", "",
        "## Files modified", _bullets(st["files_modified"]), "",
        "## Completed", _bullets(st["steps"]), "",
        "## Pending", _bullets(st["pending"]), "",
        "## Next steps", _bullets(st["next_steps"][:3]), "",
        "## Waiting on", st["waiting_on"] or "none", "",
        "## Notes", _bullets(st["notes"]), "",
    ]
    return "\n".join(parts)


def write_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
