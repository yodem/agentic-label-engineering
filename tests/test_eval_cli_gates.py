import json
import os
import stat
import subprocess

from ale.cli import main


def _init_repo(path):
    path.mkdir()
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True, text=True)


def _ledger(path):
    path.write_text(json.dumps({"event": "start", "desc": "Add a small settings panel."}) + "\n")


def test_gate_refuses_when_git_check_ignore_errors(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ledger = repo / "events.jsonl"
    _ledger(ledger)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    git = stub_dir / "git"
    git.write_text("#!/bin/sh\nexit 129\n")
    git.chmod(git.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(stub_dir) + os.pathsep + os.environ.get("PATH", ""))

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "eval-out")]) == 1
    assert "could not be confirmed git-ignored" in capsys.readouterr().err
    assert not (repo / "eval-out").exists()


def test_gate_refuses_when_git_binary_missing_inside_repo(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ledger = repo / "events.jsonl"
    _ledger(ledger)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "eval-out")]) == 1
    assert "could not be confirmed git-ignored" in capsys.readouterr().err


def test_gate_allows_non_repo_without_git_binary(tmp_path, monkeypatch):
    ledger = tmp_path / "events.jsonl"
    _ledger(ledger)
    out = tmp_path / "plain-out"
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(out)]) == 0
    assert (out / "corpus.jsonl").exists()


def test_gate_allows_ignored_directory(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("eval-out/\n")
    ledger = repo / "events.jsonl"
    _ledger(ledger)

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "eval-out")]) == 0
    assert (repo / "eval-out" / "corpus.jsonl").exists()


def test_gate_refuses_tracked_directory_and_missing_child(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ledger = repo / "events.jsonl"
    _ledger(ledger)

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "eval-out")]) == 1
    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "docs" / "eval-out")]) == 1


def test_gate_refuses_symlink_resolving_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ledger = repo / "events.jsonl"
    _ledger(ledger)
    target = repo / "tracked-parent"
    target.mkdir()
    link = tmp_path / "link-to-tracked"
    link.symlink_to(target)

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(link / "eval-out")]) == 1


def test_every_eval_subcommand_refuses_tracked_out_dir(tmp_path, roster):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ledger = repo / "events.jsonl"
    _ledger(ledger)
    roster_path = repo / "roster.json"
    roster_path.write_text(json.dumps(roster))
    empty_jsonl = repo / "empty.jsonl"
    empty_jsonl.write_text("")
    gold = repo / "gold.json"
    gold.write_text(json.dumps({"gold": {}, "basis": {}}))

    assert main(["eval", "corpus", "--ledger", str(ledger), "--out", str(repo / "corpus-out")]) == 1
    assert main(["eval", "gold", "--a", str(empty_jsonl), "--b", str(empty_jsonl), "--out", str(repo / "gold-out")]) == 1
    assert main(["eval", "judge", "--corpus", str(empty_jsonl), "--roster", str(roster_path), "--out", str(repo / "judge-out")]) == 1
    assert main(["eval", "report", "--corpus", str(empty_jsonl), "--gold", str(gold), "--judge", str(empty_jsonl),
                 "--a", str(empty_jsonl), "--roster", str(roster_path), "--out", str(repo / "report-out")]) == 1
