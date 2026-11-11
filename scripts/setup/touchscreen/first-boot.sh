#!/usr/bin/env bash
# First-boot orchestration.
# Runs when /opt/TouchKio/.provisioned does not exist.
# Called by openbox-autostart.sh inside the graphical session (DISPLAY=:0).
#
# Flow:  WiFi setup → network wait → system update → QR provisioning
#
# Uses separate Chromium user-data-dirs for each phase to avoid profile conflicts.

set -uo pipefail

LOG="/var/log/zoe-first-boot.log"
CONFIG="/opt/TouchKio/config.json"
WIFI_DONE="/opt/TouchKio/.setup_wifi_done"
PROVISIONED="/opt/TouchKio/.provisioned"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG}"; }

# ── STEP 1: WiFi Setup ───────────────────────────────────────────────────────
if [[ ! -f "${WIFI_DONE}" ]]; then
  log "WiFi not configured. Starting setup hotspot..."

  MAC=$(cat /sys/class/net/wlan0/address 2>/dev/null | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]' || echo "SETUP")
  SSID="ZoeTouch-Setup-${MAC}"

  /opt/TouchKio/wifi-portal/hotspot-start.sh "${SSID}"
  python3 /opt/TouchKio/provision-server.py --mode wifi-setup &
  PORTAL_PID=$!

  log "Hotspot '${SSID}' active. Opening WiFi picker on panel..."
  # Use separate profile dir for WiFi phase (avoids chromium-kiosk profile lock)
  /usr/bin/chromium \
    --kiosk \
    --no-sandbox \
    --ignore-certificate-errors \
    --no-first-run \
    --disable-infobars \
    --noerrdialogs \
    --touch-events=enabled \
    --user-data-dir=/home/zoe/.config/chromium-wifi-setup \
    http://192.168.4.1/ &
  BROWSER_PID=$!

  # Wait for WiFi credentials to be submitted (provision-server.py touches $WIFI_DONE)
  log "Waiting for WiFi credentials..."
  while [[ ! -f "${WIFI_DONE}" ]]; do sleep 2; done

  log "WiFi configured. Shutting down hotspot..."
  kill "${PORTAL_PID}" "${BROWSER_PID}" 2>/dev/null || true
  sleep 1
  /opt/TouchKio/wifi-portal/hotspot-stop.sh
  sleep 3
fi

# ── STEP 2: Wait for network ─────────────────────────────────────────────────
log "Waiting for network..."
ZOE_URL=$(python3 -c "
import json, pathlib
try:
    d = json.loads(pathlib.Path('${CONFIG}').read_text())
    print(d.get('server_url', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

PING_TARGET="${ZOE_URL:-8.8.8.8}"
NETWORK_OK=0
for i in $(seq 1 30); do
  if ping -c1 -W2 "${PING_TARGET}" &>/dev/null 2>&1; then
    NETWORK_OK=1
    log "Network up."
    break
  fi
  log "  Network not yet up (attempt ${i}/30)..."
  sleep 2
done

if [[ ${NETWORK_OK} -eq 0 ]]; then
  log "WARNING: Network not confirmed after 60s — continuing anyway."
fi

# ── STEP 3: System updates ───────────────────────────────────────────────────
log "Running system updates (this may take a few minutes)..."
apt-get update -qq >> "${LOG}" 2>&1 || log "apt-get update failed (non-fatal)"
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q >> "${LOG}" 2>&1 || log "apt-get upgrade failed (non-fatal)"
log "Updates done."

# ── STEP 4: QR Provisioning ──────────────────────────────────────────────────
log "Starting provisioning server..."
python3 /opt/TouchKio/provision-server.py --mode provision &
PROVISION_PID=$!

sleep 2  # let server start

log "Opening provisioning UI on panel..."
# Use separate profile dir for provision phase (avoids profile conflicts)
/usr/bin/chromium \
  --kiosk \
  --no-sandbox \
  --ignore-certificate-errors \
  --no-first-run \
  --disable-infobars \
  --noerrdialogs \
  --touch-events=enabled \
  --user-data-dir=/home/zoe/.config/chromium-provision \
  http://localhost:8888/ &

# Wait for provisioning to complete (.provisioned touched by /save-token handler)
log "Waiting for QR pairing to complete..."
while [[ ! -f "${PROVISIONED}" ]]; do sleep 3; done

log "Provisioning complete! Kiosk is live."
kill "${PROVISION_PID}" 2>/dev/null || true
# Chromium is already showing the dashboard — no restart needed.
