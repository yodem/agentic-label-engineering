"""Shadow votes and outcomes at their real firing sites (dispatch, verify, reopen, fix)."""

import json
import os

import pytest

from ale.cli import main
from ale.events import check_event, read_events
from ale.labeling import evidence as EV

from judge_fakes import read_calls, write_fake_judge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _label(task_id, cmd="false"):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id, "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["src/%s.py" % task_id],
                    "depends_on": [], "worktree": {"mode": "none", "branch": None, "base": None,
                                                   "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": cmd, "expect": "exit0"}, {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The human selected the inline lane."},
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }


def _setup(tmp_path, judge_default):
    with open(os.path.join(ROOT, "ale", "example_roster.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    command, log_path = write_fake_judge(tmp_path, "conflict")
    roster["judge"]["command"] = command
    if judge_default is None:
        roster["judge"].pop("default")
    else:
        roster["judge"]["default"] = judge_default
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(roster))
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps(_label("T1")))
    common = ["--run-dir", str(run), "--roster", str(roster_path)]
    assert main(["init-run", "--now", "1"] + common) == 0
    return run, common, log_path


def _events(run, kind=None):
    events = read_events(str(run / "events.jsonl"))
    return [event for event in events if kind is None or event["type"] == kind]


def _reject(run, common, tmp_path):
    assert main(["claim", "--task", "T1", "--agent", "a1", "--now", "2"] + common) == 0
    assert main(["submit", "--task", "T1", "--agent", "a1", "--summary", "done", "--now", "3"] + common) == 0
    assert main(["verify", "--task", "T1", "--cwd", str(tmp_path), "--now", "4"] + common) == 1


def test_dispatch_asks_no_executor_question(tmp_path, monkeypatch):
    run, common, log_path = _setup(tmp_path, "shadow")
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")
    assert main(["dispatch", "--spawn", "--now", "2"] + common) == 0
    assert [event["type"] for event in _events(run)][-1] == "spawned"
    assert _events(run, "shadow_vote") == [] and read_calls(log_path) == []


def test_verify_rejection_casts_an_evidence_vote_and_waits_for_the_outcome(tmp_path):
    run, common, log_path = _setup(tmp_path, "shadow")
    _reject(run, common, tmp_path)
    votes = _events(run, "shadow_vote")
    assert len(votes) == 1
    vote = votes[0]
    assert vote["decision"] == "rejection_action" and vote["source"] == "run_loop"
    assert vote["options"] == ["fix", "reopen", "escalate"]
    assert vote["authority"] == "lead" and vote["additive"] is True
    assert set(vote["answers"]) == {"rejection_environment", "rejection_spec_conflict", "rejection_needs_human"}
    assert vote["choice"] == "escalate" and vote["rule"] == "needs_human"
    assert vote["uncertain"] is False and vote["model"] == "fake-jev"
    assert check_event(vote) == []
    assert [call["kind"] for call in read_calls(log_path)] == ["noul", "noul", "noul"]
    state = json.loads(read_calls(log_path)[0]["state"])
    assert state["rejection_reason"].startswith("acceptance failed") and state["failing_checks"][0]["id"] == "A1"
    assert _events(run, "decision_outcome") == []
    # The run loop's real state is untouched by the escalate vote.
    assert _events(run)[-1]["type"] == "shadow_vote"
    assert [event["type"] for event in _events(run)][-3:-1] == ["verified", "rejected"]


def test_reopen_records_the_real_action_once(tmp_path):
    run, common, _log = _setup(tmp_path, "shadow")
    _reject(run, common, tmp_path)
    assert main(["reopen", "--task", "T1", "--reason", "lead repaired the check", "--now", "5"] + common) == 0
    outcomes = _events(run, "decision_outcome")
    assert [(event["decision"], event["choice"], event["source"]) for event in outcomes] == [
        ("rejection_action", "reopen", "lead")]
    assert check_event(outcomes[0]) == []


def test_fix_records_fix_outcome(tmp_path):
    run, common, _log = _setup(tmp_path, "shadow")
    _reject(run, common, tmp_path)
    assert main(["fix", "--task", "T1", "--now", "5"] + common) == 0
    outcomes = _events(run, "decision_outcome")
    assert [(event["task_id"], event["choice"]) for event in outcomes] == [("T1", "fix")]


