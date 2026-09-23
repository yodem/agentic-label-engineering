"""Regression tests for planner-unset hints and label-only pending counts."""

import json
import os
import shutil
from argparse import Namespace

from ale import events as E
from ale.cli import Ctx, _adjudicate_shadow_acceptance
from ale.decisions import options_for_decision
from ale.labeling.shadow import summarize_shadow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ("role", "sub", "phase", "model_tier", "risk", "effort", "locality")


def _setup(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = tmp_path / "roster.json"
    data = json.load(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8"))
    data["judge"]["default"] = "shadow"
    data["judge"]["bar"] = {"min_cases": 100, "min_agreement": 0.8, "max_instability": 0.1}
    roster.write_text(json.dumps(data), encoding="utf-8")
    return run, roster


def _record_vote(run, task, decision, choice):
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "shadow_vote", "example-run", 3, task, None, 1, decision=decision,
        options=[], choice=choice, confidence=.9, model="fake", latency_ms=1,
        authority="lead", additive=True))


def _ctx(run, roster):
    return Ctx(Namespace(run_dir=str(run), roster=str(roster), now=5))


def test_unset_planner_hint_lists_valid_options_and_never_none(tmp_path, capsys):
    run, roster = _setup(tmp_path)
    label_path = run / "labels" / "T01.json"
    label = json.loads(label_path.read_text(encoding="utf-8"))
    label["labels"]["role"] = None
    label_path.write_text(json.dumps(label), encoding="utf-8")
    _record_vote(run, "T01", "role", "frontend")

    _adjudicate_shadow_acceptance(_ctx(run, roster), "T01")

    text = capsys.readouterr().err
    options = options_for_decision("role", json.loads(roster.read_text(encoding="utf-8")), label=label)
    assert "planner=unset jev=frontend" in text
    assert "--value <one of: %s>" % "|".join(options) in text
    assert "--value None" not in text


def test_set_planner_hint_keeps_old_text_and_adds_jev_alternative(tmp_path, capsys):
    run, roster = _setup(tmp_path)
    _record_vote(run, "T01", "risk", "high")

    _adjudicate_shadow_acceptance(_ctx(run, roster), "T01")

    text = capsys.readouterr().err
    assert "adjudicate T01 risk: planner=low jev=high -> ale adjudicate --task T01 --decision risk --value low" in text
    assert "(or --value high to side with Jev)" in text


def test_accepted_needs_monitor_has_no_pending_and_does_not_block_bar():
    vote = {"run_id": "r", "task_id": "T1", "decision": "needs_monitor", "choice": "no",
            "confidence": .9}
    outcome = {"run_id": "r", "task_id": "T1", "decision": "needs_monitor", "choice": "no"}
    adjudication = {"run_id": "r", "task_id": "T1", "decision": "needs_monitor", "choice": "no"}
    result = summarize_shadow([vote], [outcome], [adjudication],
                              {"min_cases": 1, "min_agreement": 1, "max_instability": .1},
                              accepted_tasks={"T1"})["decisions"]["needs_monitor"]
    assert "pending_adjudication" not in result
    assert result["bar_met"] is True
