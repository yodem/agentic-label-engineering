import json
import os
import stat

import pytest

import ale.cli as cli
from ale.cli import _ale_dir, _plan_run_id, _resolve_roster, _resolve_run_dir, main
from ale.events import read_events


def test_ale_directory_is_nearest_within_git_root(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    nested = root / "a" / "b"
    (root / ".git").mkdir(parents=True)
    (root / ".ale").mkdir()
    (root / "a" / ".ale").mkdir(parents=True)
    nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(nested)
    assert _ale_dir() == str(root / "a" / ".ale")


def test_run_directory_uses_flag_sidecar_or_plan_stem(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".ale").mkdir()
    plan = repo / "PLAN.md"
    plan.write_text("plan")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    assert _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": "chosen"})()) == str(repo / ".ale/runs/chosen")
    assert _plan_run_id(str(plan), None) == "PLAN"
    (repo / "PLAN.md.ale-provenance.json").write_text(json.dumps({"run_id": "from-sidecar"}))
    assert _plan_run_id(str(plan), None) == "from-sidecar"
    assert _plan_run_id(str(plan), "explicit") == "explicit"


def test_existing_run_uses_current_file_and_overrides(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runs = repo / ".ale" / "runs"
    runs.mkdir(parents=True)
    (runs / "current").write_text("last-run\n")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    assert _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": None})()) == str(runs / "last-run")
    monkeypatch.setenv("ALE_RUN_DIR", "/explicit/env-run")
    assert _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": None})()) == "/explicit/env-run"
    assert _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": "selected"})()) == str(repo / ".ale/runs/selected")
    assert _resolve_run_dir(type("Args", (), {"run_dir": "/explicit/flag-run", "run_id": None})()) == "/explicit/flag-run"


def test_roster_resolution_prefers_flag_then_env_then_ale(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".ale").mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALE_ROSTER", raising=False)
    args = type("Args", (), {"roster": None})()
    assert _resolve_roster(args) == str(repo / ".ale/roster.json")
    monkeypatch.setenv("ALE_ROSTER", "env-roster.json")
    assert _resolve_roster(args) == "env-roster.json"
    args.roster = "flag-roster.json"
    assert _resolve_roster(args) == "flag-roster.json"


def test_setup_creates_roster_and_excludes_ale(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    git_info = repo / ".git" / "info"
    git_info.mkdir(parents=True)
    monkeypatch.chdir(repo)
    assert main(["setup"]) == 0
    roster = repo / ".ale" / "roster.json"
    assert json.loads(roster.read_text())
    assert ".ale/" in (git_info / "exclude").read_text()
    assert "Next:" in capsys.readouterr().out
    assert main(["setup"]) == 1
    assert main(["setup", "--force"]) == 0


def test_run_dry_run_does_not_create_events(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".ale").mkdir()
    (repo / ".ale" / "roster.json").write_text(open(os.path.join(os.path.dirname(__file__), "..", "examples", "roster.json")).read())
    plan = repo / "Plan.md"
    plan.write_text("# One task plan with no baked labels\n")
    monkeypatch.chdir(repo)
    assert main(["run", str(plan), "--dry-run"]) == 5
    assert "fill them" in capsys.readouterr().out
    assert not (repo / ".ale" / "runs").exists()


def test_run_reports_each_remaining_gap_before_skill_hint(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "Plan.md"
    def block(task_id, number):
        label = {"task_id": task_id, "title": "Task %s" % number,
                 "labels": {"role": "backend", "model_tier": "standard", "risk": "low",
                            "effort": "M", "lane": None},
                 "lane_reason": None, "acceptance": [], "allowed_paths": [],
                 "depends_on": [], "worktree": "none",
                 "assignments": [{"kind": "executor", "role": "backend",
                                  "model_tier": "standard", "executor": "claude-headless",
                                  "trigger": "ready"}]}
        return "## Task %s: Task %s\n\nTask body.\n\n```ale-label\n%s\n```\n" % (
            number, number, json.dumps(label, indent=1))

    plan.write_text(block("T1", 1) + "\n" + block("T2", 2))
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(plan), "--dry-run"]) == 5
    output = capsys.readouterr().out
    assert "T1: lane" in output and "T1: lane_reason" in output
    assert "T1: acceptance" in output and "T1: allowed_paths" in output
    assert output.index("T1: lane") < output.index("run /label-layer")


def test_run_fake_executor_dependency_and_resume(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".ale").mkdir()
    plan = repo / "PLAN.md"

    def block(task_id, title, depends):
        return """## Task %s: %s

**Files:** `%s.py`
Run: `test -f %s.py`
Run: `echo ok`
%s
```ale-label
{
 "task_id": "%s",
 "title": "%s",
 "labels": {"role":"backend","model_tier":"cheap","risk":"low","effort":"S","lane":"inline"},
 "lane_reason": "The task has a deterministic, bounded implementation.",
 "acceptance": [{"id":"A1","cmd":"test -f %s.py","expect":"exit0"},{"id":"A2","cmd":"echo ok","expect":"exit0"}],
 "allowed_paths": ["%s.py"],
 "depends_on": %s,
 "worktree": "none",
 "assignments": [{"kind":"executor","role":"backend","model_tier":"cheap","executor":"claude-headless","trigger":"ready"}]
}
```
""" % (task_id[1:], title, task_id.lower(), task_id.lower(),
       "Depends on Task 1." if depends else "", task_id, title, task_id.lower(),
       task_id.lower(), json.dumps(depends))

    plan.write_text(block("T1", "first", []) + "\n" + block("T2", "second", ["T1"]))
    fake_spawn = repo / "fake-spawn.py"
    fake_spawn.write_text("""#!/usr/bin/env python3
import json
import sys
from ale.cli import main
request = json.load(open(sys.argv[1], encoding='utf-8'))
env = request['env']
task = env['ALE_TASK']
agent = env['ALE_AGENT']
run = env['ALE_RUN_DIR']
roster = env['ALE_ROSTER']
if task == 'T2' or '.fix' in task:
    open(task.split('.')[0].lower() + '.py', 'w').write('ok')
assert main(['claim', '--task', task, '--agent', agent, '--run-dir', run, '--roster', roster]) == 0
assert main(['submit', '--task', task, '--agent', agent, '--summary', 'done', '--run-dir', run, '--roster', roster]) == 0
""")
    fake_spawn.chmod(fake_spawn.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(repo)
    assert main(["setup"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("ALE_SPAWN_BIN", str(fake_spawn))
    monkeypatch.setenv("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    monkeypatch.delenv("ALE_ROSTER", raising=False)
    monkeypatch.setattr(cli, "cmd_integrate", lambda args: cli.Ctx(args).emit(
        "integrated", args.task, None, 1, commit="test", files=[]) or 0)

    assert main(["run", str(plan), "--dry-run"]) == 0
    capsys.readouterr()
    run_dir = repo / ".ale" / "runs" / "PLAN"
    assert not (run_dir / "events.jsonl").exists()
    original_main = cli.main
    crashed = [False]

    def crash_after_dispatch(argv=None):
        result = original_main(argv)
        if argv and argv[0] == "dispatch" and "--spawn" in argv and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("simulated crash after synchronous dispatch")
        return result

    monkeypatch.setattr(cli, "main", crash_after_dispatch)
    with pytest.raises(RuntimeError, match="simulated crash"):
        crash_after_dispatch(["run", str(plan), "--max-cycles", "20"])
    monkeypatch.setattr(cli, "main", original_main)
    run_dir = repo / ".ale" / "runs" / "PLAN"
    interrupted_events = read_events(str(run_dir / "events.jsonl"))
    assert sum(event.get("type") == "run_started" for event in interrupted_events) == 1
    assert sum(event.get("type") == "spawned" and event.get("task_id") == "T1"
               for event in interrupted_events) == 1

    assert main(["run", str(plan), "--max-cycles", "20"]) == 0
    first_output = capsys.readouterr().out
    events_before = read_events(str(run_dir / "events.jsonl"))
    assert sum(event.get("type") == "run_started" for event in events_before) == 1
    assert [event["task_id"] for event in events_before if event.get("type") == "integrated"] == ["T1", "T2"]
    assert "T1" in first_output and "T2" in first_output

    assert main(["run", str(plan), "--max-cycles", "20"]) == 0
    events_after = read_events(str(run_dir / "events.jsonl"))
    assert len(events_after) == len(events_before)


def test_run_dry_run_prints_manual_acceptance_entries(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".ale").mkdir()
    plan = repo / "PLAN.md"

    def block(task_id, acceptance):
        return """## Task %s: task %s

**Files:** `%s.py`
Run: `echo ok`
```ale-label
{
 "task_id": "%s",
 "title": "task %s",
 "labels": {"role":"backend","model_tier":"cheap","risk":"low","effort":"S","lane":"inline"},
 "lane_reason": "The task has a deterministic, bounded implementation.",
 "acceptance": %s,
 "allowed_paths": ["%s.py"],
 "depends_on": [],
 "worktree": "none",
 "assignments": [{"kind":"executor","role":"backend","model_tier":"cheap","executor":"claude-headless","trigger":"ready"}]
}
```
""" % (task_id[1:], task_id, task_id.lower(), task_id, task_id, json.dumps(acceptance), task_id.lower())

    plan.write_text(
        block("T1", [{"id": "A1", "cmd": "echo ok", "expect": "exit0"},
                     {"id": "A2", "manual": "Live check pasted by the lead."}])
        + "\n"
        + block("T2", [{"id": "A1", "manual": "First manual item."},
                       {"id": "A2", "manual": "Second manual item."}]))
    monkeypatch.chdir(repo)
    assert main(["setup"]) == 0
    capsys.readouterr()
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    monkeypatch.delenv("ALE_ROSTER", raising=False)

    assert main(["run", str(plan), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "task T1: dependencies=none acceptance=echo ok,manual: Live check pasted by the lead." in out
    assert "task T2: dependencies=none acceptance=manual: First manual item.,manual: Second manual item." in out
    assert not (repo / ".ale" / "runs" / "PLAN" / "events.jsonl").exists()
