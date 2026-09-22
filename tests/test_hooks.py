from ale.hooks import EDIT_TOOLS, decide_heartbeat, decide_pre_tool, decide_stop, session_context, target_paths


def binding(agent="a1"):
    return {"agent_id": agent}


def label(paths=("src/**",), acceptance=None):
    return {"task_id": "T01", "title": "Build thing", "context": {"allowed_paths": list(paths)},
            "acceptance": acceptance or [{"id": "A1", "cmd": "pytest", "expect": "exit0"}]}


def live(owner="a1", heartbeat=10):
    return {"owner": owner, "state": "working", "last_heartbeat_ts": heartbeat, "attempt": 1}


def test_edit_tools_constant_and_target_paths():
    assert EDIT_TOOLS == ("Edit", "Write", "MultiEdit", "NotebookEdit")
    assert target_paths("Edit", {"file_path": "src/a.py"}) == ["src/a.py"]
    assert target_paths("Write", {"file_path": "src/b.py"}) == ["src/b.py"]


def test_multi_edit_and_notebook_paths():
    assert target_paths("MultiEdit", {"edits": [{"file_path": "a"}, {"file_path": "b"}]}) == ["a", "b"]
    assert target_paths("NotebookEdit", {"notebook_path": "notes/x.ipynb"}) == ["notes/x.ipynb"]


def test_non_edit_has_no_targets():
    assert target_paths("Bash", {"file_path": "src/a.py"}) == []


def test_apply_patch_paths_are_reported():
    patch = "*** Begin Patch\n*** Update File: src/a.py\n*** Add File: docs/new.md\n*** End Patch"
    assert target_paths("apply_patch", {"command": patch}) == ["src/a.py", "docs/new.md"]


def test_exec_command_heredoc_path_is_reported():
    command = "cat > src/generated.py <<'EOF'\nvalue = 1\nEOF"
    assert target_paths("exec_command", {"cmd": command}) == ["src/generated.py"]


def test_write_file_path_shape_is_reported():
    assert target_paths("write_file", {"path": "src/generated.py", "content": "value"}) == ["src/generated.py"]


def test_edit_tool_path_alias_is_reported():
    assert target_paths("Write", {"path": "src/generated.py", "content": "value"}) == ["src/generated.py"]


def test_unknown_command_shape_does_not_guess_paths():
    assert target_paths("exec_command", {"cmd": "echo src/not-a-target.py"}) == []


def test_lease_lost_denies_every_tool():
    result = decide_pre_tool(binding("a1"), label(), live("a2"), "Bash", {}, "/project")
    assert result["action"] == "deny" and "exit 4" in result["reason"] and "stop" in result["reason"].lower()


def test_edit_inside_allowed_path_is_allowed():
    assert decide_pre_tool(binding(), label(), live(), "Edit", {"file_path": "src/a.py"}, "/project")["action"] == "allow"


def test_edit_outside_allowed_path_is_denied():
    result = decide_pre_tool(binding(), label(), live(), "Edit", {"file_path": "README.md"}, "/project")
    assert result["action"] == "deny" and "README.md" in result["reason"]


def test_absolute_path_inside_project_is_relativised():
    result = decide_pre_tool(binding(), label(), live(), "Write", {"file_path": "/project/src/a.py"}, "/project")
    assert result["action"] == "allow"


def test_path_outside_project_is_denied():
    result = decide_pre_tool(binding(), label(paths=("**",)), live(), "Edit", {"file_path": "/other/a.py"}, "/project")
    assert result["action"] == "deny"


def test_parent_segments_are_checked():
    result = decide_pre_tool(binding(), label(paths=("src/**",)), live(), "Edit", {"file_path": "src/../README.md"}, "/project")
    assert result["action"] == "deny"


def test_non_edit_tool_allowed_when_lease_holds():
    assert decide_pre_tool(binding(), label(), live(), "Bash", {"command": "echo hi"}, "/project")["action"] == "allow"


def test_heartbeat_younger_than_throttle_is_none():
    assert decide_heartbeat(live(heartbeat=90), 100, 11, "Edit", {"file_path": "src/a.py"}) is None


def test_heartbeat_at_throttle_boundary_emits():
    assert decide_heartbeat(live(heartbeat=90), 100, 10, "Edit", {"file_path": "src/a.py"})["files"] == ["src/a.py"]


def test_heartbeat_without_previous_heartbeat_emits():
    assert decide_heartbeat(live(heartbeat=None), 100, 60, "Bash", {})["step"].startswith("auto: Bash")


def test_auto_step_is_truncated_to_200():
    result = decide_heartbeat(live(heartbeat=0), 100, 1, "Edit", {"file_path": "x" * 400})
    assert len(result["step"]) == 200


def test_stop_submits_passed_live_task():
    result = decide_stop(label(), live(), {"passed": True, "results": [], "manual": []}, 0, 2)
    assert result["action"] == "submit" and result["summary"]


def test_stop_blocks_failed_acceptance():
    result = decide_stop(label(), live(), {"passed": False, "results": [{"id": "A1", "ok": False, "tail": "bad"}], "manual": []}, 0, 2)
    assert result["action"] == "block" and "A1" in result["reason"] and "bad" in result["reason"]


def test_stop_requests_input_after_blocks_exhausted():
    result = decide_stop(label(), live(), {"passed": False, "results": [{"id": "A1", "ok": False, "tail": "bad"}], "manual": []}, 2, 2)
    assert result["action"] == "input_required" and result["question"]


def test_stop_does_nothing_for_non_live_task():
    assert decide_stop(label(), dict(live(), state="accepted"), {"passed": False, "results": [], "manual": []}, 0, 2) == {"action": "none"}


def test_stop_does_nothing_for_submitted_task():
    assert decide_stop(label(), dict(live(), state="submitted"), {"passed": True, "results": [], "manual": []}, 0, 2) == {"action": "none"}


def test_manual_acceptance_does_not_block():
    result = decide_stop(label(acceptance=[{"id": "M1", "manual": "review"}],), live(),
                         {"passed": True, "results": [], "manual": ["M1"]}, 0, 2)
    assert result["action"] == "submit"


def test_session_context_contains_required_sections_and_cap():
    text = session_context(label(), "previous handoff", "a decision",)
    assert len(text) <= 6000
    assert "T01" in text and "Build thing" in text and "src/**" in text
    assert "pytest" in text and "previous handoff" in text and "a decision" in text


def test_session_context_handles_missing_prior_text():
    text = session_context(label(), "", "")
    assert "Executor rules" in text and "Prior handoff" in text
