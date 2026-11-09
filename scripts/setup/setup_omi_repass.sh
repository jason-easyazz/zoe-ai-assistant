#!/bin/bash
# setup_omi_repass.sh - build the pendant night re-pass runtime: a dedicated venv OUTSIDE the repo + the pinned models in the HF cache.
# Idempotent; safe to re-run. Nothing here touches zoe-data's venv, the live services, or any .env. NOT run by the window or CI: the operator runs it once.
#
#   scripts/setup/setup_omi_repass.sh             venv from the pinned lock + the pinned model snapshots (needs network ONCE)
#   scripts/setup/setup_omi_repass.sh --check     report what is installed (read-only; offline)
#
# Runtime: python3.10, an ISOLATED venv (no system site-packages) from scripts/setup/omi_repass_requirements.lock.txt - the exact set the 2026-11-09 bake-off
#          ran (faster-whisper 1.2.1, ctranslate2 4.8.5 CPU wheel, speechbrain 1.1.2, torch 2.14.1). Big (about 5.6 GB: torch ships its CUDA libraries) and
#          deliberately separate, so no wheel here can move a pin zoe-data depends on.
# Models:  PINNED snapshots, downloaded once into the HF cache, then HF_HUB_OFFLINE=1 at run time:
#            Systran/faster-whisper-large-v3                edaa852ec7e145841d8ffdb056a99866b5f0a478   (the ASR weights come ONLY from the Systran org)
#            Systran/faster-distil-whisper-large-v3         c3058b475261292e64a0412df1d2681c06260fab   (the budget fallback)
#            speechbrain/spkrec-ecapa-voxceleb              0f99f2d0ebe89ac095bcc5903c4dd8f72b367286   (the speaker embedder the bake-off scored)
# Licences: faster-whisper MIT, CTranslate2 MIT, speechbrain Apache-2.0, Whisper weights MIT, ECAPA VoxCeleb weights Apache-2.0.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ZOE_OMI_REPASS_VENV:-$HOME/.zoe/venvs/omi-repass}"
LOCK="$HERE/omi_repass_requirements.lock.txt"
MODELS=(
  "Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478"
  "Systran/faster-distil-whisper-large-v3 c3058b475261292e64a0412df1d2681c06260fab"
  "speechbrain/spkrec-ecapa-voxceleb 0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
)
MODE=install
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

check() {
  local rc=0
  if [ -x "$VENV/bin/python" ]; then echo "venv      ok  $VENV"; else echo "venv      MISSING  $VENV"; return 1; fi
  "$VENV/bin/python" - <<'PY' || rc=1
import importlib.metadata as m
want = {"faster-whisper": "1.2.1", "ctranslate2": "4.8.5", "speechbrain": "1.1.2", "torch": "2.14.1"}
for k, v in want.items():
    try:
        got = m.version(k)
    except Exception:
        print(f"package   MISSING  {k}=={v}"); raise SystemExit(1)
    print(f"package   {'ok ' if got == v else 'DRIFT'} {k} {got} (pinned {v})")
PY
  for spec in "${MODELS[@]}"; do
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
  # virtualenv first (this box has no python3-venv/ensurepip - the lab's way), the stdlib venv as the fallback
  /usr/bin/python3 -m virtualenv "$VENV" 2>/dev/null || { rm -rf "$VENV"; /usr/bin/python3 -m venv "$VENV"; }
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$LOCK"
for spec in "${MODELS[@]}"; do
  set -- $spec
  "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; s('$1', revision='$2')"
done
check
