#!/usr/bin/env bash
# Export a merged fine-tuned checkpoint to GGUF Q8_0 for the :11435 eval.
# Usage: ./export_gguf.sh <merged-dir> <out.gguf>
# The merged dir comes from train_lora.py (runs/<variant>/merged).
# Uses the repo llama.cpp checkout on the box; off-box, point LLAMA_CPP at
# any llama.cpp >= b4700 checkout (gemma3 arch support).
set -euo pipefail
LLAMA_CPP="${LLAMA_CPP:-/home/zoe/llama.cpp}"
MERGED="$1"
OUT="$2"
python3 "$LLAMA_CPP/convert_hf_to_gguf.py" "$MERGED" \
    --outfile "$OUT" --outtype q8_0
echo "wrote $OUT"
