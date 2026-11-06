#!/usr/bin/env bash
# bar_baseline_tonight.sh - the guarded, evening-slot launcher for the FULL clean Samantha-bar baseline (Initiative section 9:
# "23 cells baselined; 0 regressions"; the bar now scores 29 scenarios, see docs/knowledge/samantha-bar.md "Continuity cells").
#
# It runs NOTHING itself. It checks that the brain is free, then hands over to record_bench_baselines.sh (voice self-test, the full
# bar at --samples 3, the day-sim, the person bench - each under /tmp/zoe-voice-harness.lock, each with its own gates, each REFUSING
# rather than recording a bad bar). Nothing here schedules anything persistent: you start it by hand, once.
#
# Starts only when ALL hold (any other state exits non-zero and says which):
#   * it is inside the evening slot: 22:00 up to 01:05 local (record_bench_baselines.sh needs >= 45 min before the 01:50 night
#     window and refuses the start otherwise; the 07:00-22:00 household hours and 01:50-04:55 are outside it by construction)
#   * night_busy.sh voice  exits NON-zero (no pause: household asleep, night window / voice replay not running)
#   * night_busy.sh test   exits NON-zero (night window / backup not running)
#   * no land_voice_pr.sh process is alive and /tmp/zoe-brain-window.lock is not held
#   * the live checkout is clean (a baseline is commit-bound and the harness refuses a dirty tree: say so BEFORE the evening is spent)
#
#     bash scripts/maintenance/bar_baseline_tonight.sh --check      # print the verdict and the command; run nothing (exit 0 = could start now)
#     bash scripts/maintenance/bar_baseline_tonight.sh              # check, then run record_bench_baselines.sh (extra args pass through)
# Exit: 0 ran clean (or --check says go) | 1 the baseline run failed | 3 not now (reason on stderr) | 4 live checkout dirty/unreadable
# Test hooks (paths and the clock only; no gate can be skipped): ZOE_BASELINE_NOW_EPOCH, ZOE_BASELINE_NIGHT_BUSY,
# ZOE_BASELINE_RECORD, ZOE_BASELINE_BRAIN_LOCK, ZOE_BASELINE_PGREP, ZOE_LANDING_REPO.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NIGHT_BUSY="${ZOE_BASELINE_NIGHT_BUSY:-$REPO/scripts/landing/night_busy.sh}"
RECORD="${ZOE_BASELINE_RECORD:-$REPO/scripts/maintenance/record_bench_baselines.sh}"
BRAIN_LOCK="${ZOE_BASELINE_BRAIN_LOCK:-/tmp/zoe-brain-window.lock}"
PGREP="${ZOE_BASELINE_PGREP:-pgrep}"
LIVE="${ZOE_LANDING_REPO:-/home/zoe/assistant}"
now="${ZOE_BASELINE_NOW_EPOCH:-$(date +%s)}"
hm="$(date -d "@$now" +%H%M)"
check_only=0
args=()
for a in "$@"; do case "$a" in --check) check_only=1 ;; *) args+=("$a") ;; esac; done

no() { echo "bar_baseline_tonight: not now - $1" >&2; exit 3; }

# 1. the evening slot (10#: hm is zero-padded, never read it as octal)
if ! { [ "$((10#$hm))" -ge 2200 ] || [ "$((10#$hm))" -lt 105 ]; }; then no "$hm is outside the 22:00-01:05 evening slot"; fi

# 2. the measured night pause: BOTH must say "may run" (non-zero). Exit 0 from night_busy means PAUSE.
export ZOE_LANDING_NOW_EPOCH="$now"
if bash "$NIGHT_BUSY" voice; then no "night_busy.sh voice says pause (household hours, night window, or the nightly voice replay)"; fi
if bash "$NIGHT_BUSY" test;  then no "night_busy.sh test says pause (night window or backup running)"; fi

# 3. no voice lander, no brain-window lock holder
if "$PGREP" -f land_voice_pr.sh >/dev/null 2>&1; then no "a land_voice_pr.sh process is alive"; fi
if [ -e "$BRAIN_LOCK" ]; then
  if ! ( exec 8>"$BRAIN_LOCK" && flock -n 8 ); then no "$BRAIN_LOCK is held"; fi
fi

# 4. a clean live checkout (the harness refuses a record on a dirty or unverifiable tree - find out before spending the slot)
dirty="$(git -C "$LIVE" status --porcelain 2>/dev/null)" || { echo "bar_baseline_tonight: cannot read $LIVE git status" >&2; exit 4; }
if [ -n "$dirty" ]; then
  echo "bar_baseline_tonight: the live checkout $LIVE is not clean ($(printf '%s\n' "$dirty" | wc -l) path(s)); a baseline is commit-bound - clean it first" >&2
  exit 4
fi

echo "bar_baseline_tonight: clear to run at $hm: bash $RECORD ${args[*]:-}"
[ "$check_only" = 1 ] && exit 0
exec bash "$RECORD" "${args[@]}"
