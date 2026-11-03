#!/usr/bin/env bash
# ============================================================
# deploy-pi-voice.sh — Deploy the Zoe voice daemon to the panel Pi
# Run from the Jetson:  bash scripts/setup/deploy-pi-voice.sh [--install-unit [--force-unit]]
#
# LIVE PANEL DEFAULTS: the zoe-touch panel runs the daemon as user `pi`
# with files in /home/pi/.zoe-voice/ under the USER service zoe-voice
# (ssh alias `zoe-pi`; restart with `systemctl --user restart zoe-voice`).
# These defaults target that live layout. For a fresh Pi provisioned by
# pi_voice_daemon_install.sh under a different user, override PI_USER /
# PI_DAEMON_DIR / PI_VENV explicitly.
#
# WHAT IT SHIPS (exactly): zoe_voice_daemon.py, zoe_voice_announce.py (the
# daemon imports it; a partial deploy silently disables announce polling),
# zoe_voice_enroll_mode.py (the daemon imports it; without it Zoe-driven voice
# enrolment commands are refused and logged), zoe_speaker_resnet34.py + its
# zoe_speaker_resnet34_artifacts.json (the flag-dark WeSpeaker ResNet34 embedder; only
# imported when ZOE_SPEAKER_MODEL=dual|resnet34 - the INT8 MODEL FILE itself is not
# shipped here, see docs/knowledge/speaker-resnet34-switch.md), zoe_enroll_flow.py (the hand-run
# guided flow, kept in step), pi-requirements.txt. It NEVER ships scripts/setup/zoe-voice.service as-is:
# that file is the JETSON template (/home/zoe paths, WantedBy=multi-user.target)
# and installing it over the live unit killed the panel daemon with
# status=203/EXEC (incident 2026-10-04 23:12).
#
# THE UNIT IS LEFT ALONE by default. Only `--install-unit` renders a unit for
# the target paths and installs it; it refuses when a unit already exists
# unless `--force-unit` is also given (a timestamped backup is kept first).
#
# BUILD.json: before the restart it writes ${PI_DAEMON_DIR}/BUILD.json - the git sha
# it ships from (<sha>-dirty when a shipped file differs from that commit, "unknown"
# outside git) and the time - which the daemon reads at start and reports in /health
# (build, beside code_sha256 and wake_model_sha256).
#
# After the restart the script VERIFIES on the Pi (unit active within
# ${VERIFY_TIMEOUT_S:-20}s, /health answers on 127.0.0.1:7777 and reports the
# build just shipped, md5 of every shipped file equals the local copy) and exits
# non-zero, printing the rollback, if anything is off. With ZOE_SPEAKER_MODEL=resnet34 on the Pi it also waits
# (RESNET34_READY_TIMEOUT_S) for /health's speaker_resnet34 to be ready and fails otherwise.
#
# Env: PI_HOST PI_USER PI_DAEMON_DIR PI_VENV VERIFY_TIMEOUT_S HEALTH_PORT RESNET34_READY_TIMEOUT_S
#      DRY_RUN=1  print the plan + rendered unit, touch nothing.
# Exit codes: 0 ok | 2 usage | 3 unit install refused | 4 verification failed
#             | 5 transfer/render failed
# ============================================================
set -euo pipefail

PI_HOST="${PI_HOST:-192.168.1.61}"
PI_USER="${PI_USER:-pi}"
PI_DAEMON_DIR="${PI_DAEMON_DIR:-/home/pi/.zoe-voice}"
PI_VENV="${PI_VENV:-/home/pi/.zoe-voice/venv}"
VERIFY_TIMEOUT_S="${VERIFY_TIMEOUT_S:-20}"
RESNET34_READY_TIMEOUT_S="${RESNET34_READY_TIMEOUT_S:-45}"
HEALTH_PORT="${HEALTH_PORT:-7777}"
DRY_RUN="${DRY_RUN:-0}"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_TEMPLATE="${SRC_DIR}/zoe-voice.service"
# Remote paths, expanded by the Pi's shell (not ours).
# shellcheck disable=SC2016
REMOTE_UNIT_DIR='$HOME/.config/systemd/user'
REMOTE_UNIT="${REMOTE_UNIT_DIR}/zoe-voice.service"
# The daemon's only local import is `zoe_voice_announce` (scripts/setup); ship
# exactly the daemon + what it imports + the pip manifest.
SHIPPED_FILES=(zoe_voice_daemon.py zoe_voice_announce.py zoe_voice_enroll_mode.py zoe_enroll_flow.py zoe_speaker_resnet34.py zoe_speaker_resnet34_artifacts.json fetch_speaker_models.sh pi-requirements.txt)

