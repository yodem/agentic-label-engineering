"""``ale eval cases``: the offline regression suite grown from real run failures."""
import ast
import copy
import hashlib
import json
import os
import re

import pytest

import ale
from ale import evalcases as EC
from ale import evalledger as EL
from ale import refs as REFS
from ale.cli import main

REPO = os.path.dirname(os.path.dirname(os.path.abspath(EC.__file__)))
SEED_IDS = {
    "parse-bulleted-files", "parse-letter-ids", "parse-numeric-ids-unchanged", "bake-write-task-worktree",
    "refs-trove-allowed", "refs-rm-refused", "route-claude-in-session", "analyze-relative-worktree",
    "analyze-input-required-flood", "analyze-empty-path-scope", "analyze-register-worktree",
    "analyze-clean-run",
}
FIELDS = {"id", "kind", "input", "expected", "category", "difficulty", "created_at", "active", "source_run"}


def seed_cases():
    return EC.load_cases(EC.default_cases_path())


def by_id(case_id):
    return next(case for case in seed_cases() if case["id"] == case_id)


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")
    return str(path)


def inline(case_id="refs-x", command="trove items get abc:1", allowed=True, active=True):
    return {"id": case_id, "kind": "refs", "input": command, "expected": {"allowed": allowed},
            "category": "refs", "difficulty": "easy", "created_at": "2026-10-04", "active": active,
            "source_run": "test"}


# --- the seed suite --------------------------------------------------------------------------

def test_the_default_cases_file_is_bundled_beside_the_plugin():
    assert EC.default_cases_path() == os.path.join(REPO, "evalcases", "cases.jsonl")


def test_every_seed_case_is_present_and_well_formed():
    cases = seed_cases()
    assert {case["id"] for case in cases} >= SEED_IDS
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert set(case) >= FIELDS, case["id"]
        assert case["kind"] in EC.KINDS
        assert case["source_run"], case["id"]


@pytest.mark.parametrize("case_id", sorted(SEED_IDS))
def test_every_seed_case_passes_on_this_code(case_id):
    result = EC.run_case(by_id(case_id))
    assert result["passed"] and result["score"] == 1.0, result


def _flip(case):
    """Break a case's expected output on purpose."""
    case = copy.deepcopy(case)
    expected = case["expected"]
    if case["kind"] == "parse":
        expected["tasks"][0]["id"] = expected["tasks"][0]["id"] + "x"
    elif case["kind"] == "bake":
        task_id = sorted(expected["labels"])[0]
        key = sorted(expected["labels"][task_id])[0]
        expected["labels"][task_id][key] = "flipped"
    elif case["kind"] == "route":
        expected["mode"] = "flipped"
    elif case["kind"] == "refs":
        expected["allowed"] = not expected["allowed"]
    else:
        expected["checks"] = {key: not value for key, value in expected["checks"].items()}
    return case


@pytest.mark.parametrize("case_id", sorted(SEED_IDS))
def test_every_seed_case_can_fail(case_id):
    result = EC.run_case(_flip(by_id(case_id)))
    assert not result["passed"] and result["score"] < 1.0, result
    assert result["reason"]


def test_analyze_cases_write_nothing_under_ale_home(ale_home):
    for case in seed_cases():
        if case["kind"] == "analyze":
            assert EC.run_case(case)["passed"]
    assert not os.path.exists(ale_home)


def test_seed_cases_carry_no_home_paths_emails_or_story_ids():
    root = os.path.join(REPO, "evalcases")
    for directory, _dirs, files in os.walk(root):
        assert os.path.basename(directory) != "fixtures", "setup.py skips dirs named fixtures"
        for name in files:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                text = handle.read()
            assert "/home/" not in text and "/Users/" not in text and "~/" not in text, name
            assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), name
            assert not re.search(r"\bsc-[0-9]+\b", text), name


# --- evaluators ------------------------------------------------------------------------------

