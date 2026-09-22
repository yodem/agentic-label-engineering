"""Suite-wide guard: no ALE test may execute the real ``jev-ask``.

``jev-ask`` is a live client for a paid, networked judge. Every test runs with
a guard directory first on PATH. That directory holds a ``jev-ask`` stand-in
that records the calling test and exits 97, so a test that reaches for the
judge by name gets a refusal, never the real client. The fixture then fails
that test loudly. Tests that must exercise the guard itself opt out of the
failure with the ``jev_guard_probe`` marker.

Set ``ALE_JEV_GUARD_DISABLE=1`` only for an evidence run that puts its own
logging stand-in on PATH to count invocations from outside the suite.
"""

import os
import stat
import tempfile

import pytest

pytest_plugins = ["pytester"]

GUARD_EXIT = 97
DISABLE_ENV = "ALE_JEV_GUARD_DISABLE"
_GUARD_KEY = pytest.StashKey[dict]()


def _guard_disabled() -> bool:
    return os.environ.get(DISABLE_ENV) == "1"


def _write_guard(directory: str, log_path: str) -> None:
    shim = os.path.join(directory, "jev-ask")
    with open(shim, "w", encoding="utf-8") as handle:
        handle.write(
            "#!/bin/sh\n"
            "printf '%%s | %%s\\n' \"$PYTEST_CURRENT_TEST\" \"$*\" >> '%s'\n"
            "echo 'jev-ask is blocked inside the ALE test suite' >&2\n"
            "exit %d\n" % (log_path, GUARD_EXIT))
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _log_lines(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]
    except OSError:
        return []


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "jev_guard_probe: the test deliberately triggers the jev-ask guard")
    directory = tempfile.mkdtemp(prefix="ale-jev-guard-")
    log_path = os.path.join(directory, "calls.log")
    _write_guard(directory, log_path)
    config.stash[_GUARD_KEY] = {"dir": directory, "log": log_path, "probe_hits": 0}


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    guard = config.stash.get(_GUARD_KEY, None)
    if guard is None:
        return
    if _guard_disabled():
        terminalreporter.write_line("jev-ask guard: disabled by %s" % DISABLE_ENV)
        return
    hits = _log_lines(guard["log"])
    terminalreporter.write_line(
        "jev-ask guard: %d invocation(s) outside probe tests (%d from jev_guard_probe tests)"
        % (len(hits) - guard["probe_hits"], guard["probe_hits"]))
    for line in hits:
        terminalreporter.write_line("  %s" % line)


@pytest.fixture(autouse=True)
def jev_ask_guard(request, monkeypatch):
    """Prepend the guard to PATH and fail any test that executes jev-ask."""
    guard = request.config.stash[_GUARD_KEY]
    if _guard_disabled():
        yield None
        return
    monkeypatch.setenv("PATH", guard["dir"] + os.pathsep + os.environ.get("PATH", ""))
    before = len(_log_lines(guard["log"]))
    info = {"dir": guard["dir"], "log": guard["log"], "before": before}
    yield info
    new_hits = _log_lines(guard["log"])[before:]
    if request.node.get_closest_marker("jev_guard_probe") is not None:
        guard["probe_hits"] += len(new_hits)
    elif new_hits:
        pytest.fail("test executed jev-ask (blocked by the suite guard): %s" % "; ".join(new_hits),
                    pytrace=False)
