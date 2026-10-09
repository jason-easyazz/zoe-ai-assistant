#!/bin/bash
# z0n_smoke.sh - the <= 20 model call smoke of the NIGHT MIND against the LIVE brain (llama-server on :11434), under the guards mpa_smoke.sh uses.
#
#   scripts/perf/zmb/z0n_smoke.sh OUT.json [MAX_CALLS=20] [WAIT_MIN=30]
#
# It runs the night pass over the bench's synthetic household (no real household text; demo ids; scratch stores - nothing of the household's is read or written) with the live
# 4B at its 8k slot and records: how many calls, the prompt-token peak, whether the replies parsed, the quote-verification rate (the share of moments whose quote is a span of the
# turn), the K cells it reached, and the MEASURED DECODE RATE of this brain (completion tokens per second of call time, prefill taken off) - the 8-vs-33 tok/s question.
# Refuses (exit 2 after WAIT_MIN) unless: /tmp/zoe-brain-window.lock is free and held for the run, the voice harness lock is free and no landing / bar / probe runs, the panel has had no
# voice turn for 10 minutes, and MemAvailable >= 2000 MB. While it runs it stops when MemAvailable drops under 1500 MB. It does not stop, restart or reconfigure any service.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?usage: z0n_smoke.sh OUT.json [MAX_CALLS] [WAIT_MIN]}
MAX=${2:-20}
WAIT_MIN=${3:-30}
PY=${Z0N_PY:-/usr/bin/python3}
LOCK=${BAKEOFF_LOCK:-/tmp/zoe-brain-window.lock}
HLOCK=${BAKEOFF_HARNESS_LOCK:-/tmp/zoe-voice-harness.lock}
PANEL=${BAKEOFF_PANEL_HOST:-zoe-pi}
QUIET=${BAKEOFF_QUIET_S:-600}
BRAIN=${Z0N_BRAIN_URL:-http://127.0.0.1:11434}
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
  elif [ "$(avail)" -lt 2000 ]; then why="MemAvailable $(avail) MB < 2000 MB"; flock -u 9
  else break; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then echo "REFUSED after ${WAIT_MIN} min: $why" >&2; exit 2; fi
  echo "waiting: $why"; sleep 30
done
echo "smoke: lock held, brain free, panel quiet, MemAvailable $(avail) MB; at most $MAX model calls against $BRAIN"
if ! curl -sf -m 3 "$BRAIN/health" >/dev/null; then echo "the brain on $BRAIN does not answer /health" >&2; exit 3; fi

( while sleep 0.4; do a=$(avail); if [ "$a" -lt 1500 ]; then echo "GUARD: MemAvailable $a < 1500 MB: stopping the smoke" >&2; pkill -f "z0n_window.py"; fi; done ) &
GUARD=$!
cleanup() { kill "$GUARD" 2>/dev/null; pkill -P $$ -f "z0n_window.py" 2>/dev/null; flock -u 9 2>/dev/null; }
trap cleanup EXIT INT TERM

"$PY" "$HERE/z0n_window.py" --clone-url "$BRAIN" --smoke-live --max-calls "$MAX" --ctx 8192 --no-brain --quiet-since "$(date +%s)" --out "$OUT"
rc=$?
echo "smoke finished (rc=$rc); MemAvailable $(avail) MB"
exit $rc
