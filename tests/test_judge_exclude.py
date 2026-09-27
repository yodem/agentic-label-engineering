import json
import os

from ale import events as E
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
