"""Copy the Claude Code plugin tree into the wheel as ``ale/_bundle``.

Everything else is configured in ``pyproject.toml``. ``ale.paths.plugin_root``
returns the bundle when the package is installed outside a checkout, so the
agent catalog, the launchers and ``claude --plugin-dir`` all work from a wheel.
"""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

BUNDLED = ("agents", "catalog", "bin", "hooks", "skills", ".claude-plugin", "mod")
BUNDLED_FILES = ("EXECUTOR.md",)
SKIP = shutil.ignore_patterns("node_modules", "fixtures", "*.test.ts", "__pycache__")


class BuildWithBundle(build_py):
    def run(self):
        super().run()
        here = os.path.dirname(os.path.abspath(__file__))
        bundle = os.path.join(self.build_lib, "ale", "_bundle")
        for name in BUNDLED:
            target = os.path.join(bundle, name)
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(os.path.join(here, name), target, ignore=SKIP)
        for name in BUNDLED_FILES:
            shutil.copy2(os.path.join(here, name), os.path.join(bundle, name))


if __name__ == "__main__":
    setup(cmdclass={"build_py": BuildWithBundle})
