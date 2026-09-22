import json
import os
import shutil
import subprocess

from ale.cli import _fit, main
from ale.events import MAX_EVENT_BYTES, make_event, append_event, read_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _big_evidence(n_files=300):
    return {
        "passed": True,
        "results": [{"id": "A1", "ok": True, "exit": 0, "tail": "ok"}],
        "manual": [],
        "required": [],
        "required_failures": [],
        "files": ["src/module_%04d/very_long_descriptive_file_name_for_padding.py" % i for i in range(n_files)],
        "path_violations": [],
    }


def test_fit_keeps_files_count_and_shrinks_files():
    evidence = _big_evidence(300)
    _fit(evidence)
    assert evidence["files_count"] == 300
    assert evidence.get("files_truncated") is True
    assert len(evidence["files"]) < 300
    assert len(json.dumps(evidence)) <= 3000


def test_fit_emitted_verified_event_is_accepted_and_within_limit(tmp_path):
    evidence = _big_evidence(300)
    _fit(evidence)
    ev = make_event("verified", "run1", 1234.0, task_id="T01", attempt=1, evidence=evidence)
    path = str(tmp_path / "events.jsonl")
    append_event(path, ev)
    data = json.dumps(ev, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(data) <= MAX_EVENT_BYTES
    got = read_events(path)
    assert len(got) == 1
    assert got[0]["evidence"]["files_count"] == 300


def test_verify_base_with_many_changed_files_end_to_end(tmp_path, capsys):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(tmp_path / "roster.json"))
    roster = str(tmp_path / "roster.json")

    subprocess.run(["git", "init"], cwd=str(run_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test-user"], cwd=str(run_dir), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(run_dir), check=True)
    (run_dir / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(run_dir), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(run_dir), check=True, capture_output=True)

    for i in range(290):
        p = run_dir / ("outside_module_%04d_padding_for_a_long_path_name.py" % i)
        p.write_text("x = 1\n")

    def ale(*args, now=0):
        return main(list(args) + ["--run-dir", str(run_dir), "--roster", roster, "--now", str(now)])

    ale("init-run")
    ale("claim", "--task", "T01", "--agent", "a1", now=1)
    ale("submit", "--task", "T01", "--agent", "a1", "--summary", "done", now=2)
    ale("verify", "--task", "T01", "--cwd", str(run_dir), "--base", "HEAD", now=3)

    events = read_events(str(run_dir / "events.jsonl"))
    verified = [e for e in events if e["type"] == "verified"]
    assert len(verified) == 1
    assert verified[0]["evidence"]["files_count"] >= 290
    line = json.dumps(verified[0], sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(line) <= MAX_EVENT_BYTES
