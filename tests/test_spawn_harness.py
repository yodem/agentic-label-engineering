import json
import os
import stat
import subprocess

from ale import harness as H
from ale.cli import main
from ale.events import read_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPAWN = os.path.join(ROOT, "bin", "ale-spawn")


def _fake(tmp_path, name):
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' \"$0\" \"$@\" > \"$FAKE_OUT\"\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _request(tmp_path, harness, model="m1", mode="headless", roster=None):
    roster = roster or {"routing": []}
    entry = H.registry(roster)[harness]
    argv = H.render_argv(entry["headless"], model=model, prompt="PROMPT")
    req = {"agent_id": "T1-executor-backend-1", "task_id": "", "kind": "executor", "harness": harness,
           "mode": mode, "host": "local", "model": model, "argv": argv, "usage_from": entry.get("usage_from"),
           "herdr_kind": entry.get("herdr_kind"), "cwd": str(tmp_path), "prompt_file": "PROMPT", "env": {}}
    path = tmp_path / "req.json"
    path.write_text(json.dumps(req))
    return path


def _path_env(tmp_path, **extra):
    return dict(os.environ, PATH="%s:%s" % (tmp_path / "bin", os.environ["PATH"]),
                FAKE_OUT=str(tmp_path / "out"), **extra)


def test_render_argv_drops_model_flag_when_model_is_none():
    assert H.render_argv(["codex", "exec", "--model", "{model}", "{prompt}"], model=None, prompt="P") == \
        ["codex", "exec", "P"]


def test_render_argv_keeps_prompt_text_literal():
    assert H.render_argv(["x", "--model={model}", "{prompt}"], model="m", prompt="say {model} $(id)") == \
        ["x", "--model=m", "say {model} $(id)"]


def test_codex_argv_skips_git_repo_check():
    assert "--skip-git-repo-check" in H.registry({})["codex"]["headless"]


def test_headless_codex_runs_declared_argv(tmp_path):
    _fake(tmp_path, "codex")
    subprocess.run(["sh", SPAWN, str(_request(tmp_path, "codex"))], check=True, env=_path_env(tmp_path))
    assert (tmp_path / "out").read_text().split("\n")[1:6] == \
        ["exec", "--json", "--skip-git-repo-check", "--model", "m1"]


def test_headless_argv_survives_quotes_and_newlines(tmp_path):
    _fake(tmp_path, "pi")
    req = _request(tmp_path, "pi", model=None)
    data = json.loads(req.read_text())
    data["argv"][-1] = "line one\n'quoted' \"double\" $(touch pwned) `x`\n\n"
    req.write_text(json.dumps(data))
    subprocess.run(["sh", SPAWN, str(req)], check=True, env=_path_env(tmp_path))
    out = (tmp_path / "out").read_text()
    assert "--model" not in out and "$(touch pwned)" in out
    assert not (tmp_path / "pwned").exists()


def test_headless_stdin_is_closed(tmp_path):
    path = tmp_path / "bin" / "codex"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\ncat > \"$FAKE_OUT\"\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    subprocess.run(["sh", SPAWN, str(_request(tmp_path, "codex"))], check=True, env=_path_env(tmp_path),
                   input="leaked", text=True, timeout=30)
    assert (tmp_path / "out").read_text() == ""


def test_roster_declared_harness_runs(tmp_path):
    _fake(tmp_path, "gemini")
    roster = {"harnesses": {"gemini": {"headless": ["gemini", "-m", "{model}", "-p", "{prompt}"]}}, "routing": []}
    subprocess.run(["sh", SPAWN, str(_request(tmp_path, "gemini", roster=roster))], check=True,
                   env=_path_env(tmp_path))
    assert "PROMPT" in (tmp_path / "out").read_text()


