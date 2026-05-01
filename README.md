# Flooring Takeoff — TAKEOFF-52 Lovesac Corner Shoppes at Stadium

A working web product that ingests construction PDFs, runs an automated flooring takeoff, and produces a marked-up drawing, an itemized estimate spreadsheet, and a machine-readable `prediction.json` — plus an evaluation report comparing the result to a known human/gold takeoff.

Built for the 24-hour paid flooring takeoff challenge.

## Quickstart (under 2 minutes)

```bash
git clone <this repo>
cd app-src

# 1. virtualenv + deps
make install
source .venv/bin/activate

# 2. config — paste your Anthropic API key into .env
cp .env.example .env
# edit .env, set ANTHROPIC_API_KEY=sk-ant-...

# 3. run
make run
# open http://localhost:8000
```

Drag-and-drop the two PDFs from `01_INPUT_PROJECT_FILES_UPLOAD_THESE/`:
- `19509 Cover Letter.pdf`
- `25.0722_PRE-PERMIT APPROVAL_Corner Shoppes at Stadium_Kalamazoo,MI.pdf`

The app runs the takeoff in-process (visible progress) and shows three result tabs: marked PDF, spreadsheet, and prediction JSON.

> **Important:** Do not commit `.env`. It is in `.gitignore`.

## Evaluation against the human gold takeoff

The pipeline never reads the gold output. To score a run, drop the gold files into `gold/` (gitignored) and run:

```bash
# Place these manually — they are not in the repo.
#   gold/gold_takeoff.xlsx          (Estimate - LOVESAC CORNER SHOPPES AT STADIUM.xlsx)
#   gold/gold_markup.pdf            (Markups - LOVESAC CORNER SHOPPES AT STADIUM.pdf)

make eval JOB=<job_id>
# opens outputs/jobs/<job_id>/evaluation.html
```

The evaluator reports three accuracy numbers (item-match F1, SF-weighted accuracy, LF-weighted accuracy) for transparency.

## Architecture (one paragraph)

A single Python process runs FastAPI + a 7-phase pipeline. Phase 1 finds the finish-plan sheet (A101) by titleblock heuristics. Phase 2 parses the FLT-1…FLT-7 finish schedule from vector text and maps codes to estimator-style codes (V/T/W) by keyword. Phase 3 measures floor areas by harvesting pre-printed `### SF` labels first, then filling gaps with Claude Sonnet 4.6 vision (constrained to label-citation only — no invented numbers), with vector polygon measurement as a sanity check. Phase 4 derives base/trim/transition LF from polygon perimeters and shared boundaries. Phase 5 assembles `prediction.json` with full per-item provenance (source page, bbox, method, confidence). Phase 6 overlays the result on the original A101 page using PyMuPDF. Phase 7 emits an XLSX shaped like the gold estimate.

## Project layout

```
app/
  main.py                 # FastAPI routes
  pipeline/
    run.py                # orchestrator
    sheet_finder.py       # phase 1
    schedule_parser.py    # phase 2
    zone_extractor.py     # phase 3
    linear_extractor.py   # phase 4
    assembler.py          # phase 5
    markup.py             # phase 6
    spreadsheet.py        # phase 7
    vision.py             # Claude wrapper
    models.py             # pydantic schemas
  templates/              # Jinja2 + HTMX
evaluator/
  evaluate.py             # CLI scorer
  gold_loader.py          # only module that reads /gold/
  metrics.py              # accuracy formulas
outputs/jobs/<id>/        # marked.pdf, takeoff.xlsx, prediction.json, evaluation.html
gold/                     # gitignored, populated manually
uploads/                  # gitignored
```

## Hard rules respected

- Gold output is never consumed by the prediction pipeline. A unit test greps `app/` for the string `gold` and fails if found.
- No API keys committed.
- No hardcoded answers — the pipeline derives every quantity from the input PDFs.
- Every line item carries `confidence`, `method`, and `assumptions` fields so reviewers can audit.

## Limitations

See [SUBMISSION.md](SUBMISSION.md) for the honest accuracy report, what works, and what would need v2 work.
