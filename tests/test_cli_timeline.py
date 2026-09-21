import json
import os

from ale.cli import main
from ale.events import append_event, make_event


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(tmp_path):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    with open(ROOT + "/examples/run/labels/T01.json", encoding="utf-8") as f:
        label = json.load(f)
    (run / "labels" / "T01.json").write_text(json.dumps(label))
    roster = tmp_path / "roster.json"
    with open(ROOT + "/examples/roster.json", encoding="utf-8") as f:
        roster.write_text(f.read())
    return run, roster


def _event(run, kind, ts, task="T01", agent="a", **extra):
    if kind == "label_changed":
        extra.setdefault("reason", "test")
    if kind == "spawned":
        extra.update({"agent_id_minted": "agent-1", "assignment_kind": "executor",
                      "executor": "claude-headless", "model": "m"})
    if kind == "usage":
        extra.update({"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": extra.pop("input_tokens"),
                      "gen_ai.usage.output_tokens": extra.pop("output_tokens"), "usage_source": "test",
                      "gen_ai.usage.cache_creation_input_tokens": extra.pop("cache_creation_input_tokens", 0)})
    if kind in ("spawned", "integrated", "label_changed"):
        agent = None
    append_event(str(run / "events.jsonl"), make_event(kind, "run-1", ts, task, agent, 1, **extra))


def test_timeline_orders_events_and_shows_label_changes(tmp_path, capsys):
    run, roster = _run(tmp_path)
    _event(run, "label_changed", 3, field="role", old="backend", new="frontend")
    _event(run, "claimed", 1)
    assert main(["timeline", "--run-dir", str(run), "--roster", str(roster)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("+0s claimed")
    assert "role: backend -> frontend" in lines[1]


def test_timeline_task_filter(tmp_path, capsys):
    run, roster = _run(tmp_path)
    _event(run, "claimed", 1, "T01")
    _event(run, "claimed", 2, "T02")
    assert main(["timeline", "--task", "T01", "--run-dir", str(run), "--roster", str(roster)]) == 0
    output = capsys.readouterr().out
    assert "T01" in output and "T02" not in output


def test_timeline_json_output(tmp_path, capsys):
    run, roster = _run(tmp_path)
    _event(run, "claimed", 1)
    assert main(["timeline", "--json", "--run-dir", str(run), "--roster", str(roster)]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["type"] == "claimed"


def test_meta_totals_equal_usage_sum(tmp_path, capsys):
    run, roster = _run(tmp_path)
    _event(run, "usage", 1, input_tokens=4, output_tokens=3,
           cache_read_input_tokens=2, cache_creation_input_tokens=1)
    assert main(["meta", "--json", "--run-dir", str(run), "--roster", str(roster)]) == 0
    totals = json.loads(capsys.readouterr().out)["totals"]["tokens"]
    assert totals == {"input": 4, "output": 3, "cache_read": 2, "cache_write": 1}


def test_meta_csv_has_header(tmp_path, capsys):
    run, roster = _run(tmp_path)
    assert main(["meta", "--csv", "--run-dir", str(run), "--roster", str(roster)]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("kind,id,input,output,cache_read,cache_write")


def test_status_plain_includes_worktree_and_integrated(tmp_path, capsys):
    run, roster = _run(tmp_path)
    _event(run, "spawned", 1, worktree="/tmp/wt/T01", branch="ale/run-1/T01")
    _event(run, "integrated", 2, commit="abc")
    assert main(["status", "--run-dir", str(run), "--roster", str(roster)]) == 0
    output = capsys.readouterr().out
    assert "wt=" + os.path.relpath("/tmp/wt/T01", str(run)) in output
    assert "branch=ale/run-1/T01" in output and "integrated=yes" in output
