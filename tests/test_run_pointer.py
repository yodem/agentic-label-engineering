import json

import pytest

from ale.cli import CliError, _resolve_roster, _resolve_run_dir, _write_current_run


def _args(**values):
    defaults = {"run_dir": None, "run_id": None, "roster": None}
    defaults.update(values)
    return type("Args", (), defaults)()


def test_current_pointer_writes_run_directory_name(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    run_dir = repo / ".ale" / "runs" / "directory-name"
    run_dir.mkdir(parents=True)
    monkeypatch.chdir(repo)

    _write_current_run(str(run_dir), "different-run-id")

    assert (repo / ".ale" / "runs" / "current").read_text() == "directory-name\n"


def test_old_run_id_pointer_resolves_to_matching_run_directory(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runs = repo / ".ale" / "runs"
    run_dir = runs / "directory-name"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(json.dumps({"type": "run_started", "run_id": "legacy-id"}) + "\n")
    (runs / "current").write_text("legacy-id\n")
    monkeypatch.chdir(repo)

    assert _resolve_run_dir(_args()) == str(run_dir)


def test_init_pointer_does_not_replace_another_existing_run(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runs = repo / ".ale" / "runs"
    target = runs / "new-run"
    target.mkdir(parents=True)
    (runs / "current-run").mkdir()
    (runs / "current").write_text("current-run\n")
    monkeypatch.chdir(repo)

    _write_current_run(str(target), "new-run")

    assert (runs / "current").read_text() == "current-run\n"


def test_init_pointer_replaces_stale_pointer_and_explicit_set_current(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runs = repo / ".ale" / "runs"
    target = runs / "new-run"
    other = runs / "other-run"
    target.mkdir(parents=True)
    other.mkdir()
    (runs / "current").write_text("deleted-run\n")
    monkeypatch.chdir(repo)
    _write_current_run(str(target), "new-run")
    assert (runs / "current").read_text() == "new-run\n"

    (runs / "current").write_text("other-run\n")
    _write_current_run(str(target), "new-run", set_current=True)
    assert (runs / "current").read_text() == "new-run\n"


def test_roster_resolution_uses_repository_owning_run_directory(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    worktree = repo / "task-worktree"
    run_dir = repo / ".ale" / "runs" / "run-a"
    (repo / ".git").mkdir(parents=True)
    worktree.mkdir()
    run_dir.mkdir(parents=True)
    (repo / ".ale").mkdir(exist_ok=True)
    monkeypatch.chdir(worktree)
    monkeypatch.delenv("ALE_ROSTER", raising=False)

    assert _resolve_roster(_args(run_dir=str(run_dir))) == str(repo / ".ale" / "roster.json")


def test_roster_resolution_prefers_environment_over_run_repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    worktree = repo / "task-worktree"
    run_dir = repo / ".ale" / "runs" / "run-a"
    (repo / ".git").mkdir(parents=True)
    worktree.mkdir()
    run_dir.mkdir(parents=True)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("ALE_ROSTER", "/explicit/roster.json")

    assert _resolve_roster(_args(run_dir=str(run_dir))) == "/explicit/roster.json"


def test_lane_reason_accepts_short_nonempty_value():
    from pathlib import Path

    schema = json.loads((Path(__file__).parents[1] / "ale/schema/label.schema.json").read_text())
    assert schema["properties"]["provenance"]["properties"]["lane_reason"]["minLength"] == 3
