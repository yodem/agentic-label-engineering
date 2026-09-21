"""Parse common Claude Code and agent plan formats into task skeletons."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


class PlanParseError(ValueError):
    """Raised when a plan cannot provide a usable task list."""


_HEADING = re.compile(
    r"^#{2,4}\s+(?:Task|Step|Phase)\s+([0-9]+[a-z]?)\b[:. ](.*)$"
)
_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")
_CHECKBOX = re.compile(r"^- \[ \]\s+(.*)$")
_FENCE = re.compile(r"^\s*([`~]{3,})(.*)$")
_FILE_LINE = re.compile(r"^(?:\*\*Files:\*\*|Create:|Modify:|Test:)")
_BACKTICK = re.compile(r"`([^`]+)`")
_COMMAND = re.compile(r"Run:\s*`([^`]+)`")
_DEPENDENCY = re.compile(r"\b(?:depends\s+on|after)\s+Task\s+([0-9]+)\b", re.I)
_CONSUMES = re.compile(r"\bConsumes:\s*.*?\bTask\s+([0-9]+)\b", re.I)
_GIT_WRITE = re.compile(r"^git\s+(?:add|commit|push)(?:\s|$)", re.I)


def _fenced_lines(lines: List[str]) -> List[bool]:
    fence = None
    result = []
    for line in lines:
        result.append(fence is not None)
        match = _FENCE.match(line)
        if not match:
            continue
        marker, suffix = match.groups()
        if fence is None:
            fence = (marker[0], len(marker))
        elif marker[0] == fence[0] and len(marker) >= fence[1] and not suffix.strip():
            fence = None
    return result


def _candidates(lines: List[str], fenced: List[bool], kind: str) -> List[Tuple[int, str, Optional[str]]]:
    candidates = []
    for index, line in enumerate(lines):
        if fenced[index]:
            continue
        match = _HEADING.match(line) if kind == "heading" else None
        if kind == "numbered":
            match = _NUMBERED.match(line) if not line.startswith((" ", "\t")) else None
        elif kind == "checkbox":
            match = _CHECKBOX.match(line) if not line.startswith((" ", "\t")) else None
        if match:
            if kind == "heading":
                candidates.append((index, match.group(2).strip(), match.group(1)))
            else:
                candidates.append((index, match.group(2).strip(), None))
    return candidates


def _strip_range(path: str) -> str:
    return re.sub(r":\d+-\d+$", "", path)


def _is_command(command: str) -> bool:
    return not _GIT_WRITE.match(command.strip())


def _task_fields(lines: List[str], start: int, end: int) -> Tuple[List[str], List[str], List[str]]:
    files = []
    commands = []
    dependencies = []
    index = start + 1
    while index < end:
        line = lines[index]
        if _FILE_LINE.match(line):
            for raw_path in _BACKTICK.findall(line):
                path = _strip_range(raw_path)
                if ("/" in path or "." in path) and " " not in path and path not in files:
                    files.append(path)
        for match in _COMMAND.finditer(line):
            command = match.group(1).strip()
            if _is_command(command) and command not in commands:
                commands.append(command)
        if _DEPENDENCY.search(line) or _CONSUMES.search(line):
            dependencies.extend(match.group(1) for match in _DEPENDENCY.finditer(line))
            dependencies.extend(match.group(1) for match in _CONSUMES.finditer(line))

        fence = _FENCE.match(line)
        info = fence.group(2).strip().split()[0].lower() if fence and fence.group(2).strip() else ""
        if fence and info in ("bash", "sh"):
            previous = lines[index - 1] if index > start else ""
            if re.search(r"\b(?:Run|Verify)\b", previous, re.I):
                index += 1
                while index < end and not _FENCE.match(lines[index]):
                    if lines[index].strip():
                        command = lines[index].strip()
                        if _is_command(command) and command not in commands:
                            commands.append(command)
                        break
                    index += 1
        index += 1
    return files, commands, dependencies


def parse_plan(text: str) -> List[Dict[str, object]]:
    """Return task skeletons parsed from a plan."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fenced = _fenced_lines(lines)
    candidates = []
    for kind in ("heading", "numbered", "checkbox"):
        found = _candidates(lines, fenced, kind)
        if len(found) >= 2:
            candidates = found
            selected_kind = kind
            break
    else:
        raise PlanParseError("need at least 2 tasks")

    ids = []
    for position, (_, _, number) in enumerate(candidates, 1):
        task_id = "T{}".format(number if selected_kind == "heading" else position)
        if task_id in ids:
            raise PlanParseError("duplicate task id {}".format(task_id))
        ids.append(task_id)

    tasks = []
    for position, (start, title, _) in enumerate(candidates):
        end = candidates[position + 1][0] if position + 1 < len(candidates) else len(lines)
        files, commands, raw_dependencies = _task_fields(lines, start, end)
        known = {task_id: number for number, task_id in enumerate(ids)}
        depends_on = []
        for raw_id in raw_dependencies:
            dependency = "T{}".format(raw_id)
            if dependency in known and known[dependency] < position and dependency not in depends_on:
                depends_on.append(dependency)
        body = "\n".join(lines[start + 1:end]).strip()
        tasks.append({
            "task_id": ids[position],
            "title": title,
            "body": body,
            "files": files,
            "commands": commands,
            "depends_on": depends_on,
            "line": start + 1,
        })
    return tasks
