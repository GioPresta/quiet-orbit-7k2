#!/usr/bin/env python3
"""Publish the complete snapshot as static files for GitHub Pages."""
import argparse
import csv
import html
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from arbitraggio.opportunity_tracking import ledger_csv
from arbitraggio.tracking import csv_rows


PUBLIC_FILES = (
    "opportunities-latest.csv",
    "opportunities-legs.csv",
    "opportunities-sizes.csv",
    "matches.csv",
    "opportunity-ledger.json",
    "opportunity-tracking.jsonl",
    "tracking.json",
    "tracking-history.jsonl",
)


def link(url, label="Apri"):
    if not url:
        return ""
    safe = html.escape(str(url), quote=True)
    return f'<a href="{safe}">{html.escape(label)}</a>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args()
    output, docs = args.output, args.docs
    docs.mkdir(parents=True, exist_ok=True)

    for name in PUBLIC_FILES:
        source = output / name
        if source.exists():
            shutil.copy2(source, docs / name)
    stale = docs / "details.enc"
    if stale.exists():
        stale.unlink()

    try:
        report = json.loads((output / "latest.json").read_text())
    except (OSError, ValueError):
        report = {"opportunities": [], "timestamp": 0}
    scan_timestamp = report.get("timestamp") or 0
    data_dir = docs / "data"
    data_dir.mkdir(exist_ok=True)
    recent_path = data_dir / "recent.json"
    if report.get("opportunities"):
        recent_path.write_text(json.dumps(report, ensure_ascii=False, default=str))
    elif recent_path.exists():
        try:
            recent = json.loads(recent_path.read_text())
            if recent.get("opportunities"):
                report = recent
                report["recent_fallback"] = True
                report["last_scan_at"] = scan_timestamp
        except (OSError, ValueError):
            pass
    try:
        ledger = json.loads((output / "opportunity-ledger.json").read_text())
    except (OSError, ValueError):
        ledger = {"episodes": [], "stats": {}}
    for name in PUBLIC_FILES:
        source = output / name
        if source.exists():
            shutil.copy2(source, data_dir / name)
        old_root = docs / name
        if old_root.exists():
            old_root.unlink()
    if (output / "latest.json").exists():
        shutil.copy2(output / "latest.json", data_dir / "latest.json")

    state = dict(report)
    state.setdefault("last_scan_at", scan_timestamp)
    defaults = {
        "budget": report.get("budget", "100"),
        "sort_policy": "liquidity", "min_volume24h": "0", "max_matches": 64,
        "tracking_min_yield_pct": "1.5", "tracking_target_yield_pct": "1.5",
        "tax_rate_pct": "0",
        "enabled_venues": ["polymarket", "predict", "limitless", "opinion"],
        "budgets": ["50", "100"],
    }
    defaults.update({k: v for k, v in (report.get("settings") or {}).items() if v is not None})
    state.update(server_time=time.time(), scanner_alive=True, csrf_token="static",
                 ui_settings=defaults, settings=defaults)
    (data_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, default=str))

    try:
        tracking = json.loads((output / "tracking.json").read_text())
    except (OSError, ValueError):
        tracking = {"wallets": [], "positions": [], "totals": {}}
    tracking.update(tracker_alive=True, wallet_settings=[], csrf_token="static")
    (data_dir / "tracking.json").write_text(json.dumps(tracking, ensure_ascii=False, default=str))
    fields, rows = csv_rows(tracking)
    with (data_dir / "tracking.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

    entries = []
    try:
        for line in (output / "tracking-history.jsonl").read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
    except (OSError, ValueError):
        pass
    (data_dir / "tracking-history.json").write_text(json.dumps({"entries": entries[-2000:]}, ensure_ascii=False))
    with (data_dir / "tracking-history.csv").open("w", newline="") as handle:
        fields = ["timestamp", "net_now", "pnl_now", "realized", "positions"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for entry in entries:
            totals = entry.get("totals") or {}
            writer.writerow({k: totals.get(k) if k != "timestamp" else entry.get("timestamp") for k in fields})

    ledger_state = dict(ledger)
    ledger_state["scanner_alive"] = True
    (data_dir / "opportunity-tracking.json").write_text(json.dumps(ledger_state, ensure_ascii=False, default=str))
    fields, rows = ledger_csv(ledger)
    with (data_dir / "opportunity-tracking.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    stats = ledger.get("stats") or {}
    summary = {
        "timestamp": report.get("timestamp") or 0,
        "opportunities": len(report.get("opportunities", [])),
        "tracked": stats.get("tracked"), "open": stats.get("open"),
        "target": stats.get("target"),
        "mean_entry_net_roi_pct": stats.get("mean_entry_net_roi_pct"),
        "mean_exit_net_roi_pct": stats.get("mean_exit_net_roi_pct"),
        "median_target_seconds": stats.get("median_target_seconds"),
    }
    with (docs / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader(); writer.writerow(summary)

    opportunities = []
    for item in report.get("opportunities", []):
        venues = " · ".join(sorted({a.get("venue", "") for a in item.get("allocations", [])}))
        links = " ".join(link(a.get("url")) for a in item.get("allocations", []) if a.get("url"))
        legs = []
        for leg in item.get("allocations", []):
            legs.append(
                f"<div><b>{html.escape(str(leg.get('venue', '')))} {html.escape(str(leg.get('outcome', '')))}</b> "
                f"· volume {html.escape(str(leg.get('volume24h', '')))} · ask {html.escape(str(leg.get('best_ask', '')))} "
                f"· profondità ${html.escape(str(leg.get('ask_depth_notional', '')))} · {link(leg.get('url'))}</div>"
            )
        opportunities.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('name', '')))}</td>"
            f"<td>{html.escape(venues)}</td>"
            f"<td>{html.escape(str(item.get('roi_pct', '')))}%</td>"
            f"<td>{html.escape(str(item.get('profit', '')))}</td>"
            f"<td>{html.escape(str(item.get('max_feasible_budget', '')))}</td>"
            f"<td>{links}</td>"
            f"<td><details><summary>Dettagli</summary>{''.join(legs)}</details></td>"
            "</tr>"
        )
    episodes = []
    for item in ledger.get("episodes", []):
        urls = " ".join(link(url) for url in item.get("urls", []) if url)
        episodes.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('name', '')))}</td>"
            f"<td>{html.escape(str(item.get('status', '')))}</td>"
            f"<td>{html.escape(str(item.get('entry_net_roi_pct', '')))}%</td>"
            f"<td>{html.escape(str(item.get('current_exit_net_roi_pct', '')))}%</td>"
            f"<td>{html.escape(str(item.get('duration_seconds', '')))}</td>"
            f"<td>{urls}</td>"
            "</tr>"
        )

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
main{{max-width:1250px;margin:auto;padding:28px}}
h1{{font-size:25px;margin:0 0 4px}}p{{color:#68777f;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}}
.card{{background:#fff;border:1px solid #e7ecf0;border-radius:8px;padding:14px}}
.card small{{display:block;color:#87939a;font-size:10px;margin-bottom:5px}}
.card strong{{font-size:22px}}
table{{width:100%;border-collapse:collapse;background:#fff;margin:14px 0;font-size:12px}}
th,td{{padding:9px 10px;border-bottom:1px solid #e8edf1;text-align:left;vertical-align:top}}
th{{font-size:10px;color:#87939a;text-transform:uppercase}}a{{color:#167b61}}
.links{{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0}}
details div{{margin:4px 0;color:#68777f;font-size:11px}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<h1>Market observer</h1>
<p>Aggiornato: {html.escape(stamp.isoformat())} · snapshot completo</p>
<div class="grid">
<div class="card"><small>Opportunità correnti</small><strong>{summary['opportunities']}</strong></div>
<div class="card"><small>Episodi seguiti</small><strong>{summary['tracked'] if summary['tracked'] is not None else '—'}</strong></div>
<div class="card"><small>Target raggiunti</small><strong>{summary['target'] if summary['target'] is not None else '—'}</strong></div>
<div class="card"><small>Tempo mediano al target</small><strong>{html.escape(str(median)) if median is not None else '—'}</strong></div>
<div class="card"><small>ROI ingresso medio</small><strong>{html.escape(str(mean_entry)) if mean_entry is not None else '—'}%</strong></div>
<div class="card"><small>ROI uscita medio</small><strong>{html.escape(str(mean_exit)) if mean_exit is not None else '—'}%</strong></div>
</div>
<div class="links">
<a href="opportunities-latest.csv">CSV opportunità</a>
<a href="opportunities-legs.csv">CSV gambe</a>
<a href="opportunities-sizes.csv">CSV size ladder</a>
<a href="opportunity-tracking.jsonl">Storico tracking</a>
<a href="opportunity-ledger.json">Ledger JSON</a>
</div>
<h2>Opportunità correnti</h2>
<table><thead><tr><th>Mercato</th><th>Piattaforme</th><th>ROI</th><th>Profitto</th><th>Budget max</th><th>Link</th><th>Dettagli</th></tr></thead>
<tbody>{''.join(opportunities) or '<tr><td colspan="7">Nessuna opportunità nel ciclo.</td></tr>'}</tbody></table>
<h2>Episodi seguiti</h2>
<table><thead><tr><th>Mercato</th><th>Stato</th><th>Ingresso netto fee</th><th>Uscita netto fee</th><th>Durata sec</th><th>Link</th></tr></thead>
<tbody>{''.join(episodes) or '<tr><td colspan="6">Nessun episodio.</td></tr>'}</tbody></table>
</main></body></html>"""
    (docs / "index.html").write_text(page)
    (docs / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    web = Path(__file__).resolve().parent.parent / "web"
    for name in ("index.html", "app.js", "style.css", "portfolio.css"):
        shutil.copy2(web / name, docs / name)
    print(f"Pubblicato snapshot completo in {docs}")


if __name__ == "__main__":
    main()
