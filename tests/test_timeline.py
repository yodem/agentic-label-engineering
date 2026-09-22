from ale.timeline import format_timeline, task_metadata, timeline


def labels():
    return {"T1": {"routing": {"model": "m", "executor": "x"}, "acceptance": []}}


def test_timeline_is_chronological_and_generic():
    rows = timeline([
        {"ts": 3, "type": "mystery", "task_id": "T1", "agent_id": None, "value": 7},
        {"ts": 1, "type": "claimed", "task_id": "T1", "agent_id": "a"},
    ], labels())
    assert [row["type"] for row in rows] == ["claimed", "mystery"]
    assert rows[1]["changed"] == "value=7"


def test_label_change_lines_are_specific():
    rows = timeline([
        {"ts": 0, "type": "task_added", "task_id": "T1", "label_file": "T1.json"},
        {"ts": 1, "type": "label_changed", "task_id": "T1", "field": "labels.role", "old": "a", "new": "b"},
        {"ts": 2, "type": "label_removed", "task_id": "T1", "reason": "obsolete"},
        {"ts": 3, "type": "spawned", "task_id": "T1", "model": "m", "executor": "x", "assignment_kind": "primary"},
    ], labels())
    assert rows[0]["changed"] == "added T1.json"
    assert rows[1]["changed"] == "labels.role: a -> b"
    assert rows[2]["changed"] == "removed: obsolete"
    assert rows[3]["changed"] == "assigned m/primary via x"


def test_monitor_verdict_shows_agent_and_decision():
    rows = timeline([{"ts": 4, "type": "monitor_verdict", "task_id": "T1", "agent_id": None,
                      "agent_id_minted": "T1-monitor-1", "verdict": "escalate", "text": "Needs review"}], labels())
    assert rows[0]["agent"] == "T1-monitor-1"
    assert rows[0]["changed"] == "escalate: Needs review"


def test_format_timeline():
    assert format_timeline([{"relative_time": 0, "type": "run_started", "task": None, "agent": None, "changed": ""}]) == ["+0s run_started - -"]


def test_metadata_usage_sums_per_task_agent_and_run():
    events = [
        {"ts": 0, "type": "claimed", "task_id": "T1", "agent_id": "a", "attempt": 1},
        {"ts": 2, "type": "usage", "task_id": "T1", "agent_id": "a", "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 4},
        {"ts": 4, "type": "accepted", "task_id": "T1", "agent_id": None, "attempt": 1},
    ]
    out = task_metadata(events, labels(), {})
    assert out["tasks"]["T1"]["tokens"] == {"input": 10, "output": 4, "cache_read": 0, "cache_write": 0}
    assert out["agents"]["a"]["tokens"] == out["tasks"]["T1"]["tokens"]
    assert out["totals"]["tokens"] == out["tasks"]["T1"]["tokens"]


def test_metadata_state_durations_sum_to_wall_time():
    events = [
        {"ts": 0, "type": "claimed", "task_id": "T1", "agent_id": "a", "attempt": 1},
        {"ts": 2, "type": "heartbeat", "task_id": "T1", "agent_id": "a"},
        {"ts": 5, "type": "submitted", "task_id": "T1", "agent_id": "a"},
        {"ts": 9, "type": "accepted", "task_id": "T1", "agent_id": None},
    ]
    wall = task_metadata(events, labels(), {})["tasks"]["T1"]["wall_seconds"]
    assert sum(wall.values()) == 9


def test_metadata_files_from_verify_evidence_and_breaches():
    events = [{"ts": 1, "type": "verified", "task_id": "T1", "evidence": {"files_touched": ["a.py"]}},
              {"ts": 2, "type": "breach", "task_id": "T1", "breach": "stuck"}]
    out = task_metadata(events, labels(), {})["tasks"]["T1"]
    assert out["files_touched"] == ["a.py"] and out["breaches"] == ["stuck"]


def test_metadata_optional_prices():
    events = [{"ts": 1, "type": "usage", "task_id": "T1", "agent_id": "a", "gen_ai.usage.input_tokens": 1000000, "gen_ai.usage.output_tokens": 2000000}]
    out = task_metadata(events, labels(), {}, {"m": {"input_per_mtok": 2, "output_per_mtok": 3}})
    assert out["tasks"]["T1"]["cost"] == 8.0


def test_metadata_cost_excludes_cached_input_and_clamps_billable_input():
    events = [{"ts": 1, "type": "usage", "task_id": "T1", "agent_id": "a",
               "gen_ai.usage.input_tokens": 10, "gen_ai.usage.cache_read_input_tokens": 8,
               "gen_ai.usage.output_tokens": 2}]
    out = task_metadata(events, labels(), {}, {"m": {"input_per_mtok": 2, "output_per_mtok": 3}})
    assert out["tasks"]["T1"]["cost"] == 0.00001
    events[0]["gen_ai.usage.cache_read_input_tokens"] = 100
    out = task_metadata(events, labels(), {}, {"m": {"input_per_mtok": 2, "output_per_mtok": 3}})
    assert out["tasks"]["T1"]["cost"] == 0.000006


def test_metadata_without_prices_has_unknown_cost():
    events = [{"ts": 1, "type": "usage", "task_id": "T1", "agent_id": "a", "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2}]
    assert task_metadata(events, labels(), {})["tasks"]["T1"]["cost"] is None


def test_metadata_cache_counters():
    events = [{"ts": 1, "type": "usage", "task_id": "T1", "agent_id": "a", "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2,
               "gen_ai.usage.cache_read_input_tokens": 3, "gen_ai.usage.cache_creation_input_tokens": 4}]
    assert task_metadata(events, labels(), {})["tasks"]["T1"]["tokens"]["cache_read"] == 3
    assert task_metadata(events, labels(), {})["tasks"]["T1"]["tokens"]["cache_write"] == 4


def test_metadata_resolves_spawned_model_and_executor():
    events = [{"ts": 1, "type": "spawned", "task_id": "T1", "model": "spawn-model", "executor": "spawn-exec"}]
    out = task_metadata(events, labels(), {})["tasks"]["T1"]
    assert out["model"] == "spawn-model" and out["executor"] == "spawn-exec"