def test_pane_mode_passes_kind_and_host(tmp_path):
    herdr = tmp_path / "herdr-exec.py"
    herdr.write_text("import sys,os\nopen(os.environ['FAKE_OUT'],'a').write(' '.join(sys.argv[1:])+'\\n')\n")
    req = _request(tmp_path, "pi", mode="pane")
    data = json.loads(req.read_text())
    data["host"] = "dev-server"
    data["remote_worktree"] = "/srv/wt/T1"
    req.write_text(json.dumps(data))
    env = dict(os.environ, ALE_HERDR_EXEC=str(herdr), ALE_REMOTE_WORKTREE="/wrong", FAKE_OUT=str(tmp_path / "out"))
    subprocess.run(["sh", SPAWN, str(req)], check=True, env=env)
    first, second = (tmp_path / "out").read_text().splitlines()[:2]
    assert "--kind pi" in first and "--host dev-server" in first and "--cwd /srv/wt/T1" in first
    # herdr-exec takes --host before the subcommand.
    assert first.startswith("--host dev-server start ") and second.startswith("--host dev-server send ")


def test_remote_pane_without_worktree_refuses(tmp_path):
    herdr = tmp_path / "herdr-exec.py"
    herdr.write_text("raise SystemExit('must not run')\n")
    req = _request(tmp_path, "pi", mode="pane")
    data = json.loads(req.read_text())
    data["host"] = "dev-server"
    req.write_text(json.dumps(data))
    # The env var is not consulted by ale-spawn: only the request's remote_worktree counts.
    env = dict(os.environ, ALE_HERDR_EXEC=str(herdr), ALE_REMOTE_WORKTREE="/srv/wt/T1")
    proc = subprocess.run(["sh", SPAWN, str(req)], env=env, capture_output=True, text=True)
    assert proc.returncode == 2 and "remote worktree not provisioned" in proc.stderr


def test_headless_refuses_remote_host(tmp_path):
    _fake(tmp_path, "codex")
    req = _request(tmp_path, "codex")
    data = json.loads(req.read_text())
    data.update(host="dev-server", remote_worktree="/srv/wt/T1")
    req.write_text(json.dumps(data))
    proc = subprocess.run(["sh", SPAWN, str(req)], env=_path_env(tmp_path), capture_output=True, text=True)
    assert proc.returncode == 2
    assert "headless mode cannot run on remote host dev-server" in proc.stderr
    assert not (tmp_path / "out").exists()


def test_unknown_mode_exits_two(tmp_path):
    proc = subprocess.run(["sh", SPAWN, str(_request(tmp_path, "codex", mode="in-session"))],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "unknown mode: in-session" in proc.stderr


def test_non_claude_prompt_drops_claude_harness_section():
    from ale.dispatch import render_prompt
    from ale.agentcat import HARNESS_MARKER
    agent = {"name": "a", "sha256": "0" * 8, "body": "core rules\n" + HARNESS_MARKER + "\nAgent Teams: SendMessage"}
    label = {"task_id": "T1", "title": "t", "context": {}, "labels": {}}
    assert "SendMessage" in render_prompt(label, {"harness": "claude"}, agent, refs={})
    assert "SendMessage" not in render_prompt(label, {"harness": "codex"}, agent, refs={})


# CLI dispatch -----------------------------------------------------------------------------


def _roster(tmp_path, **extra):
    roster = json.loads(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    roster["judge"] = {"default": "off"}
    roster.update(extra)
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster))
    return str(path)


def _label(executor=None, lane="workflow", mode="none", acceptance="true", paths=None):
    return {
        "schema_version": "1.0", "run_id": "run-1", "task_id": "T1", "title": "Task T1",
        "labels": {"role": "backend", "model_tier": "standard", "lane": lane, "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": paths or ["src/T1.py"],
                    "depends_on": [], "worktree": {"mode": mode, "branch": None, "base": None,
                                                   "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": acceptance, "expect": "exit0"},
                       {"id": "A2", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The human selected the lane."},
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": executor, "trigger": "ready"}],
    }


def _run(tmp_path, label):
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps(label))
    return run


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (["git", "init"], ["git", "config", "user.email", "test@example.com"],
                    ["git", "config", "user.name", "Test"]):
        subprocess.run(command, cwd=str(repo), check=True, capture_output=True)
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(repo), check=True, capture_output=True)
    return repo