INSTALL_UNIT=0
FORCE_UNIT=0
for arg in "$@"; do
  case "$arg" in
    --install-unit) INSTALL_UNIT=1 ;;
    --force-unit) FORCE_UNIT=1 ;;
    -h|--help) sed -n '2,38p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done
if [ "$FORCE_UNIT" = 1 ] && [ "$INSTALL_UNIT" != 1 ]; then
  echo "--force-unit only makes sense together with --install-unit" >&2
  exit 2
fi

TS="$(date +%Y%m%d-%H%M%S)"
BAK_SUFFIX=".bak-${TS}"

# The build the daemon will report in /health (written to the Pi as BUILD.json below).
BUILD_SHA="$(git -C "${SRC_DIR}" rev-parse HEAD 2>/dev/null || true)"
if [ -z "${BUILD_SHA}" ]; then
  BUILD_SHA="unknown"
elif [ -n "$(git -C "${SRC_DIR}" status --porcelain -- "${SHIPPED_FILES[@]}" 2>/dev/null)" ]; then
  BUILD_SHA="${BUILD_SHA}-dirty"
fi
BUILD_JSON="{\"sha\": \"${BUILD_SHA}\", \"deployed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
SSH_TARGET="${PI_USER}@${PI_HOST}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

# Run a command on the Pi. $1 = the whole remote command line.
# shellcheck disable=SC2029  # client-side expansion is intended
rssh() {
  ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "$1"
}

# Escape a value for the replacement side of a sed s||| expression.
sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

# Render the Jetson unit template for the Pi layout. Writes to stdout.
render_unit() {
  local wd ex envf
  wd="$(sed_escape "${PI_DAEMON_DIR}")"
  ex="$(sed_escape "${PI_VENV}/bin/python3 ${PI_DAEMON_DIR}/zoe_voice_daemon.py")"
  envf="$(sed_escape "${PI_DAEMON_DIR}/.env.voice")"
  sed \
    -e '/^User=/d' \
    -e '/^Group=/d' \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${wd}|" \
    -e "s|^ExecStart=.*|ExecStart=${ex}|" \
    -e "s|^EnvironmentFile=-/home/zoe/.*|EnvironmentFile=-${envf}|" \
    -e 's|^WantedBy=.*|WantedBy=default.target|' \
    "${UNIT_TEMPLATE}"
}

# Fail unless the rendered unit carries the target paths and no Jetson path.
check_rendered_unit() {
  local unit="$1" want
  if grep -q '/home/zoe' "${unit}"; then
    echo "ERROR: rendered unit still contains a Jetson path (/home/zoe):" >&2
    grep -n '/home/zoe' "${unit}" >&2
    return 1
  fi
  for want in \
    "WorkingDirectory=${PI_DAEMON_DIR}" \
    "ExecStart=${PI_VENV}/bin/python3 ${PI_DAEMON_DIR}/zoe_voice_daemon.py" \
    "EnvironmentFile=-${PI_DAEMON_DIR}/.env.voice" \
    "WantedBy=default.target"; do
    if ! grep -qxF "${want}" "${unit}"; then
      echo "ERROR: rendered unit is missing the line: ${want}" >&2
      return 1
    fi
  done
}

