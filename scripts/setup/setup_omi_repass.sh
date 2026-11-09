#!/bin/bash
# setup_omi_repass.sh - build the pendant night re-pass runtime: a dedicated venv OUTSIDE the repo + the pinned models. Idempotent; safe to re-run.
# Nothing here touches zoe-data's venv, the live services, or any .env. NOT run by the window or CI: the operator runs it once.
#
#   scripts/setup/setup_omi_repass.sh                      venv from the pinned requirements + the pinned model snapshots (needs network ONCE)
#   scripts/setup/setup_omi_repass.sh --seed-from DIR      copy already-downloaded model dirs (e.g. the bake-off's ~/.zoe/lab/omi-retranscribe-r2/models) instead of downloading;
#                                                          every weights file is VERIFIED against the pinned sha256 first - a mismatch aborts, nothing is installed from it
#   scripts/setup/setup_omi_repass.sh --check              report what is installed; exits NON-ZERO on a missing/drifted package, a torch that cannot see the GPU, a missing model or a weights
#                                                          file whose sha256 is not the pinned one (read-only; offline; --allow-cpu accepts a torch without GPU)
#
# Runtime: python3.10, an ISOLATED venv (virtualenv) from scripts/setup/omi_repass_requirements.lock.txt (top-level pins) + omi_repass_constraints.txt (transitive version pins from the
#          bake-off venvs; version-pinned over TLS, NOT hash-locked). torch is the Jetson CUDA build already in the user site (~/.local/lib/python3.10/site-packages, torch 2.8.0 / CUDA 12.6),
#          seen by the venv through a zz_usersite.pth (venv packages win, torch comes from there).
# Models (OFFICIAL repos, PINNED revisions + the sha256 of each weights file, from the repo's LFS metadata at that revision; run time is HF_HUB_OFFLINE=1):
#   Qwen/Qwen3-ASR-1.7B-hf            default ASR (Apache-2.0)                  -> $MODELS/Qwen3-ASR-1.7B-hf
#   nvidia/parakeet-tdt-0.6b-v3       production second engine (CC-BY-4.0: attribution) -> $MODELS/parakeet-tdt-0.6b-v3
#   ibm-granite/granite-speech-5.0-2b evaluation contender (Apache-2.0)         -> $MODELS/granite-speech-5.0-2b
#   Systran/faster-whisper-large-v3 (evaluation contender, needs the CUDA CTranslate2 build), Systran/faster-distil-whisper-large-v3 (CPU last resort), speechbrain/spkrec-ecapa-voxceleb
#                                     (speaker windows): HF cache, pinned revision. Systran is the only org the Whisper weights may come from.
# Licences: transformers / speechbrain / faster-whisper / ctranslate2 Apache-2.0 or MIT.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ZOE_OMI_REPASS_VENV:-$HOME/.zoe/venvs/omi-repass}"
MODELS="${ZOE_OMI_REPASS_MODELS:-$HOME/.zoe/models/omi-repass}"
REQ="$HERE/omi_repass_requirements.lock.txt"
CON="$HERE/omi_repass_constraints.txt"
USERSITE="${ZOE_OMI_REPASS_USERSITE:-$HOME/.local/lib/python3.10/site-packages}"
# dir | repo | revision | weights file | sha256 of that file at that revision
PINS=(
  "Qwen3-ASR-1.7B-hf|Qwen/Qwen3-ASR-1.7B-hf|bcd2b5b7f32b480ab5790554cfa8347f246a14f3|model.safetensors|2db53c7d81bd9b8cbc6a074e89be2c968a0d373fb4ee68bb1b1e14f7042dfee1"
  "parakeet-tdt-0.6b-v3|nvidia/parakeet-tdt-0.6b-v3|541d1f99c6b0c3cd0b11a95167540bb8edefd82b|model.safetensors|3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11"
  "granite-speech-5.0-2b|ibm-granite/granite-speech-5.0-2b|ec49ca6b1a5e7e9176127e09cb303ab78cbacdf7|model.safetensors|4151269881d2dd825d45ca75d6552b2a20e6e39d0934ed84c4db826af8bf6d88"
)
# repo | revision | weights file | sha256 (the HF cache snapshots: the same pinned-revision + weights-hash rule as PINS)
HF_PINS=(
  "Systran/faster-whisper-large-v3|edaa852ec7e145841d8ffdb056a99866b5f0a478|model.bin|69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
  "Systran/faster-distil-whisper-large-v3|c3058b475261292e64a0412df1d2681c06260fab|model.bin|b79368e19b6623813609431a6e5ee309a71506701ebc49fd7820e692dec7c5f5"
  "speechbrain/spkrec-ecapa-voxceleb|0f99f2d0ebe89ac095bcc5903c4dd8f72b367286|embedding_model.ckpt|0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2"
)
MODE=install; SEED=""; ALLOW_CPU=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --allow-cpu) ALLOW_CPU=1 ;;
    --seed-from) SEED="${2:?--seed-from needs a directory}"; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

