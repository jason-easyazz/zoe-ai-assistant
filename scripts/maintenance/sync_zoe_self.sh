#!/usr/bin/env bash
# sync_zoe_self.sh — Trigger agent_sync to rebuild live architecture docs.
# and distribute them to all agents from live service state.
#
# Usage:
#   bash scripts/maintenance/sync_zoe_self.sh [--url http://localhost:8000]
#
# The script calls POST /api/system/agent-sync on the running zoe-data service.
# This triggers run_agent_sync(), which:
#   1. Rebuilds ZOE_SELF.md from service introspection
#      → written to ~/.openclaw/workspace/ZOE_SELF.md
#   2. Patches the ZOE_SELF_BEGIN block in /home/zoe/.hermes/SOUL.md
#   3. Writes ~/.zoe/zoe_self_compact.txt (compact 500-char context prefix)
#   4. Updates CAPABILITIES.md with current MCP tool list
#      → written to ~/assistant/CAPABILITIES.md
#   5. Writes ~/.openclaw/workspace/FEDERATION_SKILLS.md with live skill counts per agent

set -euo pipefail

ZOE_URL="${1:-${ZOE_URL:-http://localhost:8000}}"
if [[ "${1:-}" == "--url" ]]; then
    ZOE_URL="$2"
fi

echo "▶ Triggering agent sync at ${ZOE_URL}/api/system/agent-sync ..."

# Load session token from .env if available
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../../services/zoe-data/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
    set +a
fi

HTTP_CODE=$(curl -s -o /tmp/agent_sync_response.json -w "%{http_code}" \
    -X POST "${ZOE_URL}/api/system/agent-sync" \
    -H "X-Session-ID: ${ZOE_SERVICE_SESSION:-}" \
    2>/dev/null || true)
HTTP_CODE="${HTTP_CODE:-000}"

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "✓ Agent sync complete."
    cat /tmp/agent_sync_response.json 2>/dev/null | python3 -m json.tool 2>/dev/null || true
elif [[ "$HTTP_CODE" == "403" ]]; then
    echo "↪ Agent sync endpoint requires admin; running local agent_sync fallback..."
    (
        cd "${SCRIPT_DIR}/../../services/zoe-data"
        python3 - <<'PY'
import asyncio
import json
from agent_sync import run_agent_sync

print(json.dumps(asyncio.run(run_agent_sync()), indent=2))
PY
    )
elif [[ "$HTTP_CODE" == "000" ]]; then
    echo "↪ Could not connect to ${ZOE_URL}; running local agent_sync fallback..."
    (
        cd "${SCRIPT_DIR}/../../services/zoe-data"
        python3 - <<'PY'
import asyncio
import json
from agent_sync import run_agent_sync

print(json.dumps(asyncio.run(run_agent_sync()), indent=2))
PY
    )
else
    echo "✗ Agent sync returned HTTP ${HTTP_CODE}:"
    cat /tmp/agent_sync_response.json 2>/dev/null || true
    exit 1
fi
