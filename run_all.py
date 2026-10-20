#!/usr/bin/env python3
"""Run scanner, dashboard and tracker under one supervisor (systemd friendly)."""
import signal
import subprocess
import sys
import time
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)
SERVICES = [
    ("dashboard", [str(ROOT / ".venv/bin/python"), "-u", "-m", "arbitraggio.dashboard",
                   "--port", os.environ.get("PORT", "8765")]),
    ("tracker", [str(ROOT / ".venv/bin/python"), "-u", "-m", "arbitraggio.tracking", "--interval", "8"]),
    ("scanner", [str(ROOT / ".venv/bin/python"), "-u", "-m", "arbitraggio", "--budget", "100",
                 "--pages", os.environ.get("SCAN_PAGES", "10"), "--reuse-catalog"]),
]
processes = {}


def stop(*_):
    for process in processes.values():
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 10
    while time.time() < deadline and any(p.poll() is None for p in processes.values()):
        time.sleep(.2)
    for process in processes.values():
        if process.poll() is None:
            process.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

for name, command in SERVICES:
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL)
    processes[name] = process
    (OUTPUT / f"{name}.pid").write_text(str(process.pid))
    print(f"{name}: PID {process.pid}", flush=True)

while True:
    for name, process in processes.items():
        code = process.poll()
        if code is not None:
            print(f"{name} terminato con codice {code}; arresto del servizio", flush=True)
            stop()
    time.sleep(2)
