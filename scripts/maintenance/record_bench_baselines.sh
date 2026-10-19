#!/usr/bin/env bash
# record_bench_baselines.sh - the Phase 0 (self-evolution) instrument run, T-3 + T-4.
#
# One sequential night run, each step under the shared harness lock:
#   1. voice_regression_probe.py --self-test   (T-3: the negative control must go RED on
#      every injected fault; writes ~/.cache/zoe/voice_probe_selftest.json, never the
#      shared voice-gate artifact)
#   2. samantha_bar.py --record-baseline --samples 3   (all scenarios, clean tree)
#   3. samantha_day_sim.py --record-baseline           (default mode)
#   4. samantha_person.py --record-baseline            (arm none, full run)
#
# Every harness enforces its own gates and REFUSES rather than recording a bad bar:
# harness quiet hours (07:00-22:00 local, harness_quiet.py) and a panel used in the last
# 10 min; the 01:45-03:15 nightly window (the 12B owns the box 02:00-04:10); a deploy in
# flight; memory headroom; and, for a baseline, a DIRTY or unverifiable live checkout, a
# partial run, an errored cell or an unproven teardown. Good windows: 22:00-01:15 and
# 04:45-06:45. ~20-30 min end to end.
#
# Run (or schedule once, e.g. `systemd-run --user --on-calendar='YYYY-MM-DD 22:30'
# --unit zoe-phase0-baselines bash <repo>/scripts/maintenance/record_bench_baselines.sh`):
#     bash scripts/maintenance/record_bench_baselines.sh [--no-selftest] [--no-bar] [--no-day-sim] [--no-person]
# Exit 0 = every step ran clean; 1 = a step refused or failed (its own output says why);
# 3 = refused up front by the quiet hours.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK=/tmp/zoe-voice-harness.lock
LOG_DIR="${HOME}/.zoe-logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/phase0-baselines.log"
export ZOE_PERF=1

selftest=1 bar=1 daysim=1 person=1
for a in "$@"; do
  case "$a" in
    --no-selftest) selftest=0 ;;
    --no-bar) bar=0 ;;
    --no-day-sim) daysim=0 ;;
    --no-person) person=0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

if ! python3 "$REPO/scripts/maintenance/harness_quiet.py" check record_bench_baselines; then
  exit 3
fi

rc=0
set -o pipefail   # a step's exit code, not tee's
step() {
  echo "== $(date -Is) $*" | tee -a "$LOG"
  flock -w 1800 "$LOCK" nice -n 5 "$@" 2>&1 | tee -a "$LOG" || rc=1
}

[ "$selftest" = 1 ] && step python3 "$REPO/scripts/maintenance/voice_regression_probe.py" --self-test
[ "$bar" = 1 ] && step python3 "$REPO/scripts/perf/samantha_bar.py" --record-baseline --samples 3
[ "$daysim" = 1 ] && step python3 "$REPO/scripts/perf/samantha_day_sim.py" --record-baseline
[ "$person" = 1 ] && step python3 "$REPO/scripts/perf/samantha_person.py" --record-baseline
echo "== $(date -Is) done rc=$rc (log: $LOG)" | tee -a "$LOG"
exit "$rc"
