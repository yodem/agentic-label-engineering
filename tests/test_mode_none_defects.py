"""Mode-``none`` defects (ALE 0.4.2): a bake-derived ``none`` on a task that has
``allowed_paths`` is recomputed to ``per_task`` at bake and init-run, a ``none``/``shared``
dependency never holds its dependent, ``ale integrate`` records a no-op for such a task,
``ale analyze`` does not count that no-op as a write, and the owner's re-claim is idempotent.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from ale import analyze as A
from ale.bake import extract_blocks
from ale.cli import main
from ale.dispatch import held_for_integration, select_assignments
from ale.events import make_event, read_events

from analyze_fixtures import entry, ev, evidence, label as analyze_label, run_header, write_run

ROOT = Path(__file__).resolve().parents[1]
WARNING = ("warning: T1: allowed_paths set but worktree.mode is none (derived at an earlier bake); "
           "baking per_task. Set worktree_reason to keep none.")
T0 = 1_700_000_000.0


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text((ROOT / "examples" / "roster.json").read_text())
    return str(path)


def _block(worktree="absent", complete=True, phase="absent"):
    labels = {"role": "backend", "model_tier": "standard", "lane": "inline",
              "risk": "low", "effort": "S", "locality": "any"}
    if phase != "absent":
        labels["phase"] = phase
    block = {"task_id": "T1", "title": "Write the thing", "labels": labels,
             "lane_reason": "small and bounded",
             "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"},
                            {"id": "A2", "cmd": "true", "expect": "exit0"}],
             "allowed_paths": ["src/thing.py"], "depends_on": []}
    if worktree != "absent":
        block["worktree"] = worktree
    if complete:
        block["assignments"] = [{"kind": "executor", "role": "backend", "model_tier": "standard",
                                 "executor": None, "trigger": "ready"}]
    return block


def _plan(tmp_path, block):
    # No Files:/Create: lines: planparse finds no paths in the body, only the block has them.
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n\n## Task 1: Write the thing\nChange the module.\n\n```ale-label\n%s\n```\n"
                    % json.dumps(block, indent=1))
    return plan


def _baked_block(plan):
    blocks = [block for _, block in extract_blocks(plan.read_text())]
    assert len(blocks) == 1
    return blocks[0]


def _bake(plan, roster, *extra):
    return main(["plan", "bake", str(plan), "--no-judge", "--roster", roster, *extra])


# (a) ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("worktree", ["none", {"mode": "none"}])
@pytest.mark.parametrize("complete", [True, False], ids=["complete-block", "partial-block"])
def test_a_bake_write_recomputes_a_derived_none_with_a_warning(tmp_path, capsys, worktree, complete):
    roster = _roster(tmp_path)
    plan = _plan(tmp_path, _block(worktree=worktree, complete=complete, phase=None))

    assert _bake(plan, roster, "--write") == 0
    err = capsys.readouterr().err
    assert WARNING in err
    block = _baked_block(plan)
    assert block["worktree"] == "per_task"
    assert block["labels"]["phase"] == "implement"
    # Baked again: per_task now, so no further warning.
    assert _bake(plan, roster, "--write") == 0
    assert WARNING not in capsys.readouterr().err
    assert _baked_block(plan)["worktree"] == "per_task"


def test_a_bake_without_write_warns_and_shows_per_task(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _plan(tmp_path, _block(worktree="none"))
    before = plan.read_bytes()

    _bake(plan, roster)
    out, err = capsys.readouterr()
    assert WARNING in err
    assert '+ "worktree": "per_task"' in out
    assert plan.read_bytes() == before


# (b) ------------------------------------------------------------------------------------------

def test_b_bake_keeps_a_deliberate_none_and_its_reason(tmp_path, capsys):
    roster = _roster(tmp_path)
    deliberate = {"mode": "none", "worktree_reason": "reads the logs; writes nothing"}
    plan = _plan(tmp_path, _block(worktree=deliberate))

    assert _bake(plan, roster, "--write") == 0
    assert _bake(plan, roster, "--write") == 0
    err = capsys.readouterr().err
    assert "allowed_paths set but worktree.mode is none" not in err
    assert _baked_block(plan)["worktree"] == deliberate


# (c) ------------------------------------------------------------------------------------------

def test_c_init_run_recomputes_a_derived_none_and_leaves_the_plan_bytes(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _plan(tmp_path, _block(worktree="none"))
    before = plan.read_bytes()
    run_dir = tmp_path / "run"

    assert main(["init-run", "--plan", str(plan), "--run-dir", str(run_dir),
                 "--roster", roster, "--now", "7"]) == 0
    assert WARNING in capsys.readouterr().err
    assert plan.read_bytes() == before
    compiled = json.loads((run_dir / "labels" / "T1.json").read_text())
    assert compiled["context"]["worktree"]["mode"] == "per_task"
    decisions = (run_dir / "decisions.md").read_text()
    assert decisions.startswith("# Decisions for run ")
    assert any(line.startswith("- [") and "T1" in line and "per_task" in line
               for line in decisions.splitlines())


def test_c_init_run_keeps_a_deliberate_none(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _plan(tmp_path, _block(worktree={"mode": "none", "worktree_reason": "think only"}))
    run_dir = tmp_path / "run"

    assert main(["init-run", "--plan", str(plan), "--run-dir", str(run_dir),
                 "--roster", roster, "--now", "7"]) == 0
    assert "allowed_paths set but worktree.mode is none" not in capsys.readouterr().err
    compiled = json.loads((run_dir / "labels" / "T1.json").read_text())
    assert compiled["context"]["worktree"]["mode"] == "none"
    assert "T1" not in (run_dir / "decisions.md").read_text()


# (d) ------------------------------------------------------------------------------------------

def _label(task_id, mode="per_task", depends=None):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id, "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [],
                    "allowed_paths": [task_id.lower() + ".txt"], "depends_on": depends or [],
                    "worktree": {"mode": mode, "branch": None, "base": None,
                                 "worktree_reason": "think only" if mode != "per_task" else None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }


def _two_tasks(mode):
    return {"T1": _label("T1", mode), "T2": _label("T2", depends=["T1"])}


def _accepted_state():
    return {"tasks": {"T1": {"state": "accepted", "integrated": False},
                      "T2": {"state": "ready", "attempt": 1}}}


@pytest.mark.parametrize("mode", ["none", "shared"])
def test_d_a_none_or_shared_dependency_never_holds_its_dependent(mode):
    labels = _two_tasks(mode)
    roster = json.loads((ROOT / "examples" / "roster.json").read_text())

    assert held_for_integration(_accepted_state(), labels) == []
    chosen, _cut = select_assignments(_accepted_state(), labels, roster)
    assert [item["task_id"] for item in chosen] == ["T2"]


def test_d_a_per_task_dependency_still_holds_until_integrated():
    labels = _two_tasks("per_task")
    roster = json.loads((ROOT / "examples" / "roster.json").read_text())

    assert held_for_integration(_accepted_state(), labels) == [("T2", "T1")]
    assert select_assignments(_accepted_state(), labels, roster)[0] == []


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "test-user"], ["config", "user.name", "Test"]):
        subprocess.run(["git"] + args, cwd=str(repo), check=True, capture_output=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(repo), check=True, capture_output=True)
    return repo


def _run_with_accepted_t1(tmp_path, mode):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    for task_id, item in _two_tasks(mode).items():
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(item))
    with (run / "events.jsonl").open("a") as handle:
        for event in [
            make_event("claimed", "run-1", 2, "T1", "lead", 1),
            make_event("submitted", "run-1", 3, "T1", "lead", 1, summary="done"),
            make_event("accepted", "run-1", 4, "T1", None, 1,
                       evidence={"passed": True, "files": [], "results": []}),
        ]:
            handle.write(json.dumps(event) + "\n")
    return run


@pytest.mark.parametrize("mode", ["none", "shared"])
def test_d_dispatch_no_exec_dispatches_the_dependent(tmp_path, capsys, mode):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run_with_accepted_t1(tmp_path, mode)

    assert main(["dispatch", "--no-exec", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert "holding T2" not in capsys.readouterr().err
    assert any(event["type"] == "spawned" and event.get("task_id") == "T2"
               for event in read_events(str(run / "events.jsonl")))


# (e) ------------------------------------------------------------------------------------------

def test_e_integrate_of_a_none_task_records_a_noop(tmp_path, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run_with_accepted_t1(tmp_path, "none")
    args = ["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster, "--cwd", str(repo)]

    assert main(args) == 0
    integrated = [event for event in read_events(str(run / "events.jsonl"))
                  if event["type"] == "integrated"]
    assert len(integrated) == 1
    assert integrated[0]["task_id"] == "T1"
    assert integrated[0]["commit"] is None and integrated[0]["noop"] is True
    capsys.readouterr()

    assert main(args) == 1
    assert "already integrated" in capsys.readouterr().err
    assert sum(event["type"] == "integrated" for event in read_events(str(run / "events.jsonl"))) == 1


# (f) ------------------------------------------------------------------------------------------

def test_f_analyze_scores_a_noop_integrated_none_task_na_for_write_has_worktree(tmp_path):
    events = run_header("r", T0, ["T1"]) + [
        ev("claimed", "r", T0 + 10, "T1", "lead"),
        ev("submitted", "r", T0 + 20, "T1", "lead", summary="checked"),
        ev("verified", "r", T0 + 30, "T1", evidence=evidence([])),
        ev("accepted", "r", T0 + 30, "T1", evidence=evidence([])),
        ev("integrated", "r", T0 + 40, "T1", commit=None, noop=True, files=[]),
    ]
    run_dir = write_run(tmp_path, "r", [analyze_label("T1", "r", worktree_mode="none")], events)
    run = A.load_run(entry(run_dir, "r"))
    report = A.evaluate([run], A.load_thresholds(), T0 + 3600, None)

    assert [row for row in report["case_results"]
            if row["evaluator"] == "ale.task.write_has_worktree"] == []


# (g) ------------------------------------------------------------------------------------------

def test_g_block_paths_without_a_worktree_key_bake_per_task_and_implement(tmp_path, capsys):
    roster = _roster(tmp_path)
    plan = _plan(tmp_path, _block(worktree="absent"))

    assert _bake(plan, roster, "--write") == 0
    assert "warning:" not in capsys.readouterr().err  # silent: there was no none to recompute
    block = _baked_block(plan)
    assert block["worktree"] == "per_task"
    assert block["labels"]["phase"] == "implement"


# (h) ------------------------------------------------------------------------------------------

def test_h_the_owner_reclaiming_is_idempotent(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps(_label("T1", "none")))
    claim = ["claim", "--task", "T1", "--run-dir", str(run), "--roster", roster, "--agent"]

    assert main(claim + ["agent-a"]) == 0
    capsys.readouterr()
    assert main(claim + ["agent-a"]) == 0
    assert "already claimed by agent-a" in capsys.readouterr().err
    claims = [event for event in read_events(str(run / "events.jsonl")) if event["type"] == "claimed"]
    assert len(claims) == 1

    assert main(claim + ["agent-b"]) == 3
    assert "claim lost" in capsys.readouterr().err
