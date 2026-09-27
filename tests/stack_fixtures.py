"""A git repo and a stacked A -> B -> C run for the stacked-task tests."""

import json
import os
import subprocess

from ale import events as E
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(cwd, *args):
    return subprocess.check_output(["git"] + list(args), cwd=str(cwd), text=True).strip()


def label(task_id, depends=None, stack=True):
    worktree = {"mode": "per_task", "branch": None, "base": None}
    if stack and depends:
        worktree["stack"] = True
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id, "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [],
                    "allowed_paths": [task_id.lower() + ".txt"],
                    "depends_on": depends or [], "worktree": worktree},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }


def make_run(tmp_path, labels):
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
    for task_id, value in labels.items():
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(value))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    return repo, run, str(roster)


def chain(tmp_path):
    """T1 <- T2 <- T3, each stacked on the one before."""
    return make_run(tmp_path, {"T1": label("T1"), "T2": label("T2", ["T1"]),
                               "T3": label("T3", ["T2"])})


def ale(run, roster, *args):
    return main(list(args) + ["--run-dir", str(run), "--roster", roster])


def dispatch(run, roster, repo):
    return ale(run, roster, "dispatch", "--no-exec", "--cwd", str(repo))


def spawned(run, task_id):
    return [event for event in E.read_events(str(run / "events.jsonl"))
            if event["type"] == "spawned" and event["task_id"] == task_id]


def work_and_accept(run, roster, task_id, text=None, attempt=1):
    """Write the task's file, claim, submit and verify in its own worktree."""
    worktree = run / "wt" / task_id
    (worktree / (task_id.lower() + ".txt")).write_text(text or (task_id + " work\n"))
    events = str(run / "events.jsonl")
    agent = "%s-agent-%d" % (task_id, attempt)
    if ale(run, roster, "claim", "--task", task_id, "--agent", agent) != 0:
        raise AssertionError("claim failed for %s" % task_id)
    assert ale(run, roster, "submit", "--task", task_id, "--agent", agent, "--summary", "done") == 0
    assert ale(run, roster, "verify", "--task", task_id, "--cwd", str(worktree)) == 0
    accepted = [event for event in E.read_events(events)
                if event["type"] == "accepted" and event["task_id"] == task_id][-1]
    return accepted["evidence"]["commit"]
