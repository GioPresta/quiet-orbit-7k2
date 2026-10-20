#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -u scripts/sync_github.py --loop --interval 180