def test_dispatch_request_carries_argv_family_and_usage(tmp_path, capsys):
    roster = _roster(tmp_path)
    run = _run(tmp_path, _label(executor="codex"))
    assert main(["dispatch", "--json", "--run-dir", str(run), "--roster", roster]) == 0
    request = json.loads(capsys.readouterr().out.strip())
    assert request["harness"] == "codex" and request["mode"] == "headless"
    assert request["argv"][:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert request["argv"][-1] == request["prompt"]
    assert request["usage_from"] == "codex-json" and request["herdr_kind"] == "codex"
    assert request["family"] == "openai"


def test_remote_host_without_worktree_releases_without_spawning(tmp_path, monkeypatch):
    roster = _roster(tmp_path, remote_host="dev-server")
    run = _run(tmp_path, _label(executor="codex"))
    marker = tmp_path / "spawn-ran"
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\ntouch '%s'\nexit 1\n" % marker)
    spawn.chmod(spawn.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))
    monkeypatch.delenv("ALE_REMOTE_WORKTREE", raising=False)

    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster", roster]) == 0
    events = read_events(str(run / "events.jsonl"))
    assert [event["type"] for event in events] == ["released"]
    assert events[0]["reason"] == "remote worktree not provisioned"
    assert not marker.exists()
    # A repeat dispatch while still unprovisioned adds no second release.
    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster", roster]) == 0
    assert [event["type"] for event in read_events(str(run / "events.jsonl"))] == ["released"]
    assert not marker.exists()


def _two_remote_tasks(tmp_path):
    roster = _roster(tmp_path, remote_host="dev-server")
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    for task_id in ("T1", "T2.a"):
        label = _label(executor="codex")
        label["task_id"], label["title"] = task_id, "Task " + task_id
        label["context"]["allowed_paths"] = ["src/%s.py" % task_id]
        (run / "labels" / (task_id + ".json")).write_text(json.dumps(label))
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\ncat \"$1\" >> '%s'\necho >> '%s'\n" % (tmp_path / "spawned", tmp_path / "spawned"))
    spawn.chmod(spawn.stat().st_mode | stat.S_IEXEC)
    return roster, run, spawn


def test_remote_worktree_resolves_per_task(tmp_path, monkeypatch):
    roster, run, spawn = _two_remote_tasks(tmp_path)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))
    monkeypatch.setenv("ALE_REMOTE_WORKTREE", "/srv/shared")
    monkeypatch.setenv("ALE_REMOTE_WORKTREE_T2_a", "/srv/wt/T2.a")

    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster", roster]) == 0
    events = read_events(str(run / "events.jsonl"))
    released = [event["task_id"] for event in events if event["type"] == "released"]
    spawned = [event["task_id"] for event in events if event["type"] == "spawned"]
    # Two remote tasks: the shared variable is ambiguous, so only the task with its own path runs.
    assert released == ["T1"] and spawned == ["T2.a"]
    request = json.loads((tmp_path / "spawned").read_text().strip())
    assert request["task_id"] == "T2.a" and request["mode"] == "pane"
    assert request["remote_worktree"] == "/srv/wt/T2.a"


def test_shared_remote_worktree_serves_a_single_remote_task(tmp_path, monkeypatch):
    roster = _roster(tmp_path, remote_host="dev-server")
    run = _run(tmp_path, _label(executor="codex"))
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\ncat \"$1\" > '%s'\n" % (tmp_path / "spawned"))
    spawn.chmod(spawn.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))
    monkeypatch.setenv("ALE_REMOTE_WORKTREE", "/srv/shared")

    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster", roster]) == 0
    assert [event["type"] for event in read_events(str(run / "events.jsonl"))] == ["spawned"]
    assert json.loads((tmp_path / "spawned").read_text())["remote_worktree"] == "/srv/shared"


