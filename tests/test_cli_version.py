import os
import subprocess
import sys

import ale
from ale.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_version_flag_prints_ale_and_the_package_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "ale %s\n" % ale.__version__


def test_module_entry_point_prints_the_same_version():
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run([sys.executable, "-m", "ale", "--version"], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "ale %s" % ale.__version__
