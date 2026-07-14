#!/usr/bin/env bash
# Launch the Serena MCP server inside a memory-capped systemd user scope so a
# runaway language server can never exhaust the host (upstream oraios/serena#944:
# unbounded memory growth, no built-in limit). The cap covers Serena AND the
# language-server children it spawns. Falls back to an uncapped launch if the
# systemd user manager is unreachable (e.g. no session bus in the spawn env).
#
# Tunables (env): SERENA_MEM_HIGH (default 1G, throttle/reclaim threshold),
# SERENA_MEM_MAX (default 2G, hard OOM-kill limit), SERENA_BIN.
set -euo pipefail

SERENA_BIN="${SERENA_BIN:-$HOME/.local/bin/serena}"
MEM_HIGH="${SERENA_MEM_HIGH:-1G}"
MEM_MAX="${SERENA_MEM_MAX:-2G}"

if systemd-run --user --scope --quiet --collect true 2>/dev/null; then
    exec systemd-run --user --scope --quiet --collect \
        -p MemoryHigh="$MEM_HIGH" -p MemoryMax="$MEM_MAX" -p MemorySwapMax="$MEM_MAX" \
        "$SERENA_BIN" "$@"
fi

echo "serena_mcp_capped: systemd user scope unavailable, launching uncapped" >&2
exec "$SERENA_BIN" "$@"
