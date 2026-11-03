#!/bin/sh
# Hand-run entrypoint for the credential-vault lab, phases V0 + V1 (README.md). Never wired into CI, a unit or zoe-data.
#   labs/credential-vault/run.sh [--keep-image] [PIECE ...]      PIECE = v0 A-F (default: all, in that order)
# Each PIECE is a separate lab: bring up, seed, its cell group, tear down, in ONE hold of the box-wide test-lane lock
# (about 5-10 minutes; driver.py's watchdog aborts a piece at 15). The lock is released between pieces so other
# agents' short test runs interleave (coordinator, 2026-10-19). Then the per-piece results are combined.
# Owner rules (2026-10-19), checked before EVERY piece: never while `night_busy.sh test` or `night_busy.sh deploy`
# says pause (the backup; the 12B night window from 02:45 until it has finished), never START a piece between 02:05
# and 02:45, and MemAvailable >= 2000 MB before anything comes up; niced. Without night_busy.sh (another clone) it
# refuses 01:35-04:55 instead. The memory wait happens BEFORE the lock, so a waiting lab never holds the lane.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
NB=/home/zoe/.zoe/agent-tools/night_busy.sh

refuse_if_busy() {
  hm=$(date +%H%M)
  if [ -f "$NB" ]; then
    if bash "$NB" test; then echo "REFUSED: night_busy.sh test says pause" >&2; exit 3; fi
    if bash "$NB" deploy; then echo "REFUSED: night_busy.sh deploy says pause (night window)" >&2; exit 3; fi
    if [ "$hm" -ge 0205 ] && [ "$hm" -lt 0245 ]; then echo "REFUSED: would overlap the 02:45 night window" >&2; exit 3; fi
  elif [ "$hm" -ge 0135 ] && [ "$hm" -le 0455 ]; then
    echo "REFUSED: $(date +%H:%M) is inside the 01:35-04:55 no-lab window" >&2; exit 3
  fi
}
mem_mb() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }

# Inner mode: one piece, already holding the lock.
if [ "${CVLAB_LOCKED:-}" = 1 ]; then
  refuse_if_busy
  i=0   # memory moves; give it up to 2 minutes after taking the lock, then let the lane go
  while [ "$(mem_mb)" -lt 2000 ]; do
    i=$((i + 1)); [ "$i" -le 8 ] || { echo "REFUSED: MemAvailable $(mem_mb) MB < 2000 after taking the lock" >&2; exit 4; }
    sleep 15
  done
  exec python3 "$here/driver.py" "$@"
fi

keep=""; pieces=""
for a in "$@"; do
  case "$a" in --keep-image) keep="--keep-image" ;; *) pieces="$pieces $a" ;; esac
done
[ -n "$pieces" ] || pieces="v0 A B C D E F"
rc=0
for p in $pieces; do
  refuse_if_busy
  deadline=$(( $(date +%s) + ${CVLAB_WAIT_MEM_S:-1800} ))
  while [ "$(mem_mb)" -lt 2000 ]; do
    refuse_if_busy
    [ "$(date +%s)" -lt "$deadline" ] || { echo "REFUSED: MemAvailable stayed below 2000 MB" >&2; exit 4; }
    sleep 15
  done
  echo "=== piece $p ($(date +%H:%M))"
  CVLAB_LOCKED=1 flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 "$0" --piece "$p" $keep || rc=1
  sleep 60   # let queued test runs take the lane between pieces
done
python3 "$here/driver.py" --combine || rc=1
exit $rc
