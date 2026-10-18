#!/usr/bin/env bash
# Multica board behind Zoe's sign-in: hermetic lab. Hand-run only; never wired into CI, a unit or zoe-data.
#   labs/multica-signin-gate/run.sh
# Gates (the box's lab rules), checked AFTER the shared test-lane lock is held so a long wait cannot outdate them:
# MemAvailable >= 2000 MB and `night_busy.sh test` must not say pause (the night window / backup own the RAM then).
# Nothing is pulled: every image is the box's own copy.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

exec 9>/tmp/zoe-test-lane.lock
flock -w 900 9 || { echo "refused: test-lane lock busy for 15 min" >&2; exit 3; }

avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail < 2000 )); then
  echo "refused: MemAvailable ${avail} MB < 2000 MB" >&2; exit 3
fi
busy="${HOME}/.zoe/agent-tools/night_busy.sh"
if [[ -f "$busy" ]] && bash "$busy" test; then
  echo "refused: night_busy.sh test says pause (night window or backup running)" >&2; exit 3
fi

nice -n 10 python3 "$here/lab/lab.py"
