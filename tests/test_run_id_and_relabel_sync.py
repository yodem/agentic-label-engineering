"""Run id from the run dir, and ``ale relabel`` rewriting the task's plan block."""
from __future__ import annotations

import json
import os

import pytest

from ale import events as E
from ale.bake import bake, extract_blocks, skeleton_label
from ale.cli import _plan_run_id, main
from ale.planparse import parse_plan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "tests", "fixtures", "plans", "superpowers.md")
EXAMPLE_RUN = os.path.join(ROOT, "examples", "run")


# --- run id from the run dir ------------------------------------------------------------------

def _run_plan(tmp_path, run_id="2026-10-05-x", text="# P\n"):
    directory = tmp_path / ".ale" / "runs" / run_id
    directory.mkdir(parents=True)
    plan = directory / "plan.md"
    plan.write_text(text)
    return str(plan)


def test_plan_in_a_run_dir_takes_the_run_dir_name(tmp_path):
    assert _plan_run_id(_run_plan(tmp_path), None) == "2026-10-05-x"


def test_other_plan_paths_keep_the_plan_basename(tmp_path):
    docs = tmp_path / "docs" / "plans"
    docs.mkdir(parents=True)
    (docs / "foo.md").write_text("# P\n")
    assert _plan_run_id(str(docs / "foo.md"), None) == "foo"
    # `runs` without an `.ale` grandparent is not a run dir
    stray = tmp_path / "other" / "runs" / "abc"
    stray.mkdir(parents=True)
    (stray / "plan.md").write_text("# P\n")
    assert _plan_run_id(str(stray / "plan.md"), None) == "plan"


