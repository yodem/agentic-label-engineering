import json
import os
import shutil

from ale import events as E
from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(__file__))


def _run(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = os.path.join(ROOT, "examples", "roster.json")
    return run, roster


def _ale(run, roster, *args, now=0):
    return main(list(args) + ["--run-dir", str(run), "--roster", roster, "--now", str(now)])


def _label(run, task="T01"):
    path = run / "labels" / (task + ".json")
    with open(str(path), encoding="utf-8") as f:
        return json.load(f), path


def _write_label(run, task, value):
    _, path = _label(run, task)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_status_json_has_dynamic_keys(tmp_path, capsys):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {"schema_version": "1.0", "run_id": "r", "task_id": "T1", "title": "Task T1",
             "labels": {"role": "backend", "model_tier": "standard", "lane": "pane", "risk": "low", "effort": "S"},
             "routing": {"executor": None, "model": None, "resolved_from": None},
             "context": {"spec_path": "x", "pointers": [], "allowed_paths": ["src/**"], "depends_on": []},
             "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}, {"id": "A2", "cmd": "true", "expect": "exit0"}],
             "provenance": {"lane_reason": "planner supplied lane"}}
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "roster.json")
    assert main(["status", "--json", "--run-dir", str(run), "--roster", roster, "--now", "0"]) == 0
    row = json.loads(capsys.readouterr().out)["tasks"]["T1"]
    assert {"state", "blocked_by", "assignees", "attempt", "breaches", "lease_expires_ts", "last_step", "fixes", "fixed_by"} <= set(row)


def test_fix_flow_end_to_end(tmp_path, capsys):
    run, roster = _run(tmp_path)
    parent, _ = _label(run)
    parent["acceptance"][0]["cmd"] = "false"
    _write_label(run, "T01", parent)
    assert _ale(run, roster, "init-run") == 0
    assert _ale(run, roster, "claim", "--task", "T01", "--agent", "a1", now=1) == 0
    assert _ale(run, roster, "submit", "--task", "T01", "--agent", "a1", "--summary", "done", now=2) == 0
    assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=3) == 1
    assert _ale(run, roster, "fix", "--task", "T01", now=4) == 0
    fix, fix_path = _label(run, "T01.fix1")
    assert fix["acceptance"][0]["cmd"] == "false"
    fix["acceptance"][0]["cmd"] = "true"
    _write_label(run, "T01.fix1", fix)
    assert _ale(run, roster, "claim", "--task", "T01.fix1", "--agent", "f1", now=5) == 0
    assert _ale(run, roster, "submit", "--task", "T01.fix1", "--agent", "f1", "--summary", "fixed", now=6) == 0
    assert _ale(run, roster, "verify", "--task", "T01.fix1", "--cwd", str(run), now=7) == 0
    out = capsys.readouterr()
    assert _ale(run, roster, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["tasks"]["T01"]["state"] == "submitted"
    parent, _ = _label(run)
    parent["acceptance"][0]["cmd"] = "true"
    _write_label(run, "T01", parent)
    assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=8) == 0


def test_fix_requires_rejected_task(tmp_path):
    run, roster = _run(tmp_path)
    assert _ale(run, roster, "init-run") == 0
    before = (run / "events.jsonl").read_text()
    assert _ale(run, roster, "fix", "--task", "T01") != 0
    assert (run / "events.jsonl").read_text() == before


def test_third_fix_records_attempts_exhausted(tmp_path):
    run, roster = _run(tmp_path)
    parent, _ = _label(run)
    parent["acceptance"][0]["cmd"] = "false"
    _write_label(run, "T01", parent)
    assert _ale(run, roster, "init-run") == 0
    for n in (1, 2):
        if n == 2:
            assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=10) == 1
        assert _ale(run, roster, "claim", "--task", "T01", "--agent", "a%d" % n, now=n) == 0
        assert _ale(run, roster, "submit", "--task", "T01", "--agent", "a%d" % n, "--summary", "s", now=n + 1) == 0
        assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=n + 2) == 1
        assert _ale(run, roster, "fix", "--task", "T01", now=n + 3) == 0
        fix, _ = _label(run, "T01.fix%d" % n)
        fix["acceptance"][0]["cmd"] = "true"
        _write_label(run, "T01.fix%d" % n, fix)
        assert _ale(run, roster, "claim", "--task", "T01.fix%d" % n, "--agent", "f%d" % n, now=n + 4) == 0
        assert _ale(run, roster, "submit", "--task", "T01.fix%d" % n, "--agent", "f%d" % n, "--summary", "s", now=n + 5) == 0
        assert _ale(run, roster, "verify", "--task", "T01.fix%d" % n, "--cwd", str(run), now=n + 6) == 0
    assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=19) == 1
    assert _ale(run, roster, "fix", "--task", "T01", now=20) == 6
    assert any(e.get("breach") == "attempts_exhausted" for e in E.read_events(str(run / "events.jsonl")))


