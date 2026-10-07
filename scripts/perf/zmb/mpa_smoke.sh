#!/bin/bash
# mpa_smoke.sh - the <= 30 model call smoke of the agent-operated MemPalace arm against the LIVE brain (llama-server on :11434), under every guard the bake-off window uses.
#
#   scripts/perf/zmb/mpa_smoke.sh OUT.json [MAX_CALLS=30] [WAIT_MIN=60]
#
# It runs ten synthetic household turns (no real household text) with the brain calling MemPalace's tools, and records tool-call validity, how often the brain searches before it
# answers, whether it supersedes correctly, the MemPalace server's RSS and the prompt-token cost. What it refuses, and checks before EVERY start:
#   - /tmp/zoe-brain-window.lock must be free (taken for the whole run with flock: a landing, the samantha bar or a bake-off window owns the brain otherwise)
#   - the voice harness lock /tmp/zoe-voice-harness.lock must be free, and no anchored match of a voice-PR landing, the samantha bar or the voice regression probe is running
#     (the same anchored patterns as bakeoff.py: a bare `pgrep -f samantha_bar.py` matches editors and tail -f)
#   - the panel has had no voice turn for 10 minutes (ssh zoe-pi, same grep as the window; an unreachable panel REFUSES here, the smoke is not a window)
#   - MemAvailable >= 1700 MB before the start (the arm adds about 170 MB; the floor is 1500)
# While it runs the driver stops when the panel hears a wake word (PanelGuard) or MemAvailable drops under 1500 MB (the guard below kills the server). It starts a ~10 MB loopback
# stub embedder inside the driver (no model, no network), and stops EVERYTHING it started on every exit path. It does not stop, restart or reconfigure any service.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?usage: mpa_smoke.sh OUT.json [MAX_CALLS] [WAIT_MIN]}
MAX=${2:-30}
WAIT_MIN=${3:-60}
PY=${MPA_PY:-/usr/bin/python3}
LOCK=${BAKEOFF_LOCK:-/tmp/zoe-brain-window.lock}
HLOCK=${BAKEOFF_HARNESS_LOCK:-/tmp/zoe-voice-harness.lock}
PANEL=${BAKEOFF_PANEL_HOST:-zoe-pi}
QUIET=${BAKEOFF_QUIET_S:-600}
BRAIN=${MPA_BRAIN_URL:-http://127.0.0.1:11434}
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1 ORT_DISABLE_TELEMETRY=1

avail() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }

busy() {
  for pat in '^bash .*/land_voice_pr\.sh' '^\S*python\S*( -\S+)* \S*(samantha_bar|samantha_bar_conv|samantha_day_sim)\.py' '^\S*python\S*( -\S+)* \S*voice_regression_probe\.py'; do
    if pgrep -f "$pat" >/dev/null 2>&1; then echo "busy: a process matches $pat"; return 0; fi
  done
  if [ -e "$HLOCK" ] && ! flock -n -s "$HLOCK" true 2>/dev/null; then echo "busy: the voice harness lock $HLOCK is held"; return 0; fi
  return 1
}

panel_quiet() {
  local last age
  last=$(ssh -o BatchMode=yes -o ConnectTimeout=6 "$PANEL" "grep -a 'Wake word detected\|Follow-up speech detected' /home/pi/.zoe-voice/voice.log | tail -1 | cut -c1-19" 2>/dev/null) || { echo "the panel is unreachable: cannot show it is quiet"; return 1; }
  [ -n "$last" ] || return 0
  age=$(( $(date +%s) - $(date -d "$last" +%s 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$QUIET" ]; then echo "the panel had a voice turn ${age}s ago (needs ${QUIET}s quiet)"; return 1; fi
  return 0
}

exec 9>"$LOCK"
deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
while true; do
  why=""
  if ! flock -n 9; then why="$LOCK is held (a landing, the samantha bar or a bake-off window owns the brain)"
  elif b=$(busy); then why="$b"; flock -u 9
  elif ! p=$(panel_quiet); then why="$p"; flock -u 9
  elif [ "$(avail)" -lt 1700 ]; then why="MemAvailable $(avail) MB < 1700 MB"; flock -u 9
  else break; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then echo "REFUSED after ${WAIT_MIN} min: $why" >&2; exit 2; fi
  echo "waiting: $why"; sleep 30
done
echo "smoke: lock held, brain free, panel quiet, MemAvailable $(avail) MB; at most $MAX model calls against $BRAIN"
if ! curl -sf -m 3 "$BRAIN/health" >/dev/null; then echo "the brain on $BRAIN does not answer /health" >&2; exit 3; fi

( while sleep 0.4; do a=$(avail); if [ "$a" -lt 1500 ]; then echo "GUARD: MemAvailable $a < 1500 MB: stopping the smoke's MemPalace server" >&2; pkill -f "mempalace-mcp --palace .*zmb-mpa-" ; fi; done ) &
GUARD=$!
cleanup() { kill "$GUARD" 2>/dev/null; pkill -P $$ -f "mpa_window.py" 2>/dev/null; flock -u 9 2>/dev/null; }
trap cleanup EXIT INT TERM

"$PY" "$HERE/mpa_window.py" --arm MPA --smoke-live --stub-embedder --max-calls "$MAX" --clone-url "$BRAIN" --quiet-since "$(date +%s)" --out "$OUT"
rc=$?
echo "smoke finished (rc=$rc); MemAvailable $(avail) MB"
exit $rc
