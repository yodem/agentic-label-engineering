import json
import os
import shutil

import pytest

from ale.cli import main
from ale.events import append_event, check_event, make_event
from ale.validate import load_schema, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_new_event_types_and_required_keys():
    ok = make_event("label_vote", "r", 1.0, "T01", None, 1, field="role", by="planner", value="backend")
    assert check_event(ok) == []
    assert check_event(make_event("label_vote", "r", 1.0, "T01", None, 1, field="role")) != []
    assert check_event(make_event("relabeled", "r", 1.0, "T01", None, 1, field="role", old="docs", new="backend", reason="spec grew")) == []
    assert check_event(make_event("adjudicated", "r", 1.0, "T01", None, 1, field="role", value="backend", by="human")) == []
    assert check_event(make_event("adjudicated", "r", 1.0, "T01", None, 1, field="role")) != []


def test_abstain_vote_value_may_be_null():
    ev = make_event("label_vote", "r", 1.0, "T01", None, 1, field="role", by="rule:0", value=None)
    assert check_event(ev) == []


def test_example_roster_valid_with_rules_and_judge_config(roster):
    assert validate(roster, load_schema("roster.schema.json")) == []
    assert isinstance(roster["rules"], list) and roster["rules"]
    assert set(roster["judge"]["questions"]) == {"role", "model_tier", "risk", "effort"}
    assert "lane" not in roster["judge"]["questions"] and "lane" not in roster["judge"]["modes"]
    assert set(roster["judge"]["modes"].values()) == {"shadow"}
    assert roster["judge"]["command"] == ["jev-ask"]


def test_rule_on_lane_is_a_schema_error(roster):
    roster["rules"].append({"field": "lane", "when": {"keyword": "x"}, "value": "pane"})
    assert validate(roster, load_schema("roster.schema.json")) != []


def test_rule_needs_exactly_the_known_shape(roster):
    roster["rules"].append({"field": "role", "value": "docs"})
    assert validate(roster, load_schema("roster.schema.json")) != []


def test_lane_in_judge_modes_is_a_schema_error(roster):
    roster["judge"]["modes"]["lane"] = "shadow"
    assert validate(roster, load_schema("roster.schema.json")) != []


def test_lane_in_judge_questions_is_a_schema_error(roster):
    roster["judge"]["questions"]["lane"] = "Which lane?"
    assert validate(roster, load_schema("roster.schema.json")) != []


def test_unknown_judge_mode_value_is_a_schema_error(roster):
    roster["judge"]["modes"]["role"] = "loud"
    assert validate(roster, load_schema("roster.schema.json")) != []


def test_example_roster_still_validates_after_schema_tightening():
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as f:
        example = json.load(f)
    assert validate(example, load_schema("roster.schema.json")) == []


def test_init_run_allowed_after_label_votes_but_not_twice(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run))
    roster = tmp_path / "roster.json"
    shutil.copy(os.path.join(ROOT, "examples", "roster.json"), str(roster))
    append_event(str(run / "events.jsonl"), make_event("label_vote", "example-run", 0.5, "T01", None, 1,
                                                       field="role", by="planner", value="backend"))
    args = ["--run-dir", str(run), "--roster", str(roster), "--now", "1"]
    assert main(["init-run"] + args) == 0
    assert main(["init-run"] + args) == 1
