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


def _stats(run, roster, capsys, *extra):
    capsys.readouterr()
    assert main(["judge-stats", "--run-dir", str(run), "--roster", str(roster), *extra]) == 0
    return json.loads(capsys.readouterr().out)["decisions"]


def test_acceptance_adjudicates_agreement_and_disagreement_once_each(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "backend")
    _vote(events, "risk", "high")
    assert _verify(run, roster) == 0
    lines = {e["decision"]: e for e in _events(events) if e["type"] == "adjudicated"}
    assert set(lines) == {"role", "risk"}
    keys = ("field", "decision", "choice", "value", "by", "authority", "additive")
    assert {k: lines["role"][k] for k in keys} == {
        "field": "role", "decision": "role", "choice": "backend", "value": "backend",
        "by": "agreement_then_accepted", "authority": "lead", "additive": True}
    assert {k: lines["risk"][k] for k in keys + ("jev_choice",)} == {
        "field": "risk", "decision": "risk", "choice": "low", "value": "low",
        "by": "disagreement_then_accepted", "authority": "lead", "additive": True, "jev_choice": "high"}
    err = capsys.readouterr().err
    assert "adjudicate T01 risk: planner=low jev=high -> ale adjudicate --task T01 --decision risk --value low" in err
    assert "(or --value high to side with Jev) [recorded: planner]" in err
    from argparse import Namespace
    from ale.cli import Ctx, _adjudicate_shadow_acceptance
    _adjudicate_shadow_acceptance(Ctx(Namespace(run_dir=str(run), roster=str(roster), now=5)), "T01")
    assert len([e for e in _events(events) if e["type"] == "adjudicated"]) == 2
    assert _verify(run, roster) == 1  # already accepted; no duplicate event
    assert len([e for e in _events(events) if e["type"] == "adjudicated"]) == 2


def test_agreement_counts_one_adjudicated_agreement(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "backend")
    assert _verify(run, roster) == 0
    role = _stats(run, roster, capsys)["role"]
    assert (role["adjudicated_count"], role["disagreement_count"], role["agreement"]) == (1, 0, 1.0)
    assert role["pending_adjudication"] == 0


def test_disagreement_is_counted_not_left_pending(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "frontend")
    assert _verify(run, roster) == 0
    role = _stats(run, roster, capsys)["role"]
    assert (role["adjudicated_count"], role["disagreement_count"], role["agreement"]) == (1, 1, 0.0)
    assert role["pending_adjudication"] == 0 and role["bar_met"] is False


def test_explicit_adjudication_overrides_the_automatic_one(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "frontend")
    assert _verify(run, roster) == 0
    assert main(["adjudicate", "--task", "T01", "--decision", "role", "--value", "frontend",
                 "--run-dir", str(run), "--roster", str(roster), "--now", "5"]) == 0
    role = _stats(run, roster, capsys)["role"]
    assert (role["adjudicated_count"], role["disagreement_count"], role["agreement"]) == (1, 0, 1.0)
    from ale.labeling.truth import truth_for
    labels = {"T01": json.loads((run / "labels" / "T01.json").read_text())}
    assert truth_for(_events(events), labels)[("T01", "role")]["value"] == "frontend"
    # A second explicit adjudication is still refused.
    assert main(["adjudicate", "--task", "T01", "--decision", "role", "--value", "backend",
                 "--run-dir", str(run), "--roster", str(roster), "--now", "6"]) == 1


def test_null_final_label_records_no_event_and_stays_pending(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "sub", "api")
    assert _verify(run, roster) == 0
    assert not [e for e in _events(events) if e["type"] == "adjudicated"]
    assert "planner=unset jev=api" in capsys.readouterr().err
    assert _stats(run, roster, capsys)["sub"]["pending_adjudication"] == 1


def test_judge_stats_all_runs_sums_every_run(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".ale" / "runs").mkdir(parents=True)
    (repo / ".git").mkdir()
    for name, choice in (("a", "backend"), ("b", "frontend")):
        case = tmp_path / name
        case.mkdir()
        run, roster, events = _setup(case)
        _vote(events, "role", choice)
        assert _verify(run, roster) == 0
        # Each real run has its own run_id; the copied example shares one.
        events.write_text(events.read_text().replace('"example-run"', '"run-%s"' % name))
        shutil.move(str(run), str(repo / ".ale" / "runs" / name))
    capsys.readouterr()
    assert main(["judge-stats", "--all-runs", "--runs-dir", str(repo / ".ale" / "runs"),
                 "--roster", str(roster)]) == 0
    role = json.loads(capsys.readouterr().out)["decisions"]["role"]
    assert (role["adjudicated_count"], role["disagreement_count"], role["agreement"]) == (2, 1, 0.5)


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


def test_judge_stats_counts_accepted_disagreement_and_blocks_bar_below_min_cases(tmp_path, capsys):
    run, roster, events = _setup(tmp_path)
    _vote(events, "role", "frontend")
    assert _verify(run, roster) == 0
    role = _stats(run, roster, capsys)["role"]
    assert role["pending_adjudication"] == 0 and role["adjudicated_count"] == 1 and role["bar_met"] is False
