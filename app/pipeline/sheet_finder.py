"""Phase 1 — Identify which pages in the drawing set carry the data we need.

For this drawing set the relevant sheets are:
  A101 — FINISH PLAN & FINISH SCHEDULE (material zones + schedule table)
  A102 — FIXTURES, FURNITURE & LIFE SAFETY PLAN (carries pre-printed room areas)
  A100 — CONSTRUCTION PLAN (dimensions, room IDs)

Architectural sheets in this set are landscape (rotation=270, mediabox 1728x2592).
The sheet identifier appears near the end of the extracted text in the
titleblock block, after the project info, e.g. "...SCB / A101 / FINISH PLAN
& FINISH SCHEDULE".

Strategy:
  1. Iterate pages, extract text.
  2. Search the LAST 30% of the text (= titleblock area in reading order) for
     a sheet number like A1\\d{2}.
  3. Use that as the page's primary sheet id.
  4. Score the page on flooring relevance (FINISH PLAN keyword, "VINYL FLOORING",
     pre-printed SF labels, etc.) but use the sheet id as the authoritative
     marker.
"""
from __future__ import annotations
import re
from pathlib import Path

import fitz  # type: ignore[import-not-found]

from .models import SheetCandidate


SHEET_NUM_RE = re.compile(r"\b([AGSEFM])(\d{3,4})\b")
SF_LABEL_RE = re.compile(r"\b\d{2,5}(?:\.\d{1,2})?\s*S(?:Q|\.)?\s*F(?:T|\.)?\b", re.IGNORECASE)


def _extract_sheet_id(text: str) -> str:
    """Architectural titleblocks place the sheet number at the very end of the
    text stream, often as the last ``A###`` token. Search backward."""
    matches = list(SHEET_NUM_RE.finditer(text))
    if not matches:
        return ""
    # prefer last 35% of text (titleblock zone)
    cut = int(len(text) * 0.65)
    tail_matches = [m for m in matches if m.start() >= cut]
    chosen = tail_matches[-1] if tail_matches else matches[-1]
    return chosen.group(0)


def _classify(text_upper: str, sheet_id: str) -> str:
    if "FINISH PLAN" in text_upper and "FINISH SCHEDULE" in text_upper:
        return "finish_plan"
    if "FIXTURES" in text_upper and ("LIFE SAFETY" in text_upper or "OCCUPANCY" in text_upper):
        return "fixtures_plan"
    if "CONSTRUCTION PLAN" in text_upper and "DOOR" in text_upper:
        return "construction_plan"
    if "REFLECTED CEILING" in text_upper:
        return "rcp"
    if "DEMOLITION" in text_upper:
        return "demolition"
    if "POWER PLAN" in text_upper:
        return "power_plan"
    if "ELEVATIONS" in text_upper or "ELEVATION" in text_upper:
        return "elevations"
    if "DETAILS" in text_upper:
        return "details"
    if "SITE PLAN" in text_upper:
        return "site_plan"
    if "COVER SHEET" in text_upper or "DRAWING SHEET INDEX" in text_upper:
        return "cover"
    if sheet_id.startswith("A1"):
        return "architectural_plan"
    return "other"


def _score(text_upper: str, sheet_id: str, role: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if role == "finish_plan":
        score += 100
        reasons.append("classified as finish_plan")
    if "FINISH PLAN" in text_upper:
        score += 30
        reasons.append("contains FINISH PLAN")
    if "FINISH SCHEDULE" in text_upper:
        score += 25
        reasons.append("contains FINISH SCHEDULE")
    if "VINYL FLOORING" in text_upper:
        score += 20
        reasons.append("contains VINYL FLOORING (zone label)")
    if "PORCELAIN" in text_upper:
        score += 15
        reasons.append("references porcelain")
    sf_hits = SF_LABEL_RE.findall(text_upper)
    if sf_hits:
        bonus = min(40, 4 * len(sf_hits))
        score += bonus
        reasons.append(f"{len(sf_hits)} pre-printed SF labels (+{bonus})")
    if sheet_id == "A101":
        score += 50
        reasons.append("sheet id A101")

    # Penalties
    if role == "rcp":
        score -= 50
        reasons.append("RCP — penalised")
    if role == "site_plan":
        score -= 30
        reasons.append("site plan — penalised")
    if role == "cover":
        score -= 80
        reasons.append("cover sheet (index) — penalised")

    return score, reasons


def find_sheets(pdf_path: str | Path) -> list[SheetCandidate]:
    """Score every page; return all candidates ordered by score (best first)."""
    doc = fitz.open(str(pdf_path))
    out: list[SheetCandidate] = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            text_upper = text.upper()
            sheet_id = _extract_sheet_id(text_upper) or f"page_{i+1}"
            role = _classify(text_upper, sheet_id)
            score, reasons = _score(text_upper, sheet_id, role)
            # Map informal roles to the closed-set used by SheetCandidate
            role_mapped: str
            if role == "finish_plan":
                role_mapped = "finish_plan"
            elif role == "construction_plan":
                role_mapped = "construction_plan"
            elif role in ("fixtures_plan", "architectural_plan"):
                role_mapped = "dimension_plan"
            else:
                role_mapped = "other"
            out.append(SheetCandidate(
                page_index=i,
                sheet_id=sheet_id,
                role=role_mapped,
                score=score,
                reasons=reasons,
            ))
    finally:
        doc.close()
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def find_finish_plan(pdf_path: str | Path) -> SheetCandidate:
    ranked = find_sheets(pdf_path)
    if not ranked:
        raise ValueError("PDF has no pages")
    return ranked[0]


def find_by_sheet_id(pdf_path: str | Path, sheet_id: str) -> SheetCandidate | None:
    """Find the page whose titleblock sheet id matches `sheet_id` (e.g. A102)."""
    sid = sheet_id.upper()
    for c in find_sheets(pdf_path):
        if c.sheet_id.upper() == sid:
            return c
    return None
