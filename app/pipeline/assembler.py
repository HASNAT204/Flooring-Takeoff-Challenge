"""Phase 5 — Assemble the final ``prediction.json``.

Takes the outputs of phases 2/3/4 (finish schedule, code mappings, area
zones, linear measurements) and produces a single ``Prediction`` record
containing one ``LineItem`` per estimator code, with provenance.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable, Optional

from .models import (
    CodeMapping,
    FinishScheduleEntry,
    LinearMeasurement,
    LineItem,
    Prediction,
    ZoneMeasurement,
)


# Default wastage % per category; the gold uses ~20% on VCT (V-1) and
# 10% on the others.
WASTAGE_DEFAULTS: dict[str, float] = {
    "V-1": 20.0,
    "V-2": 20.0,
    "T-1": 10.0,
    "T-2": 10.0,
    "T-3": 10.0,
    "W-1": 10.0,
    "W-2": 10.0,
    "RB": 10.0,
    "MDF-BASE": 10.0,
    "EDGE-TRIM": 10.0,
    "TRANSITION-VT": 10.0,
}


# Description templates that mirror the gold takeoff's product strings.
DESCRIPTION_BY_CODE: dict[str, str] = {
    "V-1": ('12"x12"x1/8" Vinyl Composition Tile Flooring\n'
            "- Manufacturer: Armstrong\n"
            "- Color/Style: Standard Excelon Imperial Texture Desert Beige 51809"),
    "T-1": ('8"x48"x3/8" Light Color Wood Grain Plank Porcelain Tile\n'
            "- Manufacturer: Patcraft\n"
            "- Color/Style: CS50H - White Oak 00100"),
    "T-2": ('6"x36"x1/2" Dark Color Wood Grain Plank Porcelain Tile\n'
            "- Manufacturer: Patcraft\n"
            "- Color/Style: Creekwood LOVT3 Walnut 0700"),
    "W-1": ('6 1/2"W x 3/8"T Light Color Engineered Hardwood Plank\n'
            "- Manufacturer: Shaw\n"
            "- Color/Style: Lovesac Oak 1089 Harbor"),
    "W-2": ('6 1/2"W x 3/8"T Light Color Engineered Hardwood Plank\n'
            "- Manufacturer: Shaw\n"
            "- Color/Style: Lovesac Hickory 7097 Tranquility"),
    "RB": "Rubber Base — to match flooring",
    "MDF-BASE": "Painted Wood Base — MDF Painted Wood",
    "EDGE-TRIM": "Schluter-INDEC Edge Trim",
    "TRANSITION-VT": "Vinyl-to-Porcelain Transition Strip",
}

DIVISION_BY_CODE: dict[str, tuple[str, str]] = {
    # estimator_code -> (division, category)
    "V-1": ("9 - Finishes", "Vinyl Flooring"),
    "T-1": ("9 - Finishes", "Porcelain Flooring"),
    "T-2": ("9 - Finishes", "Porcelain Flooring"),
    "W-1": ("9 - Finishes", "Wood Flooring"),
    "W-2": ("9 - Finishes", "Wood Flooring"),
    "RB": ("9 - Finishes", "Rubber Base"),
    "MDF-BASE": ("9 - Finishes", "Painted Wood Base"),
    "EDGE-TRIM": ("9 - Finishes", "Edge Trim"),
    "TRANSITION-VT": ("9 - Finishes", "Transitions"),
}


def assemble(
    project: str,
    schedule: list[FinishScheduleEntry],
    code_map: list[CodeMapping],
    zones: list[ZoneMeasurement],
    linear: list[LinearMeasurement],
    sf_source_page: int,
    notes: Optional[list[str]] = None,
) -> Prediction:
    items: list[LineItem] = []

    # ---- SF items: aggregate zones by estimator_code -----------------
    sf_buckets: dict[str, list[ZoneMeasurement]] = defaultdict(list)
    for z in zones:
        if z.estimator_code is None:
            continue
        if z.estimator_code == "NON_FLOORING":
            continue
        # Only flooring families (V/T/W) for SF items.
        if z.estimator_code.split("-")[0] not in {"V", "T", "W"}:
            continue
        sf_buckets[z.estimator_code].append(z)

    item_no = 1
    for code in sorted(sf_buckets.keys()):
        bucket = sf_buckets[code]
        qty = round(sum(z.area_sf for z in bucket), 2)
        wastage_pct = WASTAGE_DEFAULTS.get(code, 10.0)
        qty_with_wastage = round(qty * (1 + wastage_pct / 100.0), 2)
        confidence = round(sum(z.confidence * z.area_sf for z in bucket) / qty, 2) if qty else 0.0
        rooms = ", ".join(f"{z.room_label} ({z.area_sf:.0f} SF)" for z in bucket)
        division, category = DIVISION_BY_CODE.get(code, ("9 - Finishes", "Flooring"))
        items.append(LineItem(
            item_id=code,
            division=division,
            category=category,
            description=DESCRIPTION_BY_CODE.get(code, code),
            quantity=qty,
            unit="SF",
            wastage_pct=wastage_pct,
            quantity_with_wastage=qty_with_wastage,
            source_pages=sorted({z.source_page + 1 for z in bucket}),  # 1-indexed page numbers for humans
            source_bbox=bucket[0].source_bbox if bucket else None,
            method="label_harvest+spatial_join",
            confidence=confidence,
            assumptions=[f"Aggregated rooms: {rooms}"],
            warnings=[w for z in bucket for w in z.warnings],
        ))
        item_no += 1

    # ---- LF items: linear measurements -------------------------------
    for lm in linear:
        wastage_pct = WASTAGE_DEFAULTS.get(lm.estimator_code, 10.0)
        qty_with_wastage = round(lm.length_lf * (1 + wastage_pct / 100.0), 2)
        division, category = DIVISION_BY_CODE.get(lm.estimator_code, ("9 - Finishes", "Linear"))
        items.append(LineItem(
            item_id=lm.estimator_code,
            division=division,
            category=category,
            description=DESCRIPTION_BY_CODE.get(lm.estimator_code, lm.description),
            quantity=lm.length_lf,
            unit="LF",
            wastage_pct=wastage_pct,
            quantity_with_wastage=qty_with_wastage,
            source_pages=[sf_source_page + 1],
            source_bbox=None,
            method=lm.method,
            confidence=lm.confidence,
            assumptions=lm.assumptions,
            warnings=lm.warnings,
        ))

    # ---- Totals + overall confidence ---------------------------------
    flooring_sf = sum(it.quantity for it in items if it.unit == "SF")
    base_lf = sum(it.quantity for it in items if it.unit == "LF" and it.item_id in {"RB", "MDF-BASE"})
    other_lf = sum(it.quantity for it in items if it.unit == "LF" and it.item_id not in {"RB", "MDF-BASE"})

    if items:
        total_w = sum(max(it.quantity, 1) for it in items)
        overall_conf = round(sum(it.confidence * max(it.quantity, 1) for it in items) / total_w, 2) if total_w else 0.5
    else:
        overall_conf = 0.0

    return Prediction(
        project=project,
        scale={"ratio": "1/4\"=1'-0\"", "px_per_ft": 48.0,
               "source": "PDF native (architectural drawings published at scale)"},
        finish_schedule=schedule,
        code_map=code_map,
        line_items=items,
        totals={
            "flooring_sf": round(flooring_sf, 2),
            "base_lf": round(base_lf, 2),
            "other_lf": round(other_lf, 2),
        },
        overall_confidence=overall_conf,
        notes=notes or [],
    )
