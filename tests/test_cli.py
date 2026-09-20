import io
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


def state(run_dir, capsys, tid="T01"):
    capsys.readouterr()
    ale(run_dir, "status", "--json")
    return json.loads(capsys.readouterr().out)["tasks"][tid]


def test_validate_ok_then_broken(run_dir):
    assert ale(run_dir, "validate") == 0
    edit_label(run_dir, "T01", lambda l: l["labels"].update(role="wizard"))
    assert ale(run_dir, "validate") == 1


def test_init_run_once(run_dir):
    assert ale(run_dir, "init-run") == 0
    assert ale(run_dir, "init-run") == 1
    assert os.path.exists(os.path.join(run_dir, "decisions.md"))


def test_full_flow(run_dir, capsys):
    ale(run_dir, "init-run")
    assert ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1) == 0
    assert ale(run_dir, "claim", "--task", "T01", "--agent", "a2", now=2) == 3
    assert ale(run_dir, "heartbeat", "--task", "T01", "--agent", "a2", "--step", "x", now=3) == 4
    assert ale(run_dir, "heartbeat", "--task", "T01", "--agent", "a1", "--step", "did it", "--files", "src/auth/a.py,src/auth/b.py", now=4) == 0
    assert ale(run_dir, "submit", "--task", "T01", "--agent", "a1", "--summary", "done", now=5) == 0
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, now=6) == 0
    st = state(run_dir, capsys)
    assert st["state"] == "accepted" and st["evidence"]["passed"] is True
    text = open(os.path.join(run_dir, "handoff", "T01.a1.md")).read()
    assert "status: done" in text and "src/auth/b.py" in text


def test_dependency_blocks_claim(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "claim", "--task", "T02", "--agent", "b1", now=1) == 3


def test_verify_requires_submitted(run_dir):
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, now=2) == 1


def test_failed_acceptance_rejects(run_dir, capsys):
    edit_label(run_dir, "T01", lambda l: l["acceptance"][0].update(cmd="echo nope; exit 1"))
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    ale(run_dir, "submit", "--task", "T01", "--agent", "a1", "--summary", "trust me", now=2)
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, now=3) == 1
    st = state(run_dir, capsys)
    assert (st["state"], st["attempt"], st["rejections"]) == ("rejected", 2, 1) and "A1" in st["last_reject_reason"]


def test_manual_criterion_needs_signoff(run_dir, capsys):
    edit_label(run_dir, "T01", lambda l: l["acceptance"].__setitem__(1, {"id": "A2", "manual": "Reviewer confirms shape"}))
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    ale(run_dir, "submit", "--task", "T01", "--agent", "a1", "--summary", "s", now=2)
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, now=3) == 5
    assert state(run_dir, capsys)["state"] == "submitted"
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, "--signoff", "yotam", now=4) == 0
    assert state(run_dir, capsys)["evidence"]["signoff"] == "yotam"


def test_high_risk_needs_signoff(run_dir):
    edit_label(run_dir, "T01", lambda l: l["labels"].update(risk="high"))
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    ale(run_dir, "submit", "--task", "T01", "--agent", "a1", "--summary", "s", now=2)
    assert ale(run_dir, "verify", "--task", "T01", "--cwd", run_dir, now=3) == 5


def test_watchdog_releases_dead_claim_once(run_dir, capsys):
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=0)
    ale(run_dir, "heartbeat", "--task", "T01", "--agent", "a1", "--step", "half way", now=10)
    capsys.readouterr()
    assert ale(run_dir, "watchdog", now=2000) == 6
    assert [b["breach"] for b in json.loads(capsys.readouterr().out)] == ["lease_expired"]
    st = state(run_dir, capsys)
    assert st["state"] == "released" and st["last_step"] == "half way"
    assert ale(run_dir, "watchdog", now=2001) == 0
    assert ale(run_dir, "heartbeat", "--task", "T01", "--agent", "a1", "--step", "zombie", now=2002) == 4
    assert ale(run_dir, "claim", "--task", "T01", "--agent", "a2", now=2003) == 0


