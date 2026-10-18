#!/bin/sh
# Hand-run entrypoint for the P0 de-root lab (README.md). Builds a throwaway image, boots systemd in ONE container
# with no network, reads the CELL lines out of it, then removes the container and the image.
# Owner rules: never 01:35-04:55 (night window 01:50-04:55 + 15 min margin), MemAvailable >= 1500 MB, the box-wide
# test lane lock, niced. The memory wait happens BEFORE the lock, so a waiting lab never holds the lane.
#   ./run.sh [--keep-image]      P0LAB_WAIT_MEM_S = how long to wait for the memory gate (default 3600)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
NAME=p0-deroot-lab
IMAGE=p0-deroot-lab:local

in_night_window() {
  h=$(date +%H); m=$(date +%M); now=$(( ${h#0} * 60 + ${m#0} ))
  [ "$now" -ge 95 ] && [ "$now" -le 295 ]
}
refuse_in_window() {
  if in_night_window; then echo "REFUSED: $(date +%H:%M) is inside the 01:35-04:55 no-lab window" >&2; exit 3; fi
}
mem_mb() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }

if [ "${P0LAB_LOCKED:-}" != 1 ]; then
  refuse_in_window
  deadline=$(( $(date +%s) + ${P0LAB_WAIT_MEM_S:-3600} ))
  while [ "$(mem_mb)" -lt 1500 ]; do
    refuse_in_window
    [ "$(date +%s)" -lt "$deadline" ] || { echo "REFUSED: MemAvailable stayed below 1500 MB" >&2; exit 4; }
    sleep 15
  done
  P0LAB_LOCKED=1 exec flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 "$0" "$@"
fi
refuse_in_window
[ "$(mem_mb)" -ge 1500 ] || { echo "REFUSED: MemAvailable $(mem_mb) MB < 1500 after taking the lock" >&2; exit 4; }

ctx="$(mktemp -d)"
teardown() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  [ "${1:-}" = "--keep-image" ] || docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
  rm -rf "$ctx"
}
trap 'teardown "${KEEP:-}"' EXIT INT TERM
KEEP="${1:-}"

cp -r "$here/lab" "$ctx/lab"
cp -r "$repo/scripts/setup/p0-deroot" "$ctx/deliverables"
cp "$here/Dockerfile" "$ctx/"
docker build -q -t "$IMAGE" "$ctx" >/dev/null

# systemd as PID 1, NOT --privileged. Measured 2026-10-18 on this Jetson (Docker 29, cgroup v2): with
# --cgroupns=private systemd never booted (no output at all); with the host cgroup namespace and a read-write
# /sys/fs/cgroup it boots. The container is a throwaway on NO network with synthetic users and secrets: it can see
# the host cgroup tree (the cost of booting systemd unprivileged), never the host's users, sudoers, Docker socket or
# services. SYS_ADMIN + unconfined seccomp/apparmor let ProtectSystem / BindPaths mount inside it.
docker run -d --name "$NAME" --hostname "$NAME" --network none --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  --cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  --memory 512m --memory-swap 512m "$IMAGE" >/dev/null
i=0
until docker exec "$NAME" test -f /run/p0lab-cells.done 2>/dev/null; do
  i=$((i + 1)); [ "$i" -lt 120 ] || { echo "TIMEOUT: no cell results after 240 s" >&2; break; }
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ] || { echo "the lab container exited" >&2; break; }
  sleep 2
done
# Everything needed to debug a failed boot, captured whether or not the cells ran.
{ echo "== docker logs"; docker logs "$NAME" 2>&1
  echo "== systemctl --failed"; docker exec "$NAME" systemctl --failed --no-pager 2>&1
  echo "== journal (warning and above)"; docker exec "$NAME" journalctl -b --no-pager -p warning 2>&1 | tail -200
} > "$here/last-console.log"
out="$(docker exec "$NAME" cat /run/p0lab-cells.txt 2>/dev/null || true)"
# printf, never echo: dash's echo expands the backslash escapes inside the JSON line.
printf '%s\n' "$out" | grep -E '^CELL ' || echo "NO CELL LINES: see $here/last-console.log" >&2
printf '%s\n' "$out" | grep -E '^P0LAB-RESULT ' | sed 's/^P0LAB-RESULT //' > "$here/last-result.json" || true

teardown "$KEEP"; trap - EXIT INT TERM
[ -z "$(docker ps -aq --filter "name=^${NAME}\$")" ] || { echo "TEARDOWN FAILED: container still present" >&2; exit 6; }
echo "teardown: container removed$([ "$KEEP" = --keep-image ] || echo ', image removed')"
if [ ! -s "$here/last-result.json" ]; then
  echo "NO RESULT: the cells never finished; the boot is in $here/last-console.log" >&2; exit 5
fi
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print("%d/%d cells pass" % (r["pass"], r["total"])); sys.exit(r["pass"] != r["total"])' "$here/last-result.json"
