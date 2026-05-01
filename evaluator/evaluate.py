"""CLI evaluator.

Usage:
    python -m evaluator.evaluate \\
        --prediction outputs/jobs/<id>/prediction.json \\
        --gold gold/gold_takeoff.xlsx \\
        --out outputs/jobs/<id>/evaluation.html

The evaluator loads the gold XLSX (gold_loader is the only module that
reads from the gold/ directory), compares per-code quantities, and writes
a self-contained HTML report.
"""
from __future__ import annotations
import argparse
import html
import json
import sys
from pathlib import Path

from .gold_loader import load_gold
from .metrics import evaluate


def render_html(report: dict, prediction_meta: dict) -> str:
    s = report["summary"]
    rows_html = []
    for r in report["rows"]:
        match = "" if r["in_pred"] and r["in_gold"] else (
            " missing-pred" if r["in_gold"] and not r["in_pred"] else " missing-gold")
        rows_html.append(
            f"<tr class='{match.strip()}'>"
            f"<td><b>{html.escape(r['code'])}</b></td>"
            f"<td>{r['unit']}</td>"
            f"<td class='num'>{r['predicted']:.2f}</td>"
            f"<td class='num'>{r['gold']:.2f}</td>"
            f"<td class='num'>{r['delta']:+.2f}</td>"
            f"<td class='num'>{r['accuracy_pct']:.1f}%</td>"
            f"<td class='small'>{html.escape((r['gold_description'] or r['pred_description'])[:120])}</td>"
            f"</tr>"
        )
    badge = "PASS" if s["passes_75pct_threshold"] else "BELOW 75%"
    badge_color = "#1f7a3a" if s["passes_75pct_threshold"] else "#a62024"
    return f"""\
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Flooring Takeoff — Evaluation Report</title>
<style>
:root {{ --fg:#222; --muted:#666; --line:#dde; --accent:#3a5fbf; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; color:var(--fg); margin:24px; max-width:1080px; }}
h1 {{ margin: 0 0 8px; font-size: 22px; }}
.subtle {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; }}
.banner {{ display:flex; gap:16px; align-items:center; padding:14px 18px; border-radius:10px; background:#f4f6fc; border:1px solid var(--line); margin-bottom: 18px; }}
.banner b {{ font-size: 18px; }}
.badge {{ display:inline-block; padding:4px 10px; border-radius:6px; color:#fff; background:{badge_color}; font-weight:600; font-size:13px; }}
.metrics {{ display:flex; gap:14px; flex-wrap:wrap; margin: 12px 0 20px; }}
.metric {{ flex:1; min-width:180px; border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:#fff; }}
.metric .label {{ color: var(--muted); font-size:12px; text-transform: uppercase; letter-spacing: 0.5px; }}
.metric .value {{ font-size:24px; font-weight:600; margin-top:2px; }}
table {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }}
th, td {{ border-bottom:1px solid var(--line); padding:8px 10px; text-align:left; }}
th {{ background:#f7f8fb; font-weight:600; }}
.num {{ font-variant-numeric: tabular-nums; text-align:right; }}
.missing-pred td {{ background: #fff8e6; }}
.missing-gold td {{ background: #f0f7ee; color: var(--muted); }}
.small {{ font-size:11.5px; color: var(--muted); }}
.note {{ font-size:12px; color: var(--muted); margin-top:24px; }}
</style>
</head><body>
<h1>Flooring Takeoff — Evaluation Report</h1>
<div class="subtle">{html.escape(prediction_meta.get('project', ''))} · prediction overall confidence {float(prediction_meta.get('overall_confidence', 0)):.0%}</div>
<div class="banner">
  <span class="badge">{badge}</span>
  <span><b>Aggregate accuracy:</b> {s['aggregate_accuracy']*100:.1f}%</span>
  <span style="margin-left:auto;color:var(--muted);font-size:13px;">75% target — average of SF-weighted and LF-weighted accuracy</span>
</div>
<div class="metrics">
  <div class="metric"><div class="label">Item-match F1</div><div class="value">{s['item_match_f1']:.2f}</div><div class="small">precision {s['item_match_precision']:.2f} · recall {s['item_match_recall']:.2f}</div></div>
  <div class="metric"><div class="label">SF-weighted accuracy</div><div class="value">{s['sf_weighted_accuracy']*100:.1f}%</div></div>
  <div class="metric"><div class="label">LF-weighted accuracy</div><div class="value">{s['lf_weighted_accuracy']*100:.1f}%</div></div>
</div>
<h2 style="font-size:16px; margin:18px 0 6px;">Per-line-item comparison</h2>
<table>
  <tr><th>Code</th><th>Unit</th><th class="num">Predicted</th><th class="num">Gold</th><th class="num">Δ</th><th class="num">Accuracy</th><th>Description</th></tr>
  {''.join(rows_html)}
</table>
<p class="note">Yellow rows = item appears in gold but not in prediction (missed). Green rows = appears only in prediction (spurious). Cost columns are not evaluated — this product produces quantities only.</p>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Evaluate a flooring-takeoff prediction against the gold takeoff.")
    p.add_argument("--prediction", required=True, type=Path)
    p.add_argument("--gold", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)

    prediction = json.loads(args.prediction.read_text())
    gold = load_gold(args.gold)
    report = evaluate(prediction, gold)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_html(report, prediction))
    # Also dump raw JSON next to the HTML.
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2))

    s = report["summary"]
    print(f"Aggregate accuracy: {s['aggregate_accuracy']*100:.1f}% "
          f"(SF {s['sf_weighted_accuracy']*100:.1f}% · LF {s['lf_weighted_accuracy']*100:.1f}%)")
    print(f"Item-match F1: {s['item_match_f1']:.2f}")
    print(f"Report: {args.out}")
    return 0 if s["passes_75pct_threshold"] else 1


if __name__ == "__main__":
    sys.exit(main())
