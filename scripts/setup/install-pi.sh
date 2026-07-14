#!/usr/bin/env bash
# ============================================================================
# install-pi.sh — one-command install for a Zoe touch panel (Raspberry Pi)
# ============================================================================
# Run this FROM the Zoe repo (dev box or Jetson). It provisions a Pi over SSH:
#   1. Kiosk UI    — scripts/setup/touchscreen/install_touchscreen.sh
#   2. Voice daemon — scripts/setup/pi_voice_daemon_install.sh (wake word + STT)
#
# The voice daemon installs to ~/.zoe-voice on the Pi and runs as a `--user`
# service (systemctl --user), matching the panel's live layout — do not relocate
# it to a system service.
#
# Usage:
#   scripts/setup/install-pi.sh --host <PI_IP> --user <USER> \
#     --server-url https://<ZOE_HOST_IP> [--panel-id zoe-touch-pi] \
#     [--device-token <TOKEN>] [--audio-device hw:2,0]
#
# Options:
#   --host IP            Pi hostname/IP            (default 192.168.1.61)
#   --user NAME          SSH user on the Pi        (default zoe)
#   --ssh-key PATH       SSH identity file         (optional)
#   --server-url URL     Zoe host base URL         (default https://192.168.1.218)
#   --panel-id ID        panel identifier          (default zoe-touch-pi)
#   --device-token TOK   panel device token for voice auth (see note below)
#   --audio-device DEV   ALSA capture device       (default default; `arecord -l`)
#   --skip-kiosk         don't install the kiosk UI
#   --skip-voice         don't install the voice daemon
#   -h, --help           show this help
#
# Device token: the voice daemon authenticates to the Jetson with a panel token.
# Mint one on the Zoe host (admin auth):
#   POST /api/panels/<panel-id>/token
# Pass it via --device-token, or set it later in ~/.zoe-voice/.env.voice on the Pi.
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/setup/lib/common.sh
source "${ROOT_DIR}/scripts/setup/lib/common.sh"
SETUP_DIR="${ROOT_DIR}/scripts/setup"

HOST="192.168.1.61"
USER_NAME="zoe"
SSH_KEY=""
SERVER_URL="https://192.168.1.218"
PANEL_ID="zoe-touch-pi"
DEVICE_TOKEN=""
AUDIO_DEVICE="default"
SKIP_KIOSK=0
SKIP_VOICE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --server-url) SERVER_URL="$2"; shift 2 ;;
    --panel-id) PANEL_ID="$2"; shift 2 ;;
    --device-token) DEVICE_TOKEN="$2"; shift 2 ;;
    --audio-device) AUDIO_DEVICE="$2"; shift 2 ;;
    --skip-kiosk) SKIP_KIOSK=1; shift ;;
    --skip-voice) SKIP_VOICE=1; shift ;;
    -h|--help) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1 (try --help)" ;;
  esac
done

require_cmd ssh
require_cmd scp
REMOTE="${USER_NAME}@${HOST}"
SSH_OPTS=(-o ConnectTimeout=10)
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")

step "Provisioning ${REMOTE} (panel: ${PANEL_ID})"
log "Checking SSH connectivity…"
ssh "${SSH_OPTS[@]}" "$REMOTE" true || die "Cannot SSH to ${REMOTE} (check --host/--user/--ssh-key)."
ok "SSH ok"

# ── 1. Kiosk UI ─────────────────────────────────────────────────────────────
if [[ "$SKIP_KIOSK" == "0" ]]; then
  step "Kiosk UI"
  kiosk_args=(--host "$HOST" --user "$USER_NAME" --server-url "$SERVER_URL" --panel-id "$PANEL_ID")
  [[ -n "$SSH_KEY" ]] && kiosk_args+=(--ssh-key "$SSH_KEY")
  bash "${SETUP_DIR}/touchscreen/install_touchscreen.sh" "${kiosk_args[@]}"
  ok "kiosk installed"
else
  warn "--skip-kiosk: not installing the kiosk UI"
fi

# ── 2. Voice daemon ─────────────────────────────────────────────────────────
if [[ "$SKIP_VOICE" == "0" ]]; then
  step "Voice daemon"
  REMOTE_TMP="/tmp/zoe-voice-install"
  # shellcheck disable=SC2029  # REMOTE_TMP is a local constant; client-side expansion is intended
  ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ${REMOTE_TMP}"
  scp "${SSH_OPTS[@]}" \
    "${SETUP_DIR}/pi_voice_daemon_install.sh" \
    "${SETUP_DIR}/zoe_voice_daemon.py" \
    "${REMOTE}:${REMOTE_TMP}/"
  ok "installer + daemon copied to ${REMOTE}:${REMOTE_TMP}"

  voice_args=(--zoe-url "$SERVER_URL" --panel-id "$PANEL_ID" --audio-device "$AUDIO_DEVICE")
  [[ -n "$DEVICE_TOKEN" ]] && voice_args+=(--device-token "$DEVICE_TOKEN")
  log "Running voice daemon installer on ${REMOTE}…"
  # Quote args for the remote shell.
  printf -v remote_cmd 'bash %q/pi_voice_daemon_install.sh' "$REMOTE_TMP"
  for a in "${voice_args[@]}"; do printf -v remote_cmd '%s %q' "$remote_cmd" "$a"; done
  # shellcheck disable=SC2029  # remote_cmd is pre-quoted with %q; expand it on the remote
  ssh "${SSH_OPTS[@]}" "$REMOTE" "$remote_cmd"
  ssh "${SSH_OPTS[@]}" "$REMOTE" "systemctl --user restart zoe-voice.service || systemctl --user start zoe-voice.service || true"
  ok "voice daemon installed + started (systemctl --user)"

  if [[ -z "$DEVICE_TOKEN" ]]; then
    warn "No --device-token given. Voice auth will fail until you set DEVICE_TOKEN."
    warn "  On the Zoe host (admin): POST /api/panels/${PANEL_ID}/token"
    warn "  Then on the Pi: edit ~/.zoe-voice/.env.voice and 'systemctl --user restart zoe-voice'"
  fi
else
  warn "--skip-voice: not installing the voice daemon"
fi

step "Done"
ok "Touch panel provisioned: ${REMOTE}"
printf '%b\n' "  kiosk URL: ${SERVER_URL}/touch/skybridge.html?panel_id=${PANEL_ID}&kiosk=1"
printf '%b\n' "  ${C_DIM}voice logs: ssh ${REMOTE} 'journalctl --user -u zoe-voice -f'${C_NC}"
