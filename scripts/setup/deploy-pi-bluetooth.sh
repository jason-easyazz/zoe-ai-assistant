#!/usr/bin/env bash
# ============================================================
# deploy-pi-bluetooth.sh - Deploy the panel Bluetooth agent (Settings > Bluetooth devices) to a panel Pi
# Run from the Jetson, from a git WORKTREE of merged main (never the live checkout):
#   DRY_RUN=1 bash scripts/setup/deploy-pi-bluetooth.sh            # print the plan, touch nothing
#   bash scripts/setup/deploy-pi-bluetooth.sh [--install-unit] [--start]
#
# WHAT IT SHIPS (exactly): services/panel-bluetooth/{panel_bt,requirements.txt} -> ~/.zoe-bluetooth/app/, and ONE file for omi-sync:
# services/omi-sync/omi_sync/{radio_lock.py,ble_link.py} (omi-sync yields the radio to this agent; omi-sync itself is NOT restarted
# unless you pass --restart-omi-sync). It creates ~/.zoe-bluetooth/venv from requirements.txt (bleak 3.0.2 + smpclient 7.3.0) and a
# TEMPLATE ~/.zoe-bluetooth/.env.bluetooth ONLY if none exists (mode 0600, ENABLED=0 - you fill in DEVICE_TOKEN and flip it).
#
# It NEVER touches zoe-voice, the panel agent on :8765, the kiosk, or any existing env file. The user unit is left alone unless
# --install-unit (it refuses to overwrite an existing unit). The agent is not started unless --start.
#
# After a start it VERIFIES on the Pi: unit active (or a clean flag-off exit), `python -m panel_bt status` parses, md5 of every shipped
# file equals the local copy. Exit codes: 0 ok | 2 usage | 3 unit install refused | 4 verification failed | 5 transfer failed
#
# ROLLBACK: ssh pi@<host> 'systemctl --user disable --now zoe-bluetooth.service; rm -rf ~/.zoe-bluetooth ~/.config/systemd/user/zoe-bluetooth.service'
#           (omi-sync keeps working: with no lock holder its connect path is unchanged.)  Also unset ZOE_BLUETOOTH_SETTINGS on the Jetson.
# Env: PI_HOST PI_USER PI_DIR VERIFY_TIMEOUT_S DRY_RUN=1
# ============================================================
set -euo pipefail

PI_HOST="${PI_HOST:-192.168.1.61}"
PI_USER="${PI_USER:-pi}"
PI_DIR="${PI_DIR:-/home/pi/.zoe-bluetooth}"
VERIFY_TIMEOUT_S="${VERIFY_TIMEOUT_S:-25}"
DRY_RUN="${DRY_RUN:-0}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH_TARGET="${PI_USER}@${PI_HOST}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
OMI_SHIPPED=(radio_lock.py ble_link.py)

INSTALL_UNIT=0; START=0; RESTART_OMI=0
for arg in "$@"; do
  case "$arg" in
    --install-unit) INSTALL_UNIT=1 ;;
    --start) START=1 ;;
    --restart-omi-sync) RESTART_OMI=1 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

run() { if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
rssh() { if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] ssh ${SSH_TARGET} $1"; else ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "$1"; fi; }

[ -d "${REPO}/services/panel-bluetooth/panel_bt" ] || { echo "run this from a checkout that has services/panel-bluetooth" >&2; exit 2; }
case "$REPO" in /home/zoe/assistant) echo "refusing to deploy from the LIVE checkout; use a git worktree of merged main" >&2; exit 2 ;; esac

echo "== plan: ${REPO} -> ${SSH_TARGET}:${PI_DIR} (unit: ${INSTALL_UNIT}, start: ${START}, restart omi-sync: ${RESTART_OMI})"
rssh "mkdir -p '${PI_DIR}/app' && chmod 700 '${PI_DIR}'"

# 1. code (rsync would be nicer; tar over ssh keeps the Pi dependency-free)
if [ "$DRY_RUN" = 1 ]; then
  echo "[dry-run] tar panel_bt + requirements.txt | ssh ${SSH_TARGET} 'tar -x -C ${PI_DIR}/app'"
else
  ( cd "${REPO}/services/panel-bluetooth" && tar --exclude='__pycache__' -c panel_bt requirements.txt ) \
    | ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "rm -rf '${PI_DIR}/app/panel_bt' && tar -x -C '${PI_DIR}/app'" || { echo "transfer failed" >&2; exit 5; }
fi

