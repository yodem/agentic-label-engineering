"""Release metadata stays in lockstep across the three files that carry a version."""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_VERSION = "0.2.10"


def _pyproject_version():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "version not found in pyproject.toml"
    return match.group(1)


def _plugin_json_version():
    data = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return data["version"]


def _ale_version():
    text = (REPO_ROOT / "ale" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', text)
    assert match, "__version__ not found in ale/__init__.py"
    return match.group(1)


def test_pyproject_version_matches_expected():
    assert _pyproject_version() == EXPECTED_VERSION


def test_plugin_json_version_matches_expected():
    assert _plugin_json_version() == EXPECTED_VERSION


def test_ale_version_matches_expected():
    assert _ale_version() == EXPECTED_VERSION


def test_all_three_versions_match_each_other():
    versions = {_pyproject_version(), _plugin_json_version(), _ale_version()}
    assert len(versions) == 1, versions


if __name__ == "__main__":
    sys.exit(0)
