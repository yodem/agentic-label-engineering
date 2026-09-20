from __future__ import annotations

from typing import Dict, List, Optional

from .events import LIVE, TERMINAL
from .labelset import effective_watch


def _breach(task_id: Optional[str], name: str, attempt: Optional[int], detail: str) -> dict:
    return {"task_id": task_id, "breach": name, "attempt": attempt, "detail": detail}


def check(run_state: dict, labels: Dict[str, dict], roster: dict, now: float) -> List[dict]:
    out: List[dict] = []
    for tid in sorted(run_state["tasks"]):
        st = run_state["tasks"][tid]
        watch = effective_watch(labels[tid], roster)
        found: List[dict] = []
        state, attempt = st["state"], st["attempt"]
        expired = False
        if state in ("claimed", "working") and now - st["last_heartbeat_ts"] > watch["heartbeat_timeout_s"]:
            expired = True
            found.append(_breach(tid, "lease_expired", attempt,
                                 "no heartbeat for %ds" % int(now - st["last_heartbeat_ts"])))
        if state == "working" and not expired and now - st["step_changed_ts"] > watch["stuck_after_s"]:
            found.append(_breach(tid, "stuck", attempt,
                                 "step %r unchanged for %ds" % (st["last_step"], int(now - st["step_changed_ts"]))))
        if state in LIVE and now - st["started_ts"] > watch["max_duration_s"]:
            found.append(_breach(tid, "overrun", attempt, "running for %ds" % int(now - st["started_ts"])))
        if state not in TERMINAL and st["tokens"] > watch["budget_tokens"]:
            found.append(_breach(tid, "over_budget", attempt, "%d tokens > %d" % (st["tokens"], watch["budget_tokens"])))
        if state == "submitted" and now - st["submitted_ts"] > watch["heartbeat_timeout_s"]:
            found.append(_breach(tid, "unverified", attempt,
                                 "submitted %ds ago with no verdict" % int(now - st["submitted_ts"])))
        if state == "input-required":
            found.append(_breach(tid, "input_required", attempt, st["waiting_on"] or ""))
        if state not in TERMINAL and st["rejections"] >= 2:
            found.append(_breach(tid, "rejected_twice", attempt, st["last_reject_reason"] or ""))
        if state == "rejected" and attempt > watch["max_attempts"]:
            found.append(_breach(tid, "attempts_exhausted", attempt, "%d attempts used" % (attempt - 1)))
        seen = {(b, a) for b, a in st["breaches_seen"]}
        out.extend(b for b in found if (b["breach"], b["attempt"]) not in seen)
    cap = roster["cost_gate"]["max_run_budget_tokens"]
    if run_state["run"]["tokens"] > cap and "run_budget" not in run_state["run"]["breaches_seen"]:
        out.append(_breach(None, "run_budget", None, "%d tokens > %d" % (run_state["run"]["tokens"], cap)))
    return out
