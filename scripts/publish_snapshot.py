#!/usr/bin/env python3
"""Publish aggregate stats plus an optional passphrase-encrypted detail payload."""
import argparse
import base64
import csv
import html
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


PUBLIC_FILES = (
    "opportunities-latest.csv",
    "opportunities-legs.csv",
    "opportunities-sizes.csv",
    "opportunity-ledger.json",
    "opportunity-tracking.jsonl",
    "tracking.json",
    "tracking-history.jsonl",
)
ITERATIONS = 200_000


def encrypt_details(passphrase, payload):
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERATIONS).derive(passphrase.encode())
    encrypted = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode(), None)
    return {
        "version": 1,
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "data": base64.b64encode(encrypted).decode(),
    }


def detail_payload(report, ledger, summary):
    opportunities = []
    for item in report.get("opportunities", []):
        legs = []
        for leg in item.get("allocations", []):
            legs.append({
                "venue": leg.get("venue"), "outcome": leg.get("outcome"),
                "url": leg.get("url"), "price_avg": leg.get("price_avg"),
                "volume_total": leg.get("volume_total"), "volume24h": leg.get("volume24h"),
                "best_ask": leg.get("best_ask"), "ask_depth_notional": leg.get("ask_depth_notional"),
                "best_bid": leg.get("best_bid"), "bid_depth_notional": leg.get("bid_depth_notional"),
            })
        opportunities.append({
            "name": item.get("name"), "roi_pct": item.get("roi_pct"),
            "profit": item.get("profit"), "max_feasible_budget": item.get("max_feasible_budget"),
            "legs": legs,
        })
    episodes = []
    for item in ledger.get("episodes", []):
        episodes.append({
            "name": item.get("name"), "status": item.get("status"),
            "entry_net_roi_pct": item.get("entry_net_roi_pct"),
            "exit_net_roi_pct": item.get("final_exit_net_roi_pct") or item.get("current_exit_net_roi_pct"),
            "duration_seconds": item.get("duration_seconds"),
            "urls": item.get("urls") or [],
        })
    return {"summary": summary, "opportunities": opportunities, "episodes": episodes}


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
        "tracked": stats.get("tracked"), "open": stats.get("open"),
        "target": stats.get("target"),
        "mean_entry_net_roi_pct": stats.get("mean_entry_net_roi_pct"),
        "mean_exit_net_roi_pct": stats.get("mean_exit_net_roi_pct"),
        "median_target_seconds": stats.get("median_target_seconds"),
    }
    with (docs / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader(); writer.writerow(summary)

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

    passphrase = os.environ.get("DETAIL_PASSPHRASE", "")
    if passphrase:
        payload = encrypt_details(passphrase, detail_payload(report, ledger, summary))
        (docs / "details.enc").write_text(json.dumps(payload))
    else:
        detail_path = docs / "details.enc"
        if detail_path.exists():
            detail_path.unlink()

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
main{{max-width:1000px;margin:auto;padding:32px}}
h1{{font-size:25px;margin:0 0 4px}}p{{color:#68777f;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:20px 0}}
.card{{background:#fff;border:1px solid #e7ecf0;border-radius:8px;padding:14px}}
.card small{{display:block;color:#87939a;font-size:10px;margin-bottom:5px}}
.card strong{{font-size:22px}}
table{{width:100%;border-collapse:collapse;background:#fff;margin:14px 0;font-size:12px}}
th,td{{padding:9px 10px;border-bottom:1px solid #e8edf1;text-align:left;vertical-align:top}}
th{{font-size:10px;color:#87939a;text-transform:uppercase}}a{{color:#167b61}}
#unlock{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:18px 0}}
#pass{{height:36px;border:1px solid #dfe7e3;border-radius:7px;padding:0 10px;min-width:220px}}
button{{height:36px;border:0;border-radius:7px;background:#167b61;color:#fff;padding:0 14px;font-weight:600}}
.error{{color:#a45b5b}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<h1>Market observer</h1>
<p>Aggiornato: {html.escape(stamp.isoformat())}. Dati aggregati; dettagli cifrati.</p>
<div class="grid">
<div class="card"><small>Opportunità correnti</small><strong>{summary['opportunities']}</strong></div>
<div class="card"><small>Episodi seguiti</small><strong>{summary['tracked'] if summary['tracked'] is not None else '—'}</strong></div>
<div class="card"><small>Target raggiunti</small><strong>{summary['target'] if summary['target'] is not None else '—'}</strong></div>
<div class="card"><small>Tempo mediano al target</small><strong>{html.escape(str(median)) if median is not None else '—'}</strong></div>
<div class="card"><small>ROI ingresso medio</small><strong>{html.escape(str(mean_entry)) if mean_entry is not None else '—'}%</strong></div>
<div class="card"><small>ROI uscita medio</small><strong>{html.escape(str(mean_exit)) if mean_exit is not None else '—'}%</strong></div>
</div>
<div id="unlock"><input id="pass" type="password" placeholder="Passphrase dettagli" autocomplete="current-password"><button id="unlock-btn">Sblocca</button><span id="unlock-status" class="error"></span></div>
<div id="details" hidden></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function unlock(){{
 const status=document.getElementById('unlock-status');status.textContent='';
 try{{
  const box=await (await fetch('details.enc',{{cache:'no-store'}})).json();
  const material=await crypto.subtle.importKey('raw',new TextEncoder().encode(document.getElementById('pass').value),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({{name:'PBKDF2',salt:b64(box.salt),iterations:box.iterations,hash:'SHA-256'}},material,{{name:'AES-GCM',length:256}},false,['decrypt']);
  const plain=await crypto.subtle.decrypt({{name:'AES-GCM',iv:b64(box.iv)}},key,b64(box.data));
  render(JSON.parse(new TextDecoder().decode(plain)));
 }}catch(e){{status.textContent='Passphrase non valida o dettagli non disponibili.'}}
}}
function render(data){{
 const rows=data.opportunities.map(o=>`<tr><td>${{esc(o.name)}}</td><td>${{esc(o.roi_pct)}}%</td><td>${{esc(o.profit)}}</td><td>${{esc(o.max_feasible_budget)}}</td><td>${{(o.legs||[]).map(l=>l.url?`<a href="${{esc(l.url)}}">Apri</a>`:'').join(' ')}}</td></tr>`).join('');
 const eps=data.episodes.map(e=>`<tr><td>${{esc(e.name)}}</td><td>${{esc(e.status)}}</td><td>${{esc(e.entry_net_roi_pct)}}%</td><td>${{esc(e.exit_net_roi_pct)}}%</td><td>${{esc(e.duration_seconds)}}</td></tr>`).join('');
 document.getElementById('details').innerHTML=`<h2>Opportunità</h2><table><thead><tr><th>Mercato</th><th>ROI</th><th>Profitto</th><th>Budget max</th><th>Link</th></tr></thead><tbody>${{rows||'<tr><td colspan="5">Nessuna</td></tr>'}}</tbody></table><h2>Episodi</h2><table><thead><tr><th>Mercato</th><th>Stato</th><th>Ingresso</th><th>Uscita</th><th>Durata</th></tr></thead><tbody>${{eps||'<tr><td colspan="5">Nessuno</td></tr>'}}</tbody></table>`;
 document.getElementById('details').hidden=false;
}}
document.getElementById('unlock-btn').addEventListener('click',unlock);
</script>
</main></body></html>"""
    (docs / "index.html").write_text(page)
    (docs / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"Pubblicato summary in {docs} (full={args.full}, encrypted={bool(passphrase)})")


if __name__ == "__main__":
    main()
