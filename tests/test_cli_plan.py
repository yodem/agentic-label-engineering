import copy
import hashlib
import json
import os
import shutil
import subprocess

from ale.bake import bake, skeleton_label
from ale.cli import main
from ale.events import read_events
from ale.planparse import parse_plan


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "tests", "fixtures", "plans", "superpowers.md")


def _roster(tmp_path, judge_command=None):
    path = tmp_path / "roster.json"
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    if judge_command is not None:
        roster["judge"]["command"] = judge_command
    path.write_text(json.dumps(roster))
    return str(path)


def _valid_plan(tmp_path, roster_path):
    source = (tmp_path / "plan.md")
    source.write_text(open(PLAN, encoding="utf-8").read())
    with open(roster_path, encoding="utf-8") as handle:
        roster = json.load(handle)
    labels = {}
    for task in parse_plan(source.read_text()):
        votes = {"roster": roster}
        for field, value in (("role", "backend"), ("model_tier", "standard"),
                             ("risk", "low"), ("effort", "M")):
            votes[field] = {"value": value, "by": "planner", "confidence": None, "votes": []}
        label = skeleton_label(task, "plan-run", votes)
        label["labels"]["lane"] = "inline"
        label["provenance"]["lane_reason"] = "The human remains available for this task."
        label["acceptance"].append({"id": "A2", "cmd": "true", "expect": "exit0"})
        labels[task["task_id"]] = label
    source.write_text(bake(source.read_text(), labels))
    return source


def test_plan_parse_json_prints_skeletons(tmp_path, capsys):
    assert main(["plan", "parse", PLAN, "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert [task["task_id"] for task in parsed] == ["T1", "T2", "T3"]


def test_bake_diff_mode_writes_nothing_and_reports_gaps(tmp_path, capsys):
    plan = tmp_path / "plan.md"
    original = open(PLAN, encoding="utf-8").read()
    plan.write_text(original)
    roster = _roster(tmp_path)

    assert main(["plan", "bake", str(plan), "--no-judge", "--roster", roster]) == 1
    assert plan.read_text() == original
    output = capsys.readouterr().out
    assert "---" in output and "lane" in output


def test_bake_write_is_idempotent(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    first = plan.read_text()
    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    assert plan.read_text() == first


def test_compile_refuses_gaps_without_partial_labels(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)
    run_dir = tmp_path / "run"

    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", roster]) == 1
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 1
    assert not (run_dir / "labels").exists()


def test_compile_writes_all_labels_after_checks(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 0
    assert sorted(path.name for path in (run_dir / "labels").glob("*.json")) == ["T1.json", "T2.json", "T3.json"]


def test_init_run_plan_records_plan_hash(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"

    assert main(["init-run", "--plan", str(plan), "--run-dir", str(run_dir),
                 "--roster", roster, "--now", "7"]) == 0
    started = read_events(str(run_dir / "events.jsonl"))[0]
    assert started["type"] == "run_started"
    assert started["plan_sha256"] == hashlib.sha256(plan.read_bytes()).hexdigest()


def test_shadow_file_is_allowed_only_when_file_is_ignored(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    plan = repo / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path, ["false"])

    assert main(["plan", "bake", str(plan), "--roster", roster]) == 1
    assert "git-ignored" in capsys.readouterr().err
    assert not (repo / "plan.md.ale-shadow.jsonl").exists()

    (repo / ".gitignore").write_text("*.ale-shadow.jsonl\n")
    assert main(["plan", "bake", str(plan), "--roster", roster]) == 1
    assert (repo / "plan.md.ale-shadow.jsonl").exists()


def test_compile_preserves_existing_plan_labels(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    before = plan.read_bytes()
    run_dir = tmp_path / "run"

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 0
    assert plan.read_bytes() == before


def test_plan_compile_rejects_unsafe_run_id(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    run_dir = tmp_path / "run"
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "../unsafe",
                 "--roster", roster]) == 2
    assert not (run_dir / "labels").exists()
    plan.write_text(plan.read_text().replace('"task_id": "T1"',
                                             '"run_id": "../unsafe",\n "task_id": "T1"', 1))
    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "safe-run",
                 "--roster", roster]) == 1


def test_bake_accepts_run_id(tmp_path):
    roster = _roster(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    for command, run_dir in (("plan compile", tmp_path / "compile"), ("init-run", tmp_path / "init")):
        args = (["plan", "compile", str(plan), "--run-dir", str(run_dir), "--run-id", "custom-run",
                 "--roster", roster] if command == "plan compile" else
                ["init-run", "--plan", str(plan), "--run-dir", str(run_dir), "--run-id", "custom-run",
                 "--roster", roster, "--now", "7"])
        assert main(args) == 0
        assert {json.loads(path.read_text())["run_id"] for path in (run_dir / "labels").glob("*.json")} == {"custom-run"}


def test_plan_compile_failure_does_not_replace_existing_label_directory(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(open(PLAN, encoding="utf-8").read())
    roster = _roster(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir / "labels").mkdir(parents=True)
    marker = run_dir / "labels" / "keep.json"
    marker.write_text("keep")

    assert main(["plan", "compile", str(plan), "--run-dir", str(run_dir), "--roster", roster]) == 1
    assert marker.read_text() == "keep"
