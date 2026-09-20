import json
import os
import re
import subprocess

from ale.cli import main
from ale.evalharness import corpus


def test_ledger_deny_matches_cwd_only():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "cwd": "/tmp/private-area", "task": "T1"},
        {"event": "start", "desc": "Refresh the chart legend labels", "cwd": "/tmp/open-area", "task": "T2"},
    ], re.compile("private-area"))

    assert [r["text"] for r in rows] == ["Refresh the chart legend labels"]
    assert stats["dropped_deny"] == 1


def test_ledger_deny_uses_caller_regex_flags():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "cwd": "/tmp/work", "task": "BLUE"},
        {"event": "start", "desc": "Refresh the chart legend labels", "cwd": "/tmp/work", "task": "green"},
    ], re.compile("blue", re.I))

    assert [r["text"] for r in rows] == ["Refresh the chart legend labels"]
    assert stats["dropped_deny"] == 1


def test_ledger_profile_work_dropped_without_regex():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "profile": "work"},
        {"event": "start", "desc": "Refresh the chart legend labels", "profile": "lab"},
    ], None)

    assert [r["text"] for r in rows] == ["Refresh the chart legend labels"]
    assert stats["dropped_work"] == 1


def test_ledger_duplicates_short_rows_and_stable_ids():
    source = [
        {"event": "start", "desc": "Add pagination to the widget list"},
        {"event": "start", "desc": "Add pagination to the widget list"},
        {"event": "start", "desc": "Tiny"},
        {"event": "finish", "desc": "Refresh the chart legend labels"},
    ]

    rows1, stats1 = corpus.from_task_ledger(source, None)
    rows2, stats2 = corpus.from_task_ledger(source, None)

    assert rows1 == rows2
    assert stats1 == stats2
    assert len(rows1) == 1
    assert rows1[0]["id"].startswith("L")
    assert rows1[0]["kind"] == "short"
    assert rows1[0]["source"] == "ledger"
    assert stats1["duplicates"] == 1
    assert stats1["dropped_short"] == 1
    assert stats1["seen"] == 3


def test_plan_splitting_from_basename_text_pairs(tmp_path):
    text = """# Release Plan

### Task 1: Widget page
Add pagination to the widget list.

### Task 2: Chart polish
Refresh the chart legend labels.

### Task 3: Form states
Improve empty states for the settings form.
"""
    path = tmp_path / "plan.md"
    path.write_text(text)

    rows, stats = corpus.from_plan_files([(path.name, path.read_text())], None)

    assert len(rows) == 3
    assert stats == {"seen": 3, "kept": 3, "dropped_deny": 0}
    assert rows[0]["source"] == "plan.md#Task 1: Widget page"
    assert rows[0]["kind"] == "full"
    assert rows[0]["text"].startswith("### Task 1: Widget page")


def test_plan_deny_and_cap():
    long_body = "A" * 5000
    rows, stats = corpus.from_plan_files([
        ("plan.md", "### Task 1: Widget page\nAdd pagination to the widget list.\n"
                    "### Task 2: Chart polish\n%s\n" % long_body),
    ], re.compile("pagination"))

    assert len(rows) == 1
    assert len(rows[0]["text"]) == 4000
    assert stats == {"seen": 2, "kept": 1, "dropped_deny": 1}


def test_sampling_is_deterministic_and_respects_n():
    rows = (
        [{"id": "F%d" % i, "text": "full %d" % i, "source": "plans", "kind": "full"} for i in range(5)] +
        [{"id": "S%d" % i, "text": "short %d" % i, "source": "ledger", "kind": "short"} for i in range(20)]
    )

    sample1 = corpus.stratified_sample(rows, n=9, seed=123)
    sample2 = corpus.stratified_sample(rows, n=9, seed=123)

    assert sample1 == sample2
    assert len(sample1) == 9
    assert sum(1 for r in sample1 if r["kind"] == "full") == 3


def test_cli_refuses_tracked_output_directory_and_accepts_ignored_one(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True, text=True)
    ledger = repo / "events.jsonl"
    ledger.write_text(json.dumps({"event": "start", "desc": "Add pagination to the widget list"}) + "\n")

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "eval-out")]) == 1
    assert not (repo / "eval-out" / "corpus.jsonl").exists()

    (repo / ".gitignore").write_text("ignored-eval/\n")
    ignored = repo / "ignored-eval"
    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(ignored), "--n", "5"]) == 0
    assert (ignored / "corpus.jsonl").exists()
    assert (ignored / "corpus-stats.json").exists()
