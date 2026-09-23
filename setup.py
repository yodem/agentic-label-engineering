"""Copy ALE's runtime directories into the wheel as ``ale/_bundle``.

Everything else is configured in ``pyproject.toml``. ``ale.paths.plugin_root``
reads the bundle when the package is installed outside a checkout.
"""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

BUNDLED = ("agents", "catalog", "bin")


class BuildWithBundle(build_py):
    def run(self):
        super().run()
        here = os.path.dirname(os.path.abspath(__file__))
        for name in BUNDLED:
            target = os.path.join(self.build_lib, "ale", "_bundle", name)
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(os.path.join(here, name), target)


if __name__ == "__main__":
    setup(cmdclass={"build_py": BuildWithBundle})
