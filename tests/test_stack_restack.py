from ale import events as E
from ale import labelset as L
from ale import runner as RUNNER

from stack_fixtures import ale, chain, dispatch, git, label, make_run, work_and_accept


def _events(run):
    return E.read_events(str(run / "events.jsonl"))


def _two_accepted(tmp_path):
    repo, run, roster = chain(tmp_path)
    assert dispatch(run, roster, repo) == 0
    commit_a = work_and_accept(run, roster, "T1")
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T2")
    return repo, run, roster, commit_a


def _reverify_parent(run, roster, text):
    assert ale(run, roster, "reopen", "--task", "T1", "--reason", "review fix") == 0
    (run / "wt" / "T1" / "t1.txt").write_text(text)
    assert ale(run, roster, "verify", "--task", "T1", "--cwd", str(run / "wt" / "T1")) == 0
    return [event for event in _events(run)
            if event["type"] == "accepted" and event["task_id"] == "T1"][-1]["evidence"]["commit"]


def _state(run, task_id):
    labels = L.load_labels(str(run))
    return E.reduce_run(_events(run), labels)["tasks"][task_id]


def test_reopen_restack_and_verify_again_then_integrate_in_order(tmp_path, capsys):
    repo, run, roster, old_a = _two_accepted(tmp_path)
    new_a = _reverify_parent(run, roster, "T1 fixed\n")
    assert new_a != old_a

    assert ale(run, roster, "restack", "--task", "T2") == 0
    assert "restacked T2 onto %s" % new_a[:12] in capsys.readouterr().out
    restacked = [event for event in _events(run) if event["type"] == "restacked"][-1]
    assert (restacked["parent"], restacked["old_base"], restacked["new_base"]) == ("T1", old_a, new_a)
    worktree = run / "wt" / "T2"
    assert git(worktree, "rev-parse", "HEAD~1") == new_a
    assert (worktree / "t1.txt").read_text() == "T1 fixed\n"
    assert _state(run, "T2")["state"] == "submitted"

    assert ale(run, roster, "verify", "--task", "T2", "--cwd", str(worktree)) == 0
    assert ale(run, roster, "integrate", "--task", "T1", "--cwd", str(repo)) == 0
    assert ale(run, roster, "integrate", "--task", "T2", "--cwd", str(repo)) == 0
    assert (repo / "t1.txt").read_text() == "T1 fixed\n"
    assert (repo / "t2.txt").read_text() == "T2 work\n"


def test_restack_conflict_aborts_writes_a_note_and_exits_one(tmp_path, capsys):
    labels = {"T1": label("T1"), "T2": label("T2", ["T1"])}
    labels["T2"]["context"]["allowed_paths"].append("t1.txt")
    repo, run, roster = make_run(tmp_path, labels)
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T1")
    assert dispatch(run, roster, repo) == 0
    worktree = run / "wt" / "T2"
    # T2 also rewrites the parent's file, so rebasing onto T1's new commit conflicts.
    (worktree / "t1.txt").write_text("T2 rewrote this\n")
    work_and_accept(run, roster, "T2")
    head_before = git(worktree, "rev-parse", "HEAD")
    _reverify_parent(run, roster, "T1 fixed differently\n")
    capsys.readouterr()

    assert ale(run, roster, "restack", "--task", "T2") == 1
    assert "failed and was aborted" in capsys.readouterr().err
    assert git(worktree, "rev-parse", "HEAD") == head_before
    assert git(worktree, "status", "--porcelain", "--untracked-files=no") == ""
    notes = [event for event in _events(run) if event["type"] == "note" and event["task_id"] == "T2"]
    assert notes and "restack onto" in notes[-1]["text"]
    assert not any(event["type"] == "restacked" for event in _events(run))


def test_restack_refuses_an_unstacked_task_and_a_parent_that_is_not_accepted(tmp_path, capsys):
    repo, run, roster, _ = _two_accepted(tmp_path)
    assert ale(run, roster, "restack", "--task", "T1") == 1
    assert "not stacked" in capsys.readouterr().err
    assert ale(run, roster, "reopen", "--task", "T1", "--reason", "again") == 0
    assert ale(run, roster, "restack", "--task", "T2") == 1
    assert "verify it before restacking" in capsys.readouterr().err


def test_restack_on_the_current_parent_commit_is_a_no_op(tmp_path, capsys):
    repo, run, roster, _ = _two_accepted(tmp_path)
    before = len(_events(run))
    assert ale(run, roster, "restack", "--task", "T2") == 0
    assert "already built on" in capsys.readouterr().out
    assert len(_events(run)) == before


def test_runner_proposes_restack_then_verify(tmp_path):
    repo, run, roster, _ = _two_accepted(tmp_path)
    _reverify_parent(run, roster, "T1 fixed\n")
    labels = L.load_labels(str(run))
    events = _events(run)
    actions = RUNNER.next_actions(E.reduce_run(events, labels), labels, events)
    assert ("restack", "T2") in actions and ("integrate", "T2") not in actions
    assert ale(run, roster, "restack", "--task", "T2") == 0
    events = _events(run)
    actions = RUNNER.next_actions(E.reduce_run(events, labels), labels, events)
    assert ("verify", "T2") in actions


def test_restack_refuses_a_worktree_with_uncommitted_changes(tmp_path, capsys):
    repo, run, roster, _ = _two_accepted(tmp_path)
    _reverify_parent(run, roster, "T1 fixed\n")
    (run / "wt" / "T2" / "t2.txt").write_text("uncommitted edit\n")
    capsys.readouterr()
    assert ale(run, roster, "restack", "--task", "T2") == 1
    assert "uncommitted changes" in capsys.readouterr().err
    assert not any(event["type"] == "restacked" for event in _events(run))