def test_unclaimed_in_session_request_is_printed_again(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run(tmp_path, _label(executor="claude_code", lane="inline", mode="per_task"))
    common = ["--run-dir", str(run), "--roster", roster]
    calls = []
    import ale.cli as CLI
    original = CLI._create_worktree
    monkeypatch.setattr(CLI, "_create_worktree", lambda plan, cwd: (calls.append(plan["path"]), original(plan, cwd)))

    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    first = capsys.readouterr().out.strip()
    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    second = capsys.readouterr().out.strip()
    assert json.loads(second) == json.loads(first)
    assert [event["type"] for event in read_events(str(run / "events.jsonl"))] == ["spawned"]
    assert len(calls) == 1

    # Once claimed, the request is not handed out again.
    assert main(["claim", "--task", "T1", "--agent", json.loads(first)["agent_id"]] + common) == 0
    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    assert capsys.readouterr().out.strip() == ""


def test_in_session_dispatch_records_worktree_and_verify_runs_there(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path)
    run = _run(tmp_path, _label(executor="claude_code", lane="inline", mode="per_task",
                                acceptance="test -f marker", paths=["marker"]))
    monkeypatch.setenv("ALE_SPAWN_BIN", "/usr/bin/true")
    common = ["--run-dir", str(run), "--roster", roster]

    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    request = json.loads(capsys.readouterr().out.strip())
    assert request["mode"] == "in-session"
    spawned = [event for event in read_events(str(run / "events.jsonl")) if event["type"] == "spawned"]
    assert len(spawned) == 1 and spawned[0]["worktree"] == str(run / "wt" / "T1")
    (run / "wt" / "T1" / "marker").write_text("only in the worktree\n")
    assert not (repo / "marker").exists()

    assert main(["init-run"] + common) == 0
    assert main(["claim", "--task", "T1", "--agent", request["agent_id"]] + common) == 0
    assert main(["submit", "--task", "T1", "--agent", request["agent_id"], "--summary", "done"] + common) == 0
    monkeypatch.chdir(str(repo))
    assert main(["verify", "--task", "T1"] + common) == 0


def test_remote_task_gets_no_local_worktree(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path, remote_host="dev-server")
    run = _run(tmp_path, _label(executor="codex", mode="per_task"))
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\nexit 0\n")
    spawn.chmod(spawn.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))
    monkeypatch.setenv("ALE_REMOTE_WORKTREE", "/srv/wt/T1")

    assert main(["dispatch", "--spawn", "--run-dir", str(run), "--roster", roster, "--cwd", str(repo)]) == 0
    (spawned,) = [e for e in read_events(str(run / "events.jsonl")) if e["type"] == "spawned"]
    assert spawned["host"] == "dev-server" and spawned["remote_worktree"] == "/srv/wt/T1"
    assert "worktree" not in spawned
    assert not (run / "wt").exists()
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=str(repo), capture_output=True, text=True).stdout
    assert len(worktrees.strip().splitlines()) == 1


def test_verify_refuses_remote_task_before_register(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    roster = _roster(tmp_path, remote_host="dev-server")
    run = _run(tmp_path, _label(executor="codex", mode="per_task"))
    spawn = tmp_path / "spawn"
    spawn.write_text("#!/bin/sh\ncat \"$1\" > '%s'\n" % (tmp_path / "req.json"))
    spawn.chmod(spawn.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ALE_SPAWN_BIN", str(spawn))
    monkeypatch.setenv("ALE_REMOTE_WORKTREE", "/srv/wt/T1")
    common = ["--run-dir", str(run), "--roster", roster]
    assert main(["dispatch", "--spawn", "--cwd", str(repo)] + common) == 0
    request = json.loads((tmp_path / "req.json").read_text())
    assert request["cwd"] == str(repo) and request["remote_base"]
    assert main(["claim", "--task", "T1", "--agent", "a1"] + common) == 0
    assert main(["submit", "--task", "T1", "--agent", "a1", "--summary", "done remotely"] + common) == 0
    capsys.readouterr()
    assert main(["verify", "--task", "T1"] + common) == 1
    assert "register-worktree" in capsys.readouterr().err
    assert not [e for e in read_events(str(run / "events.jsonl")) if e["type"] in ("verified", "accepted")]
