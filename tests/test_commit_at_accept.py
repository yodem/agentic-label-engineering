import json
import os
import subprocess

from ale import events as E
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(cwd, *args):
    return subprocess.check_output(["git"] + list(args), cwd=str(cwd), text=True).strip()


def _setup(tmp_path, write=True, check="true"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=str(repo), check=True)
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["change.txt"],
                    "depends_on": [], "worktree": {"mode": "per_task", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": check, "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", str(roster),
                 "--cwd", str(repo)]) == 0
    worktree = run / "wt" / "T1"
    if write:
        (worktree / "change.txt").write_text("verified\n")
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("claimed", "run-1", 2, "T1", "worker", 1))
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1, summary="done"))
    return repo, run, str(roster), worktree, events


def _verify(run, roster, worktree):
    return main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(worktree)])


def _accepted(events):
    return [event for event in E.read_events(str(events)) if event["type"] == "accepted"][-1]


def test_verify_in_the_task_worktree_commits_the_verified_tree(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path)
    base = _git(worktree, "rev-parse", "HEAD")
    assert _verify(run, roster, worktree) == 0
    evidence = _accepted(events)["evidence"]
    assert evidence["commit"] == _git(worktree, "rev-parse", "HEAD") != base
    assert evidence["tree"] == _git(worktree, "rev-parse", "HEAD^{tree}")
    assert _git(worktree, "log", "-1", "--format=%s") == "ale: T1 Task T1"
    assert _git(worktree, "status", "--porcelain", "--", "change.txt") == ""


def test_verify_with_nothing_changed_records_head_without_a_new_commit(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path, write=False)
    base = _git(worktree, "rev-parse", "HEAD")
    assert _verify(run, roster, worktree) == 0
    assert _accepted(events)["evidence"]["commit"] == base
    assert _git(worktree, "rev-parse", "HEAD") == base


def test_integrate_merges_the_committed_tip_without_a_second_commit(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path)
    assert _verify(run, roster, worktree) == 0
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert (repo / "change.txt").read_text() == "verified\n"
    subjects = _git(repo, "log", "--format=%s").splitlines()
    assert subjects.count("ale: T1 Task T1") == 1
    integrated = E.read_events(str(events))[-1]
    assert integrated["type"] == "integrated" and integrated["files"] == ["change.txt"]
    assert not worktree.exists()


def test_integrate_refuses_a_commit_added_after_acceptance(tmp_path, capsys):
    repo, run, roster, worktree, events = _setup(tmp_path)
    assert _verify(run, roster, worktree) == 0
    (worktree / "change.txt").write_text("sneaked in\n")
    subprocess.run(["git", "commit", "-qam", "late"], cwd=str(worktree), check=True)
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 1
    assert "verified tree" in capsys.readouterr().err
    assert not (repo / "change.txt").exists()


def test_integrate_refuses_uncommitted_changes_after_acceptance(tmp_path, capsys):
    repo, run, roster, worktree, events = _setup(tmp_path)
    assert _verify(run, roster, worktree) == 0
    (worktree / "change.txt").write_text("edited after accept\n")
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 1
    assert "verified tree" in capsys.readouterr().err


def test_commit_at_accept_fills_a_missing_identity(tmp_path, monkeypatch):
    repo, run, roster, worktree, events = _setup(tmp_path)
    original_run = subprocess.run

    def no_email(args, *positional, **kwargs):
        if list(args[:4]) == ["git", "config", "--get", "user.email"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return original_run(args, *positional, **kwargs)

    monkeypatch.setattr("ale.cli.subprocess.run", no_email)
    assert _verify(run, roster, worktree) == 0
    assert _git(worktree, "log", "-1", "--format=%ae") == "ale@localhost"


def test_a_fix_commits_in_the_parent_worktree_and_the_parent_integrates(tmp_path):
    repo, run, roster, worktree, events = _setup(tmp_path, check="grep -q verified change.txt")
    (worktree / "change.txt").write_text("wrong\n")
    assert _verify(run, roster, worktree) == 1
    assert main(["fix", "--task", "T1", "--run-dir", str(run), "--roster", roster]) == 0
    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster, "--cwd", str(repo)]) == 0
    (worktree / "change.txt").write_text("verified\n")
    for command in (["claim", "--task", "T1.fix1", "--agent", "fixer"],
                    ["submit", "--task", "T1.fix1", "--agent", "fixer", "--summary", "fixed"],
                    ["verify", "--task", "T1.fix1", "--cwd", str(worktree)]):
        assert main(command + ["--run-dir", str(run), "--roster", roster]) == 0
    assert _git(worktree, "log", "-1", "--format=%s").startswith("ale: T1.fix1 ")
    assert _verify(run, roster, worktree) == 0
    assert _accepted(events)["evidence"]["commit"] == _git(worktree, "rev-parse", "HEAD")
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert (repo / "change.txt").read_text() == "verified\n"
