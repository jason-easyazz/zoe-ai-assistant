#!/bin/bash
# SOURCED (not executed) by each pytest step of .github/workflows/self-hosted-tests.yml:  source scripts/maintenance/self_hosted_lane_gate.sh <label>
#
# 2026-11-12: GitHub started the 00:30 AWST cron ~6.5 h late (06:55) and its full zoe-data pytest ran on the Jetson WITHOUT /tmp/zoe-test-lane.lock - it overlapped
# a landing's voice probe and failed it on speed (STT 1.9x). The landing's probe window takes that lock; this job must too, and it must stay out of the night.
#   1. refuse (SKIP, never red, never hang) 01:30-05:00 local, and while landing/night_busy.sh says voice | deploy | test must pause;
#   2. take /tmp/zoe-test-lane.lock on fd 9 - the lock then lives as long as the step's shell, i.e. through the pytest line that follows - waiting at most
#      LANE_WAIT_S (default 600 s per step; the job's own cap is 45 min); not won = SKIP;
#   3. re-run the checks of 1 AFTER the lock is won (the wait can last minutes: a lock won at 01:29, or as a landing / the night window starts, must not
#      run pytest into it) and release the lock + SKIP when the box is now inside the night or busy.
# A skip exits the step with 0 after a ::warning:: and a run-summary line: it is reporting-only like the rest of the job, but it is VISIBLE, not silent green.
_lane_label="${1:-suite}"
_lane_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_lane_why=""
_lane_skip(){
  echo "::warning::self-hosted tests (${_lane_label}) SKIPPED: $1"
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "SKIPPED ${_lane_label}: $1" >> "$GITHUB_STEP_SUMMARY"
  exit 0
}
# 0 = clear; else sets _lane_why and returns 1. ZOE_LANDING_NOW_EPOCH: the landing scripts' test clock, shared with night_busy.sh.
_lane_check(){
  local hm kind
  hm=$(date -d "@${ZOE_LANDING_NOW_EPOCH:-$(date +%s)}" +%H%M)
  if [ "$((10#$hm))" -ge 130 ] && [ "$((10#$hm))" -lt 500 ]; then
    _lane_why="it is ${hm}: inside the 01:30-05:00 night window (a late-started cron must not run here)"; return 1
  fi
  for kind in voice deploy test; do
    if bash "$_lane_dir/../landing/night_busy.sh" "$kind"; then
      _lane_why="night_busy.sh ${kind} says this kind of work must pause now (a landing, the night window or the backup owns the box)"; return 1
    fi
  done
  return 0
}
_lane_check || _lane_skip "$_lane_why"
exec 9>"${ZOE_TEST_LANE_LOCK:-/tmp/zoe-test-lane.lock}"
flock -x -w "${LANE_WAIT_S:-600}" 9 || _lane_skip "/tmp/zoe-test-lane.lock stayed held for ${LANE_WAIT_S:-600}s (a landing probe or another test run has the lane)"
_lane_check || { flock -u 9; exec 9>&-; _lane_skip "after the lane wait: ${_lane_why}"; }
echo "test lane held for ${_lane_label}"