def test_score_is_the_share_of_expected_fields_that_match():
    case = {"id": "p", "kind": "parse", "category": "parse",
            "input": "### Task 1: One\nFiles: `a.py`\n### Task 2: Two\nFiles: `b.py`\n",
            "expected": {"tasks": [{"id": "T1", "files": ["a.py"]}, {"id": "T2", "files": ["wrong.py"]}]}}
    result = EC.run_case(case)
    # count, T1 id, T1 files, T2 id match; T2 files does not.
    assert result["score"] == pytest.approx(4 / 5.0)
    assert result["passed"] is False and "tasks[1].files" in result["reason"]
    assert result["actual"]["tasks"][1] == {"id": "T2", "files": ["b.py"]}


def test_a_parse_error_scores_zero_with_the_error_as_reason():
    case = {"id": "p", "kind": "parse", "input": "no tasks here",
            "expected": {"tasks": [{"id": "T1", "files": []}]}}
    result = EC.run_case(case)
    assert result["score"] == 0.0 and not result["passed"] and "need at least 2 tasks" in result["reason"]


def test_an_analyze_check_with_no_result_is_none_and_can_be_expected_as_na(tmp_path):
    case = copy.deepcopy(by_id("analyze-clean-run"))
    case["input"] = {"dir": os.path.join(REPO, "evalcases", case["input"]["dir"])}
    case["expected"] = {"checks": {"ale.task.first_pass": True, "ale.no.such.check": None}}
    assert EC.run_case(case)["passed"]


def test_route_compares_only_the_named_keys():
    case = copy.deepcopy(by_id("route-claude-in-session"))
    case["expected"] = {"harness": "claude"}
    assert EC.run_case(case)["passed"]


def test_refs_allowed_command_accepts_ck_and_trove_reads_only():
    assert REFS.allowed_command(["ck", "items", "get", "x:1"])
    assert REFS.allowed_command(["trove", "items", "get", "x:1"])
    assert not REFS.allowed_command(["trove", "items", "list"])
    assert not REFS.allowed_command(["rm", "-rf", "refs"])
    assert not REFS.allowed_command([])


def test_an_unknown_kind_is_a_load_error(tmp_path):
    path = write_cases(tmp_path, [dict(inline(), kind="vibes")])
    with pytest.raises(EC.CaseError):
        EC.load_cases(path)


def test_config_hash_is_the_version_plus_the_thresholds_bytes():
    from ale import analyze
    with open(analyze.THRESHOLDS_PATH, "rb") as handle:
        blob = ale.__version__.encode("utf-8") + handle.read()
    assert EC.config_hash() == hashlib.sha256(blob).hexdigest()[:12]


# --- the command -----------------------------------------------------------------------------

def test_the_seed_suite_passes_under_ci_without_recording(capsys, ale_home):
    assert main(["eval", "cases", "--ci", "--no-record"]) == 0
    out = capsys.readouterr().out
    for case_id in SEED_IDS:
        assert "PASS %s:" % case_id in out
    assert not os.path.exists(EL.ledger_path())


def test_flipping_one_expected_makes_ci_exit_1(tmp_path, capsys):
    good = write_cases(tmp_path, [inline("refs-a"), inline("refs-b", "rm -rf refs", allowed=False)])
    assert main(["eval", "cases", "--cases", good, "--ci", "--no-record"]) == 0
    bad = write_cases(tmp_path, [inline("refs-a"), inline("refs-b", "rm -rf refs", allowed=True)])
    capsys.readouterr()
    assert main(["eval", "cases", "--cases", bad, "--ci", "--no-record"]) == 1
    out = capsys.readouterr().out
    assert "PASS refs-a:" in out and "FAIL refs-b:" in out
    # Without --ci the run reports and exits 0.
    assert main(["eval", "cases", "--cases", bad, "--no-record"]) == 0


def test_an_inactive_case_is_skipped(tmp_path, capsys):
    path = write_cases(tmp_path, [inline("refs-a"), inline("retired", "rm x", allowed=True, active=False)])
    assert main(["eval", "cases", "--cases", path, "--ci"]) == 0
    out = capsys.readouterr().out
    assert "retired" not in out.split("eval cases:")[0]
    assert "1 inactive" in out
    assert [row["case_id"] for row in EL.read_rows()] == ["refs-a"]


