import subprocess

from ale.hooks import decide_pre_tool, decide_stop
from ale.verify import paths_within, run_required


def test_denied_path_overrides_allowed_path():
    assert paths_within(["src/secret.py"], ["src/**"], ["src/secret.py"]) == ["src/secret.py"]


def test_allowed_path_not_denied_is_permitted():
    assert paths_within(["src/a.py"], ["src/**"], ["private/**"]) == []


def test_denied_path_is_checked_after_allowed_check():
    assert paths_within(["private/a.py"], ["src/**"], ["private/**"]) == ["private/a.py"]


def test_pre_tool_rejects_deny_tools():
    result = decide_pre_tool({"agent_id": "a"}, {"context": {}, "effective_rules": {"deny_tools": ["Bash"]}},
                             {"owner": "a"}, "Bash", {}, ".")
    assert result["action"] == "deny"


def test_pre_tool_rejects_denied_path_within_allowed():
    result = decide_pre_tool({"agent_id": "a"},
                             {"context": {"allowed_paths": ["src/**"]},
                              "effective_rules": {"deny_paths": ["src/no.py"]}},
                             {"owner": "a"}, "Write", {"file_path": "src/no.py"}, ".")
    assert result["action"] == "deny"


def test_run_required_reports_failed_command():
    result = run_required(["exit 1"], ".")
    assert result[0]["command"] == "exit 1"
    assert result[0]["ok"] is False


def test_run_required_passes_successful_command():
    assert run_required(["true"], ".")[0]["ok"] is True


def test_required_failure_decision_is_input_required():
    decision = decide_stop({}, {"state": "working"},
                           {"passed": True, "required_failures": [{"command": "pytest"}]}, 2, 2)
    assert decision["action"] == "input_required"
    assert "pytest" in decision["question"]


def test_required_commands_run_in_requested_directory(tmp_path):
    result = run_required(["pwd"], str(tmp_path))
    assert result[0]["ok"] is True
    assert str(tmp_path) in result[0]["output"]
