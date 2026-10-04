"""Regression tests for run-record defects that corrupted the feedback loop's inputs."""

import argparse
import json
import os
import subprocess

from ale import events as E
from ale import cli
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(cwd, *args):
    return subprocess.check_output(["git"] + list(args), cwd=str(cwd), text=True).strip()


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)
    (path / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=str(path), check=True)
    return path


def _run(tmp_path, allowed=("a.txt", "b.txt"), mode="per_task"):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S", "locality": "any"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": list(allowed),
                    "depends_on": [], "worktree": {"mode": mode, "branch": None, "base": None,
                                                      "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                       {"id": "A2", "cmd": "test -d .", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
        "provenance": {"lane_reason": "bounded task"},
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    roster.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    return run, str(roster)


def _spawned(run):
    return [event for event in E.read_events(str(run / "events.jsonl")) if event["type"] == "spawned"]


# Fix 2: the run dir, and so every recorded worktree path, is absolute.

def test_relative_run_dir_records_an_absolute_spawned_worktree(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    run, roster = _run(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALE_SPAWN_BIN", "/usr/bin/true")
    assert main(["init-run", "--run-dir", "run", "--roster", roster]) == 0
    assert main(["dispatch", "--spawn", "--run-dir", "run", "--roster", roster, "--cwd", str(repo)]) == 0

    worktree = _spawned(run)[-1]["worktree"]
    assert os.path.isabs(worktree)
    assert worktree == str(run / "wt" / "T1")


def test_resolve_run_dir_is_absolute_on_every_branch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._resolve_run_dir(argparse.Namespace(run_dir="run")) == str(tmp_path / "run")
    monkeypatch.setenv("ALE_RUN_DIR", "env-run")
    assert cli._resolve_run_dir(argparse.Namespace(run_dir=None, run_id=None)) == str(tmp_path / "env-run")


# Fix 3: every spawn records diff_base and verify diffs the task worktree from it.

def _dispatched(tmp_path):
    repo = _repo(tmp_path / "repo")
    run, roster = _run(tmp_path)
    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("claimed", "run-1", 2, "T1", "worker", 1))
    return repo, run, roster, run / "wt" / "T1", events


def _submit_and_verify(run, roster, worktree, events):
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1, summary="done"))
    return main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(worktree)])


def _commit(worktree, name, text):
    (worktree / name).write_text(text)
    subprocess.run(["git", "add", name], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-qm", "add " + name], cwd=str(worktree), check=True)


def _of_type(events, kind):
    return [event for event in E.read_events(str(events)) if event["type"] == kind]


def test_every_spawn_records_its_diff_base(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)

    assert _spawned(run)[-1]["diff_base"] == _git(repo, "rev-parse", "HEAD")
    assert "base_commit" not in _spawned(run)[-1]   # base_commit is the stack base only


def test_verify_lists_committed_and_uncommitted_changes_against_the_base(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "a.txt", "committed\n")
    (worktree / "b.txt").write_text("uncommitted\n")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    evidence = _of_type(events, "accepted")[-1]["evidence"]
    assert evidence["files"] == ["a.txt", "b.txt"]
    assert evidence["commit"] == _git(worktree, "rev-parse", "HEAD")
    assert evidence["tree"] == _git(worktree, "rev-parse", "HEAD^{tree}")
    assert _git(worktree, "status", "--porcelain", "--untracked-files=no") == ""


def test_verify_rejects_a_committed_change_outside_allowed_paths(tmp_path, capsys):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "secret.txt", "out of scope\n")

    assert _submit_and_verify(run, roster, worktree, events) == 1
    assert "path_violation: secret.txt" in capsys.readouterr().err


def test_verify_with_all_changes_committed_accepts_without_a_new_commit(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "a.txt", "committed\n")
    head = _git(worktree, "rev-parse", "HEAD")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    evidence = _of_type(events, "accepted")[-1]["evidence"]
    assert evidence["files"] == ["a.txt"]
    assert evidence["commit"] == head


