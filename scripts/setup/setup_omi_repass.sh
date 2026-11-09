#!/bin/bash
# setup_omi_repass.sh - build the pendant night re-pass runtime: a dedicated venv OUTSIDE the repo + the pinned models. Idempotent; safe to re-run.
# Nothing here touches zoe-data's venv, the live services, or any .env. NOT run by the window or CI: the operator runs it once.
#
#   scripts/setup/setup_omi_repass.sh                      venv from the pinned requirements + the pinned model snapshots (needs network ONCE)
#   scripts/setup/setup_omi_repass.sh --seed-from DIR      copy already-downloaded model dirs (e.g. the bake-off's ~/.zoe/lab/omi-retranscribe-r2/models) instead of downloading
#   scripts/setup/setup_omi_repass.sh --check              report what is installed, incl. whether torch sees the GPU (read-only; offline)
#
# Runtime: python3.10, an ISOLATED venv (virtualenv) from scripts/setup/omi_repass_requirements.lock.txt (transformers 5.17.0 etc.). torch is the Jetson CUDA build that is already
#          in the user site (~/.local/lib/python3.10/site-packages, torch 2.8.0 / CUDA 12.6): the venv sees it through a zz_usersite.pth (venv packages win, torch comes from there).
# Models (OFFICIAL repos, PINNED revisions; run time is HF_HUB_OFFLINE=1):
#   Qwen/Qwen3-ASR-1.7B-hf            default ASR (Apache-2.0)                  -> $MODELS/Qwen3-ASR-1.7B-hf
#   nvidia/parakeet-tdt-0.6b-v3       budget fallback (CC-BY-4.0: attribution)  -> $MODELS/parakeet-tdt-0.6b-v3
#   Systran/faster-whisper-large-v3, Systran/faster-distil-whisper-large-v3     CPU last resort (HF cache; Systran is the only org the Whisper weights may come from)
#   speechbrain/spkrec-ecapa-voxceleb speaker windows (Apache-2.0; HF cache)
# Licences: transformers / speechbrain / faster-whisper / ctranslate2 Apache-2.0 or MIT.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ZOE_OMI_REPASS_VENV:-$HOME/.zoe/venvs/omi-repass}"
MODELS="${ZOE_OMI_REPASS_MODELS:-$HOME/.zoe/models/omi-repass}"
REQ="$HERE/omi_repass_requirements.lock.txt"
USERSITE="${ZOE_OMI_REPASS_USERSITE:-$HOME/.local/lib/python3.10/site-packages}"
QWEN_REV=bcd2b5b7f32b480ab5790554cfa8347f246a14f3
PARAKEET_REV=541d1f99c6b0c3cd0b11a95167540bb8edefd82b
HF_PINS=(
  "Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478"
  "Systran/faster-distil-whisper-large-v3 c3058b475261292e64a0412df1d2681c06260fab"
  "speechbrain/spkrec-ecapa-voxceleb 0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
)
MODE=install; SEED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --seed-from) SEED="${2:?--seed-from needs a directory}"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

