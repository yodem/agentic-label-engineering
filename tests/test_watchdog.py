from ale.events import make_event, reduce_run
from ale.watchdog import check

RUN = "example-run"
EVID = {"passed": False, "results": [{"id": "A1", "exit": 1, "ok": False, "tail": ""}], "manual": []}


def ev(kind, ts, agent="a1", attempt=1, **kw):
    return make_event(kind, RUN, float(ts), task_id="T01", agent_id=agent, attempt=attempt, **kw)


def names(breaches):
    return sorted(b["breach"] for b in breaches)


def run(events, label, roster, now):
    labels = {"T01": label}
    return check(reduce_run(events, labels), labels, roster, float(now))


def test_quiet_run_has_no_breaches(roster, label_t01):
    assert run([ev("claimed", 0), ev("heartbeat", 100, step="s1")], label_t01, roster, 200) == []


def test_lease_expired_and_not_also_stuck(roster, label_t01):
    got = run([ev("claimed", 0), ev("heartbeat", 10, step="s1")], label_t01, roster, 10 + 901)
    assert names(got) == ["lease_expired"]


def test_stuck_when_heartbeats_continue_without_progress(roster, label_t01):
    events = [ev("claimed", 0)] + [ev("heartbeat", t, step="same") for t in range(100, 1400, 100)]
    assert names(run(events, label_t01, roster, 1350)) == ["stuck"]


def test_overrun(roster, label_t01):
    events = [ev("claimed", 0)] + [ev("heartbeat", t, step="s%d" % t) for t in range(500, 5500, 500)]
    assert "overrun" in names(run(events, label_t01, roster, 5401))


def test_over_budget(roster, label_t01):
    usage = {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 400000, "gen_ai.usage.output_tokens": 1,
             "usage_source": "adapter"}
    assert "over_budget" in names(run([ev("claimed", 0), ev("usage", 1, **usage)], label_t01, roster, 2))


def test_input_required_surfaces_and_does_not_expire(roster, label_t01):
    got = run([ev("claimed", 0), ev("input_required", 1, question="q?")], label_t01, roster, 99999)
    assert names(got) == ["input_required", "overrun"]


def test_rejected_twice_and_exhausted(roster, label_t01):
    events = []
    for i in range(3):
        events += [ev("claimed", i * 10, agent="a%d" % i, attempt=i + 1),
                   ev("submitted", i * 10 + 1, agent="a%d" % i, attempt=i + 1, summary="s"),
                   ev("rejected", i * 10 + 2, agent=None, attempt=i + 1, evidence=EVID, reason="r")]
    assert names(run(events[:6], label_t01, roster, 50)) == ["rejected_twice"]
    assert names(run(events, label_t01, roster, 50)) == ["attempts_exhausted", "rejected_twice"]


def test_seen_breach_not_repeated(roster, label_t01):
    events = [ev("claimed", 0), ev("input_required", 1, question="q?"),
              ev("breach", 2, agent=None, breach="input_required", detail="d")]
    assert names(run(events, label_t01, roster, 3)) == []


def test_submitted_without_verdict_breaches_as_unverified(roster, label_t01):
    events = [ev("claimed", 0), ev("heartbeat", 10, step="s"), ev("submitted", 20, summary="s")]
    assert names(run(events, label_t01, roster, 20 + 900)) == []
    assert names(run(events, label_t01, roster, 20 + 901)) == ["unverified"]
    seen = events + [ev("breach", 1000, agent=None, breach="unverified", detail="d")]
    assert names(run(seen, label_t01, roster, 5000)) == []


def test_run_budget(roster, label_t01):
    roster["cost_gate"]["max_run_budget_tokens"] = 1000
    usage = {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 2000, "gen_ai.usage.output_tokens": 0,
             "usage_source": "adapter"}
    got = run([ev("claimed", 0), ev("usage", 1, **usage)], label_t01, roster, 2)
    assert {"task_id": None, "breach": "run_budget"}.items() <= [b for b in got if b["breach"] == "run_budget"][0].items()
