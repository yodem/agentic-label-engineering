"""``ale claim`` refuses a worktree task that ALE never spawned, unless the lead records a deviation."""
import copy
import json
import os
import shlex
import subprocess
import sys

import pytest

from ale import evalcases as EC
from ale import events as E
from ale import labelset as L
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REASON = "the task needs the host checkout"


def make_run(tmp_path, mode="per_task", extra_labels=(), paths=True, cap=None, label_paths=None, monitors=False,
             executor="claude-headless", lane="inline", remote_host=None):
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
            "labels": {"role": "backend", "model_tier": "standard", "lane": lane, "risk": "low", "effort": "S"},
            "context": {"spec_path": "spec.md", "pointers": [],
                        "allowed_paths": (label_paths or {}).get(task_id, ["%s.txt" % task_id.lower()]) if paths else [],
                        "depends_on": []},
            "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
            "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                             "executor": executor, "trigger": "ready"}],
        }
        if monitors:
            label["assignments"].append({"kind": "monitor", "role": "backend", "model_tier": "standard",
                                         "executor": "claude-headless", "trigger": "ready"})
        if mode is not None:
            label["context"]["worktree"] = {"mode": mode, "branch": None, "base": None}
        (run / "labels" / ("%s.json" % task_id)).write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    if remote_host:
        data["remote_host"] = remote_host
    if cap is not None:
        data["cost_gate"]["max_concurrent"] = cap
        data["cost_gate"].pop("max_parallel", None)
    roster.write_text(json.dumps(data))
    return repo, run, str(roster)


def ale(run, roster, *args, now=None):
    tail = ["--now", str(now)] if now is not None else []
    return main(list(args) + ["--run-dir", str(run), "--roster", roster] + tail)


def claim(run, roster, agent="lead-1", *extra, task="T1", now=None):
    return ale(run, roster, "claim", "--task", task, "--agent", agent, *extra, now=now)


def events(run, kind=None):
    rows = E.read_events(str(run / "events.jsonl"))
    return [e for e in rows if kind is None or e["type"] == kind]


def spawned_tasks(run):
    return sorted(e["task_id"] for e in events(run, "spawned"))


def spawn(run, roster, repo, *tasks):
    """A dispatch --spawn; the root conftest's spawn guard makes the executor a no-op."""
    before = len(events(run, "spawned"))
    flags = [arg for task in tasks for arg in ("--task", task)]
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), *flags) == 0
    assert len(events(run, "spawned")) > before


def reject(run):
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "rejected", "run-1", 5000.0, "T1", None, 1, evidence={"checks": []}, reason="redo"))


def foreign_spawn(run, kind="monitor"):
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "spawned", "run-1", 5000.0, "T1", None, 2, agent_id_minted="T1-%s-backend-1" % kind,
        assignment_kind=kind, executor="claude-headless", model=None, trigger_instance="submit:1"))


