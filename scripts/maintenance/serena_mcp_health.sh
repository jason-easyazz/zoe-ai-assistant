#!/usr/bin/env bash
# Health check for the SHARED Serena MCP server (serena-mcp.service, :9121).
#
# Why a real handshake and not just `ss -lnt`: a listening socket only proves
# uvicorn is up. Claude Code does NOT auto-start a URL-based MCP server — if
# this server is down or wedged, every agent silently loses code intelligence
# and falls back to grep. So this drives an actual MCP `initialize` round-trip
# over streamable-http and asserts the server answers as an MCP server.
#
# Usage:  serena_mcp_health.sh [port]        (default 9121)
# Exit:   0 healthy, 1 unhealthy. Read-only; safe to run any time.
set -uo pipefail

PORT="${1:-9121}"
URL="http://127.0.0.1:${PORT}/mcp"

fail() { echo "UNHEALTHY: $*" >&2; exit 1; }

# 1. Is anything listening on loopback?
if ! ss -lnt "sport = :${PORT}" 2>/dev/null | grep -q LISTEN; then
    fail "nothing listening on 127.0.0.1:${PORT} (try: systemctl --user status serena-mcp)"
fi

# 2. Refuse to pass if it is exposed beyond loopback. A code-intel server can
#    read the whole repo; a 0.0.0.0 bind is a real exposure, not a nitpick.
if ss -lnt "sport = :${PORT}" 2>/dev/null | grep -qE '0\.0\.0\.0:'"${PORT}"'|\[::\]:'"${PORT}"; then
    fail "port ${PORT} is bound beyond loopback (must be 127.0.0.1 only)"
fi

# 3. Real MCP initialize handshake. Streamable HTTP requires BOTH content types
#    in Accept; omitting text/event-stream gets a 406 from the MCP SDK.
BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"serena-mcp-health","version":"1"}}}'
RESP="$(curl -sS --max-time 15 \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -d "$BODY" "$URL" 2>&1)" || fail "curl failed against ${URL}: ${RESP}"

# The SDK may answer as a plain JSON body or as an SSE frame ("data: {...}").
# Both carry the serverInfo payload, so match on the protocol content itself.
if ! grep -q '"serverInfo"' <<<"$RESP"; then
    fail "no MCP serverInfo in response from ${URL}: $(head -c 300 <<<"$RESP")"
fi

SERVER_NAME="$(grep -o '"name":"[^"]*"' <<<"$RESP" | head -1 | cut -d'"' -f4)"
echo "HEALTHY: shared Serena MCP responding at ${URL} (serverInfo.name=${SERVER_NAME:-unknown})"
exit 0
