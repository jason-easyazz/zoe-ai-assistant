#!/usr/bin/env bash
# Off-box backup lab (architecture deep dive A5). Hand-run only; never wired into CI, a unit or zoe-data.
#   labs/offbox-backup/run.sh
# Gates (the box's lab rules): the shared test-lane lock, never inside 01:35-04:55 (the night window), and
# MemAvailable >= 1500 MB. Installs the pinned restic/age into labs/offbox-backup/.bin (gitignored, sha256-checked).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

hm=$((10#$(date +%H%M)))
if (( hm >= 135 && hm < 455 )); then
  echo "refused: inside the night window (01:35-04:55)" >&2; exit 3
fi
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail < 1500 )); then
  echo "refused: MemAvailable ${avail} MB < 1500 MB" >&2; exit 3
fi

exec 9>/tmp/zoe-test-lane.lock
flock -w 900 9 || { echo "refused: test-lane lock busy for 15 min" >&2; exit 3; }

bash "$repo/scripts/setup/offbox-backup/install_tools.sh" --dest "$here/.bin" >/dev/null
python3 "$here/lab.py"
