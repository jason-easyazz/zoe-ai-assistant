#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
GRAPHIFY_BIN="${GRAPHIFY_BIN:-/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify}"
GRAPHIFY_PYTHON="${GRAPHIFY_PYTHON:-/home/zoe/.local/share/uv/tools/graphifyy/bin/python}"
MODE="${1:-}"
LOCK_FILE="${TMPDIR:-/tmp}/zoe-graphify-refresh.lock"
ERROR_MARKER="graphify-out/.last_refresh_error"
REF="origin/main"

log() { printf '[graphify-refresh] %s\n' "$*"; }

fail() {
  log "$*"
  printf '%s %s\n' "$(date -Is)" "$*" >"$ROOT/$ERROR_MARKER" 2>/dev/null || true
  exit 1
}

load_env_value() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    return 0
  fi
  local value
  value="$(python3 - "$name" <<'READ_DOTENV_VALUE'
from pathlib import Path
import sys
wanted = sys.argv[1]
for env_path in (Path('.env'), Path.home() / '.hermes' / '.env'):
    if not env_path.exists():
        continue
    for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() == wanted:
            print(value.strip().strip('"').strip("'"))
            raise SystemExit
READ_DOTENV_VALUE
)"
  if [[ -n "$value" ]]; then
    export "$name=$value"
  fi
}

cd "$ROOT"
trap 'fail "unexpected failure at line $LINENO"' ERR

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another refresh is already running; exiting"
  exit 0
fi

[[ -x "$GRAPHIFY_BIN" ]] || fail "graphify executable not found: $GRAPHIFY_BIN"
[[ -x "$GRAPHIFY_PYTHON" ]] || fail "graphify python not found: $GRAPHIFY_PYTHON"
[[ -f graphify-out/GRAPH_REPORT.md ]] || fail "missing graphify-out/GRAPH_REPORT.md"

if ! git fetch --quiet origin main; then
  fail "git fetch origin main failed; refusing to build the graph from unfetched state"
fi
current_head="$(git rev-parse --short=8 "$REF")"
built_head="$(python3 - <<'PY'
from pathlib import Path
import re
report = Path('graphify-out/GRAPH_REPORT.md').read_text(encoding='utf-8', errors='ignore')
match = re.search(r'Built from commit:\s*`?([0-9a-fA-F]{7,40})`?', report)
print(match.group(1)[:8] if match else '')
PY
)"
report_age_seconds="$(python3 - <<'PY'
from pathlib import Path
import time
print(int(time.time() - Path('graphify-out/GRAPH_REPORT.md').stat().st_mtime))
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

for env_name in OPENROUTER_API_KEY GRAPHIFY_OPENROUTER_MODEL GEMINI_API_KEY GOOGLE_API_KEY GRAPHIFY_MAX_OUTPUT_TOKENS; do
  load_env_value "$env_name"
done
if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}" ]]; then
  fail "OPENROUTER_API_KEY or GEMINI_API_KEY is required in environment, .env, or ~/.hermes/.env"
fi
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
export GRAPHIFY_OPENROUTER_MODEL="${GRAPHIFY_OPENROUTER_MODEL:-openai/gpt-4.1-mini}"
export GRAPHIFY_MAX_OUTPUT_TOKENS="${GRAPHIFY_MAX_OUTPUT_TOKENS:-}"

