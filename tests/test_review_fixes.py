"""Regression tests for the PR #1 review fixes (one section per review item)."""

from __future__ import annotations

import os

from ale import analyze as A
from ale.planparse import parse_plan

from analyze_fixtures import ev, entry, evidence, good_task, label, run_dir_for, run_header, write_run, HOUR


# Item 1: a fenced file line is not a task file.
FENCED_FILE_PLAN = """# Plan

## Task 1: Template

Write the template.

## Task 2: Document the plan format

- Modify: `docs/plans.md`

Example of a task block:

```markdown
- Create: `src/example.py`
```
"""


def test_item1_fenced_file_line_is_not_a_task_file():
    tasks = parse_plan(FENCED_FILE_PLAN)

    assert tasks[1]["task_id"] == "T2"
    assert tasks[1]["files"] == ["docs/plans.md"]


# Item 2: dependency ids accept the heading grammar (letter prefix, [a-z] suffix).
def test_item2_depends_on_accepts_a_letter_suffix():
    text = ("## Task P1a: First\nbody\n"
            "## Task P2: Second\nDepends on Task P1a.\n"
            "## Task 3: Third\nConsumes: output of Task P1a\n")
    tasks = parse_plan(text)

    assert tasks[1]["depends_on"] == ["P1a"]
    assert tasks[2]["depends_on"] == ["P1a"]


# --- analyze ---------------------------------------------------------------------------------

T0 = 1_700_000_000.0
DONE_NOW = T0 + 24 * HOUR


def _report(runs, now=DONE_NOW, since_s=None):
    return A.evaluate(runs, A.load_thresholds(), now, since_s)


def _judged_run(base, dir_name, t0, run_id="plan"):
    """A finished one-task run with a role shadow vote and its adjudication on T1."""
    run_dir = run_dir_for(base, dir_name)
    events = run_header(run_id, t0, ["T1"]) + good_task(run_id, "T1", t0 + 100, run_dir)
    events += [
        ev("shadow_vote", run_id, t0 + 1, "T1", decision="role", options=["backend", "frontend"],
           choice="backend", confidence=0.9, model="jev", latency_ms=30, uncertain=False,
           source="bake"),
        ev("adjudicated", run_id, t0 + 60, "T1", field="role", decision="role", value="backend",
           choice="backend", by="lead", authority="lead", additive=True),
    ]
    path = write_run(base, run_id, [label("T1", run_id)], events, dir_name=dir_name)
    return A.load_run(entry(path, run_id))


# Item 3: promotion cases are keyed by run key, not the raw run_id.
def test_item3_two_runs_with_the_same_run_id_stay_two_promotion_cases(tmp_path):
    first = _judged_run(tmp_path, "2026-10-01-a", T0)
    second = _judged_run(tmp_path, "2026-10-02-b", T0 + 1000)
    assert first["run_id"] == second["run_id"] == "plan"

    report = _report([first, second])

    assert report["promotions"]["role"]["cases"] == 2


# --- spawn bases (items 4+6, 7) --------------------------------------------------------------

import subprocess  # noqa: E402

import stack_fixtures as SF  # noqa: E402


def _on_branch(repo, name="main"):
    subprocess.run(["git", "checkout", "-q", "-B", name], cwd=str(repo), check=True)


# Items 4+6: every spawn records diff_base and base_ref; base_commit keeps its stack meaning.
def test_item4_6_unstacked_spawn_records_diff_base_and_base_ref_but_no_base_commit(tmp_path):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1")})
    _on_branch(repo)

    assert SF.dispatch(run, roster, repo) == 0

    event = SF.spawned(run, "T1")[-1]
    assert event["diff_base"] == SF.git(repo, "rev-parse", "HEAD")
    assert event["base_ref"] == "main"
    assert "base_commit" not in event


def test_item4_6_stacked_spawn_records_all_three(tmp_path):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1"), "T2": SF.label("T2", ["T1"])})
    _on_branch(repo)
    assert SF.dispatch(run, roster, repo) == 0
    commit = SF.work_and_accept(run, roster, "T1")

    assert SF.dispatch(run, roster, repo) == 0

    event = SF.spawned(run, "T2")[-1]
    assert event["stack_parent"] == "T1" and event["base_commit"] == commit
    assert event["diff_base"] == commit and event["base_ref"] == "main"


def test_item4_6_detached_checkout_records_no_base_ref(tmp_path):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1")})
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=str(repo), check=True)

    assert SF.dispatch(run, roster, repo) == 0

    event = SF.spawned(run, "T1")[-1]
    assert event["diff_base"] == SF.git(repo, "rev-parse", "HEAD")
    assert "base_ref" not in event


