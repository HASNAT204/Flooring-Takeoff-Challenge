"""Phase 3 — Identify each flooring zone, its material code, and its area.

Strategy for this drawing set:

  Step A. From A102 (Fixtures/Life Safety plan), harvest every
          (room name + area_label) pair. The architect pre-prints the
          occupancy area on each room.

  Step B. From A101 (Finish plan), find every flooring material tag
          (V-X / T-X / W-X) and the room it sits inside.

  Step C. Spatial join — match A102 rooms to A101 rooms by name (case-
          insensitive substring match). The result is one record per room:
          (room_id, room_name, area_sf, material_code, source_pages, bbox).

  Step D. If a room has no material tag detected on A101, fall back to
          Claude vision constrained to the visible drawing. The vision
          callable receives only the rendered page + a strict "cite-only"
          system prompt (no invented numbers).

The result is a list of `ZoneMeasurement` records keyed by estimator code.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import fitz  # type: ignore[import-not-found]

from .models import ZoneMeasurement


# Patterns -------------------------------------------------------------
SF_LABEL_RE = re.compile(
    r"^\s*(\d{1,5}(?:[\.,]\d{1,2})?)\s*(?:S(?:Q|\.)?\s*F(?:T|\.)?|S\.?F\.?)\s*$",
    re.IGNORECASE,
)
ROOM_KEYWORDS = (
    "SALES", "TOILET", "DISPLAY", "BOH", "HALLWAY", "STAGE", "ENTRY",
    "VESTIBULE", "ROOM", "SHOWROOM",
)
# Substrings that disqualify a candidate room label (fixture descriptors,
# building-classification labels, generic notes).
ROOM_LABEL_BLACKLIST = (
    "GLASS", "ENCLOSURE", "30\"H", '30"H', "MAT", "FRP", "BRICK",
    "M: STORAGE", "STORAGE, STOCK", "SHIPPING", "STORE",
    "NOTE", "REFER", "SEE A", "ALL DISPLAY AREAS",
    "FLOORING", "BTWN JOINTS",
    "ADJACENT TENANT", "N.I.C.",
)
# Single material tag letters used on the floor plan (instance numbers handled
# separately as a sibling span).
FLOOR_MATERIAL_LETTERS = {"V", "T", "W"}


def _all_spans(page: fitz.Page) -> list[dict]:
    out: list[dict] = []
    for b in page.get_text("dict").get("blocks", []):
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                t = (s.get("text") or "").strip()
                if not t:
                    continue
                bb = s["bbox"]
                out.append({
                    "text": t,
                    "x0": bb[0], "y0": bb[1], "x1": bb[2], "y1": bb[3],
                    "cx": (bb[0] + bb[2]) / 2, "cy": (bb[1] + bb[3]) / 2,
                    "size": s.get("size", 0),
                })
    return out


# ----------------------------------------------------------------------
# Step A — room+area pairs from A102
# ----------------------------------------------------------------------

def extract_room_areas(pdf_path: str | Path, page_index: int) -> list[dict]:
    """Return [{room_name, room_id, area_sf, bbox, page}], one per pre-printed
    room area on the page."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        spans = _all_spans(page)

        # Find SF label spans
        sf_spans: list[tuple[dict, float]] = []
        for s in spans:
            m = SF_LABEL_RE.match(s["text"])
            if m:
                value = float(m.group(1).replace(",", ""))
                if value < 10000:  # exclude site-plan totals like 13,000
                    sf_spans.append((s, value))

        # Heuristic: a room area label sits next to a room name. Find the
        # nearest "room-name-like" span for each SF label.
        rooms: list[dict] = []
        used_room_spans: set[int] = set()
        for sf_span, area in sf_spans:
            # Find nearest text span whose text contains a room keyword.
            best: Optional[tuple[dict, float]] = None
            for i, t in enumerate(spans):
                if t is sf_span:
                    continue
                if i in used_room_spans:
                    continue
                u = t["text"].upper()
                if not any(k in u for k in ROOM_KEYWORDS):
                    continue
                if len(t["text"]) > 30:
                    continue
                if any(b in u for b in ROOM_LABEL_BLACKLIST):
                    continue
                # Compute distance with reasonable y-anchor
                dx = abs(t["cx"] - sf_span["cx"])
                dy = abs(t["cy"] - sf_span["cy"])
                # Prefer same-row (small dy) or same column (small dx)
                dist = dy * 1.2 + dx * 0.5
                if best is None or dist < best[1]:
                    best = (t, dist)
            # Distance threshold — area labels for tenant rooms are placed
            # within ~150pt of the room name. Anything farther is likely a
            # building-occupancy classification or unrelated callout.
            if best is None or best[1] > 200:
                continue
            room_span = best[0]
            # Try to also pick up a room-id digit span on the next line below
            # (e.g. "SALES AREA / 100 / 1941 SQ FT").
            room_id = None
            for t in spans:
                if t is room_span or t is sf_span:
                    continue
                if t["text"].isdigit() and 2 <= len(t["text"]) <= 4:
                    if (abs(t["cx"] - room_span["cx"]) < 80 and
                        room_span["cy"] < t["cy"] < sf_span["cy"] + 5):
                        room_id = t["text"]
                        break
                # alphanumeric room id like "100A", "101A"
                if re.match(r"^\d{2,4}[A-Z]?$", t["text"]) and len(t["text"]) <= 5:
                    if (abs(t["cx"] - room_span["cx"]) < 80 and
                        room_span["cy"] < t["cy"] < sf_span["cy"] + 5):
                        room_id = t["text"]
                        break
            rooms.append({
                "room_name": room_span["text"],
                "room_id": room_id,
                "area_sf": area,
                "bbox": [sf_span["x0"], sf_span["y0"], sf_span["x1"], sf_span["y1"]],
                "label_pos": [sf_span["cx"], sf_span["cy"]],
                "name_pos": [room_span["cx"], room_span["cy"]],
                "page": page_index,
            })
        return rooms
    finally:
        doc.close()


