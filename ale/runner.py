from __future__ import annotations


def next_actions(state: dict, labels: dict, events: list) -> list:
    tasks = state.get("tasks", {})
    actions = []
    for task_id in sorted(tasks):
        status = tasks[task_id]
        is_fix = bool(labels.get(task_id, {}).get("fixes"))
        submitted_indexes = [index for index, event in enumerate(events)
                             if event.get("task_id") == task_id and event.get("type") == "submitted"]
        last_submit = submitted_indexes[-1] if submitted_indexes else None
        last_result = max((index for index, event in enumerate(events)
                           if event.get("task_id") == task_id
                           and event.get("type") in ("verified", "accepted", "rejected")), default=-1)
        last_rejection = max((index for index, event in enumerate(events)
                              if event.get("task_id") == task_id and event.get("type") == "rejected"), default=-1)
        last_fix_accept = max((index for index, event in enumerate(events)
                               if event.get("type") == "accepted"
                               and labels.get(event.get("task_id"), {}).get("fixes") == task_id), default=-1)
        needs_verification = last_submit is not None and (
            last_result < last_submit or (last_fix_accept > last_rejection and last_result < last_fix_accept))
        if status.get("state") == "submitted" and needs_verification:
            actions.append(("verify", task_id))
        elif status.get("state") == "accepted" and not status.get("integrated") and not is_fix:
            actions.append(("integrate", task_id))
        elif status.get("state") in ("rejected", "fixing"):
            if is_fix:
                continue
            fixes = [key for key, label in labels.items() if label.get("fixes") == task_id]
            rejected_fixes = [fix_id for fix_id in fixes
                              if tasks.get(fix_id, {}).get("state") == "rejected"]
            if status.get("state") == "fixing" and not rejected_fixes:
                continue
            if len(fixes) < 2:
                actions.append(("fix", task_id))
            else:
                actions.append(("exhausted", task_id))
    return actions


def is_complete(state: dict, labels: dict) -> bool:
    tasks = state.get("tasks", {})
    for task_id, label in labels.items():
        task = tasks.get(task_id, {})
        if label.get("fixes"):
            parent = tasks.get(label["fixes"], {})
            if task.get("state") != "accepted" or not parent.get("integrated"):
                return False
        elif task.get("state") != "accepted" or not task.get("integrated"):
            return False
    return bool(labels)
