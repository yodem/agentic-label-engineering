import json
import os
import subprocess

from ale.cli import main
from ale.events import read_events

from test_cli_dispatch import _git_repo, _label, _roster, _run, _dispatch_args


def test_worktree_setup_runs_once_and_receives_checkout_environment(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = json.loads(open(_roster(tmp_path), encoding="utf-8").read())
    roster["worktree_setup_defaults"] = [
        'printf "%s|%s" "$ALE_WORKTREE" "$ALE_CHECKOUT" > setup-env.txt'
    ]
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(roster))
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, str(roster_path), "--spawn", "--cwd", str(repo))) == 0
    worktree = run / "wt" / "T1"
    assert (worktree / "setup-env.txt").read_text() == "%s|%s" % (worktree, repo)
    assert (worktree / ".ale-setup-done").exists()
    (worktree / "setup-env.txt").write_text("sentinel")
    with (run / "events.jsonl").open("a") as handle:
        from ale.events import make_event
        handle.write(json.dumps(make_event("released", "run-1", 2, "T1", None, 1,
                                           reason="retry", spawn_key=["T1", "executor", "ready"])) + "\n")

    assert main(_dispatch_args(run, str(roster_path), "--spawn", "--cwd", str(repo))) == 0
    assert (worktree / "setup-env.txt").read_text() == "sentinel"


def test_failed_worktree_setup_records_breach_and_blocks_spawn(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = json.loads(open(_roster(tmp_path), encoding="utf-8").read())
    roster["worktree_setup_defaults"] = ["exit 17"]
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(roster))
    run = _run(tmp_path, {"T1": _label(mode="per_task")})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")

    assert main(_dispatch_args(run, str(roster_path), "--spawn", "--cwd", str(repo))) == 1
    events = read_events(str(run / "events.jsonl"))
    assert [(event["type"], event.get("breach"), event.get("detail")) for event in events] == [
        ("breach", "worktree_setup_failed", "command=exit 17 exit_code=17")
    ]
    assert "exit 17" in capsys.readouterr().err


def test_setup_outputs_and_marker_are_excluded_from_integrate(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    label = _label(mode="per_task")
    label["context"]["worktree"]["setup_outputs"] = ["node_modules"]
    run = _run(tmp_path, {"T1": label})
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")
    assert main(_dispatch_args(run, roster, "--spawn", "--cwd", str(repo))) == 0
    worktree = run / "wt" / "T1"
    (worktree / ".ale-setup-done").write_text("ok\n")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "pkg.js").write_text("generated\n")
    (worktree / "src").mkdir()
    (worktree / "src" / "T1.py").write_text("allowed\n")
    subprocess.run(["git", "add", "src/T1.py"], cwd=str(worktree), check=True)
    subprocess.run(["git", "commit", "-m", "task changes"], cwd=str(worktree), check=True, capture_output=True)
    with (run / "events.jsonl").open("a") as handle:
        from ale.events import make_event
        for event in (
            make_event("claimed", "run-1", 2, "T1", "agent", 1),
            make_event("submitted", "run-1", 3, "T1", "agent", 1, summary="done"),
            make_event("accepted", "run-1", 4, "T1", None, 1, evidence={"passed": True}),
        ):
            handle.write(json.dumps(event) + "\n")

    assert main(["integrate", "--task", "T1", "--run-dir", str(run), "--roster", roster,
                 "--cwd", str(repo)]) == 0
    assert (repo / "src" / "T1.py").exists()
    assert not (repo / "node_modules").exists()
    assert not (repo / ".ale-setup-done").exists()


def test_lead_note_on_unowned_task_and_executor_note_without_ownership(tmp_path):
    # Construct a minimal run using the shared CLI test fixture layout.
    from test_cli import ROOT
    import shutil
    run_path = tmp_path / "run"
    shutil.copytree(os.path.join(ROOT, "examples", "run"), str(run_path))
    roster = os.path.join(ROOT, "examples", "roster.json")
    assert main(["note", "--task", "T01", "--text", "lead direction", "--run-dir", str(run_path),
                 "--roster", roster]) == 0
    note = read_events(str(run_path / "events.jsonl"))[-1]
    assert note["type"] == "note" and note["agent_id"] is None and note["lead"] is True
    assert main(["claim", "--task", "T01", "--agent", "owner", "--run-dir", str(run_path),
                 "--roster", roster]) == 0
    assert "lead direction" in (run_path / "handoff" / "T01.owner.md").read_text()
    assert main(["note", "--task", "T01", "--agent", "executor", "--text", "unauthorized",
                 "--run-dir", str(run_path), "--roster", roster]) == 4
