#!/usr/bin/env bash
# Runs INSIDE the pinned Zephyr CI image (see build.sh). Mirrors upstream omi/firmware/scripts/ci/build-cv1.sh.
set -euo pipefail

mkdir -p "$HOME"
# The image registers its Zephyr SDK for ITS user; we run as the host uid with our own HOME, so name the SDK explicitly.
if [ -z "${ZEPHYR_SDK_INSTALL_DIR:-}" ]; then
  ZEPHYR_SDK_INSTALL_DIR="$(ls -d /opt/toolchains/zephyr-sdk-* | head -1)"
  export ZEPHYR_SDK_INSTALL_DIR
fi
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
git config --global --add safe.directory '*'
python3 -m pip install --user --quiet ecdsa 2>/dev/null || python3 -m pip install --user --quiet --break-system-packages ecdsa

cd /ncs
if [ ! -d .west ]; then
  west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.9.0 .
fi
west update -o=--depth=1 -n
west zephyr-export
if [ "${PHASE:-all}" = "sdk" ]; then
  echo "SDK workspace ready"
  exit 0
fi

# shellcheck disable=SC2086  # KEY_ARG is empty or one -D argument
west build -b omi/nrf5340/cpuapp /omi/firmware/omi --sysbuild -d /ncs/build-zoe --pristine always -o="-j${JOBS:-2}" \
  -- -DBOARD_ROOT=/omi/firmware $KEY_ARG

B=/ncs/build-zoe
test -s "$B/dfu_application.zip"
test -s "$B/merged.hex"
test -s "$B/merged_CPUNET.hex"
cp "$B/dfu_application.zip" "$B/merged.hex" "$B/merged_CPUNET.hex" /out/
cp "$B/omi/zephyr/zephyr.signed.bin" /out/omi.signed.bin
cp "$B/omi/zephyr/.config" /out/app.config
cd /out && sha256sum dfu_application.zip merged.hex merged_CPUNET.hex omi.signed.bin > SHA256SUMS
cat SHA256SUMS
