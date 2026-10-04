import glob
import json
import os
import shutil

import pytest

from ale import analyze as A
from ale import evalledger as EL
from ale import runindex as RI
from ale.cli import main

from analyze_fixtures import (HOUR, entry, ev, evidence, good_run, good_task, label, run_dir_for,
                              run_header, write_run)

T0 = 1_700_000_000.0
DONE_NOW = T0 + 24 * HOUR
STALE_NOW = T0 + 100 * HOUR


def thresholds():
    return A.load_thresholds()


def report_for(runs, now=DONE_NOW, since_s=None, th=None):
    return A.evaluate(runs, th or thresholds(), now, since_s)


def load(run_dir, run_id):
    run = A.load_run(entry(run_dir, run_id))
    assert run is not None
    return run


def results(report, evaluator):
    return [row for row in report["case_results"] if row["evaluator"] == evaluator]


def one(report, evaluator, case_suffix=None):
    rows = results(report, evaluator)
    if case_suffix is not None:
        rows = [row for row in rows if row["case_id"].endswith(case_suffix)]
    assert len(rows) == 1, rows
    return rows[0]


def single_task_run(tmp_path, run_id, task_events, task_label=None, header=True, extra_labels=()):
    run_dir = run_dir_for(tmp_path, run_id)
    lab = task_label or label("T1", run_id)
    events = (run_header(run_id, T0, ["T1"]) if header else []) + task_events(run_dir)
    return load(write_run(tmp_path, run_id, [lab] + list(extra_labels), events), run_id)


# --- eval ledger -----------------------------------------------------------------------------

def test_make_row_is_an_ale_row_with_an_iso_timestamp():
    row = EL.make_row("analyze-x", "0.4.0", "abcd", "r/T1", "online", "backend/M",
                      "ale.task.first_pass", 1.0, True, "ok", {"run_dir": "/x"})
    assert row["tool"] == "ale" and row["timestamp"].endswith("Z") and "T" in row["timestamp"]
    assert row["metadata"] == {"run_dir": "/x"} and row["score"] == 1.0 and row["passed"] is True


def test_latest_by_case_dedupes_and_regressions_compare_with_the_best_before():
    def row(ts, score, case="c1"):
        r = EL.make_row("x", "v", "h", case, "offline", "cat", "ev", score, score >= 1, "")
        r["timestamp"] = ts
        return r
    rows = [row("2026-01-01T00:00:00Z", 1.0), row("2026-01-02T00:00:00Z", 0.0),
            row("2026-01-01T00:00:00Z", 0.0, "c2"), row("2026-01-02T00:00:00Z", 1.0, "c2")]
    latest = EL.latest_by_case(rows)
    assert latest[("c1", "ev")]["score"] == 0.0 and latest[("c2", "ev")]["score"] == 1.0
    found = EL.regressions(rows)
    assert [r["case_id"] for r in found] == ["c1"] and found[0]["best_before"] == 1.0


def test_append_rows_writes_under_ale_home(ale_home):
    EL.append_rows([EL.make_row("x", "v", "h", "c", "online", "cat", "ev", 1.0, True, "")])
    assert EL.ledger_path() == os.path.join(ale_home, ".ale", "eval-ledger.jsonl")
    assert len(EL.read_rows()) == 1


# --- thresholds ------------------------------------------------------------------------------

def test_thresholds_are_committed_for_every_check():
    th = thresholds()
    assert set(th["checks"]) == {check_id for check_id, _kind, _fn in A.CHECKS}
    assert th["min_n"] == 3 and th["window_days"] == 7 and th["stale_after_hours"] == 72
    assert all(kind in ("task", "run") for _id, kind, _fn in A.CHECKS)


# --- every check passes on a good run --------------------------------------------------------

def test_a_good_run_passes_every_check(tmp_path):
    run = load(good_run(tmp_path, "good", T0), "good")
    report = report_for([run])
    assert report["runs"]["done"] == 1
    for check_id, check in report["checks"].items():
        assert check["n"] >= 1, check_id
        assert check["rate"] == 1.0 and not check["breached"], (check_id, check)


# --- and each one fails when its input is broken on purpose ----------------------------------

