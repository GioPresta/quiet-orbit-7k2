#!/usr/bin/env python3
"""Publish a minimal aggregate snapshot, or a full local report with --full."""
import argparse
import csv
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


PUBLIC_FILES = (
    "opportunities-latest.csv",
    "opportunities-legs.csv",
    "opportunities-sizes.csv",
    "opportunity-ledger.json",
    "opportunity-tracking.jsonl",
    "tracking.json",
    "tracking-history.jsonl",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    parser.add_argument("--full", action="store_true", help="Copy detailed local data too")
    args = parser.parse_args()
    output, docs = args.output, args.docs
    docs.mkdir(parents=True, exist_ok=True)

    try:
        report = json.loads((output / "latest.json").read_text())
    except (OSError, ValueError):
        report = {"opportunities": [], "timestamp": 0}
    try:
        ledger = json.loads((output / "opportunity-ledger.json").read_text())
    except (OSError, ValueError):
        ledger = {"episodes": [], "stats": {}}

    stats = ledger.get("stats") or {}
    summary = {
        "timestamp": report.get("timestamp") or 0,
        "opportunities": len(report.get("opportunities", [])),
        "tracked": stats.get("tracked"),
        "open": stats.get("open"),
        "target": stats.get("target"),
        "mean_entry_net_roi_pct": stats.get("mean_entry_net_roi_pct"),
        "mean_exit_net_roi_pct": stats.get("mean_exit_net_roi_pct"),
        "median_target_seconds": stats.get("median_target_seconds"),
    }
    with (docs / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)

    if args.full:
        for name in PUBLIC_FILES:
            source = output / name
            if source.exists():
                shutil.copy2(source, docs / name)
    else:
        for name in PUBLIC_FILES:
            target = docs / name
            if target.exists():
                target.unlink()

    stamp = datetime.fromtimestamp(float(summary["timestamp"]), timezone.utc)
    mean_entry = summary["mean_entry_net_roi_pct"]
    mean_exit = summary["mean_exit_net_roi_pct"]
    median = summary["median_target_seconds"]
    page = f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Market observer</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f5f7fa;color:#182830}}
main{{max-width:760px;margin:auto;padding:32px}}
h1{{font-size:25px;margin:0 0 4px}}p{{color:#68777f;font-size:13px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:20px 0}}
.card{{background:#fff;border:1px solid #e7ecf0;border-radius:8px;padding:14px}}
.card small{{display:block;color:#87939a;font-size:10px;margin-bottom:5px}}
.card strong{{font-size:23px}}
</style></head><body><main>
<h1>Market observer</h1>
<p>Aggiornato: {html.escape(stamp.isoformat())}. Dati aggregati; dettagli non pubblici.</p>
<div class="grid">
<div class="card"><small>Opportunità correnti</small><strong>{summary['opportunities']}</strong></div>
<div class="card"><small>Episodi seguiti</small><strong>{summary['tracked'] if summary['tracked'] is not None else '—'}</strong></div>
<div class="card"><small>Target raggiunti</small><strong>{summary['target'] if summary['target'] is not None else '—'}</strong></div>
<div class="card"><small>Tempo mediano al target</small><strong>{html.escape(str(median)) if median is not None else '—'}</strong></div>
<div class="card"><small>ROI ingresso medio</small><strong>{html.escape(str(mean_entry)) if mean_entry is not None else '—'}%</strong></div>
<div class="card"><small>ROI uscita medio</small><strong>{html.escape(str(mean_exit)) if mean_exit is not None else '—'}%</strong></div>
</div>
<p>Il report dettagliato non viene pubblicato.</p>
</main></body></html>"""
    (docs / "index.html").write_text(page)
    (docs / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"Pubblicato summary in {docs} (full={args.full})")


if __name__ == "__main__":
    main()
