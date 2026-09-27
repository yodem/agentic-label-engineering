import json
import os
import subprocess

from ale import events as E
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(cwd, *args):
    return subprocess.check_output(["git"] + list(args), cwd=str(cwd), text=True).strip()


def make_run(tmp_path):
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
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["t1.txt"], "depends_on": [],
                    "worktree": {"mode": "per_task", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    return repo, run, str(roster)


def ale(run, roster, *args):
    return main(list(args) + ["--run-dir", str(run), "--roster", roster])


def _worktree(repo, path, branch):
    subprocess.run(["git", "worktree", "add", "-q", str(path), "-b", branch, "HEAD"], cwd=str(repo), check=True)


def _register(run, roster, repo, path, branch, base, *extra):
    return ale(run, roster, "register-worktree", "--task", "T1", "--path", str(path), "--branch", branch,
               "--base", base, "--cwd", str(repo), *extra)


def test_register_worktree_emits_an_external_spawn_with_host(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    _worktree(repo, path, "ale/run-1/T1")
    base = git(repo, "rev-parse", "HEAD")
    assert _register(run, roster, repo, path, "ale/run-1/T1", base, "--host", "build-box") == 0
    printed = json.loads(capsys.readouterr().out)
    event = [e for e in E.read_events(str(run / "events.jsonl")) if e["type"] == "spawned"][-1]
    assert event["executor"] == "external" and event["assignment_kind"] == "executor"
    assert event["worktree"] == str(path) and event["branch"] == "ale/run-1/T1"
    assert event["base_commit"] == base and event["host"] == "build-box"
    assert event["agent_id"] is None and event["agent_id_minted"] == printed["agent_id"]


def test_registered_worktree_is_what_integrate_uses(tmp_path):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    _worktree(repo, path, "ale/run-1/T1")
    assert _register(run, roster, repo, path, "ale/run-1/T1", git(repo, "rev-parse", "HEAD")) == 0
    (path / "t1.txt").write_text("remote work\n")
    assert ale(run, roster, "claim", "--task", "T1", "--agent", "remote-1") == 0
    assert ale(run, roster, "submit", "--task", "T1", "--agent", "remote-1", "--summary", "done") == 0
    assert ale(run, roster, "verify", "--task", "T1", "--cwd", str(path)) == 0
    assert ale(run, roster, "integrate", "--task", "T1", "--cwd", str(repo)) == 0
    assert (repo / "t1.txt").read_text() == "remote work\n"


def test_register_worktree_refuses_a_path_outside_the_repository(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(other), check=True)
    assert _register(run, roster, repo, other, "main", git(repo, "rev-parse", "HEAD")) == 1
    assert "is not a worktree of the repository" in capsys.readouterr().err
    assert not (run / "events.jsonl").exists() or not any(
        e["type"] == "spawned" for e in E.read_events(str(run / "events.jsonl")))


def test_register_worktree_refuses_a_branch_mismatch_and_a_foreign_base(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    _worktree(repo, path, "ale/run-1/T1")
    head = git(repo, "rev-parse", "HEAD")
    assert _register(run, roster, repo, path, "ale/run-1/other", head) == 1
    assert "not ale/run-1/other" in capsys.readouterr().err
    assert _register(run, roster, repo, path, "ale/run-1/T1", "f" * 40) == 1
    assert "is not a commit" in capsys.readouterr().err


def test_register_worktree_rejects_an_unsafe_host_name(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    _worktree(repo, path, "ale/run-1/T1")
    assert _register(run, roster, repo, path, "ale/run-1/T1", git(repo, "rev-parse", "HEAD"),
                     "--host", "bad host;rm") == 2


def test_register_worktree_refuses_a_base_that_is_not_an_ancestor_of_the_branch(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    _worktree(repo, path, "ale/run-1/T1")
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=str(repo), check=True)
    (repo / "side.txt").write_text("side\n")
    subprocess.run(["git", "add", "side.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "side"], cwd=str(repo), check=True)
    side = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=str(repo), check=True)
    assert _register(run, roster, repo, path, "ale/run-1/T1", side) == 1
    assert "is not an ancestor of branch ale/run-1/T1" in capsys.readouterr().err
