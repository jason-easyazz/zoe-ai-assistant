#!/bin/sh
# Hand-run entrypoint for the comms Matrix lab. Never while the box's night_busy gate says pause (the night window or the backup
# is running), waits for MemAvailable >= 2000 MB BEFORE taking the shared test-lane lock (a waiting lab never holds the lane),
# then runs niced under that lock. Everything it starts is torn down by run_lab.py.
#   ./run.sh [run_lab.py args]      e.g. ./run.sh --report /tmp/claude-1000/comms/matrix-lab.json
#   MATRIX_LAB_WAIT_MEM_S           how long to wait for the memory gate (default 3600)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
busy=/home/zoe/.zoe/agent-tools/night_busy.sh
paused() { [ -x "$busy" ] || [ -f "$busy" ] && bash "$busy" test; }
if paused; then echo "REFUSED: night_busy says pause (night window or backup running)" >&2; exit 3; fi
deadline=$(( $(date +%s) + ${MATRIX_LAB_WAIT_MEM_S:-3600} ))
while [ "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" -lt 2000 ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then echo "REFUSED: MemAvailable stayed below 2000 MB" >&2; exit 4; fi
  sleep 15
done
if paused; then echo "REFUSED: night_busy says pause" >&2; exit 3; fi
exec flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 python3 "$here/run_lab.py" "$@"
