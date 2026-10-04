"""Regression tests for run-record defects that corrupted the feedback loop's inputs."""

import argparse
import json
import os
import subprocess

from ale import events as E
from ale import cli
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(cwd, *args):
    return subprocess.check_output(["git"] + list(args), cwd=str(cwd), text=True).strip()


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)
    (path / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=str(path), check=True)
    return path


def _run(tmp_path, allowed=("a.txt", "b.txt"), mode="per_task"):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S", "locality": "any"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": list(allowed),
                    "depends_on": [], "worktree": {"mode": mode, "branch": None, "base": None,
                                                      "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "test -d .", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
        "provenance": {"lane_reason": "bounded task"},
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    return run, str(roster)


def _spawned(run):
    return [event for event in E.read_events(str(run / "events.jsonl")) if event["type"] == "spawned"]


# Fix 2: the run dir, and so every recorded worktree path, is absolute.

def test_relative_run_dir_records_an_absolute_spawned_worktree(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    run, roster = _run(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALE_SPAWN_BIN", "/usr/bin/true")
    assert main(["init-run", "--run-dir", "run", "--roster", roster]) == 0
    assert main(["dispatch", "--spawn", "--run-dir", "run", "--roster", roster, "--cwd", str(repo)]) == 0

    worktree = _spawned(run)[-1]["worktree"]
    assert os.path.isabs(worktree)
    assert worktree == str(run / "wt" / "T1")


def test_resolve_run_dir_is_absolute_on_every_branch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._resolve_run_dir(argparse.Namespace(run_dir="run")) == str(tmp_path / "run")
    monkeypatch.setenv("ALE_RUN_DIR", "env-run")
    assert cli._resolve_run_dir(argparse.Namespace(run_dir=None, run_id=None)) == str(tmp_path / "env-run")
