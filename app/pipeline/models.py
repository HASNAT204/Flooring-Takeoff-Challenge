"""Shared pydantic schemas across the pipeline."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# Phase 1 ---------------------------------------------------------------
class SheetCandidate(BaseModel):
    page_index: int
    sheet_id: str
    role: Literal["finish_plan", "dimension_plan", "construction_plan", "other"]
    score: float
    reasons: list[str]


# Phase 2 ---------------------------------------------------------------
class FinishScheduleEntry(BaseModel):
    code: str                       # e.g. "FLT-2"
    category: str                   # vinyl, porcelain, wood, ceramic, ...
    manufacturer: Optional[str] = None
    style: Optional[str] = None
    color: Optional[str] = None
    location_remarks: Optional[str] = None
    raw: Optional[str] = None


class CodeMapping(BaseModel):
    flt: str                        # source architectural code, e.g. "FLT-2"
    estimator_code: str             # canonical V-1 / T-1 / W-1 / W-2 / RB / etc.
    confidence: float
    reason: str


# Phase 3 ---------------------------------------------------------------
class ZoneMeasurement(BaseModel):
    flt_code: Optional[str] = None
    estimator_code: Optional[str] = None
    room_label: Optional[str] = None
    area_sf: float
    method: Literal["label_harvest", "vision_gap_fill", "polygon_vector", "polygon_raster"]
    confidence: float
    source_page: int
    source_bbox: Optional[list[float]] = None
    evidence_text: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# Phase 4 ---------------------------------------------------------------
class LinearMeasurement(BaseModel):
    estimator_code: str             # RB, MDF-BASE, EDGE-TRIM, TRANSITION-VT
    description: str
    length_lf: float
    method: Literal["perimeter_calc", "shared_boundary", "label_harvest", "vision_estimate"]
    confidence: float
    source_page: int
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# Phase 5 ---------------------------------------------------------------
class LineItem(BaseModel):
    item_id: str
    division: str
    category: str
    description: str
    quantity: float
    unit: Literal["SF", "LF", "EA", "LS"]
    wastage_pct: float = 0.0
    quantity_with_wastage: float
    source_pages: list[int]
    source_bbox: Optional[list[float]] = None
    method: str
    confidence: float
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Prediction(BaseModel):
    project: str
    scale: dict
    finish_schedule: list[FinishScheduleEntry]
    code_map: list[CodeMapping]
    line_items: list[LineItem]
    totals: dict
    overall_confidence: float
    notes: list[str] = Field(default_factory=list)


# Pipeline status -------------------------------------------------------
class JobStatus(BaseModel):
    job_id: str
    stage: Literal[
        "queued", "parsing", "identifying_finish_plan", "parsing_schedule",
        "extracting_zones", "measuring_linear", "assembling",
        "generating_outputs", "done", "error"
    ]
    progress: float                 # 0..1
    message: str
    error: Optional[str] = None