print_rollback() {
  local f
  {
    echo ""
    echo "ROLLBACK (on the Pi, ${SSH_TARGET}) - restore the .bak copies, then restart:"
    for f in "${SHIPPED_FILES[@]}"; do
      echo "  [ -f ${PI_DAEMON_DIR}/${f}${BAK_SUFFIX} ] && cp -p ${PI_DAEMON_DIR}/${f}${BAK_SUFFIX} ${PI_DAEMON_DIR}/${f}"
    done
    if [ "$INSTALL_UNIT" = 1 ]; then
      echo "  [ -f ${REMOTE_UNIT}${BAK_SUFFIX} ] && cp -p ${REMOTE_UNIT}${BAK_SUFFIX} ${REMOTE_UNIT}   # exists only if a unit was replaced"
    fi
    echo "  rm -f ${PI_DAEMON_DIR}/BUILD.json   # it names the build just shipped; without it /health says 'unknown'"
    echo "  systemctl --user daemon-reload && systemctl --user restart zoe-voice"
    echo "  # known-good unit copy if the live one is damaged: ${PI_DAEMON_DIR}/zoe-voice.service.pi-20261004"
    echo "  # see docs/knowledge/incident-runbook.md ('Pi deploy broke the unit')"
  } >&2
}

fail_verify() {
  echo "" >&2
  echo "DEPLOY VERIFICATION FAILED: $1" >&2
  print_rollback
  exit 4
}

unit_mode="left untouched (restart only)"
if [ "$INSTALL_UNIT" = 1 ]; then
  unit_mode="--install-unit"
  [ "$FORCE_UNIT" = 1 ] && unit_mode="--install-unit --force-unit"
fi
echo "==> Deploying Zoe voice daemon to ${SSH_TARGET}:${PI_DAEMON_DIR}"
echo "    files: ${SHIPPED_FILES[*]}"
echo "    build: ${BUILD_SHA}"
echo "    unit : ${unit_mode}"

# --- 0. Render + sanity-check the unit locally (before touching the Pi) -----
RENDER_DIR=""
cleanup() {
  if [ -n "${RENDER_DIR}" ]; then rm -rf "${RENDER_DIR}"; fi
}
trap cleanup EXIT
if [ "$INSTALL_UNIT" = 1 ]; then
  RENDER_DIR="$(mktemp -d)"
  render_unit > "${RENDER_DIR}/zoe-voice.service"
  check_rendered_unit "${RENDER_DIR}/zoe-voice.service" || exit 5
fi

if [ "$DRY_RUN" = 1 ]; then
  echo "==> DRY_RUN: would rsync ${SHIPPED_FILES[*]} -> ${SSH_TARGET}:${PI_DAEMON_DIR}/ (backup suffix ${BAK_SUFFIX})"
  if [ "$INSTALL_UNIT" = 1 ]; then
    echo "==> DRY_RUN: would install this unit (refuses if one exists unless --force-unit):"
    cat "${RENDER_DIR}/zoe-voice.service"
  fi
  echo "==> DRY_RUN: would write ${PI_DAEMON_DIR}/BUILD.json: ${BUILD_JSON}"
  echo "==> DRY_RUN: would restart zoe-voice and verify active/health/build/md5"
  exit 0
fi

# --- 1. Unit pre-flight: refuse BEFORE shipping anything --------------------
if [ "$INSTALL_UNIT" = 1 ]; then
  probe="$(rssh "if [ -e \"${REMOTE_UNIT}\" ]; then echo UNIT_EXISTS; else echo UNIT_ABSENT; fi")" || {
    echo "ERROR: could not reach ${SSH_TARGET} to check for an existing unit" >&2
    exit 5
  }
  case "$probe" in
    *UNIT_EXISTS*)
      if [ "$FORCE_UNIT" != 1 ]; then
        echo "REFUSED: a unit already exists at ${REMOTE_UNIT} on the Pi." >&2
        echo "         The live unit is never overwritten by default. Re-run with" >&2
        echo "         --install-unit --force-unit to replace it (a backup is kept)," >&2
        echo "         or drop --install-unit to just ship the daemon and restart." >&2
        exit 3
      fi
      echo "==> Existing unit will be replaced (backup: ${REMOTE_UNIT}${BAK_SUFFIX})"
      ;;
    *UNIT_ABSENT*) ;;
    *) echo "ERROR: unexpected unit probe answer: ${probe}" >&2; exit 5 ;;
  esac
fi

# --- 2. Ship the daemon files (explicit list; backups kept) -----------------
echo "==> Syncing daemon files..."
src_files=()
for f in "${SHIPPED_FILES[@]}"; do src_files+=("${SRC_DIR}/${f}"); done
rsync -avz --backup --suffix="${BAK_SUFFIX}" \
  "${src_files[@]}" \
  "${SSH_TARGET}:${PI_DAEMON_DIR}/" || { echo "ERROR: rsync failed" >&2; exit 5; }

