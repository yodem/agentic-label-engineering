from __future__ import annotations

import io
import json
import os
import shutil

import pytest

from ale import events as E
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(d))
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(tmp_path / "roster.json"))
    (d / "specs").mkdir()
    (d / "specs" / "T01.md").write_text("Spec for T01 " + "x" * 500, encoding="utf-8")
    (d / "specs" / "T02.md").write_text("Spec for T02", encoding="utf-8")
    return str(d)


def ale(run_dir, *args, now=0):
    roster = os.path.join(os.path.dirname(run_dir), "roster.json")
    return main(list(args) + ["--run-dir", run_dir, "--roster", roster, "--now", str(now)])


def _label_path(run_dir, task_id):
    return os.path.join(run_dir, "labels", "%s.json" % task_id)


def _read_label(run_dir, task_id="T01"):
    with open(_label_path(run_dir, task_id), encoding="utf-8") as f:
        return json.load(f)


def _events_path(run_dir):
    return os.path.join(run_dir, "events.jsonl")


def _append(run_dir, type_, task_id="T01", agent_id=None, attempt=1, ts=0, **extra):
    ev = E.make_event(type_, "example-run", ts, task_id, agent_id, attempt, **extra)
    E.append_event(_events_path(run_dir), ev)


def _vote_pair(run_dir, task_id="T01", field="role", planner="backend", judge="frontend"):
    _append(run_dir, "label_vote", task_id=task_id, field=field, by="planner", value=planner, confidence=None)
    _append(run_dir, "label_vote", task_id=task_id, field=field, by="judge:cmd", value=judge, confidence=0.9)


def test_relabel_lane_exits_usage(run_dir):
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "lane", "--value", "pane", "--reason", "x") == 2


def test_relabel_unknown_vocabulary_value_exits_fail(run_dir):
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "role", "--value", "wizard", "--reason", "x") == 1


def test_relabel_updates_file_and_logs_event(run_dir):
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "role", "--value", "docs", "--reason", "docs only", now=10) == 0
    label = _read_label(run_dir)
    assert label["labels"]["role"] == "docs"
    assert label["provenance"]["role"]["relabeled_from"] == "backend"
    events = E.read_events(_events_path(run_dir))
    assert events[-1]["type"] == "relabeled"
    assert events[-1]["agent_id"] is None
    assert events[-1]["field"] == "role"
    assert events[-1]["old"] == "backend"
    assert events[-1]["new"] == "docs"
    assert events[-1]["reason"] == "docs only"


def test_relabel_on_accepted_task_exits_fail(run_dir):
    _append(run_dir, "claimed", agent_id="a1", ts=1)
    _append(run_dir, "submitted", agent_id="a1", ts=2, summary="done")
    _append(run_dir, "accepted", agent_id=None, ts=3, evidence={"passed": True})
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "role", "--value", "docs", "--reason", "late") == 1
    assert _read_label(run_dir)["labels"]["role"] == "backend"


def test_acceptance_relabel_requires_json(run_dir):
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "acceptance",
               "--value", "[]", "--reason", "correct command") == 2


def test_acceptance_relabel_updates_label_and_provenance(run_dir):
    new_acceptance = [{"id": "A1", "cmd": "pytest -q", "expect": "exit0"},
                      {"id": "A2", "cmd": "python -m compileall .", "expect": "exit0"}]
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "acceptance", "--json",
               "--value", json.dumps(new_acceptance), "--reason", "correct command") == 0
    label = _read_label(run_dir)
    assert label["acceptance"] == new_acceptance
    assert label["provenance"]["acceptance"]["relabeled_from"]


def test_acceptance_relabel_event_has_old_new_and_lead_provenance(run_dir):
    original = _read_label(run_dir)["acceptance"]
    updated = [{"id": "A1", "cmd": "pytest -q", "expect": "exit0"},
               {"id": "A2", "cmd": "python -m compileall .", "expect": "exit0"}]
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "acceptance", "--json",
               "--value", json.dumps(updated), "--reason", "repair check") == 0
    event = E.read_events(_events_path(run_dir))[-1]
    assert event["type"] == "relabeled" and event["agent_id"] is None
    assert event["field"] == "acceptance" and event["old"] == original
    assert event["new"] == updated and event["reason"] == "repair check"