check() {
  local rc=0 d n r spec
  if [ -x "$VENV/bin/python" ]; then echo "venv      ok  $VENV"; else echo "venv      MISSING  $VENV"; return 1; fi
  "$VENV/bin/python" - <<'PY' || rc=1
import importlib.metadata as m
want = {"transformers": "5.17.0", "faster-whisper": "1.2.1", "ctranslate2": "4.8.5", "speechbrain": "1.1.2", "torch": "2.8.0", "torchaudio": "2.8.0"}
bad = 0
for k, v in want.items():
    try:
        got = m.version(k)
    except Exception:
        print(f"package   MISSING  {k}=={v}"); bad = 1; continue
    print(f"package   {'ok ' if got.split('+')[0] == v else 'DRIFT'} {k} {got} (pinned {v})")
try:
    import torch
    print("gpu       " + ("ok  torch sees the GPU (" + torch.cuda.get_device_name(0) + ")" if torch.cuda.is_available() else "NO   torch.cuda.is_available() is False: the GPU backends fall back to CPU (check ZOE_OMI_REPASS_USERSITE / the pth)"))
    from transformers import AutoModelForMultimodalLM, AutoModelForTDT, AutoProcessor  # noqa: F401
    import faster_whisper.vad, speechbrain.inference.speaker  # noqa: F401
    print("imports   ok  transformers (Qwen3 / Parakeet classes), silero VAD, speechbrain")
except Exception as exc:
    print(f"imports   FAILED {type(exc).__name__}: {str(exc)[:120]}"); bad = 1
raise SystemExit(bad)
PY
  for d in "Qwen3-ASR-1.7B-hf:$QWEN_REV" "parakeet-tdt-0.6b-v3:$PARAKEET_REV"; do
    n="${d%%:*}"; r="${d##*:}"
    if [ -f "$MODELS/$n/model.safetensors" ]; then
      echo "model     ok  $n ($(du -sh "$MODELS/$n" | cut -f1)) revision $(cat "$MODELS/$n/.revision" 2>/dev/null || echo unknown) (pinned ${r:0:12})"
    else
      echo "model     MISSING  $MODELS/$n"; rc=1
    fi
  done
  for spec in "${HF_PINS[@]}"; do
    set -- $spec
    HF_HUB_OFFLINE=1 "$VENV/bin/python" -c "
from huggingface_hub import snapshot_download as s
print('model     ok ', '$1', s('$1', revision='$2', local_files_only=True).rsplit('/', 1)[-1][:12])" 2>/dev/null || { echo "model     MISSING  $1@$2"; rc=1; }
  done
  return $rc
}
if [ "$MODE" = check ]; then check; exit $?; fi

if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  /usr/bin/python3 -m virtualenv "$VENV" 2>/dev/null || { rm -rf "$VENV"; /usr/bin/python3 -m venv "$VENV"; }     # this box has no ensurepip: virtualenv first (the lab's way)
fi
SITE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
[ -d "$USERSITE/torch" ] || { echo "no torch in $USERSITE: the Jetson CUDA torch must be installed in the user site first (set ZOE_OMI_REPASS_USERSITE)" >&2; exit 1; }
echo "$USERSITE" > "$SITE/zz_usersite.pth"                      # last on the path: the venv's own pins win, torch comes from the user site
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REQ"
mkdir -p "$MODELS"
fetch() {      # fetch <repo> <dir> <rev> [allow patterns...]
  local repo="$1" dir="$2" rev="$3"; shift 3
  if [ -n "$SEED" ] && [ -d "$SEED/$dir" ]; then
    rm -rf "$MODELS/$dir"; cp -a "$SEED/$dir" "$MODELS/$dir"; echo "seeded-from-$SEED (revision not verified)" > "$MODELS/$dir/.revision"; return
  fi
  if [ "$(cat "$MODELS/$dir/.revision" 2>/dev/null)" = "$rev" ] && [ -f "$MODELS/$dir/model.safetensors" ]; then return; fi
  HF_HUB_DISABLE_IMPLICIT_TOKEN=1 "$VENV/bin/python" -c "
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], revision=sys.argv[3], local_dir=sys.argv[4] + '/' + sys.argv[2], allow_patterns=sys.argv[5:] or None, token=False)" "$repo" "$dir" "$rev" "$MODELS" "$@"
  echo "$rev" > "$MODELS/$dir/.revision"
}
fetch Qwen/Qwen3-ASR-1.7B-hf Qwen3-ASR-1.7B-hf "$QWEN_REV"
fetch nvidia/parakeet-tdt-0.6b-v3 parakeet-tdt-0.6b-v3 "$PARAKEET_REV" '*.json' model.safetensors '*.model' '*.txt' '*.vocab'
for spec in "${HF_PINS[@]}"; do
  set -- $spec
  "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; s('$1', revision='$2')"
done
check
