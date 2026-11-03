#!/usr/bin/env bash
# wait_household_quiet.sh - hold a zoe-data restart until the household is quiet.
#
# Owner 2026-11-02: "she is there when spoken to" - a merged PR used to restart zoe-data
# the moment it landed, even mid-conversation at the panel. Called by
# .github/workflows/deploy.yml (step before the zoe-data restart) and by
# scripts/maintenance/deploy_live.sh (before it takes the deploy lock).
#
# Signal: GET /health on zoe-data carries `last_voice_turn_age_s` - seconds since the newest
# panel voice turn (null = none since the process started). Read-only, no household data.
#
# Contract:
#   * docs-only deploy (DEPLOY_BASE_SHA..HEAD touches only docs/ or *.md) -> exit 0 at once.
#     Same definition as the restart step's own skip; unknown base = may restart = wait.
#   * a turn within ZOE_DEPLOY_QUIET_MIN minutes (default 10) -> poll until quiet.
#   * FAIL OPEN: after ZOE_DEPLOY_QUIET_MAX_WAIT_MIN (default 60) the deploy proceeds, logged;
#     a signal that cannot be read for ZOE_DEPLOY_QUIET_UNREADABLE_S (default 60) proceeds too.
#     A stuck signal can never block a deploy forever.
#   * the ONE fail-closed case is the night window: while `scripts/landing/night_busy.sh deploy`
#     says busy, never proceed (exit 1 after ZOE_DEPLOY_NIGHT_WAIT_MAX_MIN, default 90 - the
#     window ends by ~03:30 at the latest). Nothing has been restarted, a re-run is safe.
#   * a voice replay probe / landing in flight (ZOE_DEPLOY_PROBE_LOCKS, default the voice-harness and
#     brain-window flock files, either currently HELD) -> wait too: restarting zoe-data mid-probe
#     fails the probe. Never creates or holds a lock (an absent file is "not held"). Bounded by
#     ZOE_DEPLOY_PROBE_WAIT_MAX_MIN (default 45, total across the run), then FAILS OPEN, logged.
# Env: ZOE_DEPLOY_QUIET_WAIT=0 disables. ZOE_DEPLOY_QUIET_POLL_S (30). ZOE_PORT (8000).
# Test hooks: ZOE_DEPLOY_HEALTH_URL (any curl URL, file:// ok), ZOE_NIGHT_BUSY_SCRIPT, ZOE_DEPLOY_PROBE_LOCKS (space-separated lock paths).
# Elapsed time is counted in poll intervals (not the clock) so a `sleep` shim makes the
# bounds testable.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
num() { case "$2" in ''|*[!0-9]*) echo "$1" ;; *) echo "$2" ;; esac; }
ENABLED="${ZOE_DEPLOY_QUIET_WAIT:-1}"
QUIET_S=$(( $(num 10 "${ZOE_DEPLOY_QUIET_MIN:-10}") * 60 ))
MAX_S=$(( $(num 60 "${ZOE_DEPLOY_QUIET_MAX_WAIT_MIN:-60}") * 60 ))
NIGHT_MAX_S=$(( $(num 90 "${ZOE_DEPLOY_NIGHT_WAIT_MAX_MIN:-90}") * 60 ))
PROBE_MAX_S=$(( $(num 45 "${ZOE_DEPLOY_PROBE_WAIT_MAX_MIN:-45}") * 60 ))
PROBE_LOCKS="${ZOE_DEPLOY_PROBE_LOCKS:-/tmp/zoe-voice-harness.lock /tmp/zoe-brain-window.lock}"
POLL_S="$(num 30 "${ZOE_DEPLOY_QUIET_POLL_S:-30}")"; [ "$POLL_S" -ge 1 ] || POLL_S=1
UNREADABLE_S="$(num 60 "${ZOE_DEPLOY_QUIET_UNREADABLE_S:-60}")"
URL="${ZOE_DEPLOY_HEALTH_URL:-http://127.0.0.1:${ZOE_PORT:-8000}/health}"
NIGHT_BUSY="${ZOE_NIGHT_BUSY_SCRIPT:-${SCRIPT_DIR}/../landing/night_busy.sh}"
log() { echo "wait_household_quiet: $*"; }

if [ "$ENABLED" = "0" ]; then log "disabled (ZOE_DEPLOY_QUIET_WAIT=0): proceeding."; exit 0; fi

# --- docs-only deploys do not restart zoe-data: no wait ---------------------------------
TARGET="${DEPLOY_TARGET_SHA:-HEAD}"   # deploy_live.sh's post-merge recheck passes the new tip explicitly
if [ -n "${DEPLOY_BASE_SHA:-}" ] && git cat-file -e "${DEPLOY_BASE_SHA}^{commit}" 2>/dev/null \
   && [ "${DEPLOY_BASE_SHA}" != "$(git rev-parse "$TARGET" 2>/dev/null)" ]; then
  if CHANGED="$(git diff --name-only "${DEPLOY_BASE_SHA}" "$TARGET" 2>/dev/null)" && [ -n "${CHANGED}" ] \
     && [ -z "$(printf '%s\n' "${CHANGED}" | grep -vE '^docs/|\.md$' || true)" ]; then
    log "docs-only deploy since ${DEPLOY_BASE_SHA}: nothing restarts, not waiting."
    exit 0
  fi
