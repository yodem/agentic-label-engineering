from __future__ import annotations

import fnmatch
import subprocess
from typing import Callable, List

TAIL = 300


def _want(expect: str) -> int:
    return 0 if expect == "exit0" else int(expect.split(":", 1)[1])


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


def paths_within(changed: List[str], allowed: List[str]) -> List[str]:
    return [p for p in changed if not any(fnmatch.fnmatch(p, g) for g in allowed)]
