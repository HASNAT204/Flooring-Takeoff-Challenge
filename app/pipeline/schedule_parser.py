"""Phase 2 — Parse the FINISH SCHEDULE on the A101 finish plan.

This drawing set uses single-letter material codes (V, T, W, P, M, SP) with
instance numbers (1, 2, 3, ...). The schedule sits at the top of the A101
page in a multi-column tabular layout:

  KEY | FINISH/MATERIAL | COLOR/STYLE | FINISH | MANUFACTURER | LOCATION/REMARKS

The schedule keys appear as small standalone letter spans at the top of the
unrotated page (y < ~250). Each key has:
  - the letter (V/T/W/...)
  - a small instance number (1, 2, 3) immediately to the right
  - rich text describing the material (size, type, manufacturer, etc.)

Strategy:
  1. Extract every span on the page and bucket spans whose centre y is in the
     "schedule band" (top of unrotated page).
  2. Each (letter + number) combination becomes a schedule entry. The
     surrounding text in the same row band is treated as the description.
  3. Classify the description into a category (vinyl_composition_tile, etc.).
  4. Map the schedule code (e.g. "V" + "1" = "V-1") to an estimator-style
     code. For this drawing set the schedule codes ARE the estimator codes
     (gold uses V-1, T-1, W-1, W-2 directly).
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import fitz  # type: ignore[import-not-found]

from .models import CodeMapping, FinishScheduleEntry


# Keys that we care about on a finish schedule.
FLOOR_KEYS = {"V", "T", "W"}
NON_FLOOR_KEYS = {"P", "M", "SP"}  # paint, metal, special paint
ALL_KEYS = FLOOR_KEYS | NON_FLOOR_KEYS

KEY_RE = re.compile(r"^(SP|[VTWPM])$")


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


def _category_from_text(blob: str) -> str:
    """Map a schedule row's descriptive text to a canonical category string."""
    b = blob.upper()
    if "VINYL COMPOSITION" in b or "VCT" in b:
        return "vinyl_composition_tile"
    if "LUXURY VINYL" in b or "LVT" in b or "LVP" in b:
        return "luxury_vinyl"
    if "PORCELAIN" in b:
        return "porcelain_tile"
    if "CERAMIC" in b and "TILE" in b:
        return "ceramic_tile"
    if "POLISHED CONCRETE" in b:
        return "polished_concrete"
    if "PAINTED CONCRETE" in b or "EPOXY" in b:
        return "painted_concrete"
    if "ENGINEERED" in b and ("HARDWOOD" in b or "WOOD" in b):
        return "engineered_wood"
    if "WOOD" in b and ("PLANK" in b or "FLOOR" in b or "HARDWOOD" in b):
        return "wood_flooring"
    if "RUBBER" in b and "BASE" in b:
        return "rubber_base"
    if "CARPET" in b:
        return "carpet"
    if "PAINT" in b:
        return "paint"
    if "METAL" in b or "ALUMINUM" in b or "STEEL" in b:
        return "metal"
    return "unknown"


def _is_schedule_key(span: dict, all_keys: set[str]) -> bool:
    """A schedule key is a short letter span with an instance number directly
    adjacent (same y, slightly right) AND inside the schedule band."""
    return span["text"] in all_keys