def test_first_pass_fails_when_the_first_verify_rejected(tmp_path):
    def events(run_dir):
        agent = "T1-executor-backend-1"
        base = good_task("r", "T1", T0 + 100, run_dir)
        spawn, claim = base[0], base[1]
        return [spawn, claim,
                ev("submitted", "r", T0 + 130, "T1", agent, summary="first"),
                ev("verified", "r", T0 + 140, "T1", evidence=evidence(["src/a.py"], passed=False)),
                ev("rejected", "r", T0 + 140, "T1", evidence=evidence(["src/a.py"], passed=False), reason="A1"),
                ev("claimed", "r", T0 + 150, "T1", agent, attempt=2),
                ev("submitted", "r", T0 + 160, "T1", agent, attempt=2, summary="second"),
                ev("verified", "r", T0 + 170, "T1", attempt=2, evidence=evidence(["src/a.py"])),
                ev("accepted", "r", T0 + 170, "T1", attempt=2, evidence=evidence(["src/a.py"])),
                ev("usage", "r", T0 + 165, "T1", agent, attempt=2, **{
                    "gen_ai.request.model": "gpt-x", "gen_ai.usage.input_tokens": 5,
                    "gen_ai.usage.output_tokens": 5, "usage_source": "adapter"})]
    run = single_task_run(tmp_path, "r", events)
    report = report_for([run])
    assert one(report, "ale.task.first_pass")["score"] == 0.0
    row = A.task_rows(run)[0]
    assert row["first_verify_passed"] is False and row["rejects"] == 1 and row["attempts"] == 2
    assert row["state"] == "accepted"


def test_path_scope_checked_fails_on_an_empty_verified_file_list(tmp_path):
    run = single_task_run(tmp_path, "r", lambda d: good_task("r", "T1", T0 + 100, d, files=()))
    result = one(report_for([run]), "ale.task.path_scope_checked")
    assert result["score"] == 0.0 and result["passed"] is False


def test_write_has_worktree_fails_for_a_mode_none_task_that_integrated(tmp_path):
    run = single_task_run(tmp_path, "r", lambda d: good_task("r", "T1", T0 + 100, d),
                          task_label=label("T1", "r", worktree_mode="none"))
    report = report_for([run])
    assert one(report, "ale.task.write_has_worktree")["score"] == 0.0
    # path_scope_checked only judges tasks that had a worktree.
    assert results(report, "ale.task.path_scope_checked") == []


def test_dispatch_worktree_fails_for_a_register_worktree_spawn(tmp_path):
    run = single_task_run(tmp_path, "r", lambda d: good_task("r", "T1", T0 + 100, d, register=True))
    report = report_for([run])
    assert A.task_rows(run)[0]["spawned_by"] == "register"
    assert one(report, "ale.task.dispatch_worktree")["score"] == 0.0


def test_a_lead_claim_with_no_spawn_fails_dispatch_worktree(tmp_path):
    def events(run_dir):
        return [ev("claimed", "r", T0 + 10, "T1", "lead"),
                ev("submitted", "r", T0 + 20, "T1", "lead", summary="x")]
    run = single_task_run(tmp_path, "r", events)
    row = A.task_rows(run)[0]
    assert row["spawned_by"] == "none" and row["claimed_by_lead"] is True
    assert one(report_for([run]), "ale.task.dispatch_worktree")["score"] == 0.0


def test_resolved_and_no_stale_open_fail_for_a_stale_run_with_an_open_task(tmp_path):
    def events(run_dir):
        return good_task("r", "T1", T0 + 100, run_dir)[:3]   # spawned, claimed, one heartbeat
    run = single_task_run(tmp_path, "r", events)
    assert A.run_status(run, STALE_NOW, 72 * HOUR) == "stale"
    report = report_for([run], now=STALE_NOW)
    assert report["runs"]["stale"] == 1
    assert one(report, "ale.task.resolved")["score"] == 0.0
    assert one(report, "ale.run.no_stale_open")["score"] == 0.0


def test_resolved_passes_for_a_removed_task(tmp_path):
    def events(run_dir):
        return good_task("r", "T1", T0 + 100, run_dir) + [
            ev("label_removed", "r", T0 + 500, "T2", reason="not needed")]
    run = single_task_run(tmp_path, "r", events, extra_labels=[label("T2", "r")])
    report = report_for([run], now=STALE_NOW)
    assert one(report, "ale.task.resolved", "/T2")["score"] == 1.0
    assert one(report, "ale.run.no_stale_open")["score"] == 1.0


