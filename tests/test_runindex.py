import json
import os
import shutil

from ale import __version__
from ale import runindex as RI
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _example_run(tmp_path, name):
    run_dir = tmp_path / name
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    if not roster.exists():
        shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    return str(run_dir), str(roster)


def _write_run(base, run_id, events):
    run_dir = os.path.join(base, ".ale", "runs", run_id)
    os.makedirs(os.path.join(run_dir, "labels"))
    with open(os.path.join(run_dir, "events.jsonl"), "w") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    return run_dir


def test_ale_home_is_isolated_by_conftest(ale_home):
    assert os.environ["ALE_HOME"] == ale_home
    assert RI.ale_home() == ale_home
    assert RI.index_path() == os.path.join(ale_home, ".ale", "index", "runs.jsonl")
    assert RI.ale_home({}) == os.path.expanduser("~")


def test_init_run_indexes_once_and_a_second_run_adds_an_entry(tmp_path, ale_home):
    first, roster = _example_run(tmp_path, "run-a")
    assert main(["init-run", "--run-dir", first, "--roster", roster, "--now", "1"]) == 0
    entries = RI.read_index()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["run_dir"] == os.path.realpath(first) and os.path.isabs(entry["run_dir"])
    assert entry["ale_version"] == __version__
    assert entry["roster_hash"] and entry["run_id"]
    assert entry["repo_root"] and os.path.isabs(entry["repo_root"])
    assert set(entry) >= {"ts", "run_id", "run_dir", "repo_root", "ale_version", "roster_hash"}

    second, _ = _example_run(tmp_path, "run-b")
    assert main(["init-run", "--run-dir", second, "--roster", roster, "--now", "2"]) == 0
    assert sorted(e["run_dir"] for e in RI.read_index()) == sorted(
        [os.path.realpath(first), os.path.realpath(second)])


def test_append_run_refuses_a_duplicate_and_read_index_keeps_the_latest(tmp_path):
    run_dir = str(tmp_path / "r")
    os.makedirs(run_dir)
    assert RI.append_run(run_dir, str(tmp_path), "r", "0.1", "abc") is True
    assert RI.append_run(run_dir, str(tmp_path), "r", "0.2", "abc") is False
    with open(RI.index_path(), "a") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"ts": 9e9, "run_id": "r2", "run_dir": os.path.realpath(run_dir),
                                 "repo_root": str(tmp_path), "ale_version": "0.3",
                                 "roster_hash": None}) + "\n")
    entries = RI.read_index()
    assert len(entries) == 1 and entries[0]["run_id"] == "r2"


def test_backfill_finds_a_nested_run_skips_worktrees_and_is_idempotent(tmp_path):
    root = tmp_path / "dev"
    labeled = {"type": "labeled", "run_id": "nested-run", "task_id": "T1", "ts": 2.0,
               "roster_hash": "deadbeef", "labels": {}}
    nested = _write_run(str(root / "proj" / "sub"), "nested-dir",
                        [{"type": "run_started", "run_id": "nested-run", "task_id": None, "ts": 1.0}, labeled])
    plain = _write_run(str(root / "other"), "no-id", [{"type": "note", "ts": 1.0, "text": "x"}])
    # A run under a run dir's worktree is a checkout copy, never indexed.
    _write_run(os.path.join(nested, "wt", "T1"), "copy", [{"type": "run_started", "run_id": "copy", "ts": 1.0}])
    # Directories without labels/ are not runs; node_modules is not walked.
    os.makedirs(str(root / "proj" / ".ale" / "runs" / "junk"))
    _write_run(str(root / "node_modules" / "pkg"), "vendored", [{"type": "run_started", "run_id": "v", "ts": 1}])

    assert RI.backfill([str(root)]) == 2
    entries = {e["run_dir"]: e for e in RI.read_index()}
    assert set(entries) == {os.path.realpath(nested), os.path.realpath(plain)}
    assert entries[os.path.realpath(nested)]["run_id"] == "nested-run"
    assert entries[os.path.realpath(nested)]["roster_hash"] == "deadbeef"
    assert entries[os.path.realpath(nested)]["ale_version"] is None
    assert entries[os.path.realpath(plain)]["run_id"] == "no-id"
    assert entries[os.path.realpath(plain)]["roster_hash"] is None
    assert RI.backfill([str(root)]) == 0
    assert len(RI.read_index()) == 2


def test_backfill_respects_max_depth(tmp_path):
    _write_run(str(tmp_path / "a" / "b" / "c" / "d"), "deep", [{"type": "run_started", "run_id": "d", "ts": 1}])
    assert RI.backfill([str(tmp_path)], max_depth=3) == 0
    assert RI.backfill([str(tmp_path)]) == 1


def test_an_explicit_index_path_overrides_the_home(tmp_path):
    index = str(tmp_path / "elsewhere" / "runs.jsonl")
    run_dir = str(tmp_path / "r")
    os.makedirs(run_dir)
    assert RI.append_run(run_dir, str(tmp_path), "r", None, None, index=index) is True
    assert RI.read_index(index=index)[0]["run_id"] == "r"
    assert RI.read_index() == []
