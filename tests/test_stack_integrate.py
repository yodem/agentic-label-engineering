from ale import events as E
from ale import runner as RUNNER

from stack_fixtures import ale, chain, dispatch, git, work_and_accept


def _events(run):
    return E.read_events(str(run / "events.jsonl"))


def _accepted_chain(tmp_path):
    repo, run, roster = chain(tmp_path)
    commits = {}
    for task_id in ("T1", "T2", "T3"):
        assert dispatch(run, roster, repo) == 0
        commits[task_id] = work_and_accept(run, roster, task_id)
    return repo, run, roster, commits


def test_integrating_a_child_before_its_parent_exits_one(tmp_path, capsys):
    repo, run, roster, _ = _accepted_chain(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    assert ale(run, roster, "integrate", "--task", "T2", "--cwd", str(repo)) == 1
    assert "integrate T1 first" in capsys.readouterr().err
    assert ale(run, roster, "integrate", "--task", "T3", "--cwd", str(repo)) == 1
    assert "integrate T2 first" in capsys.readouterr().err
    assert git(repo, "rev-parse", "HEAD") == before
    assert not any(event["type"] == "integrated" for event in _events(run))


def test_a_stacked_chain_integrates_in_order(tmp_path):
    repo, run, roster, _ = _accepted_chain(tmp_path)
    for task_id in ("T1", "T2", "T3"):
        assert ale(run, roster, "integrate", "--task", task_id, "--cwd", str(repo)) == 0
    for name in ("t1.txt", "t2.txt", "t3.txt"):
        assert (repo / name).exists()
    files = {event["task_id"]: event["files"] for event in _events(run) if event["type"] == "integrated"}
    assert files == {"T1": ["t1.txt"], "T2": ["t2.txt"], "T3": ["t3.txt"]}


def test_reopening_an_accepted_parent_flags_each_unintegrated_child(tmp_path, capsys):
    repo, run, roster, commits = _accepted_chain(tmp_path)
    assert ale(run, roster, "reopen", "--task", "T1", "--reason", "review found a bug") == 0
    assert "restack needed: T2 is stacked on T1" in capsys.readouterr().out
    needed = [event for event in _events(run) if event["type"] == "restack_needed"]
    assert [(event["task_id"], event["parent"], event["base_commit"]) for event in needed] == [
        ("T2", "T1", commits["T1"])]


def test_reopening_an_unstacked_or_childless_task_emits_nothing(tmp_path):
    repo, run, roster, _ = _accepted_chain(tmp_path)
    assert ale(run, roster, "reopen", "--task", "T3", "--reason", "leaf") == 0
    assert not any(event["type"] == "restack_needed" for event in _events(run))


def test_integrate_refuses_a_child_built_on_a_stale_parent_commit(tmp_path, capsys):
    repo, run, roster, commits = _accepted_chain(tmp_path)
    assert ale(run, roster, "reopen", "--task", "T1", "--reason", "fix") == 0
    (run / "wt" / "T1" / "t1.txt").write_text("T1 fixed\n")
    assert ale(run, roster, "verify", "--task", "T1", "--cwd", str(run / "wt" / "T1")) == 0
    assert ale(run, roster, "integrate", "--task", "T1", "--cwd", str(repo)) == 0
    capsys.readouterr()
    assert ale(run, roster, "integrate", "--task", "T2", "--cwd", str(repo)) == 1
    err = capsys.readouterr().err
    assert "run ale restack --task T2" in err and commits["T1"][:12] in err


def test_runner_does_not_propose_integrating_a_child_before_its_parent(tmp_path):
    repo, run, roster, _ = _accepted_chain(tmp_path)
    from ale import labelset as L
    labels = L.load_labels(str(run))
    events = _events(run)
    actions = RUNNER.next_actions(E.reduce_run(events, labels), labels, events)
    assert actions == [("integrate", "T1")]