fi

# --- the night window: the one thing that is never failed open --------------------------
# Re-checked by proceed() immediately before EVERY exit-0 below: the household wait can run an
# hour, and a window that opens during it must still hold the restart (review on #2234).
night_busy() { [ -f "$NIGHT_BUSY" ] && bash "$NIGHT_BUSY" deploy >/dev/null 2>&1; }
NIGHT_WAITED=0
night_gate() {
  local nw=0
  while night_busy; do
    if [ "$nw" -ge "$NIGHT_MAX_S" ]; then
      echo "::error::night window rules (night_busy.sh deploy) still say busy after $(( NIGHT_MAX_S / 60 )) min: not restarting. Nothing was restarted; re-run this deploy after the window."
      exit 1
    fi
    log "night window rules say a deploy must pause; waiting ${POLL_S}s ($(( nw / 60 )) min so far)."
    NIGHT_WAITED=1
    sleep "$POLL_S"; nw=$(( nw + POLL_S ))
  done
}
# --- a voice replay probe in flight: never restart zoe-data under it ----------------------
# Prints the first HELD lock path. `flock -n` would CREATE a missing file, so only an existing,
# readable file is tested; it is opened and released at once (never held).
probe_held() {
  local l
  for l in $PROBE_LOCKS; do
    [ -f "$l" ] && [ -r "$l" ] || continue
    if ! flock -n "$l" true 2>/dev/null; then echo "$l"; return 0; fi
  done
  return 1
}
PROBE_WAITED_S=0
PROBE_WAITED=0
# Waits while a probe holds a lock, within the total PROBE_MAX_S budget (then fails open, logged).
probe_gate() {
  local held
  PROBE_WAITED=0
  while held="$(probe_held)"; do
    if [ "$PROBE_WAITED_S" -ge "$PROBE_MAX_S" ]; then
      log "WARNING: voice probe lock ${held} still held after $(( PROBE_MAX_S / 60 )) min of waiting; failing open, deploying anyway."
      return 0
    fi
    log "voice replay probe in flight (${held} is held): not restarting zoe-data under it; waiting ${POLL_S}s (${PROBE_WAITED_S}s of ${PROBE_MAX_S}s)."
    PROBE_WAITED=1
    sleep "$POLL_S"; PROBE_WAITED_S=$(( PROBE_WAITED_S + POLL_S ))
  done
}
# exit 0 when clear. If a night window had to be waited out, the household reading that led here is
# stale (services were restored after it; a conversation may have started): return 1 so the caller
# re-polls instead of exiting on the old verdict (review on #2234).
proceed() {
  NIGHT_WAITED=0; night_gate
  if [ "$NIGHT_WAITED" = 1 ]; then log "waited out a night window: re-checking household activity."; return 1; fi
  probe_gate
  if [ "$PROBE_WAITED" = 1 ]; then
    # the probe may have ended inside a night window or a conversation: re-check both from the top
    log "a voice probe finished: re-checking night window and household activity."
    return 1
  fi
  exit 0
}
night_gate

# --- household quiet --------------------------------------------------------------------
# prints the age in seconds (float or "none"), or "unreadable"
read_age() {
  local body
  body="$(curl -fsS -m 5 "$URL" 2>/dev/null)" || { echo unreadable; return; }
  printf '%s' "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unreadable"); raise SystemExit
if "last_voice_turn_age_s" not in d:
    print("unreadable")
else:
    v = d["last_voice_turn_age_s"]
    print("none" if v is None else float(v))
' 2>/dev/null || echo unreadable
}

waited=0
unreadable=0
while :; do
  was_unreadable=0
  age="$(read_age)"
  case "$age" in
    unreadable)
      # elapsed unreadable time is credited AFTER the sleep below, so UNREADABLE_S is a real wait
      if [ "$unreadable" -ge "$UNREADABLE_S" ]; then
        log "WARNING: last-voice-turn signal unreadable for ${unreadable}s (${URL}); failing open, deploying."
        proceed || { unreadable=0; waited=0; continue; }
      fi
      was_unreadable=1
      log "signal unreadable (${unreadable}s of ${UNREADABLE_S}s tolerated)." ;;
    none)
      log "no panel voice turn since zoe-data started: quiet, deploying."; proceed || { unreadable=0; waited=0; continue; } ;;
    *)
      unreadable=0
      if awk -v a="$age" -v q="$QUIET_S" 'BEGIN{exit !(a+0 >= q+0)}'; then
        log "last panel voice turn ${age}s ago (>= $(( QUIET_S / 60 )) min): quiet, deploying (waited $(( waited / 60 )) min)."
        proceed || { waited=0; continue; }
      fi
      log "last panel voice turn ${age}s ago (< $(( QUIET_S / 60 )) min): household not quiet; waiting ${POLL_S}s (${waited}s of ${MAX_S}s)." ;;
  esac
  if [ "$waited" -ge "$MAX_S" ]; then
    log "WARNING: household still not quiet after $(( MAX_S / 60 )) min (last signal: ${age}); failing open, deploying anyway."
    proceed || { waited=0; continue; }
  fi
  sleep "$POLL_S"; waited=$(( waited + POLL_S ))
  if [ "$was_unreadable" = 1 ]; then unreadable=$(( unreadable + POLL_S )); fi
done
