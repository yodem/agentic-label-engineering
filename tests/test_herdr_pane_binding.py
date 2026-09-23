import json
import os
from types import SimpleNamespace

from ale.cli import Ctx, _append_spawned, main
from ale.dispatch import spawn_request
from ale.events import read_events


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(path):
    run = path / "run"
    (run / "labels").mkdir(parents=True)
    with open(ROOT + "/examples/run/labels/T01.json", encoding="utf-8") as source:
        (run / "labels" / "T01.json").write_text(source.read())
    roster = path / "roster.json"
    with open(ROOT + "/examples/roster.json", encoding="utf-8") as source:
        roster.write_text(source.read())
    return run, roster


def _ctx(run, roster):
    return Ctx(SimpleNamespace(run_dir=str(run), roster=str(roster), now=1.0), need_roster=True)


def _runner(monkeypatch):
    calls = []
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda argv, **kwargs: calls.append(argv))
    return calls


def test_lead_claim_on_behalf_does_not_bind_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    monkeypatch.delenv("ALE_AGENT_ID", raising=False)
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster),
                 "--task", "T01", "--agent", "executor"]) == 0
    assert "pane" not in read_events(str(run / "events.jsonl"))[0]
    assert calls == []


def test_executor_claim_matching_agent_binds_own_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "executor-pane")
    monkeypatch.setenv("ALE_AGENT_ID", "executor")
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster),
                 "--task", "T01", "--agent", "executor"]) == 0
    assert read_events(str(run / "events.jsonl"))[0]["pane"] == "executor-pane"


def test_claim_pane_option_binds_explicitly(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    monkeypatch.delenv("ALE_AGENT_ID", raising=False)
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster),
                 "--task", "T01", "--agent", "executor", "--pane", "chosen-pane"]) == 0
    assert read_events(str(run / "events.jsonl"))[0]["pane"] == "chosen-pane"


def test_terminal_token_uses_short_ttl(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "executor-pane")
    monkeypatch.setenv("ALE_AGENT_ID", "executor")
    c = _ctx(run, roster)
    c.emit("spawned", "T01", None, 1, agent_id_minted="executor", assignment_kind="executor",
           executor="codex-exec", model="m", pane="executor-pane")
    c.emit("claimed", "T01", "executor", 1)
    c.emit("submitted", "T01", "executor", 1, summary="done")
    c.emit("accepted", "T01", None, 1, evidence={"results": []})
    assert calls[-1][-2:] == ["--ttl-ms", "60000"]


def test_two_runs_never_publish_to_the_other_runs_pane(tmp_path, monkeypatch):
    calls = _runner(monkeypatch)
    runs = [_run(tmp_path / "a"), _run(tmp_path / "b")]
    for (run, roster), pane in zip(runs, ("pane-a", "pane-b")):
        monkeypatch.setenv("HERDR_PANE_ID", pane)
        c = _ctx(run, roster)
        c.emit("spawned", "T01", None, 1, agent_id_minted="agent", assignment_kind="executor",
               executor="codex-exec", model="m", pane=pane)
    run_b, roster_b = runs[1]
    _ctx(run_b, roster_b).emit("note", "T01", "agent", 1, text="update")
    assert [argv[3] for argv in calls] == ["pane-a", "pane-b", "pane-b"]


def test_dispatch_request_exports_agent_id():
    request = spawn_request({"task_id": "T01", "labels": {}}, {"kind": "executor"},
                           "/tmp/run", "run-1")
    assert request["env"]["ALE_AGENT_ID"] == request["agent_id"]


def test_dispatch_spawned_event_does_not_bind_dispatcher_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    c = _ctx(run, roster)
    due = {"task_id": "T01", "kind": "executor", "executor": "codex-exec",
           "model": "m", "trigger_instance": "ready"}
    request = {"agent_id": "executor", "env": {}, "cwd": str(tmp_path)}
    _append_spawned(c, due, request, None)
    spawned = read_events(str(run / "events.jsonl"))[0]
    assert spawned["type"] == "spawned" and "pane" not in spawned


def test_spawned_event_accepts_explicit_executor_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    c = _ctx(run, roster)
    due = {"task_id": "T01", "kind": "executor", "executor": "herdr-pane",
           "model": "m", "trigger_instance": "ready"}
    request = {"agent_id": "executor", "env": {}, "cwd": str(tmp_path),
               "executor_pane": "executor-pane"}
    _append_spawned(c, due, request, None)
    spawned = read_events(str(run / "events.jsonl"))[0]
    assert spawned["pane"] == "executor-pane"


def test_auto_heartbeat_without_bound_executor_pane_does_not_publish(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    monkeypatch.delenv("ALE_AGENT_ID", raising=False)
    c = _ctx(run, roster)
    c.emit("claimed", "T01", "executor", 1, bind_claim_pane=False)
    c.emit("heartbeat", "T01", "executor", 1, step="process alive", auto=True)
    assert calls == []
    assert all("pane" not in event for event in read_events(str(run / "events.jsonl")))


def test_legacy_ale_agent_matching_binds_claim_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "executor-pane")
    monkeypatch.delenv("ALE_AGENT_ID", raising=False)
    monkeypatch.setenv("ALE_AGENT", "executor")
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster),
                 "--task", "T01", "--agent", "executor"]) == 0
    assert read_events(str(run / "events.jsonl"))[0]["pane"] == "executor-pane"


def test_legacy_ale_agent_mismatch_does_not_bind_claim_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = _runner(monkeypatch)
    monkeypatch.setenv("HERDR_PANE_ID", "executor-pane")
    monkeypatch.delenv("ALE_AGENT_ID", raising=False)
    monkeypatch.setenv("ALE_AGENT", "different-executor")
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster),
                 "--task", "T01", "--agent", "executor"]) == 0
    assert "pane" not in read_events(str(run / "events.jsonl"))[0]
    assert calls == []
