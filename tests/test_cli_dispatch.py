import copy
import json
import os
import subprocess
import sys

from ale.cli import main
from ale.events import make_event, read_events
from ale.handoff import handoff_path


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    source = os.path.join(ROOT, "examples", "roster.json")
    roster = json.loads(open(source, encoding="utf-8").read())
    path.write_text(json.dumps(roster))
    return str(path)


def _label(task_id="T1", mode="none", paths=None, assignments=None, fixes=None):
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id,
        "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                    "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [],
                     "allowed_paths": paths or ["src/%s.py" % task_id], "depends_on": [],
                     "worktree": {"mode": mode, "branch": None, "base": None, "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The human selected the inline lane."},
        "assignments": assignments or [{"kind": "executor", "role": "backend",
                                         "model_tier": "standard", "executor": "claude-headless",
                                         "trigger": "ready"}],
    }
    if fixes:
        label["fixes"] = fixes
    return label


def _run(tmp_path, labels):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    for task_id, label in labels.items():
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(label))
    return run


def _dispatch_args(run, roster, *extra):
    return ["dispatch", *extra, "--run-dir", str(run), "--roster", roster]


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test-user"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(repo), check=True, capture_output=True)
    return repo


def test_dispatch_json_is_dry_and_prints_due_request(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label()})

    assert main(_dispatch_args(run, roster, "--json")) == 0
    request = json.loads(capsys.readouterr().out.strip())
    assert request["task_id"] == "T1"
    assert not (run / "events.jsonl").exists()


def test_spawn_dry_prints_argv_and_records_spawned(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label()})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    output = capsys.readouterr().out
    assert "ale-exec" in output and "claude" in output
    events = read_events(str(run / "events.jsonl"))
    assert [event["type"] for event in events] == ["spawned"]


def test_dispatch_is_idempotent_after_spawn(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label()})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    capsys.readouterr()
    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    assert capsys.readouterr().out == ""
    assert len(read_events(str(run / "events.jsonl"))) == 1


def test_failed_spawn_releases_assignment(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label()})
    label_path = run / "labels" / "T1.json"
    label = json.loads(label_path.read_text())
    label["assignments"][0]["executor"] = "unknown-executor"
    label_path.write_text(json.dumps(label))

    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    assert "unknown executor" in capsys.readouterr().err
    assert [event["type"] for event in read_events(str(run / "events.jsonl"))] == ["spawned", "released"]


def test_claude_subagent_is_printed_without_spawn_event(tmp_path, capsys):
    roster = _roster(tmp_path)
    assignment = [{"kind": "executor", "role": "backend", "model_tier": "standard",
                   "executor": "claude-subagent", "trigger": "ready"}]
    run = _run(tmp_path, {"T1": _label(assignments=assignment)})

    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    assert "claude-subagent" in capsys.readouterr().out
    assert not (run / "events.jsonl").exists()


def test_per_task_worktree_is_created_before_spawn(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    worktree = run / "wt" / "T1"
    assert worktree.exists()
    branch = subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"], text=True).strip()
    assert branch == "main" or branch == "master"
    event = read_events(str(run / "events.jsonl"))[0]
    assert event["worktree"] == str(worktree)
    assert event["branch"] == "ale/run-1/T1"


def test_fix_task_reuses_parent_worktree(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    parent = _label("T1", mode="per_task")
    fix = _label("T1.fix1", mode="per_task", fixes="T1")
    run = _run(tmp_path, {"T1": parent, "T1.fix1": fix})
    (run / "wt" / "T1").mkdir(parents=True)
    with open(run / "events.jsonl", "w") as handle:
        handle.write(json.dumps(make_event("spawned", "run-1", 1, "T1", None, 1,
                                           agent_id_minted="T1-executor-backend-1",
                                           assignment_kind="executor", executor="claude-headless",
                                           model="claude-sonnet-5", worktree=str(run / "wt" / "T1"),
                                           branch="ale/run-1/T1")) + "\n")
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    capsys.readouterr()
    event = read_events(str(run / "events.jsonl"))[-1]
    assert event["task_id"] == "T1.fix1"
    assert event["worktree"] == str(run / "wt" / "T1")


def test_none_mode_creates_no_worktree(tmp_path, monkeypatch):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="none")})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(tmp_path))) == 0
    assert not (run / "wt").exists()


def test_integrate_refuses_unaccepted_task(tmp_path):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="none")})

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(tmp_path)]) == 1


def test_integrate_merges_and_removes_worktree(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")
    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    worktree = run / "wt" / "T1"
    (worktree / "change.txt").write_text("change\n")
    subprocess.run(["git", "add", "change.txt"], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=str(worktree), check=True, capture_output=True)
    events_path = run / "events.jsonl"
    with events_path.open("a") as handle:
        handle.write(json.dumps(make_event("claimed", "run-1", 2, "T1", "agent", 1)) + "\n")
        handle.write(json.dumps(make_event("submitted", "run-1", 3, "T1", "agent", 1, summary="done")) + "\n")
        handle.write(json.dumps(make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True})) + "\n")

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert (repo / "change.txt").exists()
    assert not worktree.exists()
    events = read_events(str(events_path))
    assert events[-1]["type"] == "integrated"


def test_handoff_path_keeps_old_signature_and_supports_role(tmp_path):
    old = handoff_path(str(tmp_path), "T1", "agent")
    new = handoff_path(str(tmp_path), "T1", "agent", "backend")
    assert old.endswith("T1.agent.md")
    assert new.endswith("agent-backend-handoff.md")