def test_item4_6_fix_task_takes_the_branch_diff_base_and_no_base_commit(tmp_path):
    t2 = SF.label("T2", ["T1"])
    t2["acceptance"] = [{"id": "A1", "cmd": "grep -q ok t2.txt", "expect": "exit0"},
                        {"id": "A2", "cmd": "true", "expect": "exit0"}]
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1"), "T2": t2})
    _on_branch(repo)
    assert SF.dispatch(run, roster, repo) == 0
    commit = SF.work_and_accept(run, roster, "T1")
    assert SF.dispatch(run, roster, repo) == 0
    worktree = run / "wt" / "T2"
    (worktree / "t2.txt").write_text("bad\n")
    assert SF.ale(run, roster, "claim", "--task", "T2", "--agent", "a") == 0
    assert SF.ale(run, roster, "submit", "--task", "T2", "--agent", "a", "--summary", "x") == 0
    assert SF.ale(run, roster, "verify", "--task", "T2", "--cwd", str(worktree)) == 1
    assert SF.ale(run, roster, "fix", "--task", "T2") == 0

    assert SF.dispatch(run, roster, repo) == 0

    fix = SF.spawned(run, "T2.fix1")[-1]
    assert fix["diff_base"] == commit and fix["base_ref"] == "main"
    assert "base_commit" not in fix and "stack_parent" not in fix


def test_item4_6_register_worktree_records_diff_base_and_base_ref(tmp_path):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1")})
    _on_branch(repo)
    path = run / "wt" / "T1"
    subprocess.run(["git", "worktree", "add", "-q", str(path), "-b", "ale/run-1/T1", "HEAD"],
                   cwd=str(repo), check=True)
    base = SF.git(repo, "rev-parse", "HEAD")

    assert SF.ale(run, roster, "register-worktree", "--task", "T1", "--path", str(path),
                  "--branch", "ale/run-1/T1", "--base", base, "--cwd", str(repo)) == 0

    event = SF.spawned(run, "T1")[-1]
    assert event["diff_base"] == base and event["base_ref"] == "main"