def test_no_record_writes_no_ledger_row_and_a_recorded_run_writes_offline_rows(tmp_path):
    path = write_cases(tmp_path, [inline("refs-a"), inline("refs-b", "ck items get y:2")])
    assert main(["eval", "cases", "--cases", path, "--no-record"]) == 0
    assert EL.read_rows() == []
    assert main(["eval", "cases", "--cases", path]) == 0
    rows = EL.read_rows()
    assert [row["case_id"] for row in rows] == ["refs-a", "refs-b"]
    for row in rows:
        assert row["case_kind"] == "offline" and row["tool"] == "ale"
        assert row["tool_version"] == ale.__version__ and row["config_hash"] == EC.config_hash()
        assert re.fullmatch(r"cases-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z", row["run_id"])
        assert row["evaluator"] == "ale.case.refs" and row["case_category"] == "refs"
        assert row["passed"] is True and row["score"] == 1.0
        assert row["metadata"]["source_run"] == "test"


def test_a_score_drop_versus_an_earlier_pass_is_a_regression(tmp_path, capsys):
    path = write_cases(tmp_path, [inline("refs-a")])
    earlier = EL.make_row("cases-2026-01-01T00:00:00Z", "0.3.2", "h", "refs-a", "offline", "refs",
                          "ale.case.refs", 1.0, True, "1/1 fields match")
    earlier["timestamp"] = "2026-01-01T00:00:00Z"
    EL.append_rows([earlier])
    broken = write_cases(tmp_path, [inline("refs-a", allowed=False)])
    assert main(["eval", "cases", "--cases", broken, "--ci", "--no-record"]) == 1
    out = capsys.readouterr().out
    assert "REGRESSED refs-a: score 0.00 < best 1.00" in out
    assert "1 regressed" in out


def test_a_regression_of_a_retired_case_does_not_fail_ci(tmp_path):
    rows = []
    for ts, score in (("2026-01-01T00:00:00Z", 1.0), ("2026-01-02T00:00:00Z", 0.0)):
        row = EL.make_row("cases-x", "0.3.2", "h", "retired", "offline", "refs", "ale.case.refs",
                          score, score == 1.0, "")
        row["timestamp"] = ts
        rows.append(row)
    EL.append_rows(rows)
    path = write_cases(tmp_path, [inline("refs-a"), inline("retired", active=False)])
    assert main(["eval", "cases", "--cases", path, "--ci", "--no-record"]) == 0


def test_online_rows_never_count_as_offline_regressions(tmp_path):
    rows = []
    for ts, score in (("2026-01-01T00:00:00Z", 1.0), ("2026-01-02T00:00:00Z", 0.0)):
        row = EL.make_row("analyze-x", "0.4.0", "h", "refs-a", "online", "run", "ale.case.refs",
                          score, score == 1.0, "")
        row["timestamp"] = ts
        rows.append(row)
    EL.append_rows(rows)
    path = write_cases(tmp_path, [inline("refs-a")])
    assert main(["eval", "cases", "--cases", path, "--ci", "--no-record"]) == 0