sha_ok() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]; }
# the HF-cache snapshot directory of a pinned repo@revision (offline), or empty
hf_snap() { HF_HUB_OFFLINE=1 "$VENV/bin/python" -c "
from huggingface_hub import snapshot_download as s
print(s('$1', revision='$2', local_files_only=True))" 2>/dev/null || true; }

check() {
  local rc=0 spec dir repo rev file sha snap
  if [ -x "$VENV/bin/python" ]; then echo "venv      ok  $VENV"; else echo "venv      MISSING  $VENV"; return 1; fi
  ZOE_CHECK_ALLOW_CPU="$ALLOW_CPU" "$VENV/bin/python" - <<'PY' || rc=1
import importlib.metadata as m, os
want = {"transformers": "5.17.0", "faster-whisper": "1.2.1", "ctranslate2": "4.8.5", "speechbrain": "1.1.2", "torch": "2.8.0", "torchaudio": "2.8.0"}
bad = 0
for k, v in want.items():
    try:
        got = m.version(k)
    except Exception:
        print(f"package   MISSING  {k}=={v}"); bad = 1; continue
    ok = got.split("+")[0] == v
    bad |= 0 if ok else 1
    print(f"package   {'ok ' if ok else 'DRIFT'} {k} {got} (pinned {v})")
try:
    import torch
    if torch.cuda.is_available():
        print("gpu       ok  torch sees the GPU (" + torch.cuda.get_device_name(0) + ")")
    else:
        print("gpu       NO   torch.cuda.is_available() is False: the GPU backends would run on the CPU (check ZOE_OMI_REPASS_USERSITE / the pth)")
        bad |= 0 if os.environ.get("ZOE_CHECK_ALLOW_CPU") == "1" else 1
    from transformers import AutoModelForMultimodalLM, AutoModelForSpeechSeq2Seq, AutoModelForTDT, AutoProcessor  # noqa: F401
    import faster_whisper.vad, speechbrain.inference.speaker  # noqa: F401
    print("imports   ok  transformers (Qwen3 / Parakeet / Granite classes), silero VAD, speechbrain")
except Exception as exc:
    print(f"imports   FAILED {type(exc).__name__}: {str(exc)[:120]}"); bad = 1
raise SystemExit(bad)
PY
  for spec in "${PINS[@]}"; do
    IFS='|' read -r dir repo rev file sha <<< "$spec"
    if sha_ok "$MODELS/$dir/$file" "$sha"; then
      echo "model     ok  $dir ($(du -sh "$MODELS/$dir" | cut -f1)) $file sha256 = the pinned ${rev:0:12}'s"
    elif [ -f "$MODELS/$dir/$file" ]; then
      echo "model     MISMATCH  $dir/$file is not the pinned ${rev:0:12} weights (sha256 differs)"; rc=1
    else
      echo "model     MISSING  $MODELS/$dir"; rc=1
    fi
  done
  for spec in "${HF_PINS[@]}"; do
    IFS='|' read -r repo rev file sha <<< "$spec"
    snap="$(hf_snap "$repo" "$rev")"
    if [ -n "$snap" ] && sha_ok "$snap/$file" "$sha"; then
      echo "model     ok  $repo $file sha256 = the pinned ${rev:0:12}'s"
    elif [ -n "$snap" ] && [ -f "$snap/$file" ]; then
      echo "model     MISMATCH  $repo $file is not the pinned ${rev:0:12} weights (sha256 differs)"; rc=1
    else
      echo "model     MISSING  $repo@${rev:0:12}"; rc=1
    fi
  done
  return $rc
}
if [ "$MODE" = check ]; then check; exit $?; fi

# --seed-from: VERIFY BEFORE ANYTHING IS INSTALLED OR COPIED
if [ -n "$SEED" ]; then
  for spec in "${PINS[@]}"; do
    IFS='|' read -r dir repo rev file sha <<< "$spec"
    [ -d "$SEED/$dir" ] || continue
    sha_ok "$SEED/$dir/$file" "$sha" || { echo "REFUSED: $SEED/$dir/$file is not the pinned $repo@${rev:0:12} weights (sha256 mismatch or missing); nothing was installed" >&2; exit 1; }
  done
fi
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  /usr/bin/python3 -m virtualenv "$VENV" 2>/dev/null || { rm -rf "$VENV"; /usr/bin/python3 -m venv "$VENV"; }     # this box has no ensurepip: virtualenv first (the lab's way)
fi
SITE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
[ -d "$USERSITE/torch" ] || { echo "no torch in $USERSITE: the Jetson CUDA torch must be installed in the user site first (set ZOE_OMI_REPASS_USERSITE)" >&2; exit 1; }
echo "$USERSITE" > "$SITE/zz_usersite.pth"                      # last on the path: the venv's own pins win, torch comes from the user site
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REQ" -c "$CON"
mkdir -p "$MODELS"
for spec in "${PINS[@]}"; do
  IFS='|' read -r dir repo rev file sha <<< "$spec"
  if sha_ok "$MODELS/$dir/$file" "$sha"; then continue; fi
  if [ -n "$SEED" ] && [ -d "$SEED/$dir" ]; then
    rm -rf "$MODELS/$dir"; cp -a "$SEED/$dir" "$MODELS/$dir"
  else
    allow=(); [ "$dir" = parakeet-tdt-0.6b-v3 ] && allow=('*.json' model.safetensors '*.model' '*.txt' '*.vocab')
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 "$VENV/bin/python" -c "
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3], allow_patterns=sys.argv[4:] or None, token=False)" "$repo" "$rev" "$MODELS/$dir" "${allow[@]}"
  fi
  sha_ok "$MODELS/$dir/$file" "$sha" || { echo "REFUSED: $MODELS/$dir/$file does not match the pinned sha256 after install" >&2; exit 1; }
  echo "$rev" > "$MODELS/$dir/.revision"
done
for spec in "${HF_PINS[@]}"; do
  IFS='|' read -r repo rev file sha <<< "$spec"
  "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; s('$repo', revision='$rev')"
  snap="$(hf_snap "$repo" "$rev")"
  sha_ok "$snap/$file" "$sha" || { echo "REFUSED: $repo $file does not match the pinned sha256 after download" >&2; exit 1; }
done
check
