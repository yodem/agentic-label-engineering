"""``ale claim`` refuses a worktree task that ALE never spawned, unless the lead records a deviation."""
import json
import os
import subprocess

import pytest

from ale import events as E
from ale import labelset as L
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REASON = "the task needs the host checkout"


def make_run(tmp_path, mode="per_task", extra_labels=()):
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.com"],
                ["git", "config", "user.name", "Test"]):
        subprocess.run(cmd, cwd=str(repo), check=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=str(repo), check=True)
    run = repo / ".ale" / "runs" / "run-1"
    (run / "labels").mkdir(parents=True)
    for task_id in ("T1",) + tuple(extra_labels):
        label = {
            "schema_version": "1.0", "run_id": "run-1", "task_id": task_id, "title": "Task %s" % task_id,
            "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
            "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["%s.txt" % task_id.lower()],
                        "depends_on": []},
            "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
            "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                             "executor": "claude-headless", "trigger": "ready"}],
        }
        if mode is not None:
            label["context"]["worktree"] = {"mode": mode, "branch": None, "base": None}
        (run / "labels" / ("%s.json" % task_id)).write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        roster.write_text(handle.read())
    return repo, run, str(roster)


def ale(run, roster, *args):
    return main(list(args) + ["--run-dir", str(run), "--roster", roster])


def claim(run, roster, agent="lead-1", *extra, task="T1"):
    return ale(run, roster, "claim", "--task", task, "--agent", agent, *extra)


def events(run, kind=None):
    rows = E.read_events(str(run / "events.jsonl"))
    return [e for e in rows if kind is None or e["type"] == kind]


def spawn(run, roster, repo):
    """The dispatch an in-session lead runs: a spawn event without a real executor."""
    os.environ["ALE_SPAWN_BIN"] = "/usr/bin/true"
    try:
        assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo)) == 0
    finally:
        del os.environ["ALE_SPAWN_BIN"]
    assert events(run, "spawned")


def test_claim_without_spawn_is_refused_with_the_dispatch_command(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    assert claim(run, roster) == 1
    err = capsys.readouterr().err
    assert "ALE_SPAWN_BIN=/usr/bin/true ale dispatch --run-dir %s --cwd %s --spawn" % (run, repo) in err
    assert '--no-worktree --reason "<why>"' in err
    assert events(run, "claimed") == []


def test_claim_after_a_dispatch_spawn_is_allowed(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster) == 0
    assert len(events(run, "claimed")) == 1
    assert events(run, "note") == []


def test_claim_after_register_worktree_is_allowed(tmp_path):
    repo, run, roster = make_run(tmp_path)
    path = run / "wt" / "T1"
    subprocess.run(["git", "worktree", "add", "-q", str(path), "-b", "ale/run-1/T1", "HEAD"],
                   cwd=str(repo), check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo), text=True).strip()
    assert ale(run, roster, "register-worktree", "--task", "T1", "--path", str(path), "--branch", "ale/run-1/T1",
               "--base", base, "--cwd", str(repo)) == 0
    assert claim(run, roster, "remote-1") == 0


@pytest.mark.parametrize("mode", ["none", None])
def test_tasks_without_a_worktree_are_unaffected(tmp_path, mode):
    _repo, run, roster = make_run(tmp_path, mode=mode)
    assert claim(run, roster) == 0


def test_an_unclaimable_task_still_reports_claim_lost(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    # Already owned: the race is lost (3), not refused (1), even though nothing is spawned for a2.
    assert claim(run, roster, "a2") == 3


def test_an_unknown_task_is_still_a_check_failure(tmp_path):
    _repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, task="T9") == 1


def test_no_worktree_with_a_reason_claims_and_records_the_deviation(tmp_path):
    _repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, "lead-1", "--no-worktree", "--reason", REASON) == 0
    claimed = events(run, "claimed")
    notes = events(run, "note")
    assert len(claimed) == 1 and len(notes) == 1
    assert notes[0]["text"] == "deviation worktree-outside-ale: " + REASON
    assert notes[0]["task_id"] == "T1" and notes[0]["agent_id"] == "lead-1"
    log = E.read_events(str(run / "events.jsonl"))
    assert log.index(notes[0]) > log.index(claimed[0])
    state = E.reduce_run(log, L.load_labels(str(run)))
    assert state["tasks"]["T1"]["notes"] == ["deviation worktree-outside-ale: " + REASON]


def test_no_worktree_needs_a_reason_of_ten_characters(tmp_path, capsys):
    _repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, "lead-1", "--no-worktree") == 2
    assert claim(run, roster, "lead-1", "--no-worktree", "--reason", "too short") == 2
    assert claim(run, roster, "lead-1", "--no-worktree", "--reason", "   padded   ") == 2
    assert events(run, "claimed") == []


def test_reason_without_no_worktree_is_a_usage_error(tmp_path):
    _repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, "lead-1", "--reason", REASON) == 2
    assert events(run, "claimed") == []


def test_the_override_on_a_worktree_none_task_writes_no_note(tmp_path):
    _repo, run, roster = make_run(tmp_path, mode="none")
    assert claim(run, roster, "lead-1", "--no-worktree", "--reason", REASON) == 0
    assert events(run, "note") == []


def test_a_lost_claim_with_the_override_writes_no_note(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    assert claim(run, roster, "a2", "--no-worktree", "--reason", REASON) == 3
    assert events(run, "note") == []


def test_a_reclaim_after_a_rejection_keeps_the_original_spawn(tmp_path):
    """A rejected task has no new dispatch (its ready spawn key is already used), so the
    attempt-2 claim runs in the same ALE worktree and must pass."""
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    assert ale(run, roster, "submit", "--task", "T1", "--agent", "a1", "--summary", "done") == 0
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "rejected", "run-1", 5000.0, "T1", None, 1, evidence={"checks": []}, reason="redo"))
    assert events(run, "spawned")[-1]["attempt"] == 1
    assert claim(run, roster, "a2") == 0
    assert events(run, "claimed")[-1]["attempt"] == 2


def test_a_released_claim_needs_a_new_spawn(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    E.append_event(str(run / "events.jsonl"), E.make_event("lease_expired", "run-1", 5000.0, "T1", None, 1))
    E.append_event(str(run / "events.jsonl"), E.make_event("released", "run-1", 5001.0, "T1", None, 1))
    assert claim(run, roster, "a2") == 1          # the old spawn was released: dispatch again
    spawn(run, roster, repo)
    assert claim(run, roster, "a2") == 0
