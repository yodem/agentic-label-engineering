import json
import os
from types import SimpleNamespace

from ale.cli import Ctx, main
from ale.events import read_events


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(tmp_path):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    with open(ROOT + "/examples/run/labels/T01.json", encoding="utf-8") as f:
        label = json.load(f)
    (run / "labels" / "T01.json").write_text(json.dumps(label))
    roster = run.parent / "roster.json"
    with open(ROOT + "/examples/roster.json", encoding="utf-8") as f:
        roster.write_text(f.read())
    return run, roster


def _ctx(run, roster):
    return Ctx(SimpleNamespace(run_dir=str(run), roster=str(roster), now=1.0), need_roster=True)


def test_publisher_is_off_by_default(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = []
    monkeypatch.delenv("ALE_HERDR", raising=False)
    monkeypatch.setenv("HERDR_PANE_ID", "pane-1")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda *args, **kwargs: calls.append((args, kwargs)))
    _ctx(run, roster).emit("claimed", "T01", "agent", 1)
    assert calls == []


def test_publisher_uses_exact_argv_once_per_event(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = []
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "pane-7")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    _ctx(run, roster).emit("claimed", "T01", "agent", 1)
    _ctx(run, roster).emit("note", "T01", "agent", 1, text="hello")
    assert len(calls) == 2
    argv = calls[0][0][0]
    assert argv[:7] == ["herdr", "pane", "report-metadata", "pane-7", "--source", "ale", "--token"]
    assert argv[7].startswith("ale=") and argv[8:] == ["--ttl-ms", "900000"]
    assert calls[0][1]["timeout"] == 2


def test_claimed_and_spawned_record_pane(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("HERDR_PANE_ID", "pane-2")
    c = _ctx(run, roster)
    c.emit("spawned", "T01", None, 1, agent_id_minted="agent-1", assignment_kind="executor",
           executor="claude-headless", model="m")
    c.emit("claimed", "T01", "agent", 1)
    assert [event["pane"] for event in read_events(str(run / "events.jsonl"))] == ["pane-2", "pane-2"]


def test_no_recorded_pane_means_no_call(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    calls = []
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda *args, **kwargs: calls.append(args))
    _ctx(run, roster).emit("note", "T01", "agent", 1, text="hello")
    assert calls == []


def test_runner_exception_is_swallowed_and_counted(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "pane-3")
    def explode(*args, **kwargs):
        raise TimeoutError("slow")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", explode)
    _ctx(run, roster).emit("claimed", "T01", "agent", 1)
    assert (run / "herdr-failures.count").read_text() == "1\n"


def test_nonzero_runner_is_swallowed_and_counted(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "pane-4")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda *args, **kwargs: SimpleNamespace(returncode=9))
    _ctx(run, roster).emit("claimed", "T01", "agent", 1)
    assert (run / "herdr-failures.count").read_text() == "1\n"


def test_failure_does_not_change_claim_exit_code(tmp_path, monkeypatch):
    run, roster = _run(tmp_path)
    monkeypatch.setenv("ALE_HERDR", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "pane-5")
    monkeypatch.setattr("ale.cli._HERDR_RUNNER", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert main(["claim", "--run-dir", str(run), "--roster", str(roster), "--task", "T01", "--agent", "a"]) == 0


def test_doctor_reports_publisher_failures(tmp_path, capsys):
    run, roster = _run(tmp_path)
    (run / "herdr-failures.count").write_text("2\n")
    assert main(["doctor", "--run-dir", str(run), "--roster", str(roster)]) == 1
    assert "herdr metadata failures: 2" in capsys.readouterr().err
