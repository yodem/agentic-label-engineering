"""`ale label` in shadow mode: bake decisions voted into the run, outcomes from the label."""

import json
import os

from ale.cli import main
from ale.events import read_events

from judge_fakes import read_calls, write_fake_judge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _draft(task_id, depends_on=None):
    return {
        "schema_version": "1.0", "run_id": "label-shadow", "task_id": task_id, "title": "Task %s" % task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "M"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "specs/%s.md" % task_id, "pointers": [], "allowed_paths": ["src/%s/**" % task_id],
                    "depends_on": depends_on or []},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "Short task with the human present, so inline."},
    }


def _run(tmp_path, default):
    with open(os.path.join(ROOT, "ale", "example_roster.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    command, log_path = write_fake_judge(tmp_path, "conflict")
    roster["judge"].update(command=command, default=default)
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(roster))
    run = tmp_path / "run"
    (run / "drafts").mkdir(parents=True)
    for draft in (_draft("T1"), _draft("T2", ["T1"])):
        (run / "drafts" / ("%s.json" % draft["task_id"])).write_text(json.dumps(draft))
    assert main(["label", "--run-dir", str(run), "--roster", str(roster_path), "--now", "1"]) == 0
    return run, read_calls(log_path)


def test_label_in_shadow_mode_votes_every_bake_decision_with_known_outcomes(tmp_path):
    run, calls = _run(tmp_path, "shadow")
    events = read_events(str(run / "events.jsonl"))
    votes = [event for event in events if event["type"] == "shadow_vote"]
    assert {event["decision"] for event in votes if event["task_id"] == "T1"} == {
        "role", "model_tier", "risk", "effort", "locality", "sub", "phase", "lane", "needs_monitor"}
    assert all(event["source"] == "label" for event in votes)
    outcomes = {(event["task_id"], event["decision"]): event["choice"]
                for event in events if event["type"] == "decision_outcome"}
    assert outcomes[("T1", "lane")] == "inline" and outcomes[("T1", "needs_monitor")] == "no"
    assert outcomes[("T1", "locality")] == "any" and outcomes[("T1", "role")] == "backend"
    assert not {decision for _task, decision in outcomes} & {"executor", "rejection_action", "monitor_verdict"}
    assert None not in outcomes.values()
    labels = json.loads((run / "labels" / "T1.json").read_text())
    assert labels["labels"]["lane"] == "inline" and labels["labels"]["locality"] == "any"
    assert calls


def test_label_with_off_default_calls_no_judge(tmp_path):
    run, calls = _run(tmp_path, "off")
    events = read_events(str(run / "events.jsonl"))
    assert calls == []
    assert not [event for event in events if event["type"] in ("shadow_vote", "decision_outcome")]