def test_claim_without_spawn_is_refused_with_the_dispatch_command(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    assert claim(run, roster) == 1
    err = capsys.readouterr().err
    assert "ALE has not spawned it" in err
    assert ("ale dispatch --run-dir %s --cwd %s --roster %s --task T1 --no-exec"
            % (run, repo, roster)) in err
    assert "For a lead working the task in-session" in err
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


def test_worktree_none_is_unaffected(tmp_path):
    _repo, run, roster = make_run(tmp_path, mode="none")
    assert claim(run, roster) == 0


def test_a_label_with_paths_and_no_worktree_block_is_per_task(tmp_path):
    """The gate reads dispatch's own effective mode: allowed_paths without context.worktree is per_task."""
    _repo, run, roster = make_run(tmp_path, mode=None)
    assert claim(run, roster) == 1
    assert events(run, "claimed") == []


def test_a_label_with_paths_and_no_worktree_block_passes_after_a_spawn(tmp_path):
    repo, run, roster = make_run(tmp_path, mode=None)
    spawn(run, roster, repo)
    assert claim(run, roster) == 0


def test_the_gate_reads_the_event_log_once(tmp_path, monkeypatch):
    """The gate takes its spawn bookkeeping from _dispatch_state, the same read that decides
    claimability: Ctx's dynamic-label read plus one state read, no third pass."""
    _repo, run, roster = make_run(tmp_path)
    from ale import cli
    reads = []
    real = cli.E.read_events
    monkeypatch.setattr(cli.E, "read_events", lambda path: reads.append(path) or real(path))
    assert claim(run, roster) == 1
    assert len(reads) == 2, reads


def test_a_label_with_no_paths_and_no_worktree_block_is_unaffected(tmp_path):
    _repo, run, roster = make_run(tmp_path, mode=None, paths=False)
    assert claim(run, roster) == 0


def test_the_refusal_names_the_explicit_roster_and_quotes_paths(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    capsys.readouterr()
    assert claim(run, roster) == 1
    err = capsys.readouterr().err
    assert "--cwd %s --roster %s --task T1 --no-exec" % (shlex.quote(str(repo)), shlex.quote(roster)) in err


def test_the_refusal_uses_the_roster_env_when_no_flag_is_given(tmp_path, capsys, monkeypatch):
    repo, run, roster = make_run(tmp_path)
    monkeypatch.setenv("ALE_ROSTER", roster)
    capsys.readouterr()
    assert main(["claim", "--task", "T1", "--agent", "lead-1", "--run-dir", str(run)]) == 1
    assert "--roster %s --task T1 --no-exec" % shlex.quote(roster) in capsys.readouterr().err


def test_the_refusal_quotes_a_path_with_a_space(tmp_path, capsys):
    spaced = tmp_path / "a b"
    spaced.mkdir()
    repo, run, roster = make_run(spaced)
    capsys.readouterr()
    assert claim(run, roster) == 1
    err = capsys.readouterr().err
    assert "--run-dir %s --cwd %s" % (shlex.quote(str(run)), shlex.quote(str(repo))) in err


def test_a_shared_mode_refusal_does_not_say_outside_a_worktree(tmp_path, capsys):
    _repo, run, roster = make_run(tmp_path, mode="shared")
    assert claim(run, roster) == 1
    err = capsys.readouterr().err
    assert "ALE has not spawned it" in err and "worktree mode shared" in err
    assert "outside ALE's worktree" not in err


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
    reject(run)
    assert events(run, "spawned")[-1]["attempt"] == 1
    assert claim(run, roster, "a2") == 0
    assert events(run, "claimed")[-1]["attempt"] == 2


def test_a_released_task_is_reclaimed_in_its_existing_worktree(tmp_path):
    """Any spawn in any attempt counts while its worktree exists: a release does not strand the rework."""
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    E.append_event(str(run / "events.jsonl"), E.make_event("lease_expired", "run-1", 5000.0, "T1", None, 1))
    E.append_event(str(run / "events.jsonl"), E.make_event("released", "run-1", 5001.0, "T1", None, 1))
    assert claim(run, roster, "a2") == 0


def test_a_spawn_whose_worktree_is_gone_does_not_count(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    subprocess.run(["git", "worktree", "remove", "--force", str(run / "wt" / "T1")], cwd=str(repo), check=True)
    capsys.readouterr()
    assert claim(run, roster, "a1") == 1
    assert "ALE has not spawned it" in capsys.readouterr().err
    assert events(run, "claimed") == []


def test_a_failed_monitor_spawn_does_not_end_the_executor_spawn(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1") == 0
    assert ale(run, roster, "submit", "--task", "T1", "--agent", "a1", "--summary", "done") == 0
    reject(run)
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "released", "run-1", 5001.0, "T1", None, 2, reason="spawn failed: 1",
        spawn_key=["T1", "monitor", "submit:1"]))
    assert claim(run, roster, "a2") == 0


def test_a_failed_executor_spawn_leaves_its_worktree_to_claim(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "released", "run-1", 5001.0, "T1", None, 1, reason="spawn failed: 1",
        spawn_key=["T1", "executor", "ready"]))
    assert claim(run, roster, "a1") == 0


def test_a_monitor_spawn_is_not_a_worktree_spawn(tmp_path):
    _repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, "a1", "--no-worktree", "--reason", REASON) == 0
    assert ale(run, roster, "submit", "--task", "T1", "--agent", "a1", "--summary", "done") == 0
    reject(run)
    foreign_spawn(run, "monitor")
    assert claim(run, roster, "a2") == 1          # the monitor's spawn is no executor worktree
    assert claim(run, roster, "a2", "--no-worktree", "--reason", REASON) == 0


def test_an_agent_authored_spawn_event_is_ignored(tmp_path):
    _repo, run, roster = make_run(tmp_path)
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "spawned", "run-1", 5000.0, "T1", "a1", 1, agent_id_minted="x-1", assignment_kind="executor",
        executor="external", model=None))
    assert claim(run, roster, "a1") == 1


def test_the_real_watchdog_lease_expiry_keeps_the_worktree_claimable(tmp_path):
    repo, run, roster = make_run(tmp_path)
    spawn(run, roster, repo)
    assert claim(run, roster, "a1", now=0) == 0
    assert ale(run, roster, "watchdog", now=2000) == 6
    assert events(run, "released")
    assert claim(run, roster, "a2", now=2001) == 0       # same worktree, no new dispatch


