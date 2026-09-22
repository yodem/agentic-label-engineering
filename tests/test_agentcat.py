from pathlib import Path

import pytest

from ale.agentcat import CatalogError, load_catalog, resolve_agent, split_body


def agent_text(name, role, sub, phases=None, body="core body", extra=""):
    phases = phases or ["implement"]
    phase_text = ", ".join(phases)
    return (
        "---\n"
        "name: %s\nrole: %s\nsub: %s\nphases: [%s]\n"
        "model_tier_min: standard\nreads: []\n"
        "rules:\n  deny_paths: []\n  deny_tools: []\n  require_before_submit: []\n"
        "checklist: []\norigin: none\nversion: 1\n%s---\n%s\n"
    ) % (name, role, sub, phase_text, extra, body)


def write_agent(root, relative, **kwargs):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(agent_text(**kwargs), encoding="utf-8")
    return path


def test_split_body_without_marker_keeps_all_core():
    assert split_body("core\n") == ("core\n", "")


def test_split_body_divides_core_and_harness():
    assert split_body("core\n<!-- harness: claude-code -->\nharness\n") == ("core\n", "harness\n")


def test_split_body_rejects_second_marker():
    with pytest.raises(CatalogError, match="marker"):
        split_body("a\n<!-- harness: claude-code -->\nb\n<!-- harness: claude-code -->\n")


def test_exact_resolution(tmp_path):
    write_agent(tmp_path, "frontend/css.md", name="css", role="frontend", sub="css", phases=["implement"])
    catalog = load_catalog([str(tmp_path)])
    assert resolve_agent(catalog, "frontend", "css", "implement")["matched"] == "exact"


def test_phase_mismatch_uses_existing_exact_specialty(tmp_path):
    write_agent(tmp_path, "frontend/css.md", name="css", role="frontend", sub="css", phases=["review"])
    catalog = load_catalog([str(tmp_path)])
    assert resolve_agent(catalog, "frontend", "css", "implement")["matched"] == "phase-mismatch"


def test_role_default_resolution(tmp_path):
    write_agent(tmp_path, "frontend/_default.md", name="frontend-default", role="frontend", sub="_default")
    catalog = load_catalog([str(tmp_path)])
    assert resolve_agent(catalog, "frontend", "css", "implement")["matched"] == "role-default"


def test_general_fallback_resolution(tmp_path):
    write_agent(tmp_path, "general.md", name="general", role="general", sub="general")
    catalog = load_catalog([str(tmp_path)])
    assert resolve_agent(catalog, "backend", "api", "test")["matched"] == "general"


def test_earlier_root_shadows_later_root(tmp_path):
    project = tmp_path / "project"
    bundle = tmp_path / "bundle"
    first = write_agent(project, "frontend/css.md", name="project-css", role="frontend", sub="css")
    write_agent(bundle, "frontend/css.md", name="bundle-css", role="frontend", sub="css")
    assert load_catalog([str(project), str(bundle)])[
        "frontend/css"]["name"] == "project-css"
    assert load_catalog([str(project), str(bundle)])[
        "frontend/css"]["path"] == str(first)


def test_duplicate_key_in_same_root_raises(tmp_path, monkeypatch):
    write_agent(tmp_path, "frontend/css.md", name="one", role="frontend", sub="css")
    directory = str(tmp_path / "frontend")
    monkeypatch.setattr("ale.agentcat.os.walk", lambda root: [(directory, [], ["css.md", "css.md"])])
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog([str(tmp_path)])


@pytest.mark.parametrize("field,value", [
    ("name", ""),
    ("role", "other"),
    ("sub", "wrong"),
    ("phases", "[nope]"),
    ("model_tier_min", "unknown"),
    ("version", "zero"),
])
def test_schema_errors_name_the_file(tmp_path, field, value):
    path = tmp_path / "frontend" / "css.md"
    path.parent.mkdir(parents=True)
    text = agent_text("css", "frontend", "css")
    if field == "name":
        text = text.replace("name: css", "name: \"\"")
    elif field == "role":
        text = text.replace("role: frontend", "role: other")
    elif field == "sub":
        text = text.replace("sub: css", "sub: wrong")
    elif field == "phases":
        text = text.replace("phases: [implement]", "phases: [nope]")
    elif field == "model_tier_min":
        text = text.replace("model_tier_min: standard", "model_tier_min: unknown")
    elif field == "version":
        text = text.replace("version: 1", "version: zero")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match="css.md"):
        load_catalog([str(tmp_path)])


