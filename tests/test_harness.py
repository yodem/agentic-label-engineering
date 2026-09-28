import json
import os

import pytest

from ale import harness as H
from ale.cli import main
from ale.labelset import check_label
from ale.roster import RosterError, load_roster, resolve

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roster_path(tmp_path, **extra):
    with open(os.path.join(ROOT, "examples", "roster.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    roster.update(extra)
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster))
    return str(path)


def _roster(tmp_path, **extra):
    return load_roster(_roster_path(tmp_path, **extra))


@pytest.mark.parametrize("legacy,name", [("claude-headless", "claude"), ("codex-exec", "codex"),
                                         ("pi-print", "pi"), ("claude-subagent", "claude"),
                                         ("claude_code", "claude"), ("herdr-pane", "claude"),
                                         ("codex", "codex"), ("gemini", "gemini"), (None, None)])
def test_normalize_legacy_ids(legacy, name):
    assert H.normalize(legacy) == name


def test_roster_accepts_short_and_declared_harnesses(tmp_path):
    path = _roster_path(tmp_path, harnesses={
        "gemini": {"headless": ["gemini", "-m", "{model}", "-p", "{prompt}"], "herdr_kind": "gemini"}})
    data = json.load(open(path))
    data["routing"].append({"role": "*", "model_tier": "cheap", "executor": "gemini", "model": "g-flash"})
    data["routing"].append({"role": "backend", "model_tier": "standard", "executor": "codex", "model": "gpt-6-luna"})
    open(path, "w").write(json.dumps(data))
    roster = load_roster(path)
    assert H.check(roster) == []
    assert "gemini" in H.registry(roster)


def test_unknown_harness_is_rejected_with_its_name(tmp_path):
    path = _roster_path(tmp_path)
    data = json.load(open(path))
    data["routing"][0]["executor"] = "nope"
    open(path, "w").write(json.dumps(data))
    with pytest.raises(RosterError, match="nope"):
        load_roster(path)


def test_declared_harness_without_prompt_placeholder_fails(tmp_path):
    with pytest.raises(RosterError, match=r"gemini.*\{prompt\}"):
        _roster(tmp_path, harnesses={"gemini": {"headless": ["gemini", "-m", "{model}"]}})


def test_resolve_matches_pinned_harness_against_legacy_rows():
    roster = {"routing": [{"role": "*", "model_tier": "standard", "executor": "codex-exec", "model": "gpt-6-luna"}]}
    assert resolve(roster, "backend", "standard", executor="codex") == {"executor": "codex", "model": "gpt-6-luna"}


@pytest.mark.parametrize("lane,harness,mode", [("inline", "claude", "in-session"), ("workflow", "claude", "in-session"),
                                               ("workflow", "codex", "headless"), ("inline", "pi", "headless"),
                                               ("pane", "claude", "pane"), ("pane", "codex", "pane"),
                                               (None, "claude", "headless")])
def test_derive_mode(lane, harness, mode, tmp_path):
    assert H.derive_mode(lane, harness, _roster(tmp_path)) == mode


def test_legacy_mode_wins_over_lane(tmp_path):
    roster = _roster(tmp_path)
    assert H.derive_mode("workflow", "claude-headless", roster, hint=H.mode_hint("claude-headless")) == "headless"
    assert H.derive_mode(None, "claude", roster, hint="in-session") == "in-session"


def test_non_anthropic_model_never_runs_in_session(tmp_path):
    roster = _roster(tmp_path)
    assert H.derive_mode("workflow", "claude", roster, model="openrouter/qwen") == "in-session"  # family unknown
    assert H.derive_mode("workflow", "claude", roster, model="gpt-6-luna") == "headless"
    assert H.derive_mode(None, "claude", roster, hint="in-session", model="gpt-6-luna") == "headless"


@pytest.mark.parametrize("mode,locality,host", [("in-session", "any", "local"), ("headless", "any", "dev-server"),
                                                ("pane", "any", "dev-server"), ("headless", "local", "local"),
                                                ("headless", None, "dev-server")])
def test_derive_host(mode, locality, host, tmp_path):
    assert H.derive_host(mode, locality, _roster(tmp_path, remote_host="dev-server")) == host


def test_derive_host_without_remote_host_is_local(tmp_path):
    assert H.derive_host("headless", "any", _roster(tmp_path)) == "local"


@pytest.mark.parametrize("harness,mode,spawn", [("claude", "in-session", "claude-subagent"),
                                                ("claude", "headless", "claude-headless"),
                                                ("codex", "headless", "codex-exec"), ("pi", "headless", "pi-print"),
                                                ("pi", "pane", "herdr-pane"), ("gemini", "headless", "gemini")])
def test_spawn_id(harness, mode, spawn):
    assert H.spawn_id(harness, mode) == spawn


@pytest.mark.parametrize("harness,model,fam", [("codex", None, "openai"), ("pi", "gpt-6-luna", "openai"),
                                               ("pi", "claude-sonnet-5", "anthropic"), ("claude", None, "anthropic"),
                                               ("pi", "qwen3", None)])
def test_family(harness, model, fam, tmp_path):
    assert H.family(harness, model, _roster(tmp_path)) == fam


def test_route_pins_and_derives(tmp_path, label_t01):
    roster = _roster(tmp_path, remote_host="dev-server")
    label_t01["labels"]["lane"] = "workflow"
    label_t01["labels"]["locality"] = "any"
    label_t01["assignments"] = [{"kind": "executor", "role": label_t01["labels"]["role"],
                                 "model_tier": label_t01["labels"]["model_tier"], "executor": "codex",
                                 "model": "gpt-6-luna", "trigger": "ready"}]
    assert H.route(label_t01, roster) == {"harness": "codex", "model": "gpt-6-luna", "mode": "headless",
                                          "host": "dev-server", "resolved_from": "pin"}


def test_assignment_executor_codex_validates(roster, label_t01):
    label_t01["assignments"] = [dict(kind="executor", role=label_t01["labels"]["role"],
                                     model_tier=label_t01["labels"]["model_tier"], executor="codex", trigger="ready")]
    assert check_label(label_t01, roster) == []
    label_t01["assignments"][0]["executor"] = "nope"
    assert any("unsupported assignment executor='nope'" in e for e in check_label(label_t01, roster))


def test_plan_route_cli(tmp_path, capsys):
    from tests.test_cli_plan import _valid_plan

    roster = _roster_path(tmp_path)
    plan = _valid_plan(tmp_path, roster)
    task_id = json.loads(plan.read_text().split("```ale-label", 1)[1].split("```", 1)[0])["task_id"]
    capsys.readouterr()
    assert main(["plan", "route", str(plan), "--task", task_id, "--lane", "workflow",
                 "--roster", roster, "--json"]) == 0
    route = json.loads(capsys.readouterr().out)
    assert set(route) == {"harness", "model", "mode", "host", "resolved_from"}
    assert route == {"harness": "codex", "model": "gpt-5-codex", "mode": "headless", "host": "local",
                     "resolved_from": "roster"}  # examples roster: backend/standard -> codex-exec
    assert main(["plan", "route", str(plan), "--task", "T99", "--roster", roster, "--json"]) == 2
