#!/usr/bin/env bash
set -euo pipefail

# Install Zoe touchscreen kiosk configuration onto a Raspberry Pi touchscreen.
#
# Defaults target the known touchscreen device:
#   host: 192.168.1.61
#   user: pi
#
# Example:
#   scripts/setup/touchscreen/install_touchscreen.sh
#   scripts/setup/touchscreen/install_touchscreen.sh --host 192.168.1.61 --user pi

HOST="192.168.1.61"
USER_NAME="pi"
SSH_KEY=""
SERVER_URL="https://192.168.1.218"
PANEL_ID="zoe-touch-pi"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --user)
      USER_NAME="$2"
      shift 2
      ;;
    --ssh-key)
      SSH_KEY="$2"
      shift 2
      ;;
    --server-url)
      SERVER_URL="$2"
      shift 2
      ;;
    --panel-id)
      PANEL_ID="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

CONFIG_SRC="${ROOT_DIR}/config.json"
CONFIG_TMP="${TMP_DIR}/config.json"

KIOSK_URL="${SERVER_URL}/touch/dashboard.html?panel_id=${PANEL_ID}&kiosk=1"

python3 - <<PY
import json
from pathlib import Path

src = Path("${CONFIG_SRC}")
dst = Path("${CONFIG_TMP}")
data = json.loads(src.read_text())
data["url"] = "${KIOSK_URL}"
data["panel_id"] = "${PANEL_ID}"
dst.write_text(json.dumps(data, indent=2) + "\n")
PY

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [[ -n "${SSH_KEY}" ]]; then
  SSH_OPTS+=(-i "${SSH_KEY}")
fi

REMOTE="${USER_NAME}@${HOST}"
REMOTE_TMP="/tmp/zoe-touchscreen-setup"

echo "==> Uploading touchscreen templates to ${REMOTE}"
ssh "${SSH_OPTS[@]}" "${REMOTE}" "mkdir -p ${REMOTE_TMP} ~/.config/autostart"
scp "${SSH_OPTS[@]}" "${CONFIG_TMP}" "${REMOTE}:${REMOTE_TMP}/config.json"
scp "${SSH_OPTS[@]}" "${ROOT_DIR}/start-kiosk.sh" "${REMOTE}:${REMOTE_TMP}/start-kiosk.sh"
scp "${SSH_OPTS[@]}" "${ROOT_DIR}/zoe-kiosk.desktop" "${REMOTE}:${REMOTE_TMP}/zoe-kiosk.desktop"
scp "${SSH_OPTS[@]}" "${ROOT_DIR}/display-rotation.desktop" "${REMOTE}:${REMOTE_TMP}/display-rotation.desktop"
scp "${SSH_OPTS[@]}" "${ROOT_DIR}/force-rotate.desktop" "${REMOTE}:${REMOTE_TMP}/force-rotate.desktop"

echo "==> Installing files on touchscreen"
ssh "${SSH_OPTS[@]}" "${REMOTE}" "
  sudo install -m 0644 ${REMOTE_TMP}/config.json /opt/TouchKio/config.json
  sudo install -m 0755 ${REMOTE_TMP}/start-kiosk.sh /opt/TouchKio/start-kiosk.sh
  install -m 0644 ${REMOTE_TMP}/zoe-kiosk.desktop ~/.config/autostart/zoe-kiosk.desktop
  install -m 0644 ${REMOTE_TMP}/display-rotation.desktop ~/.config/autostart/display-rotation.desktop
  install -m 0644 ${REMOTE_TMP}/force-rotate.desktop ~/.config/autostart/force-rotate.desktop
"

echo "==> Restarting Chromium kiosk process"
ssh "${SSH_OPTS[@]}" "${REMOTE}" "
  pkill -f '/usr/lib/chromium/chromium' || true
  nohup /opt/TouchKio/start-kiosk.sh >/tmp/zoe-kiosk-restart.log 2>&1 &
"

echo "==> Done"
echo "Touchscreen updated:"
echo "  host: ${HOST}"
echo "  panel_id: ${PANEL_ID}"
echo "  url: ${KIOSK_URL}"
