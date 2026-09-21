import json
import os
import shutil
import subprocess


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _commands(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "command" and isinstance(item, str):
                yield item
            else:
                yield from _commands(item)
    elif isinstance(value, list):
        for item in value:
            yield from _commands(item)


def test_registered_hook_commands_are_executable(tmp_path):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    subprocess.run(["python3", "-m", "ale", "init-run", "--run-dir", str(run_dir), "--roster", str(roster)],
                   cwd=ROOT, check=True, capture_output=True, text=True)
    env = os.environ.copy()
    env.update({"CLAUDE_PLUGIN_ROOT": ROOT, "ALE_PLUGIN_ROOT": ROOT,
                "ALE_TASK": "T01", "ALE_AGENT": "a1", "ALE_RUN_DIR": str(run_dir),
                "ALE_ROSTER": str(roster)})
    payload = json.dumps({"session_id": "s1", "cwd": ROOT, "hook_event_name": "PreToolUse",
                          "tool_name": "Write", "tool_input": {"file_path": "README.md"}})
    for config_name in ("hooks/hooks.json", "adapters/codex/hooks.json"):
        with open(os.path.join(ROOT, config_name), encoding="utf-8") as f:
            config = json.load(f)
        commands = list(_commands(config))
        assert commands
        for command in commands:
            proc = subprocess.run(["sh", "-c", command], cwd=ROOT, env=env,
                                  input=payload, text=True, capture_output=True)
            is_pre_tool = command.endswith("pre-tool")
            assert proc.returncode == (2 if is_pre_tool else 0), (config_name, command, proc.stderr)
            if is_pre_tool:
                assert proc.stderr
