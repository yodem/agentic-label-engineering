from __future__ import annotations

import os
from typing import List, Optional

from .events import LIVE
from .verify import paths_within


# Claude Code 2.1.278 harness facts: these tools edit files; file_path is the
# target except NotebookEdit, which uses notebook_path. MultiEdit may carry
# several edits. Hook decisions remain data-only and never invoke the harness.
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_TARGET_KEYS = {"NotebookEdit": "notebook_path"}


def target_paths(tool_name: str, tool_input: dict) -> List[str]:
    if tool_name not in EDIT_TOOLS or not isinstance(tool_input, dict):
        return []
    paths: List[str] = []
    key = _TARGET_KEYS.get(tool_name, "file_path")
    value = tool_input.get(key)
    if isinstance(value, str):
        paths.append(value)
    for edit in tool_input.get("edits") or []:
        if not isinstance(edit, dict):
            continue
        value = edit.get("notebook_path" if tool_name == "NotebookEdit" else "file_path")
        if isinstance(value, str):
            paths.append(value)
    return paths


def _relative_path(path: str, project_root: str) -> str:
    root = os.path.abspath(project_root)
    if os.path.isabs(path):
        return os.path.relpath(os.path.abspath(path), root)
    return os.path.normpath(path)


def decide_pre_tool(binding: dict, label: dict, task_state: dict, tool_name: str,
                    tool_input: dict, project_root: str) -> dict:
    if task_state.get("owner") != binding.get("agent_id"):
        return {"action": "deny", "reason": "lease lost (ale exit 4 semantics); stop working and release the session"}
    paths = target_paths(tool_name, tool_input)
    if paths:
        relative = [_relative_path(path, project_root) for path in paths]
        violations = paths_within(relative, label.get("context", {}).get("allowed_paths", []))
        if violations:
            return {"action": "deny", "reason": "edit path outside allowed_paths: %s" % ", ".join(violations)}
    return {"action": "allow", "reason": "lease held"}


def decide_heartbeat(task_state: dict, now: float, throttle_s: float, tool_name: str,
                     tool_input: dict) -> Optional[dict]:
    last = task_state.get("last_heartbeat_ts")
    if last is not None and now - last < throttle_s:
        return None
    paths = target_paths(tool_name, tool_input)
    target = ", ".join(paths) if paths else "-"
    return {"step": ("auto: %s %s" % (tool_name, target))[:200], "files": paths}


def decide_stop(label: dict, task_state: dict, acceptance_result: dict,
                blocks_so_far: int, max_blocks: int) -> dict:
    if task_state.get("state") not in LIVE:
        return {"action": "none"}
    if acceptance_result.get("passed"):
        manual = acceptance_result.get("manual") or []
        summary = "Acceptance passed"
        if manual:
            summary += "; manual checks require sign-off: %s" % ", ".join(manual)
        return {"action": "submit", "summary": summary}
    manual_ids = set(acceptance_result.get("manual") or [])
    failures = [r for r in acceptance_result.get("results", [])
                if not r.get("ok") and r.get("id") not in manual_ids]
    reason = "; ".join("%s: %s" % (r.get("id", "unknown"), r.get("tail", "")) for r in failures)
    if blocks_so_far < max_blocks:
        return {"action": "block", "reason": reason or "acceptance failed"}
    return {"action": "input_required", "question": "Acceptance is still failing: %s" % (reason or "unknown failure")}


def session_context(label: dict, handoff_text: str, decisions_text: str) -> str:
    acceptance = []
    for item in label.get("acceptance", []):
        if "manual" in item:
            acceptance.append("- %s: manual — %s" % (item.get("id", "?"), item["manual"]))
        else:
            acceptance.append("- %s: %s (%s)" % (item.get("id", "?"), item.get("cmd", ""), item.get("expect", "")))
    rules = [
        "1. Work only on the bound task and its allowed paths.",
        "2. Keep the task owner lease; stop if the lease is lost.",
        "3. Record concise progress and modified files in heartbeats.",
        "4. Run every automated acceptance command before stopping.",
        "5. Leave manual acceptance items for reviewer sign-off.",
    ]
    text = "\n".join([
        "Task %s: %s" % (label.get("task_id", "?"), label.get("title", "")),
        "Allowed paths: %s" % ", ".join(label.get("context", {}).get("allowed_paths", [])),
        "\nAcceptance commands:", *acceptance,
        "\nExecutor rules:", *rules,
        "\nPrior handoff:\n%s" % (handoff_text or "none"),
        "\nDecisions:\n%s" % (decisions_text or "none"),
    ])
    return text[:6000]
