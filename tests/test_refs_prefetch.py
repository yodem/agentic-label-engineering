import argparse
import io
import json
import os
import stat

import pytest

from ale import refs as R
from ale.agentcat import load_catalog
from ale.binding import binding_path
from ale.cli import main
from ale.events import read_events
from ale.handoff import write_atomic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = load_catalog([os.path.join(ROOT, "agents")])


def _ck(tmp_path, body="chapter text", code=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ck = bin_dir / "ck"
    ck.write_text("#!/bin/sh\necho '%s' \"$@\"\nexit %d\n" % (body, code))
    ck.chmod(ck.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir)


ENTRY = {"title": "Handbook, backend/api", "read_first": "ck items get BOOK:1", "how_to_read": "ck items get BOOK:3"}


def test_prefetch_writes_both_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _ck(tmp_path) + os.pathsep + os.environ["PATH"])
    out = tmp_path / "refs" / "T1.md"
    result = R.prefetch(ENTRY, str(out))
    assert result["ok"] and "BOOK:1" in out.read_text() and "BOOK:3" in out.read_text()
    assert out.read_text().index("BOOK:1") < out.read_text().index("BOOK:3")
    assert out.read_text().splitlines()[0] == "ALE-REFS-TOKEN: %s" % result["token"] and len(result["token"]) == 8
    assert result["bytes"] == len(out.read_bytes())


def test_prefetch_refuses_non_ck_commands(tmp_path):
    result = R.prefetch(dict(ENTRY, read_first="rm -rf /", how_to_read="curl x"), str(tmp_path / "o.md"))
    assert not result["ok"] and "ck" in result["error"]
    assert not (tmp_path / "o.md").exists()


def test_prefetch_refuses_a_shell_chain_after_ck(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _ck(tmp_path) + os.pathsep + os.environ["PATH"])
    result = R.prefetch(dict(ENTRY, how_to_read="curl x"), str(tmp_path / "o.md"))
    assert not result["ok"] and not (tmp_path / "o.md").exists()


def test_prefetch_missing_ck_is_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    result = R.prefetch(ENTRY, str(tmp_path / "o.md"))
    assert result == {"ok": False, "bytes": 0, "error": result["error"]} and result["error"]


def test_prefetch_failing_ck_is_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _ck(tmp_path, code=1) + os.pathsep + os.environ["PATH"])
    result = R.prefetch(ENTRY, str(tmp_path / "o.md"))
    assert result["ok"] is False and "exited 1" in result["error"]


def test_render_section_marks_required_and_names_prefetch(tmp_path):
    text = R.render_section({"backend/api": ENTRY}, {"role": "backend", "sub": "api"}, prefetched="/r/T1.md")
    assert "required" in text and "/r/T1.md" in text and "refs-ack" in text
    assert "- prefetched: /r/T1.md" in text
    plain = R.render_section({"backend/api": ENTRY}, {"role": "backend", "sub": "api"})
    assert "prefetched" not in plain and "- read first: ck items get BOOK:1" in plain
    assert R.render_section({}, {"role": "backend", "sub": "api"}).endswith("- none configured")


# --- agent key lookup -------------------------------------------------------------------------


def test_every_catalog_agent_resolves_to_its_own_key():
    refs = {key: dict(ENTRY, title=key) for key in CATALOG}
    for key, agent in CATALOG.items():
        assert R.resolve(refs, agent)[0] == key, key
        # A name-only agent (a lead-dispatched plugin agent carries no sub) finds the same chapter.
        name_only = {"role": agent["role"], "name": agent["name"]}
        assert R.resolve(refs, name_only)[0] == key, (key, agent["name"])


def test_default_agents_and_prefix_stripping():
    assert R.agent_keys({"role": "backend", "name": "backend-integration"})[:2] == [
        "backend/backend-integration", "backend/integration"]
    assert "backend/_default" in R.agent_keys({"role": "backend", "name": "backend-default"})
    assert "_cross/review" in R.agent_keys({"role": "_cross", "name": "cross-review"})
    keys = R.agent_keys({"role": "backend", "sub": "api", "name": "backend-api"})
    assert "backend/_default" not in keys and keys[-1] == "backend"
    assert R.agent_keys({"role": "general", "name": "general"}) == ["general"]


# --- CLI: dispatch, refs-ack, verify, hook ----------------------------------------------------


