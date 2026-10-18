#!/usr/bin/env bash
# samantha_weekly.sh - the weekly, COMMIT-BOUND Samantha evidence run (deep dive B8).
#
# Why: every full bar run in the trend was `dirty: true` (it ran from the live checkout), so no score was
# attributable to a commit - the defect the voice gate fixed with --expect-revision. This runs the bar and the
# day-sim from a CLEAN checkout of the commit the live service is on, with --require-bound, so a result lands in
# the trend only if (a) the live service's tracked files equal that commit, (b) the harness checkout is clean, and
# (c) the harness tree == the service tree (see samantha_bar.evidence_binding). It records the prompt-size column
# (the recall packet the brain would get) next to every verdict.
#
# Never touches the live checkout: the clean checkout is a `git clone --shared` (reads objects, writes nothing
# there). Nothing is written to the live DB except the harness's own throwaway demo_bar_* users, torn down and
# asserted by the harness itself.
#
# When: AFTER the night window (the 12B owns the box ~02:50-04:10, the daily voice probe runs 04:30). The script
# refuses to start inside 01:50-04:55 local. The shipped timer fires Sunday 05:30.
#
# Usage (operator / the timer; ZOE_PERF is set here because starting this is the explicit opt-in):
#     scripts/maintenance/samantha_weekly.sh                 # bar + day-sim
#     scripts/maintenance/samantha_weekly.sh --with-person   # also the person bench (long; ~1h)
#     scripts/maintenance/samantha_weekly.sh --dry-run       # print the plan: commit, paths, steps; run nothing
#     scripts/maintenance/samantha_weekly.sh --record-baseline   # the bar becomes the baseline (bound runs only)
#
# Exit: 0 all steps ran | 1 a regression vs the baseline (a Python crash also exits 1: read the log) |
#       2 refused/error (incl. unbound evidence) | 3 the shared harness lock was held | 4 not allowed to run now
#       (inside the night window).
# Template unit (NOT installed): scripts/setup/systemd/zoe-samantha-weekly.{service,timer}.
set -uo pipefail

LIVE="${ZOE_ASSISTANT_ROOT:-$HOME/assistant}"
CACHE="$HOME/.cache/zoe"   # fixed: the harnesses write here and do not honour an override
LOCK=/tmp/zoe-voice-harness.lock
WITH_PERSON=0
DRY=0
RECORD=0
for a in "$@"; do
  case "$a" in
    --with-person) WITH_PERSON=1 ;;
    --dry-run) DRY=1 ;;
    --record-baseline) RECORD=1 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "samantha_weekly: unknown argument $a" >&2; exit 2 ;;
  esac
done

# The night window: never run tests/evidence inside 01:50-04:55 local (the 12B and the dreaming jobs own the box).
# Checked before EVERY step too: a step can wait up to 2 h for the shared lock and must not start inside the window.
WIN_START_MIN=$((1 * 60 + 50))
WIN_END_MIN=$((4 * 60 + 55))
in_night_window() {
  local m=$((10#$(date +%H) * 60 + 10#$(date +%M)))
  [ "$m" -ge "$WIN_START_MIN" ] && [ "$m" -lt "$WIN_END_MIN" ]
}
if [ "$DRY" -eq 0 ] && in_night_window; then
  echo "samantha_weekly: inside the night window (01:50-04:55) - not starting" >&2
  exit 4
fi

COMMIT="$(git -C "$LIVE" rev-parse HEAD 2>/dev/null)" || { echo "samantha_weekly: $LIVE is not a git checkout" >&2; exit 2; }

STEPS=("scripts/perf/samantha_bar.py" "scripts/perf/samantha_day_sim.py")
[ "$WITH_PERSON" -eq 1 ] && STEPS+=("scripts/perf/samantha_person.py")

if [ "$DRY" -eq 1 ]; then
  echo "samantha_weekly: dry run"
  echo "  live service checkout : $LIVE"
  echo "  commit under test     : $COMMIT"
  echo "  harness               : a clean 'git clone --shared' of that commit, removed afterwards"
  for s in "${STEPS[@]}"; do echo "  step                  : $s --require-bound --service-dir $LIVE"; done
  echo "  trend (bound only)    : $CACHE/samantha_*_trend.jsonl   (unbound -> *.unbound.jsonl)"
  exit 0
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/samantha-weekly.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --shared --no-checkout "$LIVE" "$WORK/repo" || { echo "samantha_weekly: clone failed" >&2; exit 2; }
git -C "$WORK/repo" checkout --quiet --detach "$COMMIT" || { echo "samantha_weekly: checkout of $COMMIT failed" >&2; exit 2; }
echo "samantha_weekly: harness checkout $WORK/repo at ${COMMIT:0:12} (service: $LIVE)"

export ZOE_PERF=1
STARTED="$(date +%s)"   # the summary only reports artifacts written by THIS run (a step that exits 3 writes nothing)
worst=0
for s in "${STEPS[@]}"; do
  extra=()
  case "$s" in
    *samantha_bar.py)
      if [ "$RECORD" -eq 1 ]; then extra+=(--record-baseline)
      elif [ -f "$CACHE/samantha_bar_baseline.json" ]; then extra+=(--compare-baseline); fi ;;
  esac
  if in_night_window; then
    echo "samantha_weekly: the night window opened before $s - stopping" >&2
    [ 4 -gt "$worst" ] && worst=4
    break
  fi
  echo "samantha_weekly: === $s ==="
  # The lock wait can span the start of the window, so the window is re-checked AFTER the lock is held, in the
  # command flock runs (exit 4 = refused). `exec` keeps flock as the harness's parent, which is how the harness
  # recognises it is already serialised by this wrapper's lock.
  flock -E 3 -w 7200 "$LOCK" bash -c '
    m=$((10#$(date +%H) * 60 + 10#$(date +%M)))
    if [ "$m" -ge "$1" ] && [ "$m" -lt "$2" ]; then
      echo "samantha_weekly: the night window opened while waiting for the lock - not starting" >&2
      exit 4
    fi
    shift 2
    exec nice -n 5 python3 "$@"' _ "$WIN_START_MIN" "$WIN_END_MIN" "$WORK/repo/$s" --require-bound --service-dir "$LIVE" ${extra[@]+"${extra[@]}"}
  rc=$?
  echo "samantha_weekly: $s exit $rc"
  [ "$rc" -gt "$worst" ] && worst=$rc
done

# One summary line a human (or the next agent) can read without opening three artifacts.
python3 - "$COMMIT" "$CACHE" "$worst" "$STARTED" <<'PY'
import json, sys, time
from pathlib import Path
commit, cache, worst, started = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "commit": commit, "worst_exit": worst, "runs": {}}
for name, f in (("bar", "samantha_bar_last.json"), ("day_sim", "samantha_day_sim_last.json"), ("person", "samantha_person_last.json")):
    try:
        if (cache / f).stat().st_mtime < started:
            continue   # left over from an earlier run: not this run's result
        d = json.loads((cache / f).read_text())
    except (OSError, ValueError):
        continue
    ev = d.get("evidence") or {}
    ps = d.get("prompt_size") or {}
    out["runs"][name] = {"status": d.get("status"), "bound": ev.get("bound"), "commit": (ev.get("commit") or "")[:12],
                         "unbound_reason": ev.get("reason"), "prompt_chars_p95": ps.get("chars_p95"),
                         "prompt_packets": ps.get("packets")}
(cache / "samantha_weekly_last.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
print("samantha_weekly: " + json.dumps(out["runs"], sort_keys=True))
PY
exit "$worst"
