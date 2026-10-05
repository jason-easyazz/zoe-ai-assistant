#!/usr/bin/env bash
# ============================================================
# mac_virtual_panel.sh - run Zoe's panel voice daemon on a Mac ("virtual panel")
#
#   bash scripts/setup/mac_virtual_panel.sh install       idempotent: brew deps, venv, pip, models, .env.voice template
#   bash scripts/setup/mac_virtual_panel.sh configure     prompt (silently) for DEVICE_TOKEN + the Cloudflare Access pair
#   bash scripts/setup/mac_virtual_panel.sh devices       list audio devices (pick AUDIO_DEVICE / AUDIO_OUTPUT_DEVICE)
#   bash scripts/setup/mac_virtual_panel.sh preflight     wake-word scoring test + mic level + speaker tone + one authenticated round trip
#   bash scripts/setup/mac_virtual_panel.sh run           preflight (server only) then the daemon, foreground
#   bash scripts/setup/mac_virtual_panel.sh ptt           push-to-talk: press Enter to start a turn (POST /activate) - the wake-word fallback
#   bash scripts/setup/mac_virtual_panel.sh ui            print/open the touch-UI URL for this panel id
#   bash scripts/setup/mac_virtual_panel.sh lab-summary   aggregate BARGE_DECIDE lines from the daemon log
#   bash scripts/setup/mac_virtual_panel.sh uninstall --yes   delete $ZOE_MAC_PANEL_HOME (the whole rollback)
#   bash scripts/setup/mac_virtual_panel.sh env-template  print the .env.voice template (no side effects)
#
# EVERYTHING it creates lives under ZOE_MAC_PANEL_HOME (default ~/.zoe-virtual-panel):
# the venv, the torch-hub Silero cache (TORCH_HOME), the .env.voice, the daemon log.
# Rollback = delete that directory. It never writes outside it, except Homebrew
# packages (portaudio, python@3.12 - `brew uninstall` them if you want them gone)
# and, only if you ask with HEY_ZOE_ONNX / HEY_ZOE_TFLITE, one untracked hey_zoe.* next to the daemon.
#
# bash 3.2 compatible (macOS /bin/bash). Design + verification status:
# docs/knowledge/mac-virtual-panel.md.
# Exit codes: 0 ok | 2 usage / not macOS | 3 missing prerequisite | 4 preflight failed | 5 bad input
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PANEL_HOME="${ZOE_MAC_PANEL_HOME:-$HOME/.zoe-virtual-panel}"
VENV="${PANEL_HOME}/venv"
ENV_FILE="${PANEL_HOME}/.env.voice"
LOG_FILE="${PANEL_HOME}/voice.log"
MARKER="${PANEL_HOME}/.zoe-virtual-panel-marker"
TORCH_HOME_DIR="${PANEL_HOME}/torch"
REQ_FILE="${HERE}/mac-requirements.txt"
REQ_WAKE_FILE="${HERE}/mac-requirements-wakeword.txt"
WAKE_CLIP="${PANEL_HOME}/wakeword_test/hey_mycroft_test.wav"
WAKE_CLIP_URL="https://raw.githubusercontent.com/dscripka/openWakeWord/main/tests/data/hey_mycroft_test.wav"
REQ_STAMP="${PANEL_HOME}/.requirements.sha256"
PY_FORMULA="${MAC_PANEL_PY_FORMULA:-python@3.12}"
DEFAULT_ZOE_URL="${MAC_PANEL_DEFAULT_ZOE_URL:-https://zoe.the411.life}"

say() { printf '%s\n' "$*"; }
die() { local code="$1"; shift; printf 'ERROR: %s\n' "$*" >&2; exit "$code"; }

need_macos() {
  if [ "$(uname -s)" != "Darwin" ] && [ "${MAC_PANEL_ALLOW_NON_DARWIN:-0}" != "1" ]; then
    die 2 "this command is macOS only (uname: $(uname -s))"
  fi
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi
}

