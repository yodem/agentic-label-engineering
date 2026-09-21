"""Build, render, and validate labels embedded in plan documents."""

from __future__ import annotations

import copy
import json
import re
from typing import Dict, List, Tuple

from .labeling.merge import LaneVoteError
from .planparse import parse_plan
from .roster import resolve


class BakeError(ValueError):
    """Raised when an embedded label block is malformed or incomplete."""


_OPEN = re.compile(r"^```ale-label\s*$")
_FENCE = re.compile(r"^\s*([`~]{3,})(.*)$")
_GLOB = re.compile(r"[*?\[]")


def _fence_states(lines: List[str]) -> List[Tuple[str, int]]:
    state = None
    result = []
    for line in lines:
        result.append(state)
        match = _FENCE.match(line)
        if not match:
            continue
        marker, suffix = match.groups()
        suffix = suffix.strip()
        char = marker[0]
        if state is None:
            state = (char, len(marker))
        elif char == state[0] and len(marker) >= state[1] and not suffix:
            state = None
    return result


def _line_open(line: str) -> bool:
    return bool(_OPEN.match(line))


def _line_close(line: str) -> bool:
    return bool(re.match(r"^```\s*$", line))


def _vote(votes: dict, field: str):
    source = votes.get(field)
    if source is None and isinstance(votes.get("labels"), dict):
        source = votes["labels"].get(field)
    if isinstance(source, dict):
        return source.get("value"), source
    return source, {"by": "rule", "confidence": 1.0, "votes": []}


def _allowed_path(path: str) -> str:
    if path.endswith("/"):
        return path + "*"
    if _GLOB.search(path) or "." in path.rsplit("/", 1)[-1]:
        return path
    return path + "/*"


def _provenance(vote: dict) -> dict:
    return {
        "by": vote.get("by", "rule"),
        "confidence": vote.get("confidence", 1.0),
        "votes": copy.deepcopy(vote.get("votes", [])),
    }


def skeleton_label(task: dict, run_id: str, votes: dict) -> dict:
    """Create a complete label skeleton from a parsed task and merged votes."""
    if "lane" in votes:
        raise LaneVoteError("lane is answered by the planner with lane_reason and is never voted on")

    roster = votes.get("roster") or votes.get("_roster") or task.get("roster") or {}
    role, role_vote = _vote(votes, "role")
    effort, effort_vote = _vote(votes, "effort")
    risk, risk_vote = _vote(votes, "risk")
    model_tier, tier_vote = _vote(votes, "model_tier")

    if model_tier is None:
        for row in roster.get("routing", []):
            if row.get("role") in (role, "*"):
                model_tier = row.get("model_tier")
                break

    allowed_paths = [_allowed_path(path) for path in task.get("files", [])]
    acceptance = [
        {"id": "A%d" % index, "cmd": command, "expect": "exit0"}
        for index, command in enumerate(task.get("commands", [])[:5], 1)
    ]
    routing = {"executor": None, "model": None, "resolved_from": None}
    if roster and role and model_tier:
        try:
            resolved = resolve(roster, role, model_tier)
            routing = {"executor": resolved["executor"], "model": resolved["model"],
                       "resolved_from": "roster"}
        except Exception:
            pass

    label = {
        "schema_version": "1.0",
        "run_id": run_id,
        "task_id": task["task_id"],
        "title": task["title"],
        "labels": {"role": role, "model_tier": model_tier, "lane": None,
                    "risk": risk, "effort": effort},
        "routing": routing,
        "context": {
            "spec_path": task.get("spec_path") or "plan",
            "pointers": list(task.get("pointers", [])),
            "allowed_paths": allowed_paths,
            "depends_on": list(task.get("depends_on", [])),
            "worktree": {"mode": "per_task" if allowed_paths else "none",
                          "branch": None, "base": None, "worktree_reason": None},
        },
        "acceptance": acceptance,
        "provenance": {"role": _provenance(role_vote),
                       "effort": _provenance(effort_vote),
                       "risk": _provenance(risk_vote),
                       "model_tier": _provenance(tier_vote),
                       "lane_reason": None},
        "assignments": [{"kind": "executor", "role": role, "model_tier": model_tier,
                         "executor": None, "trigger": "ready"}],
    }
    return label


