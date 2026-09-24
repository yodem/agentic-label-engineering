import json
import os
import subprocess

from ale import events as E
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(repo), check=True, capture_output=True)
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["change.txt"],
                    "depends_on": [], "worktree": {"mode": "per_task", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 0
    worktree = run / "wt" / "T1"
    (worktree / "change.txt").write_text("verified\n")
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("claimed", "run-1", 2, "T1", "worker", 1))
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1,
                                             summary="done"))
    assert main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(worktree), "--base", "HEAD"]) == 0
    accepted = [event for event in E.read_events(str(events)) if event["type"] == "accepted"][-1]
    assert accepted["evidence"]["tree"]
    return repo, run, roster, worktree, events


def test_integrate_refuses_tree_changed_after_acceptance(tmp_path, capsys):
    repo, run, roster, worktree, _ = _setup(tmp_path)
    (worktree / "change.txt").write_text("changed after verify\n")

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 1
    message = capsys.readouterr().err
    assert "verified tree" in message and "integration tree" in message
    assert "ale reopen" in message and "verify again" in message


def test_integrate_succeeds_when_verified_tree_is_unchanged(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path)

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 0
    assert (repo / "change.txt").read_text() == "verified\n"
    assert E.read_events(str(events))[-1]["type"] == "integrated"
    assert not worktree.exists()


def _head(path):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(path), text=True).strip()


def test_refused_integrate_leaves_the_worktree_uncommitted(tmp_path):
    repo, run, roster, worktree, _ = _setup(tmp_path)
    before = _head(worktree)
    (worktree / "change.txt").write_text("changed after verify\n")
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 1
    assert _head(worktree) == before
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(worktree), text=True)
    assert "change.txt" in status


def test_verify_base_still_catches_a_committed_path_outside_allowed(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path)
    # Reopen, then commit a file outside allowed_paths inside the task worktree.
    assert main(["reopen", "--task", "T1", "--reason", "recheck", "--run-dir", str(run),
                 "--roster", str(roster)]) == 0
    (worktree / "stray.txt").write_text("outside\n")
    subprocess.run(["git", "add", "stray.txt"], cwd=str(worktree), check=True)
    subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "stray"],
                   cwd=str(worktree), check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=str(worktree), text=True).strip()
    assert main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(worktree), "--base", base]) == 1
    assert E.read_events(str(events))[-1]["type"] != "accepted"


def test_verify_outside_the_task_worktree_records_no_tree(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path)
    assert main(["reopen", "--task", "T1", "--reason", "recheck", "--run-dir", str(run),
                 "--roster", str(roster)]) == 0
    assert main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 0
    accepted = [event for event in E.read_events(str(events)) if event["type"] == "accepted"][-1]
    assert "tree" not in accepted["evidence"]
