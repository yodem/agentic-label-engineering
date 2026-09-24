import json
import os
import stat
import time

from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _label(task_id):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id,
        "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [],
                    "allowed_paths": [task_id + ".txt"], "depends_on": [],
                    "worktree": {"mode": "none", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }


def test_three_one_second_spawns_run_concurrently(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    for task_id in ("T1", "T2", "T3"):
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(_label(task_id)))
    roster = json.loads(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    roster["cost_gate"]["max_concurrent"] = 3
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(roster))
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\nsleep 1\nprintf '%s\\n' \"$1\"\n")
    spawn.chmod(spawn.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))

    started = time.monotonic()
    assert main(["dispatch", "--spawn", "--run-dir", str(run),
                 "--roster", str(roster_path)]) == 0
    elapsed = time.monotonic() - started

    assert elapsed < 2.5
    output = capsys.readouterr().out.splitlines()
    assert [os.path.basename(line) for line in output] == [
        "T1-executor-backend-1.json", "T2-executor-backend-2.json", "T3-executor-backend-3.json"]