def test_acceptance_relabel_refused_after_acceptance(run_dir):
    _append(run_dir, "claimed", agent_id="a1", ts=1)
    _append(run_dir, "submitted", agent_id="a1", ts=2, summary="done")
    _append(run_dir, "accepted", agent_id=None, ts=3, evidence={"passed": True})
    updated = [{"id": "A1", "cmd": "true", "expect": "exit0"},
               {"id": "A2", "cmd": "true", "expect": "exit0"}]
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "acceptance", "--json",
               "--value", json.dumps(updated), "--reason", "late") == 1


def test_acceptance_relabel_invalidates_prior_verification(run_dir):
    _append(run_dir, "claimed", agent_id="a1", ts=1)
    _append(run_dir, "submitted", agent_id="a1", ts=2, summary="done")
    _append(run_dir, "verified", agent_id=None, ts=3, evidence={"passed": True})
    updated = [{"id": "A1", "cmd": "pytest -q", "expect": "exit0"},
               {"id": "A2", "cmd": "python -m compileall .", "expect": "exit0"}]
    assert ale(run_dir, "relabel", "--task", "T01", "--field", "acceptance", "--json",
               "--value", json.dumps(updated), "--reason", "fix assertion") == 0
    state = E.reduce_run(E.read_events(_events_path(run_dir)),
                         {task_id: _read_label(run_dir, task_id) for task_id in ("T01", "T02")})
    from ale.runner import next_actions
    assert ("verify", "T01") in next_actions(state, {task_id: _read_label(run_dir, task_id)
                                                       for task_id in ("T01", "T02")},
                                               E.read_events(_events_path(run_dir)))


def test_adjudicate_list_prints_json_queue(run_dir, capsys):
    _vote_pair(run_dir, task_id="T01", field="role", planner="backend", judge="frontend")
    _vote_pair(run_dir, task_id="T02", field="risk", planner="low", judge="high")
    capsys.readouterr()
    assert ale(run_dir, "adjudicate", "--list") == 0
    assert json.loads(capsys.readouterr().out) == [
        {"task_id": "T01", "field": "role", "planner": "backend", "judge": "frontend"},
        {"task_id": "T02", "field": "risk", "planner": "low", "judge": "high"},
    ]


def test_non_interactive_adjudicate_removes_pair_from_queue(run_dir, capsys):
    _vote_pair(run_dir, task_id="T01", field="role", planner="backend", judge="frontend")
    assert ale(run_dir, "adjudicate", "--task", "T01", "--field", "role", "--value", "frontend", "--by", "lead") == 0
    capsys.readouterr()
    assert ale(run_dir, "adjudicate", "--list") == 0
    assert json.loads(capsys.readouterr().out) == []
    events = E.read_events(_events_path(run_dir))
    assert events[-1]["type"] == "adjudicated"
    assert events[-1]["by"] == "lead"
    assert events[-1]["value"] == "frontend"


def test_adjudicate_unknown_vocabulary_value_exits_fail(run_dir):
    assert ale(run_dir, "adjudicate", "--task", "T01", "--field", "role", "--value", "wizard") == 1


def test_adjudicate_lane_exits_usage(run_dir):
    assert ale(run_dir, "adjudicate", "--task", "T01", "--field", "lane", "--value", "pane") == 2


def test_interactive_adjudicate_records_exactly_one_event(run_dir, monkeypatch):
    _vote_pair(run_dir, task_id="T01", field="role", planner="backend", judge="frontend")
    _vote_pair(run_dir, task_id="T02", field="risk", planner="low", judge="high")
    monkeypatch.setattr("sys.stdin", io.StringIO("1\ns\nq\n"))
    assert ale(run_dir, "adjudicate") == 0
    adjudicated = [e for e in E.read_events(_events_path(run_dir)) if e["type"] == "adjudicated"]
    assert len(adjudicated) == 1
    assert adjudicated[0]["task_id"] == "T01"
    assert adjudicated[0]["field"] == "role"
    assert adjudicated[0]["value"] == "backend"
    assert adjudicated[0]["by"] == "human"
