"""The suite-wide spawn guard (root conftest.py): no test starts a real executor by accident.

Every test runs with ``ALE_SPAWN_BIN`` set to a no-op stand-in that logs its call and exits 0,
so ``ale dispatch --spawn`` (and helpers built on it) never reaches ``bin/ale-spawn`` and a real
harness. A test that must run the real launcher opts in with ``@pytest.mark.real_spawn``.
"""
import json
import os
import subprocess

import pytest

from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hits(guard):
    try:
        with open(guard["log"], encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines() if line.strip()][guard["before"]:]
    except OSError:
        return []


def test_spawn_bin_is_the_noop_stand_in(spawn_guard):
    assert spawn_guard is not None
    assert os.environ["ALE_SPAWN_BIN"] == spawn_guard["bin"]
    assert os.access(spawn_guard["bin"], os.X_OK)
    proc = subprocess.run([spawn_guard["bin"], "request.json"], capture_output=True, text=True)
    assert proc.returncode == 0
    hits = _hits(spawn_guard)
    assert len(hits) == 1 and "test_spawn_bin_is_the_noop_stand_in" in hits[0] and "request.json" in hits[0]


@pytest.mark.real_spawn
def test_a_real_spawn_test_gets_no_stand_in(spawn_guard):
    assert spawn_guard is None
    assert "ALE_SPAWN_BIN" not in os.environ


def test_a_dispatch_spawn_without_a_spawn_bin_reaches_only_the_stand_in(tmp_path, spawn_guard):
    """A headless claude task dispatched with --spawn: the stand-in gets the request, no harness runs."""
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": [], "depends_on": [],
                    "worktree": {"mode": "none", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster",
                 os.path.join(ROOT, "examples", "roster.json"), "--cwd", str(tmp_path)]) == 0
    hits = _hits(spawn_guard)
    assert len(hits) == 1 and hits[0].endswith(".json")
    assert "T1-executor-backend-1" in hits[0]


def test_an_unmarked_test_in_a_fresh_suite_gets_the_stand_in(pytester):
    with open(os.path.join(ROOT, "conftest.py"), encoding="utf-8") as handle:
        pytester.makeconftest(handle.read())
    pytester.makepyfile(test_inner="""
import os
import pytest

def test_unmarked():
    assert os.path.basename(os.environ["ALE_SPAWN_BIN"]) == "ale-spawn-noop"

@pytest.mark.real_spawn
def test_marked():
    assert "ALE_SPAWN_BIN" not in os.environ
""")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
