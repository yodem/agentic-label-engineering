"""plugin_root() and import_root() in a checkout and in an installed wheel."""
import ast
import os
import subprocess

from ale import paths

REPO = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))


def _plugin_tree(base):
    os.makedirs(os.path.join(base, ".claude-plugin"))
    os.makedirs(os.path.join(base, "bin"))
    open(os.path.join(base, ".claude-plugin", "plugin.json"), "w").close()
    open(os.path.join(base, "bin", "ale-spawn"), "w").close()


def test_checkout_layout_uses_the_package_parent(tmp_path):
    package = tmp_path / "repo" / "ale"
    package.mkdir(parents=True)
    _plugin_tree(str(tmp_path / "repo"))
    assert paths.plugin_root(str(package)) == str(tmp_path / "repo")
    assert paths.import_root(str(package)) == str(tmp_path / "repo")


def test_installed_layout_uses_the_bundle_and_imports_from_site_packages(tmp_path):
    package = tmp_path / "site-packages" / "ale"
    _plugin_tree(str(package / "_bundle"))
    assert paths.plugin_root(str(package)) == str(package / "_bundle")
    assert paths.import_root(str(package)) == str(tmp_path / "site-packages")


def test_an_unrelated_agents_package_in_site_packages_is_not_a_checkout(tmp_path):
    site = tmp_path / "site-packages"
    (site / "agents").mkdir(parents=True)          # e.g. the openai-agents distribution
    (site / "agents" / "__init__.py").write_text("")
    package = site / "ale"
    _plugin_tree(str(package / "_bundle"))
    assert paths.plugin_root(str(package)) == str(package / "_bundle")


def test_this_checkout_resolves_to_the_repo_root():
    assert paths.plugin_root() == REPO
    assert os.path.isfile(os.path.join(REPO, "agents", "general.md"))
    assert os.access(os.path.join(REPO, "bin", "ale-spawn"), os.X_OK)


def _module_constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("%s not found in setup.py" % name)


def test_setup_bundles_the_whole_plugin_and_the_sdist_carries_it():
    with open(os.path.join(REPO, "setup.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    bundled = _module_constant(tree, "BUNDLED")
    # agents/catalog/bin for the runtime; hooks/.claude-plugin/skills/mod so
    # `claude --plugin-dir <bundle>` loads the same plugin as a checkout.
    assert set(bundled) >= {"agents", "catalog", "bin", "hooks", ".claude-plugin", "skills", "mod"}
    with open(os.path.join(REPO, "MANIFEST.in"), encoding="utf-8") as handle:
        manifest = handle.read()
    assert all("graft %s" % name in manifest for name in bundled)
    assert all("include %s" % name in manifest for name in _module_constant(tree, "BUNDLED_FILES"))
    for name in bundled:
        assert os.path.isdir(os.path.join(REPO, name)), name


def test_release_check_watches_every_bundled_directory():
    with open(os.path.join(REPO, "setup.py"), encoding="utf-8") as handle:
        bundled = _module_constant(ast.parse(handle.read()), "BUNDLED")
    with open(os.path.join(REPO, "scripts", "release-check.sh"), encoding="utf-8") as handle:
        watched = next(line for line in handle if "git diff --name-only" in line)
    for name in bundled:
        assert " %s" % name in watched, name


def test_spawn_uses_the_request_python_and_import_root(tmp_path):
    request = tmp_path / "req.json"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do it")
    request.write_text(
        '{"executor": "codex-exec", "cwd": "%s", "prompt_file": "%s", "env": {"ALE_TASK": "T1", '
        '"ALE_AGENT": "a", "ALE_PLUGIN_ROOT": "/site/ale/_bundle", "ALE_IMPORT_ROOT": "/site", '
        '"ALE_PYTHON": "/venv/bin/python"}}' % (tmp_path, prompt))
    env = {k: v for k, v in os.environ.items() if k not in ("ALE_BIN", "ALE_PYTHON", "ALE_IMPORT_ROOT",
                                                            "ALE_PLUGIN_ROOT", "PYTHONPATH")}
    env["ALE_SPAWN_DRY"] = "1"
    out = subprocess.run([os.path.join(REPO, "bin", "ale-spawn"), str(request)], env=env,
                         capture_output=True, text=True, check=True).stdout
    assert "ALE_BIN='/venv/bin/python' -m ale" in out
    assert "PYTHONPATH=/site" in out