def test_input_required_bounded_fails_on_a_458_event_flood(tmp_path):
    def events(run_dir):
        base = good_task("r", "T1", T0 + 100, run_dir)
        flood = [ev("input_required", "r", T0 + 25, "T1", "T1-executor-backend-1", question="?")
                 for _ in range(458)]
        return base[:3] + flood + base[3:]
    run = single_task_run(tmp_path, "r", events)
    assert A.task_rows(run)[0]["input_required_max_per_attempt"] == 458
    assert one(report_for([run]), "ale.task.input_required_bounded")["score"] == 0.0


def test_input_required_bounded_passes_at_three_per_attempt(tmp_path):
    def events(run_dir):
        base = good_task("r", "T1", T0 + 100, run_dir)
        asks = [ev("input_required", "r", T0 + 25, "T1", "T1-executor-backend-1", question="?")
                for _ in range(3)]
        return base[:3] + asks + base[3:]
    run = single_task_run(tmp_path, "r", events)
    assert one(report_for([run]), "ale.task.input_required_bounded")["score"] == 1.0


def test_usage_recorded_fails_for_a_headless_task_without_usage(tmp_path):
    run = single_task_run(tmp_path, "r", lambda d: good_task("r", "T1", T0 + 100, d, usage=False))
    row = A.task_rows(run)[0]
    assert row["headless"] is True and row["usage_tokens"] is None
    assert one(report_for([run]), "ale.task.usage_recorded")["score"] == 0.0


def test_acceptance_held_fails_after_an_acceptance_relabel(tmp_path):
    def events(run_dir):
        return good_task("r", "T1", T0 + 100, run_dir) + [
            ev("label_changed", "r", T0 + 105, "T1", field="acceptance", old=[], reason="weaker",
               new=[{"id": "A1", "cmd": "true", "expect": "exit0"},
                    {"id": "A2", "cmd": "true", "expect": "exit0"}])]
    run = single_task_run(tmp_path, "r", events)
    assert A.task_rows(run)[0]["acceptance_relabeled"] is True
    assert one(report_for([run]), "ale.task.acceptance_held")["score"] == 0.0


def test_absolute_paths_fails_on_a_relative_spawned_worktree(tmp_path):
    run = single_task_run(tmp_path, "r", lambda d: good_task("r", "T1", T0 + 100, d,
                                                             worktree=".ale/runs/r/wt/T1"))
    result = one(report_for([run]), "ale.run.absolute_paths")
    assert result["score"] == 0.0 and result["case_id"] == run["run_key"]


# --- n/a premises ----------------------------------------------------------------------------

def test_checks_whose_premise_does_not_hold_write_no_case_result(tmp_path):
    def events(run_dir):
        # In-session task, never spawned with a worktree, never verified; legacy run (no run_started).
        return [ev("claimed", "r", T0 + 10, "T1", "agent-1"),
                ev("heartbeat", "r", T0 + 20, "T1", "agent-1", step="x")]
    run = single_task_run(tmp_path, "r", events, header=False,
                          task_label=label("T1", "r", executor="claude", mode="in-session"))
    report = report_for([run], now=T0 + HOUR)          # still active
    for check_id in ("ale.task.first_pass", "ale.task.path_scope_checked", "ale.task.write_has_worktree",
                     "ale.task.resolved", "ale.task.usage_recorded", "ale.task.acceptance_held",
                     "ale.run.absolute_paths"):
        assert results(report, check_id) == [], check_id
        assert report["checks"][check_id]["n"] == 0
        assert report["checks"][check_id]["rate"] is None and not report["checks"][check_id]["breached"]
    assert report["runs"]["active"] == 1


def test_a_legacy_accept_without_a_file_list_is_n_a_for_path_scope(tmp_path):
    def events(run_dir):
        base = good_task("r", "T1", T0 + 100, run_dir)
        for event in base:
            if event["type"] in ("verified", "accepted"):
                event["evidence"] = {"passed": True, "results": []}
        return [e for e in base if e["type"] != "integrated"]
    run = single_task_run(tmp_path, "r", events)
    assert A.task_rows(run)[0]["verified_files"] is None
    assert results(report_for([run]), "ale.task.path_scope_checked") == []


def test_breached_needs_min_n(tmp_path):
    def failing(run_id, n):
        run_dir = run_dir_for(tmp_path, run_id)
        ids = ["T%d" % (i + 1) for i in range(n)]
        events = run_header(run_id, T0, ids)
        for i, task_id in enumerate(ids):
            events += good_task(run_id, task_id, T0 + 100 * (i + 1), run_dir, usage=False)
        return load(write_run(tmp_path, run_id, [label(t, run_id) for t in ids], events), run_id)
    two = report_for([failing("two", 2)])["checks"]["ale.task.usage_recorded"]
    assert two["n"] == 2 and two["rate"] == 0.0 and two["breached"] is False
    three = report_for([failing("three", 3)])["checks"]["ale.task.usage_recorded"]
    assert three["n"] == 3 and three["breached"] is True
    key = A.run_key(run_dir_for(tmp_path, "three"))
    assert key == "%s:three" % tmp_path.name
    assert three["examples"] == [key + "/T1", key + "/T2", key + "/T3"]


