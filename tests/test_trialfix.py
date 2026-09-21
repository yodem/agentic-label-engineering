import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

from ale.bake import bake, compile_plan, render_block
from ale.cli import Ctx, main
from ale.dispatch import due_assignments
from ale.events import append_event, make_event, reduce_run
from ale.handoff import write_atomic
from ale.labeling.merge import merge
from ale.roster import load_roster


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text(open(ROOT + "/examples/roster.json", encoding="utf-8").read())
    return str(path)


def _label(task_id="T1", run_id="run-1", allowed=None):
    return {
        "schema_version": "1.0", "run_id": run_id, "task_id": task_id, "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "plan", "pointers": [], "allowed_paths": allowed or ["src/%s.py" % task_id], "depends_on": [],
                    "worktree": {"mode": "none", "branch": None, "base": None, "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}, {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The planner selected this lane."},
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard", "executor": None, "trigger": "ready"}],
    }


def test_bare_write_atomic_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_atomic("bare.txt", "ok")
    assert (tmp_path / "bare.txt").read_text() == "ok"


def test_plan_commands_use_environment_run_dir(tmp_path, monkeypatch):
    roster = _roster(tmp_path)
    plan = tmp_path / "plan.md"
    label = _label()
    plan.write_text("## Task 1: One\n" + render_block(label) + "\n## Task 2: Two\n" +
                    render_block(dict(label, task_id="T2", context=dict(label["context"], allowed_paths=["src/T2.py"]))))
    run = tmp_path / "run"
    monkeypatch.setenv("ALE_RUN_DIR", str(run))
    monkeypatch.setenv("ALE_ROSTER", roster)
    assert main(["plan", "compile", str(plan)]) == 0
    assert (run / "labels" / "T1.json").exists()


def test_rejection_then_accepted_fix_allows_parent_acceptance():
    labels = {"T1": _label(), "T1.fix1": dict(_label("T1.fix1"), fixes="T1")}
    ev = [
        make_event("claimed", "run-1", 1, "T1", "a", 1),
        make_event("submitted", "run-1", 2, "T1", "a", 1, summary="s"),
        make_event("rejected", "run-1", 3, "T1", None, 1, evidence={"results": []}, reason="bad"),
        make_event("claimed", "run-1", 4, "T1.fix1", "f", 1),
        make_event("submitted", "run-1", 5, "T1.fix1", "f", 1, summary="fixed"),
        make_event("accepted", "run-1", 6, "T1.fix1", None, 1, evidence={"results": []}),
        make_event("verified", "run-1", 7, "T1", None, 2, evidence={"results": []}),
        make_event("accepted", "run-1", 8, "T1", None, 2, evidence={"results": []}),
    ]
    assert reduce_run(ev, labels)["tasks"]["T1"]["state"] == "accepted"


def test_overridden_executor_uses_its_own_model(tmp_path):
    roster = load_roster(_roster(tmp_path))
    roster["routing"].insert(0, {"role": "backend", "model_tier": "standard", "executor": "codex-exec", "model": "codex-model"})
    labels = {"T1": dict(_label(), assignments=[{"kind": "executor", "role": "backend", "model_tier": "standard",
                                                   "executor": "codex-exec", "trigger": "ready"}])}
    state = {"tasks": {"T1": {"state": "ready", "attempt": 1}}, "spawned": []}
    assert due_assignments(state, labels, roster)[0]["model"] == "codex-model"


def test_default_vote_is_lower_precedence():
    votes = [{"field": "role", "value": "backend", "by": "default", "confidence": 1},
             {"field": "role", "value": "docs", "by": "rule:0", "confidence": 1}]
    assert merge("role", votes, "shadow", 0.75)["by"] == "rule:0"


def test_compact_block_omits_defaults_and_compiles_defaults_back():
    label = _label()
    label["routing"] = {"executor": None, "model": None, "resolved_from": None}
    label["provenance"]["role"] = {"by": "default", "votes": [{"by": "default", "value": "backend"}]}
    block = render_block(label)
    assert '"run_id"' not in block and '"schema_version"' not in block
    assert "provenance" not in block
    text = "## Task 1: One\n" + block + "\n## Task 2: Two\n" + render_block(dict(label, task_id="T2"))
    assert compile_plan(text)["T1"]["run_id"] == "run-1"
    assert bake(text, {"T1": label, "T2": dict(label, task_id="T2")}) == bake(text, {"T1": label, "T2": dict(label, task_id="T2")})


def test_superpowers_baked_blocks_have_hard_line_limit(tmp_path):
    plan = tmp_path / "superpowers.md"
    shutil.copyfile(ROOT + "/tests/fixtures/plans/superpowers.md", str(plan))
    assert main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", ROOT + "/examples/roster.json"]) == 1
    blocks = re.findall(r"```ale-label\n(.*?)\n```", plan.read_text(), re.S)
    assert blocks and all(len(block.splitlines()) <= 22 for block in blocks)
    assert (tmp_path / "superpowers.md.ale-provenance.json").exists()


def test_timeline_formats_fractional_time_and_evidence():
    from ale.timeline import format_timeline, timeline
    rows = timeline([make_event("verified", "r", 10, "T1", None, 1,
                                evidence={"results": [{"id": "A1", "ok": True}, {"id": "A2", "ok": False}]})], {})
    line = format_timeline(rows)[0]
    assert "+0s" in line and "A1 ok, A2 FAIL" in line and len(line) <= 160


def test_spawn_request_models_are_usage_aware(tmp_path):
    request = {"agent_id": "T1-executor-backend-1", "task_id": "T1", "kind": "executor", "role": "backend",
               "executor": "codex-exec", "model": "m", "cwd": str(tmp_path), "prompt_file": "p",
               "env": {"ALE_TASK": "T1", "ALE_AGENT": "a", "ALE_RUN_DIR": str(tmp_path), "ALE_ROSTER": "r"}}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    proc = subprocess.run([ROOT + "/bin/ale-spawn", str(path)], env=dict(os.environ, ALE_SPAWN_DRY="1"),
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--usage-from codex-json" in proc.stdout and "codex exec --json" in proc.stdout


def test_integrate_commits_allowed_worktree_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=str(repo), check=True)
    (repo / "base").write_text("base\n")
    subprocess.run(["git", "add", "base"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(repo), check=True, capture_output=True)
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    label = _label(allowed=["src/T1.py"])
    label["context"]["worktree"]["mode"] = "per_task"
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    worktree = run / "wt" / "T1"
    subprocess.run(["git", "worktree", "add", str(worktree), "-b", "ale/run-1/T1", "HEAD"],
                   cwd=str(repo), check=True, capture_output=True)
    (worktree / "src").mkdir()
    (worktree / "src" / "T1.py").write_text("ok\n")
    events = [
        make_event("spawned", "run-1", 1, "T1", None, 1, agent_id_minted="a",
                   assignment_kind="executor", executor="claude-headless", model="m",
                   worktree=str(worktree), branch="ale/run-1/T1"),
        make_event("claimed", "run-1", 2, "T1", "a", 1),
        make_event("submitted", "run-1", 3, "T1", "a", 1, summary="done"),
        make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"results": []}),
    ]
    for event in events:
        append_event(str(run / "events.jsonl"), event)
    roster = _roster(tmp_path)
    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert (repo / "src" / "T1.py").read_text() == "ok\n"
