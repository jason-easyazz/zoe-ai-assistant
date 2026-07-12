#!/bin/bash
set -e

export DISPLAY=:0
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

CONFIG="/opt/TouchKio/config.json"
PROVISIONED="/opt/TouchKio/.provisioned"
PROVISION_SERVER="/opt/TouchKio/provision-server.py"
KIOSK_HOME="${HOME:-/home/pi}"
# Fallback matches the tracked config.json template: the local LAN Skybridge
# surface. Never fall back to zoe.the411.life (Cloudflare-blocked from the
# panel) or the retired dashboard.html.
DEFAULT_URL="https://192.168.1.218/touch/home.html?panel_id=zoe-touch-pi&kiosk=1"

# Resolve the Chromium binary — image-dependent name. Fail loudly so systemd
# journal shows why the panel is black instead of silently looping.
CHROMIUM_BIN="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "${CHROMIUM_BIN}" ]; then
  echo "ERROR: no Chromium binary found (tried chromium-browser, chromium)" >&2
  exit 1
fi

read_config_key() {
  KEY="$1" CONFIG_PATH="${CONFIG}" python3 - <<'PYC'
import json
import os

try:
    with open(os.environ["CONFIG_PATH"]) as fh:
        data = json.load(fh)
    print(data.get(os.environ["KEY"]) or "")
except Exception:
    print("")
PYC
}

ZOE_URL="$(read_config_key url)"
ZOE_URL="${ZOE_URL:-${DEFAULT_URL}}"
TOKEN="$(read_config_key token)"

# Wait for X server
for i in $(seq 1 30); do
  xset q >/dev/null 2>&1 && break
  sleep 1
done

# DevTools stays loopback-only: debug from the host via an SSH tunnel
# (ssh -L 9222:127.0.0.1:9222). Never expose it to the LAN.
COMMON_FLAGS=(
  --remote-debugging-port=9222
  --remote-debugging-address=127.0.0.1
  --kiosk
  --no-first-run
  --disable-infobars
  --disable-session-crashed-bubble
  --disable-restore-session-state
  --noerrdialogs
  --disable-features=TranslateUI,OptimizationHints,AutofillServerCommunication
  --touch-events=enabled
  --start-maximized
  --ignore-certificate-errors
  --disable-extensions
  --no-sandbox
)

# Unprovisioned or reset panel: open the local provisioning UI instead of the
# kiosk URL so the panel can re-enter the provision flow from the device.
# Only when the provision server is actually installed — the live zoe-touch
# panel runs WITHOUT the provisioning flow (no provision-server.py, no token
# in config.json) and must boot straight into the kiosk, not a dead
# localhost:8888 page.
if [ -f "${PROVISION_SERVER}" ] && { [ ! -f "${PROVISIONED}" ] || [ -z "${TOKEN}" ]; }; then
  echo "[start-kiosk] .provisioned missing or token empty — entering provision mode"
  mkdir -p "${KIOSK_HOME}/.config/chromium-provision"
  python3 "${PROVISION_SERVER}" --mode provision &
  sleep 2
  exec "${CHROMIUM_BIN}" \
    "${COMMON_FLAGS[@]}" \
    --user-data-dir="${KIOSK_HOME}/.config/chromium-provision" \
    "http://localhost:8888/"
fi

echo "Starting Zoe Kiosk: ${ZOE_URL}"
mkdir -p "${KIOSK_HOME}/.config/chromium-kiosk"

# Gate on the configured Zoe host being reachable. On total failure exit
# non-zero so systemd (Restart=always) retries, instead of booting Chromium
# into a connection-error page it never recovers from.
ZOE_HOST="$(python3 -c "import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname or '')" "${ZOE_URL}")"
if [ -n "${ZOE_HOST}" ]; then
  NET_OK=0
  for i in $(seq 1 60); do
    if ping -c1 -W1 "${ZOE_HOST}" >/dev/null 2>&1; then
      NET_OK=1
      break
    fi
    sleep 1
  done
  if [ "${NET_OK}" != "1" ]; then
    echo "ERROR: Zoe host ${ZOE_HOST} unreachable after 60 attempts; exiting for systemd retry" >&2
    exit 1
  fi
fi

# Display power settings
xset s off || true
xset -dpms || true
xset s noblank || true

# Rotation (prefer HDMI panels)
xrandr --output HDMI-1 --rotate right 2>/dev/null || \
xrandr --output HDMI-A-1 --rotate right 2>/dev/null || \
xrandr --output DSI-1 --rotate left 2>/dev/null || true

# Hide cursor
pkill -f 'unclutter -idle' || true
unclutter -idle 0.2 -root &

sleep 2

exec "${CHROMIUM_BIN}" \
  "${COMMON_FLAGS[@]}" \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  --user-data-dir="${KIOSK_HOME}/.config/chromium-kiosk" \
  "${ZOE_URL}"