# ── the .env.voice template ───────────────────────────────────────────────────
env_template() {
  cat <<EOF
# Zoe virtual panel (Mac) - ${ENV_FILE}
# Read by \`mac_virtual_panel.sh run\` (sourced, so keep values quoted). chmod 600.
# Fill the three secrets with \`mac_virtual_panel.sh configure\` (silent prompts, nothing
# lands in your shell history). Full guide: docs/knowledge/mac-virtual-panel.md

# --- which backend the daemon uses (pi = aplay/pactl, mac = in-process PortAudio) ---
PANEL_PLATFORM="mac"

# --- identity: a NEW panel id, never the live panel's ---
PANEL_ID="mac-dev"
# Panel device token issued for mac-dev by an admin (POST /api/panels/mac-dev/token).
DEVICE_TOKEN=""

# --- reaching zoe-data through the Cloudflare tunnel (away from the LAN) ---
# On the home VPN use the LAN address instead and no Access pair:
#   MAC_PANEL_DEFAULT_ZOE_URL=https://192.168.1.218 ... install, then VERIFY_SSL="false" below.
ZOE_URL="${DEFAULT_ZOE_URL}"
# Public CA certificate on the tunnel host: keep verification ON. (The Pi sets false
# only because it talks to the Jetson's self-signed LAN cert; set false here only if
# ZOE_URL is that LAN address.)
VERIFY_SSL="true"
# Cloudflare Access service token (Service Auth policy on /api/voice/*). Both or neither.
CF_ACCESS_CLIENT_ID=""
CF_ACCESS_CLIENT_SECRET=""

# --- audio: "default" = the macOS default input/output. Run \`devices\` to pick others. ---
AUDIO_DEVICE="default"
AUDIO_OUTPUT_DEVICE="default"

# --- the daemon's /health + the unauthenticated /activate: loopback only on a laptop ---
HEALTH_BIND="127.0.0.1"
HEALTH_PORT="7777"

# --- logs (the barge-in lab reads this) ---
ZOE_VOICE_LOG="${LOG_FILE}"

# --- behaviour: the live panel's values where they matter, laptop-safe otherwise ---
BARGE_IN_ENABLED="true"
BARGE_IN_THRESHOLD="0.75"     # the live panel's value (tuned on a Jabra); re-tune for a Mac mic
BARGE_DUCK_ENABLED="false"    # set true for the barge-in lab (phase 1 duck/decide/resume)
SPEAKER_ID_ENABLED="false"    # no voice-profile sync / embeddings on a laptop
AMBIENT_CAPTURE_ENABLED="false"  # never record room speech for ambient memory from a laptop
ZOE_ANNOUNCE_POLL_S="15"      # the Pi polls every 5 s over the LAN; 15 s is kinder over a tunnel
ZOE_VOICE_STREAM="1"
EOF
}

# ── the panel home and its marker ─────────────────────────────────────────────
# The marker proves a directory is OURS. `uninstall` deletes only a directory that
# carries it, and install refuses to adopt an existing non-empty directory that does
# not (ZOE_MAC_PANEL_HOME=$HOME/Documents must never become an rm -rf target).
ensure_panel_home() {
  if [ -d "${PANEL_HOME}" ] && [ ! -f "${MARKER}" ] && [ -n "$(ls -A "${PANEL_HOME}" 2>/dev/null)" ]; then
    die 5 "${PANEL_HOME} exists, is not empty and is not a virtual-panel directory (no .zoe-virtual-panel-marker); choose another ZOE_MAC_PANEL_HOME"
  fi
  mkdir -p "${PANEL_HOME}"
  chmod 700 "${PANEL_HOME}" 2>/dev/null || true
  [ -f "${MARKER}" ] || printf 'created by mac_virtual_panel.sh; uninstall deletes only a directory with this file\n' > "${MARKER}"
}

# ── env file helpers ──────────────────────────────────────────────────────────
write_env_if_absent() {
  ensure_panel_home
  if [ -f "${ENV_FILE}" ]; then
    say "kept existing ${ENV_FILE} (not overwritten; \`env-template\` shows the current template)"
    return 0
  fi
  ( umask 077; env_template > "${ENV_FILE}" )
  chmod 600 "${ENV_FILE}"
  say "wrote ${ENV_FILE} (mode 600)"
}

