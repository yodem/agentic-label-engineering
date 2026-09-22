import json
import re
from pathlib import Path

from ale.agentcat import load_catalog, resolve_agent, split_body
from ale.frontmatter import parse_frontmatter
from ale.validate import load_schema, validate


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"


def test_agent_bundle_and_routing_fixtures():
    catalog = load_catalog([str(AGENTS)])
    assert len(catalog) >= 30

    schema = load_schema("agent.schema.json")
    forbidden = re.compile(r"MCP|Opus|Agent Teams|Browser Automation|CC 2\.|Claude Code 2|Delegation \(CC")
    for path in sorted(AGENTS.rglob("*.md")):
        source = path.read_text(encoding="utf-8")
        assert "<<<<<<<" not in source and ">>>>>>>" not in source
        assert not re.search(r"—|/Users/|@gmail|yonaigross", source)
        if "_refs" in path.relative_to(AGENTS).parts:
            continue

        frontmatter, body = parse_frontmatter(source)
        assert not validate(frontmatter, schema), path
        assert frontmatter.get("origin"), path
        core, _ = split_body(body)
        assert core.count("<!-- harness: claude-code -->") <= 1, path
        marker = "<!-- harness: claude-code -->"
        before_marker = core.split(marker, 1)[0]
        assert not any(
            forbidden.search(line)
            for line in before_marker.splitlines()
            if line.startswith("## ")
        ), path
        assert "## Status Protocol" in before_marker, path
        assert 1 <= len(frontmatter["checklist"]) <= 12, path
        for read in frontmatter["reads"]:
            if read.startswith("agents/_refs/"):
                assert (ROOT / read).is_file(), (path, read)

    for expected_path in sorted((ROOT / "tests/fixtures/agents").glob("*/expected.json")):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        result = resolve_agent(
            catalog, expected["role"], expected.get("sub"), expected["phase"]
        )
        assert result["matched"] == expected["matched"], expected_path


def test_reference_skills_have_origin_first_line_and_no_conflict_markers():
    skills = sorted((AGENTS / "_refs").glob("*/SKILL.md"))
    assert skills
    for path in skills:
        assert path.read_text(encoding="utf-8").splitlines()[0].startswith("origin:"), path
