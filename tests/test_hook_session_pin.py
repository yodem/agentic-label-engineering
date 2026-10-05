"""An env-bound ALE hook acts only for the executor's own session.

A child ``claude`` session started from inside an executor (measured: a Jev bake-off
``claude -p`` in a temp dir) inherits ALE_TASK/ALE_AGENT/ALE_RUN_DIR. Before 0.4.4 its Stop
ran the task's acceptance in its own cwd and wrote an ``auto-stop-block`` note on the task,
and its PreToolUse applied the task's path guard to its own writes.
"""
import io
import json
import os
import shutil
import subprocess

import pytest

from ale.binding import read_pins
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def run(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    monkeypatch.setenv("ALE_HOME", str(tmp_path / "home"))
    worktree = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "-b", "ale/t/T01", str(worktree)], check=True)
    subprocess.run(["git", "-C", str(worktree), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    common = ["--run-dir", str(run_dir), "--roster", str(roster)]
    assert main(["init-run"] + common + ["--now", "0"]) == 0
    assert main(["register-worktree"] + common + ["--task", "T01", "--path", str(worktree),
                                                  "--branch", "ale/t/T01", "--base", "HEAD", "--cwd", str(worktree), "--now", "1"]) == 0
    assert main(["claim"] + common + ["--task", "T01", "--agent", "a1", "--now", "2"]) == 0
    for key, value in (("ALE_TASK", "T01"), ("ALE_AGENT", "a1"), ("ALE_RUN_DIR", str(run_dir)),
                       ("ALE_ROSTER", str(roster))):
        monkeypatch.setenv(key, value)
    return run_dir, worktree, elsewhere


def hook(monkeypatch, event, session, cwd, **extra):
    data = {"session_id": session, "hook_event_name": event, "cwd": str(cwd), "transcript_path": "missing.json"}
    data.update(extra)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    return main(["hook", event])


def failing_acceptance(run_dir):
    path = run_dir / "labels" / "T01.json"
    label = json.loads(path.read_text())
    label["acceptance"][0]["cmd"] = "false"
    path.write_text(json.dumps(label))


def notes(run_dir):
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    return [e.get("text", "") for e in events if e["type"] == "note"]


def outside_write(cwd):
    return {"tool_name": "Write", "tool_input": {"file_path": str(cwd / "outside.txt"), "content": "x"}}


def test_session_start_in_worktree_pins_the_executor_session(run, monkeypatch):
    run_dir, worktree, _ = run
    assert hook(monkeypatch, "session-start", "exec", worktree) == 0
    assert read_pins(str(run_dir), "T01", "a1") == ["exec"]


def test_foreign_session_stop_writes_no_note(run, monkeypatch, capsys):
    run_dir, worktree, elsewhere = run
    hook(monkeypatch, "session-start", "exec", worktree)
    failing_acceptance(run_dir)
    capsys.readouterr()
    assert hook(monkeypatch, "stop", "child", elsewhere) == 0
    assert capsys.readouterr().out == ""
    assert not any(text.startswith("auto-stop-block") for text in notes(run_dir))


def test_foreign_session_pre_tool_is_not_guarded(run, monkeypatch, capsys):
    run_dir, worktree, elsewhere = run
    hook(monkeypatch, "session-start", "exec", worktree)
    capsys.readouterr()
    assert hook(monkeypatch, "pre-tool", "child", elsewhere, **outside_write(elsewhere)) == 0
    assert "outside allowed_paths" not in "".join(capsys.readouterr())


def test_foreign_session_start_does_not_take_the_pin(run, monkeypatch):
    run_dir, worktree, elsewhere = run
    hook(monkeypatch, "session-start", "exec", worktree)
    hook(monkeypatch, "session-start", "child", elsewhere)
    assert read_pins(str(run_dir), "T01", "a1") == ["exec"]


def test_pinned_session_is_still_guarded_and_stop_still_blocks(run, monkeypatch, capsys):
    run_dir, worktree, _ = run
    hook(monkeypatch, "session-start", "exec", worktree)
    capsys.readouterr()
    assert hook(monkeypatch, "pre-tool", "exec", worktree, **outside_write(worktree)) == 2
    assert "outside allowed_paths" in capsys.readouterr().err
    failing_acceptance(run_dir)
    assert hook(monkeypatch, "stop", "exec", worktree) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert any(text.startswith("auto-stop-block") for text in notes(run_dir))


def test_new_session_inside_the_worktree_repins_and_is_honoured(run, monkeypatch, capsys):
    run_dir, worktree, _ = run
    hook(monkeypatch, "session-start", "exec", worktree)
    hook(monkeypatch, "session-start", "exec-after-clear", worktree)
    assert read_pins(str(run_dir), "T01", "a1") == ["exec", "exec-after-clear"]
    capsys.readouterr()
    assert hook(monkeypatch, "pre-tool", "exec-after-clear", worktree, **outside_write(worktree)) == 2
    assert "outside allowed_paths" in capsys.readouterr().err


def test_without_a_pin_every_env_bound_session_is_honoured(run, monkeypatch, capsys):
    run_dir, _, elsewhere = run
    failing_acceptance(run_dir)
    capsys.readouterr()
    assert hook(monkeypatch, "stop", "any", elsewhere) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_pinned_executor_that_cds_out_of_the_worktree_stays_guarded(run, monkeypatch, capsys):
    run_dir, worktree, elsewhere = run
    hook(monkeypatch, "session-start", "exec", worktree)
    capsys.readouterr()
    assert hook(monkeypatch, "pre-tool", "exec", elsewhere, **outside_write(elsewhere)) == 2
    assert "outside allowed_paths" in capsys.readouterr().err


def test_child_starting_inside_the_worktree_does_not_unbind_the_executor(run, monkeypatch, capsys):
    run_dir, worktree, elsewhere = run
    hook(monkeypatch, "session-start", "exec", worktree)
    hook(monkeypatch, "session-start", "child", worktree)
    assert read_pins(str(run_dir), "T01", "a1") == ["exec", "child"]
    capsys.readouterr()
    assert hook(monkeypatch, "pre-tool", "exec", elsewhere, **outside_write(elsewhere)) == 2
    assert "outside allowed_paths" in capsys.readouterr().err
