import json
import os
import shutil

from ale.cli import main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_run(tmp_path):
    run_dir = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    return str(run_dir), str(roster)


def test_bind_unknown_task_exits_one(tmp_path, monkeypatch):
    run_dir, roster = setup_run(tmp_path)
    monkeypatch.setenv("ALE_HOME", str(tmp_path / "home"))
    assert main(["bind", "--session", "s1", "--task", "NOPE", "--agent", "a1",
                 "--run-dir", run_dir, "--roster", roster]) == 1


def test_bind_and_unbind_round_trip(tmp_path, monkeypatch):
    run_dir, roster = setup_run(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("ALE_HOME", str(home))
    args = ["--run-dir", run_dir, "--roster", roster]
    assert main(["bind", "--session", "s1", "--subagent", "worker", "--task", "T01",
                 "--agent", "a1"] + args) == 0
    path = home / ".ale" / "bindings" / "s1.worker.json"
    assert json.loads(path.read_text())["source"] == "file"
    assert main(["unbind", "--session", "s1", "--subagent", "worker"] + args) == 0
    assert not path.exists()


def test_unbind_missing_is_success(tmp_path, monkeypatch):
    run_dir, roster = setup_run(tmp_path)
    monkeypatch.setenv("ALE_HOME", str(tmp_path / "home"))
    assert main(["unbind", "--session", "s1", "--run-dir", run_dir, "--roster", roster]) == 0


def test_cli_sanitises_traversal_session(tmp_path, monkeypatch):
    run_dir, roster = setup_run(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("ALE_HOME", str(home))
    assert main(["bind", "--session", "../../x", "--task", "T01", "--agent", "a1",
                 "--run-dir", run_dir, "--roster", roster]) == 0
    assert list((home / ".ale" / "bindings").glob("*.json"))