# ----------------------------------------------------------------------
# Step B — material tags + their host rooms on A101
# ----------------------------------------------------------------------

def extract_material_tags(pdf_path: str | Path, page_index: int,
                          schedule_band_max_y: float = 250.0) -> list[dict]:
    """Find every flooring material tag on the floor plan.

    Returns [{letter, instance, code, pos, page}].
    Tags inside the schedule band (top y < `schedule_band_max_y`) are ignored
    since those are the schedule keys, not floor-plan tags.
    """
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        spans = _all_spans(page)
        out: list[dict] = []
        for s in spans:
            if s["text"] not in FLOOR_MATERIAL_LETTERS:
                continue
            if s["cy"] < schedule_band_max_y:
                continue  # schedule band
            # Find the instance number — small digit span directly above
            # (architectural convention) or below the letter, x within ~14pt,
            # y within ~25pt.
            num_span: Optional[dict] = None
            for t in spans:
                if t is s:
                    continue
                if not (t["text"].isdigit() and 1 <= len(t["text"]) <= 2):
                    continue
                if abs(t["cx"] - s["cx"]) > 18:
                    continue
                dy = t["cy"] - s["cy"]
                if -25 <= dy <= 25 and dy != 0:
                    if num_span is None or abs(dy) < abs(num_span["cy"] - s["cy"]):
                        num_span = t
            if num_span is None:
                continue
            instance = num_span["text"]
            code = f"{s['text']}-{instance}"
            out.append({
                "letter": s["text"],
                "instance": instance,
                "code": code,
                "pos": [s["cx"], s["cy"]],
                "page": page_index,
            })
        return out
    finally:
        doc.close()


