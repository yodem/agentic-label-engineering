import os

import pytest

from ale.events import make_event, reduce_run
from ale.handoff import handoff_path, render, write_atomic


def test_render_has_seven_fields_in_order(label_t01):
    evs = [make_event("claimed", "example-run", 1.0, "T01", "a1", 1),
           make_event("heartbeat", "example-run", 2.0, "T01", "a1", 1, step="wrote tests",
                      files_modified=["src/auth/a.py"], pending=["docs: not started"], next_steps=["n1", "n2", "n3", "n4"]),
           make_event("input_required", "example-run", 3.0, "T01", "a1", 1, question="which port?")]
    st = reduce_run(evs, {"T01": label_t01})["tasks"]["T01"]
    text = render("T01", "a1", label_t01, st)
    heads = ["status:", "## Summary", "## Files modified", "## Completed", "## Pending", "## Next steps", "## Waiting on"]
    positions = [text.index(h) for h in heads]
    assert positions == sorted(positions)
    assert "status: blocked" in text and "which port?" in text and "src/auth/a.py" in text
    assert "n3" in text and "n4" not in text


def test_status_mapping(label_t01):
    base = {"T01": label_t01}
    st = reduce_run([make_event("claimed", "example-run", 1.0, "T01", "a1", 1)], base)["tasks"]["T01"]
    assert "status: in_progress" in render("T01", "a1", label_t01, st)
    st["state"] = "accepted"
    assert "status: done" in render("T01", "a1", label_t01, st)
    st["state"] = "failed"
    assert "status: failed" in render("T01", "a1", label_t01, st)


def test_write_atomic_replaces_and_leaves_no_temp(tmp_path):
    p = handoff_path(str(tmp_path), "T01", "a1")
    write_atomic(p, "one")
    write_atomic(p, "two")
    assert open(p).read() == "two" and os.listdir(os.path.dirname(p)) == ["T01.a1.md"]


@pytest.mark.parametrize("task,agent", [("T01", "../x"), ("T01", "a/b"), ("T01", ".."), ("T01", ""), ("../T01", "a1"),
                                        ("T01", "a" * 65), ("T01", "-x"), ("T01", "a\\b")])
def test_handoff_path_rejects_unsafe_ids(tmp_path, task, agent):
    with pytest.raises(ValueError):
        handoff_path(str(tmp_path), task, agent)