# --- rework claims and the --no-exec remedy (review item 1) ---------------------------------

def remedy(err):
    """The remedy command line the refusal prints, as argv for main() (without the leading ale)."""
    line = next(row.strip() for row in err.splitlines() if row.strip().startswith("ale dispatch "))
    return shlex.split(line)[1:]


def set_acceptance(run, cmd, task="T1"):
    path = run / "labels" / ("%s.json" % task)
    label = json.loads(path.read_text())
    label["acceptance"] = [{"id": "A1", "cmd": cmd, "expect": "exit0"}]
    path.write_text(json.dumps(label))


def failing_executor(tmp_path):
    """A spawn bin that claims and submits as the minted executor, then exits non-zero."""
    script = tmp_path / "failing-spawn"
    script.write_text(
        "#!%s\n"
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from ale.cli import main\n"
        "request = json.load(open(sys.argv[1]))\n"
        "env = request['env']\n"
        "common = ['--run-dir', env['ALE_RUN_DIR'], '--roster', env['ALE_ROSTER']]\n"
        "assert main(['claim', '--task', env['ALE_TASK'], '--agent', env['ALE_AGENT']] + common) == 0\n"
        "assert main(['submit', '--task', env['ALE_TASK'], '--agent', env['ALE_AGENT'], '--summary', 'x'] + common) == 0\n"
        "sys.exit(3)\n" % (sys.executable, ROOT))
    script.chmod(0o755)
    return str(script)


def test_a_failed_headless_spawn_then_a_rejection_is_reclaimed_by_the_lead(tmp_path, monkeypatch):
    """(a) headless spawn, the executor submits and exits non-zero (released), verify rejects:
    the lead's reclaim works in the same worktree."""
    repo, run, roster = make_run(tmp_path)
    set_acceptance(run, "test -f t1.txt")
    monkeypatch.setenv("ALE_SPAWN_BIN", failing_executor(tmp_path))
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T1") == 0
    assert [e["type"] for e in events(run) if e["type"] in ("claimed", "submitted", "released")] == \
        ["claimed", "submitted", "released"]
    assert ale(run, roster, "verify", "--task", "T1", "--cwd", str(run / "wt" / "T1")) == 1
    assert events(run, "rejected")
    assert claim(run, roster, "lead-1") == 0
    assert events(run, "claimed")[-1]["attempt"] == 2


def test_a_no_worktree_claim_then_a_rejection_is_refused_with_the_no_exec_remedy(tmp_path, capsys):
    """(b) the first claim used --no-worktree, so no ALE worktree exists; the rework claim is
    refused with the --no-exec remedy, which records the spawn and its worktree, and launches nothing."""
    repo, run, roster = make_run(tmp_path)
    assert claim(run, roster, "a1", "--no-worktree", "--reason", REASON) == 0
    assert ale(run, roster, "submit", "--task", "T1", "--agent", "a1", "--summary", "done") == 0
    reject(run)
    capsys.readouterr()
    assert claim(run, roster, "lead-1") == 1
    err = capsys.readouterr().err
    assert "ALE_SPAWN_BIN" not in err and "=/" not in err
    argv = remedy(err)
    assert argv == ["dispatch", "--run-dir", str(run), "--cwd", str(repo), "--roster", roster,
                    "--task", "T1", "--no-exec"]
    assert main(argv) == 0
    spawned = events(run, "spawned")
    assert len(spawned) == 1 and os.path.isdir(spawned[0]["worktree"])
    assert claim(run, roster, "lead-1") == 0
    assert events(run, "claimed")[-1]["attempt"] == 2


def test_the_remedy_on_a_fresh_task_records_the_spawn_and_launches_nothing(tmp_path, capsys, spawn_guard):
    repo, run, roster = make_run(tmp_path)
    capsys.readouterr()
    assert claim(run, roster) == 1
    assert main(remedy(capsys.readouterr().err)) == 0
    assert spawned_tasks(run) == ["T1"]
    with open(spawn_guard["log"], encoding="utf-8") as handle:
        assert len(handle.read().splitlines()) == spawn_guard["before"]     # nothing was launched
    assert claim(run, roster) == 0


# --- remote-routed tasks (review item 2) ----------------------------------------------------

