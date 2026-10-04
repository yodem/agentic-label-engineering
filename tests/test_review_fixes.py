"""Regression tests for the PR #1 review fixes (one section per review item)."""

from __future__ import annotations

import os

from ale import analyze as A
from ale.planparse import parse_plan

from analyze_fixtures import ev, entry, evidence, good_task, label, run_dir_for, run_header, write_run, HOUR


# Item 1: a fenced file line is not a task file.
FENCED_FILE_PLAN = """# Plan

## Task 1: Template

Write the template.

## Task 2: Document the plan format

- Modify: `docs/plans.md`

Example of a task block:

```markdown
- Create: `src/example.py`
```
"""


def test_item1_fenced_file_line_is_not_a_task_file():
    tasks = parse_plan(FENCED_FILE_PLAN)

    assert tasks[1]["task_id"] == "T2"
    assert tasks[1]["files"] == ["docs/plans.md"]


# Item 2: dependency ids accept the heading grammar (letter prefix, [a-z] suffix).
def test_item2_depends_on_accepts_a_letter_suffix():
    text = ("## Task P1a: First\nbody\n"
            "## Task P2: Second\nDepends on Task P1a.\n"
            "## Task 3: Third\nConsumes: output of Task P1a\n")
    tasks = parse_plan(text)

    assert tasks[1]["depends_on"] == ["P1a"]
    assert tasks[2]["depends_on"] == ["P1a"]


# --- analyze ---------------------------------------------------------------------------------

T0 = 1_700_000_000.0
DONE_NOW = T0 + 24 * HOUR


def _report(runs, now=DONE_NOW, since_s=None):
    return A.evaluate(runs, A.load_thresholds(), now, since_s)


def _judged_run(base, dir_name, t0, run_id="plan"):
    """A finished one-task run with a role shadow vote and its adjudication on T1."""
    run_dir = run_dir_for(base, dir_name)
    events = run_header(run_id, t0, ["T1"]) + good_task(run_id, "T1", t0 + 100, run_dir)
    events += [
        ev("shadow_vote", run_id, t0 + 1, "T1", decision="role", options=["backend", "frontend"],
           choice="backend", confidence=0.9, model="jev", latency_ms=30, uncertain=False,
           source="bake"),
        ev("adjudicated", run_id, t0 + 60, "T1", field="role", decision="role", value="backend",
           choice="backend", by="lead", authority="lead", additive=True),
    ]
    path = write_run(base, run_id, [label("T1", run_id)], events, dir_name=dir_name)
    return A.load_run(entry(path, run_id))


# Item 3: promotion cases are keyed by run key, not the raw run_id.
def test_item3_two_runs_with_the_same_run_id_stay_two_promotion_cases(tmp_path):
    first = _judged_run(tmp_path, "2026-10-01-a", T0)
    second = _judged_run(tmp_path, "2026-10-02-b", T0 + 1000)
    assert first["run_id"] == second["run_id"] == "plan"

    report = _report([first, second])

    assert report["promotions"]["role"]["cases"] == 2
