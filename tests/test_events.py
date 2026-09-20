import pytest

from ale.events import EventError, append_event, check_event, make_event, read_events, reduce_run

RUN = "example-run"


def ev(kind, ts, task="T01", agent="a1", attempt=1, **kw):
    return make_event(kind, RUN, float(ts), task_id=task, agent_id=agent, attempt=attempt, **kw)


EVID = {"passed": True, "results": [{"id": "A1", "exit": 0, "ok": True, "tail": ""}], "manual": []}


def labels_of(*ls):
    return {l["task_id"]: l for l in ls}


def test_roundtrip(tmp_path):
    p = str(tmp_path / "events.jsonl")
    append_event(p, ev("claimed", 1))
    append_event(p, ev("heartbeat", 2, step="wrote tests"))
    got = read_events(p)
    assert [e["type"] for e in got] == ["claimed", "heartbeat"] and got[1]["step"] == "wrote tests"


def test_missing_file_reads_empty(tmp_path):
    assert read_events(str(tmp_path / "nope.jsonl")) == []


def test_invalid_event_refused(tmp_path):
    with pytest.raises(EventError):
        append_event(str(tmp_path / "e.jsonl"), ev("heartbeat", 1))  # no step


def test_accepted_without_evidence_refused():
    assert check_event(ev("accepted", 1, evidence={})) != []
    assert check_event(ev("accepted", 1, evidence=EVID)) == []


def test_oversize_event_refused(tmp_path):
    with pytest.raises(EventError):
        append_event(str(tmp_path / "e.jsonl"), ev("note", 1, text="x" * 5000))


def test_torn_tail_ignored(tmp_path):
    p = tmp_path / "e.jsonl"
    append_event(str(p), ev("claimed", 1))
    with open(str(p), "ab") as f:
        f.write(b'{"type":"heartb')
    assert len(read_events(str(p))) == 1


def test_happy_path(label_t01):
    st = reduce_run([
        ev("claimed", 10), ev("heartbeat", 20, step="s1", files_modified=["src/auth/a.py"]),
        ev("heartbeat", 30, step="s1"), ev("heartbeat", 40, step="s2"),
        ev("submitted", 50, summary="done"), ev("accepted", 60, agent=None, evidence=EVID),
    ], labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "accepted" and st["owner"] is None
    assert st["steps"] == ["s1", "s2"] and st["files_modified"] == ["src/auth/a.py"]
    assert st["step_changed_ts"] == 40.0 and st["last_heartbeat_ts"] == 40.0


def test_planned_becomes_ready_and_dependency_blocks(label_t01, label_t02):
    out = reduce_run([ev("claimed", 5, task="T02", agent="b1")], labels_of(label_t01, label_t02))["tasks"]
    assert out["T01"]["state"] == "ready" and out["T01"]["claimable"]
    assert out["T02"]["state"] == "planned" and out["T02"]["owner"] is None and not out["T02"]["claimable"]


def test_second_claimant_ignored(label_t01):
    st = reduce_run([ev("claimed", 1, agent="a1"), ev("claimed", 1.5, agent="a2")], labels_of(label_t01))["tasks"]["T01"]
    assert st["owner"] == "a1"


def test_non_owner_heartbeat_ignored(label_t01):
    st = reduce_run([ev("claimed", 1), ev("heartbeat", 2, agent="intruder", step="x")], labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "claimed" and st["last_step"] is None


def test_rejection_frees_task_and_bumps_attempt(label_t01):
    st = reduce_run([
        ev("claimed", 1), ev("submitted", 2, summary="s"),
        ev("rejected", 3, agent=None, evidence=EVID, reason="A1 failed"),
    ], labels_of(label_t01))["tasks"]["T01"]
    assert (st["state"], st["attempt"], st["rejections"], st["owner"], st["claimable"]) == ("rejected", 2, 1, None, True)


def test_lease_expiry_then_release_keeps_progress(label_t01):
    st = reduce_run([
        ev("claimed", 1), ev("heartbeat", 2, step="half way"),
        ev("lease_expired", 1000, agent=None), ev("released", 1000, agent=None),
    ], labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "released" and st["claimable"] and st["last_step"] == "half way"


def test_input_required_roundtrip(label_t01):
    base = [ev("claimed", 1), ev("input_required", 2, question="which port?")]
    st = reduce_run(base, labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "input-required" and st["waiting_on"] == "which port?"
    st = reduce_run(base + [ev("input_answered", 9, agent=None, text="8080")], labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "working" and st["waiting_on"] is None and st["last_heartbeat_ts"] == 9.0


def test_usage_and_breaches_recorded(label_t01):
    usage = {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20,
             "usage_source": "adapter", "cost_usd": 0.5}
    out = reduce_run([ev("claimed", 1), ev("usage", 2, **usage), ev("breach", 3, agent=None, breach="stuck", detail="d"),
                      make_event("breach", RUN, 4.0, breach="run_budget", detail="d")], labels_of(label_t01))
    assert out["tasks"]["T01"]["tokens"] == 120 and out["run"]["tokens"] == 120 and out["run"]["cost_usd"] == 0.5
    assert out["tasks"]["T01"]["breaches_seen"] == [["stuck", 1]] and out["run"]["breaches_seen"] == ["run_budget"]


def test_submitted_ts_recorded_and_reset_on_reclaim(label_t01):
    base = [ev("claimed", 1), ev("submitted", 7, summary="s")]
    assert reduce_run(base, labels_of(label_t01))["tasks"]["T01"]["submitted_ts"] == 7.0
    again = base + [ev("rejected", 8, agent=None, evidence=EVID, reason="r"), ev("claimed", 9, agent="a2", attempt=2)]
    st = reduce_run(again, labels_of(label_t01))["tasks"]["T01"]
    assert st["owner"] == "a2" and st["submitted_ts"] is None


def test_non_owner_cannot_fail_or_cancel(label_t01):
    for kind, extra in (("failed", {"reason": "because"}), ("canceled", {})):
        st = reduce_run([ev("claimed", 1), ev("heartbeat", 2, step="s1"), ev(kind, 3, agent="mallory", **extra)],
                        labels_of(label_t01))["tasks"]["T01"]
        assert st["state"] == "working" and st["owner"] == "a1"


def test_owner_cannot_fail_or_accept_own_task(label_t01):
    base = [ev("claimed", 1), ev("submitted", 2, summary="s")]
    for forged in (ev("accepted", 3, agent="a1", evidence=EVID), ev("rejected", 3, agent="a1", evidence=EVID, reason="r"),
                   ev("failed", 3, agent="a1", reason="r"), ev("verified", 3, agent="a1", evidence=EVID)):
        st = reduce_run(base + [forged], labels_of(label_t01))["tasks"]["T01"]
        assert st["state"] == "submitted" and st["owner"] == "a1" and st["evidence"] is None and st["rejections"] == 0


def test_system_can_fail_and_cancel(label_t01):
    for kind, extra in (("failed", {"reason": "attempts_exhausted"}), ("canceled", {})):
        st = reduce_run([ev("claimed", 1), ev(kind, 2, agent=None, **extra)], labels_of(label_t01))["tasks"]["T01"]
        assert st["state"] == kind and st["owner"] is None


def test_forged_release_and_answer_ignored(label_t01):
    st = reduce_run([ev("claimed", 1), ev("lease_expired", 2, agent="mallory")], labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "claimed" and st["owner"] == "a1"
    st = reduce_run([ev("claimed", 1), ev("input_required", 2, question="q?"), ev("input_answered", 3, agent="mallory", text="x")],
                    labels_of(label_t01))["tasks"]["T01"]
    assert st["state"] == "input-required"