def render_block(label: dict) -> str:
    return "```ale-label\n%s\n```\n" % json.dumps(label, sort_keys=True, indent=1)


def extract_blocks(text: str) -> List[Tuple[int, dict]]:
    lines = text.splitlines()
    states = _fence_states(lines)
    blocks = []
    index = 0
    while index < len(lines):
        if states[index] is not None or not _line_open(lines[index]):
            index += 1
            continue
        opening_line = index + 1
        index += 1
        content = []
        while index < len(lines) and not _line_close(lines[index]):
            content.append(lines[index])
            index += 1
        if index == len(lines):
            raise BakeError("malformed ale-label block at line %d" % opening_line)
        try:
            label = json.loads("\n".join(content))
        except (TypeError, ValueError) as exc:
            raise BakeError("malformed ale-label JSON at line %d: %s" % (opening_line, exc))
        if not isinstance(label, dict):
            raise BakeError("malformed ale-label JSON at line %d: expected object" % opening_line)
        blocks.append((opening_line, label))
        index += 1
    return blocks


def _label_map(labels) -> Dict[str, dict]:
    if isinstance(labels, dict):
        return labels
    return {label["task_id"]: label for label in labels}


def bake(text: str, labels) -> str:
    label_map = _label_map(labels)
    tasks = parse_plan(text)
    lines = text.splitlines(keepends=True)
    states = _fence_states([line.rstrip("\r\n") for line in lines])
    crlf = text.count("\r\n") > text.count("\n") - text.count("\r\n")
    newline = "\r\n" if crlf else "\n"
    starts = [task["line"] - 1 for task in tasks]
    for position in range(len(tasks) - 1, -1, -1):
        task = tasks[position]
        label = label_map.get(task["task_id"])
        if label is None:
            continue
        start = starts[position]
        replacement = render_block(label).replace("\n", newline).splitlines(keepends=True)
        block_start = start + 1
        if (block_start < len(lines) and states[block_start] is None
                and _line_open(lines[block_start].rstrip("\r\n"))):
            block_end = block_start + 1
            while block_end < len(lines) and not _line_close(lines[block_end].rstrip("\r\n")):
                block_end += 1
            if block_end < len(lines):
                block_end += 1
                lines[block_start:block_end] = replacement
                continue
        if lines[start].endswith(("\n", "\r")):
            lines[block_start:block_start] = replacement
        else:
            lines[start] = lines[start] + newline
            lines[block_start:block_start] = replacement
    return "".join(lines)


def compile_plan(text: str) -> Dict[str, dict]:
    blocks = extract_blocks(text)
    labels = {}
    for _, label in blocks:
        task_id = label.get("task_id")
        if not task_id:
            raise BakeError("ale-label block has no task_id")
        if task_id in labels:
            raise BakeError("duplicate ale-label block for %s" % task_id)
        labels[task_id] = label
    for task in parse_plan(text):
        if task["task_id"] not in labels:
            raise BakeError("task %s has no ale-label block" % task["task_id"])
    return labels


def gaps(label: dict) -> List[str]:
    missing = []
    labels = label.get("labels", {})
    provenance = label.get("provenance", {})
    if labels.get("lane") is None:
        missing.append("lane")
    if not provenance.get("lane_reason"):
        missing.append("lane_reason")
    if len(label.get("acceptance", [])) < 2:
        missing.append("acceptance")
    role_provenance = provenance.get("role", {})
    rule_votes = role_provenance.get("votes")
    if labels.get("role") is None:
        missing.append("role")
    if not label.get("context", {}).get("allowed_paths"):
        missing.append("allowed_paths")
    return missing
