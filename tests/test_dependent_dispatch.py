import json
import os
import subprocess
import stat

from ale.cli import main
import ale.cli as cli
from ale.events import make_event, read_events
from ale.dispatch import held_for_integration


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_dependency_on_fix_task_does_not_hold_dependent():
    labels = {
        "F1": {"fixes": "T1"},
        "T2": {
            "context": {"depends_on": ["F1"], "allowed_paths": ["x"],
                        "worktree": {"mode": "per_task"}},
            "assignments": [{"kind": "executor", "trigger": "ready"}],
        },
    }
    state = {"tasks": {"F1": {"state": "accepted", "integrated": False}, "T2": {"state": "ready"}}}
    assert held_for_integration(state, labels) == []


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    source = os.path.join(ROOT, "examples", "roster.json")
    path.write_text(open(source, encoding="utf-8").read())
    return str(path)


def _label(task_id, mode="per_task", paths=None, depends=None):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id,
        "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [],
                    "allowed_paths": paths or [task_id.lower() + ".txt"],
                    "depends_on": depends or [],
                    "worktree": {"mode": mode, "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }


def _run(tmp_path, labels):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    for task_id, label in labels.items():
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(label))
    return run


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


def _dispatch(run, roster, *args):
    return main(["dispatch", *args, "--run-dir", str(run), "--roster", roster])


def test_dependent_is_held_then_spawned_from_integrated_head(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    labels = {"T1": _label("T1", paths=["t1.txt"]),
              "T2": _label("T2", paths=["t2.txt"], depends=["T1"])}
    run = _run(tmp_path, labels)
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert _dispatch(run, roster, "--spawn", "--cwd", str(repo)) == 0
    capsys.readouterr()
    worktree = run / "wt" / "T1"
    (worktree / "t1.txt").write_text("integrated dependency\n")
    events_path = run / "events.jsonl"
    events = read_events(str(events_path))
    spawn = events[0]
    with events_path.open("a") as handle:
        for event in [
            make_event("claimed", "run-1", 2, "T1", "agent", 1),
            make_event("submitted", "run-1", 3, "T1", "agent", 1, summary="done"),
            make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True}),
        ]:
            handle.write(json.dumps(event) + "\n")

    assert _dispatch(run, roster, "--spawn", "--cwd", str(repo)) == 0
    held = capsys.readouterr()
    assert "holding T2: dependency T1 is accepted but not integrated" in held.err
    events = read_events(str(events_path))
    assert not any(event.get("task_id") == "T2" and event["type"] in ("spawned", "released")
                   for event in events)

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert _dispatch(run, roster, "--spawn", "--cwd", str(repo)) == 0
    capsys.readouterr()
    dependent = run / "wt" / "T2"
    head = lambda path: subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(path), text=True).strip()
    assert head(dependent) == head(repo)
    assert (dependent / "t1.txt").read_text() == "integrated dependency\n"
    assert any(event.get("task_id") == "T2" and event["type"] == "spawned"
               for event in read_events(str(events_path)))


def _run_two_task_plan(tmp_path, monkeypatch, capsys, dependent_mode):
    repo = _git_repo(tmp_path)
    (repo / ".ale").mkdir()
    plan = repo / "PLAN.md"

    def block(task_id, depends):
        filename = task_id.lower() + ".txt"
        return """## Task %s: Task %s

**Files:** `%s`
Run: `test -f %s`
Run: `echo ok`
```ale-label
{
 "task_id":"%s", "title":"Task %s",
 "labels":{"role":"backend","model_tier":"cheap","risk":"low","effort":"S","lane":"inline"},
 "lane_reason":"bounded task", "acceptance":[{"id":"A1","cmd":"test -f %s","expect":"exit0"},{"id":"A2","cmd":"echo ok","expect":"exit0"}],
 "allowed_paths":["%s"], "depends_on":%s, "worktree":"%s",
 "assignments":[{"kind":"executor","role":"backend","model_tier":"cheap","executor":"claude-headless","trigger":"ready"}]
}
```
""" % (task_id[1:], task_id, filename, filename, task_id, task_id,
       filename, filename, json.dumps(depends), dependent_mode)

    plan.write_text(block("T1", []) + "\n" + block("T2", ["T1"]))
    fake_spawn = repo / "fake-spawn.py"
    fake_spawn.write_text("""#!/usr/bin/env python3
import json, sys
from ale.cli import main
request = json.load(open(sys.argv[1], encoding='utf-8'))
env = request['env']
task, agent = env['ALE_TASK'], env['ALE_AGENT']
open(task.lower() + '.txt', 'w').write(task)
assert main(['claim', '--task', task, '--agent', agent, '--run-dir', env['ALE_RUN_DIR'], '--roster', env['ALE_ROSTER']]) == 0
assert main(['submit', '--task', task, '--agent', agent, '--summary', 'done', '--run-dir', env['ALE_RUN_DIR'], '--roster', env['ALE_ROSTER']]) == 0
""")
    fake_spawn.chmod(fake_spawn.stat().st_mode | stat.S_IXUSR)
    subprocess.run(["git", "add", "PLAN.md", "fake-spawn.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "test plan"], cwd=str(repo), check=True, capture_output=True)
    monkeypatch.chdir(repo)
    assert main(["setup"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("ALE_SPAWN_BIN", str(fake_spawn))
    monkeypatch.setenv("PYTHONPATH", ROOT)
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    monkeypatch.delenv("ALE_ROSTER", raising=False)
    if dependent_mode == "none":
        monkeypatch.setattr("ale.cli.cmd_integrate", lambda args: cli.Ctx(args).emit(
            "integrated", args.task, None, 1, commit="test", files=[]) or 0)

    assert main(["run", str(plan), "--max-cycles", "20"]) == 0
    events = read_events(str(repo / ".ale" / "runs" / "PLAN" / "events.jsonl"))
    integrated = next(i for i, event in enumerate(events)
                      if event.get("task_id") == "T1" and event["type"] == "integrated")
    spawned = next(i for i, event in enumerate(events)
                   if event.get("task_id") == "T2" and event["type"] == "spawned")
    assert integrated < spawned
    assert [event["task_id"] for event in events if event["type"] == "integrated"] == ["T1", "T2"]


def test_run_integrates_before_dependent_dispatch(tmp_path, monkeypatch, capsys):
    _run_two_task_plan(tmp_path, monkeypatch, capsys, "none")


def test_run_does_not_deadlock_with_per_task_dependency(tmp_path, monkeypatch, capsys):
    _run_two_task_plan(tmp_path, monkeypatch, capsys, "per_task")
