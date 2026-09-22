import copy
import json
import os
import subprocess

import pytest

from ale.roster import RosterError, load_roster, resolve, roster_hash


def test_resolve_wildcard_row():
    roster = {"routing": [{"role": "*", "model_tier": "standard",
                            "executor": "claude-headless", "model": "claude-sonnet-5"}]}
    assert resolve(roster, "backend", "standard") == {"executor": "claude-headless", "model": "claude-sonnet-5"}


def test_exact_row_beats_wildcard():
    roster = {"routing": [
        {"role": "*", "model_tier": "standard", "executor": "claude-headless", "model": "claude-sonnet-5"},
        {"role": "backend", "model_tier": "standard", "executor": "pi", "model": "openrouter/x"},
    ]}
    assert resolve(roster, "backend", "standard")["executor"] == "pi"


def test_unresolvable_pair_raises(roster):
    roster["routing"] = [r for r in roster["routing"] if r["model_tier"] != "frontier"]
    with pytest.raises(RosterError):
        resolve(roster, "backend", "frontier")


def test_hash_is_stable_and_changes(roster):
    h1 = roster_hash(roster)
    assert len(h1) == 8 and h1 == roster_hash(json.loads(json.dumps(roster)))
    roster["uncertain_below"] = 0.5
    assert roster_hash(roster) != h1


def test_load_roster_rejects_bad_file(tmp_path):
    p = tmp_path / "roster.json"
    p.write_text(json.dumps({"schema_version": "1.0"}))
    with pytest.raises(RosterError):
        load_roster(str(p))


def test_load_roster_accepts_sub_and_phase_judge_modes():
    from ale.validate import load_schema, validate

    repo_root = os.path.dirname(os.path.dirname(__file__))
    roster_path = os.path.join(repo_root, "examples", "roster.json")
    loaded = load_roster(roster_path)
    assert loaded["judge"]["modes"]["sub"] == "shadow"
    assert loaded["judge"]["modes"]["phase"] == "shadow"

    invalid_mode_roster = copy.deepcopy(loaded)
    invalid_mode_roster["judge"]["modes"]["sub"] = "authoritative"
    invalid_mode_roster["judge"]["modes"]["role"] = "authoritative"
    assert validate(invalid_mode_roster, load_schema("roster.schema.json")) == []

    invalid_mode_roster["judge"]["modes"]["sub"] = "invalid"
    errors = validate(invalid_mode_roster, load_schema("roster.schema.json"))
    assert any("judge.modes.sub" in error for error in errors)


@pytest.mark.parametrize("revision", ["v0.2.2", "earliest"])
def test_historical_rosters_validate_and_status_with_locality_default(tmp_path, capsys, revision):
    from ale.cli import main

    repo_root = os.path.dirname(os.path.dirname(__file__))
    ref = ("v0.2.2" if revision == "v0.2.2" else
           subprocess.check_output(
               ["git", "log", "--all", "--format=%H", "--", "examples/roster.json"],
               cwd=repo_root, text=True).splitlines()[-1])
    old_roster = subprocess.check_output(
        ["git", "show", "%s:examples/roster.json" % ref], cwd=repo_root, text=True)
    roster_path = tmp_path / (revision.replace(".", "_") + ".json")
    roster_path.write_text(old_roster)

    run_dir = tmp_path / (revision.replace(".", "_") + "-run")
    subprocess.run(["cp", "-R", os.path.join(repo_root, "examples", "run"), str(run_dir)], check=True)
    args = ["--run-dir", str(run_dir), "--roster", str(roster_path)]
    assert main(["validate", *args]) == 0
    capsys.readouterr()
    assert main(["status", *args]) == 0
    assert load_roster(str(roster_path))["vocab"]["locality"] == {
        "any": "No planner-machine-only resources are required.",
        "local": "Requires the planner's own machine or local-only resources.",
    }
    loaded = load_roster(str(roster_path))
    assert loaded["judge"]["modes"]["locality"] == "shadow"
    assert loaded["worktree_setup_defaults"] == []
    assert loaded["vocab"]["cross_sub"]
    assert loaded["vocab"]["phase"]
    assert loaded["vocab"]["sub"]


def test_roster_hash_normalizes_missing_locality(roster):
    with_locality = copy.deepcopy(roster)
    without_locality = copy.deepcopy(roster)
    without_locality["vocab"].pop("locality", None)
    assert roster_hash(with_locality) == roster_hash(without_locality)


def test_roster_schema_accepts_missing_locality(roster):
    from ale.validate import load_schema, validate

    without_locality = copy.deepcopy(roster)
    without_locality["vocab"].pop("locality", None)
    assert validate(without_locality, load_schema("roster.schema.json")) == []
