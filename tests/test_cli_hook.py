import io
import json
import os
import shutil
import subprocess
import time

import pytest

from ale.binding import binding_path
from ale.cli import main
from ale.handoff import write_atomic


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "bin", "ale-hook")


def fake_python(tmp_path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake = fake_bin / "python3"
    fake.write_text("#!/bin/sh\nprintf x > \"$ALE_MARKER\"\n")
    fake.chmod(0o755)
    return fake_bin


def run_launcher(tmp_path, **env_overrides):
    env = os.environ.copy()
    env.pop("ALE_TASK", None)
    env.pop("ALE_AGENT", None)
    env.pop("ALE_RUN_DIR", None)
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = str(fake_python(tmp_path)) + os.pathsep + env.get("PATH", "")
    env.update(env_overrides)
    return subprocess.run([LAUNCHER, "pre-tool"], input="{}", text=True,
                          capture_output=True, env=env)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    home = tmp_path / "home"
    monkeypatch.setenv("ALE_HOME", str(home))
    assert main(["init-run", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "0"]) == 0
    return tmp_path, run_dir, roster, home


def bind(home, run_dir, roster, session="s1", agent="a1", task="T01", subagent=None):
    path = binding_path(str(home), session, subagent)
    write_atomic(path, json.dumps({"run_dir": str(run_dir), "roster": str(roster),
                                   "task_id": task, "agent_id": agent, "source": "file"}))


def hook_input(monkeypatch, data):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))


def call_hook(monkeypatch, event, **extra):
    data = {"session_id": "s1", "hook_event_name": event, "cwd": "."}
    data.update(extra)
    hook_input(monkeypatch, data)
    return main(["hook", event])


def test_unbound_session_is_silent_and_fast(monkeypatch, capsys):
    hook_input(monkeypatch, {"session_id": "missing", "hook_event_name": "PreToolUse"})
    started = time.perf_counter()
    assert main(["hook", "pre-tool"]) == 0
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05 and capsys.readouterr() == ("", "")


def test_launcher_unbound_does_not_start_python(tmp_path):
    marker = tmp_path / "started"
    result = run_launcher(tmp_path, ALE_MARKER=str(marker))
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert not marker.exists()


def test_launcher_binding_file_falls_through_to_python(tmp_path):
    home = tmp_path / "home"
    bindings = home / ".ale" / "bindings"
    bindings.mkdir(parents=True)
    (bindings / "s.json").write_text("{}")
    marker = tmp_path / "started"
    result = run_launcher(tmp_path, ALE_MARKER=str(marker))
    assert result.returncode == 0 and marker.exists()


def test_launcher_binding_environment_falls_through_to_python(tmp_path):
    marker = tmp_path / "started"
    result = run_launcher(tmp_path, ALE_MARKER=str(marker), ALE_TASK="T01")
    assert result.returncode == 0 and marker.exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh is unavailable")
def test_launcher_unbound_twenty_calls_average_under_25ms(tmp_path):
    started = time.perf_counter()
    for _ in range(20):
        result = run_launcher(tmp_path, ALE_MARKER=str(tmp_path / "started"))
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    average_ms = (time.perf_counter() - started) * 1000 / 20
    assert average_ms < 25


