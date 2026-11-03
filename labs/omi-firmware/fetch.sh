#!/usr/bin/env bash
# Fetch the upstream Omi firmware at the PINNED commit (sparse + blobless: only omi/firmware/, ~19 MB), overlay the Zoe
# sources and apply the patch series. Nothing is vendored into this repo. Hand-run; touches no device.
#   ZOE_OMI_FW_WORK  work dir (default ~/.cache/zoe-omi-fw); the patched tree lands in $ZOE_OMI_FW_WORK/src
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="https://github.com/BasedHardware/omi"
PIN="f12031d15ea177bf8f6ac0781d7f28f3dc433eae"     # tag Omi_CV1_v3.0.21 (the pendant's stock firmware)
WORK="${ZOE_OMI_FW_WORK:-$HOME/.cache/zoe-omi-fw}"
SRC="$WORK/src"

rm -rf "$SRC"
mkdir -p "$SRC"
git -C "$SRC" init -q
git -C "$SRC" remote add origin "$UPSTREAM"
git -C "$SRC" config core.sparseCheckout true
echo "omi/firmware/" > "$SRC/.git/info/sparse-checkout"
git -C "$SRC" fetch -q --depth 1 --filter=blob:none origin "$PIN"
git -C "$SRC" checkout -q FETCH_HEAD
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$PIN" ]; then
  echo "pinned commit mismatch" >&2
  exit 1
fi

APP="$SRC/omi/firmware/omi"
cp -r "$HERE/overlay/omi/src/zoe" "$APP/src/"
for p in "$HERE"/patches/*.patch; do
  git -C "$SRC" apply --whitespace=nowarn "$p"
  echo "applied $(basename "$p")"
done
# Upstream builds prj.conf = omi.conf; ours appends the Zoe fragment (later assignments win).
cat "$APP/omi.conf" "$HERE/overlay/omi/zoe.conf" > "$APP/prj.conf"
echo "patched tree: $SRC/omi/firmware (upstream $PIN)"
