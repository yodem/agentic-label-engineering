"""Headless Claude executors get a bounded, prompt-free grant (docs/harness-facts.md item 16).

Every test runs bin/ale-spawn against a fake ``claude``/``codex``/``pi`` on PATH that records its
argv; no real executor starts.
"""
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

from ale import dispatch, harness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPAWN = os.path.join(ROOT, "bin", "ale-spawn")
ALE_PY = os.path.join(ROOT, "bin", "ale-py")
ACCEPT = "uvx --python 3.9 pytest -q tests/test_headless_permissions.py"
COMMA = "make test,fast"
PARENS = "d=$(mktemp -d) && ls $d"
FORBIDDEN = ("bypassPermissions", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")


def _env(tmp_path, **extra):
    bindir = tmp_path / "fakebin"
    if not bindir.exists():
        bindir.mkdir()
        for name in ("claude", "codex", "pi"):
            fake = bindir / name
            fake.write_text("#!%s\nimport json, os, sys\n"
                            "open(os.environ['FAKE_OUT'], 'w').write(json.dumps(sys.argv))\n" % sys.executable)
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items()
           if k not in ("ALE_BIN", "ALE_PYTHON", "ALE_IMPORT_ROOT", "ALE_PLUGIN_ROOT", "ALE_READ_ONLY",
                        "ALE_TASK", "ALE_SPAWN_DRY", "PYTHONPATH")}
    env.update(PATH=str(bindir) + os.pathsep + os.environ["PATH"], FAKE_OUT=str(tmp_path / "argv.json"))
    env.update(extra)
    return env


def _request(tmp_path, harness_name="claude", kind="executor", permission_mode="missing",
             acceptance=(ACCEPT, COMMA, PARENS)):
    (tmp_path / "labels").mkdir(exist_ok=True)
    (tmp_path / "labels" / "T2.json").write_text(json.dumps(
        {"task_id": "T2", "acceptance": [{"id": "A%d" % i, "cmd": cmd, "expect": "exit0"}
                                         for i, cmd in enumerate(acceptance)]}))
    roster = {"harnesses": {"claude": {}}}
    if permission_mode != "missing":
        roster["harnesses"]["claude"]["permission_mode"] = permission_mode
    (tmp_path / "roster.json").write_text(json.dumps(roster))
    env = {"ALE_RUN_DIR": str(tmp_path), "ALE_ROSTER": str(tmp_path / "roster.json"),
           "ALE_PLUGIN_ROOT": ROOT, "ALE_IMPORT_ROOT": ROOT, "ALE_PYTHON": sys.executable}
    if kind == "monitor":
        env["ALE_READ_ONLY"] = "1"
    template = harness.BUILTIN[harness_name]["headless"]
    request = {"agent_id": "T2-%s-backend-1" % kind, "task_id": "T2", "kind": kind,
               "harness": harness_name, "mode": "headless", "host": "local",
               "argv": harness.render_argv(template, "test-model", "the prompt"),
               "cwd": str(tmp_path), "env": env}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return path, request["argv"]


def _spawn(tmp_path, path, **extra):
    out = tmp_path / "argv.json"
    if out.exists():
        out.unlink()
    proc = subprocess.run([SPAWN, str(path)], cwd=str(tmp_path), env=_env(tmp_path, **extra),
                          text=True, capture_output=True)
    argv = json.loads(out.read_text()) if out.exists() else None
    return proc, argv


def _rules(argv):
    assert argv.count("--allowedTools") == 1, argv
    return argv[argv.index("--allowedTools") + 1].split(",")


