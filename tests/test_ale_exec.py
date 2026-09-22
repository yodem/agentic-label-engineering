import json
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(ROOT, "bin", "ale-exec")


@pytest.fixture
def harness(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log = tmp_path / "ale.log"
    fake = tmp_path / "fake-ale"
    fake.write_text(textwrap.dedent("""
        #!/bin/sh
        printf '%s %s\n' "$1" "$*" >> "$ALE_FAKE_LOG"
        case "$1" in
          claim) [ "${ALE_FAKE_CLAIM:-ok}" = lost ] && exit 3; exit 0 ;;
          heartbeat) [ "${ALE_FAKE_HEARTBEAT:-ok}" = lost ] && exit 4; exit 0 ;;
          check)
            if [ "${ALE_FAKE_CHECK:-ok}" = fail ]; then
              printf '%s\n' 'A1 FAIL exit=1'
              exit 1
            fi
            printf '%s\n' 'A1 ok exit=0'
            exit 0
            ;;
          submit|input-required|usage) exit 0 ;;
          *) exit 0 ;;
        esac
    """))
    fake.chmod(0o755)
    return run_dir, log, str(fake)


def run_exec(harness, command, *options, **env_updates):
    run_dir, log, fake = harness
    env = os.environ.copy()
    env.update({
        "ALE_BIN": fake,
        "ALE_RUN_DIR": str(run_dir),
        "ALE_ROSTER": str(run_dir / "roster.json"),
        "ALE_FAKE_LOG": str(log),
    })
    env.update(env_updates)
    return subprocess.run(
        [WRAPPER, "--task", "T1", "--agent", "agent", *options, "--", *command],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )


def log_text(harness):
    return harness[1].read_text() if harness[1].exists() else ""


def heartbeat_loops_for(wrapper_pid):
    marker = "ale-exec-heartbeat-loop-%s" % wrapper_pid
    result = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return {int(pid) for pid in result.stdout.split()}


def test_help_documents_three_limits():
    proc = subprocess.run([WRAPPER, "--help"], text=True, capture_output=True)
    assert proc.returncode == 0
    assert "proves the process is alive" in proc.stdout
    assert "no path blocking" in proc.stdout
    assert "between heartbeats" in proc.stdout


def test_claim_loss_does_not_start_child(harness, tmp_path):
    marker = tmp_path / "started"
    child = tmp_path / "child.sh"
    child.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
    child.chmod(0o755)
    proc = run_exec(harness, [str(child)], ALE_FAKE_CLAIM="lost")
    assert proc.returncode == 3
    assert not marker.exists()


def test_heartbeats_arrive_and_success_submits(harness):
    proc = run_exec(harness, ["sleep", "0.25"], "--heartbeat-s", "0.05")
    assert proc.returncode == 0
    lines = log_text(harness).splitlines()
    assert sum(line.startswith("heartbeat ") for line in lines) >= 2
    assert any(line.startswith("submit ") for line in lines)


def test_child_exit_code_is_preserved(harness):
    proc = run_exec(harness, ["sh", "-c", "exit 7"])
    assert proc.returncode == 7
    assert "submit " in log_text(harness)


def test_no_heartbeat_process_survives_normal_child(harness):
    proc = run_exec(harness, ["sleep", "0.05"], "--heartbeat-s", "0.01")
    assert proc.returncode == 0
    pgrep = subprocess.run(["pgrep", "-f", "ale-exec-heartbeat-loop-0"], capture_output=True)
    assert pgrep.returncode != 0


def test_kill_nine_child_leaves_no_heartbeat_process(harness, tmp_path):
    pid_file = tmp_path / "pid"
    child = tmp_path / "child.sh"
    child.write_text("#!/bin/sh\necho $$ > '%s'\nsleep 10\n" % pid_file)
    child.chmod(0o755)
    env = os.environ.copy()
    env.update({"ALE_BIN": harness[2], "ALE_RUN_DIR": str(harness[0]),
                "ALE_ROSTER": str(harness[0] / "roster.json"), "ALE_FAKE_LOG": str(harness[1])})
    proc = subprocess.Popen([WRAPPER, "--task", "T1", "--agent", "agent", "--heartbeat-s", "0.02", "--", str(child)],
                            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if pid_file.exists():
            break
        time.sleep(0.01)
    os.kill(int(pid_file.read_text()), 9)
    assert proc.wait(timeout=5) == 137
    assert subprocess.run(["pgrep", "-f", "ale-exec-heartbeat-loop-0"], capture_output=True).returncode != 0


def test_lease_loss_terminates_child_and_returns_four(harness, tmp_path):
    child = tmp_path / "child.sh"
    child.write_text("#!/bin/sh\nsleep 10\n")
    child.chmod(0o755)
    proc = run_exec(harness, [str(child)], "--heartbeat-s", "0.01", ALE_FAKE_HEARTBEAT="lost")
    assert proc.returncode == 4


def test_acceptance_failure_raises_input_required(harness):
    proc = run_exec(harness, ["true"], ALE_FAKE_CHECK="fail")
    assert proc.returncode == 0
    log = log_text(harness)
    assert "input-required " in log
    assert "A1 FAIL exit=1" in log
    assert "submit " not in log


def test_codex_usage_event_is_recorded(harness, tmp_path):
    child = tmp_path / "child.sh"
    event = json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 24763, "cached_input_tokens": 24448,
        "output_tokens": 122, "reasoning_output_tokens": 0,
    }})
    child.write_text("#!/bin/sh\nprintf '%s\\n' '%s'\n" % (event.replace("'", "'\\''"), event.replace("'", "'\\''")))
    child.chmod(0o755)
    proc = run_exec(harness, [str(child)], "--usage-from", "codex-json", ALE_MODEL="codex-test")
    assert proc.returncode == 0
    usage = [line for line in log_text(harness).splitlines() if line.startswith("usage ")]
    assert len(usage) == 1
    assert "24763" in usage[0] and "122" in usage[0] and "codex-test" in usage[0]


