import json
import os

from ale import events as E
from ale.cli import main
from ale.labeling import exclude as EXCL

from judge_fakes import drive_run, read_calls, setup_repo


def test_root_matches_a_glob_a_directory_prefix_and_a_home_pattern(tmp_path, monkeypatch):
    project = tmp_path / "work" / "secret-repo"
    project.mkdir(parents=True)
    assert EXCL.root_excluded({"judge": {"exclude_paths": [str(tmp_path / "work" / "*")]}}, str(project))
    assert EXCL.root_excluded({"judge": {"exclude_paths": [str(tmp_path / "work")]}}, str(project))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert EXCL.root_excluded({"judge": {"exclude_paths": ["~/work/*"]}}, str(project))
    assert not EXCL.root_excluded({"judge": {"exclude_paths": [str(tmp_path / "other" / "*")]}}, str(project))
    assert not EXCL.root_excluded({"judge": {}}, str(project))
    assert not EXCL.root_excluded({"judge": {"exclude_paths": [str(tmp_path / "work")]}}, str(tmp_path / "workshop"))


def test_excluded_judge_abstains_without_running_anything():
    judge = EXCL.ExcludedJudge("m")
    assert judge.ask("role", "q", ["a: x", "other: none"], "state")["detail"] == {"error": "excluded"}
    assert judge.ask("role", "q", ["a: x"], "state")["value"] is None
    assert judge.noul("large_change", "q", "state") == {
        "key": "large_change", "p": None, "model": "m", "detail": {"error": "excluded"}}


def _exclude(roster_path, patterns):
    with open(roster_path, encoding="utf-8") as handle:
        roster = json.load(handle)
    roster["judge"]["exclude_paths"] = patterns
    with open(roster_path, "w", encoding="utf-8") as handle:
        json.dump(roster, handle)


def test_excluded_project_makes_no_judge_call_and_records_excluded(tmp_path, monkeypatch):
    repo, roster_path, _roster, log_path = setup_repo(tmp_path, monkeypatch, "shadow", "conflict")
    _exclude(roster_path, [repo])
    run_dir, codes = drive_run(repo, roster_path)
    assert read_calls(log_path) == []
    votes = [event for event in E.read_events(os.path.join(run_dir, "events.jsonl"))
             if event["type"] == "shadow_vote"]
    assert votes
    assert all("excluded" in (vote.get("error") or "") for vote in votes)


def test_the_same_run_calls_the_judge_when_nothing_is_excluded(tmp_path, monkeypatch):
    repo, roster_path, _roster, log_path = setup_repo(tmp_path, monkeypatch, "shadow", "conflict")
    _exclude(roster_path, [str(tmp_path / "somewhere-else")])
    drive_run(repo, roster_path)
    assert read_calls(log_path)


def test_explicit_project_cwd_excludes_verify_and_dispatch_judge_calls(tmp_path, monkeypatch):
    repo, roster_path, _roster, log_path = setup_repo(tmp_path, monkeypatch, "shadow", "conflict")
    project_cwd = os.path.join(repo, "wt")
    os.makedirs(project_cwd)
    _exclude(roster_path, [repo])
    run = tmp_path / "outside-run"
    (run / "labels").mkdir(parents=True)
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Secret task",
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline",
                   "risk": "low", "effort": "S"},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["change.txt"],
                    "depends_on": [], "worktree": {"mode": "none", "branch": None, "base": None}},
        "acceptance": [{"id": "A1", "cmd": "false", "expect": "exit0"}],
        "assignments": [{"kind": "monitor", "role": "monitor", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "on_breach"}],
    }
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    events = str(run / "events.jsonl")
    E.append_event(events, E.make_event("run_started", "run-1", 1))
    E.append_event(events, E.make_event("breach", "run-1", 2, "T1", None, 1,
                                        breach="stuck", detail="no progress"))
    E.append_event(events, E.make_event("claimed", "run-1", 3, "T1", "worker", 1))
    E.append_event(events, E.make_event("submitted", "run-1", 4, "T1", "worker", 1, summary="done"))
    monkeypatch.chdir(tmp_path)

    common = ["--run-dir", str(run), "--roster", roster_path]
    assert main(["verify", "--task", "T1", "--cwd", project_cwd] + common) == 1
    assert read_calls(log_path) == []
    assert main(["dispatch", "--spawn", "--cwd", project_cwd] + common) == 0
    assert read_calls(log_path) == []