def test_headless_claude_gets_accept_edits_and_one_allowlist_before_the_prompt(tmp_path):
    path, _ = _request(tmp_path)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--permission-mode") == 1
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    rules = _rules(argv)
    for git in ("add", "commit", "status", "diff", "log"):
        assert "Bash(git %s:*)" % git in rules
    for prefix in ("python3 -m ale", sys.executable + " -m ale", ALE_PY, "$ALE_BIN"):
        for sub in ("claim", "heartbeat", "status", "submit", "usage", "note", "input-required", "refs-ack"):
            assert "Bash(%s %s:*)" % (prefix, sub) in rules
        # Only the protocol subcommands: never the whole CLI (rescope, accept, integrate ...).
        assert "Bash(%s:*)" % prefix not in rules
        assert "Bash(%s rescope:*)" % prefix not in rules
    assert "Bash(%s)" % ACCEPT in rules
    assert not any(rule.startswith("Bash(*") for rule in rules)
    # --allowedTools is variadic: an option token must follow its value, and all of it precedes -p.
    assert argv[argv.index("--allowedTools") + 2] == "--permission-mode"
    assert argv.index("--permission-mode") < argv.index("--model") < argv.index("-p")
    assert argv[-2:] == ["-p", "the prompt"]


def test_ale_bin_rules_are_granted_only_when_ale_bin_is_the_shim(tmp_path):
    # $ALE_BIN rules match the unexpanded text, so they are safe only when ale-spawn exports
    # ALE_BIN as the bin/ale-py shim. An inherited ALE_BIN pointing elsewhere gets no such rule.
    path, _ = _request(tmp_path)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert "Bash($ALE_BIN status:*)" in _rules(argv)
    assert "not the ale-py shim" not in proc.stderr
    proc, argv = _spawn(tmp_path, path, ALE_BIN="/usr/bin/env")
    assert proc.returncode == 0, proc.stderr
    rules = _rules(argv)
    assert not any("$ALE_BIN" in rule for rule in rules), rules
    for sub in ("claim", "status", "submit"):
        assert "Bash(%s %s:*)" % (ALE_PY, sub) in rules
        assert "Bash(python3 -m ale %s:*)" % sub in rules
    assert "ale-spawn: ALE_BIN is not the ale-py shim; not granting $ALE_BIN rules" in proc.stderr
    proc, argv = _spawn(tmp_path, path, ALE_BIN=ALE_PY)
    assert proc.returncode == 0, proc.stderr
    assert "Bash($ALE_BIN status:*)" in _rules(argv)


def test_unsplittable_acceptance_commands_are_skipped_with_a_note(tmp_path):
    path, _ = _request(tmp_path)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    joined = argv[argv.index("--allowedTools") + 1]
    assert "make test" not in joined and "mktemp" not in joined
    assert "skipped allowedTools entry" in proc.stderr
    assert COMMA in proc.stderr and PARENS in proc.stderr


def test_read_only_monitor_gets_no_accept_edits_and_keeps_its_write_deny(tmp_path):
    path, _ = _request(tmp_path, kind="monitor")
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert "--permission-mode" not in argv and "--allowedTools" not in argv
    deny = argv[argv.index("--disallowedTools") + 1]
    assert "Write" in deny and "Edit" in deny


@pytest.mark.parametrize("name", ["codex", "pi"])
def test_codex_and_pi_argv_are_unchanged(tmp_path, name):
    path, original = _request(tmp_path, harness_name=name)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert argv[1:] == original[1:]
    assert os.path.basename(argv[0]) == name


def test_roster_permission_mode_null_restores_the_old_argv(tmp_path):
    path, original = _request(tmp_path, permission_mode=None)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert argv[1:] == ["--plugin-dir", ROOT] + original[1:]


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk", "auto"])
def test_any_other_roster_permission_mode_is_refused(tmp_path, mode):
    path, _ = _request(tmp_path, permission_mode=mode)
    proc, argv = _spawn(tmp_path, path)
    assert proc.returncode == 2 and argv is None
    assert "permission_mode must be acceptEdits or null" in proc.stderr
    errs = harness.check({"harnesses": {"claude": {"permission_mode": mode}}, "routing": []})
    assert any("permission_mode" in err for err in errs)