def test_sha256_is_stable_across_loads(tmp_path):
    write_agent(tmp_path, "frontend/css.md", name="css", role="frontend", sub="css")
    first = load_catalog([str(tmp_path)])["frontend/css"]["sha256"]
    second = load_catalog([str(tmp_path)])["frontend/css"]["sha256"]
    assert first == second
    assert len(first) == 64


def test_infra_role_maps_to_devops_infra(tmp_path):
    write_agent(tmp_path, "devops/infra.md", name="infra", role="devops", sub="infra")
    ref = resolve_agent(load_catalog([str(tmp_path)]), "infra", "infra", "implement")
    assert ref["key"] == "devops/infra"


def test_none_sub_goes_to_general(tmp_path):
    write_agent(tmp_path, "frontend/css.md", name="css", role="frontend", sub="css")
    write_agent(tmp_path, "general.md", name="general", role="general", sub="general")
    ref = resolve_agent(load_catalog([str(tmp_path)]), "frontend", None, "implement")
    assert ref["key"] == "general"


def test_cross_cutting_resolution_under_unknown_role(tmp_path):
    write_agent(tmp_path, "_cross/debugging.md", name="debug", role="_cross", sub="debugging")
    ref = resolve_agent(load_catalog([str(tmp_path)]), "unknown", "debugging", "implement")
    assert ref["key"] == "_cross/debugging"


def test_missing_catalog_root_is_skipped(tmp_path):
    existing = tmp_path / "bundle"
    write_agent(existing, "general.md", name="general", role="general", sub="general")
    catalog = load_catalog([str(tmp_path / ".ale" / "agents"), str(existing)])
    assert "general" in catalog


def test_real_agents_directory_validates():
    catalog = load_catalog(["agents"])
    assert len(catalog) == 7


def test_symlinked_agent_file_outside_root_fails_closed(tmp_path):
    catalog_root = tmp_path / "catalog"
    outside_root = tmp_path / "outside"
    outside_file = write_agent(
        outside_root, "frontend/css.md", name="css", role="frontend", sub="css"
    )
    linked_file = catalog_root / "frontend" / "css.md"
    linked_file.parent.mkdir(parents=True)
    linked_file.symlink_to(outside_file)
    with pytest.raises(CatalogError, match="css.md"):
        load_catalog([str(catalog_root)])


def test_symlinked_catalog_root_is_allowed(tmp_path):
    real_root = tmp_path / "real-catalog"
    write_agent(real_root, "general.md", name="general", role="general", sub="general")
    linked_root = tmp_path / "catalog-link"
    linked_root.symlink_to(real_root, target_is_directory=True)
    assert "general" in load_catalog([str(linked_root)])


def test_seeded_style_empty_rules_are_filled_with_all_default_keys(tmp_path):
    path = tmp_path / "frontend" / "css.md"
    path.parent.mkdir(parents=True)
    path.write_text('''---
name: frontend-css
role: frontend
sub: css
phases: [implement, review, maintain]
model_tier_min: cheap
reads:
  - "agents/_refs/design-system-tokens/SKILL.md"
rules: {}
checklist:
  - "No hard-coded colours"
origin: orchestkit/frontend-ui-developer@9.8.0
version: 1
---
body
''', encoding="utf-8")
    entry = load_catalog([str(tmp_path)])["frontend/css"]
    assert entry["rules"] == {
        "deny_paths": [],
        "deny_tools": [],
        "require_before_submit": [],
    }


@pytest.mark.parametrize("frontmatter_sub", [None, "null", "_default"])
def test_default_agent_accepts_missing_null_or_default_sub(tmp_path, frontmatter_sub):
    path = tmp_path / "frontend" / "_default.md"
    path.parent.mkdir(parents=True)
    text = agent_text("frontend-default", "frontend", "_default")
    if frontmatter_sub is None:
        text = text.replace("sub: _default\n", "")
    else:
        text = text.replace("sub: _default\n", "sub: %s\n" % frontmatter_sub)
    path.write_text(text, encoding="utf-8")
    entry = load_catalog([str(tmp_path)])["frontend/_default"]
    assert entry["sub"] == "_default"


def test_default_agent_rejects_other_declared_sub(tmp_path):
    path = tmp_path / "frontend" / "_default.md"
    path.parent.mkdir(parents=True)
    text = agent_text("frontend-default", "frontend", "_default").replace(
        "sub: _default\n", "sub: css\n"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match="_default.md must not declare sub 'css'"):
        load_catalog([str(tmp_path)])
