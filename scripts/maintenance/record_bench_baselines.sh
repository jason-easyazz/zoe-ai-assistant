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
# partial run, an errored cell or an unproven teardown.
#
# THIS orchestrator also owns the brain window (binding, scripts/AGENTS.md): it holds the
# shared harness lock across several steps, so it refuses to START 01:50-04:55 (the 12B night
# window, the 04:10-04:52 voice gate and the 04:30 nightly regression own the box) and refuses
# a start with less than ZOE_BASELINES_RUN_MIN (default 45) minutes before 01:50; ZOE_HARNESS_FORCE
# does NOT bypass it. It re-checks before every step too (a step can sit behind the lock for
# up to 30 min). Good windows: 22:00-01:05 and 05:00-06:45. ~20-30 min end to end.
#
# Run (or schedule once, e.g. `systemd-run --user --on-calendar='YYYY-MM-DD 22:30'
# --unit zoe-phase0-baselines bash <repo>/scripts/maintenance/record_bench_baselines.sh`):
#     bash scripts/maintenance/record_bench_baselines.sh [--no-selftest] [--no-bar] [--no-day-sim] [--no-person]
# Exit 0 = every step ran clean; 1 = a step refused or failed (its own output says why);
# 3 = refused up front by the quiet hours or the brain window.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK="${ZOE_HARNESS_LOCK:-/tmp/zoe-voice-harness.lock}"   # override: tests only
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

RUN_MIN="${ZOE_BASELINES_RUN_MIN:-45}"
STEP_MIN="${ZOE_BASELINES_STEP_MIN:-20}"
if ! python3 "$REPO/scripts/maintenance/harness_quiet.py" check record_bench_baselines; then
  exit 3
fi
if ! python3 "$REPO/scripts/maintenance/harness_quiet.py" brain-window record_bench_baselines --budget-min "$RUN_MIN"; then
  exit 3
fi

rc=0
set -o pipefail   # a step's exit code, not tee's
step() {
  echo "== $(date -Is) $*" | tee -a "$LOG"
  # The brain-window re-check runs INSIDE the lock, after any wait for it (a step can sit behind the lock for up to
  # 30 min), so the step only starts if it can still finish before 01:50 / starts after 04:55. Exit 3 = refused.
  flock -w 1800 "$LOCK" bash -c 'python3 "$1" brain-window record_bench_baselines --budget-min "$2" || exit 3
shift 2; exec nice -n 5 "$@"' _ "$REPO/scripts/maintenance/harness_quiet.py" "$STEP_MIN" "$@" 2>&1 | tee -a "$LOG" || rc=1
}

[ "$selftest" = 1 ] && step python3 "$REPO/scripts/maintenance/voice_regression_probe.py" --self-test
[ "$bar" = 1 ] && step python3 "$REPO/scripts/perf/samantha_bar.py" --record-baseline --samples 3
[ "$daysim" = 1 ] && step python3 "$REPO/scripts/perf/samantha_day_sim.py" --record-baseline
[ "$person" = 1 ] && step python3 "$REPO/scripts/perf/samantha_person.py" --record-baseline
echo "== $(date -Is) done rc=$rc (log: $LOG)" | tee -a "$LOG"
exit "$rc"
