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


def test_ledger_profile_work_filter_is_case_and_space_tolerant():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "profile": "Work"},
        {"event": "start", "desc": "Refresh the chart legend labels", "profile": "WORK"},
        {"event": "start", "desc": "Improve empty states for settings", "profile": " work "},
        {"event": "start", "desc": "Tune the local example fixtures", "profile": "lab"},
    ], None)

    assert [r["text"] for r in rows] == ["Tune the local example fixtures"]
    assert stats["dropped_work"] == 3


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


def test_ledger_deny_matches_task_id_as_well_as_task():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "task_id": "private-1"},
        {"event": "start", "desc": "Refresh the chart legend labels", "task": "private-2"},
        {"event": "start", "desc": "Improve empty states for settings", "task_id": "open-1"},
    ], re.compile("private"))

    assert [r["text"] for r in rows] == ["Improve empty states for settings"]
    assert stats["dropped_deny"] == 2


def test_ledger_exclude_task_id_regex_matches_task_id_and_task():
    rows, stats = corpus.from_task_ledger([
        {"event": "start", "desc": "Add pagination to the widget list", "task_id": "drop-1"},
        {"event": "start", "desc": "Refresh the chart legend labels", "task": "drop-2"},
        {"event": "start", "desc": "Improve empty states for settings", "task_id": "keep-1"},
    ], None, exclude_task_id=re.compile("drop"))

    assert [r["text"] for r in rows] == ["Improve empty states for settings"]
    assert stats["dropped_task_id"] == 2


def test_cli_corpus_exclude_task_id_regex_counts_dropped_task_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("out/\n")
    ledger = repo / "events.jsonl"
    ledger.write_text(
        json.dumps({"event": "start", "desc": "Add pagination to the widget list", "task_id": "sample-drop"}) + "\n" +
        json.dumps({"event": "start", "desc": "Refresh the chart legend labels", "task_id": "sample-keep"}) + "\n"
    )
    out = repo / "out"

    assert main(["eval", "corpus", "--ledger", str(ledger), "--exclude-task-id-regex", "drop",
                 "--out", str(out), "--n", "5"]) == 0

    stats = json.loads((out / "corpus-stats.json").read_text())
    rows = [json.loads(line) for line in (out / "corpus.jsonl").read_text().splitlines()]
    assert stats["ledger"]["dropped_task_id"] == 1
    assert [r["text"] for r in rows] == ["Refresh the chart legend labels"]


def test_cli_corpus_merges_repeatable_plan_globs_and_dedupes_real_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("out/\n")
    ledger = repo / "events.jsonl"
    ledger.write_text("")
    plan_a = repo / "plan-a.md"
    plan_b = repo / "plan-b.md"
    plan_a.write_text("### Task 1: Widget page\nAdd pagination to the widget list.\n")
    plan_b.write_text("### Task 1: Chart polish\nRefresh the chart legend labels.\n")
    out = repo / "out"

    assert main(["eval", "corpus", "--ledger", str(ledger), "--plans", str(repo / "plan-*.md"),
                 "--plans", str(plan_a), "--out", str(out), "--n", "10", "--full-share", "1"]) == 0

    rows = [json.loads(line) for line in (out / "corpus.jsonl").read_text().splitlines()]
    assert sorted(r["source"].split("#", 1)[0] for r in rows) == ["plan-a.md", "plan-b.md"]


def test_stratified_sample_full_share_one_takes_full_rows_first_and_default_unchanged():
    rows = (
        [{"id": "F%d" % i, "text": "full %d" % i, "source": "plans", "kind": "full"} for i in range(5)] +
        [{"id": "S%d" % i, "text": "short %d" % i, "source": "ledger", "kind": "short"} for i in range(20)]
    )

    default_sample = corpus.stratified_sample(rows, n=9, seed=123)
    full_first = corpus.stratified_sample(rows, n=4, seed=123, full_share=1)

    assert sum(1 for r in default_sample if r["kind"] == "full") == 3
    assert len(full_first) == 4
    assert all(r["kind"] == "full" for r in full_first)


def test_redact_builtins_extra_patterns_order_counts_and_input_unchanged():
    original = [{
        "id": "C1",
        "text": ("Path /Users/sam/project and /home/sam/log contact sam@example.org ssh git@example.org "
                 "host 192.0.2.10 api_key=abcdefgh token: abcdefghi label alpha beta"),
        "source": "/home/sam/source password=secretvalue alpha",
        "kind": "short",
    }]
    extra = [(re.compile("alpha"), "beta"), (re.compile("beta"), "gamma")]

    redacted, counts = corpus.redact(original, extra)

    assert original[0]["text"].startswith("Path /Users/sam")
    assert redacted is not original
    assert redacted[0] is not original[0]
    assert "/Users/sam" not in redacted[0]["text"]
    assert "/home/sam" not in redacted[0]["source"]
    assert "sam@example.org" not in redacted[0]["text"]
    assert "git@example.org" not in redacted[0]["text"]
    assert "192.0.2.10" not in redacted[0]["text"]
    assert "api_key=<redacted>" in redacted[0]["text"]
    assert "token: <redacted>" in redacted[0]["text"]
    assert "password=<redacted>" in redacted[0]["source"]
    assert "gamma gamma" in redacted[0]["text"]
    assert counts["home_dir"] == 3
    assert counts["addr"] == 2
    assert counts["ip"] == 1
    assert counts["secret"] == 3
    assert counts["extra_1"] == 2
    assert counts["extra_2"] == 3


def test_cli_corpus_redact_stats_and_english_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("out/\n")
    ledger = repo / "events.jsonl"
    ledger.write_text(
        json.dumps({"event": "start", "desc": "Email sam@example.org from /home/sam with token=abcdefgh marker"}) + "\n" +
        json.dumps({"event": "start", "desc": "שלום שלום שלום שלום local words"}) + "\n"
    )
    out = repo / "out"

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(out), "--n", "5",
                 "--redact", "--redact-regex", "marker=>tag", "--english-only"]) == 0

    stats = json.loads((out / "corpus-stats.json").read_text())
    rows = [json.loads(line) for line in (out / "corpus.jsonl").read_text().splitlines()]
    assert stats["redactions"]["home_dir"] == 1
    assert stats["redactions"]["addr"] == 1
    assert stats["redactions"]["secret"] == 1
    assert stats["redactions"]["extra_1"] == 1
    assert stats["dropped_non_english"] == 1
    assert len(rows) == 1
    assert "sam@example.org" not in rows[0]["text"]
    assert "tag" in rows[0]["text"]
