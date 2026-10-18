#!/usr/bin/env bash
# Weekly opensrc cache refresh (zoe-opensrc-refresh.timer; tracked 2026-10-18, was ~/bin/refresh-opensrc.sh).
# Keeps cached third-party source aligned to Zoe's *installed* versions (not the
# latest published, which `opensrc fetch <pkg>` would otherwise grab). Bare-name
# resolution drifts; pinning to importlib's installed version keeps agent context
# matched to what Zoe actually runs. The versions are read from the interpreter
# zoe-data RUNS (the B0.7 py3.12 venv: chromadb 1.5.9), not the 3.10 system site,
# which held the retired 0.6.3 pair and sent agents to the wrong source.
set -uo pipefail

OPENSRC="${OPENSRC_BIN:-$HOME/.local/bin/opensrc}"
_venv_py="$HOME/.zoe/venvs/zoe-data-py312/bin/python"
[[ -x "$_venv_py" ]] || _venv_py=/usr/bin/python3
PY="${ZOE_PYTHON:-$_venv_py}"

log() { printf '[opensrc-refresh] %s\n' "$*"; }

[[ -x "$OPENSRC" ]] || { log "FATAL: opensrc not found at $OPENSRC"; exit 1; }
command -v "$PY" >/dev/null 2>&1 || PY="python3"

rc=0

# PyPI packages kept in lockstep with the installed Zoe stack.
PKGS=(fastapi chromadb livekit faster-whisper mcp fastmcp)
for p in "${PKGS[@]}"; do
  ver="$("$PY" - "$p" <<'PYEOF'
import importlib.metadata as m, sys
try:
    print(m.version(sys.argv[1]))
except Exception:
    print("")
PYEOF
)"
  if [[ -n "$ver" ]]; then
    log "fetch pypi:$p@$ver"
    if ! "$OPENSRC" fetch "pypi:$p@$ver" --quiet; then
      log "WARN: fetch pypi:$p@$ver failed"; rc=1
    fi
  else
    log "skip $p (not installed)"
  fi
done

# Tracking-branch repos: re-fetch to refresh @main/@master HEAD.
REPOS=(
  google/a2a
  google-a2a/a2a-python
  modelcontextprotocol/python-sdk
  browser-use/browser-use
  pyannote/pyannote-audio
  ag-ui-protocol/ag-ui
  CloakHQ/cloakbrowser
  agronholm/apscheduler
)
for r in "${REPOS[@]}"; do
  log "fetch $r"
  if ! "$OPENSRC" fetch "$r" --quiet; then
    log "WARN: fetch $r failed"; rc=1
  fi
done

if [[ "$rc" -eq 0 ]]; then log "done (all fetches ok)"; else log "done (with warnings)"; fi
exit "$rc"
