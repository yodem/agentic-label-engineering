import json
import os

from ale.events import read_events
from tests.test_cli import ale, run_dir, state  # noqa: F401  (run_dir is a fixture)


def _label(run_dir, tid):
    with open(os.path.join(run_dir, "labels", tid + ".json")) as handle:
        return json.load(handle)


def _changes(run_dir):
    return [e for e in read_events(os.path.join(run_dir, "events.jsonl")) if e.get("type") == "label_changed"]


def test_add_and_remove_paths(run_dir, capsys):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "src/session/**",
               "--remove-path", "tests/auth/**", "--reason", "session code moved into scope") == 0
    assert _label(run_dir, "T01")["context"]["allowed_paths"] == ["src/auth/**", "src/session/**"]
    (change,) = _changes(run_dir)
    assert change["field"] == "context.allowed_paths" and change["agent_id"] is None
    assert change["old"] == ["src/auth/**", "tests/auth/**"] and change["reason"].startswith("session")
    capsys.readouterr()
    ale(run_dir, "status", "--json")  # the effective label (events applied) agrees with the file
    assert ale(run_dir, "validate") == 0


def test_remove_dependency(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T02", "--remove-dep", "T01", "--reason", "docs no longer wait") == 0
    assert _label(run_dir, "T02")["context"]["depends_on"] == []
    assert [c["field"] for c in _changes(run_dir)] == ["context.depends_on"]


def test_overlap_between_independent_tasks_is_refused(run_dir):
    ale(run_dir, "init-run")
    ale(run_dir, "rescope", "--task", "T02", "--remove-dep", "T01", "--reason", "independent now")
    before = _label(run_dir, "T02")
    assert ale(run_dir, "rescope", "--task", "T02", "--add-path", "src/auth/login.py", "--reason", "x" * 5) == 1
    assert _label(run_dir, "T02") == before and len(_changes(run_dir)) == 1


def test_cycle_and_unknown_dependency_are_refused(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T01", "--add-dep", "T02", "--reason", "would loop") == 1
    assert ale(run_dir, "rescope", "--task", "T01", "--add-dep", "T99", "--reason", "no such task") == 1
    assert _changes(run_dir) == []


def test_terminal_task_is_refused(run_dir):
    ale(run_dir, "init-run")
    from ale.events import append_event, make_event
    run_id = _label(run_dir, "T02")["run_id"]
    append_event(os.path.join(run_dir, "events.jsonl"), make_event("canceled", run_id, 1, task_id="T02", attempt=1))
    assert ale(run_dir, "rescope", "--task", "T02", "--add-path", "docs/extra/**", "--reason", "late") == 1
    assert _changes(run_dir) == []


def test_usage_errors(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T01", "--reason", "nothing to change") == 2
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "x/**", "--reason", "") == 2
    assert ale(run_dir, "rescope", "--task", "T01", "--remove-path", "not/there/**", "--reason", "absent") == 2
    assert ale(run_dir, "rescope", "--task", "T01", "--remove-path", "src/auth/**",
               "--remove-path", "tests/auth/**", "--reason", "empties the scope") == 1


def test_noop_change_emits_nothing(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "src/auth/**", "--reason", "already there") == 0
    assert _changes(run_dir) == []


def _claim(run_dir, tid="T01"):
    assert ale(run_dir, "claim", "--task", tid, "--agent", "a1", now=1) == 0


def test_started_task_may_only_widen(run_dir, capsys):
    ale(run_dir, "init-run")
    _claim(run_dir)
    assert ale(run_dir, "rescope", "--task", "T01", "--remove-path", "tests/auth/**", "--reason", "narrow") == 1
    assert ale(run_dir, "rescope", "--task", "T02", "--remove-dep", "T01", "--reason", "unstarted") == 0
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "src/session/**", "--reason", "fix touched it") == 0
    capsys.readouterr()
    ale(run_dir, "status", "--json")
    assert "src/session/**" in _label(run_dir, "T01")["context"]["allowed_paths"]


def test_started_task_dependency_change_is_refused(run_dir):
    ale(run_dir, "init-run")
    _claim(run_dir, "T01")
    edit = [c for c in _changes(run_dir)]
    assert ale(run_dir, "rescope", "--task", "T01", "--add-dep", "T02", "--reason", "late dependency") == 1
    assert _changes(run_dir) == edit


def test_path_forms_are_normalized_or_refused(run_dir):
    ale(run_dir, "init-run")
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "./src/session/**", "--reason", "dot") == 0
    assert "src/session/**" in _label(run_dir, "T01")["context"]["allowed_paths"]
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "lib/", "--reason", "dir form") == 2
    assert ale(run_dir, "rescope", "--task", "T01", "--add-path", "../other/**", "--reason", "escape") == 2


def test_fix_task_overlap_does_not_block_unrelated_rescope(run_dir):
    ale(run_dir, "init-run")
    fix = _label(run_dir, "T01")
    fix.update(task_id="T01.fix1", fixes="T01", title="Fix T01 acceptance")
    fix["context"]["depends_on"] = []
    with open(os.path.join(run_dir, "labels", "T01.fix1.json"), "w") as handle:
        json.dump(fix, handle)
    assert ale(run_dir, "rescope", "--task", "T02", "--add-path", "docs/extra/**", "--reason", "unrelated") == 0