def test_reopen_without_a_prior_vote_records_no_outcome(tmp_path):
    run, common, _log = _setup(tmp_path, "shadow")
    run_events = run / "events.jsonl"
    assert main(["claim", "--task", "T1", "--agent", "a1", "--now", "2"] + common) == 0
    assert main(["submit", "--task", "T1", "--agent", "a1", "--summary", "done", "--now", "3"] + common) == 0
    # Reject by hand without shadow collection: remove the vote the verify site wrote.
    assert main(["verify", "--task", "T1", "--cwd", str(tmp_path), "--now", "4"] + common) == 1
    lines = [line for line in run_events.read_text().splitlines() if '"shadow_vote"' not in line]
    run_events.write_text("\n".join(lines) + "\n")
    assert main(["reopen", "--task", "T1", "--reason", "lead repair", "--now", "5"] + common) == 0
    assert _events(run, "decision_outcome") == []


@pytest.mark.parametrize("judge_default", ["off", None])
def test_off_and_legacy_rosters_collect_nothing_at_run_sites(tmp_path, judge_default):
    run, common, log_path = _setup(tmp_path, judge_default)
    _reject(run, common, tmp_path)
    assert main(["fix", "--task", "T1", "--now", "5"] + common) == 0
    assert _events(run, "shadow_vote") == [] and _events(run, "decision_outcome") == []
    assert read_calls(log_path) == []


class _Judge:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def noul(self, key, question, state):
        self.asked.append(key)
        value = self.answers.get(key)
        if value is None:
            return {"key": key, "p": None, "model": "m", "detail": {"error": "timeout", "latency_ms": 7}}
        return {"key": key, "p": value, "model": "m", "detail": {"latency_ms": 5}}


def test_evidence_vote_records_uncertain_and_missing_answers():
    roster = {"judge": {"questions": {"over_an_hour": "Q1?", "unattended": "Q2?"}}}
    uncertain = EV.evidence_vote(_Judge({"over_an_hour": 0.55, "unattended": 0.1}), "lane", roster, "{}",
                                 {"role": "backend", "independent_tasks": 0})
    assert uncertain["choice"] == "pane" and uncertain["uncertain"] is True
    assert uncertain["latency_ms"] == 10 and uncertain["answers"] == {"over_an_hour": 0.55, "unattended": 0.1}
    missing = EV.evidence_vote(_Judge({"unattended": 0.1}), "lane", roster, "{}",
                               {"role": "backend", "independent_tasks": 0})
    assert missing["choice"] is None and missing["rule"] == "missing_evidence"
    assert "timeout" in missing["error"]


def test_evidence_vote_without_a_roster_question_abstains_and_asks_nothing():
    judge = _Judge({"over_an_hour": 0.9, "unattended": 0.9})
    vote = EV.evidence_vote(judge, "lane", {"judge": {"questions": {}}}, "{}",
                            {"role": "backend", "independent_tasks": 0})
    assert judge.asked == [] and vote["choice"] is None and "missing_question" in vote["error"]


def test_shared_evidence_is_asked_once_per_state():
    roster = {"judge": {"questions": {"over_an_hour": "Q1?", "unattended": "Q2?", "external_side_effects": "Q3?"}}}
    judge = _Judge({"over_an_hour": 0.1, "unattended": 0.1, "external_side_effects": 0.1})
    cache = {}
    EV.evidence_vote(judge, "lane", roster, "{}", {"role": "backend", "independent_tasks": 0}, cache)
    EV.evidence_vote(judge, "needs_monitor", roster, "{}", {"risk": "low"}, cache)
    assert judge.asked == ["over_an_hour", "unattended", "external_side_effects"]


def test_shared_evidence_latency_is_counted_once_per_call():
    from ale.labeling.shadow import summarize_shadow
    roster = {"judge": {"questions": {"over_an_hour": "Q1?", "unattended": "Q2?", "external_side_effects": "Q3?"}}}
    judge = _Judge({"over_an_hour": 0.1, "unattended": 0.1, "external_side_effects": 0.1})
    cache = {}
    lane = EV.evidence_vote(judge, "lane", roster, "{}", {"role": "backend", "independent_tasks": 0}, cache)
    monitor = EV.evidence_vote(judge, "needs_monitor", roster, "{}", {"risk": "low"}, cache)
    assert lane["calls_latency_ms"] == [5, 5] and lane["latency_ms"] == 10
    assert monitor["calls_latency_ms"] == [5] and monitor["latency_ms"] == 5
    stats = summarize_shadow([dict(lane, task_id="T1"), dict(monitor, task_id="T1")], [], [], {})
    assert stats["decisions"]["lane"]["latency_ms_median"] == 5
    assert stats["decisions"]["needs_monitor"]["latency_ms_median"] == 5
