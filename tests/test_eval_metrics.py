import math

import pytest

from ale.evalharness.metrics import (accuracy, cohen_kappa, confusion_pairs, coverage_table, percentile,
                                     position_sensitivity)


def test_accuracy_ignores_items_without_gold():
    assert accuracy({"a": "x", "b": "y", "c": "z"}, {"a": "x", "b": "q"}) == {"n": 2, "correct": 1, "accuracy": 0.5}
    assert accuracy({}, {}) == {"n": 0, "correct": 0, "accuracy": None}


def test_coverage_table():
    preds = {"a": ("x", 0.95), "b": ("y", 0.8), "c": ("x", 0.4), "d": (None, None)}
    gold = {"a": "x", "b": "z", "c": "x", "d": "x"}
    rows = coverage_table(preds, gold, [0.5, 0.75, 0.9])
    assert rows[0] == {"threshold": 0.5, "n": 4, "covered": 2, "coverage": 0.5, "correct": 1, "accuracy_covered": 0.5}
    assert rows[2]["covered"] == 1 and rows[2]["accuracy_covered"] == 1.0


def test_coverage_with_nothing_covered():
    assert coverage_table({"a": ("x", 0.1)}, {"a": "x"}, [0.9])[0]["accuracy_covered"] is None


def test_kappa_perfect_none_and_known_value():
    assert cohen_kappa(["x", "y", "x", "y"], ["x", "y", "x", "y"]) == 1.0
    assert cohen_kappa(["x", "x", "y", "y"], ["x", "y", "x", "y"]) == 0.0
    a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert math.isclose(cohen_kappa(a, b), 0.4, abs_tol=1e-9)


def test_kappa_degenerate_inputs():
    assert cohen_kappa([], []) is None
    assert cohen_kappa(["x", "x"], ["x", "x"]) is None
    with pytest.raises(ValueError):
        cohen_kappa(["x"], ["x", "y"])


def test_confusion_pairs_sorted_by_count():
    got = confusion_pairs({"a": "x", "b": "x", "c": "y", "d": "z"}, {"a": "y", "b": "y", "c": "x", "d": "z"})
    assert got == [{"gold": "y", "predicted": "x", "count": 2}, {"gold": "x", "predicted": "y", "count": 1}]


def test_percentile():
    assert percentile([], 50) is None and percentile([7], 95) == 7
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([1, 2, 3, 4, 5], 100) == 5


def test_position_sensitivity():
    rows = [{"id": "a", "perm": 0, "choice": "x"}, {"id": "a", "perm": 1, "choice": "x"},
            {"id": "b", "perm": 0, "choice": "x"}, {"id": "b", "perm": 1, "choice": "y"},
            {"id": "c", "perm": 0, "choice": "x"}]
    assert position_sensitivity(rows) == {"items_with_repeats": 2, "unstable": 1, "rate": 0.5}
    assert position_sensitivity([])["rate"] is None
