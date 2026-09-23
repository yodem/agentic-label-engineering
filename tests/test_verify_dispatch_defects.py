"""Regression tests for verification and dispatch defects."""

import json
import os
import subprocess

from ale.cli import main
from ale.events import read_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roster(tmp_path):
    roster = json.loads(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    roster["judge"] = {"default": "off"}
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster))
    return str(path)


def _label(task="T1", mode="none", executor="claude-headless"):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task, "title": "Task " + task,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["src/**"],
                    "depends_on": [], "worktree": {"mode": mode, "branch": None,
                    "base": None, "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                        {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The human selected the inline lane."},
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": executor, "trigger": "ready"}],
    }


def _run(tmp_path, label=None):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps(label or _label()))
    roster = _roster(tmp_path)
    common = ["--run-dir", str(run), "--roster", roster]
    return run, roster, common


def _submitted(run, common):
    assert main(["init-run"] + common) == 0
    assert main(["claim", "--task", "T1", "--agent", "a1"] + common) == 0
    assert main(["submit", "--task", "T1", "--agent", "a1", "--summary", "done"] + common) == 0


def test_verify_can_record_manual_rejection_reason_without_running_acceptance(tmp_path, monkeypatch):
    run, _roster_path, common = _run(tmp_path)
    _submitted(run, common)
    def should_not_run(*args, **kwargs):
        raise AssertionError("manual reject must not run acceptance commands")
    monkeypatch.setattr("ale.cli.V.run_acceptance", should_not_run)

    assert main(["verify", "--task", "T1", "--reject", "manual review failed"] + common) == 1
    events = read_events(str(run / "events.jsonl"))
    rejected = [event for event in events if event["type"] == "rejected"][-1]
    assert rejected["reason"] == "manual review failed"


def test_verify_ignores_setup_marker_in_allowed_path_check(tmp_path):
    repo = tmp_path / "repo"
    _git_repo(repo)
    run, _roster_path, common = _run(tmp_path)
    _submitted(run, common)
    (repo / ".ale-setup-done").write_text("completed\n")
    assert main(["verify", "--task", "T1", "--cwd", str(repo), "--base", "HEAD"] + common) == 0


def _git_repo(path, create=True):
    if create:
        path.mkdir()
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    (path / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(path), check=True, capture_output=True)


def test_dispatch_no_exec_creates_and_records_worktree_without_worker_spawn(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _git_repo(repo)
    run, _roster_path, common = _run(tmp_path, _label(mode="per_task"))
    original_run = subprocess.run
    def guarded_run(*args, **kwargs):
        if args[0][0].endswith("ale-spawn"):
            raise AssertionError("must not launch worker process")
        return original_run(*args, **kwargs)
    monkeypatch.setattr("ale.cli.subprocess.run", guarded_run)

    assert main(["dispatch", "--no-exec", "--cwd", str(repo)] + common) == 0
    event = next(event for event in read_events(str(run / "events.jsonl")) if event["type"] == "spawned")
    assert event["worktree"] == str(run / "wt" / "T1")
    assert (run / "wt" / "T1").is_dir()


def test_claude_subagent_dispatch_creates_worktree(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _git_repo(repo)
    run, _roster_path, common = _run(tmp_path, _label(mode="per_task", executor="claude-subagent"))
    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    output = capsys.readouterr().out
    request = json.loads(output.strip())
    assert request["cwd"] == str(run / "wt" / "T1")
    assert "claude-subagent" in output
