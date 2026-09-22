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
_COMPACT_KEYS = ("task_id", "title", "labels", "lane_reason", "acceptance", "allowed_paths",
                 "depends_on", "worktree", "assignments", "fixes", "spec_path", "pointers",
                 "watch", "milestone")
_LABEL_KEYS = ("role", "model_tier", "lane", "risk", "effort")
_ASSIGNMENT_KEYS = ("kind", "role", "model_tier", "executor", "trigger")
_ACCEPTANCE_KEYS = ("id", "cmd", "expect", "manual")
_WORKTREE_KEYS = ("mode", "worktree_reason")
_WATCH_KEYS = ("heartbeat_timeout_s", "stuck_after_s", "max_duration_s", "budget_tokens", "max_attempts")


def _check_compact_keys(task_id: str, value: dict, allowed: Tuple[str, ...], location: str) -> None:
    for key in value:
        if key not in allowed:
            raise BakeError("%s: unknown key '%s' in %s; expected one of %s" %
                            (task_id, key, location, ", ".join(allowed)))


def _validate_compact_block(compact: dict) -> None:
    task_id = compact.get("task_id") or "<unknown task>"
    _check_compact_keys(task_id, compact, _COMPACT_KEYS, "ale-label block")
    labels = compact.get("labels")
    if isinstance(labels, dict):
        _check_compact_keys(task_id, labels, _LABEL_KEYS, "labels")
    assignments = compact.get("assignments")
    if isinstance(assignments, list):
        for assignment in assignments:
            if isinstance(assignment, dict):
                _check_compact_keys(task_id, assignment, _ASSIGNMENT_KEYS, "assignment")
    acceptance = compact.get("acceptance")
    if isinstance(acceptance, list):
        for entry in acceptance:
            if isinstance(entry, dict):
                _check_compact_keys(task_id, entry, _ACCEPTANCE_KEYS, "acceptance entry")
    worktree = compact.get("worktree")
    if isinstance(worktree, dict):
        _check_compact_keys(task_id, worktree, _WORKTREE_KEYS, "worktree")
    watch = compact.get("watch")
    if isinstance(watch, dict):
        _check_compact_keys(task_id, watch, _WATCH_KEYS, "watch")


def validate_compact_blocks(text: str) -> None:
    """Raise for unknown fields in any compact label blocks in a plan."""
    for _, compact in extract_blocks(text):
        _validate_compact_block(compact)


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
    if not all(key in label for key in ("acceptance", "context", "assignments", "provenance")):
        return "```ale-label\n%s\n```\n" % json.dumps(label, sort_keys=True, indent=1)
    labels = label.get("labels", {})
    context = label.get("context", {})
    worktree = context.get("worktree") or {}
    compact_labels = {key: labels.get(key) for key in ("role", "model_tier", "risk", "effort", "lane")}
    worktree_value = worktree.get("mode")
    if worktree_value == "shared":
        worktree_value = {"mode": "shared", "worktree_reason": worktree.get("worktree_reason")}
    values = [
        ("task_id", label.get("task_id")),
        ("title", label.get("title")),
        ("labels", compact_labels),
        ("lane_reason", label.get("provenance", {}).get("lane_reason")),
        ("acceptance", label.get("acceptance", [])),
        ("allowed_paths", context.get("allowed_paths", [])),
        ("depends_on", context.get("depends_on", [])),
        ("worktree", worktree_value),
        ("assignments", label.get("assignments", [])),
    ]
    if label.get("fixes") is not None:
        values.append(("fixes", label["fixes"]))
    if context.get("spec_path") and context.get("spec_path") != "plan":
        values.append(("spec_path", context["spec_path"]))
    if context.get("pointers"):
        values.append(("pointers", context["pointers"]))
    if label.get("watch"):
        values.append(("watch", label["watch"]))
    if label.get("milestone") is not None:
        values.append(("milestone", label["milestone"]))

    def compact(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    lines = ["```ale-label", "{"]
    for index, (key, value) in enumerate(values):
        comma = "," if index < len(values) - 1 else ""
        if key in ("acceptance", "assignments"):
            lines.append(' "%s": [' % key)
            for item_index, item in enumerate(value):
                item_comma = "," if item_index < len(value) - 1 else ""
                lines.append("  %s%s" % (compact(item), item_comma))
            lines.append(" ]%s" % comma)
        else:
            lines.append(" %s%s" % (json.dumps(key), ": " + compact(value) + comma))
    lines.extend(["}", "```", ""])
    return "\n".join(lines)


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


def compile_plan(text: str, run_id: str = "run-1", provenance: dict = None) -> Dict[str, dict]:
    blocks = extract_blocks(text)
    for _, compact in blocks:
        _validate_compact_block(compact)
    parsed_tasks = {task["task_id"]: task for task in parse_plan(text)}
    labels = {}
    for _, compact in blocks:
        label = {
            "schema_version": "1.0", "run_id": run_id,
            "task_id": compact.get("task_id"), "title": compact.get("title"),
            "labels": dict(compact.get("labels") or {}),
            "routing": {"executor": None, "model": None, "resolved_from": None},
            "context": {"spec_path": compact.get("spec_path", "plan"),
                         "pointers": list(compact.get("pointers", [])),
                         "allowed_paths": list(compact.get("allowed_paths", [])),
                         "depends_on": list(compact.get("depends_on", [])),
                         "spec_text": re.sub(r"\n?```ale-label\s*\n.*?\n```", "",
                                             parsed_tasks.get(compact.get("task_id"), {}).get("body", ""),
                                             flags=re.DOTALL).strip()[:4000]},
            "acceptance": list(compact.get("acceptance", [])),
            "assignments": list(compact.get("assignments", [])),
            "provenance": {},
        }
        worktree = compact.get("worktree")
        if isinstance(worktree, dict):
            mode = worktree.get("mode")
            reason = worktree.get("worktree_reason")
        else:
            mode, reason = worktree, None
        label["context"]["worktree"] = {"mode": mode, "branch": None, "base": None,
                                          "worktree_reason": reason}
        spec_text = label["context"].get("spec_text", "")
        if not spec_text:
            label["context"].pop("spec_text", None)
        if compact.get("fixes") is not None:
            label["fixes"] = compact["fixes"]
        if compact.get("watch") is not None:
            label["watch"] = compact["watch"]
        if compact.get("milestone") is not None:
            label["milestone"] = compact["milestone"]
        sidecar = (provenance or {}).get(label["task_id"], {})
        if provenance is not None and label["task_id"] in provenance:
            if isinstance(sidecar.get("provenance"), dict):
                label["provenance"] = copy.deepcopy(sidecar["provenance"])
            else:
                label["provenance"] = copy.deepcopy(sidecar)
            if isinstance(sidecar.get("routing"), dict):
                label["routing"] = copy.deepcopy(sidecar["routing"])
        else:
            for field in ("role", "model_tier", "risk", "effort"):
                label["provenance"][field] = copy.deepcopy(sidecar.get(field) or {"by": "default"})
            label["provenance"]["lane_reason"] = sidecar.get("lane_reason")
            for field in ("acceptance", "allowed_paths", "depends_on", "assignments", "worktree"):
                if field not in label["provenance"]:
                    value = compact.get(field)
                    label["provenance"][field] = {"by": "planner" if value else "default"}
        # The block is what a human edits: a lane_reason written there wins over any sidecar value.
        if compact.get("lane_reason"):
            label["provenance"]["lane_reason"] = compact["lane_reason"]
        for field in ("role", "model_tier", "risk", "effort", "lane"):
            if field not in label["labels"]:
                label["labels"][field] = None
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