def test_task_added_event_is_small_for_large_fix_label(tmp_path):
    run, roster = _run(tmp_path)
    parent, _ = _label(run)
    parent["acceptance"][0]["cmd"] = "false"
    parent["acceptance"][1]["cmd"] = "x" * 3000
    _write_label(run, "T01", parent)
    assert _ale(run, roster, "init-run") == 0
    E.append_event(str(run / "events.jsonl"), E.make_event("claimed", "example-run", 1, "T01", "a", 1))
    E.append_event(str(run / "events.jsonl"), E.make_event("submitted", "example-run", 2, "T01", "a", 1, summary="s"))
    E.append_event(str(run / "events.jsonl"), E.make_event("rejected", "example-run", 3, "T01", None, 1,
                                                            evidence={"passed": False, "results":[{"id":"A1","ok":False}],"manual":[]}, reason="r"))
    assert _ale(run, roster, "fix", "--task", "T01") == 0
    line = [x for x in (run / "events.jsonl").read_bytes().splitlines() if b'task_added' in x][-1]
    assert len(line) < 4096


def test_assignments_relabel_and_invalid_value(tmp_path, capsys):
    run, roster = _run(tmp_path)
    assert _ale(run, roster, "init-run") == 0
    value = '[{"kind":"executor","role":"backend","model_tier":"standard","executor":null,"trigger":"ready"}]'
    assert _ale(run, roster, "relabel", "--task", "T01", "--field", "assignments", "--json", "--value", value, "--reason", "route") == 0
    assert _ale(run, roster, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["tasks"]["T01"]
    before = (run / "events.jsonl").read_text()
    bad = '[{"kind":"executor"},{"kind":"executor"}]'
    assert _ale(run, roster, "relabel", "--task", "T01", "--field", "assignments", "--json", "--value", bad, "--reason", "bad") == 1
    assert (run / "events.jsonl").read_text() == before


def test_lane_without_reason_is_refused(tmp_path):
    run, roster = _run(tmp_path)
    assert _ale(run, roster, "init-run") == 0
    assert _ale(run, roster, "relabel", "--task", "T01", "--field", "lane", "--value", "inline") == 2


def test_executor_label_change_is_ignored_by_status(tmp_path, capsys):
    run, roster = _run(tmp_path)
    assert _ale(run, roster, "init-run") == 0
    E.append_event(str(run / "events.jsonl"), E.make_event("label_changed", "example-run", 1, "T01", "worker", 1,
                                                            field="labels.role", old="backend", new="docs", reason="bad"))
    assert _ale(run, roster, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["tasks"]["T01"]["state"] == "ready"


def test_remove_live_refused_and_removed_dependency_breaches(tmp_path, capsys):
    run, roster = _run(tmp_path)
    t2, p2 = _label(run, "T02")
    t2["context"]["depends_on"] = ["T01"]
    p2.write_text(json.dumps(t2), encoding="utf-8")
    assert _ale(run, roster, "init-run") == 0
    assert _ale(run, roster, "claim", "--task", "T01", "--agent", "a", now=1) == 0
    assert _ale(run, roster, "remove", "--task", "T01", "--reason", "obsolete") == 1
    E.append_event(str(run / "events.jsonl"), E.make_event("lease_expired", "example-run", 2, "T01", None, 1))
    E.append_event(str(run / "events.jsonl"), E.make_event("released", "example-run", 3, "T01", None, 1))
    assert _ale(run, roster, "remove", "--task", "T01", "--reason", "obsolete") == 0
    assert _ale(run, roster, "watchdog") == 6
    # The first attempt was live, so the event log has only the later removal.
    assert sum(e["type"] == "label_removed" for e in E.read_events(str(run / "events.jsonl"))) == 1


def test_task_added_paths_are_sandboxed(tmp_path, capsys):
    run, roster = _run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text((run / "labels" / "T01.json").read_text(), encoding="utf-8")
    for value in (str(outside), "../outside.json"):
        E.append_event(str(run / "events.jsonl"), E.make_event(
            "task_added", "example-run", 1, "T99", None, 1,
            label_file=value, reason="test"))
        assert _ale(run, roster, "status", "--json") == 0
        assert "T99" not in json.loads(capsys.readouterr().out)["tasks"]


def test_task_added_symlink_escape_is_ignored(tmp_path, capsys):
    run, roster = _run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text((run / "labels" / "T01.json").read_text(), encoding="utf-8")
    (run / "labels" / "T99.json").symlink_to(outside)
    E.append_event(str(run / "events.jsonl"), E.make_event(
        "task_added", "example-run", 1, "T99", None, 1,
        label_file="T99.json", reason="test"))
    assert _ale(run, roster, "status", "--json") == 0
    assert "T99" not in json.loads(capsys.readouterr().out)["tasks"]


def test_fix_task_cannot_create_another_fix(tmp_path):
    run, roster = _run(tmp_path)
    label, path = _label(run)
    label["acceptance"][0]["cmd"] = "false"
    label["fixes"] = "T00"
    path.write_text(json.dumps(label), encoding="utf-8")
    assert _ale(run, roster, "init-run") == 0
    assert _ale(run, roster, "claim", "--task", "T01", "--agent", "a", now=1) == 0
    assert _ale(run, roster, "submit", "--task", "T01", "--agent", "a", "--summary", "s", now=2) == 0
    assert _ale(run, roster, "verify", "--task", "T01", "--cwd", str(run), now=3) == 1
    assert _ale(run, roster, "fix", "--task", "T01", now=4) == 6
    assert any(event.get("breach") == "attempts_exhausted"
               for event in E.read_events(str(run / "events.jsonl")))
