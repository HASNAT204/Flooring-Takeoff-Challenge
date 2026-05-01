"""Phase 6 — Generate the marked-up PDF.

We extract the single A101 finish-plan page from the input PDF and overlay
on top of it:
  - a colour-filled rectangle (or simple polygon) for each measured zone,
    centred on the room name;
  - a leader line from the zone centroid to a callout box reading
    e.g. ``T-1 — 1934.7 SF``;
  - a bottom-strip legend with one swatch per estimator code and the
    grand total.

We do NOT detect actual room polygons (the vector layer mixes walls, doors,
fixtures, dimensions). Instead, we centre a generously-sized translucent
rectangle on the room label as a visual evidence indicator. The label tells
the reviewer which room and quantity it represents; the legend totals
provide the audit trail.
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

import fitz  # type: ignore[import-not-found]

from .models import Prediction, ZoneMeasurement


# Colour palette for the estimator codes (RGB 0..1).
CODE_COLOURS: dict[str, tuple[float, float, float]] = {
    "V-1": (0.95, 0.55, 0.85),     # pink/violet
    "V-2": (0.85, 0.45, 0.75),
    "T-1": (0.40, 0.75, 0.50),     # green (matches gold markup)
    "T-2": (0.30, 0.55, 0.45),
    "W-1": (0.85, 0.65, 0.30),     # tan/wood
    "W-2": (0.70, 0.50, 0.20),
}


def _colour_for(code: str) -> tuple[float, float, float]:
    return CODE_COLOURS.get(code, (0.55, 0.55, 0.95))


def _zone_rect(zone: ZoneMeasurement) -> fitz.Rect:
    """Build a rectangle around the room name centroid sized roughly to the
    measured area. ``page.get_text("dict")`` returns coords in the
    unrotated mediabox space, and ``page.draw_rect`` on a rotated page also
    uses mediabox space — so we do NOT apply any rotation transform here.

    Scale: at 1/4"=1'-0" on a PDF whose units are 1 pt = 1/72", one foot
    of real-world distance equals 1/4 inch on paper = 18 PDF points.
    """
    cx = (zone.source_bbox[0] + zone.source_bbox[2]) / 2
    cy = (zone.source_bbox[1] + zone.source_bbox[3]) / 2
    side_ft = (zone.area_sf ** 0.5)
    side_pt = side_ft * 18.0
    half = side_pt / 2.0
    return fitz.Rect(cx - half, cy - half, cx + half, cy + half)


def render_marked_pdf(
    input_pdf: str | Path,
    finish_plan_page_index: int,
    prediction: Prediction,
    zones: list[ZoneMeasurement],
    output_pdf: str | Path,
) -> None:
    """Open the input PDF, copy the finish-plan page into a new doc, draw
    overlays on it, and save."""
    src = fitz.open(str(input_pdf))
    try:
        out = fitz.open()
        out.insert_pdf(src, from_page=finish_plan_page_index, to_page=finish_plan_page_index)
        page = out[0]
        # ``page.rect`` reflects the rotated visible orientation; we use
        # ``page.mediabox`` for drawing so coordinates align with the text
        # bboxes captured during extraction.
        mb = page.mediabox

        # 1. Draw a translucent square per zone -------------------------
        for z in zones:
            if not z.estimator_code or not z.source_bbox:
                continue
            colour = _colour_for(z.estimator_code)
            r = _zone_rect(z)
            page.draw_rect(
                r, color=colour, fill=colour, fill_opacity=0.30, width=1.0,
                overlay=True,
            )
            # 2. Callout box near the rectangle (positioned in mediabox space)
            label = f"{z.estimator_code} - {z.area_sf:.1f} SF"
            cb_w = 160
            cb_h = 32
            # Place callout slightly above the zone rect, clamped to mediabox.
            anchor_x = min(mb.x1 - cb_w - 8, max(mb.x0 + 8, (r.x0 + r.x1) / 2 - cb_w / 2))
            anchor_y = max(mb.y0 + 8, r.y0 - cb_h - 8)
            cbox = fitz.Rect(anchor_x, anchor_y, anchor_x + cb_w, anchor_y + cb_h)
            page.draw_rect(cbox, color=(0, 0, 0), fill=(1, 1, 1), width=1.0, overlay=True)
            page.insert_textbox(
                cbox, label, fontsize=10, color=(0, 0, 0),
                fontname="helv", align=fitz.TEXT_ALIGN_CENTER,
            )
            # 3. Leader line from rect to callout
            page.draw_line(
                fitz.Point((r.x0 + r.x1) / 2, r.y0),
                fitz.Point((cbox.x0 + cbox.x1) / 2, cbox.y1),
                color=(0, 0, 0), width=1.0, overlay=True,
            )

        # 4. Legend strip — placed along the LEFT EDGE of the portrait
        # mediabox, which becomes the BOTTOM of the visible landscape after
        # the page's 270° rotation. The strip is wide so the swatches and
        # labels render legibly.
        legend_w = 240
        legend = fitz.Rect(mb.x0 + 12, mb.y0 + 80, mb.x0 + 12 + legend_w, mb.y1 - 80)
        page.draw_rect(legend, color=(0.2, 0.2, 0.2), fill=(1, 1, 1), width=1.5, overlay=True)
        page.insert_textbox(
            fitz.Rect(legend.x1 - 30, legend.y0 + 8, legend.x1 - 8, legend.y1 - 8),
            "FLOORING TAKEOFF SUMMARY",
            fontsize=12, color=(0, 0, 0), fontname="hebo",
            rotate=90, align=fitz.TEXT_ALIGN_CENTER,
        )
        sf_items = [it for it in prediction.line_items if it.unit == "SF"]
        lf_items = [it for it in prediction.line_items if it.unit == "LF"]
        all_items = sf_items + lf_items
        if all_items:
            avail_h = legend.height - 60
            slot_h = avail_h / (len(all_items) + 1)  # +1 for totals row
            for i, it in enumerate(all_items):
                slot_top = legend.y0 + 30 + i * slot_h
                slot_bot = slot_top + slot_h
                colour = _colour_for(it.item_id)
                # Swatch (mediabox: appears as a square on the visible bottom strip)
                sw = fitz.Rect(legend.x0 + 8, slot_top + slot_h * 0.15,
                               legend.x0 + 36, slot_top + slot_h * 0.85)
                page.draw_rect(sw, color=colour, fill=colour, fill_opacity=0.7, width=0.5)
                # Label rotated 90 to read correctly in the landscape view
                page.insert_textbox(
                    fitz.Rect(legend.x0 + 44, slot_top + 4, legend.x1 - 50, slot_bot - 4),
                    f"{it.item_id} - {it.quantity:,.1f} {it.unit}",
                    fontsize=10, color=(0, 0, 0), fontname="helv", rotate=90,
                    align=fitz.TEXT_ALIGN_CENTER,
                )
            # Totals line
            total_sf = sum(it.quantity for it in sf_items)
            total_lf = sum(it.quantity for it in lf_items)
            slot_top = legend.y0 + 30 + len(all_items) * slot_h
            page.insert_textbox(
                fitz.Rect(legend.x0 + 8, slot_top + 4, legend.x1 - 50, slot_top + slot_h - 4),
                f"TOTAL: {total_sf:,.0f} SF / {total_lf:,.0f} LF\n"
                f"Conf {prediction.overall_confidence:.0%}",
                fontsize=9, color=(0, 0, 0), fontname="hebo", rotate=90,
                align=fitz.TEXT_ALIGN_CENTER,
            )

        out.save(str(output_pdf), garbage=4, deflate=True)
        out.close()
    finally:
        src.close()
