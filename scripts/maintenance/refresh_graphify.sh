#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
GRAPHIFY_BIN="${GRAPHIFY_BIN:-/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify}"
MODE="${1:-}"
LOCK_FILE="${TMPDIR:-/tmp}/zoe-graphify-refresh.lock"
ERROR_MARKER="graphify-out/.last_refresh_error"
REF="${GRAPHIFY_REF:-origin/main}"
DEFAULT_OPENROUTER_MODEL="openai/gpt-4.1-mini"
FAIL_PATTERN='insufficient[_ -]?quota|quota exceeded|rate[_ -]?limit|unauthori[sz]ed|invalid api key|authentication|provider error|payment required|HTTP[ /]*(401|402|403|429)'

log() {
  printf '[graphify-refresh] %s\n' "$*"
}

fail() {
  log "$*"
  printf '%s %s\n' "$(date -Is)" "$*" >"$ROOT/$ERROR_MARKER" 2>/dev/null || true
  exit 1
}

read_env_key() {
  local file="$1"
  local key="$2"
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$key" <<'INNER_PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
wanted = sys.argv[2]
for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    if key.strip() == wanted:
        print(value.strip().strip('"').strip("'"))
        break
INNER_PY
}

load_env_key() {
  local key="$1"
  local current="${!key:-}"
  local value=""
  if [[ -n "$current" ]]; then
    return 0
  fi
  value="$(read_env_key "$ROOT/.env" "$key")"
  if [[ -z "$value" ]]; then
    value="$(read_env_key "${HOME:-/home/zoe}/.hermes/.env" "$key")"
  fi
  if [[ -n "$value" ]]; then
    export "$key=$value"
  fi
}

scan_failure_log() {
  local log_file="$1"
  [[ -f "$log_file" ]] || return 0
  if grep -Eiq "$FAIL_PATTERN" "$log_file"; then
    return 1
  fi
  return 0
}

normalize_snapshot_paths() {
  local graph_dir="$1"
  local snapshot_dir="$2"
  local root_dir="$3"
  python3 - "$graph_dir" "$snapshot_dir" "$root_dir" <<'INNER_PY'
from pathlib import Path
import sys

graph_dir = Path(sys.argv[1])
snapshot = sys.argv[2]
root = sys.argv[3]
if not snapshot or not graph_dir.exists():
    raise SystemExit(0)
for path in graph_dir.rglob("*"):
    if not path.is_file():
        continue
    try:
        data = path.read_bytes()
    except OSError:
        continue
    needle = snapshot.encode()
    if needle not in data:
        continue
    path.write_bytes(data.replace(needle, root.encode()))
INNER_PY
}

cd "$ROOT"

on_unexpected_error() {
  log "unexpected failure at line $1"
  printf '%s unexpected failure at line %s\n' "$(date -Is)" "$1" >"$ROOT/$ERROR_MARKER" 2>/dev/null || true
}
trap 'on_unexpected_error $LINENO' ERR

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

if ! git fetch --quiet origin main; then
  fail "git fetch origin main failed; refusing to build the graph from unfetched state"
fi
current_head="$(git rev-parse --short=8 "$REF")"

built_head="$(python3 - <<'INNER_PY'
from pathlib import Path
import re

report = Path("graphify-out/GRAPH_REPORT.md").read_text(encoding="utf-8", errors="ignore")
match = re.search(r"Built from commit:\s*`?([0-9a-fA-F]{7,40})`?", report)
print(match.group(1)[:8] if match else "")
INNER_PY
)"

report_age_seconds="$(python3 - <<'INNER_PY'
from pathlib import Path
import time

path = Path("graphify-out/GRAPH_REPORT.md")
print(int(time.time() - path.stat().st_mtime))
INNER_PY
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

for key in OPENROUTER_API_KEY OPENAI_API_KEY GRAPHIFY_BACKEND GRAPHIFY_MODEL GRAPHIFY_OPENROUTER_MODEL; do
  load_env_key "$key"
