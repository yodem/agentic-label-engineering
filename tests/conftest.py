import copy
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def roster():
    return copy.deepcopy(_load("examples/roster.json"))


@pytest.fixture
def label_t01():
    return copy.deepcopy(_load("examples/run/labels/T01.json"))


@pytest.fixture
def label_t02():
    return copy.deepcopy(_load("examples/run/labels/T02.json"))
