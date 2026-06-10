#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
GRAPHIFY_BIN="${GRAPHIFY_BIN:-/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify}"
MODE="${1:-}"
LOCK_FILE="${TMPDIR:-/tmp}/zoe-graphify-refresh.lock"
ERROR_MARKER="graphify-out/.last_refresh_error"

log() {
  printf '[graphify-refresh] %s\n' "$*"
}

fail() {
  log "$*"
  printf '%s %s\n' "$(date -Is)" "$*" >"$ROOT/$ERROR_MARKER" 2>/dev/null || true
  exit 1
}

cd "$ROOT"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another refresh is already running; exiting"
  exit 0
fi

if [[ ! -x "$GRAPHIFY_BIN" ]]; then
  fail "graphify executable not found: $GRAPHIFY_BIN"
fi

if [[ ! -f graphify-out/GRAPH_REPORT.md ]]; then
  fail "missing graphify-out/GRAPH_REPORT.md"
fi

# Refresh from a clean snapshot of the latest committed state so the live
# working tree can stay dirty / on a feature branch without blocking the
# nightly refresh (the old dirty-tree guard failed almost every night).
REF="origin/main"
if ! git fetch --quiet origin main; then
  fail "git fetch origin main failed; refusing to build the graph from unfetched state"
fi
current_head="$(git rev-parse --short=8 "$REF")"

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
  rm -f "$ERROR_MARKER"
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
  fail "OPENAI_API_KEY is required; set it in the environment or .env"
fi
export OPENAI_API_KEY

SNAPSHOT_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/zoe-graphify-snapshot.XXXXXX")"
SNAPSHOT_DIR="$SNAPSHOT_PARENT/src"

cleanup() {
  git worktree remove --force "$SNAPSHOT_DIR" >/dev/null 2>&1 || true
  rm -rf "$SNAPSHOT_PARENT"
  git worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "creating snapshot worktree at $SNAPSHOT_DIR from $REF ($current_head)"
git worktree add --detach "$SNAPSHOT_DIR" "$REF" >/dev/null

# Reuse the LLM extraction cache so unchanged files are not re-billed.
if [[ -d graphify-out/cache ]]; then
  cp -a graphify-out/cache "$SNAPSHOT_DIR/graphify-out/"
fi

log "refreshing graphify graph for $current_head"
if ! (cd "$SNAPSHOT_DIR" && "$GRAPHIFY_BIN" extract . --backend openai && "$GRAPHIFY_BIN" cluster-only . --no-viz); then
  fail "graphify extract/cluster failed for $current_head"
fi

log "syncing refreshed graph back to $ROOT/graphify-out"
rsync -a "$SNAPSHOT_DIR/graphify-out/" "$ROOT/graphify-out/"

rm -f "$ERROR_MARKER"
log "graphify refresh complete for $current_head"