# Item 7: verify diffs against merge-base HEAD <base_ref>, so merged upstream work is not blamed.
def test_item7_task_branch_that_merges_upstream_verifies_with_only_its_own_files(tmp_path, capsys):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1")})
    _on_branch(repo)
    assert SF.dispatch(run, roster, repo) == 0
    worktree = run / "wt" / "T1"
    (repo / "upstream.txt").write_text("someone else\n")
    subprocess.run(["git", "add", "upstream.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "upstream"], cwd=str(repo), check=True)
    (worktree / "t1.txt").write_text("mine\n")
    subprocess.run(["git", "add", "t1.txt"], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=str(worktree), check=True)
    subprocess.run(["git", "merge", "-q", "--no-edit", "main"], cwd=str(worktree), check=True)
    (worktree / "t1.txt").write_text("mine, edited after the merge\n")
    assert SF.ale(run, roster, "claim", "--task", "T1", "--agent", "a") == 0
    assert SF.ale(run, roster, "submit", "--task", "T1", "--agent", "a", "--summary", "x") == 0
    capsys.readouterr()

    assert SF.ale(run, roster, "verify", "--task", "T1", "--cwd", str(worktree)) == 0, capsys.readouterr().err

    accepted = [e for e in SF.E.read_events(str(run / "events.jsonl")) if e["type"] == "accepted"][-1]
    assert accepted["evidence"]["files"] == ["t1.txt"]


def test_item7_without_base_ref_verify_falls_back_to_diff_base(tmp_path, capsys):
    repo, run, roster = SF.make_run(tmp_path, {"T1": SF.label("T1")})
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=str(repo), check=True)
    assert SF.dispatch(run, roster, repo) == 0
    worktree = run / "wt" / "T1"
    (worktree / "t1.txt").write_text("mine\n")
    subprocess.run(["git", "add", "t1.txt"], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=str(worktree), check=True)
    (worktree / "stray.txt").write_text("untracked and out of scope\n")
    assert SF.ale(run, roster, "claim", "--task", "T1", "--agent", "a") == 0
    assert SF.ale(run, roster, "submit", "--task", "T1", "--agent", "a", "--summary", "x") == 0
    capsys.readouterr()

    assert SF.ale(run, roster, "verify", "--task", "T1", "--cwd", str(worktree)) == 1
    assert "path_violation: stray.txt" in capsys.readouterr().err
    verified = [e for e in SF.E.read_events(str(run / "events.jsonl")) if e["type"] == "verified"][-1]
    assert verified["evidence"]["files"] == ["stray.txt", "t1.txt"]


# Item 5: promotion progress is cumulative over every indexed run, not the window.
def test_item5_promotion_progress_counts_runs_outside_the_window(tmp_path):
    old = _judged_run(tmp_path, "2026-09-01-old", T0 - 30 * 24 * HOUR)
    recent = _judged_run(tmp_path, "2026-10-01-new", T0)

    report = _report([old, recent], since_s=7 * 24 * HOUR)

    assert report["runs"]["done"] == 1   # only the recent run is in the window
    assert report["promotions"]["role"]["cases"] == 2


def _single_task_run(base, run_id, task_events, task_label=None):
    run_dir = run_dir_for(base, run_id)
    events = run_header(run_id, T0, ["T1"]) + task_events
    path = write_run(base, run_id, [task_label or label("T1", run_id)], events)
    return A.load_run(entry(path, run_id))


def _cases(report, evaluator):
    return [row for row in report["case_results"] if row["evaluator"] == evaluator]


# Item 8: a think-only task (worktree.mode none) claimed in-session is n/a for dispatch_worktree.
def test_item8_think_only_task_claimed_in_session_is_na_for_dispatch_worktree(tmp_path):
    events = [ev("claimed", "r", T0 + 10, "T1", "lead-session"),
              ev("submitted", "r", T0 + 20, "T1", "lead-session", summary="notes"),
              ev("verified", "r", T0 + 30, "T1", evidence=evidence([])),
              ev("accepted", "r", T0 + 30, "T1", evidence=evidence([]))]
    run = _single_task_run(tmp_path, "r", events, label("T1", "r", worktree_mode="none"))

    assert _cases(_report([run]), "ale.task.dispatch_worktree") == []


# Item 9: evidence.files_count / files_truncated feed files_changed and path_scope_checked.
def test_item9_truncated_evidence_counts_its_files(tmp_path):
    truncated = dict(evidence([]), files_count=40, files_truncated=True)
    events = [ev("spawned", "r", T0 + 1, "T1", agent_id_minted="T1-x", assignment_kind="executor",
                 executor="codex-exec", worktree=os.path.join(run_dir_for(tmp_path, "r"), "wt", "T1"),
                 branch="ale/r/T1", pane="p"),
              ev("claimed", "r", T0 + 10, "T1", "T1-x"),
              ev("submitted", "r", T0 + 20, "T1", "T1-x", summary="big"),
              ev("verified", "r", T0 + 30, "T1", evidence=truncated),
              ev("accepted", "r", T0 + 30, "T1", evidence=truncated)]
    run = _single_task_run(tmp_path, "r", events)

    assert A.task_rows(run)[0]["files_changed"] == 40
    [scope] = _cases(_report([run]), "ale.task.path_scope_checked")
    assert scope["passed"] is True and scope["reason"] == "verified against 40 files"


# Item 10: only a lead-authored input_answered (agent_id None) answers an ask.
def test_item10_executor_authored_answer_does_not_reopen_the_ask():
    from ale.hooks import input_pending

    events = [ev("input_required", "r", 1, "T1", "T1-x", question="stuck"),
              ev("input_answered", "r", 2, "T1", "T1-x", text="self-answer")]
    assert input_pending(events, "T1", 1) is True

    events.append(ev("input_answered", "r", 3, "T1", None, text="lead answer"))
    assert input_pending(events, "T1", 1) is False


# Item 11: the index takes repo_root from the run dir's repository, not the process cwd.
def test_item11_init_run_in_another_repo_indexes_that_repo(tmp_path, monkeypatch, ale_home):
    import shutil

    from ale import runindex as RI
    from ale.cli import main

    here = tmp_path / "here"
    other = tmp_path / "other"
    for repo in (here, other):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    run_dir = other / ".ale" / "runs" / "r1"
    shutil.copytree(os.path.join(SF.ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(SF.ROOT, "examples", "roster.json"), str(roster))
    monkeypatch.chdir(here)

    assert main(["init-run", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0

    [row] = RI.read_index()
    assert row["repo_root"] == os.path.realpath(str(other))


# Item 12: analyze appends a ledger row only when (case_id, evaluator) is new or its score/passed changed.
def test_item12_analyze_twice_appends_ledger_rows_once(tmp_path, monkeypatch, ale_home):
    from ale import evalledger as EL
    from ale import runindex as RI
    from ale.cli import main
    from analyze_fixtures import good_run

    monkeypatch.setenv("ALE_NOW", str(DONE_NOW))
    run_dir = good_run(tmp_path, "r", T0)
    assert RI.append_run(run_dir, os.path.dirname(run_dir), "r", "0.4.0", "abcd1234")

    assert main(["analyze"]) == 0
    first = EL.read_rows()
    assert first
    assert main(["analyze"]) == 0

    assert len(EL.read_rows()) == len(first)


# Item 13: one tolerant JSONL reader, one ISO formatter, one ALE_HOME resolver, one flock append.
def test_item13_shared_record_helpers(tmp_path, monkeypatch):
    import argparse

    from ale import analyze, cli, evalledger, records, runindex
    from ale.events import locked_append

    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a": 1}\nnot json\n[1, 2]\n\xff\xfe\n{"b": 2}\n')
    assert records.read_jsonl(str(path)) == [{"a": 1}, {"b": 2}]
    assert records.read_jsonl(str(tmp_path / "missing.jsonl")) == []

    locked_append(str(path), lambda: b"")
    locked_append(str(path), b'{"c": 3}\n')
    assert records.read_jsonl(str(path))[-1] == {"c": 3}

    assert records.iso(0) == "1970-01-01T00:00:00Z"
    assert analyze.iso is records.iso and evalledger.iso is records.iso

    monkeypatch.setenv("ALE_HOME", str(tmp_path / "h"))
    assert runindex.ale_home() == cli._hook_home() == str(tmp_path / "h")
    assert cli._binding_home(argparse.Namespace(home=None)) == str(tmp_path / "h")