SNAPSHOT_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/zoe-graphify-snapshot.XXXXXX")"
SNAPSHOT_DIR="$SNAPSHOT_PARENT/src"
cleanup() {
  git worktree remove --force "$SNAPSHOT_DIR" >/dev/null 2>&1 || true
  rm -rf "$SNAPSHOT_PARENT"
  git worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "creating snapshot worktree at $SNAPSHOT_DIR from $REF ($current_head)"
git worktree add --detach "$SNAPSHOT_DIR" "$REF" >/dev/null || fail "git worktree add failed for $REF at $SNAPSHOT_DIR"

if [[ -d graphify-out/cache ]]; then
  mkdir -p "$SNAPSHOT_DIR/graphify-out"
  cp -a graphify-out/cache "$SNAPSHOT_DIR/graphify-out/"
fi

GRAPHIFY_RUN_LOG="$SNAPSHOT_PARENT/graphify-refresh.log"
OPENROUTER_CLI="$ROOT/scripts/maintenance/graphify_openrouter_cli.py"
PROVIDER_ERROR_PATTERN="Error code:|insufficient_quota|more credits|rate[_ -]?limit|authentication|invalid api key|permission_denied|chunk .* failed|semantic extraction failed"

run_graphify_provider() {
  local provider="$1"
  : >"$GRAPHIFY_RUN_LOG"
  if [[ "$provider" == "openrouter" ]]; then
    log "refreshing graphify graph for $current_head via OpenRouter (${GRAPHIFY_OPENROUTER_MODEL})"
    (cd "$SNAPSHOT_DIR" && "$GRAPHIFY_PYTHON" "$OPENROUTER_CLI" extract . --backend openrouter && "$GRAPHIFY_BIN" cluster-only . --no-viz) 2>&1 | tee "$GRAPHIFY_RUN_LOG"
  else
    log "refreshing graphify graph for $current_head via Gemini"
    (cd "$SNAPSHOT_DIR" && rm -rf graphify-out/graph.json graphify-out/GRAPH_REPORT.md graphify-out/.graphify_analysis.json graphify-out/.graphify_labels.json && "$GRAPHIFY_BIN" extract . --backend gemini && "$GRAPHIFY_BIN" cluster-only . --no-viz) 2>&1 | tee "$GRAPHIFY_RUN_LOG"
  fi
}

provider_ok=0
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  if run_graphify_provider openrouter && ! grep -Eiq "$PROVIDER_ERROR_PATTERN" "$GRAPHIFY_RUN_LOG"; then
    provider_ok=1
  else
    log "OpenRouter refresh failed; trying Gemini fallback if configured"
  fi
fi
if [[ "$provider_ok" != "1" && -n "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}" ]]; then
  if run_graphify_provider gemini && ! grep -Eiq "$PROVIDER_ERROR_PATTERN" "$GRAPHIFY_RUN_LOG"; then
    provider_ok=1
  fi
fi
if [[ "$provider_ok" != "1" ]]; then
  fail "graphify backend/model error for $current_head; leaving existing graphify-out unchanged (see systemd journal output)"
fi
if [[ ! -s "$SNAPSHOT_DIR/graphify-out/graph.json" || ! -s "$SNAPSHOT_DIR/graphify-out/GRAPH_REPORT.md" ]]; then
  fail "graphify backend completed without required output files for $current_head; leaving existing graphify-out unchanged"
fi

python3 - "$SNAPSHOT_DIR" "$ROOT" <<'NORMALIZE_GRAPHIFY_PATHS'
import re
import sys
from pathlib import Path
snapshot = Path(sys.argv[1]).resolve()
root = Path(sys.argv[2]).resolve()
out = snapshot / 'graphify-out'
pattern = re.compile(r'/tmp/zoe-graphify-(?:snapshot|local-probe)\.[^/]+/(?:src|graphify-local-repo)')
for path in out.rglob('*'):
    if path.suffix not in {'.json', '.md'}:
        continue
    text = path.read_text(encoding='utf-8', errors='ignore')
    updated = pattern.sub(str(root), text).replace(str(snapshot), str(root))
    if updated != text:
        path.write_text(updated, encoding='utf-8')
NORMALIZE_GRAPHIFY_PATHS

log "syncing refreshed graph back to $ROOT/graphify-out"
rsync -a --delete "$SNAPSHOT_DIR/graphify-out/" "$ROOT/graphify-out/" || fail "rsync of refreshed graph back to $ROOT/graphify-out failed; live copy may be partial"

# Land the refreshed graph in git via an automated PR.
#
# All git work happens inside the disposable snapshot worktree ($SNAPSHOT_DIR),
# which is a pristine detached `origin/main` checkout with only graphify-out/
# regenerated. The live checkout ($ROOT) is never touched by this step, so a
# failure here leaves both the committed artifacts on origin/main and the
# freshly-synced live tree intact. The step is fail-closed: any git/gh error
# routes through `fail` (logs, writes the error marker, exits non-zero) and the
# snapshot worktree is discarded by the EXIT trap.
open_refresh_pr() {
  local branch="chore/graphify-refresh-$current_head"
  local title="chore(graphify): automated knowledge-graph refresh for $current_head"

  # Only the tracked graphify-out artifacts should ever change. Stage the
  # directory (respects .gitignore, so graphify-out/cache stays excluded) and
  # bail cleanly if it matches origin/main — no commit, no branch, no PR.
  git -C "$SNAPSHOT_DIR" add -A graphify-out >/dev/null 2>&1 \
    || fail "git add of graphify-out failed in snapshot; committed graph untouched"
  if git -C "$SNAPSHOT_DIR" diff --cached --quiet -- graphify-out; then
    log "refreshed graphify-out matches $REF for $current_head; no PR needed"
    return 0
  fi

  # Reuse an existing branch/PR for this head; never open a duplicate.
  if gh pr list --head "$branch" --state open --json number \
       --jq '.[].number' 2>/dev/null | grep -q .; then
    log "open PR for $branch already exists; skipping duplicate PR creation"
    return 0
  fi

  git -C "$SNAPSHOT_DIR" switch -C "$branch" >/dev/null 2>&1 \
    || fail "git switch -C $branch failed in snapshot; committed graph untouched"
  git -C "$SNAPSHOT_DIR" add -A graphify-out >/dev/null 2>&1 \
    || fail "git add of graphify-out failed after branch switch; committed graph untouched"

  GIT_AUTHOR_NAME="zoe-graphify-bot" GIT_AUTHOR_EMAIL="noreply@anthropic.com" \
  GIT_COMMITTER_NAME="zoe-graphify-bot" GIT_COMMITTER_EMAIL="noreply@anthropic.com" \
  git -C "$SNAPSHOT_DIR" commit --only -- graphify-out \
    -m "chore(graphify): refresh knowledge graph for $current_head" \
    -m "Automated nightly Graphify rebuild from origin/main snapshot $current_head." \
    -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" >/dev/null \
    || fail "git commit of graphify-out failed in snapshot; committed graph untouched"

  git -C "$SNAPSHOT_DIR" push --force-with-lease origin "$branch" >/dev/null 2>&1 \
    || fail "git push of $branch failed; committed graph untouched and no PR opened"

  local body
  body="Automated knowledge-graph refresh.

Rebuilt \`graphify-out/\` (graph.json + GRAPH_REPORT.md) from a clean \`origin/main\` snapshot at commit \`$current_head\` by the nightly \`zoe-graphify-refresh\` timer. This keeps the committed graph that agents read in sync with HEAD instead of drifting.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"

  if ! gh pr create --base main --head "$branch" \
       --title "$title" --body "$body" >/dev/null 2>&1; then
    # A pre-existing open PR (race) is benign; anything else is a real failure.
    if gh pr list --head "$branch" --state open --json number \
         --jq '.[].number' 2>/dev/null | grep -q .; then
      log "PR for $branch already open after push; not creating a duplicate"
      return 0
    fi
    fail "gh pr create for $branch failed; committed graph untouched"
  fi
  log "opened graphify-refresh PR for $branch (head $current_head)"
}

open_refresh_pr

rm -f "$ERROR_MARKER"
log "graphify refresh complete for $current_head"