def test_verify_with_nothing_changed_notes_the_base(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    base = _spawned(run)[-1]["diff_base"]

    assert _submit_and_verify(run, roster, worktree, events) == 0
    assert _of_type(events, "accepted")[-1]["evidence"]["files"] == []
    notes = [event["text"] for event in _of_type(events, "note")]
    assert notes == ["verify: no changed files found against base %s" % base[:12]]


def test_legacy_spawn_without_a_recorded_base_diffs_from_head(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_record_diff_base", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_checkout_branch", lambda *args, **kwargs: None)
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    assert not {"base_commit", "diff_base", "base_ref"} & set(_spawned(run)[-1])
    _commit(worktree, "a.txt", "committed\n")
    (worktree / "b.txt").write_text("uncommitted\n")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    assert _of_type(events, "accepted")[-1]["evidence"]["files"] == ["b.txt"]


# Fix 4: untracked files in the checkout do not block integrate.

def _accepted_task(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    (worktree / "a.txt").write_text("done\n")
    assert _submit_and_verify(run, roster, worktree, events) == 0
    return repo, run, roster


def _integrate(repo, run, roster):
    return main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)])


def test_integrate_ignores_untracked_files_in_the_checkout(tmp_path):
    repo, run, roster = _accepted_task(tmp_path)
    (repo / ".ale-provenance.json").write_text("{}\n")
    (repo / ".DS_Store").write_text("finder\n")

    assert _integrate(repo, run, roster) == 0
    assert (repo / "a.txt").read_text() == "done\n"


def test_integrate_still_refuses_a_modified_tracked_file(tmp_path, capsys):
    repo, run, roster = _accepted_task(tmp_path)
    (repo / "base.txt").write_text("edited\n")

    assert _integrate(repo, run, roster) == 1
    assert "uncommitted changes: base.txt" in capsys.readouterr().err


# Fix 5: the stop hook emits input_required at most once per attempt.

def _bound_failing_task(tmp_path, monkeypatch):
    import io
    import shutil

    from ale.binding import binding_path
    from ale.handoff import write_atomic

    run = tmp_path / "hook-run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = tmp_path / "hook-roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    home = tmp_path / "home"
    monkeypatch.setenv("ALE_HOME", str(home))
    label_path = run / "labels" / "T01.json"
    label = json.loads(label_path.read_text())
    label["acceptance"][0]["cmd"] = "false"
    label_path.write_text(json.dumps(label))
    assert main(["init-run", "--run-dir", str(run), "--roster", str(roster), "--now", "0"]) == 0
    write_atomic(binding_path(str(home), "s1", None),
                 json.dumps({"run_dir": str(run), "roster": str(roster), "task_id": "T01",
                             "agent_id": "a1", "source": "file"}))

    def stop():
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"session_id": "s1", "hook_event_name": "stop", "cwd": ".",
             "transcript_path": "missing.json"})))
        return main(["hook", "stop"])

    def claim(now):
        return main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run),
                     "--roster", str(roster), "--now", str(now)])

    return run, stop, claim


def _input_required(run):
    return [event for event in E.read_events(str(run / "events.jsonl"))
            if event["type"] == "input_required"]


def test_blocked_stops_in_one_attempt_emit_one_input_required(tmp_path, monkeypatch):
    run, stop, claim = _bound_failing_task(tmp_path, monkeypatch)
    assert claim(1) == 0
    for _ in range(6):
        assert stop() == 0

    assert [event["attempt"] for event in _input_required(run)] == [1]


def test_a_new_attempt_may_ask_again(tmp_path, monkeypatch):
    run, stop, claim = _bound_failing_task(tmp_path, monkeypatch)
    assert claim(1) == 0
    for _ in range(4):
        assert stop() == 0
    events = run / "events.jsonl"
    E.append_event(str(events), E.make_event("lease_expired", "example-run", 50, "T01", None, 1))
    E.append_event(str(events), E.make_event("released", "example-run", 51, "T01", None, 1,
                                             reason="stale"))
    assert claim(60) == 0
    for _ in range(4):
        assert stop() == 0

    assert [event["attempt"] for event in _input_required(run)] == [1, 2]


