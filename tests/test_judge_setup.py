"""Roster judge modes: absent default (legacy), explicit off, shadow; setup --judge."""

import json
import subprocess
from pathlib import Path

import pytest

from ale import cli
from ale.cli import main
from ale.roster import load_roster

from judge_fakes import PLAN, read_calls, write_fake_judge

SHIPPED = Path(__file__).resolve().parents[1] / "ale" / "example_roster.json"


def test_setup_judge_shadow_changes_only_default(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    roster_path = tmp_path / ".ale" / "roster.json"
    roster_path.parent.mkdir()
    value = json.loads(SHIPPED.read_text())
    value["sentinel"] = 7
    roster_path.write_text(json.dumps(value))
    assert main(["setup", "--judge", "shadow"]) == 0
    value = json.loads(roster_path.read_text())
    assert value["judge"]["default"] == "shadow"
    assert value["sentinel"] == 7
    assert main(["setup", "--judge", "off"]) == 0
    assert json.loads(roster_path.read_text())["judge"]["default"] == "off"


def test_roster_without_judge_key_loads_and_is_off(tmp_path):
    source = json.loads(SHIPPED.read_text())
    source.pop("judge")
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(source))
    loaded = load_roster(str(path))
    assert "default" not in loaded["judge"]
    assert cli._judge_mode(loaded) == "off"


@pytest.mark.parametrize("default,bar,expected", [
    (None, True, "legacy"), ("off", True, "off"), ("shadow", False, "off"), ("shadow", True, "shadow")])
def test_judge_mode_has_three_states(default, bar, expected):
    roster = json.loads(SHIPPED.read_text())
    roster["judge"].pop("default")
    if default is not None:
        roster["judge"]["default"] = default
    if not bar:
        roster["judge"].pop("bar")
    assert cli._judge_mode(roster) == expected


def test_old_judge_shape_still_loads(tmp_path):
    source = json.loads(SHIPPED.read_text())
    for key in ("default", "bar", "questions", "model"):
        source["judge"].pop(key, None)
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(source))
    assert cli._judge_mode(load_roster(str(path))) == "legacy"


def _bake_with(tmp_path, monkeypatch, mutate, *flags):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    monkeypatch.chdir(repo)
    assert main(["setup"]) == 0
    roster_path = repo / ".ale" / "roster.json"
    roster = json.loads(roster_path.read_text())
    command, log_path = write_fake_judge(tmp_path, "conflict")
    roster["judge"]["command"] = command
    mutate(roster["judge"])
    roster_path.write_text(json.dumps(roster))
    plan = repo / "plan.md"
    plan.write_text(PLAN)
    main(["plan", "bake", str(plan), "--roster", str(roster_path)] + list(flags))
    return read_calls(log_path), repo


def test_explicit_off_beats_the_judge_flag(tmp_path, monkeypatch):
    calls, repo = _bake_with(tmp_path, monkeypatch, lambda judge: judge.update(default="off"), "--judge")
    assert calls == [] and not (repo / ".ale" / "shadow").exists()


def test_no_judge_flag_disables_shadow_default(tmp_path, monkeypatch):
    calls, _repo = _bake_with(tmp_path, monkeypatch, lambda judge: judge.update(default="shadow"), "--no-judge")
    assert calls == []


def test_shadow_default_collects_without_a_flag(tmp_path, monkeypatch):
    calls, repo = _bake_with(tmp_path, monkeypatch, lambda judge: judge.update(default="shadow"))
    assert calls and (repo / ".ale" / "shadow").exists()
    questions = {call["question"] for call in calls}
    shipped = json.loads(SHIPPED.read_text())["judge"]["questions"]
    assert shipped["over_an_hour"] in questions and shipped["sub"] in questions


def test_legacy_default_keeps_the_old_opt_in_and_asks_no_part_b_question(tmp_path, monkeypatch):
    calls, _repo = _bake_with(tmp_path, monkeypatch, lambda judge: judge.pop("default"))
    assert calls == []
    calls, _repo = _bake_with(tmp_path / "again", monkeypatch, lambda judge: judge.pop("default"), "--judge")
    shipped = json.loads(SHIPPED.read_text())["judge"]["questions"]
    legacy = {shipped[key] for key in ("role", "model_tier", "risk", "effort", "locality")}
    assert calls and {call["question"] for call in calls} <= legacy
