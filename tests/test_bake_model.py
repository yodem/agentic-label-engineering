"""The baked block shows its route (harness, model, mode); the roster owns the model."""

import json

import pytest

from ale.bake import compile_plan, extract_blocks
from ale.cli import main

PLAN = """# P

## Task 1: Add endpoint

**Files:**
- Modify: `app/api.py`

Run: `pytest -q`

## Task 2: Docs

**Files:**
- Modify: `docs/api.md`

Run: `true`
"""


CHEAP = "claude-haiku-4-5-20251001"


def _blocks(text):
    return {b["task_id"]: b for _, b in extract_blocks(text)}


@pytest.fixture
def roster_path(tmp_path, roster):
    # Both tasks label as the roster's first (cheap) tier, so they route through routing[0].
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster))
    return path


@pytest.fixture
def edit_roster(roster_path):
    def edit(change):
        data = json.loads(roster_path.read_text())
        change(data)
        roster_path.write_text(json.dumps(data))
    return edit


@pytest.fixture
def bake_plan(tmp_path, roster_path):
    plan = tmp_path / "plan.md"

    def run(text):
        plan.write_text(text)
        # Exit 1 only reports planning gaps (no lane yet); the plan is still written.
        assert main(["plan", "bake", str(plan), "--no-judge", "--write",
                     "--roster", str(roster_path)]) in (0, 1)
        return plan.read_text()
    return run


def test_bake_writes_resolved_model(roster_path, bake_plan):
    text = bake_plan(PLAN)
    route = _blocks(text)["T1"]["route"]
    assert route["harness"] == "claude" and route["model"] == CHEAP
    assert route["mode"] is None  # no lane yet


def test_pinned_model_survives_rebake_and_roster_change(roster_path, bake_plan, edit_roster):
    text = bake_plan(PLAN)
    text = text.replace('"executor":null', '"executor":"codex","model":"gpt-6-luna","pin_reason":"long context"', 1)
    edit_roster(lambda r: r["routing"].__setitem__(0, dict(r["routing"][0], model="claude-haiku-5")))
    blocks = _blocks(bake_plan(text))
    assert blocks["T1"]["route"] == {"harness": "codex", "model": "gpt-6-luna", "mode": None}
    assert blocks["T1"]["assignments"][0]["pin_reason"] == "long context"
    assert blocks["T2"]["route"]["model"] == "claude-haiku-5"


def test_unpinned_model_follows_roster_on_rebake(roster_path, bake_plan, edit_roster):
    bake_plan(PLAN)
    edit_roster(lambda r: r["routing"].__setitem__(0, dict(r["routing"][0], model="claude-haiku-5")))
    text = bake_plan((roster_path.parent / "plan.md").read_text())
    assert _blocks(text)["T1"]["route"]["model"] == "claude-haiku-5"


def test_hand_edited_route_is_recomputed(roster_path, bake_plan):
    text = bake_plan(PLAN).replace('"%s"' % CHEAP, '"made-up"', 1)
    assert '"made-up"' in text
    assert _blocks(bake_plan(text))["T1"]["route"]["model"] == CHEAP


def test_route_mode_appears_once_a_lane_exists(roster_path, bake_plan, edit_roster):
    text = bake_plan(PLAN).replace('"lane":null', '"lane":"pane"', 1)
    # The roster's legacy id claude-headless fixes the mode, whatever the lane says.
    assert _blocks(bake_plan(text))["T1"]["route"] == {
        "harness": "claude", "model": CHEAP, "mode": "headless"}
    # A bare harness name lets the lane decide.
    edit_roster(lambda r: r["routing"].__setitem__(0, dict(r["routing"][0], executor="claude")))
    assert _blocks(bake_plan(text))["T1"]["route"]["mode"] == "pane"


def test_rebake_is_idempotent(roster_path, bake_plan):
    text = bake_plan(PLAN)
    assert bake_plan(text) == text


def test_compile_with_roster_fills_routing_and_without_keeps_null(roster_path, bake_plan):
    from ale.roster import load_roster
    text = bake_plan(PLAN)
    assert compile_plan(text)["T1"]["routing"] == {
        "executor": None, "model": None, "resolved_from": None, "agent": None}
    routing = compile_plan(text, roster=load_roster(str(roster_path)))["T1"]["routing"]
    assert routing == {"executor": "claude", "model": CHEAP, "mode": "headless",
                       "host": "local", "resolved_from": "roster", "agent": None}


def test_old_block_without_route_or_pin_reason_still_compiles():
    old = {"task_id": "T1", "title": "Old", "labels": {"role": "backend", "lane": "inline"},
           "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                            "executor": "codex-exec", "trigger": "ready"}]}
    plan = "## Task 1: Old\n```ale-label\n%s\n```\n" % json.dumps(old)
    assert compile_plan(plan)["T1"]["assignments"] == old["assignments"]


def test_pin_without_reason_fails(roster, label_t01):
    from ale.labelset import check_label
    label_t01["assignments"] = [dict(kind="executor", role=label_t01["labels"]["role"],
                                     model_tier=label_t01["labels"]["model_tier"], executor="codex",
                                     model="gpt-6-luna", trigger="ready")]
    assert any("pinned model needs pin_reason" in e for e in check_label(label_t01, roster))
    label_t01["assignments"][0]["pin_reason"] = "  "
    assert any("pinned model needs pin_reason" in e for e in check_label(label_t01, roster))
    label_t01["assignments"][0]["pin_reason"] = "needs the long-context model"
    assert check_label(label_t01, roster) == []
