import json
import os
import subprocess


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "bin", "ale-spawn")


def _request(tmp_path, executor, agent="T1-executor-backend-1", kind="executor"):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("prompt")
    path = tmp_path / "request.json"
    env = {"ALE_TASK": "T1", "ALE_AGENT": agent, "ALE_RUN_DIR": str(tmp_path),
           "ALE_ROSTER": "roster.json"}
    if kind == "monitor":
        env.pop("ALE_TASK")
        env["ALE_READ_ONLY"] = "1"
    path.write_text(json.dumps({
        "agent_id": agent, "task_id": "T1", "kind": kind, "role": "backend",
        "executor": executor, "model": "model-x", "cwd": str(tmp_path),
        "spec_path": "spec.md", "handoff_path": str(tmp_path / "handoff.md"),
        "env": env, "prompt_file": str(prompt),
    }))
    return path


def _run(path, env=None):
    merged = os.environ.copy()
    merged["ALE_SPAWN_DRY"] = "1"
    if env:
        merged.update(env)
    return subprocess.run([SCRIPT, str(path)], cwd=ROOT, env=merged, text=True,
                          capture_output=True)


def test_shell_syntax():
    assert subprocess.run(["sh", "-n", SCRIPT]).returncode == 0


def test_claude_headless_dry_run_prints_argv(tmp_path):
    proc = _run(_request(tmp_path, "claude-headless"), {"ALE_PLUGIN_ROOT": ROOT})
    assert proc.returncode == 0
    assert "ale-exec" in proc.stdout and "claude" in proc.stdout and "-p" in proc.stdout
    assert "--model model-x" in proc.stdout and "--plugin-dir" in proc.stdout


def test_codex_exec_dry_run_prints_argv(tmp_path):
    proc = _run(_request(tmp_path, "codex-exec"))
    assert proc.returncode == 0
    assert "ale-exec" in proc.stdout and "codex" in proc.stdout and "exec" in proc.stdout
    assert "--model model-x" in proc.stdout


def test_pi_print_dry_run_prints_argv(tmp_path):
    proc = _run(_request(tmp_path, "pi-print"))
    assert proc.returncode == 0
    assert "ale-exec" in proc.stdout and "pi" in proc.stdout and "-p" in proc.stdout
    assert "--model model-x" in proc.stdout


def test_child_environment_has_ale_bin_and_plugin_pythonpath(tmp_path):
    proc = _run(_request(tmp_path, "claude-headless"))
    assert proc.returncode == 0
    assert "ALE_BIN=python3 -m ale" in proc.stdout
    assert "PYTHONPATH=" + ROOT in proc.stdout


def test_monitor_claude_does_not_receive_plugin_dir(tmp_path):
    proc = _run(_request(tmp_path, "claude-headless", "T1-monitor-backend-1", kind="monitor"),
                {"ALE_PLUGIN_ROOT": ROOT})
    assert proc.returncode == 0
    assert "claude" in proc.stdout and "--model model-x" in proc.stdout
    assert "--plugin-dir" in proc.stdout and "--disallowedTools Write,Edit,MultiEdit,NotebookEdit" in proc.stdout
    assert "ALE_READ_ONLY=1" in proc.stdout


def test_herdr_pane_starts_and_sends_with_safe_agent_name(tmp_path):
    path = _request(tmp_path, "herdr-pane", "Agent.ID_99")
    herdr = tmp_path / "herdr-exec.py"
    herdr.write_text("#!/usr/bin/env python3\n")
    proc = _run(path, {"ALE_HERDR_EXEC": str(herdr)})
    assert proc.returncode == 0
    assert "start" in proc.stdout and "send" in proc.stdout
    assert "agent-id_99" in proc.stdout


def test_unknown_executor_exits_two(tmp_path):
    proc = _run(_request(tmp_path, "mystery"))
    assert proc.returncode == 2
    assert "unknown executor" in proc.stderr
