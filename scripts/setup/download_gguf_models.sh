#!/usr/bin/env bash
# Download Zoe's canonical local brain: Gemma 4 E4B-QAT + the MTP drafter.
#
# These are the LOCKED rocks (see docs/CANONICAL.md) that host-native
# llama-server (:11434) loads. Source: unsloth/gemma-4-E4B-it-qat-GGUF.
#   - gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf   (~4.2 GB) — the brain
#   - mtp-gemma-4-E4B-it.gguf              (~60 MB)  — the MTP speculative drafter
# Optionally also fetches the two-stage router decoder (FunctionGemma-270M).
#
# Idempotent: files already present (non-empty) are skipped.
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

# Prefer the modern `hf` CLI; fall back to the (deprecated) huggingface-cli.
HF=""
if have_cmd hf; then HF="hf"
elif have_cmd huggingface-cli; then HF="huggingface-cli"
else
  log "Installing huggingface_hub CLI…"
  pip3 install -U "huggingface_hub[cli]"
  HF="hf"; have_cmd hf || HF="huggingface-cli"
fi

hf_download() { # <repo> <local-dir> <include-glob>
  local repo="$1" dir="$2" include="$3"
  mkdir -p "$dir"
  "$HF" download "$repo" --include "$include" --local-dir "$dir"
}

nonempty() { [[ -s "$1" ]]; }

step "Gemma 4 E4B-QAT brain → ${MODELS_DIR}"
mkdir -p "$MODELS_DIR"

if nonempty "${MODELS_DIR}/${BRAIN_FILE}"; then
  ok "brain already present — skipping ($(du -h "${MODELS_DIR}/${BRAIN_FILE}" | cut -f1))"
else
  log "Downloading ${BRAIN_FILE} (~4.2 GB) from ${BRAIN_REPO}…"
  hf_download "$BRAIN_REPO" "$MODELS_DIR" "$BRAIN_FILE"
  nonempty "${MODELS_DIR}/${BRAIN_FILE}" || die "brain download failed"
  ok "brain downloaded"
fi

if nonempty "${MODELS_DIR}/${MTP_FILE}"; then
  ok "MTP drafter already present — skipping"
else
  log "Downloading MTP drafter (${MTP_FILE}) from ${BRAIN_REPO}…"
  # The drafter lives under MTP/ in the repo; pull it then flatten to the dir root
  # so it sits next to the brain where llama-server.service expects it.
  hf_download "$BRAIN_REPO" "$MODELS_DIR" "MTP/*.gguf"
  if [[ ! -s "${MODELS_DIR}/${MTP_FILE}" ]]; then
    found="$(find "${MODELS_DIR}/MTP" -maxdepth 1 -iname '*.gguf' 2>/dev/null | head -1 || true)"
    [[ -n "$found" ]] || die "MTP drafter download failed"
    mv "$found" "${MODELS_DIR}/${MTP_FILE}"
    rmdir "${MODELS_DIR}/MTP" 2>/dev/null || true
  fi
  ok "MTP drafter downloaded"
fi

if [[ "$WITH_ROUTER" == "1" ]]; then
  step "FunctionGemma-270M router decoder → ${ROUTER_DIR}"
  if compgen -G "${ROUTER_DIR}/*.gguf" >/dev/null 2>&1; then
    ok "router model already present — skipping"
  else
    log "Downloading ${ROUTER_REPO} (GGUF)…"
    hf_download "$ROUTER_REPO" "$ROUTER_DIR" "*.gguf"
    ok "router model downloaded"
  fi
fi

step "Done"
ok "brain:   ${MODELS_DIR}/${BRAIN_FILE}"
ok "drafter: ${MODELS_DIR}/${MTP_FILE}"
printf '%b\n' "  ${C_DIM}llama-server.service loads these two paths — see scripts/setup/systemd/README.md${C_NC}"
