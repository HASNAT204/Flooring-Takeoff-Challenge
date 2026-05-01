"""Pipeline orchestrator. End-to-end: input PDFs -> outputs.

Public entry point: ``run_pipeline(input_pdfs, job_dir, status_cb=None)``.

Status callbacks let the FastAPI layer drive a progress UI. Each callback
takes ``(stage, progress_0_to_1, message)`` and is also persisted to disk
in ``status.json`` so the polling endpoint can read the latest state.
"""
from __future__ import annotations
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .assembler import assemble
from .linear_extractor import compute_linear_measurements
from .markup import render_marked_pdf
from .schedule_parser import (
    map_to_estimator_codes,
    parse_schedule,
    parse_schedule_with_vision,
)
from .sheet_finder import find_by_sheet_id, find_finish_plan, find_sheets
from .spreadsheet import write_takeoff_xlsx
from .vision import is_available as vision_available, parse_json as vision_json
from .zone_extractor import (
    assign_materials_to_rooms,
    extract_material_tags,
    extract_room_areas,
    extract_rooms_on_page,
)


log = logging.getLogger(__name__)


StatusCb = Callable[[str, float, str], None]


def _noop_status(stage: str, progress: float, message: str) -> None:
    log.info("[%s %.0f%%] %s", stage, progress * 100, message)


def _pick_drawing_pdf(input_pdfs: list[Path]) -> Path:
    """Of the uploaded PDFs, choose the one most likely to contain drawings.

    Strategy: prefer the larger file, the one with more pages, and the one
    whose text contains "FINISH PLAN".
    """
    if len(input_pdfs) == 1:
        return input_pdfs[0]
    best: tuple[float, Path] | None = None
    for p in input_pdfs:
        try:
            size = p.stat().st_size
            ranked = find_sheets(p)
            score = max((c.score for c in ranked), default=0.0) + size / 100_000
            if best is None or score > best[0]:
                best = (score, p)
        except Exception:
            continue
    return best[1] if best else input_pdfs[0]


def run_pipeline(
    input_pdfs: list[Path],
    job_dir: Path,
    status_cb: Optional[StatusCb] = None,
) -> dict:
    """Run all phases. Returns a dict with output paths and the prediction."""
    cb = status_cb or _noop_status
    job_dir.mkdir(parents=True, exist_ok=True)

    # Persist status for HTMX polling -----------------------------------
    status_path = job_dir / "status.json"

    def write_status(stage: str, progress: float, message: str, error: Optional[str] = None) -> None:
        cb(stage, progress, message)
        # Write atomically — readers can be polling concurrently and a
        # partial write produces invalid JSON.
        payload = json.dumps({
            "stage": stage, "progress": progress, "message": message,
            "error": error, "ts": time.time(),
        })
        tmp = status_path.with_suffix(".json.tmp")
        tmp.write_text(payload)
        tmp.replace(status_path)

    try:
        write_status("parsing", 0.05, "Reading uploaded PDFs")
        pdf = _pick_drawing_pdf(input_pdfs)

        write_status("identifying_finish_plan", 0.15, f"Identifying finish plan in {pdf.name}")
        finish = find_finish_plan(pdf)
        a102 = find_by_sheet_id(pdf, "A102")
        if a102 is None:
            # fall back: any 'fixtures'/'occupancy' page
            ranked = find_sheets(pdf)
            a102_candidates = [c for c in ranked if c.role in ("dimension_plan", "construction_plan")]
            a102 = a102_candidates[0] if a102_candidates else finish
        write_status("identifying_finish_plan", 0.20,
                     f"Finish plan = {finish.sheet_id} (page {finish.page_index + 1}); "
                     f"area-source = {a102.sheet_id} (page {a102.page_index + 1})")

        # Phase 2 — Schedule
        write_status("parsing_schedule", 0.30, "Parsing finish schedule")
        schedule = parse_schedule(pdf, finish.page_index)
        # If text-based parse returned no flooring entries (V/T/W), fall
        # back to vision (when an API key is available).
        flooring_count = sum(1 for e in schedule if e.code.split("-")[0] in {"V", "T", "W"})
        if flooring_count == 0 and vision_available():
            write_status("parsing_schedule", 0.35, "Schedule text parse empty — using Claude vision fallback")
            schedule = parse_schedule_with_vision(pdf, finish.page_index, vision_json)
        code_map = map_to_estimator_codes(schedule)

        # Phase 3 — Zones
        write_status("extracting_zones", 0.50, "Harvesting room areas + material tags")
        rooms = extract_room_areas(pdf, a102.page_index)
        a101_rooms = extract_rooms_on_page(pdf, finish.page_index)
        material_tags = extract_material_tags(pdf, finish.page_index)
        zones = assign_materials_to_rooms(rooms, a101_rooms, material_tags, finish.page_index)

        # Phase 4 — Linear
        write_status("measuring_linear", 0.65, "Computing base / trim / transitions")
        linear = compute_linear_measurements(zones)

        # Phase 5 — Assemble
        write_status("assembling", 0.75, "Assembling line items")
        notes: list[str] = []
        if not vision_available():
            notes.append("Pipeline ran without Claude vision (ANTHROPIC_API_KEY not set). "
                         "All measurements are from deterministic text/vector extraction.")
        prediction = assemble(
            project="Lovesac — Corner Shoppes at Stadium",
            schedule=schedule,
            code_map=code_map,
            zones=zones,
            linear=linear,
            sf_source_page=finish.page_index,
            notes=notes,
        )

        # Phase 6 — Markup
        write_status("generating_outputs", 0.85, "Rendering marked-up PDF")
        marked_pdf_path = job_dir / "marked.pdf"
        render_marked_pdf(pdf, finish.page_index, prediction, zones, marked_pdf_path)

        # Phase 7 — Spreadsheet
        write_status("generating_outputs", 0.92, "Writing takeoff.xlsx")
        xlsx_path = job_dir / "takeoff.xlsx"
        write_takeoff_xlsx(prediction, xlsx_path)

        # Persist prediction.json
        prediction_path = job_dir / "prediction.json"
        prediction_path.write_text(prediction.model_dump_json(indent=2))

        write_status("done", 1.0, "Takeoff complete")
        return {
            "marked_pdf": str(marked_pdf_path),
            "takeoff_xlsx": str(xlsx_path),
            "prediction_json": str(prediction_path),
            "prediction": prediction.model_dump(),
        }
    except Exception as e:
        tb = traceback.format_exc()
        log.error("Pipeline failed: %s\n%s", e, tb)
        write_status("error", 0.0, f"Pipeline failed: {e}", error=tb)
        raise
