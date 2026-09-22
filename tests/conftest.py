import copy
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config.addinivalue_line("markers", "ale_real_cwd: run without changing the current working directory")


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch, tmp_path, request):
    for name in ("ALE_RUN_DIR", "ALE_ROSTER", "ALE_NOW", "ALE_SPAWN_BIN",
                 "ALE_SPAWN_DRY", "ALE_HERDR", "ALE_BIN"):
        monkeypatch.delenv(name, raising=False)
    if request.node.get_closest_marker("ale_real_cwd") is None:
        monkeypatch.chdir(tmp_path)


@pytest.fixture
def roster():
    return copy.deepcopy(_load("examples/roster.json"))


@pytest.fixture
def label_t01():
    return copy.deepcopy(_load("examples/run/labels/T01.json"))


@pytest.fixture
def label_t02():
    return copy.deepcopy(_load("examples/run/labels/T02.json"))
