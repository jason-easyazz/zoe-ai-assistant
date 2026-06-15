#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
HOOK_PATH="$ROOT/.git/hooks/post-merge"

mkdir -p "$(dirname "$HOOK_PATH")"
cat >"$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

nohup env ZOE_ASSISTANT_ROOT="$ROOT" GRAPHIFY_REF=HEAD \
  "$ROOT/scripts/maintenance/refresh_graphify.sh" \
  >>"${TMPDIR:-/tmp}/zoe-graphify-post-merge.log" 2>&1 &

if [[ -x "$ROOT/scripts/maintenance/zoe_post_merge_latency_probe.py" ]]; then
  nohup "$ROOT/scripts/maintenance/zoe_post_merge_latency_probe.py" \
    >>"${TMPDIR:-/tmp}/zoe-post-merge-health.log" 2>&1 &
fi
HOOK
chmod +x "$HOOK_PATH"
printf 'installed Zoe Graphify post-merge hook at %s\n' "$HOOK_PATH"
