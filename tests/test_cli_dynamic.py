import json
import os

from ale import events as E
from ale.cli import main


def test_status_json_has_dynamic_keys(tmp_path, capsys):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = {"schema_version": "1.0", "run_id": "r", "task_id": "T1", "title": "Task T1",
             "labels": {"role": "backend", "model_tier": "standard", "lane": "pane", "risk": "low", "effort": "S"},
             "routing": {"executor": None, "model": None, "resolved_from": None},
             "context": {"spec_path": "x", "pointers": [], "allowed_paths": ["src/**"], "depends_on": []},
             "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}, {"id": "A2", "cmd": "true", "expect": "exit0"}],
             "provenance": {"lane_reason": "planner supplied lane"}}
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    roster = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "roster.json")
    assert main(["status", "--json", "--run-dir", str(run), "--roster", roster, "--now", "0"]) == 0
    row = json.loads(capsys.readouterr().out)["tasks"]["T1"]
    assert {"state", "blocked_by", "assignees", "attempt", "breaches", "lease_expires_ts", "last_step", "fixes", "fixed_by"} <= set(row)
