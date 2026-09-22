"""The suite-wide jev-ask guard (root conftest.py) is loaded and fires."""

import os
import shutil
import subprocess

import pytest

from ale.labeling.judge import CommandJudge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(os.environ.get("ALE_JEV_GUARD_DISABLE") == "1",
                                reason="guard disabled for an outside evidence run")


def _hits(info):
    with open(info["log"], encoding="utf-8") as handle:
        return [line for line in handle.read().splitlines() if line.strip()][info["before"]:]


def test_guard_is_loaded_and_first_on_path(jev_ask_guard):
    assert jev_ask_guard is not None
    assert os.environ["PATH"].split(os.pathsep)[0] == jev_ask_guard["dir"]
    assert shutil.which("jev-ask") == os.path.join(jev_ask_guard["dir"], "jev-ask")


@pytest.mark.jev_guard_probe
def test_guard_refuses_exec_and_names_the_test(jev_ask_guard):
    proc = subprocess.run(["jev-ask", "noul", "Is this a probe?"], input="probe state",
                          capture_output=True, text=True)
    assert proc.returncode == 97
    assert "blocked" in proc.stderr
    hits = _hits(jev_ask_guard)
    assert len(hits) == 1
    assert "test_guard_refuses_exec_and_names_the_test" in hits[0]


@pytest.mark.jev_guard_probe
def test_command_judge_by_name_reaches_the_guard_not_the_client(jev_ask_guard):
    answer = CommandJudge(["jev-ask"], timeout_s=5).ask("role", "Which role?", ["backend: x"], "task text")
    assert answer["value"] is None
    assert answer["detail"]["error"] == "exit_97"
    assert len(_hits(jev_ask_guard)) == 1


def test_guard_fails_an_unmarked_test_that_calls_jev_ask(pytester):
    with open(os.path.join(ROOT, "conftest.py"), encoding="utf-8") as handle:
        pytester.makeconftest(handle.read())
    pytester.makepyfile(test_inner="""
import subprocess

def test_calls_the_judge():
    proc = subprocess.run(["jev-ask", "noul", "q"], input="s", capture_output=True, text=True)
    assert proc.returncode == 97
""")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*test executed jev-ask (blocked by the suite guard)*"])
    result.stdout.fnmatch_lines(["*jev-ask guard: 1 invocation(s) outside probe tests*"])


def test_guard_negative_control_quiet_test_passes(pytester):
    with open(os.path.join(ROOT, "conftest.py"), encoding="utf-8") as handle:
        pytester.makeconftest(handle.read())
    pytester.makepyfile(test_inner="""
def test_does_not_call_the_judge():
    assert True
""")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*jev-ask guard: 0 invocation(s) outside probe tests*"])
