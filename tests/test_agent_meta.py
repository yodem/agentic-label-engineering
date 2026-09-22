import csv
import io
import json

from ale.timeline import agent_metadata


def labels_for(*, fixed=False):
    labels = {
        "T1": {"routing": {"agent": {"name": "css", "version": "1"}}},
        "T2": {"routing": {"agent": {"name": "css", "version": "1"}}},
    }
    if fixed:
        labels["F1"] = {
            "fixes": "T2",
            "routing": {"agent": {"name": "css", "version": "1"}},
        }
    return labels


def sample_events():
    return [
        {"type": "accepted", "task_id": "T1", "attempt": 1, "ts": 1},
        {"type": "accepted", "task_id": "T2", "attempt": 2, "ts": 5},
        {"type": "fix_started", "task_id": "F1", "ts": 6},
        {"type": "breach", "task_id": "T2", "breach": "timeout"},
        {"type": "usage", "task_id": "T1", "ts": 0.5, "gen_ai.usage.input_tokens": 4,
         "gen_ai.usage.output_tokens": 6},
        {"type": "usage", "task_id": "T2", "ts": 3, "gen_ai.usage.input_tokens": 10,
         "gen_ai.usage.output_tokens": 5},
        {"type": "claimed", "task_id": "T1", "ts": 0},
        {"type": "accepted", "task_id": "F1", "attempt": 1, "ts": 10},
        {"type": "claimed", "task_id": "T2", "ts": 2},
        {"type": "claimed", "task_id": "F1", "ts": 6},
    ]


def test_agent_metadata_groups_by_name_and_version():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert list(result) == ["css@1"]


def test_agent_metadata_counts_tasks():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["tasks"] == 3


def test_agent_metadata_counts_first_verify():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["accepted_first_verify"] == {"count": 1, "rate": 1 / 3}


def test_agent_metadata_excludes_fix_acceptance_from_first_verify_rate():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["accepted_first_verify"]["count"] == 1


def test_agent_metadata_counts_fix_tasks():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["fix_tasks"] == 1


def test_agent_metadata_counts_breaches():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["breaches"] == 1


def test_agent_metadata_sums_billable_tokens():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["billable_tokens"] == 25


def test_agent_metadata_uses_median_wall_seconds():
    result = agent_metadata(sample_events(), labels_for(fixed=True))
    assert result["css@1"]["wall_seconds_median"] == 3


def test_agent_metadata_empty_input():
    assert agent_metadata([], {}) == {}


def test_rewrite_candidate_requires_five_tasks_and_rate_below_threshold():
    labels = {
        "T%d" % index: {"routing": {"agent": {"name": "css", "version": "1"}}}
        for index in range(1, 6)
    }
    events = [
        {"type": "accepted", "task_id": "T%d" % index, "attempt": 1}
        for index in (1, 2, 3)
    ]
    result = agent_metadata(events, labels)
    assert result["css@1"]["rewrite_candidate"] is True
    assert result["css@1"]["accepted_first_verify"]["rate"] == 0.6


def test_rewrite_candidate_is_false_below_five_tasks():
    labels = {
        "T%d" % index: {"routing": {"agent": {"name": "css", "version": "1"}}}
        for index in range(1, 5)
    }
    result = agent_metadata([], labels)
    assert result["css@1"]["rewrite_candidate"] is False


def test_variant_overlay_resolves_and_is_recorded(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ale.cli import _resolve_run_agents

    variant = tmp_path / "variant.md"
    variant.write_text(
        "---\nname: variant-css\nrole: frontend\nsub: css\nphases: [implement]\n"
        "model_tier_min: cheap\nreads: []\nrules:\n  deny_paths: []\n  deny_tools: []\n"
        "  require_before_submit: []\nchecklist: []\nversion: 2\n---\nvariant body\n",
        encoding="utf-8",
    )
    (tmp_path / "labels").mkdir()
    label = {"labels": {"role": "frontend", "sub": "css", "phase": "implement",
                        "model_tier": "standard"}}
    (tmp_path / "labels" / "T1.json").write_text(json.dumps(label), encoding="utf-8")
    context = SimpleNamespace(labels={"T1": label}, run_dir=str(tmp_path))
    empty_catalog = tmp_path / "empty-catalog"
    empty_catalog.mkdir()
    monkeypatch.setattr("ale.cli._agent_roots", lambda: [str(empty_catalog)])

    assert _resolve_run_agents(context, ["frontend/css=%s" % variant])
    recorded = label["routing"]["agent"]
    assert recorded["name"] == "variant-css"
    assert recorded["version"] == 2
    assert recorded["variant"] is True
    assert recorded["path"] == str(variant)
    assert len(recorded["sha256"]) == 64


def test_meta_csv_contains_agent_columns(tmp_path, monkeypatch, capsys):
    from ale.cli import main
    monkeypatch.setattr("ale.cli.R.load_roster", lambda _path: {})
    run = tmp_path / "run"
    (run / "labels").mkdir(parents=True)
    (run / "labels" / "T1.json").write_text(json.dumps({
        "task_id": "T1", "routing": {"agent": {"name": "css", "version": "1"}}
    }), encoding="utf-8")
    (run / "events.jsonl").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["meta", "--run-dir", str(run), "--csv"]) == 0
    header = next(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert "accepted_first_verify_count" in header
    assert "accepted_first_verify_rate" in header
    assert "wall_seconds_median" in header


def test_run_help_exposes_agent_variant(capsys):
    from ale.cli import main
    assert main(["run", "--help"]) == 0
    assert "--agent-variant" in capsys.readouterr().out


def test_meta_help_exits_successfully():
    from ale.cli import main
    assert main(["meta", "--help"]) == 0


def test_two_run_ids_produce_two_run_directories(tmp_path, monkeypatch):
    from ale.cli import _resolve_run_dir

    (tmp_path / ".git").mkdir()
    (tmp_path / ".ale").mkdir()
    monkeypatch.chdir(tmp_path)
    first = _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": "plan-v1"})())
    second = _resolve_run_dir(type("Args", (), {"run_dir": None, "run_id": "plan-v2"})())
    assert first != second
