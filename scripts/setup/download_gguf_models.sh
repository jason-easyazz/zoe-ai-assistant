#!/usr/bin/env bash
# Download Zoe's canonical local brain: Gemma 4 E4B-QAT + the MTP drafter.
#
# These are the LOCKED rocks (see docs/CANONICAL.md) that host-native
# llama-server (:11434) loads. Source: unsloth/gemma-4-E4B-it-qat-GGUF.
#   - gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf   (~4.2 GB) — the brain
#   - mtp-gemma-4-E4B-it.gguf              (~60 MB)  — the MTP speculative drafter
# Optionally also fetches the two-stage router decoder (FunctionGemma-270M).
#
# Safe to re-run: a file that already matches its pinned sha256 (models.lock) is not fetched again; a download goes to
# <name>.part (resumable), is renamed only after size + sha256 match, then <name>.ready is written last. A failure is one
# plain sentence and a non-zero exit; an existing good file is never deleted. Overrides: ZOE_MODELS_LOCK, ZOE_MODEL_BASE_URL.
#
# Usage:
#   scripts/setup/download_gguf_models.sh [--models-dir DIR] [--with-router]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/setup/lib/common.sh
source "${ROOT_DIR}/scripts/setup/lib/common.sh"

MODELS_DIR="${HOME}/models/gemma4-e4b-qat"
ROUTER_DIR="${HOME}/models/functiongemma-270m"
WITH_ROUTER=0

BRAIN_REPO="unsloth/gemma-4-E4B-it-qat-GGUF"
BRAIN_FILE="gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
MTP_FILE="mtp-gemma-4-E4B-it.gguf"
ROUTER_REPO="unsloth/functiongemma-270m-it"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --models-dir) MODELS_DIR="$2"; shift 2 ;;
    --with-router) WITH_ROUTER=1; shift ;;
    -h|--help) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# Brain + drafter: verified download (pinned sha256 + size from models.lock, resume, free-space check, .ready marker).
# shellcheck source=scripts/setup/lib/verified_download.sh
source "${ROOT_DIR}/scripts/setup/lib/verified_download.sh"
LOCK="${ZOE_MODELS_LOCK:-${ROOT_DIR}/models.lock}"
BASE_URL="${ZOE_MODEL_BASE_URL:-https://huggingface.co/${BRAIN_REPO}/resolve/main}"

step "Gemma 4 E4B-QAT brain + MTP drafter → ${MODELS_DIR}"
verified_download "$MODELS_DIR" "$BRAIN_FILE" "${BASE_URL}/${BRAIN_FILE}" "$LOCK" || exit 1
verified_download "$MODELS_DIR" "$MTP_FILE" "${BASE_URL}/${MTP_FILE}" "$LOCK" || exit 1

if [[ "$WITH_ROUTER" == "1" ]]; then
  step "FunctionGemma-270M router decoder → ${ROUTER_DIR}"
  if compgen -G "${ROUTER_DIR}/*.gguf" >/dev/null 2>&1; then
    ok "router model already present — skipping"
  else
    warn "The router decoder is not pinned (it is replaced by design), so it is not checksum-verified."
    have_cmd hf || have_cmd huggingface-cli || die "The router download needs the 'hf' tool (pip3 install -U 'huggingface_hub[cli]'), which is not installed."
    HF="hf"; have_cmd hf || HF="huggingface-cli"
    mkdir -p "$ROUTER_DIR"
    "$HF" download "$ROUTER_REPO" --include "*.gguf" --local-dir "$ROUTER_DIR" >/dev/null 2>&1 || true
    compgen -G "${ROUTER_DIR}/*.gguf" >/dev/null 2>&1 || die "The router decoder download produced no model file; it is built on the box, so skip --with-router."
    ok "router model downloaded"
  fi
fi

step "Done"
ok "brain:   ${MODELS_DIR}/${BRAIN_FILE}"
ok "drafter: ${MODELS_DIR}/${MTP_FILE}"
printf '%b\n' "  ${C_DIM}llama-server.service loads these two paths — see scripts/setup/systemd/README.md${C_NC}"
