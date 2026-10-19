#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Ambiente Python mancante: crea .venv e installa requirements.txt" >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import os
import subprocess
from pathlib import Path

root = Path.cwd()
output = root / "output"
output.mkdir(exist_ok=True)

services = [
    ("dashboard", [".venv/bin/python", "-u", "-m", "arbitraggio.dashboard", "--port", "8765"]),
    ("tracker", [".venv/bin/python", "-u", "-m", "arbitraggio.tracking", "--interval", "8"]),
    ("scanner", [".venv/bin/python", "-u", "-m", "arbitraggio", "--budget", "100", "--pages", "50", "--reuse-catalog"]),
]

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False

for name, args in services:
    pid_file = output / f"{name}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid = None
        if pid and alive(pid):
            print(f"{name}: gia attivo (PID {pid})")
            continue
    log = open(output / f"{name}.log", "ab", buffering=0)
    process = subprocess.Popen(
        args,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file.write_text(str(process.pid))
    print(f"{name}: avviato (PID {process.pid})")
PY

echo "Dashboard: http://127.0.0.1:8765"
echo "Log: output/dashboard.log, output/tracker.log, output/scanner.log"