def test_a_remote_routed_task_is_refused_then_the_remedy_lets_it_claim(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path, lane="pane", remote_host="dev-server")
    capsys.readouterr()
    assert claim(run, roster, "lead-1") == 1
    assert main(remedy(capsys.readouterr().err)) == 0
    assert events(run, "released") == []                  # no unprovisioned-remote release: no loop
    assert os.path.isdir(events(run, "spawned")[0]["worktree"])
    assert claim(run, roster, "lead-1") == 0


def test_an_unprovisioned_remote_spawn_does_not_strand_the_remedy(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path, lane="pane", remote_host="dev-server")
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T1") == 0
    assert [e["reason"] for e in events(run, "released")] == ["remote worktree not provisioned"]
    capsys.readouterr()
    assert claim(run, roster, "lead-1") == 1
    assert main(remedy(capsys.readouterr().err)) == 0
    assert claim(run, roster, "lead-1") == 0


def test_a_remote_worktree_spawn_satisfies_the_gate(tmp_path, monkeypatch):
    repo, run, roster = make_run(tmp_path, lane="pane", remote_host="dev-server")
    monkeypatch.setenv("ALE_REMOTE_WORKTREE", "/srv/remote/wt/T1")
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T1") == 0
    spawned = events(run, "spawned")
    assert spawned[0]["remote_worktree"] == "/srv/remote/wt/T1" and "worktree" not in spawned[0]
    assert claim(run, roster, "remote-1") == 0


# --- no_exec spawns are lead work (review item 4) -------------------------------------------

def test_a_no_exec_spawn_records_lead_work_not_the_routed_executor(tmp_path):
    repo, run, roster = make_run(tmp_path, executor="codex-exec")
    assert ale(run, roster, "dispatch", "--no-exec", "--cwd", str(repo), "--task", "T1") == 0
    spawned = events(run, "spawned")[0]
    assert spawned["no_exec"] is True
    assert spawned["executor"] == "lead" and spawned["agent_id_minted"] == "lead"
    assert spawned["model"] is None and "mode" not in spawned
    assert "codex" not in json.dumps(spawned) and "T1-executor" not in json.dumps(spawned)
    assert os.path.isdir(spawned["worktree"])


def test_a_launched_spawn_keeps_its_routed_executor(tmp_path):
    repo, run, roster = make_run(tmp_path, executor="codex-exec")
    spawn(run, roster, repo)
    spawned = events(run, "spawned")[0]
    assert "no_exec" not in spawned
    assert spawned["executor"] == "codex-exec" and spawned["agent_id_minted"] == "T1-executor-backend-1"


# --- ale dispatch --task -------------------------------------------------------------------

def test_dispatch_task_spawns_only_the_named_task(tmp_path):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2", "T3"))
    spawn(run, roster, repo, "T2")
    assert spawned_tasks(run) == ["T2"]
    assert claim(run, roster, "a1", task="T2") == 0
    assert claim(run, roster, "a1", task="T1") == 1      # T1 stays unspawned, not stranded as spawned


def test_dispatch_task_is_repeatable(tmp_path):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2", "T3"))
    spawn(run, roster, repo, "T1", "T3")
    assert spawned_tasks(run) == ["T1", "T3"]


def test_dispatch_task_is_not_starved_by_the_parallel_cap(tmp_path):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2", "T3"), cap=1)
    spawn(run, roster, repo, "T3")
    assert spawned_tasks(run) == ["T3"]


def test_dispatch_task_is_not_blocked_by_an_overlapping_unnamed_task(tmp_path):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2",), label_paths={"T2": ["t1.txt", "t2.txt"]})
    spawn(run, roster, repo, "T2")
    assert spawned_tasks(run) == ["T2"]
    assert claim(run, roster, "a1", task="T2") == 0


def test_dispatch_task_with_two_assignments_per_task_at_cap_one(tmp_path, capsys):
    """The cap counts assignments (an executor and a monitor per task), as in a plain dispatch:
    T4 is still reached when named alone, and a second named task is reported as skipped."""
    repo, run, roster = make_run(tmp_path, extra_labels=("T2", "T3", "T4"), cap=1, monitors=True)
    spawn(run, roster, repo, "T4")
    assert "T4" in spawned_tasks(run) and "T1" not in spawned_tasks(run) and "T3" not in spawned_tasks(run)
    capsys.readouterr()
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T2", "--task", "T3") == 0
    err = capsys.readouterr().err
    assert "T3" in err and "parallel cap" in err
    assert "T2" in spawned_tasks(run)


def test_dispatch_task_does_not_reprint_other_tasks_unclaimed_requests(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2",), executor="claude-subagent")
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo)) == 0
    assert spawned_tasks(run) == ["T1", "T2"]
    capsys.readouterr()
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T1") == 0
    out = capsys.readouterr().out
    assert '"task_id": "T1"' in out and '"task_id": "T2"' not in out
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo)) == 0
    out = capsys.readouterr().out
    assert '"task_id": "T1"' in out and '"task_id": "T2"' in out


