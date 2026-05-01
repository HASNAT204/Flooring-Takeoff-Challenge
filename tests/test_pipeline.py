"""End-to-end smoke test of the pipeline against the real input PDF.

This test runs only when the input PDFs are available alongside the repo
(under the parent ``Assessment 2.0 ...`` folder). On CI without those
assets, the test skips.
"""
from __future__ import annotations
from pathlib import Path

import pytest

from app.pipeline.run import run_pipeline


REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = (REPO_ROOT.parent /
             "Assessment 2.0 Paid Flooring Challenge - TAKEOFF-52 Lovesac" /
             "01_INPUT_PROJECT_FILES_UPLOAD_THESE")


def _input_pdfs():
    if not INPUT_DIR.exists():
        return None
    return sorted(INPUT_DIR.glob("*.pdf"))


@pytest.mark.skipif(_input_pdfs() is None, reason="real input PDFs not on disk")
def test_pipeline_runs_end_to_end(tmp_path):
    pdfs = _input_pdfs()
    job_dir = tmp_path / "job"
    result = run_pipeline(pdfs, job_dir)
    pred = result["prediction"]

    # Must produce a non-trivial number of line items
    assert len(pred["line_items"]) >= 4, "expected at least 4 line items"

    # Every item must declare its unit + confidence + method
    for it in pred["line_items"]:
        assert it["unit"] in ("SF", "LF", "EA", "LS")
        assert 0.0 <= it["confidence"] <= 1.0
        assert it["method"], f"line item {it['item_id']} missing method"

    # Output files exist
    for k in ("marked_pdf", "takeoff_xlsx", "prediction_json"):
        assert Path(result[k]).exists(), f"{k} not written"

    # Total flooring area is reasonable (Lovesac suite ~2400 SF)
    total_sf = pred["totals"]["flooring_sf"]
    assert 1500 < total_sf < 4000, f"unreasonable total SF: {total_sf}"


@pytest.mark.skipif(_input_pdfs() is None, reason="real input PDFs not on disk")
def test_pipeline_output_contains_v_t_w_codes(tmp_path):
    """The Lovesac suite uses Vinyl, Porcelain Tile, and Wood — the pipeline
    should emit at least one line item per family."""
    pdfs = _input_pdfs()
    result = run_pipeline(pdfs, tmp_path / "job")
    codes = {it["item_id"] for it in result["prediction"]["line_items"]}
    assert any(c.startswith("V-") for c in codes), f"no V-* code in {codes}"
    assert any(c.startswith("T-") for c in codes), f"no T-* code in {codes}"
    assert any(c.startswith("W-") for c in codes), f"no W-* code in {codes}"