done

if [[ -z "${GRAPHIFY_BACKEND:-}" ]]; then
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    GRAPHIFY_BACKEND="openrouter"
  elif [[ -n "${OPENAI_API_KEY:-}" ]]; then
    GRAPHIFY_BACKEND="openai"
  else
    GRAPHIFY_BACKEND="openrouter"
  fi
fi

case "$GRAPHIFY_BACKEND" in
  openrouter)
    if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
      fail "OPENROUTER_API_KEY is required for Graphify backend openrouter; set it in .env or ~/.hermes/.env"
    fi
    export OPENROUTER_API_KEY
    export GRAPHIFY_OPENROUTER_MODEL="${GRAPHIFY_OPENROUTER_MODEL:-${GRAPHIFY_MODEL:-$DEFAULT_OPENROUTER_MODEL}}"
    ;;
  openai)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      fail "OPENAI_API_KEY is required for Graphify backend openai; set it in .env or ~/.hermes/.env"
    fi
    export OPENAI_API_KEY
    ;;
  *)
    if [[ -n "${GRAPHIFY_MODEL:-}" ]]; then
      export GRAPHIFY_MODEL
    fi
    ;;
esac
export GRAPHIFY_BACKEND

SNAPSHOT_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/zoe-graphify-snapshot.XXXXXX")"
SNAPSHOT_DIR="$SNAPSHOT_PARENT/src"
RUN_LOG="$SNAPSHOT_PARENT/graphify-refresh.log"

cleanup() {
  git worktree remove --force "$SNAPSHOT_DIR" >/dev/null 2>&1 || true
  rm -rf "$SNAPSHOT_PARENT"
  git worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "creating snapshot worktree at $SNAPSHOT_DIR from $REF ($current_head)"
if ! git worktree add --detach "$SNAPSHOT_DIR" "$REF" >/dev/null; then
  fail "git worktree add failed for $REF at $SNAPSHOT_DIR"
fi

if [[ -d graphify-out/cache ]]; then
  mkdir -p "$SNAPSHOT_DIR/graphify-out"
  cp -a graphify-out/cache "$SNAPSHOT_DIR/graphify-out/"
fi

log "refreshing graphify graph for $current_head with backend $GRAPHIFY_BACKEND"
if ! (cd "$SNAPSHOT_DIR" && "$GRAPHIFY_BIN" extract . --backend "$GRAPHIFY_BACKEND" && "$GRAPHIFY_BIN" cluster-only . --no-viz) >"$RUN_LOG" 2>&1; then
  cat "$RUN_LOG" >&2 || true
  fail "graphify extract/cluster failed for $current_head using backend $GRAPHIFY_BACKEND"
fi
if ! scan_failure_log "$RUN_LOG"; then
  cat "$RUN_LOG" >&2 || true
  fail "graphify output contained quota/auth/provider failure text for $current_head using backend $GRAPHIFY_BACKEND"
fi

if [[ ! -s "$SNAPSHOT_DIR/graphify-out/graph.json" ]]; then
  fail "graphify produced missing or empty graph.json for $current_head"
fi
if [[ ! -s "$SNAPSHOT_DIR/graphify-out/GRAPH_REPORT.md" ]]; then
  fail "graphify produced missing or empty GRAPH_REPORT.md for $current_head"
fi

normalize_snapshot_paths "$SNAPSHOT_DIR/graphify-out" "$SNAPSHOT_DIR" "$ROOT"

log "syncing refreshed graph back to $ROOT/graphify-out"
if ! rsync -a --delete "$SNAPSHOT_DIR/graphify-out/" "$ROOT/graphify-out/"; then
  fail "rsync of refreshed graph back to $ROOT/graphify-out failed; live copy may be partial"
fi

rm -f "$ERROR_MARKER"
log "graphify refresh complete for $current_head using backend $GRAPHIFY_BACKEND"
