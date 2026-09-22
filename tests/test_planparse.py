from pathlib import Path

import pytest

from ale.planparse import PlanParseError, parse_plan


FIXTURES = Path(__file__).parent / "fixtures" / "plans"


def read_fixture(name):
    return (FIXTURES / name).read_text()


def test_superpowers_fixture_ids_titles_files_and_commands():
    tasks = parse_plan(read_fixture("superpowers.md"))

    assert [task["task_id"] for task in tasks] == ["T1", "T2", "T3"]
    assert [task["title"] for task in tasks] == [
        "Add the todo model",
        "Add the todo screen",
        "Document the app",
    ]
    assert tasks[0]["files"] == ["todo/model.py", "tests/test_model.py"]
    assert tasks[1]["files"] == ["todo/ui.py"]
    assert tasks[2]["files"] == ["docs/guide.md"]
    assert tasks[0]["commands"] == ["python -m pytest tests/test_model.py"]
    assert tasks[2]["commands"] == ["python -m pytest"]


def test_superpowers_fixture_dependencies_and_lines():
    tasks = parse_plan(read_fixture("superpowers.md"))

    assert tasks[0]["depends_on"] == []
    assert tasks[1]["depends_on"] == ["T1"]
    assert tasks[2]["depends_on"] == ["T2"]
    assert tasks[0]["line"] == 3
    assert "Create the model" in tasks[0]["body"]


def test_planmode_fixture_reads_fenced_commands():
    tasks = parse_plan(read_fixture("planmode.md"))

    assert [task["task_id"] for task in tasks] == ["T1", "T2", "T3"]
    assert tasks[0]["commands"] == ["python -m pytest tests/test_model.py"]
    assert tasks[1]["commands"] == ["python -m pytest tests/test_ui.py"]


def test_checklist_fixture_uses_positions_and_metadata():
    tasks = parse_plan(read_fixture("checklist.md"))

    assert [task["task_id"] for task in tasks] == ["T1", "T2", "T3"]
    assert [task["title"] for task in tasks] == [
        "Create the model",
        "Create the screen",
        "Add the guide",
    ]
    assert tasks[1]["files"] == ["todo/ui.py"]
    assert tasks[1]["commands"] == ["python -m pytest tests/test_ui.py"]


def test_heading_inside_code_fence_is_ignored():
    text = """# Todo app
```markdown
## Task 99: Not a task
```
## Task 1: Real one
## Task 2: Real two
"""

    assert [task["task_id"] for task in parse_plan(text)] == ["T1", "T2"]


def test_line_ranges_are_stripped_from_files():
    task = parse_plan("## Task 1: One\n**Files:** `src/app.py:123-145`\n" 
                     "## Task 2: Two\n**Files:** `src/other.py`\n")[0]

    assert task["files"] == ["src/app.py"]


def test_git_commands_are_dropped():
    text = ("## Task 1: One\nRun: `git add todo.py`\nRun: `git push origin main`\n"
            "Run: `python tool.py`\n## Task 2: Two\n")

    assert parse_plan(text)[0]["commands"] == ["python tool.py"]


def test_duplicate_heading_ids_raise():
    with pytest.raises(PlanParseError, match="duplicate task id T1"):
        parse_plan("## Task 1: One\n## Task 1: Again\n")


def test_single_task_raises():
    with pytest.raises(PlanParseError, match="need at least 2 tasks"):
        parse_plan("## Task 1: Only one\n")


def test_dependency_forms_are_case_insensitive_and_validated():
    text = ("## Task 1: One\n## Task 2: Two\nDepends on task 1\n"
            "## Task 3: Three\nAfter TASK 2\n## Task 4: Four\n"
            "Consumes: the output of Task 3 and Task 99\n")

    tasks = parse_plan(text)
    assert tasks[1]["depends_on"] == ["T1"]
    assert tasks[2]["depends_on"] == ["T2"]
    assert tasks[3]["depends_on"] == ["T3"]


def test_self_dependencies_are_dropped():
    text = "## Task 1: One\nAfter Task 1\n## Task 2: Two\n"

    assert parse_plan(text)[0]["depends_on"] == []


def test_crlf_input_is_supported():
    text = "## Task 1: One\r\nRun: `python one.py`\r\n## Task 2: Two\r\n"

    assert parse_plan(text)[0]["commands"] == ["python one.py"]


def test_heading_strategy_wins_over_lists():
    text = ("1. A list item\n2. Another list item\n## Task 1: One\n"
            "## Task 2: Two\n")

    assert [task["task_id"] for task in parse_plan(text)] == ["T1", "T2"]


def test_body_excludes_boundary_lines_and_has_task_fields():
    tasks = parse_plan("## Task 1: One\nfirst line\nsecond line\n## Task 2: Two\n")

    assert tasks[0]["body"] == "first line\nsecond line"
    assert set(tasks[0]) == {
        "task_id", "title", "body", "files", "commands", "depends_on", "line", "locality"
    }


def test_large_plan_is_parsed():
    text = "\n".join(
        "## Task {0}: Item {0}\nDetails for item {0}.".format(number)
        for number in range(1, 2501)
    )

    tasks = parse_plan(text)

    assert len(tasks) == 2500
    assert tasks[-1]["task_id"] == "T2500"


def test_headings_inside_backtick_or_tilde_fences_are_ignored():
    text = "## Task 1: One\n~~~\n## Task 9: Nested\n~~~\n## Task 2: Two\n"
    assert [task["task_id"] for task in parse_plan(text)] == ["T1", "T2"]


def test_forward_dependency_is_kept():
    text = "## Task 1: One\nDepends on Task 2\n## Task 2: Two\n"
    assert parse_plan(text)[0]["depends_on"] == ["T2"]


def test_backward_dependency_is_kept():
    text = "## Task 1: One\n## Task 2: Two\nDepends on Task 1\n"
    assert parse_plan(text)[1]["depends_on"] == ["T1"]


def test_self_dependency_is_dropped():
    text = "## Task 1: One\nDepends on Task 1\n## Task 2: Two\n"
    assert parse_plan(text)[0]["depends_on"] == []


def test_unknown_dependency_is_dropped():
    text = "## Task 1: One\nDepends on Task 9\n## Task 2: Two\n"
    assert parse_plan(text)[0]["depends_on"] == []
