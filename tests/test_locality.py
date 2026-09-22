import copy
import json

from ale import bake
from ale.labeling import cascade
from ale.labelset import check_label
from ale.planparse import parse_plan
from ale.validate import load_schema, validate


def _votes(roster, **values):
    result = {"roster": roster}
    for field, value in values.items():
        result[field] = {"value": value, "by": "rule:0", "confidence": 1.0, "votes": []}
    return result


def _task():
    return {"task_id": "T1", "title": "Keychain", "body": "", "files": [],
            "commands": [], "depends_on": [], "line": 1}


def test_skeleton_defaults_locality_any(roster):
    label = bake.skeleton_label(_task(), "run-1", _votes(
        roster, role="backend", effort="M", risk="low", model_tier="standard"))
    assert label["labels"]["locality"] == "any"


def test_parse_marks_keychain_paths_local():
    text = ("### Task 1: touch keychain\n**Files:** Modify `~/Library/Keychains/login.keychain-db`\n"
            "### Task 2: unrelated\n**Files:** Modify `src/main.py`\n")
    tasks = parse_plan(text)
    assert tasks[0]["locality"] == "local"
    assert tasks[1]["locality"] == "any"


def test_roster_without_locality_vocab_is_accepted_and_filled(roster):
    roster["vocab"].pop("locality", None)
    assert validate(roster, load_schema("roster.schema.json")) == []


def test_cascade_fields_include_locality():
    assert "locality" in cascade.FIELDS


def test_label_reader_defaults_missing_locality_to_any(roster):
    draft = bake.skeleton_label(_task(), "run-1", _votes(
        roster, role="backend", effort="M", risk="low", model_tier="standard"))
    draft["labels"].pop("locality")
    final, _ = cascade.label_task(draft, "", roster)
    assert final["labels"]["locality"] == "any"


def test_compact_block_round_trips_locality(label_t01):
    label = copy.deepcopy(label_t01)
    label["labels"]["locality"] = "local"
    text = bake.render_block(label)
    (_, compact), = bake.extract_blocks(text)
    assert compact["labels"]["locality"] == "local"


def test_compile_defaults_missing_locality(label_t01):
    def block(task_id, title):
        compact = {"task_id": task_id, "title": title,
                   "labels": {"role": "backend", "model_tier": "standard", "risk": "low",
                              "effort": "M", "lane": "inline"}}
        return "```ale-label\n%s\n```\n" % json.dumps(compact)

    text = "## Task 1: Model\n" + block("T1", "Model") + "## Task 2: Screen\n" + block("T2", "Screen")
    compiled = bake.compile_plan(text)
    assert compiled["T1"]["labels"]["locality"] == "any"


def test_legacy_compiled_label_still_validates(roster, label_t01):
    legacy = copy.deepcopy(label_t01)
    legacy["labels"].pop("locality", None)
    roster["vocab"]["locality"] = {"any": "Any machine", "local": "Planner machine"}
    assert check_label(legacy, roster) == []
