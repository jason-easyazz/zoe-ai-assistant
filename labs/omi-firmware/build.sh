#!/usr/bin/env bash
# Compile the patched firmware in the pinned Zephyr CI image (nRF Connect SDK v2.9.0, sysbuild + MCUboot), exactly the
# toolchain upstream's own CV1 release workflow uses. Hand-run; it NEVER flashes or talks to a pendant.
#   ZOE_OMI_FW_WORK      work dir (default ~/.cache/zoe-omi-fw; run fetch.sh first). Outputs land in $WORK/out/
#   ZOE_FW_SIGNING_KEY   path to the owner's MCUboot RSA-2048 key (OUTSIDE the repo; see RUNBOOK key ceremony). Unset =
#                        upstream's committed PUBLIC dev key - the one the stock bootloader trusts (an OTA build).
#   $1 = "sdk" fetches/updates the SDK workspace only (network-bound, light); default compiles too.
#   ZOE_FW_BUILD_MEM     container memory cap (default 2g, no swap); ZOE_FW_BUILD_JOBS ninja jobs (default 2)
# The SDK workspace (~2-3 GB) lives in the docker volume zoe-omi-ncs-v2.9.0 and is reused; remove it with
# `docker volume rm zoe-omi-ncs-v2.9.0`. Stop a running build with `docker stop zoe-omi-fw-build`.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${ZOE_OMI_FW_WORK:-$HOME/.cache/zoe-omi-fw}"
IMAGE="ghcr.io/zephyrproject-rtos/ci:v0.26.13@sha256:b0ac6334d1926cd0971a0a444f7adc6dd020e88ee3ce865aa070b6475a3ac4eb"
VOL="zoe-omi-ncs-v2.9.0"
MEM="${ZOE_FW_BUILD_MEM:-2g}"
JOBS="${ZOE_FW_BUILD_JOBS:-2}"
KEY="${ZOE_FW_SIGNING_KEY:-}"

avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "$avail" -lt 2000 ]; then
  echo "refused: MemAvailable ${avail} MB < 2000 MB" >&2
  exit 2
fi
if [ -x "$HOME/.zoe/agent-tools/night_busy.sh" ] && bash "$HOME/.zoe/agent-tools/night_busy.sh" test; then
  echo "refused: the night window or the backup is running" >&2
  exit 2
fi
if [ ! -f "$WORK/src/omi/firmware/omi/prj.conf" ]; then
  echo "run fetch.sh first" >&2
  exit 2
fi

key_mount=()
key_arg=""
if [ -n "$KEY" ]; then
  case "$(realpath "$KEY")" in
    "$(realpath "$HERE/../..")"/*) echo "refused: the signing key must live outside the repository" >&2; exit 2 ;;
  esac
  key_mount=(-v "$(realpath "$KEY"):/key/zoe-mcuboot.pem:ro")
  key_arg="-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE=/key/zoe-mcuboot.pem"
  echo "signing with the OWNER key (only a pendant re-flashed over SWD with this build's MCUboot will accept it)"
else
  echo "signing with upstream's PUBLIC dev key (what the stock bootloader trusts)"
fi

docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL:/ncs" "$IMAGE" chown "$(id -u):$(id -g)" /ncs
mkdir -p "$WORK/out"
lane=(flock -x -w 3600 /tmp/zoe-test-lane.lock)      # the compile is the RAM-heavy part: one test lane on this box
if [ "${1:-all}" = "sdk" ]; then lane=(); fi
"${lane[@]}" nice -n 10 \
  docker run --rm --name zoe-omi-fw-build --memory="$MEM" --memory-swap="$MEM" --user "$(id -u):$(id -g)" \
    -e HOME=/ncs/home -e JOBS="$JOBS" -e PHASE="${1:-all}" -e KEY_ARG="$key_arg" \
    -v "$VOL:/ncs" -v "$WORK/src/omi/firmware:/omi/firmware" -v "$WORK/out:/out" \
    -v "$HERE/container-build.sh:/zoe/container-build.sh:ro" "${key_mount[@]}" \
    "$IMAGE" bash /zoe/container-build.sh
echo "outputs: $WORK/out"
