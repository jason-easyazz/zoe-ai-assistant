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
#    read the whole repo; a non-loopback bind is a real exposure, not a nitpick.
#    ALLOW-LIST, not a deny-list: matching known-bad wildcards (0.0.0.0, [::])
#    would silently pass a specific non-loopback bind such as
#    `--host 192.168.1.218`. Anything that is not loopback fails.
#
#    ONE sanctioned exception, and it is never Serena itself: the scoped bridge
#    for the zoe-omnigent container (scripts/setup/systemd/system/
#    serena-bridge.{socket,service}) puts a systemd-socket-proxyd listener on
#    ${BRIDGE_ADDR}, the gateway of the one-member internal `zoe-codeintel`
#    network, restricted by IPAddressAllow. Without this arm the check would
#    start failing the moment that bridge is installed. It is deliberately
#    narrow: the exact address, the socket unit actually active, AND the socket
#    must not belong to serena — a Serena rebound onto that address still fails.
BRIDGE_ADDR="172.28.0.1:${PORT}"
while read -r local_addr proc; do
    [ -n "$local_addr" ] || continue
    case "${local_addr%:*}" in           # strip the :port -> 127.0.0.1 | [::1] | 0.0.0.0 | *
        127.*|"[::1]"|"::1") continue ;; # loopback: fine
    esac
    case "$proc" in
        *'"serena"'*) fail "Serena itself is bound to '${local_addr%:*}', beyond loopback (must be 127.0.0.1 only)" ;;
    esac
    if [ "$local_addr" = "$BRIDGE_ADDR" ] \
       && systemctl is-active --quiet serena-bridge.socket 2>/dev/null; then
        echo "NOTE: ${BRIDGE_ADDR} is the scoped serena-bridge proxy (system unit," \
             "IPAddressAllow-restricted to the omnigent container); Serena itself is loopback-only."
        continue
    fi
    fail "port ${PORT} is bound to '${local_addr%:*}', beyond loopback (must be 127.0.0.1 only)"
done <<EOF
$(ss -lntp "sport = :${PORT}" 2>/dev/null | awk 'NR > 1 {print $4, $6}')
EOF

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
