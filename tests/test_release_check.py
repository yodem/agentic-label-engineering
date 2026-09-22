import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release-check.sh"


def _run(cwd, env=None):
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _write_plugin_json(repo, version):
    plugin_dir = repo / ".claude-plugin"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "ale", "version": version}, indent=2) + "\n"
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    _write_plugin_json(repo, "0.1.0")
    mod_dir = repo / "mod"
    mod_dir.mkdir()
    (mod_dir / "thing.py").write_text("print('hi')\n")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    return repo


def test_fails_when_release_files_changed_without_version_bump(tmp_path):
    repo = _init_repo(tmp_path)

    (repo / "mod" / "thing.py").write_text("print('changed')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change mod without bump")

    result = _run(repo)
    assert result.returncode == 1
    assert "mod/thing.py" in result.stderr


def test_passes_when_version_bumped(tmp_path):
    repo = _init_repo(tmp_path)

    (repo / "mod" / "thing.py").write_text("print('changed')\n")
    _write_plugin_json(repo, "0.2.0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change mod with bump")

    result = _run(repo)
    assert result.returncode == 0