# --- 2b. BUILD.json: what /health will report after the restart ---------------
rssh "printf '%s\\n' '${BUILD_JSON}' > ${PI_DAEMON_DIR}/BUILD.json.tmp && mv ${PI_DAEMON_DIR}/BUILD.json.tmp ${PI_DAEMON_DIR}/BUILD.json" \
  || { echo "ERROR: could not write BUILD.json on the Pi" >&2; exit 5; }

# --- 3. Python dependencies --------------------------------------------------
echo "==> Installing Python dependencies on Pi..."
rssh "cd ${PI_DAEMON_DIR} && { ${PI_VENV}/bin/pip install -r pi-requirements.txt --quiet || python3 -m pip install -r pi-requirements.txt --quiet; } && echo 'Dependencies installed.'" \
  || { echo "ERROR: dependency install failed" >&2; exit 5; }

# --- 4. Optional: install the RENDERED unit ---------------------------------
if [ "$INSTALL_UNIT" = 1 ]; then
  echo "==> Installing rendered user unit..."
  staged="${PI_DAEMON_DIR}/zoe-voice.service.rendered"
  scp "${SSH_OPTS[@]}" "${RENDER_DIR}/zoe-voice.service" "${SSH_TARGET}:${staged}" \
    || { echo "ERROR: scp of the rendered unit failed" >&2; exit 5; }
  rssh "mkdir -p \"${REMOTE_UNIT_DIR}\" && if [ -e \"${REMOTE_UNIT}\" ]; then cp -p \"${REMOTE_UNIT}\" \"${REMOTE_UNIT}${BAK_SUFFIX}\"; fi && mv \"${staged}\" \"${REMOTE_UNIT}\" && systemctl --user daemon-reload && systemctl --user enable zoe-voice" \
    || { echo "ERROR: unit install failed" >&2; print_rollback; exit 5; }
fi

# --- 5. Restart ---------------------------------------------------------------
echo "==> Restarting zoe-voice (user service)..."
rssh "systemctl --user restart zoe-voice" || fail_verify "systemctl --user restart zoe-voice failed"

# --- 6. Verify ---------------------------------------------------------------
echo "==> Verifying (timeout ${VERIFY_TIMEOUT_S}s)..."
if ! rssh "for i in \$(seq 1 ${VERIFY_TIMEOUT_S}); do [ \"\$(systemctl --user is-active zoe-voice)\" = active ] && exit 0; sleep 1; done; systemctl --user status zoe-voice --no-pager -n 15 || true; exit 1"; then
  fail_verify "systemctl --user is-active zoe-voice was not 'active' within ${VERIFY_TIMEOUT_S}s"
fi
echo "    unit active"

health_url="http://127.0.0.1:${HEALTH_PORT}/health"
if ! rssh "for i in \$(seq 1 ${VERIFY_TIMEOUT_S}); do (curl -fsS --max-time 3 ${health_url} >/dev/null 2>&1 || ${PI_VENV}/bin/python3 -c \"import urllib.request; urllib.request.urlopen('${health_url}', timeout=3)\" >/dev/null 2>&1) && exit 0; sleep 1; done; exit 1"; then
  fail_verify "${health_url} did not answer within ${VERIFY_TIMEOUT_S}s"
fi
echo "    /health answers"

# The daemon that answers must be the build just shipped (BUILD.json is read at start).
health_body="$(rssh "printf 'HEALTH_OK '; curl -fsS --max-time 3 ${health_url} 2>/dev/null || ${PI_VENV}/bin/python3 -c \"import sys, urllib.request; sys.stdout.write(urllib.request.urlopen('${health_url}', timeout=3).read().decode())\"" 2>/dev/null)" || health_body=""
case "$health_body" in
  *"\"build\": \"${BUILD_SHA}\""*) echo "    /health reports build ${BUILD_SHA}" ;;
  *) fail_verify "/health does not report build ${BUILD_SHA} - the daemon that answered is not the one just shipped" ;;
esac

