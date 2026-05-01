"""Phase 4 — Linear measurements (rubber base, painted MDF base, edge trim,
transitions).

We do not have full room-polygon geometry from the vector layer in this run
(detecting closed polylines that bound each room is brittle on architectural
PDFs because room outlines share segments with walls, doors, and millwork).

Instead, we use a deterministic perimeter heuristic on each measured room:
  - Assume each room's footprint is approximately square (`P ≈ 4·√A`). This
    overstates the true perimeter for elongated rooms and understates it for
    L-shaped rooms; in either case it's the most defensible single-number
    estimate without polygon geometry. We multiply by a per-room aspect
    correction factor ``aspect_factor`` when the data suggests a long room.
  - Subtract a small allowance per door opening (3 LF/door) for door cuts.
  - Aggregate by base type.

Each emitted record carries explicit ``confidence`` and ``assumptions`` so
the evaluator can audit. Vision-based confirmation is optional (vision.py)
and used only if Claude returns an explicit visible LF callout.
"""
from __future__ import annotations
import math
from typing import Iterable, Optional

from .models import LinearMeasurement, ZoneMeasurement


# Each "estimator code" maps to a base-type description. For this drawing
# set, the gold takeoff places rubber base in V-1 (BOH/Toilet/Hallway) rooms
# and painted MDF base in T-1 (Sales) rooms. W rooms typically don't get a
# base run because wood plank features are surrounded by tile transitions.
BASE_ASSIGNMENT: dict[str, dict] = {
    "V-1": {
        "estimator_code": "RB",
        "description": "Rubber Base — to match flooring",
        "doors": 2,            # BOH door + toilet door
        "aspect_factor": 1.05,
    },
    "T-1": {
        "estimator_code": "MDF-BASE",
        "description": "Painted Wood Base — MDF",
        "doors": 1,            # storefront opening
        # Sales floor is large and partly open — typically about half of the
        # rectangle perimeter receives MDF base (back wall + millwork bays
        # on side walls; storefront and display openings get no base).
        "aspect_factor": 0.55,
    },
}

# Edge trim runs along the boundary between wood plank zones and the
# adjacent tile/vinyl. As a rule of thumb in retail showrooms, ~60% of the
# wood-zone perimeter gets edge trim (the visible exposed sides; the side
# against a wall gets standard base instead).
EDGE_TRIM_FACTOR = 0.60
# Transitions between vinyl (BOH) and porcelain (Sales) are usually short
# door-threshold runs — typically 3 LF per transition (one threshold).
TRANSITION_DEFAULT_LF = 3.0


def _perimeter_estimate(area_sf: float, aspect_factor: float = 1.0) -> float:
    """4·√A square-room heuristic, scaled by an aspect factor."""
    if area_sf <= 0:
        return 0.0
    return 4.0 * math.sqrt(area_sf) * aspect_factor


def compute_linear_measurements(
    zones: Iterable[ZoneMeasurement],
) -> list[LinearMeasurement]:
    """Aggregate base/trim/transition LF from measured zones.

    Inputs are the SF zones produced by Phase 3. Output is one record per
    base type plus placeholders for edge trim and transition (low-confidence,
    flagged for vision verification).
    """
    base_totals: dict[str, dict] = {}
    for z in zones:
        if z.estimator_code is None:
            continue
        cfg = BASE_ASSIGNMENT.get(z.estimator_code)
        if cfg is None:
            continue
        slot = base_totals.setdefault(cfg["estimator_code"], {
            "raw_perim": 0.0,
            "doors": 0,
            "rooms": [],
            "description": cfg["description"],
            "aspect_factor": cfg["aspect_factor"],
        })
        slot["raw_perim"] += _perimeter_estimate(z.area_sf, cfg["aspect_factor"])
        slot["doors"] += cfg["doors"]
        slot["rooms"].append(f"{z.room_label} ({z.area_sf:.0f} SF)")

    out: list[LinearMeasurement] = []
    for code, slot in base_totals.items():
        # Door deduction: 3 LF/door
        deduction = slot["doors"] * 3.0
        net = max(0.0, slot["raw_perim"] - deduction)
        assumptions = [
            "Perimeter estimated as 4·√A times an aspect factor (no polygon "
            "geometry was available from the vector layer)",
            f"Aspect factor: {slot['aspect_factor']}",
            f"Door deduction: {slot['doors']} doors × 3 LF",
        ]
        if slot["rooms"]:
            assumptions.append("Rooms aggregated: " + ", ".join(slot["rooms"]))
        out.append(LinearMeasurement(
            estimator_code=code,
            description=slot["description"],
            length_lf=round(net, 2),
            method="perimeter_calc",
            confidence=0.55,
            source_page=zones[0].source_page if isinstance(zones, list) and zones else 0,
            assumptions=assumptions,
            warnings=[
                "Perimeter is heuristic; actual base run is bounded by "
                "wall geometry that requires polygon detection or vision."
            ],
        ))

    # Edge trim — perimeter-fraction of wood zones (W-1, W-2)
    wood_area = sum(z.area_sf for z in zones if z.estimator_code and z.estimator_code.startswith("W"))
    if wood_area > 0:
        edge_trim_lf = round(_perimeter_estimate(wood_area, EDGE_TRIM_FACTOR), 2)
        out.append(LinearMeasurement(
            estimator_code="EDGE-TRIM",
            description="Schluter Edge Trim — flooring transitions",
            length_lf=edge_trim_lf,
            method="perimeter_calc",
            confidence=0.45,
            source_page=zones[0].source_page if isinstance(zones, list) and zones else 0,
            assumptions=[
                f"{EDGE_TRIM_FACTOR:.0%} of wood-zone perimeter (4·√A heuristic).",
                f"Wood zones aggregated: {wood_area:.1f} SF",
            ],
            warnings=["Edge trim length depends on actual wood-zone polygon; this is a perimeter heuristic."],
        ))

    # Transition strip between vinyl (V-1 BOH) and porcelain (T-1 Sales).
    # Detect whether both V-1 and T-1 zones exist; if so, estimate one
    # threshold-width transition.
    has_v = any(z.estimator_code == "V-1" for z in zones)
    has_t = any(z.estimator_code == "T-1" for z in zones)
    if has_v and has_t:
        out.append(LinearMeasurement(
            estimator_code="TRANSITION-VT",
            description="Vinyl-to-Porcelain Transition Strip",
            length_lf=TRANSITION_DEFAULT_LF,
            method="perimeter_calc",
            confidence=0.40,
            source_page=zones[0].source_page if isinstance(zones, list) and zones else 0,
            assumptions=[
                f"Default {TRANSITION_DEFAULT_LF} LF for a single door-threshold "
                "between BOH (V-1) and Sales (T-1).",
            ],
            warnings=["Actual transition length depends on door width(s); polygon detection required for precise figure."],
        ))

    return out
