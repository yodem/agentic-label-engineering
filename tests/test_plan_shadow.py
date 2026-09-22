"""Judge on vs off: the same CLI run leaves labels, lane, routing and run state identical.

Both runs drive every judged firing site (bake, init-run import, dispatch,
verify rejection, fix, fix-of-fix, monitor breach) through real CLI commands.
The shadow run uses a fake judge whose answers conflict with the planner.
"""

import glob
import json
import os

import pytest

from ale import events as E
from ale import labelset as L

from judge_fakes import LANE_REASON, drive_run, read_calls, setup_repo

SHADOW_TYPES = ("shadow_vote", "decision_outcome")


def _run(base, monkeypatch, judge_default):
    os.makedirs(str(base))
    repo, roster, _value, log_path = setup_repo(base, monkeypatch, judge_default, "conflict")
    run_dir, codes = drive_run(repo, roster)
    events = E.read_events(os.path.join(run_dir, "events.jsonl"))
    labels = L.load_labels(run_dir)
    with open(os.path.join(repo, "plan.md"), encoding="utf-8") as handle:
        plan_text = handle.read()
    requests = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "requests", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            request = json.load(handle)
        requests[os.path.basename(path)] = {key: request.get(key) for key in (
            "task_id", "executor", "model", "trigger_instance", "agent_id")}
    return {"codes": codes, "events": events, "labels": labels, "plan": plan_text,
            "requests": requests, "calls": read_calls(log_path), "repo": repo}


def _authority(label):
    """Everything in a label except the judge's own recorded votes."""
    value = json.loads(json.dumps(label))
    for field, entry in (value.get("provenance") or {}).items():
        if isinstance(entry, dict):
            entry.pop("votes", None)
    return value


def _plain_events(events, repo):
    rows = []
    for event in events:
        if event["type"] in SHADOW_TYPES:
            continue
        row = json.loads(json.dumps(event, sort_keys=True).replace(repo, "<repo>"))
        # The two rosters differ only in judge.default (and the fake judge path),
        # so their hashes differ by construction.
        row.pop("roster_hash", None)
        rows.append(row)
    return rows


@pytest.fixture
def both_runs(tmp_path, monkeypatch):
    shadow = _run(tmp_path / "on", monkeypatch, "shadow")
    off = _run(tmp_path / "off", monkeypatch, "off")
    return shadow, off


def test_judge_on_vs_off_leaves_authority_and_state_identical(both_runs):
    shadow, off = both_runs
    assert shadow["codes"] == off["codes"]
    assert shadow["plan"] == off["plan"]
    assert sorted(shadow["labels"]) == sorted(off["labels"])
    for task_id in shadow["labels"]:
        assert _authority(shadow["labels"][task_id]) == _authority(off["labels"][task_id])
    assert shadow["requests"] == off["requests"]
    assert (E.reduce_run(shadow["events"], shadow["labels"])
            == E.reduce_run(off["events"], off["labels"]))
    assert _plain_events(shadow["events"], shadow["repo"]) == _plain_events(off["events"], off["repo"])


def test_conflicting_votes_were_really_cast_in_the_shadow_run(both_runs):
    shadow, off = both_runs
    votes = [event for event in shadow["events"] if event["type"] == "shadow_vote"]
    outcomes = {(event["task_id"], event["decision"]): event["choice"]
                for event in shadow["events"] if event["type"] == "decision_outcome"}
    disagreements = [vote for vote in votes
                     if (vote["task_id"], vote["decision"]) in outcomes
                     and vote["choice"] != outcomes[(vote["task_id"], vote["decision"])]]
    assert len(disagreements) >= 8
    # WRK-46: Jev's lane vote conflicts, the planner's lane and lane_reason stand.
    lane_votes = [vote for vote in votes if vote["decision"] == "lane"]
    assert lane_votes and all(vote["choice"] == "pane" for vote in lane_votes)
    for task_id in ("T1", "T2"):
        assert shadow["labels"][task_id]["labels"]["lane"] == "inline"
        assert shadow["labels"][task_id]["provenance"]["lane_reason"] == LANE_REASON


def test_off_default_makes_no_judge_call_and_no_shadow_event(both_runs):
    _shadow, off = both_runs
    assert off["calls"] == []
    assert not [event for event in off["events"] if event["type"] in SHADOW_TYPES]
    assert not os.path.exists(os.path.join(off["repo"], ".ale", "shadow"))