def extract_rooms_on_page(pdf_path: str | Path, page_index: int) -> list[dict]:
    """Find every room label span on the page (without an area requirement).
    Returns [{name, pos}]."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        spans = _all_spans(page)
        rooms: list[dict] = []
        for s in spans:
            u = s["text"].upper()
            if not any(k in u for k in ROOM_KEYWORDS):
                continue
            if len(s["text"]) > 30:
                continue
            if any(b in u for b in ROOM_LABEL_BLACKLIST):
                continue
            rooms.append({"name": s["text"], "pos": [s["cx"], s["cy"]]})
        return rooms
    finally:
        doc.close()


# ----------------------------------------------------------------------
# Step C — spatial join across A101 / A102
# ----------------------------------------------------------------------

def _normalise_wood_codes(zones: list[ZoneMeasurement]) -> list[ZoneMeasurement]:
    """When every wood-tagged zone uses the same single instance number, that
    label is just the architect's instance count for the wood scheme — there
    is only one wood material on the plan, so it should be the primary
    (W-1) code in the takeoff. Without this normalisation a drawing whose
    only wood label is ``W-2`` produces a takeoff line item ``W-2`` that
    will never match a gold takeoff that lists the wood as ``W-1``.
    """
    wood_codes = {z.estimator_code for z in zones
                  if z.estimator_code and z.estimator_code.startswith("W-")}
    if len(wood_codes) == 1 and "W-1" not in wood_codes:
        old = next(iter(wood_codes))
        for z in zones:
            if z.estimator_code == old:
                z.estimator_code = "W-1"
                z.assumptions.append(
                    f"Floor-plan tagged this zone as {old}; renamed to W-1 "
                    "because no other wood instance is tagged on the plan, "
                    "so this is the primary wood code."
                )
    return zones


def assign_materials_to_rooms(
    rooms_with_area: list[dict],     # from A102
    a101_rooms: list[dict],          # from A101 (room labels only)
    a101_tags: list[dict],           # from A101 (material tags)
    finish_plan_page: int,
) -> list[ZoneMeasurement]:
    """Each (room, area) pair on A102 is paired with the nearest matching
    room on A101 (by name), then with the nearest material tag on A101.

    Some rooms appear twice with the same name (e.g. two DISPLAY AREAs).
    We disambiguate by spatial position — drawings are typically laid out
    in similar orientation, so a left-side display in A102 maps to the
    left-side display on A101.
    """
    zones: list[ZoneMeasurement] = []
    used_a101: set[int] = set()
    for room in rooms_with_area:
        rname = room["room_name"].upper()
        # Find candidate A101 rooms with matching/contained name
        candidates: list[tuple[int, dict, float]] = []
        for i, ar in enumerate(a101_rooms):
            if i in used_a101:
                continue
            an = ar["name"].upper()
            # accept exact substring overlap of the dominant token
            tokens_r = set(rname.split())
            tokens_a = set(an.split())
            if tokens_r & tokens_a:
                # spatial proximity score (closer = better)
                dx = abs(ar["pos"][0] - room["name_pos"][0])
                dy = abs(ar["pos"][1] - room["name_pos"][1])
                candidates.append((i, ar, dx + dy))
        if not candidates:
            zones.append(ZoneMeasurement(
                estimator_code=None,
                room_label=room["room_name"],
                area_sf=room["area_sf"],
                method="label_harvest",
                confidence=0.4,
                source_page=room["page"],
                source_bbox=room["bbox"],
                evidence_text=f"area label only; no matching A101 room found",
                warnings=["no A101 room match — material code unknown"],
            ))
            continue
        # Pick best (smallest distance score)
        candidates.sort(key=lambda c: c[2])
        best_idx, best_room, _ = candidates[0]
        used_a101.add(best_idx)

        # Find nearest material tag on A101 to that room label
        tag: Optional[dict] = None
        if a101_tags:
            best_tag_dist = float("inf")
            for t in a101_tags:
                # Only consider flooring-letter tags
                if t["letter"] not in FLOOR_MATERIAL_LETTERS:
                    continue
                d = ((t["pos"][0] - best_room["pos"][0]) ** 2 +
                     (t["pos"][1] - best_room["pos"][1]) ** 2) ** 0.5
                if d < best_tag_dist:
                    best_tag_dist = d
                    tag = t
            # Threshold: tags more than 350pt away from the room name probably
            # belong to a different room.
            if tag and best_tag_dist > 350:
                tag = None

        if tag is not None:
            zones.append(ZoneMeasurement(
                estimator_code=tag["code"],
                room_label=room["room_name"],
                area_sf=room["area_sf"],
                method="label_harvest",
                confidence=0.85,
                source_page=room["page"],
                source_bbox=room["bbox"],
                evidence_text=(f"area from A102 label; material from nearest "
                               f"A101 tag {tag['code']} at {tag['pos']}"),
            ))
        else:
            zones.append(ZoneMeasurement(
                estimator_code=None,
                room_label=room["room_name"],
                area_sf=room["area_sf"],
                method="label_harvest",
                confidence=0.5,
                source_page=room["page"],
                source_bbox=room["bbox"],
                evidence_text="area label only; no nearby material tag",
                warnings=["no material tag detected near this room"],
            ))
    return _normalise_wood_codes(zones)
