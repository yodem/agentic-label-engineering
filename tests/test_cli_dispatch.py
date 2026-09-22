import json
import os
import subprocess
import sys

from ale.cli import main
from ale.cli import _extract_monitor_verdict
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


def test_monitor_spawn_captures_child_verdict_event(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path)
    assignment = [{"kind": "monitor", "role": "backend", "model_tier": "standard",
                   "executor": "claude-headless", "trigger": "on_breach"}]
    run = _run(tmp_path, {"T1": _label(mode="none", assignments=assignment)})
    breach = make_event("breach", "run-1", 3, "T1", None, 2, breach="lease_expired", detail="lease elapsed")
    (run / "events.jsonl").write_text(json.dumps(breach) + "\n")
    monkeypatch.setattr("ale.cli.subprocess.run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout="Verdict: escalate\nLease elapsed", stderr=""))

    assert main(_dispatch_args(run, roster, "--spawn")) == 0
    events = read_events(str(run / "events.jsonl"))
    verdict = next(event for event in events if event["type"] == "monitor_verdict")
    assert verdict["verdict"] == "escalate" and verdict["text"].startswith("Verdict: escalate")
    assert verdict["agent_id_minted"] == "T1-monitor-backend-1"
    request = json.loads((run / "requests" / "T1-monitor-backend-1.json").read_text())
    assert "ALE_TASK" not in request["env"] and request["env"]["ALE_PLUGIN_ROOT"]
    assert request["env"]["ALE_READ_ONLY"] == "1"
    prompt = (run / "prompts" / "T1-monitor-backend-1.md").read_text()
    assert '"type": "lease_expired"' in prompt and '"attempt": 2' in prompt
    assert '"last_heartbeat_step": null' in prompt
    assert '"acceptance_commands"' in prompt and '"handoff_path"' in prompt and '"worktree"' in prompt
    payload = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert "commands" not in payload
    assert "Verdict: escalate" in capsys.readouterr().out


def test_monitor_verdict_parser_accepts_required_forms_and_last_occurrence():
    assert _extract_monitor_verdict("**continue**") == "continue"
    assert _extract_monitor_verdict("Verdict: continue") == "continue"
    assert _extract_monitor_verdict("## Verdict\n\nContinue\n") == "continue"
    assert _extract_monitor_verdict("nudge") == "nudge"
    assert _extract_monitor_verdict("continue\n\n**ESCALATE**") == "escalate"


def test_monitor_file_write_is_reverted_and_escalated(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    assignment = [{"kind": "monitor", "role": "backend", "model_tier": "standard",
                   "executor": "claude-headless", "trigger": "on_breach"}]
    run = _run(tmp_path, {"T1": _label(mode="none", assignments=assignment)})
    breach = make_event("breach", "run-1", 3, "T1", None, 2, breach="lease_expired", detail="lease elapsed")
    (run / "events.jsonl").write_text(json.dumps(breach) + "\n")
    created = repo / "monitor-created.txt"
    original_run = subprocess.run

    def fake_child(args, **kwargs):
        if args[0].endswith("ale-spawn"):
            created.write_text("unauthorized")
            return subprocess.CompletedProcess(args, 1, stdout="Verdict: continue\n", stderr="monitor failed")
        return original_run(args, **kwargs)

    monkeypatch.setattr("ale.cli.subprocess.run", fake_child)
    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    assert not created.exists()
    verdict = next(event for event in read_events(str(run / "events.jsonl"))
                   if event["type"] == "monitor_verdict")
    assert verdict["verdict"] == "escalate"
    assert verdict["wrote_files"] == ["monitor-created.txt"]
    assert verdict["text"] == "monitor wrote files: monitor-created.txt"


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


def test_integrate_twice_refuses_without_appending(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    events_path = run / "events.jsonl"
    rows = [
        make_event("spawned", "run-1", 1, "T1", None, 1, agent_id_minted="a1",
                   assignment_kind="executor", executor="claude-headless", model="m",
                   worktree=str(run / "wt" / "T1"), branch="ale/run-1/T1"),
        make_event("claimed", "run-1", 2, "T1", "a1", 1),
        make_event("submitted", "run-1", 3, "T1", "a1", 1, summary="done"),
        make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True}),
        make_event("integrated", "run-1", 5, "T1", None, 1, commit="abcdef1234567890"),
    ]
    events_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = events_path.read_bytes()

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(tmp_path)]) == 1
    assert events_path.read_bytes() == before
    assert "task T1 is already integrated (abcdef1)" in capsys.readouterr().err


def test_integrate_missing_worktree_is_clean_error(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    missing = run / "missing-worktree"
    events_path = run / "events.jsonl"
    rows = [
        make_event("spawned", "run-1", 1, "T1", None, 1, agent_id_minted="a1",
                   assignment_kind="executor", executor="claude-headless", model="m",
                   worktree=str(missing), branch="ale/run-1/T1"),
        make_event("claimed", "run-1", 2, "T1", "a1", 1),
        make_event("submitted", "run-1", 3, "T1", "a1", 1, summary="done"),
        make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True}),
    ]
    events_path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert str(missing) in err and "Traceback" not in err


def test_integrate_fills_only_missing_git_identity(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run(tmp_path, {"T1": _label(mode="per_task", paths=["change.txt"])})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")
    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    worktree = run / "wt" / "T1"
    (worktree / "change.txt").write_text("change\n")
    original_run = subprocess.run

    def report_missing_email(args, *positional, **kwargs):
        if args[:4] == ["git", "config", "--get", "user.email"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return original_run(args, *positional, **kwargs)

    monkeypatch.setattr("ale.cli.subprocess.run", report_missing_email)
    events_path = run / "events.jsonl"
    with events_path.open("a") as handle:
        handle.write(json.dumps(make_event("claimed", "run-1", 2, "T1", "agent", 1)) + "\n")
        handle.write(json.dumps(make_event("submitted", "run-1", 3, "T1", "agent", 1, summary="done")) + "\n")
        handle.write(json.dumps(make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True})) + "\n")

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    authors = subprocess.check_output(["git", "log", "--format=%ae", "--", "change.txt"],
                                      cwd=str(repo), text=True).splitlines()
    assert "ale@localhost" in authors


def test_handoff_path_keeps_old_signature_and_supports_role(tmp_path):
    old = handoff_path(str(tmp_path), "T1", "agent")
    new = handoff_path(str(tmp_path), "T1", "agent", "backend")
    assert old.endswith("T1.agent.md")
    assert new.endswith("agent-backend-handoff.md")


def test_extract_verdict_bold_prefixed_label():
    assert _extract_monitor_verdict("**Verdict: escalate**\n\n**Reasoning:** A1 fails.") == "escalate"
    assert _extract_monitor_verdict("## Verdict\n\n**continue** because A1 and A2 pass") == "continue"
