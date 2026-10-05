"""Suite-wide guard: no ALE test may execute the real ``jev-ask``.

``jev-ask`` is a live client for a paid, networked judge. Every test runs with
a guard directory first on PATH. That directory holds a ``jev-ask`` stand-in
that records the calling test and exits 97, so a test that reaches for the
judge by name gets a refusal, never the real client. The fixture then fails
that test loudly. Tests that must exercise the guard itself opt out of the
failure with the ``jev_guard_probe`` marker.

Set ``ALE_JEV_GUARD_DISABLE=1`` only for an evidence run that puts its own
logging stand-in on PATH to count invocations from outside the suite.

A second guard keeps the suite from starting a real executor: every test runs with
``ALE_SPAWN_BIN`` pointing at a no-op stand-in that logs its call and exits 0, so
``ale dispatch --spawn`` never reaches ``bin/ale-spawn`` and a real harness. A test that
must run the real launcher (with fake harnesses on PATH) opts in with ``real_spawn``.
"""

import os
import stat
import tempfile

import pytest

pytest_plugins = ["pytester"]

GUARD_EXIT = 97
DISABLE_ENV = "ALE_JEV_GUARD_DISABLE"
_GUARD_KEY = pytest.StashKey[dict]()
_SPAWN_KEY = pytest.StashKey[dict]()
SPAWN_STAND_IN = "ale-spawn-noop"


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


def _write_spawn_stand_in(directory: str, log_path: str) -> str:
    shim = os.path.join(directory, SPAWN_STAND_IN)
    with open(shim, "w", encoding="utf-8") as handle:
        handle.write(
            "#!/bin/sh\n"
            "printf '%%s | %%s\\n' \"$PYTEST_CURRENT_TEST\" \"$*\" >> '%s'\n"
            "exit 0\n" % log_path)
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


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
    config.addinivalue_line(
        "markers", "real_spawn: the test runs the real ALE_SPAWN_BIN default (bin/ale-spawn)")
    spawn_dir = tempfile.mkdtemp(prefix="ale-spawn-guard-")
    spawn_log = os.path.join(spawn_dir, "calls.log")
    config.stash[_SPAWN_KEY] = {"bin": _write_spawn_stand_in(spawn_dir, spawn_log), "log": spawn_log}


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
def ale_home(tmp_path, monkeypatch):
    """Point ``ALE_HOME`` at a per-test directory so no test writes the real ``~/.ale``
    (the run index, reports and eval ledger all live under it). It is ``tmp_path/home``,
    the directory tests already use as ``HOME`` and ``ALE_HOME``, so launcher tests that set
    only ``HOME`` still resolve the same ALE home."""
    home = str(tmp_path / "home")
    monkeypatch.setenv("ALE_HOME", home)
    return home


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


@pytest.fixture(autouse=True)
def spawn_guard(request, monkeypatch):
    """Point ``ALE_SPAWN_BIN`` at the no-op stand-in, unless the test is marked ``real_spawn``
    (then it is unset, so dispatch uses ``bin/ale-spawn``). Yields the stand-in's ``bin``, its
    ``log`` and the log length ``before`` the test, or None for a ``real_spawn`` test."""
    if request.node.get_closest_marker("real_spawn") is not None:
        monkeypatch.delenv("ALE_SPAWN_BIN", raising=False)
        yield None
        return
    guard = request.config.stash[_SPAWN_KEY]
    monkeypatch.setenv("ALE_SPAWN_BIN", guard["bin"])
    yield {"bin": guard["bin"], "log": guard["log"], "before": len(_log_lines(guard["log"]))}
