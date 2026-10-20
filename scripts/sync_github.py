#!/usr/bin/env python3
"""Publish fresher local opportunities to GitHub when the local scanner is ahead."""
import argparse
import json
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
DOCS = ROOT / "docs"


def run(*args, check=True):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


def remote_timestamp():
    run("git", "fetch", "origin", "main")
    result = run("git", "show", "origin/main:docs/data/state.json", check=False)
    if result.returncode:
        return 0
    try:
        return float(json.loads(result.stdout).get("timestamp") or 0)
    except (ValueError, TypeError):
        return 0


def sync_once():
    try:
        local = json.loads((OUTPUT / "latest.json").read_text())
    except (OSError, ValueError):
        return "nessun latest locale"
    if not local.get("opportunities"):
        return "nessuna opportunità locale"
    local_ts = float(local.get("timestamp") or 0)
    remote_ts = remote_timestamp()
    if local_ts <= remote_ts + 30:
        return f"GitHub già aggiornato (locale {local_ts:.0f} <= remoto {remote_ts:.0f})"
    run("git", "checkout", "origin/main", "--", "docs")
    run(str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/publish_snapshot.py"),
        "--output", str(OUTPUT), "--docs", str(DOCS))
    run("git", "add", "docs")
    changed = run("git", "diff", "--cached", "--quiet", check=False).returncode != 0
    if not changed:
        return "nessuna differenza da pubblicare"
    run("git", "commit", "-m", f"local snapshot {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    for attempt in range(1, 4):
        try:
            run("git", "push", "origin", "main")
            return f"pubblicato su GitHub (tentativo {attempt})"
        except RuntimeError as error:
            if attempt == 3:
                return f"push fallito: {error}"
            run("git", "fetch", "origin", "main")
            time.sleep(3)
    return "push non riuscito"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=180)
    args = parser.parse_args()
    while True:
        try:
            print(time.strftime("%H:%M:%S"), sync_once(), flush=True)
        except Exception as error:
            print(time.strftime("%H:%M:%S"), type(error).__name__, error, flush=True)
        if not args.loop:
            break
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
