"""plugin_root() finds agents/, catalog/ and bin/ in a checkout and in an installed wheel."""
import ast
import os

from ale import paths


def _layout(base, names):
    for name in names:
        os.makedirs(os.path.join(base, name))


def test_checkout_layout_uses_the_package_parent(tmp_path):
    package = tmp_path / "repo" / "ale"
    package.mkdir(parents=True)
    _layout(str(tmp_path / "repo"), ["agents", "catalog", "bin"])
    assert paths.plugin_root(str(package)) == str(tmp_path / "repo")


def test_installed_layout_uses_the_bundled_copy(tmp_path):
    package = tmp_path / "site-packages" / "ale"
    _layout(str(package / "_bundle"), ["agents", "catalog", "bin"])
    assert paths.plugin_root(str(package)) == str(package / "_bundle")


def test_this_checkout_resolves_to_the_repo_root():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
    assert paths.plugin_root() == repo
    assert os.path.isfile(os.path.join(paths.plugin_root(), "agents", "general.md"))
    assert os.access(os.path.join(paths.plugin_root(), "bin", "ale-spawn"), os.X_OK)


def test_setup_bundles_every_runtime_directory():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
    with open(os.path.join(repo, "setup.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    bundled = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and node.targets[0].id == "BUNDLED")
    assert set(bundled) >= {"agents", "catalog", "bin"}
    with open(os.path.join(repo, "MANIFEST.in"), encoding="utf-8") as handle:
        manifest = handle.read()
    assert all("graft %s" % name in manifest for name in bundled)
