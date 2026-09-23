import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest


def _run_dir(path):
    (path / "labels").mkdir()
    (path / "events.jsonl").write_text("", encoding="utf-8")


def _start_board(path):
    return subprocess.Popen(
        [sys.executable, "-m", "ale", "board", "--run-dir", str(path)],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_signal_removes_board_metadata(tmp_path, stop_signal):
    _run_dir(tmp_path)
    process = _start_board(tmp_path)
    try:
        url = process.stdout.readline()
        assert url.startswith("http://127.0.0.1:")
        assert (tmp_path / "board.json").exists()
        process.send_signal(stop_signal)
        assert process.wait(timeout=5) == 0
        assert not (tmp_path / "board.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_dead_pid_board_metadata_does_not_block_start(tmp_path):
    _run_dir(tmp_path)
    (tmp_path / "board.json").write_text(json.dumps({"pid": 2147483647, "url": "http://127.0.0.1:1/stale/"}), encoding="utf-8")
    process = _start_board(tmp_path)
    try:
        url = process.stdout.readline()
        assert url.startswith("http://127.0.0.1:")
        metadata = json.loads((tmp_path / "board.json").read_text(encoding="utf-8"))
        assert metadata["pid"] == process.pid
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)