def test_input_required_and_answer(run_dir, capsys):
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    assert ale(run_dir, "answer", "--task", "T01", "--text", "early", now=2) == 1
    ale(run_dir, "input-required", "--task", "T01", "--agent", "a1", "--question", "which port?", now=3)
    assert ale(run_dir, "watchdog", now=4) == 6
    assert ale(run_dir, "answer", "--task", "T01", "--text", "8080", now=5) == 0
    assert state(run_dir, capsys)["state"] == "working"


def test_note_truncated_and_decide_logged(run_dir):
    ale(run_dir, "init-run")
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=1)
    assert ale(run_dir, "note", "--task", "T01", "--agent", "a1", "--text", "z" * 9000, now=2) == 0
    assert ale(run_dir, "decide", "--text", "Refresh tokens are opaque strings", now=3) == 0
    assert "opaque strings" in open(os.path.join(run_dir, "decisions.md")).read()


def test_usage_recorded(run_dir, capsys):
    ale(run_dir, "init-run")
    assert ale(run_dir, "usage", "--task", "T01", "--model", "claude-sonnet-5", "--input-tokens", "1200", "--output-tokens", "300", now=1) == 0
    assert state(run_dir, capsys)["tokens"] == 1500


def test_paths_within(run_dir, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("src/auth/a.py\n"))
    assert ale(run_dir, "paths-within", "T01") == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("src/auth/a.py\nREADME.md\n"))
    assert ale(run_dir, "paths-within", "T01") == 1
    assert "README.md" in capsys.readouterr().out


def test_doctor(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "doctor", now=1) == 0
    ale(run_dir, "claim", "--task", "T01", "--agent", "a1", now=2)
    assert ale(run_dir, "doctor", now=99999) == 1
    with open(os.path.join(run_dir, "events.jsonl"), "a") as f:
        f.write("not json\n")
    assert ale(run_dir, "doctor", now=3) == 1


def test_missing_run_dir_is_usage_error(monkeypatch):
    monkeypatch.delenv("ALE_RUN_DIR", raising=False)
    assert main(["status"]) == 2


def test_unsafe_ids_refused_before_any_write(run_dir):
    ale(run_dir, "init-run")
    events = os.path.join(run_dir, "events.jsonl")
    before = open(events).read()
    assert ale(run_dir, "claim", "--task", "T01", "--agent", "../../../../escape-poc", now=1) == 2
    assert ale(run_dir, "heartbeat", "--task", "../T01", "--agent", "a1", "--step", "x", now=2) == 2
    assert ale(run_dir, "note", "--task", "T01", "--agent", "a1", "--text", "t", "--to", "../T02", now=3) == 2
    assert open(events).read() == before
    assert not os.path.exists(os.path.join(run_dir, "handoff"))


def test_non_numeric_now_is_usage_error(run_dir):
    roster = os.path.join(os.path.dirname(run_dir), "roster.json")
    assert main(["status", "--run-dir", run_dir, "--roster", roster, "--now", "abc"]) == 2


def test_doctor_reports_deleted_label(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "doctor", now=1) == 0
    os.remove(os.path.join(run_dir, "labels", "T02.json"))
    assert ale(run_dir, "doctor", now=2) == 1


def test_unsafe_task_id_inside_label_file_is_refused(run_dir):
    edit_label(run_dir, "T01", lambda l: l.update(task_id="../T01"))
    assert ale(run_dir, "status") == 1


def test_newline_agent_id_refused(run_dir):
    ale(run_dir, "init-run")
    events = os.path.join(run_dir, "events.jsonl")
    before = open(events).read()
    assert ale(run_dir, "claim", "--task", "T01", "--agent", "a1\n", now=1) == 2
    assert open(events).read() == before