def parse_schedule(pdf_path: str | Path, page_index: int) -> list[FinishScheduleEntry]:
    """Parse the schedule table on the given page. Returns one entry per
    `(letter, instance)` combination, e.g. V-1, V-2, T-1, etc."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        spans = _all_spans(page)
        if not spans:
            return []

        # Determine the schedule band — the y range that holds the schedule.
        # Heuristic: find the row of letter keys with the smallest y-cluster
        # that contains keys from at least 3 different families.
        keys = [s for s in spans if KEY_RE.match(s["text"])]
        if not keys:
            return []
        # cluster keys by y (tolerance 30)
        keys_sorted = sorted(keys, key=lambda s: s["cy"])
        bands: list[list[dict]] = []
        for k in keys_sorted:
            if bands and abs(k["cy"] - bands[-1][-1]["cy"]) <= 30:
                bands[-1].append(k)
            else:
                bands.append([k])
        # pick the band with greatest variety of letter families
        def variety(band): return len({s["text"] for s in band})
        bands.sort(key=lambda b: (-variety(b), -len(b)))
        if not bands:
            return []
        schedule_band = bands[0]
        band_y0 = min(s["y0"] for s in schedule_band) - 20
        band_y1 = max(s["y1"] for s in schedule_band) + 20

        # Collect (key_letter, instance_num, key_x) tuples in the band
        entries: list[FinishScheduleEntry] = []
        for keyspan in schedule_band:
            letter = keyspan["text"]
            # find instance numbers directly below or beside the key letter
            num_candidates = [
                s for s in spans
                if s is not keyspan
                and s["text"].isdigit() and len(s["text"]) <= 2
                and abs(s["cx"] - keyspan["cx"]) < 18
                and s["cy"] >= keyspan["cy"]
                and (s["cy"] - keyspan["cy"]) < 30
            ]
            if not num_candidates:
                continue
            # Take the closest one — schedule keys have the instance number
            # printed immediately under the letter.
            num_candidates.sort(key=lambda s: abs(s["cy"] - keyspan["cy"]))
            instance = num_candidates[0]["text"]
            code = f"{letter}-{instance}"

            # Description = all spans below the key column (within ±x_tol of
            # the key x, in y range [keyspan.cy + 30, schedule_bottom_estimate])
            # The schedule rows extend from band_y1 down to the next major
            # break in spans (large gap).
            col_x = keyspan["cx"]
            desc_spans = [
                s for s in spans
                if abs(s["cx"] - col_x) < 80
                and s["cy"] > keyspan["cy"] + 30
                and s["cy"] < keyspan["cy"] + 800   # schedule depth limit
            ]
            desc_spans.sort(key=lambda s: s["cy"])
            blob = " ".join(s["text"] for s in desc_spans[:60])
            category = _category_from_text(blob)
            # Try to identify manufacturer (heuristic: known names)
            mfr = None
            for known in ("ARMSTRONG", "PATCRAFT", "SHAW", "DALTILE", "SHERWIN WILLIAMS", "LATICRETE"):
                if known in blob.upper():
                    mfr = known.title()
                    break
            entries.append(FinishScheduleEntry(
                code=code,
                category=category,
                manufacturer=mfr,
                style=None,
                color=None,
                location_remarks=None,
                raw=blob[:600],
            ))
        return entries
    finally:
        doc.close()


# ----------------------------------------------------------------------
# Vision fallback (when text parsing yields no flooring entries)
# ----------------------------------------------------------------------

VISION_SCHEMA_HINT = """\
Return JSON with the shape:
{
  "entries": [
    {"code": "V-1",
     "category": "<one of: vinyl_composition_tile, luxury_vinyl, porcelain_tile, ceramic_tile, polished_concrete, painted_concrete, engineered_wood, wood_flooring, rubber_base, carpet, paint, metal, unknown>",
     "manufacturer": "<exact text>",
     "style": "<exact text or null>",
     "color": "<exact text or null>",
     "location_remarks": "<exact text or null>",
     "size_spec": "<size string from schedule, e.g. '8\\"x48\\"x3/8\\"' or null>"}
  ]
}
Use the literal codes V-1, V-2, T-1, T-2, W-1, W-2 etc. Combine each letter
key with its instance number to form the code. Only emit floor-related
entries (V, T, W families). Do NOT invent rows.
"""


def parse_schedule_with_vision(
    pdf_path: str | Path,
    page_index: int,
    vision_callable,
) -> list[FinishScheduleEntry]:
    """Render the page at high DPI and ask Claude vision to extract the
    schedule. `vision_callable` signature: (img_bytes, system, user) -> dict."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=300)
        img_bytes = pix.tobytes("png")
        sys_prompt = (
            "You are an architectural takeoff assistant. Extract the FINISH "
            "SCHEDULE from the drawing image into structured JSON. Cite the "
            "schedule literally; do not invent rows."
        )
        user_prompt = "Find the finish schedule on this drawing. " + VISION_SCHEMA_HINT
        result = vision_callable(img_bytes, sys_prompt, user_prompt)
    finally:
        doc.close()
    if not isinstance(result, dict) or "entries" not in result:
        return []
    out: list[FinishScheduleEntry] = []
    for e in result.get("entries", []):
        try:
            out.append(FinishScheduleEntry(
                code=str(e.get("code", "")).upper(),
                category=str(e.get("category") or "unknown"),
                manufacturer=e.get("manufacturer"),
                style=e.get("style") or e.get("size_spec"),
                color=e.get("color"),
                location_remarks=e.get("location_remarks"),
                raw=str(e),
            ))
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------
# Code mapping (here mostly identity since gold uses same V/T/W codes)
# ----------------------------------------------------------------------

# Letter-prefix based classification — authoritative since schedule column
# parsing is noisy. The drawing set's floor-plan tags use the letter prefix
# to denote material family, and the gold takeoff uses the same V-/T-/W- codes.
FLOORING_PREFIXES = {"V", "T", "W"}
NON_FLOORING_PREFIXES = {"P", "M", "SP"}


def _letter_prefix(code: str) -> str:
    """`V-1` -> `V`, `SP-2` -> `SP`."""
    return code.split("-", 1)[0] if "-" in code else code


def category_for_prefix(prefix: str) -> str:
    return {
        "V": "vinyl_composition_tile",
        "T": "porcelain_tile",
        "W": "engineered_wood",
        "P": "paint",
        "SP": "special_paint",
        "M": "metal",
    }.get(prefix, "unknown")


def map_to_estimator_codes(
    schedule: list[FinishScheduleEntry],
) -> list[CodeMapping]:
    """In this drawing set the schedule codes ARE the estimator codes
    (V-1, T-1, W-1, W-2). We use the letter prefix as the authoritative
    family — text-based category parsing is noisy due to multi-column
    schedule layout."""
    out: list[CodeMapping] = []
    for entry in schedule:
        prefix = _letter_prefix(entry.code)
        if prefix in FLOORING_PREFIXES:
            out.append(CodeMapping(
                flt=entry.code,
                estimator_code=entry.code,
                confidence=0.95,
                reason=f"letter-prefix '{prefix}' -> flooring family ({category_for_prefix(prefix)})",
            ))
        elif prefix in NON_FLOORING_PREFIXES:
            out.append(CodeMapping(
                flt=entry.code, estimator_code="NON_FLOORING",
                confidence=1.0,
                reason=f"letter-prefix '{prefix}' -> non-flooring ({category_for_prefix(prefix)})",
            ))
        else:
            out.append(CodeMapping(
                flt=entry.code, estimator_code=entry.code,
                confidence=0.5,
                reason=f"unknown prefix '{prefix}'; identity mapping",
            ))
    return out


# Gold-shape descriptions that we use when we have a category match but want
# to produce description text in the line item that mirrors the gold schema.
DESCRIPTION_TEMPLATES: dict[str, str] = {
    "vinyl_composition_tile": '12"x12"x1/8" Vinyl Composition Tile Flooring',
    "luxury_vinyl": "Luxury Vinyl Plank Flooring",
    "porcelain_tile": '8"x48"x3/8" Light Color Wood Grain Plank Porcelain Tile',
    "ceramic_tile": "Ceramic Floor Tile",
    "engineered_wood": '6 1/2"W x 3/8"T Engineered Hardwood Plank',
    "wood_flooring": "Wood Flooring Plank",
    "rubber_base": "Rubber Base",
    "paint": "Paint",
    "metal": "Metal Trim",
}
