from __future__ import annotations

import json
import os
import re
from typing import Mapping, Optional

from . import handoff as H


_BINDING_KEYS = ("run_dir", "roster", "task_id", "agent_id")
_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")


def _normalise_id(value: str) -> str:
    value = _SANITIZE.sub("_", str(value))
    if not value or not value[0].isalnum():
        value = "x-" + value
    value = value[:64]
    if not H.is_safe_id(value):
        value = "x-id"
    return value


def binding_path(home: str, session_id: str, subagent: Optional[str]) -> str:
    session = _normalise_id(session_id)
    name = session
    if subagent is not None:
        name += "." + _normalise_id(subagent)
    return os.path.join(home, ".ale", "bindings", name + ".json")


def from_env(env: Mapping[str, str]) -> Optional[dict]:
    task_id = env.get("ALE_TASK")
    agent_id = env.get("ALE_AGENT")
    run_dir = env.get("ALE_RUN_DIR")
    if not task_id or not agent_id or not run_dir:
        return None
    if not H.is_safe_id(task_id) or not H.is_safe_id(agent_id):
        return None
    roster = env.get("ALE_ROSTER")
    if not roster:
        derived = os.path.normpath(os.path.join(run_dir, "../../roster.json"))
        roster = derived if os.path.exists(derived) else "roster.json"
    return {"run_dir": run_dir, "roster": roster, "task_id": task_id,
            "agent_id": agent_id, "source": "env"}


def _read_binding_file(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in _BINDING_KEYS):
        return None
    if not H.is_safe_id(value["task_id"]) or not H.is_safe_id(value["agent_id"]):
        return None
    return {"run_dir": value["run_dir"], "roster": value["roster"],
            "task_id": value["task_id"], "agent_id": value["agent_id"], "source": "file"}


def resolve(env: Mapping[str, str], home: str, session_id: str,
            subagent: Optional[str]) -> Optional[dict]:
    found = from_env(env)
    if found is not None:
        return found
    paths = []
    if subagent is not None:
        paths.append(binding_path(home, session_id, subagent))
    paths.append(binding_path(home, session_id, None))
    for path in paths:
        if os.path.exists(path):
            return _read_binding_file(path)
    return None
