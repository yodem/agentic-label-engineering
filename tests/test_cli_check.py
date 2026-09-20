import hashlib
import json
import os
import shutil

import pytest

from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(d))
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(tmp_path / "roster.json"))
    return str(d)


def ale(run_dir, *args, now=0):
    roster = os.path.join(os.path.dirname(run_dir), "roster.json")
    return main(list(args) + ["--run-dir", run_dir, "--roster", roster, "--now", str(now)])


def edit_label(run_dir, tid, fn):
    p = os.path.join(run_dir, "labels", tid + ".json")
    with open(p) as f:
        label = json.load(f)
    fn(label)
    with open(p, "w") as f:
        json.dump(label, f)


def test_check_works_for_ready_and_claimed_without_writing_events(run_dir, capsys):
    assert ale(run_dir, "init-run") == 0
    events = os.path.join(run_dir, "events.jsonl")
    before = open(events, "rb").read()
    before_hash = hashlib.sha256(before).hexdigest()
    assert ale(run_dir, "check", "--task", "T01", "--cwd", run_dir) == 0
    assert open(events, "rb").read() == before
    assert hashlib.sha256(open(events, "rb").read()).hexdigest() == before_hash
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    claimed = open(events, "rb").read()
    assert ale(run_dir, "check", "--task", "T01", "--cwd", run_dir) == 0
    assert open(events, "rb").read() == claimed
    assert "A1 ok exit=0" in capsys.readouterr().out


def test_check_failing_command_names_it_and_returns_one(run_dir, capsys):
    edit_label(run_dir, "T01", lambda l: l["acceptance"][0].update(cmd="echo nope; exit 1"))
    assert ale(run_dir, "check", "--task", "T01", "--cwd", run_dir) == 1
    output = capsys.readouterr().out
    assert "A1 FAIL exit=1" in output


def test_check_manual_entry_is_listed_but_does_not_fail(run_dir, capsys):
    edit_label(run_dir, "T01", lambda l: l["acceptance"].append({"id": "A3", "manual": "review"}))
    assert ale(run_dir, "check", "--task", "T01", "--cwd", run_dir) == 0
    assert "manual: A3" in capsys.readouterr().out


def test_check_json_has_evidence_keys_and_tails(run_dir, capsys):
    edit_label(run_dir, "T01", lambda l: l["acceptance"].__setitem__(0, {
        "id": "A1", "cmd": "python3 -c 'print(\"x\" * 500)'", "expect": "exit0"
    }))
    assert ale(run_dir, "check", "--task", "T01", "--cwd", run_dir, "--json") == 0
    evidence = json.loads(capsys.readouterr().out)
    assert set(evidence) == {"passed", "results", "manual"}
    assert len(evidence["results"][0]["tail"]) <= 300


def test_check_unknown_and_unsafe_task_errors(run_dir, capsys):
    assert ale(run_dir, "check", "--task", "missing", "--cwd", run_dir) == 1
    assert "ale: unknown task missing" in capsys.readouterr().err
    assert ale(run_dir, "check", "--task", "../T01", "--cwd", run_dir) == 2
    assert "ale: unsafe id" in capsys.readouterr().err