# 2. the omi-sync side of the shared radio lock (additive; omi-sync is only restarted on request)
for f in "${OMI_SHIPPED[@]}"; do
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] scp omi_sync/$f -> ~/.zoe-omi-sync/app/omi_sync/$f (a .bak-<ts> copy is kept)"; continue; fi
  if ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "test -d ~/.zoe-omi-sync/app/omi_sync"; then
    ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "test -f ~/.zoe-omi-sync/app/omi_sync/$f && cp -p ~/.zoe-omi-sync/app/omi_sync/$f ~/.zoe-omi-sync/app/omi_sync/$f.bak-\$(date +%Y%m%d-%H%M%S) || true"
    scp "${SSH_OPTS[@]}" "${REPO}/services/omi-sync/omi_sync/$f" "${SSH_TARGET}:.zoe-omi-sync/app/omi_sync/$f" >/dev/null || { echo "transfer failed" >&2; exit 5; }
  else
    echo "note: omi-sync is not installed on this Pi; skipping omi_sync/$f"
  fi
done
[ "$RESTART_OMI" = 1 ] && rssh "systemctl --user try-restart omi-sync.service || true"

# 3. venv (idempotent) - a SEPARATE venv: bleak 3.x here, omi-sync stays on 0.22.3
rssh "test -x '${PI_DIR}/venv/bin/python3' || python3 -m venv '${PI_DIR}/venv'; '${PI_DIR}/venv/bin/pip' install --quiet -r '${PI_DIR}/app/requirements.txt'"

# 4. env template, ONLY if absent (never overwrites; 0600; flags OFF)
rssh "test -f '${PI_DIR}/.env.bluetooth' || { umask 077; printf '%s\n' \
'# panel Bluetooth agent - fill in DEVICE_TOKEN (the panel device token) and set PANEL_BT_ENABLED=1' \
'PANEL_BT_ENABLED=0' 'ZOE_URL=https://192.168.1.218' 'DEVICE_TOKEN=' 'VERIFY_SSL=0' \
'# firmware updates stay OFF until one supervised update has been run' 'PANEL_BT_OTA_ENABLED=0' \
'# optional: a USB Bluetooth adapter for a stronger link (hci1)' '#PANEL_BT_HCI=hci1' > '${PI_DIR}/.env.bluetooth'; echo created env template; }"

# 5. unit (never overwritten)
if [ "$INSTALL_UNIT" = 1 ]; then
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] install ${REPO}/scripts/setup/systemd/zoe-bluetooth.service -> ~/.config/systemd/user/ (refuses if present)"
  else
    if ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "test -e ~/.config/systemd/user/zoe-bluetooth.service"; then echo "unit already installed; not overwriting" >&2; exit 3; fi
    ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "mkdir -p ~/.config/systemd/user"
    scp "${SSH_OPTS[@]}" "${REPO}/scripts/setup/systemd/zoe-bluetooth.service" "${SSH_TARGET}:.config/systemd/user/zoe-bluetooth.service" >/dev/null
    ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "systemctl --user daemon-reload"
  fi
fi

# 6. start + verify
if [ "$START" = 1 ]; then
  rssh "systemctl --user enable --now zoe-bluetooth.service"
fi
if [ "$DRY_RUN" != 1 ]; then
  rssh "cd '${PI_DIR}/app' && set -a && . '${PI_DIR}/.env.bluetooth' && set +a && '${PI_DIR}/venv/bin/python3' -m panel_bt status >/dev/null" \
    || { echo "VERIFY FAILED: python -m panel_bt status did not run" >&2; exit 4; }
  bad=0
  while IFS= read -r f; do
    want="$(md5sum "${REPO}/services/panel-bluetooth/${f}" | cut -d' ' -f1)"
    got="$(ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "md5sum '${PI_DIR}/app/${f}' 2>/dev/null | cut -d' ' -f1" || true)"
    [ "$want" = "$got" ] || { echo "md5 mismatch: ${f}" >&2; bad=1; }
  done < <(cd "${REPO}/services/panel-bluetooth" && find panel_bt -type f ! -path '*/__pycache__/*' | sort)
  [ "$bad" = 0 ] || exit 4
  if [ "$START" = 1 ]; then
    for _ in $(seq 1 "$VERIFY_TIMEOUT_S"); do
      if ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "systemctl --user is-active --quiet zoe-bluetooth.service"; then echo "agent active"; exit 0; fi
      sleep 1
    done
    echo "NOTE: unit not active - expected when PANEL_BT_ENABLED=0 (clean flag-off exit). Check: journalctl --user -u zoe-bluetooth -n 30" >&2
  fi
fi
echo "done (rollback in the header of this script)."
