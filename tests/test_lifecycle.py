import os
import subprocess
import sys

import pytest

from ale.events import append_event, make_event, read_events, reduce_run
from ale.lifecycle import LeaseLost, owner_guard, try_claim

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RACER = """
import json, sys
from ale.lifecycle import try_claim
label = json.load(open(sys.argv[1]))
ok = try_claim(sys.argv[2], {label["task_id"]: label}, label["run_id"], label["task_id"], sys.argv[3], 100.0, 3)
sys.exit(0 if ok else 3)
"""


def test_single_claim_wins(tmp_path, label_t01):
    p = str(tmp_path / "e.jsonl")
    assert try_claim(p, {"T01": label_t01}, "example-run", "T01", "a1", 1.0, 3) is True
    assert try_claim(p, {"T01": label_t01}, "example-run", "T01", "a2", 2.0, 3) is False


def test_claim_refused_past_max_attempts(tmp_path, label_t01):
    p = str(tmp_path / "e.jsonl")
    evid = {"passed": False, "results": [{"id": "A1", "exit": 1, "ok": False, "tail": ""}], "manual": []}
    for i in range(3):
        assert try_claim(p, {"T01": label_t01}, "example-run", "T01", "a%d" % i, float(i), 3)
        append_event(p, make_event("submitted", "example-run", i + 0.1, "T01", "a%d" % i, i + 1, summary="s"))
        append_event(p, make_event("rejected", "example-run", i + 0.2, "T01", None, i + 1, evidence=evid, reason="r"))
    assert try_claim(p, {"T01": label_t01}, "example-run", "T01", "late", 9.0, 3) is False


def test_twenty_racers_exactly_one_winner(tmp_path):
    events = str(tmp_path / "e.jsonl")
    label = os.path.join(ROOT, "examples", "run", "labels", "T01.json")
    procs = [subprocess.Popen([sys.executable, "-c", RACER, label, events, "agent-%02d" % i], cwd=ROOT) for i in range(20)]
    codes = [p.wait() for p in procs]
    assert sorted(set(codes)) == [0, 3] and codes.count(0) == 1
    evs = read_events(events)
    assert all(e["type"] == "claimed" for e in evs) and 1 <= len(evs) <= 20


def test_owner_guard(tmp_path, label_t01):
    p = str(tmp_path / "e.jsonl")
    try_claim(p, {"T01": label_t01}, "example-run", "T01", "a1", 1.0, 3)
    state = reduce_run(read_events(p), {"T01": label_t01})
    owner_guard(state, "T01", "a1")
    with pytest.raises(LeaseLost):
        owner_guard(state, "T01", "a2")