def _roster(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text(open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8").read())
    return str(path)


def _label(task_id="T1", agent_key="backend/api"):
    label = {
        "schema_version": "1.0", "run_id": "run-1", "task_id": task_id, "title": "Task " + task_id,
        "labels": {"role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"},
        "routing": {"executor": None, "model": None, "resolved_from": None},
        "context": {"spec_path": "spec.md", "pointers": [], "allowed_paths": ["src/%s.py" % task_id],
                    "depends_on": [], "worktree": {"mode": "none", "branch": None, "base": None,
                                                   "worktree_reason": None}},
        "acceptance": [{"id": "A1", "cmd": "true", "expect": "exit0"}],
        "provenance": {"lane_reason": "The human selected the inline lane."},
        "assignments": [{"kind": "executor", "role": "backend", "model_tier": "standard",
                         "executor": "claude-headless", "trigger": "ready"}],
    }
    if agent_key:
        label["routing"]["agent"] = {"key": agent_key}
    return label


@pytest.fixture
def run(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "labels").mkdir(parents=True)
    (run_dir / "labels" / "T1.json").write_text(json.dumps(_label()))
    (run_dir / "labels" / "T2.json").write_text(json.dumps(_label("T2", agent_key=None)))
    refs_file = tmp_path / "refs.json"
    refs_file.write_text(json.dumps({"backend/api": ENTRY}))
    monkeypatch.setenv("ALE_REFS_FILE", str(refs_file))
    monkeypatch.setenv("ALE_SPAWN_DRY", "1")
    monkeypatch.setenv("PATH", _ck(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.chdir(tmp_path)
    return run_dir, _roster(tmp_path)


def _common(run_dir, roster):
    return ["--run-dir", str(run_dir), "--roster", roster]


def _events(run_dir, kind):
    return [e for e in read_events(str(run_dir / "events.jsonl")) if e["type"] == kind]


def _dispatch(run_dir, roster):
    assert main(["dispatch", "--spawn"] + _common(run_dir, roster)) == 0


def test_dispatch_prefetches_refs_and_names_them_in_the_prompt(run, capsys):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    refs_path = run_dir / "refs" / "T1.md"
    assert "BOOK:1" in refs_path.read_text()
    [fetched] = _events(run_dir, "refs_fetched")
    assert fetched["task_id"] == "T1" and fetched["ok"] is True and fetched["key"] == "backend/api"
    assert fetched["path"] == str(refs_path) and len(fetched["token"]) == 8
    prompt = (run_dir / "prompts" / "T1-executor-backend-1.md").read_text()
    assert "- prefetched: %s" % refs_path in prompt and "refs-ack" in prompt
    request = json.loads((run_dir / "requests" / "T1-executor-backend-1.json").read_text())
    assert any(str(refs_path) in part for part in request["argv"])
    types = [e["type"] for e in read_events(str(run_dir / "events.jsonl"))]
    assert types.index("refs_fetched") < types.index("spawned")


def test_failing_ck_still_spawns_and_records_the_failure(run, tmp_path, monkeypatch, capsys):
    run_dir, roster = run
    (tmp_path / "failing").mkdir()
    monkeypatch.setenv("PATH", _ck(tmp_path / "failing", code=1) + os.pathsep + os.environ["PATH"])
    _dispatch(run_dir, roster)
    [fetched] = _events(run_dir, "refs_fetched")
    assert fetched["ok"] is False and fetched["error"] and fetched["token"] is None
    assert [e["task_id"] for e in _events(run_dir, "spawned")] == ["T1", "T2"]
    prompt = (run_dir / "prompts" / "T1-executor-backend-1.md").read_text()
    assert "prefetched" not in prompt and "- read first: ck items get BOOK:1" in prompt


def _claim_submit(run_dir, roster, task="T1"):
    agent = "%s-executor-backend-1" % task
    assert main(["claim", "--task", task, "--agent", agent] + _common(run_dir, roster)) == 0
    assert main(["submit", "--task", task, "--agent", agent, "--summary", "done"] + _common(run_dir, roster)) == 0
    return agent


def _verify_evidence(run_dir, roster, task="T1"):
    assert main(["verify", "--task", task, "--cwd", str(run_dir)] + _common(run_dir, roster)) == 0
    return [e for e in _events(run_dir, "verified") if e["task_id"] == task][-1]["evidence"]


def _token(run_dir):
    first = (run_dir / "refs" / "T1.md").read_text().splitlines()[0]
    return first.split(": ", 1)[1]


def test_verify_reports_refs_not_read_without_an_ack(run, capsys):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    _claim_submit(run_dir, roster)
    evidence = _verify_evidence(run_dir, roster)
    assert evidence["refs_read"] is False and "refs_token_mismatch" not in evidence
    assert "refs read: no" in capsys.readouterr().out


def test_ack_with_the_prefetched_token_reports_read(run, monkeypatch, capsys):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    agent = _claim_submit(run_dir, roster)
    monkeypatch.setenv("ALE_TASK", "T1")
    monkeypatch.setenv("ALE_AGENT", agent)
    assert main(["refs-ack", "--token", _token(run_dir), "--summary", "x"] + _common(run_dir, roster)) == 0
    [ack] = _events(run_dir, "refs_read")
    assert ack["via"] == "ack" and ack["token_ok"] is True and ack["agent_id"] == agent
    evidence = _verify_evidence(run_dir, roster)
    assert evidence["refs_read"] is True
    assert "refs read: yes" in capsys.readouterr().out


def test_ack_accepts_the_whole_token_line(run, monkeypatch):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    monkeypatch.setenv("ALE_TASK", "T1")
    line = (run_dir / "refs" / "T1.md").read_text().splitlines()[0]
    assert main(["refs-ack", "--token", line] + _common(run_dir, roster)) == 0
    assert _events(run_dir, "refs_read")[0]["token_ok"] is True


def test_ack_with_a_wrong_token_reports_a_mismatch(run, monkeypatch):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    _claim_submit(run_dir, roster)
    monkeypatch.setenv("ALE_TASK", "T1")
    assert main(["refs-ack", "--token", "deadbeef", "--summary", "x"] + _common(run_dir, roster)) == 0
    evidence = _verify_evidence(run_dir, roster)
    assert evidence["refs_read"] is False and evidence["refs_token_mismatch"] is True


def test_task_without_refs_entry_reports_null(run, capsys):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    _claim_submit(run_dir, roster, task="T2")
    evidence = _verify_evidence(run_dir, roster, task="T2")
    assert evidence["refs_read"] is None
    assert "refs read: n/a" in capsys.readouterr().out


def test_refs_ack_usage_errors(run, monkeypatch):
    run_dir, roster = run
    monkeypatch.delenv("ALE_TASK", raising=False)
    assert main(["refs-ack", "--token", "abc"] + _common(run_dir, roster)) == 2
    monkeypatch.setenv("ALE_TASK", "T1")
    assert main(["refs-ack"] + _common(run_dir, roster)) == 2
    assert not (run_dir / "events.jsonl").exists() or not _events(run_dir, "refs_read")


def test_remote_host_skips_prefetch(run, monkeypatch):
    from ale import cli
    run_dir, roster = run
    c = cli.Ctx(argparse.Namespace(run_dir=str(run_dir), roster=roster, now=None))
    c.roster_path = roster
    request = {"prompt_file": "unchanged"}
    cli._prefetch_refs(c, {"task_id": "T1", "kind": "executor", "host": "dev-server"}, request)
    [fetched] = _events(run_dir, "refs_fetched")
    assert fetched["ok"] is False and fetched["error"] == "remote host" and fetched["path"] is None
    assert request["prompt_file"] == "unchanged" and not (run_dir / "refs").exists()


# --- hook detection ---------------------------------------------------------------------------


def _hook(monkeypatch, tool_name, tool_input):
    data = {"session_id": "s1", "hook_event_name": "PostToolUse", "cwd": ".",
            "tool_name": tool_name, "tool_input": tool_input}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    return main(["hook", "post-tool"])


def test_hook_records_a_ck_read_once_per_attempt(run, tmp_path, monkeypatch):
    run_dir, roster = run
    _dispatch(run_dir, roster)
    agent = "T1-executor-backend-1"
    assert main(["claim", "--task", "T1", "--agent", agent] + _common(run_dir, roster)) == 0
    home = tmp_path / "home"
    monkeypatch.setenv("ALE_HOME", str(home))
    write_atomic(binding_path(str(home), "s1", None),
                 json.dumps({"run_dir": str(run_dir), "roster": roster, "task_id": "T1",
                             "agent_id": agent, "source": "file"}))
    assert _hook(monkeypatch, "Bash", {"command": "ls"}) == 0
    assert _events(run_dir, "refs_read") == []
    assert _hook(monkeypatch, "Bash", {"command": "ck items get BOOK:3"}) == 0
    assert _hook(monkeypatch, "Read", {"file_path": str(run_dir / "refs" / "T1.md")}) == 0
    [read] = _events(run_dir, "refs_read")
    assert read["via"] == "hook" and read["agent_id"] == agent and read["token_ok"] is None
    assert "ck items get BOOK:3" in read["summary"]
    assert main(["submit", "--task", "T1", "--agent", agent, "--summary", "done"] + _common(run_dir, roster)) == 0
    assert _verify_evidence(run_dir, roster)["refs_read"] is True


def test_prefetch_refuses_ck_commands_other_than_items_get(tmp_path):
    result = R.prefetch(dict(ENTRY, read_first="ck books create x"), str(tmp_path / "o.md"))
    assert not result["ok"] and "items get" in result["error"]


def test_hook_ck_detection_edges():
    from ale.hooks import refs_read_signal
    assert refs_read_signal("Bash", {"command": "cd x\nck items get B:1"}, None)
    assert refs_read_signal("Bash", {"command": "echo 'foo;ck items get B:1'"}, None) is None
    assert refs_read_signal("Read", {"file_path": "r.md"}, "/nope/r.md", cwd=["not", "a", "str"]) is None


def test_refs_status_survives_catalog_errors(monkeypatch, tmp_path):
    import ale.cli as cli

    class _Ctx:
        events_path = str(tmp_path / "events.jsonl")
        labels = {"T1": {}}
    open(_Ctx.events_path, "w").close()

    def boom(*_args):
        raise cli.CliError(2, "variant file missing")
    monkeypatch.setattr(cli, "_task_refs", boom)
    assert cli._refs_read_status(_Ctx(), "T1", 1, None) == (None, False)