def test_session_start_claims_and_prints_context(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert call_hook(monkeypatch, "session-start") == 0
    out = capsys.readouterr().out
    assert "T01" in out and "Executor rules" in out


def test_lost_claim_tells_session_to_stop(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster, agent="a1")
    assert main(["claim", "--task", "T01", "--agent", "a2", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "session-start") == 0
    assert "stop" in capsys.readouterr().out.lower()


def test_pre_tool_denial_uses_exit_two_and_stderr(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    code = call_hook(monkeypatch, "pre-tool", tool_name="Edit", tool_input={"file_path": "README.md"})
    assert code == 2 and "allowed" in capsys.readouterr().err


def test_lease_lost_denies_read_tool(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster, agent="a1")
    assert main(["claim", "--task", "T01", "--agent", "a2", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "pre-tool", tool_name="Read", tool_input={}) == 2
    assert "lease lost" in capsys.readouterr().err


def test_unbound_handler_crash_is_silent(monkeypatch, capsys):
    hook_input(monkeypatch, {"hook_event_name": "not-real"})
    assert main(["hook", "stop"]) == 0
    assert capsys.readouterr() == ("", "")


def test_bound_unreadable_label_denies_pre_tool(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    os.unlink(run_dir / "labels" / "T01.json")
    assert call_hook(monkeypatch, "pre-tool", tool_name="Read", tool_input={}) == 2
    assert capsys.readouterr().err


def test_post_tool_ten_calls_emit_one_heartbeat(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    for _ in range(10):
        assert call_hook(monkeypatch, "post-tool", tool_name="Read", tool_input={}) == 0
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["type"] == "heartbeat"]) == 1
    assert [e for e in events if e["type"] == "heartbeat"][0].get("auto") is True


def test_stop_submits_on_pass(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "stop", transcript_path="missing.json") == 0
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "submitted" for e in events)


def test_stop_blocks_twice_then_input_required(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    label_path = run_dir / "labels" / "T01.json"
    label = json.loads(label_path.read_text())
    label["acceptance"][0]["cmd"] = "false"
    label_path.write_text(json.dumps(label))
    for _ in range(2):
        assert call_hook(monkeypatch, "stop", transcript_path="missing.json") == 0
        assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert call_hook(monkeypatch, "stop", transcript_path="missing.json") == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "input_required" for e in events)


def test_stop_hook_active_never_blocks(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    label_path = run_dir / "labels" / "T01.json"
    label = json.loads(label_path.read_text())
    label["acceptance"][0]["cmd"] = "false"
    label_path.write_text(json.dumps(label))
    assert call_hook(monkeypatch, "stop", stop_hook_active=True, transcript_path="missing.json") == 0
    assert capsys.readouterr() == ("", "")


def test_usage_recorded_once_across_stop_calls(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "m", "usage": {"input_tokens": 2, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n")
    assert call_hook(monkeypatch, "stop", transcript_path=str(transcript)) == 0
    usage = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if '"type":"usage"' in line]
    assert len(usage) == 1


def test_subagent_binding_beats_session_binding(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster, agent="a1")
    bind(home, run_dir, roster, agent="a2", subagent="ag1")
    assert call_hook(monkeypatch, "pre-tool", agent_id="ag1", tool_name="Read", tool_input={}) == 2
    assert "lease lost" in capsys.readouterr().err


def test_guard_path_exit_codes(fixture):
    tmp_path, run_dir, roster, home = fixture
    assert main(["guard-path", "--task", "T01", "--path", "src/auth/a.py", "--project-root", str(tmp_path), "--run-dir", str(run_dir), "--roster", str(roster)]) == 0
    assert main(["guard-path", "--task", "T01", "--path", "README.md", "--project-root", str(tmp_path), "--run-dir", str(run_dir), "--roster", str(roster)]) == 1


def test_guard_path_accepts_absolute_path_inside_project(fixture):
    tmp_path, run_dir, roster, home = fixture
    assert main(["guard-path", "--task", "T01", "--path", str(tmp_path / "src" / "auth" / "a.py"), "--project-root", str(tmp_path), "--run-dir", str(run_dir), "--roster", str(roster)]) == 0


def test_prompt_submit_digest_is_at_most_twelve_lines(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    monkeypatch.setenv("ALE_ORCHESTRATOR_RUN_DIR", str(run_dir))
    hook_input(monkeypatch, {"session_id": "missing", "hook_event_name": "UserPromptSubmit"})
    assert main(["hook", "prompt-submit"]) == 0
    assert len(capsys.readouterr().out.splitlines()) <= 12


def test_debug_mode_writes_invocation_log(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    monkeypatch.setenv("ALE_HOOK_DEBUG", "1")
    bind(home, run_dir, roster)
    assert call_hook(monkeypatch, "post-tool", tool_name="Read", tool_input={}) == 0
    assert (run_dir / "hook-debug.log").exists()


def test_auto_heartbeat_cli_sets_auto(fixture):
    tmp_path, run_dir, roster, home = fixture
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert main(["heartbeat", "--task", "T01", "--agent", "a1", "--step", "auto", "--auto", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "2"]) == 0
    assert '"auto":true' in (run_dir / "events.jsonl").read_text()


def test_throttled_cli_heartbeat_does_not_write(fixture):
    tmp_path, run_dir, roster, home = fixture
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert main(["heartbeat", "--task", "T01", "--agent", "a1", "--step", "x", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "2"]) == 0
    before = len((run_dir / "events.jsonl").read_text().splitlines())
    assert main(["heartbeat", "--task", "T01", "--agent", "a1", "--step", "x", "--throttle-s", "60", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "3"]) == 0
    assert len((run_dir / "events.jsonl").read_text().splitlines()) == before


def test_subagent_stop_uses_agent_transcript(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster, agent="a1", subagent="ag1")
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("{}\n")
    assert call_hook(monkeypatch, "stop", agent_id="ag1", agent_transcript_path=str(transcript), transcript_path="missing") == 0


def test_pre_tool_denies_symlinked_file_outside_project(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    project = tmp_path / "project"
    (project / "src" / "auth").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (project / "src" / "auth" / "link.py").symlink_to(outside)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "pre-tool", cwd=str(project), tool_name="Write",
                     tool_input={"file_path": "src/auth/link.py"}) == 2
    assert "allowed" in capsys.readouterr().err


def test_pre_tool_denies_symlinked_directory_outside_project(fixture, monkeypatch, capsys):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    project = tmp_path / "project"
    (project / "src" / "auth").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "src" / "auth" / "linked").symlink_to(outside, target_is_directory=True)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "pre-tool", cwd=str(project), tool_name="Write",
                     tool_input={"file_path": "src/auth/linked/new.py"}) == 2
    assert "allowed" in capsys.readouterr().err


def test_pre_tool_allows_project_root_reached_through_symlink(fixture, monkeypatch):
    tmp_path, run_dir, roster, home = fixture
    bind(home, run_dir, roster)
    real_project = tmp_path / "project"
    (real_project / "src" / "auth").mkdir(parents=True)
    linked_project = tmp_path / "project-link"
    linked_project.symlink_to(real_project, target_is_directory=True)
    assert main(["claim", "--task", "T01", "--agent", "a1", "--run-dir", str(run_dir), "--roster", str(roster), "--now", "1"]) == 0
    assert call_hook(monkeypatch, "pre-tool", cwd=str(linked_project), tool_name="Write",
                     tool_input={"file_path": "src/auth/new.py"}) == 0
