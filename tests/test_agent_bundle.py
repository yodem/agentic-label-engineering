import json
import re
from pathlib import Path

from ale.agentcat import load_catalog, resolve_agent, split_body
from ale.frontmatter import parse_frontmatter
from ale.validate import load_schema, validate


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
REFS = ROOT / "catalog" / "refs"


def test_agent_bundle_and_routing_fixtures():
    catalog = load_catalog([str(AGENTS)])
    assert len(catalog) >= 30

    schema = load_schema("agent.schema.json")
    forbidden = re.compile(r"MCP|Opus|Agent Teams|Browser Automation|CC 2\.|Claude Code 2|Delegation \(CC")
    for path in sorted(REFS.rglob("*.md")):
        source = path.read_text(encoding="utf-8")
        assert "<<<<<<<" not in source and ">>>>>>>" not in source
        assert not re.search(r"—|/Users/|/home/|@gmail", source)

    for path in sorted(AGENTS.rglob("*.md")):
        source = path.read_text(encoding="utf-8")
        assert "<<<<<<<" not in source and ">>>>>>>" not in source
        assert not re.search(r"—|/Users/|/home/|@gmail", source)
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
            if read.startswith("catalog/refs/"):
                assert (ROOT / read).is_file(), (path, read)

    for expected_path in sorted((ROOT / "tests/fixtures/agents").glob("*/expected.json")):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        result = resolve_agent(
            catalog, expected["role"], expected.get("sub"), expected["phase"]
        )
        assert result["matched"] == expected["matched"], expected_path


def test_reference_skills_have_origin_first_line_and_no_conflict_markers():
    skills = sorted(REFS.glob("*/SKILL.md"))
    assert skills
    for path in skills:
        assert path.read_text(encoding="utf-8").splitlines()[0].startswith("origin:"), path


def test_bundle_keeps_reference_material_outside_plugin_agents_tree():
    assert (REFS / "api-design" / "SKILL.md").is_file()
    assert not any("_refs" in path.relative_to(AGENTS).parts
                   for path in AGENTS.rglob("*"))
    for agent_path in AGENTS.rglob("*.md"):
        frontmatter, _ = parse_frontmatter(agent_path.read_text(encoding="utf-8"))
        for read in frontmatter["reads"]:
            assert not read.startswith("agents/_refs/"), (agent_path, read)
            if read.startswith("catalog/refs/"):
                assert (ROOT / read).is_file(), (agent_path, read)


HANDBOOK_SKILL = ROOT / "skills" / "agent-handbook" / "SKILL.md"
REFS_FIXTURES = ROOT / "tests" / "fixtures" / "refs"
# A CandleKeep-style id (c + 24 lowercase letters or digits), a home path, a personal
# address, or an em dash has no place in the public skill or its fixtures.
NOT_PORTABLE = re.compile(r"\bc[a-z0-9]{24}\b|/Users/|/home/|~/|@gmail|\u2014")


def test_agent_handbook_skill_and_refs_fixtures_are_portable():
    paths = [HANDBOOK_SKILL] + sorted(REFS_FIXTURES.glob("*.json"))
    assert len(paths) >= 2
    for path in paths:
        found = NOT_PORTABLE.search(path.read_text(encoding="utf-8"))
        assert found is None, (path, found.group(0) if found else None)


def test_agent_handbook_skill_names_every_step_and_part_zero():
    frontmatter, body = parse_frontmatter(HANDBOOK_SKILL.read_text(encoding="utf-8"))
    assert frontmatter["name"] == "agent-handbook"
    assert frontmatter["description"]
    for step in range(1, 7):
        assert "\n%d. **" % step in body, step
    assert "Part 0" in body and "decision matrix" in body.lower()
    assert "ALE_REFS_FILE" in body and "refs_file" in body
    assert "in place" in body


def test_example_refs_file_resolves_every_shipped_agent_and_reads_part_zero_first():
    from ale import refs as REFS_MOD
    refs = REFS_MOD.load_refs(str(REFS_FIXTURES / "example.json"))
    read_first = {entry["read_first"] for entry in refs.values()}
    assert len(read_first) == 1
    catalog = load_catalog([str(AGENTS)])
    unresolved = [key for key, agent in catalog.items() if REFS_MOD.resolve(refs, agent) is None]
    assert unresolved == []
