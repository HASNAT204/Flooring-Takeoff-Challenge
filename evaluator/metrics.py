"""Accuracy metrics. Three numbers reported transparently:

  - item_match_f1     : F1 of code matches between prediction and gold
  - sf_weighted       : 1 - |pred-gold|/gold per SF item, weighted by gold qty
  - lf_weighted       : same for LF items
  - aggregate         : equal-weight mean of (sf_weighted, lf_weighted)
"""
from __future__ import annotations
from typing import Iterable


def per_item_accuracy(pred: float, gold: float) -> float:
    if gold <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(pred - gold) / gold)


def weighted_accuracy(items: Iterable[tuple[str, float, float]]) -> float:
    """items = iterable of (code, pred_qty, gold_qty). Returns gold-quantity-
    weighted mean of per-item accuracy."""
    total_w = 0.0
    total = 0.0
    for code, pred, gold in items:
        if gold <= 0:
            continue
        total += gold * per_item_accuracy(pred, gold)
        total_w += gold
    return total / total_w if total_w > 0 else 0.0


def item_match_f1(pred_codes: set[str], gold_codes: set[str]) -> tuple[float, float, float]:
    if not pred_codes and not gold_codes:
        return 1.0, 1.0, 1.0
    matched = pred_codes & gold_codes
    precision = len(matched) / len(pred_codes) if pred_codes else 0.0
    recall = len(matched) / len(gold_codes) if gold_codes else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def evaluate(prediction: dict, gold: dict) -> dict:
    """Return a structured comparison report."""
    pred_items = {it["item_id"]: it for it in prediction.get("line_items", [])}
    gold_items = {k: v for k, v in gold.items() if not k.startswith("_")}

    # Per-line-item rows
    rows: list[dict] = []
    sf_pairs: list[tuple[str, float, float]] = []
    lf_pairs: list[tuple[str, float, float]] = []
    all_codes = sorted(set(pred_items.keys()) | set(gold_items.keys()))
    for code in all_codes:
        p = pred_items.get(code)
        g = gold_items.get(code)
        pq = float(p["quantity"]) if p else 0.0
        gq = float(g["quantity"]) if g else 0.0
        unit = (p or g)["unit"] if (p or g) else "?"
        acc = per_item_accuracy(pq, gq) if g else (0.0 if p else 1.0)
        rows.append({
            "code": code,
            "predicted": pq,
            "gold": gq,
            "delta": round(pq - gq, 3),
            "accuracy_pct": round(acc * 100.0, 1),
            "unit": unit,
            "in_pred": p is not None,
            "in_gold": g is not None,
            "pred_description": p["description"] if p else "",
            "gold_description": g["description"] if g else "",
        })
        if g and unit == "SF":
            sf_pairs.append((code, pq, gq))
        elif g and unit == "LF":
            lf_pairs.append((code, pq, gq))

    sf_weighted = weighted_accuracy(sf_pairs)
    lf_weighted = weighted_accuracy(lf_pairs)
    # Aggregate is the equal-weight mean of the unit-classes that actually
    # have data — averaging 0% LF accuracy in when there are no LF items
    # would punish the score artificially.
    parts = []
    if sf_pairs:
        parts.append(sf_weighted)
    if lf_pairs:
        parts.append(lf_weighted)
    aggregate = sum(parts) / len(parts) if parts else 0.0
    precision, recall, f1 = item_match_f1(set(pred_items.keys()), set(gold_items.keys()))

    return {
        "summary": {
            "item_match_precision": round(precision, 3),
            "item_match_recall": round(recall, 3),
            "item_match_f1": round(f1, 3),
            "sf_weighted_accuracy": round(sf_weighted, 3),
            "lf_weighted_accuracy": round(lf_weighted, 3),
            "aggregate_accuracy": round(aggregate, 3),
            "passes_75pct_threshold": aggregate >= 0.75,
        },
        "rows": rows,
        "gold_unclassified": gold.get("_unclassified", []),
    }