def test_no_argv_ever_bypasses_permissions(tmp_path):
    for case in (dict(), dict(kind="monitor"), dict(permission_mode=None),
                 dict(harness_name="codex"), dict(harness_name="pi")):
        path, _ = _request(tmp_path, **case)
        proc, argv = _spawn(tmp_path, path)
        assert proc.returncode == 0, proc.stderr
        assert not any(arg in FORBIDDEN for arg in argv), argv
        if "--permission-mode" in argv:
            assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def _dispatch_request(tmp_path, permission_mode="missing"):
    """A request built by dispatch.spawn_request, the way `ale dispatch` builds it."""
    with open(os.path.join(ROOT, "examples", "run", "labels", "T01.json"), encoding="utf-8") as handle:
        label = json.load(handle)
    label["acceptance"] = [{"id": "A1", "cmd": ACCEPT, "expect": "exit0"}]
    (tmp_path / "labels").mkdir(exist_ok=True)
    (tmp_path / "labels" / "T01.json").write_text(json.dumps(label))
    roster = {"harnesses": {"claude": {}}}
    if permission_mode != "missing":
        roster["harnesses"]["claude"]["permission_mode"] = permission_mode
    (tmp_path / "roster.json").write_text(json.dumps(roster))
    assignment = {"kind": "executor", "role": "backend", "executor": "claude", "model": "test-model"}
    request = dispatch.spawn_request(label, assignment, str(tmp_path), "run-x", cwd=str(tmp_path), worktree={})
    request.update(mode="headless", host="local", argv=harness.render_argv(
        harness.BUILTIN["claude"]["headless"], "test-model", "the prompt"))
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return path


def _dry_spawn(tmp_path, path):
    proc = subprocess.run([SPAWN, str(path)], cwd=str(tmp_path), env=_env(tmp_path, ALE_SPAWN_DRY="1"),
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_dispatch_built_request_gets_the_grant_and_honours_the_opt_out(tmp_path):
    out = _dry_spawn(tmp_path, _dispatch_request(tmp_path))
    assert "--allowedTools" in out and "--permission-mode acceptEdits" in out
    assert "Bash(%s)" % ACCEPT in out
    out = _dry_spawn(tmp_path, _dispatch_request(tmp_path, permission_mode=None))
    assert "--permission-mode" not in out and "--allowedTools" not in out


def _spawned_ale_bin(tmp_path):
    out = _dry_spawn(tmp_path, _dispatch_request(tmp_path))
    return next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("ALE_BIN="))


def test_ale_bin_is_the_ale_py_shim(tmp_path):
    ale_bin = _spawned_ale_bin(tmp_path)
    assert os.path.realpath(ale_bin) == os.path.realpath(ALE_PY)
    assert os.stat(ALE_PY).st_mode & stat.S_IXUSR


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_unquoted_ale_bin_runs_in_sh_and_zsh(tmp_path, shell):
    if shutil.which(shell) is None:
        pytest.skip("%s is not installed" % shell)
    ale_bin = _spawned_ale_bin(tmp_path)
    env = _env(tmp_path, ALE_BIN=ale_bin, ALE_PYTHON=sys.executable, ALE_IMPORT_ROOT=ROOT)
    proc = subprocess.run([shell, "-c", "$ALE_BIN --version"], cwd=str(tmp_path), env=env,
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("ale ")


def test_permission_helper_rules():
    mode, allowed, skipped = harness.claude_headless_permissions(
        {}, [{"cmd": "python -m pytest"}, {"cmd": "echo a,b"}], ["python3 -m ale"], read_only=True)
    assert (mode, allowed, skipped) == (None, [], [])
    mode, allowed, skipped = harness.claude_headless_permissions(
        {}, [{"cmd": "echo a,b"}, {"cmd": "true"}, "junk", {"cmd": ""}],
        ["python3 -m ale", "/odd (path)/py -m ale"])
    assert mode == "acceptEdits"
    assert "Bash(python3 -m ale claim:*)" in allowed and "Bash(python3 -m ale:*)" not in allowed
    assert allowed[-1] == "Bash(true)"
    assert skipped == ["/odd (path)/py -m ale", "echo a,b"]
    with pytest.raises(ValueError):
        harness.claude_permission_mode({"harnesses": {"claude": {"permission_mode": "bypassPermissions"}}})
