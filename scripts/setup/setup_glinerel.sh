#!/bin/bash
# setup_glinerel.sh - build the night-extract runtime: a dedicated venv OUTSIDE the repo + the pinned model in a fixed offline cache.
# Idempotent; safe to re-run. Nothing here touches zoe-data's venv, the live services, or any .env.
#
#   scripts/setup/setup_glinerel.sh             venv + glinerel-fast==0.1.1 + model download (needs network ONCE)
#   scripts/setup/setup_glinerel.sh --check     report what is installed (read-only)
#   scripts/setup/setup_glinerel.sh --seed-from DIR   copy an existing HF hub cache for the model instead of downloading
#
# Runtime: system python3 (3.10) + its torch via --system-site-packages (the same way the lab ran it), glinerel-fast installed --no-deps.
# Model: VAGOsolutions/SauerkrautLM-GLiNEREL-128m @ the pinned revision, cache ~/.zoe/models/glinerel/hub; at run time HF_HUB_OFFLINE=1.
# Licence: the weights are LFM Open License v1.0 (free under USD 10M revenue; not Apache). The glinerel-fast package is Apache-2.0.
set -euo pipefail
VENV="${ZOE_NIGHT_EXTRACT_VENV:-$HOME/.zoe/venvs/glinerel}"
CACHE="${HF_HUB_CACHE:-$HOME/.zoe/models/glinerel/hub}"
PKG="glinerel-fast==0.1.1"
REPO_ID="VAGOsolutions/SauerkrautLM-GLiNEREL-128m"
REV="5b8448f983c2bc4058ea6359ec67589cd41422b9"
MODE=install; SEED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --seed-from) SEED="${2:?--seed-from needs a directory}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

check() {
  [ -x "$VENV/bin/python" ] && echo "venv      ok  $VENV" || echo "venv      MISSING  $VENV"
  "$VENV/bin/python" -c "import importlib.metadata as m; print('package   ok ', m.version('glinerel-fast'))" 2>/dev/null || echo "package   MISSING  $PKG"
  HF_HUB_OFFLINE=1 HF_HUB_CACHE="$CACHE" "$VENV/bin/python" -c "
from huggingface_hub import snapshot_download as s
print('model     ok ', s('$REPO_ID', revision='$REV', local_files_only=True))" 2>/dev/null || echo "model     MISSING  $CACHE"
}
if [ "$MODE" = check ]; then check; exit 0; fi

if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  # virtualenv first (the lab's way; this box has no python3-venv/ensurepip), the stdlib venv as the fallback
  /usr/bin/python3 -m virtualenv --system-site-packages "$VENV" 2>/dev/null || { rm -rf "$VENV"; /usr/bin/python3 -m venv --system-site-packages "$VENV"; }
fi
"$VENV/bin/python" -c "import torch" 2>/dev/null || { echo "system torch is not importable; the venv reuses it and will not install another" >&2; exit 1; }
if ! "$VENV/bin/python" -c "import importlib.metadata as m,sys; sys.exit(0 if m.version('glinerel-fast')=='0.1.1' else 1)" 2>/dev/null; then
  "$VENV/bin/python" -m pip install --no-deps "$PKG"
fi
mkdir -p "$CACHE"
if [ -n "$SEED" ]; then
  cp -a "$SEED/models--${REPO_ID/\//--}" "$CACHE"/
elif ! HF_HUB_OFFLINE=1 HF_HUB_CACHE="$CACHE" "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; s('$REPO_ID', revision='$REV', local_files_only=True)" 2>/dev/null; then
  HF_HUB_CACHE="$CACHE" "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; s('$REPO_ID', revision='$REV')"
fi
check
