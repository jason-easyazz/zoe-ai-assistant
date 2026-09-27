#!/usr/bin/env bash
# Provision the Kokoro ONNX Runtime backend (B5.1) — venv + model files. Idempotent.
#
# Installs NOTHING into the system site-packages: the backend runs from its own uv
# venv (default ~/.venvs/kokoro-onnx) so onnxruntime-gpu + numpy 2 never touch the
# system Python that llama-server tooling / the PyTorch sidecar use.
#
#   scripts/setup/install_kokoro_onnx.sh            # venv + models, verify hashes
#   KOKORO_ONNX_VENV=/path scripts/setup/install_kokoro_onnx.sh
#
# Does not start, stop or restart any service. The cutover is a separate, operator-
# run Kokoro-window step: docs/knowledge/kokoro-onnx-migration.md.
set -euo pipefail

VENV="${KOKORO_ONNX_VENV:-$HOME/.venvs/kokoro-onnx}"
MODELS="${KOKORO_ONNX_MODELS:-$HOME/models/kokoro-onnx}"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
JETSON_INDEX="https://pypi.jetson-ai-lab.io/jp6/cu126"
RELEASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
SPACY_MODEL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# sha256 of the model-files-v1.1 assets (verified 2026-09-27).
declare -A SHA256=(
  [kokoro-v1.0.fp16.onnx]=f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96
  [voices-v1.0.bin]=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
)

[ -x "$UV" ] || { echo "uv not found (set UV=/path/to/uv)" >&2; exit 1; }

if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv "$VENV" --python /usr/bin/python3
fi
# onnxruntime-gpu: the Jetson JP6/CUDA 12.6 cp310 aarch64 wheel (PyPI ships no aarch64
# GPU build). It is numpy-2 ABI, so numpy 2.2.6 satisfies it AND kokoro-onnx.
"$UV" pip install --python "$VENV/bin/python" "onnxruntime-gpu==1.24.0" "numpy==2.2.6" \
  --index-url "$JETSON_INDEX" --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
# kokoro-onnx without deps: its hard `onnxruntime` (CPU) dep would shadow the GPU wheel.
"$UV" pip install --python "$VENV/bin/python" --no-deps "kokoro-onnx==0.6.1"
# kokoro-onnx runtime deps + misaki (KPipeline's G2P, for phoneme parity) + the sidecar's server deps.
"$UV" pip install --python "$VENV/bin/python" \
  "espeakng-loader==0.2.4" "phonemizer-fork==3.3.2" \
  "misaki==0.9.4" "num2words==0.5.14" "spacy==3.8.14" "$SPACY_MODEL" \
  "fastapi==0.141.1" "uvicorn==0.54.0" "pydantic==2.13.5"

mkdir -p "$MODELS"
for f in "${!SHA256[@]}"; do
  if [ ! -f "$MODELS/$f" ] || ! echo "${SHA256[$f]}  $MODELS/$f" | sha256sum -c --quiet - 2>/dev/null; then
    curl -fsSL -o "$MODELS/$f.part" "$RELEASE/$f"
    mv "$MODELS/$f.part" "$MODELS/$f"
  fi
  echo "${SHA256[$f]}  $MODELS/$f" | sha256sum -c -
done

"$VENV/bin/python" - <<'EOF'
import onnxruntime as ort, numpy, kokoro_onnx, misaki  # noqa: F401 — import smoke
providers = ort.get_available_providers()
print("onnxruntime", ort.__version__, "numpy", numpy.__version__, "providers", providers)
assert "CUDAExecutionProvider" in providers, "CUDA EP missing — wrong onnxruntime wheel?"
EOF
echo "OK: venv=$VENV models=$MODELS"
