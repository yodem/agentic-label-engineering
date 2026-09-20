import json
import os

from ale.binding import binding_path, from_env, resolve
from ale.handoff import write_atomic


def test_from_env_complete_uses_explicit_roster(tmp_path):
    env = {"ALE_TASK": "T01", "ALE_AGENT": "a1", "ALE_RUN_DIR": str(tmp_path),
           "ALE_ROSTER": "/tmp/roster.json"}
    assert from_env(env) == {"run_dir": str(tmp_path), "roster": "/tmp/roster.json",
                             "task_id": "T01", "agent_id": "a1", "source": "env"}


def test_from_env_incomplete_is_none(tmp_path):
    assert from_env({"ALE_TASK": "T01", "ALE_AGENT": "a1"}) is None


def test_from_env_derives_existing_roster(tmp_path):
    run_dir = tmp_path / "a" / "b"
    run_dir.mkdir(parents=True)
    roster = tmp_path / "roster.json"
    roster.write_text("{}")
    env = {"ALE_TASK": "T01", "ALE_AGENT": "a1", "ALE_RUN_DIR": str(run_dir)}
    assert from_env(env)["roster"] == str(roster)


def test_from_env_unsafe_ids_are_none(tmp_path):
    env = {"ALE_TASK": "../T01", "ALE_AGENT": "a1", "ALE_RUN_DIR": str(tmp_path)}
    assert from_env(env) is None


def test_file_binding_round_trip(tmp_path):
    path = binding_path(str(tmp_path), "session-1", None)
    write_atomic(path, json.dumps({"run_dir": "/run", "roster": "/roster",
                                   "task_id": "T01", "agent_id": "a1"}))
    assert resolve({}, str(tmp_path), "session-1", None) == {
        "run_dir": "/run", "roster": "/roster", "task_id": "T01", "agent_id": "a1",
        "source": "file"}


def test_subagent_binding_beats_session_binding(tmp_path):
    write_atomic(binding_path(str(tmp_path), "s1", None),
                 json.dumps({"run_dir": "/session", "roster": "r", "task_id": "T01", "agent_id": "a1"}))
    write_atomic(binding_path(str(tmp_path), "s1", "worker"),
                 json.dumps({"run_dir": "/worker", "roster": "r", "task_id": "T02", "agent_id": "a2"}))
    assert resolve({}, str(tmp_path), "s1", "worker")["task_id"] == "T02"


def test_corrupt_json_returns_none(tmp_path):
    write_atomic(binding_path(str(tmp_path), "s1", None), "not json")
    assert resolve({}, str(tmp_path), "s1", None) is None


def test_unreadable_or_invalid_binding_returns_none(tmp_path):
    write_atomic(binding_path(str(tmp_path), "s1", None), json.dumps({"task_id": "../bad"}))
    assert resolve({}, str(tmp_path), "s1", None) is None


def test_odd_session_id_stays_inside_bindings(tmp_path):
    path = binding_path(str(tmp_path), "../../x", None)
    bindings = os.path.realpath(os.path.join(str(tmp_path), ".ale", "bindings"))
    assert os.path.dirname(os.path.realpath(path)) == bindings


def test_resolve_prefers_environment(tmp_path):
    write_atomic(binding_path(str(tmp_path), "s1", None),
                 json.dumps({"run_dir": "/file", "roster": "r", "task_id": "T01", "agent_id": "a1"}))
    env = {"ALE_TASK": "T02", "ALE_AGENT": "a2", "ALE_RUN_DIR": "/env"}
    assert resolve(env, str(tmp_path), "s1", None)["source"] == "env"


def test_missing_binding_is_none(tmp_path):
    assert resolve({}, str(tmp_path), "missing", None) is None