def test_a_relative_plan_path_in_a_run_dir_resolves_too(tmp_path, monkeypatch):
    _run_plan(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _plan_run_id(os.path.join(".ale", "runs", "2026-10-05-x", "plan.md"), None) == "2026-10-05-x"


def test_supplied_run_id_wins(tmp_path):
    assert _plan_run_id(_run_plan(tmp_path), "chosen") == "chosen"


def test_an_existing_sidecar_wins_over_the_run_dir(tmp_path):
    plan = _run_plan(tmp_path)
    with open(plan + ".ale-provenance.json", "w", encoding="utf-8") as handle:
        json.dump({"run_id": "plan", "tasks": {}}, handle)
    assert _plan_run_id(plan, None) == "plan"


def test_a_sidecar_without_a_run_id_falls_back_to_the_run_dir(tmp_path):
    plan = _run_plan(tmp_path)
    with open(plan + ".ale-provenance.json", "w", encoding="utf-8") as handle:
        json.dump({"tasks": {}}, handle)
    assert _plan_run_id(plan, None) == "2026-10-05-x"


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        path.write_text(json.dumps(json.load(handle)))
    return str(path)


def test_plan_bake_write_records_the_run_dir_name_as_run_id(tmp_path):
    plan = _run_plan(tmp_path, text=open(PLAN, encoding="utf-8").read())
    main(["plan", "bake", plan, "--no-judge", "--write", "--roster", _roster(tmp_path)])
    with open(plan + ".ale-provenance.json", encoding="utf-8") as handle:
        assert json.load(handle)["run_id"] == "2026-10-05-x"


def test_plan_bake_run_id_flag_wins_over_the_run_dir(tmp_path):
    plan = _run_plan(tmp_path, text=open(PLAN, encoding="utf-8").read())
    main(["plan", "bake", plan, "--no-judge", "--write", "--run-id", "mine", "--roster", _roster(tmp_path)])
    with open(plan + ".ale-provenance.json", encoding="utf-8") as handle:
        assert json.load(handle)["run_id"] == "mine"


# --- a baked plan and a run initialised from it ----------------------------------------------

def _baked_text(roster_path, source_text=None):
    source = source_text if source_text is not None else open(PLAN, encoding="utf-8").read()
    with open(roster_path, encoding="utf-8") as handle:
        roster = json.load(handle)
    labels = {}
    for task in parse_plan(source):
        votes = {"roster": roster}
        for field, value in (("role", "backend"), ("model_tier", "standard"),
                             ("risk", "low"), ("effort", "M")):
            votes[field] = {"value": value, "by": "planner", "confidence": None, "votes": []}
        label = skeleton_label(task, "plan-run", votes)
        label["labels"]["lane"] = "inline"
        label["provenance"]["lane_reason"] = "The human remains available for this task."
        label["acceptance"].append({"id": "A2", "cmd": "true", "expect": "exit0"})
        labels[task["task_id"]] = label
    return bake(source, labels)


def _setup(tmp_path, run_id="2026-10-05-x", text=None, init=True):
    roster = _roster(tmp_path)
    plan = _run_plan(tmp_path, run_id, _baked_text(roster, text))
    run_dir = str(tmp_path / ".ale" / "runs" / run_id)
    if init:
        assert main(["init-run", "--plan", plan, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    return plan, run_dir, roster


def _events(run_dir, kind):
    return [e for e in E.read_events(os.path.join(run_dir, "events.jsonl")) if e["type"] == kind]


def _read(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8")


def _blocks(plan):
    return {block["task_id"]: block for _, block in extract_blocks(_read(plan))}


def _relabel(run_dir, roster, field, value, task="T1", *extra):
    return main(["relabel", "--task", task, "--field", field, "--value", value, "--reason", "spec grew",
                 "--run-dir", run_dir, "--roster", roster, "--now", "5"] + list(extra))


def _last_note(run_dir):
    return [e for e in _events(run_dir, "note") if e.get("task_id") == "T1"][-1]["text"]


def _block_span(text, task):
    """The (start, end) offsets of the task's block in ``text``."""
    opening = text.index('"task_id": "%s"' % task) if ('"task_id": "%s"' % task) in text else text.index(
        '"task_id":"%s"' % task)
    start = text.rindex("```ale-label", 0, opening)
    end = text.index("\n```", opening) + len("\n```")
    return start, end


def test_init_run_plan_records_the_absolute_plan_path_and_run_id_source(tmp_path, monkeypatch):
    roster = _roster(tmp_path)
    plan = _run_plan(tmp_path, "2026-10-05-x", _baked_text(roster))
    run_dir = str(tmp_path / ".ale" / "runs" / "2026-10-05-x")
    monkeypatch.chdir(tmp_path)
    relative = os.path.relpath(plan)
    assert main(["init-run", "--plan", relative, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    started = _events(run_dir, "run_started")[0]
    assert started["plan_path"] == os.path.abspath(plan)
    assert os.path.isabs(started["plan_path"])
    assert started["run_id"] == "2026-10-05-x"
    assert started["run_id_from"] == "run-dir"


def test_init_run_run_id_from_keeps_its_other_values(tmp_path):
    roster = _roster(tmp_path)
    plan = tmp_path / "stem-plan.md"
    plan.write_text(_baked_text(roster))
    run_dir = str(tmp_path / "run")
    assert main(["init-run", "--plan", str(plan), "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    started = _events(run_dir, "run_started")[0]
    assert started["run_id_from"] == "plan-stem"
    assert started["run_id"] == "stem-plan"


# --- relabel rewrites the plan block ----------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("model_tier", "frontier"), ("risk", "high"), ("effort", "L"), ("locality", "local"), ("role", "docs"),
])
def test_relabel_label_field_rewrites_only_that_block(tmp_path, field, value):
    plan, run_dir, roster = _setup(tmp_path)
    before = _read(plan)
    old = _blocks(plan)["T1"]["labels"][field]
    assert old != value

    assert _relabel(run_dir, roster, field, value) == 0

    after = _read(plan)
    start, end = _block_span(before, "T1")
    new_start, new_end = _block_span(after, "T1")
    assert before[:start] == after[:new_start]           # everything before the block, byte for byte
    assert before[end:] == after[new_end:]               # everything after it too
    blocks = _blocks(plan)
    assert blocks["T1"]["labels"][field] == value
    expected = json.loads(json.dumps(extract_blocks(before)[0][1]))
    expected["labels"][field] = value
    changed = {key for key in set(expected) | set(blocks["T1"]) if expected.get(key) != blocks["T1"].get(key)}
    assert changed <= {"labels", "route"}                # the route view follows the labels
    for key in ("task_id", "title", "acceptance", "assignments", "allowed_paths", "lane_reason"):
        assert blocks["T1"][key] == expected[key]
    assert {k: v for k, v in blocks["T1"]["labels"].items() if k != field} == \
        {k: v for k, v in expected["labels"].items() if k != field}
    note = _last_note(run_dir)
    assert note == "relabel: plan block for T1 updated in %s; re-approve the plan" % plan
    assert _events(run_dir, "relabeled")[-1]["new"] == value


def _route(plan, roster, capsys, task="T1"):
    capsys.readouterr()
    assert main(["plan", "route", plan, "--task", task, "--roster", roster, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _frontier_models(roster):
    with open(roster, encoding="utf-8") as handle:
        return {row["model"] for row in json.load(handle)["routing"] if row["model_tier"] == "frontier"}


def test_plan_route_reads_the_new_assignments_after_a_synced_relabel(tmp_path, capsys):
    plan, run_dir, roster = _setup(tmp_path)
    stale = _route(plan, roster, capsys)
    assert stale["model"] not in _frontier_models(roster)
    new = [{"kind": "executor", "role": "backend", "model_tier": "frontier", "executor": None, "trigger": "ready"}]
    assert _relabel(run_dir, roster, "assignments", json.dumps(new), "T1", "--json") == 0
    fresh = _route(plan, roster, capsys)
    assert fresh["model"] in _frontier_models(roster)
    assert _route(plan, roster, capsys, "T2") == stale          # T2 keeps its route


def test_plan_route_reads_the_new_label_after_a_synced_relabel(tmp_path, capsys):
    """When the assignment leaves the tier to the labels, relabeling model_tier moves the route."""
    plan, run_dir, roster = _setup(tmp_path)
    bare = [{"kind": "executor", "executor": None, "trigger": "ready"}]
    assert _relabel(run_dir, roster, "assignments", json.dumps(bare), "T1", "--json") == 0
    stale = _route(plan, roster, capsys)
    assert stale["model"] not in _frontier_models(roster)
    assert _relabel(run_dir, roster, "model_tier", "frontier") == 0
    assert _route(plan, roster, capsys)["model"] in _frontier_models(roster)
    assert _blocks(plan)["T1"]["route"]["model"] in _frontier_models(roster)   # the block's route view follows


def test_relabel_assignments_rewrites_the_top_level_key(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    new = [{"kind": "executor", "role": "backend", "model_tier": "frontier", "executor": None, "trigger": "ready"}]
    assert _relabel(run_dir, roster, "assignments", json.dumps(new), "T1", "--json") == 0
    assert _blocks(plan)["T1"]["assignments"] == new
    assert _blocks(plan)["T2"]["assignments"] != new
    assert _last_note(run_dir).startswith("relabel: plan block for T1 updated in ")


def test_relabel_acceptance_rewrites_the_top_level_key(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    new = [{"id": "A1", "cmd": "true", "expect": "exit0"}, {"id": "A2", "cmd": "false", "expect": "exit:1"}]
    assert _relabel(run_dir, roster, "acceptance", json.dumps(new), "T1", "--json") == 0
    assert _blocks(plan)["T1"]["acceptance"] == new
    assert _last_note(run_dir).startswith("relabel: plan block for T1 updated in ")


def test_a_compact_hand_written_block_gets_only_the_labels_key_changed(tmp_path):
    roster = _roster(tmp_path)
    hand = {"task_id": "T1", "title": "Add the todo model",
            "labels": {"role": "backend", "model_tier": "standard", "risk": "low", "effort": "M", "lane": "inline"},
            "lane_reason": "Human stays", "allowed_paths": ["todo/model.py"], "worktree": "per_task",
            "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                             "executor": None, "trigger": "ready"}],
            "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                           {"id": "A2", "cmd": "true", "expect": "exit0"}]}
    text = ("# Plan\n\n## Task 1: Add the todo model\n\nCreate: `todo/model.py`\n\n```ale-label\n"
            + json.dumps(hand) + "\n```\n\nRun: `true`\n")
    plan = _run_plan(tmp_path, "2026-10-05-x", text)
    run_dir = str(tmp_path / ".ale" / "runs" / "2026-10-05-x")
    assert main(["init-run", "--plan", plan, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0

    assert _relabel(run_dir, roster, "model_tier", "frontier") == 0

    expected = json.loads(json.dumps(hand))
    expected["labels"]["model_tier"] = "frontier"
    block = _blocks(plan)["T1"]
    assert {k: v for k, v in block.items() if k != "route"} == expected
    after = _read(plan)
    assert after.startswith("# Plan\n\n## Task 1: Add the todo model\n\nCreate: `todo/model.py`\n\n```ale-label\n")
    assert after.endswith("\n```\n\nRun: `true`\n")


def test_crlf_plan_keeps_its_line_endings(tmp_path):
    roster = _roster(tmp_path)
    crlf = _baked_text(roster).replace("\n", "\r\n")
    plan, run_dir, roster = _setup(tmp_path, text=None, init=False)
    with open(plan, "wb") as handle:
        handle.write(crlf.encode("utf-8"))
    assert main(["init-run", "--plan", plan, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    assert _relabel(run_dir, roster, "risk", "high") == 0
    raw = open(plan, "rb").read()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert _blocks(plan)["T1"]["labels"]["risk"] == "high"


def test_a_plan_without_a_trailing_newline_is_kept(tmp_path):
    plan, run_dir, roster = _setup(tmp_path, init=False)
    text = _read(plan)
    start, end = _block_span(text, "T3")
    cut = text[:end]                      # T3's block closes the file, with no newline after it
    with open(plan, "wb") as handle:
        handle.write(cut.encode("utf-8"))
    assert main(["init-run", "--plan", plan, "--run-dir", run_dir, "--roster", roster, "--now", "1"]) == 0
    assert _relabel(run_dir, roster, "risk", "high", "T3") == 0
    after = _read(plan)
    assert not after.endswith("\n") and after.endswith("```")
    assert after[:start] == cut[:start]
    assert _blocks(plan)["T3"]["labels"]["risk"] == "high"


# --- relabel degrades to a note, never a failure ---------------------------------------------

def _assert_not_synced(plan, before, run_dir, roster, reason_fragment, field="model_tier", value="frontier"):
    assert _relabel(run_dir, roster, field, value) == 0
    assert _read(plan) == before if plan and os.path.exists(plan) else True
    note = _last_note(run_dir)
    assert "plan block for T1 not updated" in note
    assert reason_fragment in note
    # the relabel itself still happened
    assert _events(run_dir, "relabeled")[-1]["new"] == value
    with open(os.path.join(run_dir, "labels", "T1.json"), encoding="utf-8") as handle:
        assert json.load(handle)["labels"][field] == value


def test_no_plan_path_on_run_started_is_a_note(tmp_path):
    run_dir = str(tmp_path / "run")
    import shutil
    shutil.copytree(EXAMPLE_RUN, run_dir)
    roster = _roster(tmp_path)
    assert not any("plan_path" in e for e in _events(run_dir, "run_started"))
    assert main(["relabel", "--task", "T01", "--field", "risk", "--value", "high", "--reason", "x",
                 "--run-dir", run_dir, "--roster", roster, "--now", "5"]) == 0
    note = [e for e in _events(run_dir, "note") if e.get("task_id") == "T01"][-1]["text"]
    assert "plan block for T01 not updated" in note and "no plan_path" in note


def test_missing_plan_file_is_a_note(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    os.remove(plan)
    _assert_not_synced(None, None, run_dir, roster, "plan file missing")
    assert not os.path.exists(plan)


def test_missing_block_for_the_task_is_a_note(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    text = _read(plan)
    start, end = _block_span(text, "T1")
    with open(plan, "wb") as handle:
        handle.write((text[:start] + text[end:]).encode("utf-8"))
    _assert_not_synced(plan, _read(plan), run_dir, roster, "no block for T1")


def test_two_blocks_for_the_task_is_a_note(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    text = _read(plan)
    start, end = _block_span(text, "T1")
    with open(plan, "wb") as handle:
        handle.write((text[:end] + "\n\n" + text[start:end] + text[end:]).encode("utf-8"))
    _assert_not_synced(plan, _read(plan), run_dir, roster, "more than one block for T1")


def test_unparseable_plan_is_a_note(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    with open(plan, "ab") as handle:
        handle.write(b"\n```ale-label\n{not json\n```\n")
    _assert_not_synced(plan, _read(plan), run_dir, roster, "do not parse")


def test_a_hand_edited_value_that_differs_is_a_note(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    text = _read(plan)
    start, end = _block_span(text, "T1")
    block = text[start:end]
    assert '"model_tier":"standard"' in block
    edited = block.replace('"model_tier":"standard"', '"model_tier":"cheap"', 1)
    with open(plan, "wb") as handle:
        handle.write((text[:start] + edited + text[end:]).encode("utf-8"))
    _assert_not_synced(plan, _read(plan), run_dir, roster, "differs")


def test_relabeled_stays_the_last_event_and_stdout_stays_clean(tmp_path, capsys):
    plan, run_dir, roster = _setup(tmp_path)
    capsys.readouterr()
    assert _relabel(run_dir, roster, "risk", "high") == 0
    out, err = capsys.readouterr()
    assert out == ""                                             # stdout is not part of this feature
    assert "plan block for T1 updated" in err
    assert E.read_events(os.path.join(run_dir, "events.jsonl"))[-1]["type"] == "relabeled"


def test_an_unreadable_plan_is_a_note_and_the_relabel_still_exits_zero(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    with open(plan, "wb") as handle:
        handle.write(b"\xff\xfe not utf-8")
    assert _relabel(run_dir, roster, "model_tier", "frontier") == 0
    assert "plan block for T1 not updated" in _last_note(run_dir)
    assert _read_bytes(plan) == b"\xff\xfe not utf-8"


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def test_a_failed_relabel_does_not_touch_the_plan(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    before = _read_bytes(plan)
    assert _relabel(run_dir, roster, "model_tier", "wizard") == 1
    assert _read_bytes(plan) == before
    assert not [e for e in _events(run_dir, "note") if "plan block" in e.get("text", "")]


def test_the_plan_keeps_its_file_mode(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    os.chmod(plan, 0o644)
    assert _relabel(run_dir, roster, "risk", "high") == 0
    assert os.stat(plan).st_mode & 0o777 == 0o644


def test_a_different_task_with_the_same_field_value_is_never_touched(tmp_path):
    plan, run_dir, roster = _setup(tmp_path)
    before = _blocks(plan)
    assert _relabel(run_dir, roster, "effort", "L") == 0
    after = _blocks(plan)
    assert after["T2"] == before["T2"] and after["T3"] == before["T3"]


# --- the eval case ----------------------------------------------------------------------------

def _case():
    from ale import evalcases as EC
    cases = {c["id"]: c for c in EC.load_cases(EC.default_cases_path())}
    return EC, cases["bake-run-id-from-run-dir"]


def test_the_run_id_eval_case_passes():
    EC, case = _case()
    result = EC.run_case(case)
    assert result["passed"], result
    assert result["actual"]["run_id"] == "2026-10-05-demo"


def test_the_run_id_eval_case_fails_for_a_plan_outside_a_run_dir():
    EC, case = _case()
    case["input"] = dict(case["input"], plan_path="docs/plans/plan.md")
    result = EC.run_case(case)
    assert not result["passed"] and result["actual"]["run_id"] == "plan"