def test_an_answered_question_may_be_asked_again_in_the_same_attempt(tmp_path, monkeypatch):
    run, stop, claim = _bound_failing_task(tmp_path, monkeypatch)
    assert claim(1) == 0
    for _ in range(3):
        assert stop() == 0
    E.append_event(str(run / "events.jsonl"),
                   E.make_event("input_answered", "example-run", 50, "T01", None, 1, text="go on"))
    assert claim(60) == 0
    for _ in range(4):
        assert stop() == 0

    assert [event["attempt"] for event in _input_required(run)] == [1, 1]


# Fix 6: refs prefetch accepts the renamed `trove` CLI as well as `ck`.

def _fake_cli(tmp_path, name):
    import stat

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\necho '%s page' \"$@\"\n" % name)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir)


def test_refs_prefetch_runs_trove_and_ck_item_gets(tmp_path, monkeypatch):
    from ale import refs as R

    monkeypatch.setenv("PATH", _fake_cli(tmp_path, "trove") + os.pathsep + _fake_cli(tmp_path, "ck")
                       + os.pathsep + os.environ["PATH"])
    entry = {"title": "Book", "read_first": "trove items get BOOK:1", "how_to_read": "ck items get BOOK:2"}
    out = tmp_path / "refs.md"
    result = R.prefetch(entry, str(out))

    assert result["ok"], result
    text = out.read_text()
    assert "trove page items get BOOK:1" in text and "ck page items get BOOK:2" in text


def test_refs_prefetch_still_refuses_other_commands(tmp_path):
    from ale import refs as R

    result = R.prefetch({"title": "Book", "read_first": "rm -rf BOOK"}, str(tmp_path / "o.md"))

    assert not result["ok"]
    assert "'ck items get'" in result["error"] and "'trove items get'" in result["error"]
    assert not (tmp_path / "o.md").exists()


# Fix 7: dispatch warns, once, when the checkout is behind its upstream.

def _behind_checkout(tmp_path, ahead):
    upstream = tmp_path / "up.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    seed = _repo(tmp_path / "seed")
    subprocess.run(["git", "push", "-q", str(upstream), "HEAD:refs/heads/main"], cwd=str(seed), check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(upstream), str(repo)], check=True)
    for index in range(ahead):
        _commit(seed, "later-%d.txt" % index, "later\n")
    subprocess.run(["git", "push", "-q", str(upstream), "HEAD:refs/heads/main"], cwd=str(seed), check=True)
    subprocess.run(["git", "fetch", "-q"], cwd=str(repo), check=True)
    return repo


def _notes(run):
    return [event["text"] for event in E.read_events(str(run / "events.jsonl")) if event["type"] == "note"]


def test_dispatch_warns_once_when_base_is_behind_upstream(tmp_path, capsys):
    repo = _behind_checkout(tmp_path, ahead=1)
    run, roster = _run(tmp_path)

    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    message = "ale dispatch: base HEAD is 1 commit(s) behind its upstream; fetch and fast-forward first"
    assert message in capsys.readouterr().err
    assert _notes(run) == [message]
    assert _spawned(run)[-1]["diff_base"] == _git(repo, "rev-parse", "HEAD")


def test_dispatch_is_silent_when_base_is_current(tmp_path, capsys):
    repo = _behind_checkout(tmp_path, ahead=0)
    run, roster = _run(tmp_path)

    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert "behind its upstream" not in capsys.readouterr().err
    assert _notes(run) == []


# Review fix: a fix task reuses its parent's worktree, so it diffs from the parent's base.