def test_an_unreadable_cases_file_is_a_usage_error(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    assert main(["eval", "cases", "--cases", str(path)]) == 2
    assert main(["eval", "cases", "--cases", str(tmp_path / "missing.jsonl")]) == 2


# --- bundling --------------------------------------------------------------------------------

def _setup_constant(name):
    with open(os.path.join(REPO, "setup.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def test_evalcases_is_bundled_shipped_and_release_checked():
    assert "evalcases" in _setup_constant("BUNDLED")
    with open(os.path.join(REPO, "MANIFEST.in"), encoding="utf-8") as handle:
        assert "graft evalcases" in handle.read().splitlines()
    with open(os.path.join(REPO, "scripts", "release-check.sh"), encoding="utf-8") as handle:
        watched = next(line for line in handle if "git diff --name-only" in line)
    assert " evalcases" in watched


# --- ale analyze gates on offline regressions of active cases ---------------------------------

def _ledger_drop(case_id):
    rows = []
    for ts, score in (("2026-01-01T00:00:00Z", 1.0), ("2026-01-02T00:00:00Z", 0.0)):
        row = EL.make_row("cases-x", "0.4.0", "h", case_id, "offline", "refs", "ale.case.refs",
                          score, score == 1.0, "")
        row["timestamp"] = ts
        rows.append(row)
    EL.append_rows(rows)


def _default_cases(tmp_path, monkeypatch):
    path = write_cases(tmp_path, [inline("live-case"), inline("retired-case", active=False)])
    monkeypatch.setattr(EC, "default_cases_path", lambda: path)


def test_analyze_exits_1_on_an_active_offline_regression(tmp_path, monkeypatch, capsys):
    _default_cases(tmp_path, monkeypatch)
    _ledger_drop("live-case")
    index = str(tmp_path / "empty-index.jsonl")
    assert main(["analyze", "--no-write", "--index", index, "--since", "all"]) == 1
    assert "offline case live-case" in capsys.readouterr().out


def test_analyze_ignores_a_retired_cases_regression(tmp_path, monkeypatch, capsys):
    _default_cases(tmp_path, monkeypatch)
    _ledger_drop("retired-case")
    _ledger_drop("unknown-case")
    index = str(tmp_path / "empty-index.jsonl")
    assert main(["analyze", "--no-write", "--index", index, "--since", "all"]) == 0
    out = capsys.readouterr().out
    assert "retired-case" not in out and "unknown-case" not in out and "Nothing regressed." in out


def test_ci_with_no_active_cases_exits_1(tmp_path, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert main(["eval", "cases", "--cases", str(empty), "--ci", "--no-record"]) == 1
    assert "no active cases" in capsys.readouterr().err
    retired = write_cases(tmp_path, [inline("old", active=False)])
    assert main(["eval", "cases", "--cases", retired, "--ci", "--no-record"]) == 1
    # Without --ci it is reported, not gated.
    assert main(["eval", "cases", "--cases", retired, "--no-record"]) == 0
    assert "no active cases" in capsys.readouterr().err


def _unwritable_home(ale_home):
    os.makedirs(ale_home, exist_ok=True)
    with open(os.path.join(ale_home, ".ale"), "w") as handle:
        handle.write("a file where the .ale directory should be\n")


def test_an_unwritable_ledger_warns_and_keeps_the_verdict(tmp_path, ale_home, capsys):
    _unwritable_home(ale_home)
    path = write_cases(tmp_path, [inline("refs-a")])
    assert main(["eval", "cases", "--cases", path, "--ci"]) == 0
    captured = capsys.readouterr()
    assert "PASS refs-a:" in captured.out
    assert "eval ledger not updated" in captured.err and "Traceback" not in captured.err
    broken = write_cases(tmp_path, [inline("refs-a", allowed=False)])
    assert main(["eval", "cases", "--cases", broken, "--ci"]) == 1


def test_analyze_with_an_unwritable_home_warns_and_keeps_the_exit_code(tmp_path, ale_home, capsys):
    _unwritable_home(ale_home)
    index = str(tmp_path / "empty-index.jsonl")
    assert main(["analyze", "--index", index, "--since", "all"]) == 0
    captured = capsys.readouterr()
    assert "report not written" in captured.err and "Traceback" not in captured.err
    assert "# ALE analyze" in captured.out


def test_a_fix_record_falls_back_to_date_when_ts_does_not_parse():
    from ale import analyze
    report = {"min_n": 3, "case_results": [], "checks": {}}
    fixes = [{"evaluator": "a", "ts": "not a time", "date": "2026-10-01"},
             {"evaluator": "b", "date": "2026-10-01"},
             {"evaluator": "c", "ts": "2026-10-01T10:00:00.123+03:00", "date": "garbage"},
             {"evaluator": "d", "ts": "nope", "date": "also nope"},
             {"evaluator": "e"}]
    status = {item["evaluator"]: item["status"] for item in analyze.fix_statuses(report, fixes)}
    assert status == {"a": "pending", "b": "pending", "c": "pending", "d": "invalid", "e": "invalid"}