# The speaker model must be READY when it is the acting one (ZOE_SPEAKER_MODEL=resnet34): /health answers even when the ResNet34
# embedder was refused (missing file, pin or self-test drift), so the deploy reads its state - bounded wait - and fails on not-ready.
# dual: ResNet34 is shadow-only, so not-ready is reported loudly but does not fail. FAIL-CLOSED: when the daemon's state cannot be
# read (ssh hiccup, helper import error, no SPEAKER_STATE line, daemon unreachable) the deploy passes ONLY if the Pi's configured
# ZOE_SPEAKER_MODEL (.env.voice) proves the legacy mode; a daemon that reports a different mode than the one configured also fails.
cfg_raw="$(rssh "printf 'CFG_OK '; grep -E '^[[:space:]]*(export[[:space:]]+)?ZOE_SPEAKER_MODEL=' ${PI_DAEMON_DIR}/.env.voice 2>/dev/null | tail -n 1 | cut -d= -f2-" 2>/dev/null)" || cfg_raw=""
case "$cfg_raw" in
  CFG_OK*)
    cfg_val="$(printf '%s' "${cfg_raw#CFG_OK}" | tr -d "\"' \t\r" | tr '[:upper:]' '[:lower:]')"
    case "$cfg_val" in dual|resnet34) cfg_mode="$cfg_val" ;; *) cfg_mode="resemblyzer" ;; esac ;;
  *) cfg_mode="unreadable" ;;
esac
speaker_state="$(rssh "cd ${PI_DAEMON_DIR} && ${PI_VENV}/bin/python3 zoe_speaker_resnet34.py --health-state ${health_url} ${RESNET34_READY_TIMEOUT_S}" 2>/dev/null | grep '^SPEAKER_STATE ' | tail -n 1)" || speaker_state=""
speaker_model=""; speaker_rn34=""
read -r _ speaker_model speaker_rn34 <<<"$speaker_state" || true
if [ -z "$speaker_state" ] || [ "$speaker_model" = "unknown" ]; then
  [ "$cfg_mode" = "resemblyzer" ] \
    || fail_verify "could not read the daemon's speaker-model state (configured ZOE_SPEAKER_MODEL: ${cfg_mode}) - cannot prove the speaker model runs"
  echo "    speaker model: resemblyzer (configured; the daemon's state was unreadable)"
elif [ "$cfg_mode" != "unreadable" ] && [ "$speaker_model" != "$cfg_mode" ]; then
  fail_verify "the daemon reports speaker model '${speaker_model}' but .env.voice configures '${cfg_mode}' - the restart did not pick up the configuration"
else
  case "$speaker_model" in
    resnet34)
      [ "$speaker_rn34" = "ready" ] \
        || fail_verify "ZOE_SPEAKER_MODEL=resnet34 but speaker_resnet34 is '${speaker_rn34}' after ${RESNET34_READY_TIMEOUT_S}s - the acting speaker model cannot run (fetch_speaker_models.sh verify)"
      echo "    speaker model resnet34: ready" ;;
    dual)
      if [ "$speaker_rn34" = "ready" ]; then echo "    speaker model dual: resnet34 ready (shadow)"
      else echo "    WARNING: speaker model dual, resnet34 is '${speaker_rn34}' - shadow scoring is OFF until fixed (Resemblyzer still acts)"; fi ;;
    *)
      echo "    speaker model: ${speaker_model}" ;;
  esac
fi

local_md5="$(cd "${SRC_DIR}" && md5sum "${SHIPPED_FILES[@]}" | sort -k2)"
remote_md5="$(rssh "cd ${PI_DAEMON_DIR} && md5sum ${SHIPPED_FILES[*]}" | sort -k2)" \
  || fail_verify "could not md5 the shipped files on the Pi"
if [ "$local_md5" != "$remote_md5" ]; then
  { echo "--- local"; echo "$local_md5"; echo "--- pi"; echo "$remote_md5"; } >&2
  fail_verify "md5 of the shipped files on the Pi differs from the local copies"
fi
echo "    md5 of ${#SHIPPED_FILES[@]} shipped files matches"

echo ""
echo "==> Deployment complete and verified."
echo "    Pi logs:  ssh ${SSH_TARGET} 'tail -f ${PI_DAEMON_DIR}/voice.log'  (journalctl -u zoe-voice, NOT --user)"
echo "    Backups:  ${PI_DAEMON_DIR}/*${BAK_SUFFIX}"
