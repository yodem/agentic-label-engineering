import os

from ale import labelset as L
from ale import roster as R
from ale.bake import compile_plan, extract_blocks
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROSTER = os.path.join(ROOT, "examples", "roster.json")


def _task(number, title, depends, path):
    lines = ["## Task %d: %s" % (number, title), ""]
    lines += ["Depends " + "on Task %d." % dependency for dependency in depends]
    if path:
        lines.append("**" + "Files:** `%s`" % path)
    lines.append("R" + "un: `true`")
    return "\n".join(lines) + "\n\n"


PLAN = "# Stack plan\n\n" + "".join([
    _task(1, "Base", [], "src/base.py"),
    _task(2, "Child", [1], "src/child.py"),
    _task(3, "Merge point", [1, 2], "src/merge.py"),
    _task(4, "Docs child", [1], None),
])


def _bake(tmp_path, *flags):
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    main(["plan", "bake", str(plan), "--write", "--no-judge", "--roster", ROSTER] + list(flags))
    return plan.read_text(encoding="utf-8")


def _worktrees(text):
    return {block["task_id"]: block.get("worktree") for _, block in extract_blocks(text)}


def test_bake_stack_marks_only_single_dependency_worktree_tasks(tmp_path):
    worktrees = _worktrees(_bake(tmp_path, "--stack"))
    assert worktrees["T1"] == "per_task"
    assert worktrees["T2"] == {"mode": "per_task", "stack": True}
    assert worktrees["T3"] == "per_task"
    assert worktrees["T4"] == "none"


def test_bake_without_stack_writes_no_stack_key(tmp_path):
    text = _bake(tmp_path)
    assert '"stack"' not in text
    assert _worktrees(text)["T2"] == "per_task"


def test_stacked_block_compiles_to_a_valid_label(tmp_path):
    text = _bake(tmp_path, "--stack")
    labels = compile_plan(text, "run-1")
    assert labels["T2"]["context"]["worktree"]["stack"] is True
    assert "stack" not in labels["T1"]["context"]["worktree"]
    roster = R.load_roster(ROSTER)
    schema_errors = [error for error in L.check_label(labels["T2"], roster) if "stack" in error]
    assert schema_errors == []


def test_rebaking_a_stacked_plan_is_idempotent(tmp_path):
    first = _bake(tmp_path, "--stack")
    plan = tmp_path / "plan.md"
    main(["plan", "bake", str(plan), "--write", "--no-judge", "--roster", ROSTER, "--stack"])
    assert plan.read_text(encoding="utf-8") == first
    main(["plan", "bake", str(plan), "--write", "--no-judge", "--roster", ROSTER])
    assert _worktrees(plan.read_text(encoding="utf-8"))["T2"] == {"mode": "per_task", "stack": True}
