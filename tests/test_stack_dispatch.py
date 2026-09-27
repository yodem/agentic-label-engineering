from ale.dispatch import held_for_integration

from stack_fixtures import ale, chain, dispatch, git, label, make_run, spawned, work_and_accept


def test_a_stacked_chain_dispatches_each_child_from_its_parents_accepted_commit(tmp_path, capsys):
    repo, run, roster = chain(tmp_path)
    assert dispatch(run, roster, repo) == 0
    assert [e["task_id"] for e in spawned(run, "T1")] == ["T1"]
    assert spawned(run, "T2") == []
    commit_a = work_and_accept(run, roster, "T1")

    assert dispatch(run, roster, repo) == 0
    assert "holding T2" not in capsys.readouterr().err
    event_b = spawned(run, "T2")[-1]
    assert event_b["stack_parent"] == "T1" and event_b["base_commit"] == commit_a
    assert git(run / "wt" / "T2", "rev-parse", "HEAD") == commit_a
    assert (run / "wt" / "T2" / "t1.txt").read_text() == "T1 work\n"
    commit_b = work_and_accept(run, roster, "T2")

    assert dispatch(run, roster, repo) == 0
    event_c = spawned(run, "T3")[-1]
    assert event_c["stack_parent"] == "T2" and event_c["base_commit"] == commit_b
    assert (run / "wt" / "T3" / "t2.txt").read_text() == "T2 work\n"
    # Nothing was integrated to get here.
    assert not (repo / "t1.txt").exists()


def test_unstacked_dependent_is_still_held_until_integration(tmp_path, capsys):
    repo, run, roster = make_run(tmp_path, {"T1": label("T1"), "T2": label("T2", ["T1"], stack=False)})
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T1")
    assert dispatch(run, roster, repo) == 0
    assert "holding T2: dependency T1 is accepted but not integrated" in capsys.readouterr().err
    assert spawned(run, "T2") == []


def test_two_dependencies_hold_even_when_the_label_says_stack(tmp_path, capsys):
    labels = {"T1": label("T1"), "T2": label("T2"), "T3": label("T3", ["T1", "T2"])}
    labels["T3"]["context"]["worktree"]["stack"] = True
    repo, run, roster = make_run(tmp_path, labels)
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T1")
    work_and_accept(run, roster, "T2")
    assert dispatch(run, roster, repo) == 0
    assert "holding T3" in capsys.readouterr().err
    assert spawned(run, "T3") == []


def test_a_parent_accepted_without_a_commit_still_holds_its_child():
    labels = {"T1": label("T1"), "T2": label("T2", ["T1"])}
    state = {"tasks": {"T1": {"state": "accepted", "integrated": False, "evidence": {"passed": True}},
                       "T2": {"state": "ready"}}}
    assert held_for_integration(state, labels) == [("T2", "T1")]
    state["tasks"]["T1"]["evidence"]["commit"] = "a" * 40
    assert held_for_integration(state, labels) == []


def test_a_child_of_an_integrated_parent_starts_from_head_without_stack_fields(tmp_path):
    repo, run, roster = make_run(tmp_path, {"T1": label("T1"), "T2": label("T2", ["T1"])})
    assert dispatch(run, roster, repo) == 0
    work_and_accept(run, roster, "T1")
    assert ale(run, roster, "integrate", "--task", "T1", "--cwd", str(repo)) == 0
    assert dispatch(run, roster, repo) == 0
    event = spawned(run, "T2")[-1]
    assert "stack_parent" not in event and "base_commit" not in event
    assert git(run / "wt" / "T2", "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