# --- robustness ------------------------------------------------------------------------------

def test_malformed_event_and_label_lines_are_skipped_and_counted(tmp_path):
    run_dir = good_run(tmp_path, "r", T0)
    with open(os.path.join(run_dir, "events.jsonl"), "a") as handle:
        handle.write("{broken\n")
        handle.write(json.dumps({"type": "usage", "ts": T0, "task_id": "T1"}) + "\n")  # missing keys
        handle.write("[1, 2]\n")
    with open(os.path.join(run_dir, "labels", "Tbad.json"), "w") as handle:
        handle.write("{not json")
    run = load(run_dir, "r")
    assert run["skipped_lines"] == 4
    report = report_for([run])
    assert report["skipped_lines"] == 4
    assert report["checks"]["ale.task.first_pass"]["n"] == 3


def test_a_missing_run_dir_loads_as_none_and_counts_as_skipped(tmp_path):
    assert A.load_run(entry(str(tmp_path / "gone"), "gone")) is None
    report = report_for([None])
    assert report["runs"]["skipped"] == 1


def test_window_and_exclusions(tmp_path):
    old = load(good_run(tmp_path, "old", T0 - 30 * 24 * HOUR), "old")
    new = load(good_run(tmp_path, "new", T0), "new")
    excluded = load(good_run(tmp_path, "roundtrip", T0), "roundtrip")
    by_dir = load(good_run(tmp_path, "plan", T0, dir_name="jev-bakeoff"), "plan")
    report = report_for([old, new, excluded, by_dir], since_s=7 * 24 * HOUR)
    assert report["runs"]["done"] == 1 and report["runs"]["skipped"] == 2
    assert {row["case_id"].split("/")[0] for row in report["case_results"]} == {new["run_key"]}
    everything = report_for([old, new, excluded, by_dir], since_s=None)
    assert everything["runs"]["done"] == 2


def test_previous_window_rates_are_reported(tmp_path):
    week = 7 * 24 * HOUR
    prev_dir = run_dir_for(tmp_path, "prev")
    ids = ["T1", "T2", "T3"]
    events = run_header("prev", T0 - week, ids)
    for i, task_id in enumerate(ids):
        events += good_task("prev", task_id, T0 - week + 100 * (i + 1), prev_dir, usage=False)
    prev = load(write_run(tmp_path, "prev", [label(t, "prev") for t in ids], events), "prev")
    now_run = load(good_run(tmp_path, "now", T0), "now")
    report = report_for([prev, now_run], since_s=week, now=T0 + HOUR)
    assert report["previous"]["ale.task.usage_recorded"] == 0.0
    assert report["checks"]["ale.task.usage_recorded"]["rate"] == 1.0


def test_case_ids_categories_and_config_hash(tmp_path):
    run = load(good_run(tmp_path, "r", T0), "r")
    report = report_for([run])
    row = one(report, "ale.task.first_pass", "/T1")
    assert row["case_id"] == run["run_key"] + "/T1" and row["case_category"] == "backend/M"
    assert row["config_hash"] == "abcd1234" and row["tool_version"] == "0.4.0"
    assert row["metadata"]["run_dir"] == run["run_dir"]
    assert one(report, "ale.run.no_stale_open")["case_category"] == "run"


def test_run_keys_tell_apart_same_named_runs_in_two_repos(tmp_path):
    first = A.load_run(entry(good_run(tmp_path / "repo-a", "plan", T0, dir_name="eval"), "plan"))
    second = A.load_run(entry(good_run(tmp_path / "repo-b", "plan", T0, dir_name="eval"), "plan"))
    assert first["run_key"] == "repo-a:eval" and second["run_key"] == "repo-b:eval"
    cases = report_for([first, second])["case_results"]
    assert len({(c["case_id"], c["evaluator"]) for c in cases}) == len(cases)
    assert A.run_key("/somewhere/custom-run") == "custom-run"


