"""B-SPEC-3 A2: one end-to-end run through real CLI commands with a fake judge.

plan bake -> planner fills lane -> init-run (imports bake votes) -> dispatch ->
claim/submit -> verify reject -> fix -> fix-of-fix escalates -> a monitor
breach -> judge-stats. Every judged decision must show votes; executor none.
"""

import json
import os

from ale import decisions as DECISIONS
from ale.cli import main
from ale.events import read_events

from judge_fakes import PLAN, drive_run, fill_blocks, read_calls, setup_repo

EXPECTED_CODES = {"bake": 1, "init": 0, "dispatch1": 0, "claim": 0, "submit": 0, "verify": 1,
                  "fix": 0, "claim_fix": 0, "submit_fix": 0, "verify_fix": 1, "fix_of_fix": 6,
                  "claim2": 0, "watchdog": 6, "dispatch2": 0}


def _events_of(run_dir, kind):
    return [event for event in read_events(os.path.join(run_dir, "events.jsonl")) if event["type"] == kind]


def test_end_to_end_every_judged_decision_collects_votes(tmp_path, monkeypatch, capsys):
    repo, roster, roster_value, log_path = setup_repo(tmp_path, monkeypatch, "shadow", "conflict")
    run_dir, codes = drive_run(repo, roster)
    assert codes == EXPECTED_CODES  # bake 1: lane gaps before the planner fills them

    assert len(list((tmp_path / "repo" / ".ale" / "shadow").glob("plan-*.jsonl"))) == 1
    imported = [event for event in _events_of(run_dir, "shadow_vote") if event["source"] == "bake"]
    assert {event["decision"] for event in imported} == {
        "role", "model_tier", "risk", "effort", "locality", "sub", "phase", "lane", "needs_monitor"}
    assert [event["verdict"] for event in _events_of(run_dir, "monitor_verdict")] == ["nudge"]

    capsys.readouterr()
    assert main(["judge-stats", "--run-dir", run_dir, "--roster", roster]) == 0
    stats = json.loads(capsys.readouterr().out)
    print(json.dumps({name: {"votes": row["vote_count"], "uncertain_band": row["uncertain_band"],
                             "agreement": row["agreement"]}
                      for name, row in stats["decisions"].items()}, sort_keys=True))

    judged = set(DECISIONS.judged_decision_ids())
    assert len(judged) == 11 and "executor" not in judged
    assert set(stats["decisions"]) == judged
    assert all(stats["decisions"][name]["vote_count"] > 0 for name in judged)
    assert not [event for event in _events_of(run_dir, "shadow_vote") if event["decision"] == "executor"]

    outcomes = {(event["task_id"], event["decision"]): event["choice"]
                for event in _events_of(run_dir, "decision_outcome")}
    assert outcomes[("T1", "rejection_action")] == "fix"
    assert outcomes[("T1.fix1", "rejection_action")] == "escalate"
    assert outcomes[("T2", "monitor_verdict")] == "nudge"
    assert outcomes[("T1", "lane")] == "inline" and outcomes[("T2", "needs_monitor")] == "yes"
    assert outcomes[("T1", "needs_monitor")] == "no"
    assert all(choice is not None for choice in outcomes.values())
    assert not [key for key in outcomes if key[1] == "executor"]

    # Every question the fake judge saw is an explicit roster question.
    questions = set(roster_value["judge"]["questions"].values())
    calls = read_calls(log_path)
    assert calls and {call["question"] for call in calls} <= questions
    assert not [call for call in calls if "planner-independent" in call["question"]]
    assert {call["kind"] for call in calls} == {"choice", "noul"}

    # Evidence votes carry their answers, the rule that fired, and the model.
    votes = _events_of(run_dir, "shadow_vote")
    rejection = next(event for event in votes
                     if event["decision"] == "rejection_action" and event["task_id"] == "T1")
    assert set(rejection["answers"]) == set(DECISIONS.EVIDENCE_QUESTIONS["rejection_action"])
    assert rejection["rule"] == "needs_human" and rejection["choice"] == "escalate"
    fix_of_fix = next(event for event in votes
                      if event["decision"] == "rejection_action" and event["task_id"] == "T1.fix1")
    assert fix_of_fix["rule"] == "fixes_exhausted"
    monitor = next(event for event in votes if event["decision"] == "monitor_verdict")
    assert set(monitor["answers"]) == set(DECISIONS.EVIDENCE_QUESTIONS["monitor_verdict"])
    assert all(event["model"] == "fake-jev" for event in votes)
    assert all(isinstance(event["latency_ms"], int) for event in votes)
    # The monitor's own verdict line never reaches the judge's state.
    monitor_states = [call["state"] for call in calls if "monitor_report" in call["state"]]
    assert monitor_states and not [state for state in monitor_states if "Verdict" in state]


def test_repeated_bake_imports_only_the_latest_bake(tmp_path, monkeypatch):
    repo, roster, _roster_value, _log = setup_repo(tmp_path, monkeypatch, "shadow", "conflict")
    plan = os.path.join(repo, "plan.md")
    with open(plan, "w", encoding="utf-8") as handle:
        handle.write(PLAN)
    run_dir = os.path.join(repo, ".ale", "runs", "plan")
    assert main(["plan", "bake", plan, "--write", "--roster", roster]) == 1
    with open(plan, encoding="utf-8") as handle:
        filled = fill_blocks(handle.read())
    with open(plan, "w", encoding="utf-8") as handle:
        handle.write(filled)
    assert main(["plan", "bake", plan, "--write", "--roster", roster]) == 0
    rows = [json.loads(line) for path in (tmp_path / "repo" / ".ale" / "shadow").glob("plan-*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]
    assert len({row["bake_id"] for row in rows}) == 2
    assert main(["init-run", "--plan", plan, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    imported = _events_of(run_dir, "shadow_vote")
    assert len({event["bake_id"] for event in imported}) == 1
    assert len(imported) == len(rows) // 2
