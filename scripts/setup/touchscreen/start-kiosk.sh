#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="/opt/TouchKio/config.json"
PROVISIONED="/opt/TouchKio/.provisioned"
DEFAULT_SERVER="https://192.168.1.218"
DEFAULT_PANEL_ID="zoe-touch-pi"

# ── Read config ───────────────────────────────────────────────────────────────
TOKEN=""
SERVER_URL=""
PANEL_ID=""
if [[ -f "${CONFIG_PATH}" ]]; then
  TOKEN=$(python3 -c "
import json, pathlib
try:
    d = json.loads(pathlib.Path('${CONFIG_PATH}').read_text())
    print(d.get('token', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
  SERVER_URL=$(python3 -c "
import json, pathlib
try:
    d = json.loads(pathlib.Path('${CONFIG_PATH}').read_text())
    print(d.get('server_url', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
  PANEL_ID=$(python3 -c "
import json, pathlib
try:
    d = json.loads(pathlib.Path('${CONFIG_PATH}').read_text())
    print(d.get('panel_id', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

SERVER_URL="${SERVER_URL:-${DEFAULT_SERVER}}"
PANEL_ID="${PANEL_ID:-${DEFAULT_PANEL_ID}}"

# ── Provisioning check ────────────────────────────────────────────────────────
# If .provisioned exists but token is missing/empty, re-enter provision mode.
if [[ ! -f "${PROVISIONED}" ]] || [[ -z "${TOKEN}" ]]; then
  echo "[start-kiosk] No token or .provisioned missing — entering provision mode..."
  python3 /opt/TouchKio/provision-server.py --mode provision &
  KIOSK_URL="http://localhost:8888/"
  sleep 2
  exec /usr/bin/chromium \
    --kiosk \
    --no-first-run \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --noerrdialogs \
    --touch-events=enabled \
    --start-maximized \
    --autoplay-policy=no-user-gesture-required \
    --user-data-dir=/home/pi/.config/chromium-provision \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --ignore-certificate-errors \
    --disable-extensions \
    --no-sandbox \
    "${KIOSK_URL}"
fi

# ── Normal kiosk mode ─────────────────────────────────────────────────────────
KIOSK_URL="${SERVER_URL}/touch/dashboard.html?panel_id=${PANEL_ID}&kiosk=1"

# Wait briefly for desktop and network readiness.
sleep 5

exec /usr/bin/chromium \
  --kiosk \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --noerrdialogs \
  --touch-events=enabled \
  --start-maximized \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir=/home/pi/.config/chromium-kiosk \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --ignore-certificate-errors \
  --disable-extensions \
  --no-sandbox \
  "${KIOSK_URL}"