def test_case_ids_use_the_run_dir_name_when_the_run_id_differs(tmp_path):
    # Plans compiled from a file called plan.md all get the run id "plan".
    run_dir = good_run(tmp_path, "plan", T0, dir_name="2026-10-04-eval")
    run = A.load_run(entry(run_dir, "plan"))
    assert run["run_id"] == "plan"
    report = report_for([run])
    assert {row["case_id"].split("/")[0] for row in report["case_results"]} == {
        "%s:2026-10-04-eval" % tmp_path.name}
    assert {row["run_id"] for row in report["case_results"]} == {"plan"}


def test_metrics_and_calibration(tmp_path):
    run = load(good_run(tmp_path, "r", T0), "r")
    report = report_for([run])
    metrics = report["metrics"]["backend|standard|codex|gpt-x"]
    assert metrics["tasks"] == 3 and metrics["acceptance_rate"] == 1.0 and metrics["first_pass_rate"] == 1.0
    assert metrics["attempts_per_accepted"] == 1.0 and metrics["claim_to_accept_p50_s"] == 30.0
    assert metrics["tokens_per_accepted"] == 1200 and metrics["unknown_usage_share"] == 0.0
    buckets = report["calibration"]["effort_buckets"]
    assert buckets["M"] == {"n": 3, "median_s": 30.0, "median_files": 1}
    assert report["calibration"]["effort_monotonic"] is None   # fewer than 5 per bucket
    assert report["calibration"]["outlier_groups"] == []
    assert all(value["status"].startswith("shadow: ") for value in report["promotions"].values())


def test_effort_monotonic_detects_a_reversed_bucket(tmp_path):
    run_dir = run_dir_for(tmp_path, "r")
    labels, events = [], []
    for i in range(10):
        task_id = "T%d" % (i + 1)
        effort = "S" if i < 5 else "L"
        labels.append(label(task_id, "r", effort=effort))
        task = good_task("r", task_id, T0 + 1000 * i, run_dir)
        if effort == "S":   # small tasks take far longer than large ones: miscalibrated
            for event in task:
                if event["type"] in ("submitted", "verified", "accepted", "integrated", "usage"):
                    event["ts"] += 500
        events += task
    run = load(write_run(tmp_path, "r", labels, run_header("r", T0, [l["task_id"] for l in labels]) + events), "r")
    assert report_for([run])["calibration"]["effort_monotonic"] is False


def test_findings_track_first_seen_and_fix_status(tmp_path):
    def failing(run_id, t0):
        run_dir = run_dir_for(tmp_path, run_id)
        ids = ["T1", "T2", "T3"]
        events = run_header(run_id, t0, ids)
        for i, task_id in enumerate(ids):
            events += good_task(run_id, task_id, t0 + 100 * (i + 1), run_dir, usage=False)
        return load(write_run(tmp_path, run_id, [label(t, run_id) for t in ids], events), run_id)
    report = report_for([failing("a", T0)])
    previous = {"ale.task.usage_recorded": {"first_seen": "2026-01-01"}}
    open_findings = A.findings(report, previous, [])
    finding = open_findings["ale.task.usage_recorded"]
    assert finding["first_seen"] == "2026-01-01" and finding["n"] == 3 and finding["value"] == 0.0
    assert finding["bar"] == 0.50 and finding["fix"] is None and len(finding["examples"]) == 3
    assert "ale.task.first_pass" not in open_findings

    fix = {"ts": T0 - 10, "evaluator": "ale.task.usage_recorded", "commit": "abc", "repo": "ale",
           "note": "record usage", "cases_added": 1}
    scored = A.fix_statuses(report, [fix])
    assert A.findings(report, {}, scored)["ale.task.usage_recorded"]["fix"]["status"] == "regressed"
    late = A.fix_statuses(report, [dict(fix, ts=T0 + 10 * HOUR)])
    assert A.findings(report, {}, late)["ale.task.usage_recorded"]["fix"]["status"] == "pending"
    good = report_for([load(good_run(tmp_path, "g", T0), "g")])
    assert A.fix_statuses(good, [fix])[0]["status"] == "holding"


def test_render_markdown_answers_the_questions_in_order(tmp_path):
    report = report_for([load(good_run(tmp_path, "r", T0), "r")])
    text = A.render_markdown(report)
    headings = ["What regressed", "Weakest category", "Weakest evaluator", "Trend", "Saturated checks",
                "Fix status", "Metrics", "Calibration", "Promotions"]
    positions = [text.index("## " + heading) for heading in headings]
    assert positions == sorted(positions)
    assert "ale.task.first_pass" in text


