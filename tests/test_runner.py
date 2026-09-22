from ale.runner import next_actions


def test_submitted_task_is_verified_after_latest_submission_even_with_equal_timestamps():
    state = {"tasks": {"T1": {"state": "submitted"}}}
    labels = {"T1": {}}
    events = [{"task_id": "T1", "type": "submitted", "ts": 1},
              {"task_id": "T1", "type": "verified", "ts": 1},
              {"task_id": "T1", "type": "submitted", "ts": 1}]
    assert next_actions(state, labels, events) == [("verify", "T1")]


def test_fix_task_submissions_verify_but_accepted_fixes_do_not_integrate():
    labels = {"T1": {}, "T1.fix1": {"fixes": "T1"}}
    submitted = {"tasks": {"T1": {"state": "fixing"}, "T1.fix1": {"state": "submitted"}}}
    events = [{"task_id": "T1.fix1", "type": "submitted", "ts": 1}]
    assert next_actions(submitted, labels, events) == [("verify", "T1.fix1")]
    accepted = {"tasks": {"T1": {"state": "fixing"}, "T1.fix1": {"state": "accepted"}}}
    assert next_actions(accepted, labels, events) == []


def test_rejected_fixes_retry_parent_until_two_then_breach():
    labels = {"T1": {}, "T1.fix1": {"fixes": "T1"}}
    state = {"tasks": {"T1": {"state": "fixing"}, "T1.fix1": {"state": "rejected"}}}
    assert next_actions(state, labels, []) == [("fix", "T1")]
    labels["T1.fix2"] = {"fixes": "T1"}
    state["tasks"]["T1.fix2"] = {"state": "rejected"}
    assert next_actions(state, labels, []) == [("exhausted", "T1")]


def test_pending_second_fix_is_verified_before_exhaustion():
    labels = {"T1": {}, "T1.fix1": {"fixes": "T1"}, "T1.fix2": {"fixes": "T1"}}
    state = {"tasks": {"T1": {"state": "fixing"},
                        "T1.fix1": {"state": "rejected"},
                        "T1.fix2": {"state": "submitted"}}}
    events = [{"task_id": "T1.fix2", "type": "submitted"}]
    actions = next_actions(state, labels, events)
    assert ("exhausted", "T1") not in actions
    assert ("verify", "T1.fix2") in actions

    state["tasks"]["T1.fix2"]["state"] = "rejected"
    events.append({"task_id": "T1.fix2", "type": "rejected"})
    assert ("exhausted", "T1") in next_actions(state, labels, events)


def test_reopened_task_is_scheduled_for_verification():
    state = {"tasks": {"T1": {"state": "submitted"}}}
    events = [{"task_id": "T1", "type": "reopened"}]
    assert next_actions(state, {"T1": {}}, events) == [("verify", "T1")]
