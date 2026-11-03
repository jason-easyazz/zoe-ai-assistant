#!/bin/bash
# night_busy.sh voice|deploy|test|deadline -> exit 0 when that kind of work must PAUSE now, 1 when it may run.
# Replaces the fixed 01:50-04:55 blackout (owner, 2026-10-19: "doesn't the night brain only take like 15 minutes").
# Measured 10-12..10-18: zoe-night-window starts 02:50 (+<=2 min jitter) and FINISHED 02:52-03:10 every night.
#   voice : a voice landing holds the brain-window lock up to ~45 min, and the window REFUSES (a lost night) if it is
#           held at 02:50 -> no voice landing STARTS from 01:55 until tonight's window has finished; and 04:00-04:55
#           the nightly voice-regression (04:30) owns the single voice-gate artifact slot.
#   deploy: a deploy restarts zoe-data, which collides with the window's stops -> pause 02:45 until it has finished.
#   test  : RAM -> pause while the night window or the backup is running.
# `night_busy.sh deadline` prints the epoch second a voice lander must be GONE by (see below).
# Test hook: ZOE_LANDING_NOW_EPOCH pretends "now" (the clock is otherwise `date`); ZOE_LANDING_HOME/voice-day-ok is the day gate.
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
kind=${1:-deploy}
now=$(landing_now); hm=$(date -d "@$now" +%H%M); today=$(date -d "@$now" +%F)
active(){ s=$(systemctl --user is-active "$1" 2>/dev/null); [ "$s" = active ] || [ "$s" = activating ] || [ "$s" = deactivating ]; }
# Has tonight's window finished SUCCESSFULLY? (its last exit is after 02:45 today and it exited 0). 2026-10-26: a REFUSED
# window (exit 2, e.g. "a voice-PR landing is running") used to count as finished and released every lander - so the night
# stays paused until the 03:30 fallback, leaving room for a re-run.
finished_tonight(){
  t=$(systemctl --user show zoe-night-window.service -p ExecMainExitTimestamp --value 2>/dev/null)
  rc=$(systemctl --user show zoe-night-window.service -p ExecMainStatus --value 2>/dev/null)
  [ -n "$t" ] && [ "${rc:-1}" = 0 ] && [ "$(date -d "$t" +%s 2>/dev/null || echo 0)" -ge "$(date -d "$today 02:45" +%s)" ]
}
# 2026-10-27: the next 02:40 - ten minutes before the window's 02:50 start (+<=2 min jitter) - never later than tomorrow's.
# A lander that started at 01:50, or sat 30 min on CI with the probe lock held, used to straddle 02:50 and made the window refuse
# (2026-10-26, a lost 12B night). The window's busy check pgreps `land_voice_pr.sh` and probes /tmp/zoe-brain-window.lock,
# so BOTH must be gone by this instant.
if [ "$kind" = deadline ]; then
  d=$(date -d "$today 02:40" +%s); [ "$now" -ge "$d" ] && d=$(date -d "$(date -d "$today +1 day" +%F) 02:40" +%s)
  echo "$d"; exit 0
fi
active zoe-night-window.service && exit 0
case "$kind" in
  voice)
    # 2026-10-28: the nightly replay (zoe-voice-regression.service, 04:30) owns the single voice-gate artifact; its quiet-box wait can push it past 04:55 when landings
    # resume, and an overwrite between a lander's probe and the deploy's read gets that deploy refused. Busy while it is activating or active.
    active zoe-voice-regression.service && exit 0
    # 2026-10-20: a voice landing STOPS Kokoro for ~15 min (the probe) - Zoe goes silent. Never while the household is awake.
    # Owner may lift the daytime pause for one day: $ZOE_LANDING_HOME/voice-day-ok containing today's date (YYYY-MM-DD).
    if [ "$hm" -ge 0700 ] && [ "$hm" -lt 2200 ] && [ "$(cat "$LANDING_HOME/voice-day-ok" 2>/dev/null)" != "$today" ]; then exit 0; fi
    if [ "$hm" -ge 0155 ] && [ "$hm" -lt 0400 ]; then
      finished_tonight && exit 1
      # never ran tonight (box was busy/refused?) - stay paused until 03:30 at the latest, then let work resume
      [ "$hm" -lt 0330 ] && exit 0 || exit 1
    fi
    [ "$hm" -ge 0400 ] && [ "$hm" -le 0455 ] && exit 0
    exit 1 ;;
  deploy)
    if [ "$hm" -ge 0245 ] && [ "$hm" -lt 0400 ]; then
      finished_tonight && exit 1
      [ "$hm" -lt 0330 ] && exit 0 || exit 1
    fi
    exit 1 ;;
  test)
    active zoe-backup.service && exit 0
    exit 1 ;;
esac
exit 1
