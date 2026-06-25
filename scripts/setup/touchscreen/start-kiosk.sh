#!/bin/bash
set -e

export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
mkdir -p /home/pi/.config/chromium-kiosk

CONFIG="/opt/TouchKio/config.json"
DEFAULT_URL="https://zoe.the411.life/touch/dashboard.html"
ZOE_URL=$(python3 - <<'PYC'
import json
try:
    d=json.load(open('/opt/TouchKio/config.json'))
    print(d.get('url') or 'https://zoe.the411.life/touch/dashboard.html')
except Exception:
    print('https://zoe.the411.life/touch/dashboard.html')
PYC
)

echo "Starting Zoe Kiosk: $ZOE_URL"

# Wait for network
for i in $(seq 1 30); do
  ping -c1 -W1 192.168.1.218 >/dev/null 2>&1 && break
  sleep 1
done

# Wait for X server
for i in $(seq 1 30); do
  xset q >/dev/null 2>&1 && break
  sleep 1
done

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

exec chromium-browser \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=* \
  --kiosk \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --noerrdialogs \
  --disable-features=TranslateUI,OptimizationHints,AutofillServerCommunication \
  --touch-events=enabled \
  --start-maximized \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  --user-data-dir=/home/pi/.config/chromium-kiosk \
  --ignore-certificate-errors \
  --disable-extensions \
  --no-sandbox \
  "$ZOE_URL"

