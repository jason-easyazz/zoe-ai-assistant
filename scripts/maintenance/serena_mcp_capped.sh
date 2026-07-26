#!/usr/bin/env bash
# Launch the Serena MCP server inside a memory-capped systemd user scope so a
# runaway language server can never exhaust the host (upstream oraios/serena#944:
# unbounded memory growth, no built-in limit). The cap covers Serena AND the
# language-server children it spawns. Falls back to an uncapped launch if the
# systemd user manager is unreachable (e.g. no session bus in the spawn env).
#
# NOTE: the scope wrapper exists because a stdio-spawned Serena has no unit of
# its own to carry limits. When Serena runs AS a systemd unit
# (scripts/setup/systemd/serena-mcp.service, the shared server), the unit
# carries MemoryHigh/MemoryMax natively and the scope must be skipped — set
# SERENA_NO_SCOPE=1. Nesting a scope inside a service reparents Serena to PID 1
# and moves it into app.slice, escaping the service cgroup: Restart= tracking
# breaks and reap_stale_serena.py sees a PID-1 "orphan". This script's other
# jobs (resolving the serena binary, self-provisioning jedi-language-server)
# still apply in both modes.
#
# Tunables (env): SERENA_MEM_HIGH (default 1G, throttle/reclaim threshold),
# SERENA_MEM_MAX (default 2G, hard OOM-kill limit), SERENA_BIN,
# SERENA_NO_SCOPE (1 = launch directly, for use under a systemd unit).
set -euo pipefail

# Resolve the serena binary: explicit SERENA_BIN, then the canonical Zoe
# install, then the caller's HOME, then PATH. The canonical path comes first
# so a service/container launcher with HOME=/root still uses the Zoe install.
ZOE_LOCAL_BIN="/home/zoe/.local/bin"
SERENA_BIN="${SERENA_BIN:-}"
if [ -z "$SERENA_BIN" ]; then
    for cand in "$ZOE_LOCAL_BIN/serena" "${HOME:-/nonexistent}/.local/bin/serena"; do
        if [ -x "$cand" ]; then SERENA_BIN="$cand"; break; fi
    done
fi
if [ -z "$SERENA_BIN" ]; then
    SERENA_BIN="$(command -v serena || true)"
fi
if [ -z "$SERENA_BIN" ] || [ ! -x "$SERENA_BIN" ]; then
    echo "serena_mcp_capped: serena binary not found (set SERENA_BIN)" >&2
    exit 1
fi
MEM_HIGH="${SERENA_MEM_HIGH:-1G}"
MEM_MAX="${SERENA_MEM_MAX:-2G}"

# The project's .serena/project.yml uses python_jedi, which needs
# jedi-language-server on PATH. Self-provision on fresh hosts and fail
# hard if it cannot be provided — starting Serena without a Python LS
# would silently break MCP code intelligence.
export PATH="$ZOE_LOCAL_BIN:$PATH"
if ! command -v jedi-language-server >/dev/null 2>&1; then
    if command -v uv >/dev/null 2>&1; then
        uv tool install --quiet jedi-language-server >&2 || true
        # uv installs into the CALLER's tool bin dir, which may not be
        # /home/zoe/.local/bin when launched with a foreign HOME — add it
        # to PATH before re-checking.
        UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "${HOME:-/nonexistent}/.local/bin")"
        export PATH="$UV_BIN_DIR:$PATH"
    fi
fi
if ! command -v jedi-language-server >/dev/null 2>&1; then
    echo "serena_mcp_capped: jedi-language-server not found and auto-install failed; refusing to start Serena without a Python LS (install with: uv tool install jedi-language-server)" >&2
    exit 1
fi

# Running under a systemd unit that already carries the caps: launch directly.
# Wrapping in a scope here would escape that unit's cgroup (see header).
if [ "${SERENA_NO_SCOPE:-0}" = "1" ]; then
    exec "$SERENA_BIN" "$@"
fi

if systemd-run --user --scope --quiet --collect true 2>/dev/null; then
    exec systemd-run --user --scope --quiet --collect \
        -p MemoryHigh="$MEM_HIGH" -p MemoryMax="$MEM_MAX" -p MemorySwapMax="$MEM_MAX" \
        "$SERENA_BIN" "$@"
fi

echo "serena_mcp_capped: systemd user scope unavailable, launching uncapped" >&2
exec "$SERENA_BIN" "$@"