def test_saturation_needs_the_last_n_reports_at_full_rate(tmp_path):
    report = report_for([load(good_run(tmp_path, "r", T0), "r")])
    report["history"] = [{"date": "d%d" % i, "checks": {"ale.task.first_pass": 1.0}} for i in range(4)]
    assert "ale.task.first_pass" in A.saturated(report, 4)
    report["history"][0]["checks"]["ale.task.first_pass"] = 0.5
    assert "ale.task.first_pass" in A.saturated(report, 4)        # only the last three count
    report["history"][-1]["checks"]["ale.task.first_pass"] = 0.5
    assert "ale.task.first_pass" not in A.saturated(report, 4)


# --- the CLI ---------------------------------------------------------------------------------

def _index(run_dir, run_id):
    assert RI.append_run(run_dir, os.path.dirname(run_dir), run_id, "0.4.0", "abcd1234")


def _reports(ale_home):
    return os.path.join(ale_home, ".ale", "reports")


@pytest.fixture
def now_env(monkeypatch):
    monkeypatch.setenv("ALE_NOW", str(DONE_NOW))


def test_cli_exits_0_on_a_clean_window_and_writes_the_report(tmp_path, ale_home, now_env, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    assert main(["analyze"]) == 0
    out = capsys.readouterr().out
    assert "ale.task.first_pass" in out
    reports = _reports(ale_home)
    names = sorted(os.listdir(reports))
    assert names == ["2023-11-15.json", "2023-11-15.md", "findings.json", "promotions.json"]
    with open(os.path.join(reports, "findings.json")) as handle:
        assert json.load(handle) == {}
    rows = EL.read_rows()
    assert rows and all(row["tool"] == "ale" and row["case_kind"] == "online" for row in rows)
    assert all(row["run_id"].startswith("analyze-") and row["config_hash"] == "abcd1234" for row in rows)


def test_cli_exits_1_on_a_breach_and_records_the_finding(tmp_path, ale_home, now_env, capsys):
    run_dir = run_dir_for(tmp_path, "r")
    ids = ["T1", "T2", "T3"]
    events = run_header("r", T0, ids)
    for i, task_id in enumerate(ids):
        events += good_task("r", task_id, T0 + 100 * (i + 1), run_dir, usage=False)
    _index(write_run(tmp_path, "r", [label(t, "r") for t in ids], events), "r")
    assert main(["analyze", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["ale.task.usage_recorded"]["breached"] is True
    with open(os.path.join(_reports(ale_home), "findings.json")) as handle:
        assert set(json.load(handle)) == {"ale.task.usage_recorded"}


def test_cli_no_write_writes_nothing(tmp_path, ale_home, now_env, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    before = sorted(os.listdir(os.path.join(ale_home, ".ale")))
    assert main(["analyze", "--no-write"]) == 0
    assert sorted(os.listdir(os.path.join(ale_home, ".ale"))) == before
    assert not os.path.exists(_reports(ale_home)) and not os.path.exists(EL.ledger_path())
    assert "ale.task.first_pass" in capsys.readouterr().out


def test_cli_if_due_without_a_reports_dir_runs_then_is_not_due(tmp_path, ale_home, now_env, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    assert not os.path.exists(_reports(ale_home))
    assert main(["analyze", "--if-due"]) == 0
    assert os.path.isdir(_reports(ale_home)) and "ale.task.first_pass" in capsys.readouterr().out
    assert main(["analyze", "--if-due"]) == 0
    assert capsys.readouterr().out.strip() == "ale analyze: not due (last report 2023-11-15)"


def test_cli_if_due_runs_again_after_due_after_days(tmp_path, ale_home, monkeypatch, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    monkeypatch.setenv("ALE_NOW", str(DONE_NOW))
    assert main(["analyze"]) == 0
    monkeypatch.setenv("ALE_NOW", str(DONE_NOW + 7 * 24 * HOUR))
    capsys.readouterr()
    assert main(["analyze", "--if-due", "--since", "all"]) == 0
    assert "not due" not in capsys.readouterr().out
    assert os.path.exists(os.path.join(_reports(ale_home), "2023-11-22.md"))


def test_cli_twice_a_day_overwrites_the_report_and_dedupes_ledger_rows(tmp_path, ale_home, now_env):
    _index(good_run(tmp_path, "r", T0), "r")
    assert main(["analyze"]) == 0
    first = len(EL.read_rows())
    assert main(["analyze"]) == 0
    rows = EL.read_rows()
    assert len(rows) == first   # unchanged scores are not appended again
    assert len(EL.latest_by_case(rows)) == first
    assert sorted(glob.glob(os.path.join(_reports(ale_home), "*.md"))) == [
        os.path.join(_reports(ale_home), "2023-11-15.md")]


def test_cli_skips_a_deleted_run_dir_with_a_warning(tmp_path, ale_home, now_env, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    gone = good_run(tmp_path, "gone", T0)
    _index(gone, "gone")
    shutil.rmtree(gone)
    assert main(["analyze", "--no-write"]) == 0
    err = capsys.readouterr().err
    assert "ale analyze: skipped %s (missing)" % os.path.realpath(gone) in err


def test_cli_backfills_a_legacy_run_and_fails_its_absolute_paths(tmp_path, ale_home, now_env, capsys):
    root = tmp_path / "repos"

    def events(run_dir):
        base = good_task("legacy", "T1", T0 + 100, run_dir, worktree=".ale/runs/legacy/wt/T1")
        del base[0]["base_commit"]
        return base
    run_dir = run_dir_for(root, "legacy")
    # Legacy: no run_started, no index entry, a relative worktree, no base_commit.
    write_run(root, "legacy", [label("T1", "legacy")], events(run_dir))
    assert RI.read_index() == []
    assert main(["analyze", "--backfill", str(root), "--json", "--no-write"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(RI.read_index()) == 1
    assert report["checks"]["ale.run.absolute_paths"]["rate"] == 0.0
    assert report["checks"]["ale.task.acceptance_held"]["n"] == 0     # n/a: no run_started
    assert report["checks"]["ale.task.first_pass"]["rate"] == 1.0


def test_cli_rejects_a_bad_since(ale_home):
    assert main(["analyze", "--since", "soon", "--no-write"]) == 2


def test_cli_explicit_index_and_thresholds(tmp_path, ale_home, now_env, capsys):
    index = str(tmp_path / "idx.jsonl")
    run_dir = good_run(tmp_path, "r", T0)
    RI.append_run(run_dir, str(tmp_path), "r", "0.4.0", "abcd1234", index=index)
    th = A.load_thresholds()
    th["checks"]["ale.task.first_pass"] = 1.01   # an impossible bar
    th_path = str(tmp_path / "th.json")
    with open(th_path, "w") as handle:
        json.dump(th, handle)
    assert main(["analyze", "--index", index, "--no-write"]) == 0
    assert main(["analyze", "--index", index, "--thresholds", th_path, "--no-write"]) == 1


# --- review fixes ----------------------------------------------------------------------------

def test_a_rejected_fix_task_whose_parent_was_accepted_is_superseded_not_open(tmp_path):
    run_dir = run_dir_for(tmp_path, "r")
    fix = label("T1F", "r", role="fixer")
    fix["fixes"] = "T1"
    a, f = "T1-executor-backend-1", "T1F-executor-fixer-1"
    bad = evidence(["src/a.py"], passed=False)
    good = evidence(["src/a.py"])
    events = run_header("r", T0, ["T1", "T1F"]) + good_task("r", "T1", T0 + 100, run_dir)[:2] + [
        ev("submitted", "r", T0 + 130, "T1", a, summary="first"),
        ev("verified", "r", T0 + 140, "T1", evidence=bad),
        ev("rejected", "r", T0 + 140, "T1", evidence=bad, reason="A1"),
        ev("task_added", "r", T0 + 150, "T1F", label_file="T1F.json", reason="fix"),
        ev("claimed", "r", T0 + 160, "T1F", f),
        ev("submitted", "r", T0 + 170, "T1F", f, summary="fix"),
        ev("verified", "r", T0 + 180, "T1F", evidence=bad),
        ev("rejected", "r", T0 + 180, "T1F", evidence=bad, reason="A1"),
        ev("reopened", "r", T0 + 190, "T1", attempt=2, reason="fixed by hand"),
        ev("verified", "r", T0 + 200, "T1", attempt=3, evidence=good),
        ev("accepted", "r", T0 + 200, "T1", attempt=3, evidence=good),
    ]
    run = load(write_run(tmp_path, "r", [label("T1", "r"), fix], events), "r")
    states = {row["task_id"]: row["state"] for row in A.task_rows(run)}
    assert states == {"T1": "accepted", "T1F": "superseded"}
    assert A.run_status(run, STALE_NOW, 72 * HOUR) == "done"
    report = report_for([run], now=STALE_NOW)
    assert [r["score"] for r in results(report, "ale.task.resolved")] == [1.0, 1.0]
    assert one(report, "ale.run.no_stale_open")["score"] == 1.0


@pytest.mark.parametrize("stamp", ["2023-11-14T00:00:00Z", "2023-11-14T00:00:00.789Z",
                                   "2023-11-14T00:00:00.123456Z", "2023-11-14T02:00:00+02:00",
                                   "2023-11-14T00:00:00", "2023-11-14", 1699920000])
def test_fix_timestamps_parse_in_every_iso_shape(stamp):
    assert A._epoch(stamp) == pytest.approx(1699920000.0, abs=1.0)


def test_fix_status_reads_ms_offset_and_date_fields_and_flags_an_invalid_stamp(tmp_path):
    good = report_for([load(good_run(tmp_path, "g", T0), "g")])   # T0 is 2023-11-14T22:13:20Z
    base = {"evaluator": "ale.task.usage_recorded", "commit": "abc", "note": "n"}
    statuses = A.fix_statuses(good, [
        dict(base, ts="2023-11-14T12:00:00.789Z"),
        dict(base, ts="2023-11-14T14:00:00+02:00"),
        dict(base, date="2023-11-14"),
        dict(base, ts="last tuesday"),
        dict(base),
    ])
    by_stamp = {str(s.get("ts", s.get("date"))): s["status"] for s in statuses}
    assert by_stamp == {"2023-11-14T12:00:00.789Z": "holding", "2023-11-14T14:00:00+02:00": "holding",
                        "2023-11-14": "holding", "last tuesday": "invalid", "None": "invalid"}


def _claimed_by(agent):
    def events(run_dir):
        task = good_task("r", "T1", T0 + 100, run_dir, usage=False)
        for event in task:
            if event.get("agent_id") is not None:
                event["agent_id"] = agent
        return task
    return events


def test_usage_recorded_is_n_a_for_a_lead_claimed_task_despite_a_headless_spawn(tmp_path):
    run = single_task_run(tmp_path, "r", _claimed_by("lead"))
    row = A.task_rows(run)[0]
    assert row["headless"] is True and row["claimed_by_lead"] is True and row["usage_tokens"] is None
    assert results(report_for([run]), "ale.task.usage_recorded") == []


def test_usage_recorded_fails_for_an_executor_claimed_headless_task_without_usage(tmp_path):
    run = single_task_run(tmp_path, "r", _claimed_by("T1-executor-backend-1"))
    assert A.task_rows(run)[0]["claimed_by_lead"] is False
    assert one(report_for([run]), "ale.task.usage_recorded")["score"] == 0.0


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_event_log_is_skipped_with_the_warning(tmp_path, ale_home, now_env, capsys):
    _index(good_run(tmp_path, "r", T0), "r")
    locked = good_run(tmp_path, "locked", T0)
    _index(locked, "locked")
    events_path = os.path.join(locked, "events.jsonl")
    os.chmod(events_path, 0)
    try:
        assert A.load_run(entry(locked, "locked")) is None
        assert main(["analyze", "--no-write"]) == 0
    finally:
        os.chmod(events_path, 0o644)
    assert "ale analyze: skipped %s (missing)" % os.path.realpath(locked) in capsys.readouterr().err


def test_the_runs_own_labeled_event_wins_over_the_index_entry(tmp_path):
    run_dir = good_run(tmp_path, "r", T0)                     # labeled events carry abcd1234
    run = A.load_run(entry(run_dir, "r", roster_hash="fromindex", ale_version="0.3.0"))
    assert run["roster_hash"] == "abcd1234" and run["ale_version"] == "0.3.0"
    legacy_dir = write_run(tmp_path, "legacy", [label("T1", "legacy")],
                           good_task("legacy", "T1", T0, run_dir_for(tmp_path, "legacy")))
    legacy = A.load_run(entry(legacy_dir, "legacy", roster_hash="fromindex"))
    assert legacy["roster_hash"] == "fromindex"               # no labeled event: index fallback
    stamped_dir = write_run(tmp_path, "stamped", [label("T1", "stamped")],
                            [ev("run_started", "stamped", T0, attempt=None, ale_version="0.4.0")])
    assert A.load_run(entry(stamped_dir, "stamped", ale_version="0.3.0"))["ale_version"] == "0.4.0"


def test_no_write_help_says_backfill_still_updates_the_index(capsys):
    assert main(["analyze", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "writes no report, findings or ledger rows; --backfill still updates the index" in text
