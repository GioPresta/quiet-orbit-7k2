#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SOURCE="$APP_DIR/deploy/arbitraggio.service"
UNIT_TARGET="/etc/systemd/system/arbitraggio.service"

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/output"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "Creato $APP_DIR/.env: inserisci le API key se disponibili."
fi

sed -e "s|/opt/arbitraggio|$APP_DIR|g" -e "s|__USER__|$(id -un)|g" "$UNIT_SOURCE" \
  | sudo tee "$UNIT_TARGET" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now arbitraggio.service
sudo systemctl --no-pager --full status arbitraggio.service || true

echo "Dashboard locale alla VM: http://127.0.0.1:8765"
echo "Log: sudo journalctl -u arbitraggio -f"
