#!/usr/bin/env bash
# Build the isolated Needle spike environment OUTSIDE the repo (lab-only,
# nothing is wired into zoe-data / systemd / CI). Requires uv + network.
#
# Usage: WORK=~/needle-work bash labs/needle-benchmark/setup.sh
set -euo pipefail

WORK="${WORK:?set WORK to a scratch dir outside the repo}"
NEEDLE_SRC="$(opensrc path cactus-compute/needle)"   # pinned in README (commit)

mkdir -p "$WORK" && cd "$WORK"
uv venv --python 3.11 venv
# jax/flax pinned to a mutually compatible CPU pair proven on ARM64 (see README:
# latest jax 0.10 removed remat `concrete`, which needle's flax scan still uses).
VIRTUAL_ENV="$PWD/venv" uv pip install "jax==0.6.2" "flax==0.10.6" \
    numpy sentencepiece huggingface_hub tqdm

# Copy needle source into the scratch dir (NOT the repo — opensrc cache is never
# vendored) and trim the heavy HF-datasets import chain the inference path
# doesn't need (needle.dataset.dataset pulls `datasets`; the tokenizer module
# has everything generate() uses).
cp -r "$NEEDLE_SRC/needle" needle
sed -i 's/from ..dataset.dataset import get_tokenizer, to_snake_case, DEFAULT_MAX_ENC_LEN, DEFAULT_MAX_GEN_LEN/from ..dataset.tokenizer import get_tokenizer, to_snake_case, DEFAULT_MAX_ENC_LEN, DEFAULT_MAX_GEN_LEN/' needle/model/run.py
echo "# lab-trimmed: avoid importing HF datasets for inference" > needle/dataset/__init__.py
cat > needle/__init__.py <<'EOF'
# lab-trimmed init: inference-only surface (no HF datasets dependency)
from needle.model.architecture import SimpleAttentionNetwork, TransformerConfig
from needle.model.run import generate, load_checkpoint
from needle.dataset.tokenizer import get_tokenizer
EOF

# Weights + tokenizer (Cactus-Compute/needle on HF; pkl is the fp checkpoint).
./venv/bin/python - <<'EOF'
from huggingface_hub import hf_hub_download
hf_hub_download(repo_id="Cactus-Compute/needle", filename="needle.pkl", local_dir="ckpt")
for f in ["tokenizer/needle.model", "tokenizer/needle.vocab"]:
    hf_hub_download(repo_id="Cactus-Compute/needle", filename=f, local_dir="ckpt")
EOF
mkdir -p tokenizer && cp ckpt/tokenizer/needle.* tokenizer/

echo "OK. Run e.g.:"
echo "  cd $WORK && PYTHONPATH=$WORK ./venv/bin/python <repo>/labs/needle-benchmark/needle_bench.py --ckpt ckpt/needle.pkl --mode full --out <repo>/labs/needle-benchmark/results/needle_full.json"