def test_dispatch_task_lists_only_the_named_task_in_json(tmp_path, capsys):
    _repo, run, roster = make_run(tmp_path, extra_labels=("T2",))
    capsys.readouterr()
    assert ale(run, roster, "dispatch", "--json", "--task", "T2") == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert [row["task_id"] for row in rows] == ["T2"]


def test_dispatch_with_an_unknown_task_is_a_usage_error(tmp_path):
    repo, run, roster = make_run(tmp_path)
    assert ale(run, roster, "dispatch", "--spawn", "--cwd", str(repo), "--task", "T9") == 2
    assert events(run, "spawned") == []


def test_dispatch_without_task_still_spawns_every_due_task(tmp_path):
    repo, run, roster = make_run(tmp_path, extra_labels=("T2",))
    spawn(run, roster, repo)
    assert spawned_tasks(run) == ["T1", "T2"]


# --- the `claim` eval case kind ------------------------------------------------------------

def _case(case_id):
    return next(c for c in EC.load_cases(EC.default_cases_path()) if c["id"] == case_id)


@pytest.mark.parametrize("case_id", ["claim-without-spawn-refused", "claim-after-spawn-allowed"])
def test_the_claim_seed_cases_pass(case_id):
    result = EC.run_case(_case(case_id))
    assert result["passed"] and result["score"] == 1.0, result


def test_the_seed_cases_pin_the_gate_and_its_positive_control():
    refused, allowed = _case("claim-without-spawn-refused"), _case("claim-after-spawn-allowed")
    assert refused["expected"] == {"allowed": False, "exit": 1, "refused": True}
    assert allowed["expected"] == {"allowed": True, "exit": 0, "refused": False}
    assert refused["input"]["roster"] == allowed["input"]["roster"]      # one small shared roster
    assert len(json.dumps(refused["input"]["roster"])) < 1500


def test_a_broken_roster_is_not_mistaken_for_the_gate():
    """main() also exits 1 on a bad roster; only the gate's own message counts as refused."""
    case = copy.deepcopy(_case("claim-without-spawn-refused"))
    case["input"]["roster"] = {"schema_version": "1.0"}
    result = EC.run_case(case)
    assert not result["passed"] and "refused" in result["reason"]
    assert result["actual"] == {"allowed": False, "exit": 1, "refused": False}


def test_the_gate_removed_would_fail_the_refused_case():
    case = copy.deepcopy(_case("claim-without-spawn-refused"))
    case["input"]["args"] = ["--no-worktree", "--reason", "the host checkout is needed"]
    result = EC.run_case(case)
    assert not result["passed"] and result["actual"] == {"allowed": True, "exit": 0, "refused": False}


@pytest.mark.parametrize("args", ["--no-worktree", [1, 2], ["--ok", None], {"a": 1}])
def test_claim_case_args_must_be_a_list_of_strings(args):
    case = copy.deepcopy(_case("claim-without-spawn-refused"))
    case["input"]["args"] = args
    result = EC.run_case(case)
    assert not result["passed"] and "args" in result["reason"] and result["actual"] is None


def test_the_positive_control_fails_when_its_worktree_is_gone():
    case = copy.deepcopy(_case("claim-after-spawn-allowed"))
    case["input"]["worktrees"] = []
    result = EC.run_case(case)
    assert not result["passed"] and result["actual"] == {"allowed": False, "exit": 1, "refused": True}


@pytest.mark.parametrize("worktrees", ["wt/T1", ["/abs"], ["../up"], [""]])
def test_claim_case_worktrees_must_be_run_relative(worktrees):
    case = copy.deepcopy(_case("claim-after-spawn-allowed"))
    case["input"]["worktrees"] = worktrees
    result = EC.run_case(case)
    assert not result["passed"] and "worktrees" in result["reason"] and result["actual"] is None


def test_the_claim_case_leaves_the_fixture_and_the_environment_alone(monkeypatch):
    monkeypatch.delenv("ALE_HERDR", raising=False)
    case = _case("claim-after-spawn-allowed")
    base = os.path.join(ROOT, "evalcases", case["input"]["dir"])
    before = sorted(os.listdir(base)), open(os.path.join(base, "events.jsonl")).read()
    assert EC.run_case(case)["passed"]
    assert (sorted(os.listdir(base)), open(os.path.join(base, "events.jsonl")).read()) == before
    assert "ALE_HERDR" not in os.environ