def test_codex_usage_records_cache_counters(harness, tmp_path):
    child = tmp_path / "child.sh"
    event = json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 24763, "cached_input_tokens": 24448,
        "cache_write_input_tokens": 100, "output_tokens": 122,
    }})
    child.write_text("#!/bin/sh\nprintf '%s\\n' '%s'\n" % (event, event))
    child.chmod(0o755)
    proc = run_exec(harness, [str(child)], "--usage-from", "codex-json")
    assert proc.returncode == 0
    usage = [line for line in log_text(harness).splitlines() if line.startswith("usage ")]
    assert len(usage) == 1
    assert "--cache-read-tokens 24448" in usage[0]
    assert "--cache-write-tokens 100" in usage[0]


def test_pi_usage_records_cache_counters(harness, tmp_path):
    child = tmp_path / "child.sh"
    event = json.dumps({"type": "message_end", "message": {"usage": {
        "input": 200, "output": 10, "cacheRead": 150, "cacheWrite": 20,
    }}})
    child.write_text("#!/bin/sh\nprintf '%s\\n' '%s'\n" % (event, event))
    child.chmod(0o755)
    proc = run_exec(harness, [str(child)], "--usage-from", "pi-json")
    assert proc.returncode == 0
    usage = [line for line in log_text(harness).splitlines() if line.startswith("usage ")]
    assert len(usage) == 1
    assert "--input-tokens 200" in usage[0] and "--output-tokens 10" in usage[0]
    assert "--cache-read-tokens 150" in usage[0]
    assert "--cache-write-tokens 20" in usage[0]


def test_codex_usage_event_attributes_wrapper_agent(tmp_path):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    initialized = subprocess.run(
        [sys.executable, "-m", "ale", "init-run", "--run-dir", str(run_dir),
         "--roster", str(roster), "--now", "0"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert initialized.returncode == 0

    child = tmp_path / "child.sh"
    event = json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "output_tokens": 3,
    }})
    child.write_text("#!/bin/sh\nprintf '%s\\n' '%s'\n" % (event, event))
    child.chmod(0o755)
    env = os.environ.copy()
    env.update({"ALE_RUN_DIR": str(run_dir), "ALE_ROSTER": str(roster), "ALE_MODEL": "codex-test"})
    proc = subprocess.run(
        [WRAPPER, "--task", "T01", "--agent", "codex-worker", "--usage-from", "codex-json",
         "--", str(child)],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    assert proc.returncode == 0
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1 and usage[0]["agent_id"] == "codex-worker"


def test_scoped_heartbeat_check_ignores_unrelated_loop(harness):
    dummy = subprocess.Popen(
        ["ale-exec-heartbeat-loop-0", "-c", "import time; time.sleep(30)"],
        executable=sys.executable,
    )
    try:
        proc = subprocess.Popen(
            [WRAPPER, "--task", "T1", "--agent", "agent", "--", "sleep", "0.05"],
            cwd=ROOT,
            env={**os.environ, "ALE_BIN": harness[2], "ALE_RUN_DIR": str(harness[0]),
                 "ALE_ROSTER": str(harness[0] / "roster.json"), "ALE_FAKE_LOG": str(harness[1])},
        )
        assert proc.wait(timeout=5) == 0
        assert heartbeat_loops_for(proc.pid) == set()
    finally:
        dummy.terminate()
        dummy.wait(timeout=5)


def test_term_wrapper_terminates_child(harness, tmp_path):
    pid_file = tmp_path / "pid"
    child = tmp_path / "child.sh"
    child.write_text("#!/bin/sh\necho $$ > '%s'\nsleep 30\n" % pid_file)
    child.chmod(0o755)
    env = os.environ.copy()
    env.update({"ALE_BIN": harness[2], "ALE_RUN_DIR": str(harness[0]),
                "ALE_ROSTER": str(harness[0] / "roster.json"), "ALE_FAKE_LOG": str(harness[1])})
    proc = subprocess.Popen([WRAPPER, "--task", "T1", "--agent", "agent", "--", str(child)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if pid_file.exists():
            break
        time.sleep(0.01)
    child_pid = int(pid_file.read_text())
    os.kill(proc.pid, 15)
    assert proc.wait(timeout=15) in (130, 143)
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("child process survived wrapper termination")


def test_second_term_during_cleanup_is_ignored_and_cleanup_finishes(harness, tmp_path):
    pid_file = tmp_path / "pid"
    child = tmp_path / "child.sh"
    child.write_text("#!/bin/sh\necho $$ > '%s'\ntrap '' TERM INT\nsleep 30\n" % pid_file)
    child.chmod(0o755)
    env = os.environ.copy()
    env.update({"ALE_BIN": harness[2], "ALE_RUN_DIR": str(harness[0]),
                "ALE_ROSTER": str(harness[0] / "roster.json"), "ALE_FAKE_LOG": str(harness[1]),
                "ALE_EXEC_GRACE_S": "1"})
    proc = subprocess.Popen([WRAPPER, "--task", "T1", "--agent", "agent", "--", str(child)],
                            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if pid_file.exists():
            break
        time.sleep(0.01)
    assert pid_file.exists()
    os.kill(proc.pid, 15)
    time.sleep(0.3)
    os.kill(proc.pid, 15)
    assert proc.wait(timeout=5) == 143
    child_pid = int(pid_file.read_text())
    with pytest.raises(OSError):
        os.kill(child_pid, 0)
    assert subprocess.run(["pgrep", "-f", "ale-exec-heartbeat-loop-0"], capture_output=True).returncode != 0