def test_fix_task_of_a_stacked_child_verifies_against_the_parent_base(tmp_path, capsys):
    from stack_fixtures import ale, dispatch, label, make_run, spawned, work_and_accept

    t2 = label("T2", ["T1"])
    t2["acceptance"] = [{"id": "A1", "cmd": "grep -q ok t2.txt", "expect": "exit0"},
                        {"id": "A2", "cmd": "true", "expect": "exit0"}]
    repo, run, roster = make_run(tmp_path, {"T1": label("T1"), "T2": t2})
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T1")
    assert dispatch(run, roster, repo) == 0
    worktree = run / "wt" / "T2"
    (worktree / "t2.txt").write_text("bad\n")
    assert ale(run, roster, "claim", "--task", "T2", "--agent", "a") == 0
    assert ale(run, roster, "submit", "--task", "T2", "--agent", "a", "--summary", "x") == 0
    assert ale(run, roster, "verify", "--task", "T2", "--cwd", str(worktree)) == 1
    assert ale(run, roster, "fix", "--task", "T2") == 0
    assert dispatch(run, roster, repo) == 0
    assert spawned(run, "T2.fix1")[-1]["diff_base"] == spawned(run, "T2")[-1]["diff_base"]
    (worktree / "t2.txt").write_text("ok\n")
    assert ale(run, roster, "claim", "--task", "T2.fix1", "--agent", "f") == 0
    assert ale(run, roster, "submit", "--task", "T2.fix1", "--agent", "f", "--summary", "x") == 0
    capsys.readouterr()

    assert ale(run, roster, "verify", "--task", "T2.fix1", "--cwd", str(worktree)) == 0, \
        capsys.readouterr().err


# Review fix: an uncommitted change that cancels a committed one is still checked and committed.

def test_a_committed_out_of_scope_file_removed_uncommitted_is_still_a_violation(tmp_path, capsys):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "secret.txt", "out of scope\n")
    (worktree / "secret.txt").unlink()
    (worktree / "a.txt").write_text("x\n")

    assert _submit_and_verify(run, roster, worktree, events) == 1
    assert "path_violation: secret.txt" in capsys.readouterr().err


def test_an_uncommitted_revert_of_a_committed_change_is_committed_at_accept(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    _commit(worktree, "a.txt", "committed\n")
    (worktree / "a.txt").unlink()
    (worktree / "b.txt").write_text("kept\n")

    assert _submit_and_verify(run, roster, worktree, events) == 0
    evidence = _of_type(events, "accepted")[-1]["evidence"]
    assert evidence["files"] == ["a.txt", "b.txt"]
    assert evidence["commit"] == _git(worktree, "rev-parse", "HEAD")
    assert evidence["tree"] == _git(worktree, "rev-parse", "HEAD^{tree}")
    assert _git(worktree, "ls-tree", "--name-only", "HEAD").splitlines() == ["b.txt", "base.txt"]
    assert _git(worktree, "status", "--porcelain", "--untracked-files=no") == ""


# Review nit: the empty-file note names what was actually compared.

def test_empty_file_note_names_an_explicit_base_override(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1, summary="done"))
    assert main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(worktree), "--base", "HEAD"]) == 0

    assert [event["text"] for event in _of_type(events, "note")] == [
        "verify: no changed files found against --base HEAD"]


def test_empty_file_note_says_no_diff_ran_outside_the_task_worktree(tmp_path):
    repo, run, roster, worktree, events = _dispatched(tmp_path)
    E.append_event(str(events), E.make_event("submitted", "run-1", 3, "T1", "worker", 1, summary="done"))
    assert main(["verify", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0

    assert [event["text"] for event in _of_type(events, "note")] == [
        "verify: no changed files recorded; verified outside the task worktree, so no diff ran"]


# Review fix: a `trove items get` shell read earns refs_read like `ck items get`.

def test_refs_read_signal_counts_trove_and_ck_item_gets():
    from ale import hooks as HK

    for command in ("trove items get BOOK:1", "cd x && trove items get BOOK:1", "ck items get BOOK:1"):
        assert HK.refs_read_signal("Bash", {"command": command}, None), command
    for command in ("trove items list", "echo 'trove items get BOOK:1'", "mytrove items get BOOK:1"):
        assert HK.refs_read_signal("Bash", {"command": command}, None) is None, command
