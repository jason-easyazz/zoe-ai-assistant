#!/usr/bin/env bash
# ============================================================
# fetch_face_models.sh — download the on-panel face-ID models
#
# Fetches insightface's buffalo_sc pack (SCRFD-500M detector +
# w600k_mbf MobileFaceNet-class recognizer, ~15 MB total) into
# ~/.zoe-voice/models/ for zoe_face_id.py. SHA256-pinned: a
# tampered or moved release fails loudly instead of installing.
#
# Run ON THE PANEL Pi (or pass FACE_MODEL_DIR to stage elsewhere).
# ============================================================
set -euo pipefail

MODEL_DIR="${FACE_MODEL_DIR:-$HOME/.zoe-voice/models}"
ZIP_URL="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
ZIP_SHA256="57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72"
DET_SHA256="5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a"
REC_SHA256="9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f"

if [ -f "$MODEL_DIR/det_500m.onnx" ] && [ -f "$MODEL_DIR/w600k_mbf.onnx" ]; then
  if echo "$DET_SHA256  $MODEL_DIR/det_500m.onnx" | sha256sum -c --quiet - \
     && echo "$REC_SHA256  $MODEL_DIR/w600k_mbf.onnx" | sha256sum -c --quiet -; then
    echo "==> Face models already present and verified in $MODEL_DIR"
    exit 0
  fi
  echo "==> Existing models failed verification; re-fetching."
fi

mkdir -p "$MODEL_DIR"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "==> Downloading buffalo_sc pack (~15 MB)..."
curl -fsSL -o "$TMP/buffalo_sc.zip" "$ZIP_URL"
echo "$ZIP_SHA256  $TMP/buffalo_sc.zip" | sha256sum -c --quiet -

unzip -o -q "$TMP/buffalo_sc.zip" -d "$TMP/buffalo_sc"
echo "$DET_SHA256  $TMP/buffalo_sc/det_500m.onnx" | sha256sum -c --quiet -
echo "$REC_SHA256  $TMP/buffalo_sc/w600k_mbf.onnx" | sha256sum -c --quiet -

install -m 0644 "$TMP/buffalo_sc/det_500m.onnx" "$MODEL_DIR/det_500m.onnx"
install -m 0644 "$TMP/buffalo_sc/w600k_mbf.onnx" "$MODEL_DIR/w600k_mbf.onnx"
echo "==> Face models installed and verified in $MODEL_DIR"
