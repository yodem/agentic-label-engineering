"""Render a task's verification evidence as deterministic markdown.

The input is the task's label and its reduced state; the output depends on
nothing else (no clock, no paths outside the evidence), so the same log always
renders the same bytes. Publish tooling uses it as a PR body or handoff file.
"""

from __future__ import annotations

from typing import List, Optional

TAIL_LINES = 5


def _cell(text: object) -> str:
    value = "" if text is None else str(text)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = value.replace("|", "\\|").replace("\r", "")
    return "<br>".join(line.rstrip() for line in value.split("\n")).strip()


def _code(text: object) -> str:
    value = "" if text is None else str(text).replace("\n", " ")
    fence = "``" if "`" in value else "`"
    pad = " " if fence == "``" else ""
    return "%s%s%s%s%s" % (fence, pad, value.replace("|", "\\|"), pad, fence)


def _expected(expect: Optional[str]) -> str:
    if expect == "exit0":
        return "exit 0"
    if isinstance(expect, str) and expect.startswith("exit:"):
        return "exit %s" % expect.split(":", 1)[1]
    return _cell(expect)


def _tail(text: object) -> str:
    lines = [line for line in ("" if text is None else str(text)).splitlines() if line.strip()]
    return _cell("\n".join(lines[-TAIL_LINES:]))


def render(task_id: str, label: dict, task_state: dict) -> str:
    """Markdown for one task. Raises ValueError when the task has no evidence."""
    evidence = task_state.get("evidence")
    if not evidence:
        raise ValueError("task %s has no verification evidence" % task_id)
    results = {item.get("id"): item for item in evidence.get("results") or []}
    manual = set(evidence.get("manual") or [])
    lines: List[str] = ["# Evidence: %s %s" % (task_id, _cell(label.get("title", ""))), ""]
    lines.append("- state: %s (attempt %s)" % (task_state.get("state"), task_state.get("attempt")))
    lines.append("- result: %s" % ("passed" if evidence.get("passed") else "failed"))
    if task_state.get("state") == "rejected" and task_state.get("last_reject_reason"):
        lines.append("- rejection: %s" % _cell(task_state["last_reject_reason"]))
    lines.append("- tree: %s" % (_code(evidence["tree"]) if evidence.get("tree") else "not pinned"))
    lines.append("- commit: %s" % (_code(evidence["commit"]) if evidence.get("commit") else "none"))
    lines.append("- sign-off: %s" % (_cell(evidence["signoff"]) if evidence.get("signoff") else "none"))
    lines += ["", "## Acceptance", "",
              "| id | requirement | command | expected | exit / ok | output (last %d lines) |" % TAIL_LINES,
              "| --- | --- | --- | --- | --- | --- |"]
    for item in label.get("acceptance") or []:
        acceptance_id = item.get("id", "")
        if "manual" in item:
            status = "manual" if acceptance_id in manual else "not run"
            lines.append("| %s | %s | manual | manual | %s |  |" % (
                _cell(acceptance_id), _cell(item["manual"]), status))
            continue
        result = results.get(acceptance_id)
        if result is None:
            outcome, tail = "not run", ""
        else:
            outcome = "%s / %s" % (result.get("exit"), "ok" if result.get("ok") else "FAIL")
            tail = _tail(result.get("tail"))
        lines.append("| %s | - | %s | %s | %s | %s |" % (
            _cell(acceptance_id), _code(item.get("cmd", "")), _expected(item.get("expect")), outcome, tail))
    lines += ["", "## Required commands", ""]
    required = evidence.get("required") or []
    if required:
        lines += ["| command | exit / ok | output (last %d lines) |" % TAIL_LINES, "| --- | --- | --- |"]
        for item in required:
            lines.append("| %s | %s / %s | %s |" % (_code(item.get("command", "")), item.get("exit"),
                                                     "ok" if item.get("ok") else "FAIL", _tail(item.get("output"))))
    else:
        lines.append("- none")
    lines += ["", "## Files changed", ""]
    files = evidence.get("files") or []
    lines += ["- %s" % _code(path) for path in files] or ["- none recorded"]
    if evidence.get("files_truncated"):
        lines.append("- and %d more" % (int(evidence.get("files_count") or len(files)) - len(files)))
    return "\n".join(lines) + "\n"
