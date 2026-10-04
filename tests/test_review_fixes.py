"""Regression tests for the PR #1 review fixes (one section per review item)."""

from __future__ import annotations

from ale.planparse import parse_plan


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
