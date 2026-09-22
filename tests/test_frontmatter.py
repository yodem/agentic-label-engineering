import pytest

from ale.frontmatter import FrontmatterError, parse_frontmatter


def test_parses_string_scalar():
    assert parse_frontmatter("---\nname: frontend-css\n---\nbody")[0] == {"name": "frontend-css"}


def test_parses_integer_scalar():
    assert parse_frontmatter("---\nversion: 1\n---\n")[0] == {"version": 1}


def test_parses_boolean_scalar():
    assert parse_frontmatter("---\nenabled: true\n---\n")[0] == {"enabled": True}


def test_parses_null_scalar():
    assert parse_frontmatter("---\nvalue: null\n---\n")[0] == {"value": None}


def test_parses_inline_list():
    assert parse_frontmatter("---\nphases: [implement, review]\n---\n")[0] == {"phases": ["implement", "review"]}


def test_parses_block_list_with_quoted_string():
    assert parse_frontmatter('---\nreads:\n  - "src/styles/**"\n  - plain\n---\n')[0] == {"reads": ["src/styles/**", "plain"]}


def test_parses_nested_mapping_with_inline_and_block_lists():
    text = "---\nrules:\n  deny_paths: [infra/**, '*.sql']\n  deny_tools:\n    - bash\n    - 'git push'\n---\n"
    assert parse_frontmatter(text)[0] == {
        "rules": {"deny_paths": ["infra/**", "*.sql"], "deny_tools": ["bash", "git push"]}
    }


def test_ignores_comments():
    assert parse_frontmatter("---\n# comment\nname: value # trailing\n---\n")[0] == {"name": "value"}


def test_missing_closing_delimiter_reports_line():
    with pytest.raises(FrontmatterError, match="line 3"):
        parse_frontmatter("---\nname: value\n")


def test_tab_indentation_reports_line():
    with pytest.raises(FrontmatterError, match="line 2"):
        parse_frontmatter("---\n\t- item\n---\n")


def test_unknown_construct_reports_line():
    with pytest.raises(FrontmatterError, match="line 2"):
        parse_frontmatter("---\nkey: |\n---\n")


def test_body_is_returned_byte_identically():
    body = "\n\n# heading\r\nline\n"
    assert parse_frontmatter("---\nname: value\n---\n" + body)[1] == body


def test_no_frontmatter_returns_original_text():
    text = "not frontmatter\n---\n"
    assert parse_frontmatter(text) == ({}, text)


def test_accepts_crlf_input_and_preserves_body():
    text = "---\r\nname: value\r\n---\r\nbody\r\n"
    assert parse_frontmatter(text) == ({"name": "value"}, "body\r\n")


def test_quoted_strings_support_escapes():
    assert parse_frontmatter('---\ntext: "a\\nb"\n---\n')[0] == {"text": "a\nb"}


def test_seeded_frontmatter_with_empty_rules_mapping():
    text = '''---
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
'''
    parsed, body = parse_frontmatter(text)
    assert parsed["rules"] == {}
    assert body == "body\n"


def test_parses_flat_inline_mapping_with_scalar_values():
    assert parse_frontmatter('---\nvalues: {a: 1, b: "x"}\n---\n')[0] == {
        "values": {"a": 1, "b": "x"}
    }


def test_parses_empty_inline_list():
    assert parse_frontmatter("---\nvalues: []\n---\n")[0] == {"values": []}


def test_rejects_nested_flow_mapping():
    with pytest.raises(FrontmatterError, match="line 2"):
        parse_frontmatter("---\nvalues: {a: {b: 1}}\n---\n")
