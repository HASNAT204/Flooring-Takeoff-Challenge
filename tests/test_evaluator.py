"""Unit tests for the evaluator's accuracy formulas."""
from __future__ import annotations

from evaluator.metrics import evaluate, item_match_f1, per_item_accuracy, weighted_accuracy


def test_per_item_exact():
    assert per_item_accuracy(100, 100) == 1.0


def test_per_item_off_by_10pct():
    assert abs(per_item_accuracy(110, 100) - 0.9) < 1e-9
    assert abs(per_item_accuracy(90, 100) - 0.9) < 1e-9


def test_per_item_zero_gold():
    assert per_item_accuracy(50, 0) == 0.0


def test_per_item_predicted_double_gold():
    # |200 - 100| / 100 = 1.0, accuracy = max(0, 1 - 1) = 0
    assert per_item_accuracy(200, 100) == 0.0


def test_weighted_accuracy_dominant_item():
    # Big item is exact, tiny item is 0% — overall dominated by big item.
    items = [("big", 1000.0, 1000.0), ("tiny", 0.0, 10.0)]
    acc = weighted_accuracy(items)
    # weighted by gold: 1000 * 1.0 + 10 * 0.0 = 1000 / 1010 = 0.9901
    assert 0.989 < acc < 0.991


def test_item_match_f1_perfect():
    p, r, f = item_match_f1({"V-1", "T-1"}, {"V-1", "T-1"})
    assert (p, r, f) == (1.0, 1.0, 1.0)


def test_item_match_f1_partial():
    p, r, f = item_match_f1({"V-1", "T-1", "W-1"}, {"V-1", "T-1", "Y-1"})
    assert p == 2 / 3
    assert r == 2 / 3
    assert abs(f - 2 / 3) < 1e-9


def test_evaluate_basic():
    prediction = {"line_items": [
        {"item_id": "V-1", "quantity": 311.0, "unit": "SF", "description": "vinyl"},
        {"item_id": "T-1", "quantity": 1934.0, "unit": "SF", "description": "tile"},
    ]}
    gold = {
        "V-1": {"quantity": 311.52, "unit": "SF", "description": "vinyl"},
        "T-1": {"quantity": 1934.66, "unit": "SF", "description": "tile"},
    }
    report = evaluate(prediction, gold)
    s = report["summary"]
    assert s["item_match_f1"] == 1.0
    assert s["sf_weighted_accuracy"] > 0.99
    assert s["passes_75pct_threshold"] is True
