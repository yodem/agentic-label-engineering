"""Claude Code loads every Markdown file under a plugin's agents/ as a subagent."""
from pathlib import Path

from ale.frontmatter import parse_frontmatter


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"


def test_plugin_agents_dir_holds_only_role_agents():
    files = sorted(path for path in AGENTS.rglob("*") if path.is_file())
    assert all(path.suffix == ".md" for path in files), files
    assert len(files) == 32
    assert not any("_refs" in path.parts for path in files)


def test_every_plugin_agent_has_claude_required_identity_fields():
    for path in sorted(AGENTS.rglob("*.md")):
        frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert isinstance(frontmatter.get("name"), str) and frontmatter["name"].strip(), path
        description = frontmatter.get("description")
        assert isinstance(description, str) and description.strip(), path


def test_plugin_agent_names_are_unique():
    seen = {}
    for path in sorted(AGENTS.rglob("*.md")):
        frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        name = frontmatter["name"]
        assert name not in seen, (name, seen.get(name), path)
        seen[name] = path
