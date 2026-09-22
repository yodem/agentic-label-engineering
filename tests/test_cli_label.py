import json
import os
import shutil
import stat

import pytest

from ale.cli import main
from ale.events import read_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ale(run_dir, roster_path, *args, now=0):
    return main(list(args) + ["--run-dir", run_dir, "--roster", roster_path, "--now", str(now)])


def _draft(task_id, role="backend", model_tier="standard", risk="low", effort="M", lane="inline",
           depends_on=None, allowed_paths=None):
    return {
        "schema_version": "1.0",
        "run_id": "cli-label-run",
        "task_id": task_id,
        "title": "Task %s" % task_id,
        "labels": {"role": role, "model_tier": model_tier, "lane": lane, "risk": risk, "effort": effort},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "specs/%s.md" % task_id, "pointers": [],
                     "allowed_paths": allowed_paths or ["src/%s/**" % task_id], "depends_on": depends_on or []},
        "acceptance": [
            {"id": "A1", "cmd": "true", "expect": "exit0"},
            {"id": "A2", "cmd": "test -d .", "expect": "exit0"},
        ],
        "provenance": {"lane_reason": "Short task with the human present, so inline."},
    }


@pytest.fixture
def label_run(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "drafts").mkdir(parents=True)
    roster_path = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster_path))
    return str(run_dir), str(roster_path)


def _write_draft(run_dir, draft):
    with open(os.path.join(run_dir, "drafts", "%s.json" % draft["task_id"]), "w", encoding="utf-8") as f:
        json.dump(draft, f)


def test_no_judge_writes_labels_events_then_validate_and_init_run_succeed(label_run):
    run_dir, roster_path = label_run
    _write_draft(run_dir, _draft("T1"))
    _write_draft(run_dir, _draft("T2", role="docs", allowed_paths=["docs/**"]))
    assert ale(run_dir, roster_path, "label", "--no-judge") == 0
    assert os.path.exists(os.path.join(run_dir, "labels", "T1.json"))
    assert os.path.exists(os.path.join(run_dir, "labels", "T2.json"))
    events = read_events(os.path.join(run_dir, "events.jsonl"))
    label_votes = [e for e in events if e["type"] == "label_vote"]
    assert len(label_votes) > 0
    assert all(e["agent_id"] is None and e["attempt"] == 1 and e["task_id"] in ("T1", "T2") for e in label_votes)
    assert ale(run_dir, roster_path, "validate") == 0
    assert ale(run_dir, roster_path, "init-run") == 0


def test_invalid_draft_writes_nothing(label_run):
    run_dir, roster_path = label_run
    bad = _draft("T1")
    bad["labels"]["role"] = "not-a-real-role"
    _write_draft(run_dir, bad)
    assert ale(run_dir, roster_path, "label", "--no-judge") == 1
    assert not os.path.exists(os.path.join(run_dir, "labels"))
    assert not os.path.exists(os.path.join(run_dir, "events.jsonl"))


def test_broken_judge_command_still_exits_zero_and_abstains(label_run):
    run_dir, roster_path = label_run
    _write_draft(run_dir, _draft("T1"))
    with open(roster_path, encoding="utf-8") as f:
        roster = json.load(f)
    roster["judge"]["command"] = ["/nonexistent/judge-bin"]
    with open(roster_path, "w", encoding="utf-8") as f:
        json.dump(roster, f)
    ret = ale(run_dir, roster_path, "label")
    out = None
    assert ret == 0
    with open(os.path.join(run_dir, "labels", "T1.json"), encoding="utf-8") as f:
        final = json.load(f)
    draft = _draft("T1")
    assert final["labels"] == dict(draft["labels"], locality="any")


def test_summary_reports_judge_abstains_and_disagreements(label_run, tmp_path, capsys):
    run_dir, roster_path = label_run
    _write_draft(run_dir, _draft("T1", role="backend"))
    stub = tmp_path / "stub-judge.sh"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type": "choice", "choice": "frontend: UI components, styling, client state.", '
        '"confidence": 0.9, "probabilities": {"frontend: UI components, styling, client state.": 0.9}, "model": "m"}\'\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    with open(roster_path, encoding="utf-8") as f:
        roster = json.load(f)
    roster["judge"]["command"] = [str(stub)]
    with open(roster_path, "w", encoding="utf-8") as f:
        json.dump(roster, f)
    capsys.readouterr()
    assert ale(run_dir, roster_path, "label") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tasks"] == 1
    role_disagreements = [d for d in summary["disagreements"] if d["field"] == "role"]
    assert role_disagreements == [{"task": "T1", "field": "role", "planner": "backend", "judge": "frontend"}]
    assert summary["judge_abstains"] == 4  # model_tier, risk, effort, locality abstain (stub always says "frontend")


def test_outside_spec_path_content_never_reaches_the_judge(label_run, tmp_path, capsys, monkeypatch):
    run_dir, roster_path = label_run
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("TOP SECRET CONTENT THAT MUST NOT LEAK")

    draft = _draft("T1")
    draft["context"]["spec_path"] = str(secret)
    _write_draft(run_dir, draft)

    stdin_capture = tmp_path / "stdin_capture.txt"
    stub = tmp_path / "stub-judge.sh"
    stub.write_text(
        "#!/bin/sh\n"
        "cat > %s\n"
        'echo \'{"type": "choice", "choice": "other: none of these fit", '
        '"confidence": 0.5, "probabilities": {"other: none of these fit": 0.5}, "model": "m"}\'\n' % str(stdin_capture)
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    with open(roster_path, encoding="utf-8") as f:
        roster = json.load(f)
    roster["judge"]["command"] = [str(stub)]
    with open(roster_path, "w", encoding="utf-8") as f:
        json.dump(roster, f)

    capsys.readouterr()
    assert ale(run_dir, roster_path, "label") == 0
    err = capsys.readouterr().err
    assert "spec_path outside the project, not read: %s" % str(secret) in err

    captured_stdin = stdin_capture.read_text() if stdin_capture.exists() else ""
    assert "TOP SECRET CONTENT THAT MUST NOT LEAK" not in captured_stdin


def test_label_after_run_started_is_refused(label_run, capsys):
    run_dir, roster_path = label_run
    _write_draft(run_dir, _draft("T1"))
    assert ale(run_dir, roster_path, "label", "--no-judge") == 0
    assert ale(run_dir, roster_path, "init-run") == 0

    events_path = os.path.join(run_dir, "events.jsonl")
    with open(os.path.join(run_dir, "labels", "T1.json"), encoding="utf-8") as f:
        label_before = f.read()
    events_before = read_events(events_path)

    capsys.readouterr()
    assert ale(run_dir, roster_path, "label", "--no-judge") == 1
    err = capsys.readouterr().err
    assert "run already started: labels are frozen, use `ale relabel`" in err

    with open(os.path.join(run_dir, "labels", "T1.json"), encoding="utf-8") as f:
        label_after = f.read()
    assert label_after == label_before
    assert read_events(events_path) == events_before


def test_no_judge_produces_no_judge_abstains(label_run, capsys):
    run_dir, roster_path = label_run
    _write_draft(run_dir, _draft("T1"))
    capsys.readouterr()
    assert ale(run_dir, roster_path, "label", "--no-judge") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["judge_abstains"] == 0
    assert summary["disagreements"] == []
