import json
from pathlib import Path

from ale.board import EventTailer, build_snapshot, diff_snapshots, health_verdict


FIXTURE = Path(__file__).parent / "fixtures" / "board" / "events.jsonl"


def _events():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def _label(task_id, **labels):
    return {
        "task_id": task_id, "title": "Title " + task_id,
        "labels": {"role": "backend", "model_tier": "fast", "lane": "workflow",
                    "risk": "low", "effort": "S", **labels},
        "routing": {"executor": "claude_code", "model": "m", "resolved_from": "fixture",
                    "agent": {"name": "worker-a"}},
        "context": {"depends_on": [], "worktree": {"mode": "none"}},
        "acceptance": [],
    }


def test_terminal_tasks_do_not_show_stale_heartbeat_step():
    snapshot = build_snapshot("/run", {"run": {}, "tasks": {"T1": {"state": "accepted", "last_step": "process alive"}}},
                              {"T1": _label("T1")}, _events(), {})
    assert snapshot["tasks"]["T1"]["state"] == "accepted"
    assert "last_step" not in snapshot["tasks"]["T1"]
    assert snapshot["tasks"]["T1"]["outcome"]["type"] == "accepted"


def test_released_spawn_failure_beats_later_auto_heartbeat_for_same_attempt():
    snapshot = build_snapshot("/run", {"run": {}, "tasks": {"T2": {"state": "working", "last_step": "process alive"}}},
                              {"T2": _label("T2")}, _events(), {})
    assert snapshot["tasks"]["T2"]["state"] == "released"
    assert snapshot["tasks"]["T2"]["outcome"]["reason"] == "spawn failed: FileNotFoundError"


def test_board_state_matches_status_state_for_every_fixture_task():
    status = {"run": {}, "tasks": {"T1": {"state": "accepted"}, "T2": {"state": "released"},
                                       "T3": {"state": "ready"}}}
    labels = {task_id: _label(task_id) for task_id in status["tasks"]}
    snapshot = build_snapshot("/run", status, labels, _events(), {})
    assert {key: value["state"] for key, value in snapshot["tasks"].items()} == {
        "T1": "accepted", "T2": "released", "T3": "ready"}


def test_attempt_tokens_sum_to_task_and_run_totals():
    events = _events() + [
        {"type": "usage", "task_id": "T3", "attempt": 1, "gen_ai.usage.input_tokens": 100,
         "gen_ai.usage.output_tokens": 25},
        {"type": "usage", "task_id": "T3", "attempt": 2, "gen_ai.usage.input_tokens": 200,
         "gen_ai.usage.output_tokens": 75},
    ]
    snapshot = build_snapshot("/run", {"run": {}, "tasks": {"T3": {"state": "ready"}}},
                              {"T3": _label("T3")}, events, {})
    task = snapshot["tasks"]["T3"]
    assert task["attempt_tokens"] == {"1": 125, "2": 275}
    assert task["tokens"] == 400 == snapshot["run"]["tokens"]


def test_every_nonterminal_task_exposes_why_it_is_not_running():
    labels = {"T3": _label("T3"), "T4": _label("T4", lane="inline")}
    labels["T3"]["context"]["depends_on"] = ["T9"]
    labels["T4"]["routing"]["executor"] = None
    status = {"run": {}, "tasks": {"T3": {"state": "planned", "blocked_by": ["T9"]},
                                     "T4": {"state": "ready"}}}
    snapshot = build_snapshot("/run", status, labels, [], {})
    assert snapshot["tasks"]["T3"]["not_running_reason"].startswith("Waiting on T9")
    assert snapshot["tasks"]["T4"]["not_running_reason"] == "No executor routed."


def test_agent_unknown_is_omitted_instead_of_rendered():
    label = _label("T5")
    label["routing"].pop("agent")
    snapshot = build_snapshot("/run", {"run": {}, "tasks": {"T5": {"state": "ready"}}},
                              {"T5": label}, [], {})
    assert "agent" not in snapshot["tasks"]["T5"]
    assert "unknown" not in json.dumps(snapshot)


def test_example_run_health_pill_is_stalled_and_no_task_is_hidden():
    example = json.loads((Path(__file__).parent / "fixtures" / "board" / "example_snapshot.json").read_text())
    assert health_verdict(example)["label"] == "Stalled: needs you"
    assert len(example["tasks"]) == 16


def test_rejected_fixes_of_accepted_parent_are_superseded_and_not_needs_you():
    snapshot = json.loads((Path(__file__).parent / "fixtures" / "board" / "superseded_snapshot.json").read_text())
    assert health_verdict(snapshot)["label"] == "Idle"


def test_diff_snapshots_emits_only_changed_and_removed_tasks():
    before = {"run": {"finished": False}, "tasks": {"T1": {"state": "ready"}, "T2": {"state": "working"}}}
    after = {"run": {"finished": False}, "tasks": {"T1": {"state": "working"}}}
    assert diff_snapshots(before, after) == [
        {"event": "task-remove", "data": {"task_id": "T2"}},
        {"event": "task-upsert", "data": {"task": {"state": "working"}}},
    ]


def test_tailer_retains_partial_lines_skips_malformed_and_oversized(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"type":"heartbeat"')
    tailer = EventTailer(str(path), max_line_bytes=32)
    assert tailer.poll() == []
    with path.open("ab") as handle:
        handle.write(b'}\nnot-json\n' + b'x' * 33 + b'\n')
    assert tailer.poll() == [{"type": "heartbeat"}]
    assert tailer.parse_errors == 2


def test_tailer_recovers_after_replacement_and_stable_projection_order(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"a"}\n')
    tailer = EventTailer(str(path))
    assert tailer.poll() == [{"type": "a"}]
    replacement = tmp_path / "replacement"
    replacement.write_text('{"type":"b"}\n')
    replacement.replace(path)
    assert tailer.poll() == [{"type": "b"}]
    snapshot = build_snapshot("/run", {"run": {}, "tasks": {"T2": {}, "T1": {}}},
                              {"T2": _label("T2"), "T1": _label("T1")}, [], {})
    assert list(snapshot["tasks"]) == ["T1", "T2"]


def test_projection_records_latest_event_timestamp_and_run_directory():
    snapshot = build_snapshot("/copied/run", {"run": {}, "tasks": {}}, {},
                              [{"type": "heartbeat", "ts": 123.5}], {})
    assert snapshot["run"]["last_event_ts"] == 123.5
    assert snapshot["run"]["path"] == "/copied/run"


def test_stale_idle_verdict_exposes_last_activity():
    snapshot = {"run": {"finished": False, "last_event_ts": 1000}, "tasks": {"T1": {"state": "ready"}}}
    verdict = health_verdict(snapshot, now=1000 + 3 * 3600)
    assert verdict["label"] == "Idle, last activity 3h ago"
