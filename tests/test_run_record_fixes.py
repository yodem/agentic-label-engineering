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


# Fix 3: every spawn records base_commit and verify diffs the task worktree from it.

def _dispatched(tmp_path):
    repo = _repo(tmp_path / "repo")
    run, roster = _run(tmp_path)
    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("claimed", "run-1", 2, "T1", "worker", 1))
    return repo, run, roster, run / "wt" / "T1", events


def _submit_and_verify(run, roster, worktree, events):
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1, summary="done"))
    return main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(worktree)])


def _commit(worktree, name, text):
    (worktree / name).write_text(text)
    subprocess.run(["git", "add", name], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-qm", "add " + name], cwd=str(worktree), check=True)


def _of_type(events, kind):
    return [event for event in E.read_events(str(events)) if event["type"] == kind]


def test_every_spawn_records_its_base_commit(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)

    assert _spawned(run)[-1]["base_commit"] == _git(repo, "rev-parse", "HEAD")


def test_verify_lists_committed_and_uncommitted_changes_against_the_base(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "a.txt", "committed\n")
    (worktree / "b.txt").write_text("uncommitted\n")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    evidence = _of_type(events, "accepted")[-1]["evidence"]
    assert evidence["files"] == ["a.txt", "b.txt"]
    assert evidence["commit"] == _git(worktree, "rev-parse", "HEAD")
    assert evidence["tree"] == _git(worktree, "rev-parse", "HEAD^{tree}")
    assert _git(worktree, "status", "--porcelain", "--untracked-files=no") == ""


def test_verify_rejects_a_committed_change_outside_allowed_paths(tmp_path, capsys):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "secret.txt", "out of scope\n")

    assert _submit_and_verify(run, roster, worktree, events) == 1
    assert "path_violation: secret.txt" in capsys.readouterr().err


def test_verify_with_all_changes_committed_accepts_without_a_new_commit(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "a.txt", "committed\n")
    head = _git(worktree, "rev-parse", "HEAD")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    evidence = _of_type(events, "accepted")[-1]["evidence"]
    assert evidence["files"] == ["a.txt"]
    assert evidence["commit"] == head


def test_verify_with_nothing_changed_notes_the_base(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    base = _spawned(run)[-1]["base_commit"]

    assert _submit_and_verify(run, roster, worktree, events) == 0
    assert _of_type(events, "accepted")[-1]["evidence"]["files"] == []
    notes = [event["text"] for event in _of_type(events, "note")]
    assert notes == ["verify: no changed files found against base %s" % base[:12]]


def test_legacy_spawn_without_base_commit_diffs_from_head(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_record_base_commit", lambda *args, **kwargs: None)
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    assert "base_commit" not in _spawned(run)[-1]
    _commit(worktree, "a.txt", "committed\n")
    (worktree / "b.txt").write_text("uncommitted\n")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    assert _of_type(events, "accepted")[-1]["evidence"]["files"] == ["b.txt"]
