#!/usr/bin/env bash
# Launch codebase-memory-mcp inside a memory-capped systemd user scope.
#
# WHY (measured 2026-08-02): codebase-memory is declared with `command:` in BOTH
# agent configs, so every agent session stdio-spawns its own copy. Fourteen were
# live at once totalling ~1.27 GB — one 473 MB, one 388 MB, and one that had been
# resident for 2.3 DAYS. On a 15.6 GB box whose brain + TTS already hold ~9 GB
# that is the difference between a working voice gate and a thrashing host
# (45 GB of swap, load 55 observed the same afternoon).
#
# WHY A SCOPE AND NOT A SHARED SERVER — the important distinction from Serena.
# Serena speaks streamable-http, so the fleet was consolidated onto ONE server
# (serena-mcp.service, port 9121) and the per-agent spawn was deleted outright.
# codebase-memory-mcp 0.8.1 is **stdio-only**: `--help` offers exactly "Run MCP
# server on stdio", and its `--port` is the UI graph viewer (9749), NOT an MCP
# transport. So per-agent spawning cannot be removed here — it is forced by the
# tool. What CAN be fixed is that each spawn was unbounded. This caps them.
#
# Do NOT "fix" this by pointing agents at a URL: there is no HTTP MCP endpoint to
# point at. If a future release adds one, consolidate the way Serena was
# consolidated and delete this wrapper.
#
# The reaper is not an answer either: these processes have LIVE parents (their
# agent session), so they are not orphans and reap_stale_serena-style cleanup
# correctly leaves them alone.
#
# Tunables (env): CODEBASE_MEMORY_MEM_HIGH (default 512M, throttle/reclaim),
# CODEBASE_MEMORY_MEM_MAX (default 768M, hard OOM-kill), CODEBASE_MEMORY_BIN.
# Defaults are deliberately well ABOVE the observed healthy working set
# (~50-220 MB) and below the runaway outliers (388-473 MB), so a normal session
# never notices the cap and a leaking one is killed instead of the host.
set -euo pipefail

ZOE_LOCAL_BIN="/home/zoe/.local/bin"
BIN="${CODEBASE_MEMORY_BIN:-}"
if [ -z "$BIN" ]; then
    for cand in "$ZOE_LOCAL_BIN/codebase-memory-mcp" \
                "${HOME:-/nonexistent}/.local/bin/codebase-memory-mcp"; do
        if [ -x "$cand" ]; then BIN="$cand"; break; fi
    done
fi
if [ -z "$BIN" ]; then
    BIN="$(command -v codebase-memory-mcp || true)"
fi
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
    echo "codebase_memory_capped: codebase-memory-mcp not found (set CODEBASE_MEMORY_BIN)" >&2
    exit 1
fi

MEM_HIGH="${CODEBASE_MEMORY_MEM_HIGH:-512M}"
MEM_MAX="${CODEBASE_MEMORY_MEM_MAX:-768M}"

# Escape hatch matching serena_mcp_capped.sh: nesting a scope inside a systemd
# unit reparents the process to PID 1 and moves it out of the unit's cgroup.
if [ "${CODEBASE_MEMORY_NO_SCOPE:-0}" = "1" ]; then
    exec "$BIN" "$@"
fi

# MemorySwapMax is set alongside MemoryMax deliberately: capping RSS without
# capping swap just relocates a leak into swap, which is what happened to Serena
# (2.1 GB leaked into swap under a MemoryMax that looked correct).
if systemd-run --user --scope --quiet --collect true 2>/dev/null; then
    exec systemd-run --user --scope --quiet --collect \
        -p MemoryHigh="$MEM_HIGH" -p MemoryMax="$MEM_MAX" -p MemorySwapMax="$MEM_MAX" \
        "$BIN" "$@"
fi

echo "codebase_memory_capped: systemd user scope unavailable, launching uncapped" >&2
exec "$BIN" "$@"
