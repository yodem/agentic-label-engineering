from __future__ import annotations

from typing import Dict

from .events import append_event, make_event, read_events, reduce_run


class LeaseLost(Exception):
    pass


def try_claim(events_path: str, labels: Dict[str, dict], run_id: str, task_id: str,
              agent_id: str, now: float, max_attempts: int) -> bool:
    st = reduce_run(read_events(events_path), labels)["tasks"][task_id]
    if not st["claimable"] or st["attempt"] > max_attempts:
        return False
    append_event(events_path, make_event("claimed", run_id, now, task_id, agent_id, st["attempt"]))
    after = reduce_run(read_events(events_path), labels)["tasks"][task_id]
    return after["owner"] == agent_id


def owner_guard(run_state: dict, task_id: str, agent_id: str) -> None:
    owner = run_state["tasks"][task_id]["owner"]
    if owner != agent_id:
        raise LeaseLost("task %s is owned by %r, not %r" % (task_id, owner, agent_id))
