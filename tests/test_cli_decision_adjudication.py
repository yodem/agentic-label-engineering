import os
import shutil
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def run_dir(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(tmp_path / "roster.json"))
    return str(run)

from ale.cli import main


def test_decision_adjudication_rejects_duplicate(run_dir, capsys):
    roster = os.path.join(os.path.dirname(run_dir), "roster.json")
    args = ["adjudicate", "--decision", "role", "--task", "T01", "--value", "backend",
            "--by", "lead", "--run-dir", run_dir, "--roster", roster]
    assert main(args) == 0
    assert main(args) == 1
    assert "already adjudicated" in capsys.readouterr().err


def _args(run_dir, decision, value, task="T01"):
    roster = os.path.join(os.path.dirname(run_dir), "roster.json")
    return ["adjudicate", "--decision", decision, "--task", task, "--value", value,
            "--by", "lead", "--run-dir", run_dir, "--roster", roster]


def test_deterministic_executor_cannot_be_adjudicated(run_dir, capsys):
    assert main(_args(run_dir, "executor", "codex-exec")) == 2
    assert "deterministic" in capsys.readouterr().err


def test_every_judged_decision_accepts_a_valid_value_and_rejects_an_unknown_one(run_dir, capsys):
    from ale.decisions import judged_decision_ids
    valid = {"lane": "pane", "role": "backend", "sub": "api", "phase": "implement", "model_tier": "cheap",
             "risk": "low", "effort": "M", "locality": "local", "rejection_action": "reopen",
             "monitor_verdict": "nudge", "needs_monitor": "yes"}
    assert set(valid) == set(judged_decision_ids())
    for decision in judged_decision_ids():
        assert main(_args(run_dir, decision, "not-an-option")) == 1, decision
        assert main(_args(run_dir, decision, valid[decision])) == 0, decision
    assert main(_args(run_dir, "no_such_decision", "x")) == 2
