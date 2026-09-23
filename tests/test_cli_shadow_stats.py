"""`ale judge-stats` reads the run's events.jsonl and reports judged decisions only."""

import json
import os

from ale import events as E
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _append(path, kind, ts, task, **extra):
    E.append_event(str(path), E.make_event(kind, "fixture", ts, task, None, 1, **extra))


def test_judge_stats_reports_judged_decisions_and_skips_executor(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    events = run / "events.jsonl"
    roster = os.path.join(ROOT, "ale", "example_roster.json")
    common = dict(authority="lead", additive=True, model="fake-jev")
    _append(events, "shadow_vote", 1, "T1", decision="lane", options=["inline", "workflow", "pane"],
            choice="pane", confidence=0.6, latency_ms=12, uncertain=True, source="bake",
            answers={"large_change": 0.6, "needs_person": 0.2}, rule="large_change", **common)
    _append(events, "decision_outcome", 2, "T1", decision="lane", choice="inline", source="planner",
            authority="lead", additive=True)
    _append(events, "shadow_vote", 3, "T1", decision="role", options=["backend", "frontend"],
            choice="backend", confidence=0.9, latency_ms=30, uncertain=False, source="bake", **common)
    _append(events, "decision_outcome", 4, "T1", decision="role", choice="backend", source="planner",
            authority="lead", additive=True)
    _append(events, "adjudicated", 5, "T1", field="role", decision="role", value="backend",
            choice="backend", by="lead", authority="lead", additive=True)
    # A legacy executor vote from an older round must not be reported.
    _append(events, "shadow_vote", 6, "T1", decision="executor", options=["codex-exec"],
            choice="codex-exec", confidence=0.9, latency_ms=1, source="dispatcher", **common)

    assert main(["judge-stats", "--run-dir", str(run), "--roster", roster]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["decisions"]) == {"role"}
    role = result["decisions"]["role"]
    assert role["agreement_outside_band"] == 1.0 and role["adjudicated_count"] == 1
    assert role["latency_ms_median"] == 30 and role["grey_zone"] is False
    assert result["bar"] == {"min_cases": 100, "min_agreement": 0.8, "max_instability": 0.1}
    assert result["cases"] == 1
