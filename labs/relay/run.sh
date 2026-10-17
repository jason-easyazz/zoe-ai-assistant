#!/bin/sh
# Hand-run entrypoint for the F0 relay lab. Takes the box-wide test-lane lock and runs niced, as the owner rules require.
#   ./run.sh unit          the Python unit tests (no Docker)
#   ./run.sh lab [args]    the full hermetic lab (needs MemAvailable >= 1500 MB)
# The runner itself refuses to run 01:50-04:55 local time and re-checks the memory gate after taking the lock.
# The wait for memory happens BEFORE the lock is taken, so a waiting lab never holds the lane against other agents.
#   RELAY_LAB_WAIT_MEM_S   how long to wait for the gate (default 3600)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3.12}"
case "${1:-}" in
  unit)
    shift
    exec flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 "$PY" -W ignore -m unittest discover -s "$here/tests" "$@"
    ;;
  lab)
    shift
    deadline=$(( $(date +%s) + ${RELAY_LAB_WAIT_MEM_S:-3600} ))
    while [ "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" -lt 1500 ]; do
      if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "REFUSED: MemAvailable stayed below 1500 MB for ${RELAY_LAB_WAIT_MEM_S:-3600}s" >&2
        exit 4
      fi
      sleep 15
    done
    exec flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 "$PY" "$here/run_lab.py" --wait-mem-s 300 "$@"
    ;;
  *)
    echo "usage: $0 unit | lab [run_lab.py args]" >&2
    exit 2
    ;;
esac