# set_env_key KEY VALUE - rewrite one KEY="..." line (append if missing). No sed: the
# value never goes through an escape layer (awk reads it from the environment).
set_env_key() {
  local key="$1" val="$2" tmp
  case "$val" in
    *\"*|*\$*|*\`*|*\\*) die 5 "$key: value contains a quote, \$, backtick or backslash - tokens never do; refusing" ;;
  esac
  case "$val" in *$'\n'*) die 5 "$key: value contains a newline" ;; esac
  tmp="${ENV_FILE}.tmp.$$"
  ( umask 077
    K="$key" V="$val" awk '
      BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; done = 0 }
      index($0, k "=") == 1 { print k "=\"" v "\""; done = 1; next }
      { print }
      END { if (!done) print k "=\"" v "\"" }' "${ENV_FILE}" > "${tmp}" )
  mv "${tmp}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
}

cmd_configure() {
  [ -f "${ENV_FILE}" ] || write_env_if_absent
  local v
  say "Silent prompts; press Enter to keep the current value."
  printf 'DEVICE_TOKEN for mac-dev: ';           read -rs v; printf '\n'; [ -z "$v" ] || set_env_key DEVICE_TOKEN "$v"
  printf 'CF_ACCESS_CLIENT_ID: ';                read -rs v; printf '\n'; [ -z "$v" ] || set_env_key CF_ACCESS_CLIENT_ID "$v"
  printf 'CF_ACCESS_CLIENT_SECRET: ';            read -rs v; printf '\n'; [ -z "$v" ] || set_env_key CF_ACCESS_CLIENT_SECRET "$v"
  say "saved to ${ENV_FILE}"
}

load_env() {
  [ -f "${ENV_FILE}" ] || die 3 "no ${ENV_FILE}; run: bash scripts/setup/mac_virtual_panel.sh install"
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
  export PANEL_PLATFORM="${PANEL_PLATFORM:-mac}"
  export TORCH_HOME="${TORCH_HOME_DIR}"
  export WAKEWORD_TEST_CLIP="${WAKEWORD_TEST_CLIP:-${WAKE_CLIP}}"
  export PYTHONUNBUFFERED=1
}

venv_python() {
  [ -x "${VENV}/bin/python" ] || die 3 "no venv at ${VENV}; run: bash scripts/setup/mac_virtual_panel.sh install"
  printf '%s' "${VENV}/bin/python"
}

