#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="/opt/TouchKio/config.json"
DEFAULT_URL="https://192.168.1.218/touch/dashboard.html?panel_id=zoe-touch-pi&kiosk=1"
KIOSK_URL="${DEFAULT_URL}"

if [[ -f "${CONFIG_PATH}" ]]; then
  URL_FROM_CONFIG="$(python3 - <<'PY'
import json
import pathlib

p = pathlib.Path("/opt/TouchKio/config.json")
try:
    data = json.loads(p.read_text())
    print(data.get("url", ""))
except Exception:
    print("")
PY
)"
  if [[ -n "${URL_FROM_CONFIG}" ]]; then
    KIOSK_URL="${URL_FROM_CONFIG}"
  fi
fi

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
