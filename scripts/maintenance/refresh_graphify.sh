#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
GRAPHIFY_BIN="${GRAPHIFY_BIN:-/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify}"
MODE="${1:-}"
LOCK_FILE="${TMPDIR:-/tmp}/zoe-graphify-refresh.lock"

log() {
  printf '[graphify-refresh] %s\n' "$*"
}

cd "$ROOT"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another refresh is already running; exiting"
  exit 0
fi

if [[ ! -x "$GRAPHIFY_BIN" ]]; then
  log "graphify executable not found: $GRAPHIFY_BIN"
  exit 1
fi

if [[ ! -f graphify-out/GRAPH_REPORT.md ]]; then
  log "missing graphify-out/GRAPH_REPORT.md"
  exit 1
fi

if [[ "${GRAPHIFY_ALLOW_DIRTY:-0}" != "1" ]]; then
  dirty_source="$(git status --porcelain --untracked-files=no -- . ':(exclude)graphify-out' || true)"
  if [[ -n "$dirty_source" ]]; then
    log "source tree has tracked changes outside graphify-out; commit or stash before refreshing"
    exit 2
  fi
fi

current_head="$(git rev-parse --short=8 HEAD)"
built_head="$(python3 - <<'PY'
from pathlib import Path
import re

report = Path("graphify-out/GRAPH_REPORT.md").read_text(encoding="utf-8", errors="ignore")
match = re.search(r"Built from commit:\s*`?([0-9a-fA-F]{7,40})`?", report)
print(match.group(1)[:8] if match else "")
PY
)"

report_age_seconds="$(python3 - <<'PY'
from pathlib import Path
import time

path = Path("graphify-out/GRAPH_REPORT.md")
print(int(time.time() - path.stat().st_mtime))
PY
)"

should_refresh=0
if [[ "$MODE" == "--force" ]]; then
  should_refresh=1
elif [[ -z "$built_head" || "$built_head" != "$current_head" ]]; then
  should_refresh=1
elif [[ "$MODE" == "--daily" && "$report_age_seconds" -ge 86400 ]]; then
  should_refresh=1
fi

if [[ "$should_refresh" != "1" ]]; then
  log "graph is current for $current_head; no refresh needed"
  exit 0
fi

if [[ -z "${OPENAI_API_KEY:-}" && -f .env ]]; then
  OPENAI_API_KEY="$(python3 - <<'PY'
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "OPENAI_API_KEY":
        value = value.strip().strip('"').strip("'")
        print(value)
        break
PY
)"
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  log "OPENAI_API_KEY is required; set it in the environment or .env"
  exit 1
fi
export OPENAI_API_KEY

log "refreshing graphify graph for $current_head"
"$GRAPHIFY_BIN" extract . --backend openai
"$GRAPHIFY_BIN" cluster-only . --no-viz
log "graphify refresh complete"
