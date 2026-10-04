"""Plan parsing of bulleted file lines and letter-prefixed task ids."""

from ale.bake import bake, compile_plan, skeleton_label
from ale.planparse import parse_plan


TEMPLATE = """# Plan

### Task 1: Thing
**Files:**
- Create: `src/a.py`
- Modify: `src/b.py:10-20`
- Test: `tests/test_a.py`

Run: `python -m pytest tests/test_a.py`
"""


def _votes(roster):
    result = {"roster": roster}
    for field, value in (("role", "backend"), ("effort", "M"), ("risk", "low"),
                         ("model_tier", "standard")):
        result[field] = {"value": value, "by": "rule:0", "confidence": 1.0, "votes": []}
    return result


def test_writing_plans_template_bulleted_files_are_read():
    tasks = parse_plan(TEMPLATE)

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "T1"
    assert tasks[0]["files"] == ["src/a.py", "src/b.py", "tests/test_a.py"]


def test_star_bullets_indented_bullets_and_plain_files_line_are_read():
    text = ("## Task 1: One\n"
            "  * Create: `src/one.py`\n"
            "Files: `src/two.py`\n"
            "## Task 2: Two\n"
            "Modify: `src/three.py`\n")
    tasks = parse_plan(text)

    assert tasks[0]["files"] == ["src/one.py", "src/two.py"]
    assert tasks[1]["files"] == ["src/three.py"]


def test_bulleted_template_bakes_a_per_task_worktree(roster):
    tasks = parse_plan(TEMPLATE)
    labels = {task["task_id"]: skeleton_label(task, "run-1", _votes(roster)) for task in tasks}
    compiled = compile_plan(bake(TEMPLATE, labels))

    assert compiled["T1"]["context"]["allowed_paths"] == ["src/a.py", "src/b.py", "tests/test_a.py"]
    assert compiled["T1"]["context"]["worktree"]["mode"] not in (None, "none")


def test_numeric_heading_ids_keep_the_t_prefix():
    tasks = parse_plan("### Task 3: Three\nbody\n### Task 4b: Four\nbody\n")

    assert [task["task_id"] for task in tasks] == ["T3", "T4b"]


def test_letter_prefixed_heading_id_is_kept_as_written():
    tasks = parse_plan("### Task T2: Two\nbody\n### Task T3: Three\nAfter Task T2.\n")

    assert [task["task_id"] for task in tasks] == ["T2", "T3"]
    assert tasks[1]["depends_on"] == ["T2"]
