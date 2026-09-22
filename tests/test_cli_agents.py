import hashlib
import json
import shutil
from types import SimpleNamespace

from ale.agentcat import load_catalog
from ale.cli import main


def agent_file(root, name="project-css", model_tier_min="cheap"):
    path = root / "frontend" / "css.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: %s\nrole: frontend\nsub: css\nphases: [implement]\n"
        "model_tier_min: %s\nreads: []\nrules:\n  deny_paths: []\n  deny_tools: []\n"
        "  require_before_submit: []\nchecklist: []\nversion: 1\n---\ncore\n" % (name, model_tier_min),
        encoding="utf-8",
    )
    return path


def test_agents_list_help():
    assert main(["agents", "list", "--help"]) == 0


def test_agents_show_help():
    assert main(["agents", "show", "--help"]) == 0


def test_agents_list_json_shows_project_source(tmp_path, monkeypatch, capsys):
    path = agent_file(tmp_path / ".ale" / "agents")
    monkeypatch.setattr("ale.cli._agent_roots", lambda: [str(tmp_path / ".ale" / "agents")])
    monkeypatch.chdir(tmp_path)
    assert main(["agents", "list", "--json"]) == 0
    output = capsys.readouterr().out
    assert str(path.parent.parent) in output
    assert "project-css" in output


def test_agents_show_reports_sha_and_body(tmp_path, monkeypatch, capsys):
    path = agent_file(tmp_path / ".ale" / "agents")
    monkeypatch.setattr("ale.cli._agent_roots", lambda: [str(tmp_path / ".ale" / "agents")])
    monkeypatch.chdir(tmp_path)
    assert main(["agents", "show", "frontend/css"]) == 0
    output = capsys.readouterr().out
    assert hashlib.sha256(path.read_bytes()).hexdigest() in output
    assert "core" in output


def test_catalog_project_file_shadows_bundle(tmp_path):
    project = tmp_path / "project"
    bundle = tmp_path / "bundle"
    agent_file(project, "project-css")
    agent_file(bundle, "bundle-css")
    assert load_catalog([str(project), str(bundle)])["frontend/css"]["name"] == "project-css"


def test_init_run_refuses_general_fallback_for_specialty(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["init-run", "--plan", str(tmp_path / "plan.md")])
    assert result == 1
    assert "general" in capsys.readouterr().err.lower()


def test_status_shows_frozen_agent_and_staleness(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    shutil.copytree("examples/run", str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy("examples/roster.json", str(roster))
    label_path = run_dir / "labels" / "T01.json"
    label = json.loads(label_path.read_text(encoding="utf-8"))
    label["routing"] = {"agent": {"key": "frontend/css", "name": "frontend-css", "sha256": "a" * 64}}
    label_path.write_text(json.dumps(label), encoding="utf-8")
    empty_catalog = tmp_path / "empty-agents"
    empty_catalog.mkdir()
    monkeypatch.setattr("ale.cli._agent_roots", lambda: [str(empty_catalog)])

    for args in ([], ["--json"]):
        assert main(["status", *args, "--run-dir", str(run_dir), "--roster", str(roster)]) == 0
        output = capsys.readouterr().out
        if args:
            assert json.loads(output)["tasks"]["T01"]["agent"] == "agent: unknown"
        else:
            assert "agent: unknown" in output


def test_status_survives_unreadable_agent_catalog(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    shutil.copytree("examples/run", str(run_dir))
    roster = tmp_path / "roster.json"
    shutil.copy("examples/roster.json", str(roster))
    label_path = run_dir / "labels" / "T01.json"
    label = json.loads(label_path.read_text(encoding="utf-8"))
    label["routing"] = {"agent": {"key": "frontend/css", "name": "frontend-css", "sha256": "a" * 64}}
    label_path.write_text(json.dumps(label), encoding="utf-8")
    monkeypatch.setattr("ale.cli.AC.load_catalog", lambda _roots: (_ for _ in ()).throw(PermissionError("unreadable")))
    assert main(["status", "--run-dir", str(run_dir), "--roster", str(roster)]) == 0
    assert "agent: unknown" in capsys.readouterr().out


def test_relabel_parser_accepts_sub_and_phase():
    assert main(["relabel", "--help"]) == 0


def test_agent_floor_raises_label_and_executor_assignments_only(tmp_path, monkeypatch):
    catalog_root = tmp_path / "agents"
    agent_file(catalog_root, model_tier_min="standard")
    run_dir = tmp_path / "run"
    (run_dir / "labels").mkdir(parents=True)
    monkeypatch.setattr("ale.cli._agent_roots", lambda: [str(catalog_root)])
    label = {
        "labels": {"role": "frontend", "sub": "css", "phase": "implement", "model_tier": "cheap"},
        "assignments": [
            {"kind": "executor", "role": "frontend", "model_tier": "cheap"},
            {"kind": "monitor", "role": "frontend", "model_tier": "cheap"},
        ],
        "provenance": {},
    }
    context = SimpleNamespace(labels={"T1": label}, run_dir=str(run_dir))
    from ale.cli import _resolve_run_agents
    assert _resolve_run_agents(context) is True
    assert label["labels"]["model_tier"] == "standard"
    assert label["assignments"][0]["model_tier"] == "standard"
    assert label["assignments"][1]["model_tier"] == "cheap"
    assert label["provenance"]["model_tier"]["by"] == "agent-floor"
