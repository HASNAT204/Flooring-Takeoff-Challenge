"""FastAPI entry point. The web UI uses HTMX for progress polling so the
whole stack stays in a single Python process.

Routes:
  GET  /                 -> upload page
  POST /upload           -> accepts multipart PDFs, kicks off background job
  GET  /job/{id}/status  -> JSON for HTMX polling
  GET  /job/{id}/progress -> HTMX partial (HTML) showing current stage
  GET  /job/{id}         -> results page
  GET  /job/{id}/marked.pdf
  GET  /job/{id}/takeoff.xlsx
  GET  /job/{id}/prediction.json
  POST /job/{id}/evaluate -> runs evaluator if gold/ contains gold_takeoff.xlsx
  GET  /job/{id}/evaluation.html
"""
from __future__ import annotations
import json
import logging
import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app.pipeline.run import run_pipeline


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs" / "jobs"
GOLD_DIR = ROOT / "gold"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Flooring Takeoff", version="0.1.0")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _job_dir(job_id: str) -> Path:
    if not job_id or "/" in job_id or ".." in job_id:
        raise HTTPException(400, "invalid job id")
    return OUTPUTS_DIR / job_id


def _read_status(job_id: str) -> dict:
    p = _job_dir(job_id) / "status.json"
    if not p.exists():
        return {"stage": "queued", "progress": 0.0, "message": "Queued..."}
    # The writer uses atomic rename, but be defensive against transient
    # read failures (e.g. file present but not yet visible mid-rename).
    for _ in range(3):
        try:
            text = p.read_text()
            if text.strip():
                return json.loads(text)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return {"stage": "queued", "progress": 0.0, "message": "Queued..."}


def _gold_present() -> bool:
    return (GOLD_DIR / "gold_takeoff.xlsx").exists()


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {"request": request,
         "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
         "gold_present": _gold_present()},
    )


@app.post("/upload")
async def upload(files: list[UploadFile]) -> JSONResponse:
    if not files:
        raise HTTPException(400, "no files uploaded")
    job_id = uuid.uuid4().hex[:12]
    job_uploads = UPLOADS_DIR / job_id
    job_uploads.mkdir(parents=True)
    saved: list[Path] = []
    for f in files:
        if not f.filename:
            continue
        # Don't trust client filenames — sanitise but keep extension
        name = Path(f.filename).name
        target = job_uploads / name
        with target.open("wb") as fh:
            while chunk := await f.read(1024 * 1024):
                fh.write(chunk)
        saved.append(target)
    if not saved:
        raise HTTPException(400, "no readable files")

    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    # write initial status
    (job_dir / "status.json").write_text(json.dumps({
        "stage": "queued", "progress": 0.02,
        "message": f"Uploaded {len(saved)} file(s). Starting takeoff...",
    }))

    def runner():
        try:
            run_pipeline(saved, job_dir)
        except Exception as e:  # noqa: BLE001
            logging.exception("pipeline failed: %s", e)

    threading.Thread(target=runner, daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/job/{job_id}/status")
def job_status(job_id: str) -> JSONResponse:
    return JSONResponse(_read_status(job_id))


@app.get("/job/{job_id}/progress", response_class=HTMLResponse)
def job_progress(request: Request, job_id: str) -> HTMLResponse:
    status = _read_status(job_id)
    return templates.TemplateResponse(
        "progress.html",
        {"request": request, "job_id": job_id, "status": status,
         "is_done": status.get("stage") == "done",
         "is_error": status.get("stage") == "error"},
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_view(request: Request, job_id: str) -> HTMLResponse:
    status = _read_status(job_id)
    if status.get("stage") != "done":
        # Render processing template — HTMX will poll progress and reload on done.
        return templates.TemplateResponse(
            "processing.html",
            {"request": request, "job_id": job_id, "status": status},
        )
    pred_path = _job_dir(job_id) / "prediction.json"
    if not pred_path.exists():
        raise HTTPException(404, "prediction not found")
    prediction = json.loads(pred_path.read_text())
    return templates.TemplateResponse(
        "results.html",
        {"request": request, "job_id": job_id, "prediction": prediction,
         "gold_present": _gold_present(),
         "has_evaluation": (_job_dir(job_id) / "evaluation.html").exists()},
    )


@app.get("/job/{job_id}/marked.pdf")
def job_marked(job_id: str):
    p = _job_dir(job_id) / "marked.pdf"
    if not p.exists():
        raise HTTPException(404, "not ready")
    return FileResponse(str(p), media_type="application/pdf", filename=f"{job_id}-marked.pdf")


@app.get("/job/{job_id}/takeoff.xlsx")
def job_xlsx(job_id: str):
    p = _job_dir(job_id) / "takeoff.xlsx"
    if not p.exists():
        raise HTTPException(404, "not ready")
    return FileResponse(
        str(p),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{job_id}-takeoff.xlsx",
    )


@app.get("/job/{job_id}/prediction.json")
def job_json(job_id: str):
    p = _job_dir(job_id) / "prediction.json"
    if not p.exists():
        raise HTTPException(404, "not ready")
    return FileResponse(str(p), media_type="application/json")


@app.post("/job/{job_id}/evaluate", response_class=HTMLResponse)
def job_evaluate(request: Request, job_id: str) -> HTMLResponse:
    if not _gold_present():
        raise HTTPException(400, "gold/gold_takeoff.xlsx is missing — populate it first")
    pred = _job_dir(job_id) / "prediction.json"
    if not pred.exists():
        raise HTTPException(404, "prediction not found")
    out = _job_dir(job_id) / "evaluation.html"
    # Run evaluator in-process (CLI also works)
    from evaluator.evaluate import main as eval_main
    rc = eval_main(["--prediction", str(pred), "--gold", str(GOLD_DIR / "gold_takeoff.xlsx"), "--out", str(out)])
    return RedirectResponse(f"/job/{job_id}/evaluation.html", status_code=303)


@app.get("/job/{job_id}/evaluation.html", response_class=HTMLResponse)
def job_eval_view(job_id: str):
    p = _job_dir(job_id) / "evaluation.html"
    if not p.exists():
        raise HTTPException(404, "evaluation not run yet")
    return HTMLResponse(p.read_text())
