from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import subprocess
from typing import Callable, Dict, List

TAIL = 300


def _want(expect: str) -> int:
    return 0 if expect == "exit0" else int(expect.split(":", 1)[1])


def outside_cds(label: dict, cwd: str) -> List[tuple]:
    """(check id, path) for each command check that starts by cd-ing to an absolute path outside cwd."""
    root = os.path.realpath(cwd)
    found = []
    for item in label.get("acceptance", []):
        match = re.match(r"^\s*cd\s+(/\S+)", item.get("cmd", ""))
        if not match:
            continue
        target = match.group(1).rstrip(";&|")
        real = os.path.realpath(target)
        if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
            found.append((item["id"], target))
    return found


def run_acceptance(label: dict, cwd: str, run: Callable = subprocess.run, timeout: int = 600) -> dict:
    results, manual = [], []
    for item in label["acceptance"]:
        if "manual" in item:
            manual.append(item["id"])
            continue
        try:
            proc = run(item["cmd"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code, out = -1, "timeout"
        results.append({"id": item["id"], "exit": code, "ok": code == _want(item["expect"]), "tail": out[-TAIL:]})
    return {"passed": all(r["ok"] for r in results), "results": results, "manual": manual}


def paths_within(changed: List[str], allowed: List[str], denied: List[str] = None) -> List[str]:
    out = []
    denied = denied or []
    for p in changed:
        norm = posixpath.normpath(p)
        if any(fnmatch.fnmatch(norm, pattern) for pattern in denied):
            out.append(p)
        elif posixpath.isabs(norm) or norm == ".." or norm.startswith("../"):
            out.append(p)
        elif not any(fnmatch.fnmatch(norm, g) for g in allowed):
            out.append(p)
    return out


def run_required(commands: List[str], cwd: str, run: Callable = subprocess.run,
                 timeout: int = 600) -> List[dict]:
    results = []
    for command in commands:
        try:
            proc = run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            code = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code, output = -1, "timeout"
        results.append({"command": command, "exit": code, "ok": code == 0, "output": output[-TAIL:]})
    return results
