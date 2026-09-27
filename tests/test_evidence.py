import json
import os

from ale import events as E
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "evidence", "T1.md")

LABEL = {
    "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Add the parser",
    "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
    "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["src/*"], "depends_on": []},
    "acceptance": [
        {"id": "A1", "cmd": "pytest -q tests/test_parser.py", "expect": "exit0"},
        {"id": "A2", "cmd": "grep -c 'a|b' src/parser.py", "expect": "exit:1"},
        {"id": "A3", "manual": "A reviewer reads the parser error messages."},
    ],
}

EVIDENCE = {
    "passed": True, "manual": ["A3"],
    "results": [
        {"id": "A1", "exit": 0, "ok": True,
         "tail": "collecting\nline 2\nline 3\nline 4\nline 5\nline 6\n3 passed in 0.1s\n"},
        {"id": "A2", "exit": 1, "ok": True, "tail": "0\n"},
    ],
    "required": [{"command": "ruff check src", "exit": 0, "ok": True, "output": "All checks passed!\n"}],
    "required_failures": [],
    "files": ["src/parser.py", "tests/test_parser.py"],
    "tree": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "signoff": "lead: read the <error> text",
}


def _run(tmp_path, evidence=EVIDENCE, accept=True):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps(LABEL))
    events = str(run / "events.jsonl")
    E.append_event(events, E.make_event("claimed", "run-1", 1, "T1", "worker", 1))
    E.append_event(events, E.make_event("submitted", "run-1", 2, "T1", "worker", 1, summary="done"))
    if accept:
        E.append_event(events, E.make_event("verified", "run-1", 3, "T1", None, 1, evidence=evidence))
        E.append_event(events, E.make_event("accepted", "run-1", 4, "T1", None, 1, evidence=evidence))
    return run


def _roster():
    return os.path.join(ROOT, "examples", "roster.json")


def test_evidence_render_matches_the_golden_file(tmp_path, capsys):
    run = _run(tmp_path)
    assert main(["evidence", "T1", "--run-dir", str(run), "--roster", _roster()]) == 0
    with open(GOLDEN, encoding="utf-8") as handle:
        assert capsys.readouterr().out == handle.read()


def test_evidence_out_writes_the_same_bytes_twice(tmp_path):
    run = _run(tmp_path)
    out = tmp_path / "evidence" / "T1.md"
    for _ in range(2):
        assert main(["evidence", "T1", "--run-dir", str(run), "--roster", _roster(), "--out", str(out)]) == 0
    with open(GOLDEN, encoding="utf-8") as handle:
        assert out.read_text(encoding="utf-8") == handle.read()


def test_evidence_without_verification_exits_one(tmp_path, capsys):
    run = _run(tmp_path, accept=False)
    assert main(["evidence", "T1", "--run-dir", str(run), "--roster", _roster()]) == 1
    assert "has no verification evidence" in capsys.readouterr().err


def test_evidence_for_an_unknown_task_exits_one(tmp_path, capsys):
    run = _run(tmp_path)
    assert main(["evidence", "T9", "--run-dir", str(run), "--roster", _roster()]) == 1
    assert "unknown task T9" in capsys.readouterr().err
