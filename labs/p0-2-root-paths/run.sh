#!/bin/sh
# Hand-run entrypoint for the P0-2 root-path lab (README.md). Builds a throwaway image, runs the cells in ONE
# container with no network and no host mounts, prints the CELL lines, removes the container and the image.
# Owner rules: never 01:00-05:00; no voice harness running; MemAvailable >= 2000 MiB; the box-wide test lane lock.
#   ./run.sh [--keep-image]      P02LAB_WAIT_MEM_S = how long to wait for the memory gate (default 1800)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
NAME=p0-2-root-paths-lab
IMAGE=p0-2-root-paths-lab:local
PROBES='[v]oice_regression_probe\.py|[m]easure_voice\.py|[m]easure_tts\.py|[m]easure_cold_first_turn\.py'

mem_mb() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }
gate() {
  h=$(date +%H); h=${h#0}
  if [ "$h" -ge 1 ] && [ "$h" -lt 5 ]; then echo "REFUSED: $(date +%H:%M) is inside the 01:00-05:00 no-lab window" >&2; exit 3; fi
  if pgrep -f "$PROBES" >/dev/null 2>&1; then return 1; fi
  [ "$(mem_mb)" -ge 2000 ]
}

if [ "${P02LAB_LOCKED:-}" != 1 ]; then
  deadline=$(( $(date +%s) + ${P02LAB_WAIT_MEM_S:-1800} ))
  until gate; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "REFUSED: a voice probe ran or MemAvailable stayed below 2000 MiB" >&2; exit 4; }
    sleep 30
  done
  P02LAB_LOCKED=1 exec flock -x -w 14000 /tmp/zoe-test-lane.lock nice -n 10 "$0" "$@"
fi
gate || { echo "REFUSED after taking the lock: voice probe running or MemAvailable $(mem_mb) MiB < 2000" >&2; exit 4; }

KEEP="${1:-}"
ctx="$(mktemp -d)"
teardown() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  [ "$KEEP" = "--keep-image" ] || docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
  rm -rf "$ctx"
}
trap teardown EXIT INT TERM
cp -r "$here/lab" "$ctx/lab"
mkdir -p "$ctx/deliverables"
cp -r "$repo/scripts/setup/p0-deroot/p0-2" "$repo/scripts/setup/p0-deroot/libexec" "$ctx/deliverables/"
cp "$here/Dockerfile" "$ctx/"
docker build -q -t "$IMAGE" "$ctx" >/dev/null

# NOT --privileged, no host mount of any kind, private cgroup namespace (Docker's default), no network, the
# default seccomp profile. SYS_ADMIN is the one added capability: the runtime stand-in needs mount(2) and
# unshare(2) to model a container's bind mounts (the box runs no AppArmor; with it, the container would also need
# apparmor=unconfined). Strictly less than labs/p0-deroot, which also shares the host cgroup tree.
set +e
docker run --name "$NAME" --hostname "$NAME" --network none --cap-add SYS_ADMIN \
  --memory 256m --memory-swap 256m --pids-limit 256 "$IMAGE" > "$here/last-console.log" 2>&1
rc=$?
set -e
grep -E '^CELL ' "$here/last-console.log" || echo "NO CELL LINES: see $here/last-console.log" >&2
grep -E '^P0LAB-RESULT ' "$here/last-console.log" | sed 's/^P0LAB-RESULT //' > "$here/last-result.json" || true

teardown; trap - EXIT INT TERM
[ -z "$(docker ps -aq --filter "name=^${NAME}\$")" ] || { echo "TEARDOWN FAILED: container still present" >&2; exit 6; }
echo "teardown: container removed$([ "$KEEP" = --keep-image ] || echo ', image removed') (cells exit $rc)"
[ -s "$here/last-result.json" ] || { echo "NO RESULT: read $here/last-console.log" >&2; exit 5; }
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print("%d/%d cells pass" % (r["pass"], r["total"])); sys.exit(r["pass"] != r["total"])' "$here/last-result.json"
