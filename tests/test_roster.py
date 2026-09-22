import copy
import json
import os

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
