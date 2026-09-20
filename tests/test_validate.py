import pytest

from ale.validate import load_schema, validate


def test_example_label_is_valid(label_t01):
    assert validate(label_t01, load_schema("label.schema.json")) == []


def test_example_roster_is_valid(roster):
    assert validate(roster, load_schema("roster.schema.json")) == []


def test_missing_lane_is_reported_with_path(label_t01):
    del label_t01["labels"]["lane"]
    errs = validate(label_t01, load_schema("label.schema.json"))
    assert any("$.labels" in e and "lane" in e for e in errs)


def test_unknown_lane_rejected(label_t01):
    label_t01["labels"]["lane"] = "cloud"
    assert validate(label_t01, load_schema("label.schema.json")) != []


def test_short_lane_reason_rejected(label_t01):
    label_t01["provenance"]["lane_reason"] = "pane"
    assert validate(label_t01, load_schema("label.schema.json")) != []


def test_one_acceptance_entry_rejected(label_t01):
    label_t01["acceptance"] = label_t01["acceptance"][:1]
    assert validate(label_t01, load_schema("label.schema.json")) != []


def test_manual_acceptance_entry_allowed(label_t01):
    label_t01["acceptance"][1] = {"id": "A2", "manual": "Reviewer confirms the API shape"}
    assert validate(label_t01, load_schema("label.schema.json")) == []


def test_additional_property_rejected(label_t01):
    label_t01["labels"]["colour"] = "blue"
    assert validate(label_t01, load_schema("label.schema.json")) != []


def test_bool_is_not_an_integer():
    assert validate(True, {"type": "integer"}) != []


def test_null_allowed_in_type_list():
    assert validate(None, {"type": ["string", "null"]}) == []


def test_unsupported_keyword_raises():
    with pytest.raises(ValueError):
        validate({}, {"type": "object", "patternProperties": {}})


def test_pattern_dollar_does_not_accept_trailing_newline(label_t01):
    label_t01["task_id"] = "T01\n"
    assert validate(label_t01, load_schema("label.schema.json")) != []
    assert validate("exit0\n", {"type": "string", "pattern": "^exit(0|:[0-9]{1,3})$"}) != []
    assert validate("exit0", {"type": "string", "pattern": "^exit(0|:[0-9]{1,3})$"}) == []
