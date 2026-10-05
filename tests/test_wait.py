import json
import os
import time

from ale import cli

ROSTER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "roster.json")


def _run(tmp_path, count=1):
    run_dir = tmp_path / "run"
    labels = run_dir / "labels"
    labels.mkdir(parents=True)
    for number in range(1, count + 1):
        task_id = "T%d" % number
        (labels / (task_id + ".json")).write_text(json.dumps({
            "schema_version": "1.0", "run_id": "r", "task_id": task_id,
            "title": task_id, "labels": {"role": "backend", "effort": "M"},
            "context": {"depends_on": []},
        }))
    (run_dir / "events.jsonl").write_text("")
    return run_dir


def _event(run_dir, kind, task="T1", **fields):
    with open(run_dir / "events.jsonl", "a") as handle:
        handle.write(json.dumps({"type": kind, "task_id": task, "ts": time.time(), **fields}) + "\n")


def test_wait_returns_when_all_tasks_reach_requested_state(tmp_path, capsys):
    run_dir = _run(tmp_path, count=2)
    for task_id in ("T1", "T2"):
        _event(run_dir, "claimed", task_id, agent_id="a", attempt=1)
        _event(run_dir, "submitted", task_id, summary="done", agent_id="a", attempt=1)
        _event(run_dir, "accepted", task_id, evidence={"ok": True}, attempt=1)
    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--until", "accepted", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "condition met"
    assert [row["state"] for row in result["tasks"]] == ["accepted", "accepted"]


def test_wait_times_out(tmp_path, capsys):
    run_dir = _run(tmp_path)
    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--until", "accepted",
                     "--timeout", "0.01", "--interval", "0.001"]) == 1
    assert "reason: timeout" in capsys.readouterr().out


def test_wait_requires_run_dir(capsys):
    assert cli.main(["wait", "--until", "accepted"]) == 2
    assert "--run-dir is required" in capsys.readouterr().err


def test_wait_returns_early_for_input_required_with_question(tmp_path, capsys):
    run_dir = _run(tmp_path)
    _event(run_dir, "claimed", agent_id="a", attempt=1)
    question = "Need a decision from the lead"
    _event(run_dir, "input_required", agent_id="a", attempt=1, question=question)
    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--until", "accepted"]) == 1
    out = capsys.readouterr().out
    assert "T1 input_required: " + question in out
    assert "attention: T1 input-required" in out


def test_wait_task_subset_ignores_other_tasks(tmp_path, capsys):
    run_dir = _run(tmp_path, count=2)
    _event(run_dir, "claimed", "T1", agent_id="a", attempt=1)
    _event(run_dir, "submitted", "T1", summary="done", agent_id="a", attempt=1)
    _event(run_dir, "accepted", "T1", evidence={"ok": True}, attempt=1)
    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--task", "T1", "--until", "accepted",
                     "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [row["task"] for row in result["tasks"]] == ["T1"]


def test_wait_does_not_return_early_for_fixing_parent(tmp_path, capsys):
    run_dir = _run(tmp_path, count=2)
    child = run_dir / "labels" / "T2.json"
    label = json.loads(child.read_text())
    label["fixes"] = "T1"
    child.write_text(json.dumps(label))
    _event(run_dir, "claimed", "T1", agent_id="a", attempt=1)
    _event(run_dir, "submitted", "T1", agent_id="a", attempt=1, summary="needs fixes")
    _event(run_dir, "rejected", "T1", reason="fix needed", evidence={"ok": False})
    _event(run_dir, "reopened", "T1", reason="fix task started")
    _event(run_dir, "claimed", "T2", agent_id="b", attempt=1)

    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--until", "accepted", "--timeout", "0.01", "--interval", "0.001"]) == 1
    out = capsys.readouterr().out
    assert "T1 fixing" in out
    assert "reason: timeout" in out


def test_wait_treats_requested_rejected_state_as_success(tmp_path, capsys):
    run_dir = _run(tmp_path, count=2)
    _event(run_dir, "claimed", "T1", agent_id="a", attempt=1)
    _event(run_dir, "submitted", "T1", agent_id="a", attempt=1, summary="needs fixes")
    _event(run_dir, "rejected", "T1", reason="fix needed", evidence={"ok": False})
    _event(run_dir, "claimed", "T2", agent_id="b", attempt=1)
    _event(run_dir, "submitted", "T2", agent_id="b", attempt=1, summary="done")
    _event(run_dir, "accepted", "T2", evidence={"ok": True})

    assert cli.main(["wait", "--run-dir", str(run_dir), "--roster", ROSTER,
                     "--until", "rejected,accepted", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "condition met"
    assert [row["state"] for row in result["tasks"]] == ["rejected", "accepted"]
