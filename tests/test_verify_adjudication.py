"""Acceptance-time adjudication of recorded Jev shadow votes."""

import json
import os
import shutil

from ale import events as E
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ("role", "sub", "phase", "model_tier", "risk", "effort", "locality")


def _setup(tmp_path, mode="shadow"):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = tmp_path / "roster.json"
    data = json.load(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8"))
    if mode == "legacy":
        data["judge"].pop("default", None)
    else:
        data["judge"]["default"] = mode
    data["judge"]["bar"] = {"min_cases": 100, "min_agreement": 0.8, "max_instability": 0.1}
    roster.write_text(json.dumps(data), encoding="utf-8")
    label_path = run / "labels" / "T01.json"
    label = json.loads(label_path.read_text(encoding="utf-8"))
    label["acceptance"] = [{"id": "A1", "cmd": "true", "expect": "exit0"}]
    label_path.write_text(json.dumps(label), encoding="utf-8")
    events = run / "events.jsonl"
    for kind, ts, extra in (("claimed", 1, {"owner": "worker"}),
                            ("submitted", 2, {"summary": "done"})):
        E.append_event(str(events), E.make_event(kind, "example-run", ts, "T01", "worker", 1, **extra))
    return run, roster, events


def _vote(events, decision, choice):
    E.append_event(str(events), E.make_event("shadow_vote", "example-run", 3, "T01", None, 1,
        decision=decision, options=[], choice=choice, confidence=0.9, model="fake",
        latency_ms=1, authority="lead", additive=True))


def _verify(run, roster):
    return main(["verify", "--task", "T01", "--run-dir", str(run), "--roster", str(roster), "--now", "4"])


def _events(path):
    return E.read_events(str(path))


def test_acceptance_adjudicates_agreement_once_and_lists_disagreement(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "backend")
    _vote(events, "risk", "high")
    assert _verify(run, roster) == 0
    lines = [e for e in _events(events) if e["type"] == "adjudicated"]
    assert len(lines) == 1
    assert {k: lines[0][k] for k in ("field", "decision", "choice", "value", "by", "authority", "additive")} == {
        "field": "role", "decision": "role", "choice": "backend", "value": "backend",
        "by": "agreement_then_accepted", "authority": "lead", "additive": True}
    assert "adjudicate T01 risk: planner=low jev=high -> ale adjudicate --task T01 --decision risk --value low" in capsys.readouterr().err
    from argparse import Namespace
    from ale.cli import Ctx, _adjudicate_shadow_acceptance
    _adjudicate_shadow_acceptance(Ctx(Namespace(run_dir=str(run), roster=str(roster), now=5)), "T01")
    assert len([e for e in _events(events) if e["type"] == "adjudicated"]) == 1
    assert _verify(run, roster) == 1  # already accepted; no duplicate event
    assert len([e for e in _events(events) if e["type"] == "adjudicated"]) == 1


def test_acceptance_skips_off_legacy_lane_and_failed_votes(tmp_path, capsys):
    for mode in ("off", "legacy"):
        case = tmp_path / mode
        case.mkdir()
        run, roster, events = _setup(case, mode)
        _vote(events, "role", "backend")
        assert _verify(run, roster) == 0
        assert not [e for e in _events(events) if e["type"] == "adjudicated"]
    case = tmp_path / "shadow"
    case.mkdir()
    run, roster, events = _setup(case)
    _vote(events, "lane", "inline")
    _vote(events, "role", None)
    assert _verify(run, roster) == 0
    assert not [e for e in _events(events) if e["type"] == "adjudicated"]
    assert capsys.readouterr().err == ""


def test_judge_stats_reports_pending_accepted_vote_and_blocks_bar(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "frontend")
    assert _verify(run, roster) == 0
    assert main(["judge-stats", "--run-dir", str(run), "--roster", str(roster)]) == 0
    result = json.loads(capsys.readouterr().out)
    role = result["decisions"]["role"]
    assert role["pending_adjudication"] == 1 and role["bar_met"] is False
