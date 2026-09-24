import json
import os
import shutil

from ale import events as E
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = os.path.join(ROOT, "examples", "roster.json")
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("claimed", "example-run", 1, "T01", "worker", 1))
    E.append_event(str(events), E.make_event("submitted", "example-run", 2, "T01", "worker", 1,
                                             summary="done"))
    E.append_event(str(events), E.make_event("accepted", "example-run", 3, "T01", None, 1,
                                             evidence={"passed": True}))
    return run, roster, events


def _ale(run, roster, *args):
    return main(list(args) + ["--run-dir", str(run), "--roster", roster])


def test_accepted_unintegrated_task_reopens_and_verifies_again(tmp_path):
    run, roster, events = _setup(tmp_path)

    assert _ale(run, roster, "reopen", "--task", "T01", "--reason", "review fix") == 0
    reopened = [event for event in E.read_events(str(events)) if event["type"] == "reopened"][-1]
    assert reopened["from_state"] == "accepted"
    assert E.reduce_run(E.read_events(str(events)), {
        "T01": json.loads((run / "labels" / "T01.json").read_text())})["tasks"]["T01"]["state"] == "submitted"
    assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run)) == 0
    assert len([event for event in E.read_events(str(events)) if event["type"] == "accepted"]) == 2


def test_integrated_task_still_cannot_reopen(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    E.append_event(str(events), E.make_event("integrated", "example-run", 4, "T01", None, 1,
                                             commit="abcdef1234567890"))

    assert _ale(run, roster, "reopen", "--task", "T01", "--reason", "too late") == 1
    assert "task T01 is integrated and cannot be reopened" in capsys.readouterr().err
