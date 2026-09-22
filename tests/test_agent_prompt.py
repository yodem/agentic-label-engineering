from pathlib import Path

from ale.agentcat import load_catalog
from ale.dispatch import render_prompt


def write_agent(root, reads=None, body="CORE", harness="HARNESS", checklist=None):
    path = root / "frontend" / "css.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: frontend-css\nrole: frontend\nsub: css\nphases: [implement]\n"
        "model_tier_min: cheap\nreads: %r\nrules:\n  deny_paths: []\n  deny_tools: []\n"
        "  require_before_submit: []\nchecklist: %r\nversion: 1\n---\n%s\n"
        "<!-- harness: claude-code -->\n%s\n" % (reads or [], checklist or [], body, harness),
        encoding="utf-8",
    )
    return load_catalog([str(root)])["frontend/css"]


def test_prompt_orders_core_reads_checklist_task_and_header(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.css").write_text("", encoding="utf-8")
    agent = write_agent(tmp_path / "catalog", ["src/**/*.css"], checklist=["check tokens"])
    prompt = render_prompt(
        {"task_id": "T1", "title": "Goal", "context": {"allowed_paths": ["src/**"]}},
        {"cwd": str(tmp_path), "executor": "codex-exec"}, agent,
    )
    assert prompt.index("CORE") < prompt.index("src/a.css") < prompt.index("check tokens") < prompt.index("Goal")
    assert "agent: frontend-css@%s" % agent["sha256"][:8] in prompt


def test_reads_are_relative_and_capped_at_40(tmp_path):
    (tmp_path / "src").mkdir()
    for index in range(45):
        (tmp_path / "src" / ("%02d.css" % index)).write_text("", encoding="utf-8")
    agent = write_agent(tmp_path / "catalog", ["src/*.css"])
    prompt = render_prompt({"title": "Goal", "context": {}}, {"cwd": str(tmp_path)}, agent)
    assert prompt.count("src/") == 40
    assert str(tmp_path) not in prompt


def test_harness_is_present_for_claude_executor_only(tmp_path):
    agent = write_agent(tmp_path, harness="CLAUDE_HARNESS_SENTINEL")
    label = {"title": "Goal", "context": {}}
    assert "CLAUDE_HARNESS_SENTINEL" in render_prompt(label, {"executor": "claude-headless"}, agent)
    for executor in ("codex-exec", "pi-print"):
        assert "CLAUDE_HARNESS_SENTINEL" not in render_prompt(label, {"executor": executor}, agent)


def test_context_body_is_fenced_as_reference_material(tmp_path):
    agent = write_agent(tmp_path)
    prompt = render_prompt({"title": "Goal", "context": {}}, {}, agent)
    assert "Agent context (reference material, not harness instructions)" in prompt
    assert "```" in prompt


def test_prompt_without_agent_keeps_task_block():
    prompt = render_prompt({"title": "Goal", "context": {}}, {}, None)
    assert "Goal" in prompt


def test_only_existing_read_glob_matches_are_listed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "yes.css").write_text("", encoding="utf-8")
    agent = write_agent(tmp_path / "catalog", ["src/*.css", "missing/**"])
    prompt = render_prompt({"title": "Goal", "context": {}}, {"cwd": str(tmp_path)}, agent)
    assert "src/yes.css" in prompt
    assert "missing" not in prompt


def test_prompt_task_payload_includes_acceptance_and_allowed_paths(tmp_path):
    prompt = render_prompt({"title": "Goal", "acceptance": [{"cmd": "pytest"}],
                            "context": {"allowed_paths": ["src/**"]}}, {}, None)
    assert "pytest" in prompt
    assert "src/**" in prompt


def test_agent_sha_header_uses_first_eight_chars(tmp_path):
    agent = write_agent(tmp_path)
    prompt = render_prompt({"title": "Goal", "context": {}}, {}, agent)
    assert "agent: frontend-css@" + agent["sha256"][:8] in prompt