# ── install ───────────────────────────────────────────────────────────────────
cmd_install() {
  need_macos
  if [ "$(uname -m)" != "arm64" ] && [ "${MAC_PANEL_ALLOW_NON_DARWIN:-0}" != "1" ]; then
    say "note: this is $(uname -m), not Apple silicon; the guide was written for arm64 (should still work)"
  fi
  command -v brew >/dev/null 2>&1 || die 3 "Homebrew not found. Install it from https://brew.sh then re-run."

  say "== Homebrew: portaudio, ${PY_FORMULA}"
  brew list portaudio >/dev/null 2>&1 || brew install portaudio
  brew list "${PY_FORMULA}" >/dev/null 2>&1 || brew install "${PY_FORMULA}"
  local brew_py
  brew_py="${MAC_PANEL_PYTHON:-$(brew --prefix "${PY_FORMULA}")/bin/python3.12}"
  [ -x "${brew_py}" ] || die 3 "no interpreter at ${brew_py} (set MAC_PANEL_PYTHON to a python3.10-3.12)"

  ensure_panel_home
  if [ ! -x "${VENV}/bin/python" ]; then
    say "== venv: ${VENV}"
    "${brew_py}" -m venv "${VENV}"
  else
    say "== venv exists: ${VENV}"
  fi

  local want have
  want="$(sha256_of "${REQ_FILE}")$(sha256_of "${REQ_WAKE_FILE}")"
  have="$(cat "${REQ_STAMP}" 2>/dev/null || true)"
  if [ "${want}" = "${have}" ]; then
    say "== pip: requirements unchanged since the last install - skipping"
  else
    say "== pip: ${REQ_FILE}"
    local bp
    bp="$(brew --prefix portaudio)"
    # PyAudio builds from source; on Apple silicon Homebrew is not on the compiler's default search path.
    CFLAGS="-I${bp}/include" LDFLAGS="-L${bp}/lib" "${VENV}/bin/python" -m pip install --upgrade pip
    CFLAGS="-I${bp}/include" LDFLAGS="-L${bp}/lib" "${VENV}/bin/python" -m pip install -r "${REQ_FILE}"
    # Separate, so a missing wheel cannot fail the whole install - but never silently:
    # without a TFLite runtime the wake word is dead on Apple silicon (openWakeWord #336).
    if ! "${VENV}/bin/python" -m pip install -r "${REQ_WAKE_FILE}"; then
      say "WARNING: no TFLite runtime wheel for this Mac (${REQ_WAKE_FILE})."
      say "         The wake word will not fire; use push-to-talk (\`$0 ptt\`). preflight will say FAIL."
    fi
    printf '%s' "${want}" > "${REQ_STAMP}"
  fi

  say "== models: openWakeWord (hey_jarvis + the feature models), Silero VAD (torch hub)"
  TORCH_HOME="${TORCH_HOME_DIR}" "${VENV}/bin/python" - <<'PY'
import openwakeword
from openwakeword.utils import download_models
download_models(["hey_jarvis", "hey_mycroft"])  # hey_mycroft: the preflight scoring test
import torch
torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False, trust_repo=True)
print("models ready")
PY

  mkdir -p "$(dirname "${WAKE_CLIP}")"
  if [ ! -s "${WAKE_CLIP}" ]; then
    curl -fsSL -o "${WAKE_CLIP}" "${WAKE_CLIP_URL}" \
      || say "WARNING: could not download the wake-word test clip; preflight will FAIL its scoring test until you re-run install"
  fi

  if [ -n "${HEY_ZOE_TFLITE:-}" ]; then
    [ -f "${HEY_ZOE_TFLITE}" ] || die 5 "HEY_ZOE_TFLITE=${HEY_ZOE_TFLITE} is not a file"
    cp "${HEY_ZOE_TFLITE}" "${HERE}/hey_zoe.tflite"
    say "== custom wake word: copied to ${HERE}/hey_zoe.tflite (untracked; the Mac's TFLite backend loads it by that name)"
  fi
  if [ -n "${HEY_ZOE_ONNX:-}" ]; then
    [ -f "${HEY_ZOE_ONNX}" ] || die 5 "HEY_ZOE_ONNX=${HEY_ZOE_ONNX} is not a file"
    cp "${HEY_ZOE_ONNX}" "${HERE}/hey_zoe.onnx"
    say "== custom wake word: copied to ${HERE}/hey_zoe.onnx (untracked; the daemon loads it by that name)"
  elif [ ! -f "${HERE}/hey_zoe.onnx" ]; then
    say "== wake word: no hey_zoe.onnx beside the daemon -> it will answer to 'Hey Jarvis'."
    say "   To test 'Hey Zoe' on the Mac you need the TFLite build: HEY_ZOE_TFLITE=/path/to/hey_zoe.tflite bash $0 install"
  fi

  write_env_if_absent
  cat <<EOF

Installed. Next:
  1. bash $0 configure      # DEVICE_TOKEN + Cloudflare Access pair (see the guide for how to get them)
  2. bash $0 devices        # optional: choose AUDIO_DEVICE / AUDIO_OUTPUT_DEVICE in ${ENV_FILE}
  3. bash $0 preflight      # mic, speaker, tunnel, token - fix anything marked FAIL
  4. bash $0 run            # the daemon; say the wake word
  5. bash $0 ui             # the touch UI for ${PANEL_ID:-mac-dev}, in a browser tab
Rollback: bash $0 uninstall --yes   (deletes ${PANEL_HOME})
EOF
}

# ── run / preflight / devices ─────────────────────────────────────────────────
cmd_devices() {
  need_macos
  local py; py="$(venv_python)"
  load_env
  exec "${py}" "${HERE}/mac_panel/preflight.py" devices
}

cmd_preflight() {
  need_macos
  local py; py="$(venv_python)"
  load_env
  "${py}" "${HERE}/mac_panel/preflight.py" check "$@" || die 4 "preflight failed - fix the [FAIL] lines above"
}

cmd_run() {
  need_macos
  local py skip=0 a
  py="$(venv_python)"
  for a in "$@"; do [ "$a" = "--skip-preflight" ] && skip=1; done
  load_env
  [ -n "${DEVICE_TOKEN:-}" ] || die 3 "DEVICE_TOKEN is empty in ${ENV_FILE}; run: bash $0 configure"
  if [ "$skip" != 1 ]; then
    "${py}" "${HERE}/mac_panel/preflight.py" check --no-audio || die 4 "preflight failed; fix it, or pass --skip-preflight and talk with push-to-talk (bash $0 ptt) if only the wake word is dead"
  fi
  cd "${HERE}"
  say "starting the virtual panel '${PANEL_ID}' -> ${ZOE_URL} (Ctrl-C to stop; log: ${ZOE_VOICE_LOG:-stderr})"
  # caffeinate -i keeps an idle Mac awake while the daemon runs (closing the lid still sleeps it).
  if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i "${py}" "${HERE}/zoe_voice_daemon.py"
  fi
  exec "${py}" "${HERE}/zoe_voice_daemon.py"
}

# Push-to-talk: the wake-word fallback. POST /activate is the same trigger the touch UI's
# orb-tap uses (it starts a recording as if the wake word had fired). No Origin header, so
# the Mac daemon's origin guard lets it through; the daemon must already be running.
cmd_ptt() {
  [ -f "${ENV_FILE}" ] && load_env
  local url="http://${HEALTH_BIND:-127.0.0.1}:${HEALTH_PORT:-7777}/activate"
  say "Push-to-talk: press Enter to start a turn, Ctrl-D or Ctrl-C to quit. (daemon: ${url})"
  while printf '> ' && read -r _; do
    curl -fsS -X POST "${url}" >/dev/null || say "  could not reach the daemon at ${url} - is \`run\` going?"
  done
  printf '\n'
}

cmd_ui() {
  [ -f "${ENV_FILE}" ] && load_env
  local url="${ZOE_URL:-${DEFAULT_ZOE_URL}}/touch/home.html?panel_id=${PANEL_ID:-mac-dev}&kiosk=1"
  say "${url}"
  if [ "$(uname -s)" = "Darwin" ]; then open "${url}"; fi
}

cmd_lab_summary() {
  local py; py="$(venv_python)"
  [ -f "${ENV_FILE}" ] && load_env
  exec "${py}" "${HERE}/mac_panel/barge_lab_summary.py" "${ZOE_VOICE_LOG:-${LOG_FILE}}" "$@"
}

cmd_uninstall() {
  [ "${1:-}" = "--yes" ] || die 2 "this deletes ${PANEL_HOME} (venv, Silero cache, .env.voice with your tokens, log). Re-run with --yes."
  case "${PANEL_HOME}" in
    ""|"/"|"$HOME"|"$HOME/") die 2 "refusing to delete '${PANEL_HOME}'" ;;
  esac
  [ -d "${PANEL_HOME}" ] || die 2 "${PANEL_HOME} does not exist; nothing to remove"
  [ -f "${MARKER}" ] || die 2 "refusing to delete ${PANEL_HOME}: it has no .zoe-virtual-panel-marker, so this script did not create it"
  rm -rf "${PANEL_HOME}"
  say "removed ${PANEL_HOME}. (Homebrew portaudio/${PY_FORMULA} left in place; hey_zoe.onnx, if you copied one, is at ${HERE}/hey_zoe.onnx.)"
}

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}"; }

main() {
  local cmd="${1:-help}"
  [ "$#" -eq 0 ] || shift
  case "$cmd" in
    install) cmd_install "$@" ;;
    configure) cmd_configure "$@" ;;
    devices) cmd_devices "$@" ;;
    preflight) cmd_preflight "$@" ;;
    run) cmd_run "$@" ;;
    ptt) cmd_ptt "$@" ;;
    ui) cmd_ui "$@" ;;
    lab-summary) cmd_lab_summary "$@" ;;
    uninstall) cmd_uninstall "$@" ;;
    env-template) env_template ;;
    env) write_env_if_absent ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
