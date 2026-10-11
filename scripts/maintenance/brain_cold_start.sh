#!/usr/bin/env bash
# brain_cold_start.sh - let the 4B brain (llama-server) cold-start on a Jetson whose NvMap will not reclaim page cache.
#
# THE FAILURE (2026-10-10, twice in one day): after the brain had been stopped, `systemctl --user start llama-server` failed for 14.5 min with
#   NvMapMemAllocInternalTagged ... error 12 / cudaMalloc ... 2493 MiB failed
# while MemAvailable said ~6 GB. Tegra's NvMap allocator does not reclaim page cache; loading the 4.2 GB GGUF fills the cache faster than the big
# weight buffer lands, and `Restart=on-failure` retried into the same wall. ONE drop_caches before the start was not enough; what recovered it was
# `echo 1 > /proc/sys/vm/drop_caches` every ~0.3 s WHILE the server loaded (the same loop scripts/perf/engine_bench.py carries for its own windows).
#
# WHAT THIS DOES. Wired as `ExecStartPre=-` by the tracked drop-in scripts/setup/systemd/llama-server.service.d/50-cold-start.conf, so the unit's
# own ExecStart is untouched. A Pre step must EXIT before the server starts, so it cannot BE the loop; it:
#   1. drops the page cache once, synchronously (the cache is clean before the load begins);
#   2. detaches a bounded background loop (setsid) that keeps dropping every BRAIN_COLD_START_INTERVAL_S and stops when
#      http://127.0.0.1:11434/health answers 2xx (the model is loaded) or after BRAIN_COLD_START_MAX_S (180 s, the unit's TimeoutStartSec);
#   3. exits 0.
# A start that fails and is retried runs this again; the new run SUPERSEDES the old loop (pidfile take-over), so there is only ever one.
#
# NEVER BLOCKS, NEVER FAILS THE UNIT. Every path exits 0: no passwordless sudo = one log line and nothing else; an unwritable state dir, a
# missing curl, a spawn failure = a log line. The loop is bounded by a wall clock, not by a counter. The loop's stdout/stderr go to a log file,
# never the caller's pipe, so a caller that captures our output (night_window.py's Host.run) returns at once instead of waiting for the loop.
#
# Privilege: `sudo -n sh -c 'echo 1 > /proc/sys/vm/drop_caches'` only (the same single command engine_bench.py and the night window use). Page
# cache is clean by definition (`1` = page cache, not dentries/inodes); dropping it costs a re-read from disk, nothing is lost.
#
# Usage:  brain_cold_start.sh            (the ExecStartPre / "before I start the brain" mode; returns at once)
#         brain_cold_start.sh --loop     (internal: the detached loop)
# Environment (all optional; none is a ZOE_* flag):
#   BRAIN_COLD_START_DISABLE=1       do nothing, exit 0
#   BRAIN_COLD_START_HEALTH_URL      default http://127.0.0.1:11434/health
#   BRAIN_COLD_START_MAX_S           default 180   (hard bound on the loop)
#   BRAIN_COLD_START_INTERVAL_S      default 0.3
#   BRAIN_COLD_START_STATE_DIR       default ${XDG_RUNTIME_DIR:-$HOME/.zoe}/brain-cold-start  (pidfile + loop log)
# Operator view of what happened:  journalctl --user -t brain-cold-start --since "-10min"   (or the loop log in the state dir)

set -u
export LC_ALL=C

HEALTH_URL="${BRAIN_COLD_START_HEALTH_URL:-http://127.0.0.1:11434/health}"
MAX_S="${BRAIN_COLD_START_MAX_S:-180}"
INTERVAL_S="${BRAIN_COLD_START_INTERVAL_S:-0.3}"
STATE_DIR="${BRAIN_COLD_START_STATE_DIR:-${XDG_RUNTIME_DIR:-${HOME:-/tmp}/.zoe}/brain-cold-start}"
PIDFILE="$STATE_DIR/loop.pid"
LOGFILE="$STATE_DIR/loop.log"
SELF="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"
DROP_CMD='echo 1 > /proc/sys/vm/drop_caches'
MAX_DROP_FAILURES=5

log() {
    # stdout (the unit's journal in Pre mode, the loop log in loop mode) plus a journal line tagged for `journalctl -t brain-cold-start`.
    printf 'brain_cold_start: %s\n' "$*"
    logger -t brain-cold-start -- "$*" >/dev/null 2>&1 || true
}

have_sudo() {
    command -v sudo >/dev/null 2>&1 && timeout 10 sudo -n true >/dev/null 2>&1
}

drop_once() {
    timeout 20 sudo -n sh -c "$DROP_CMD" >/dev/null 2>&1
}

healthy() {
    command -v curl >/dev/null 2>&1 || return 1
    curl -sf -m 2 "$HEALTH_URL" >/dev/null 2>&1
}

now_us() {
    local ns
    ns="$(date +%s%N 2>/dev/null)" || ns=""
    case "$ns" in
        ''|*[!0-9]*) printf '%s000000' "$(date +%s 2>/dev/null || echo 0)" ;;
        *) printf '%s' "$((ns / 1000))" ;;
    esac
}

to_us() {
    # seconds (maybe fractional) -> integer microseconds; garbage -> the default given as $2
    local v
    v="$(awk -v s="$1" 'BEGIN { if (s ~ /^[0-9]+(\.[0-9]+)?$/ && s + 0 > 0) printf "%d", s * 1000000; else print "" }' 2>/dev/null)"
    printf '%s' "${v:-$2}"
}

cmdline_is_ours() {
    # a recycled pid must never get killed: only a process whose command line names this helper counts as our loop
    [ -r "/proc/$1/cmdline" ] && tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'brain_cold_start'
}

supersede_old_loop() {
    local old
    [ -r "$PIDFILE" ] || return 0
    old="$(head -n1 "$PIDFILE" 2>/dev/null)"
    case "$old" in ''|*[!0-9]*) return 0 ;; esac
    if cmdline_is_ours "$old"; then
        kill -TERM -- "-$old" >/dev/null 2>&1 || kill -TERM "$old" >/dev/null 2>&1 || true
        log "superseded the previous loop (pid $old)"
    fi
}

run_loop() {
    # The detached loop. Exits 0 on every path.
    local interval_us max_us start now drops=0 fails=0 elapsed_ms
    interval_us="$(to_us "$INTERVAL_S" 300000)"
    max_us="$(to_us "$MAX_S" 180000000)"
    start="$(now_us)"
    printf '%s\n' "$$" > "$PIDFILE" 2>/dev/null || true
    trap 'log "stopped: superseded by a newer start (after $drops drops)"; exit 0' TERM INT HUP
    trap 'if [ "$(head -n1 "$PIDFILE" 2>/dev/null)" = "$$" ]; then rm -f "$PIDFILE"; fi' EXIT
    log "loop started: dropping the page cache every ${INTERVAL_S}s until $HEALTH_URL answers or ${MAX_S}s pass"
    while :; do
        if healthy; then
            elapsed_ms=$((($(now_us) - start) / 1000))
            log "stopped: brain healthy after ${elapsed_ms} ms ($drops drops)"
            return 0
        fi
        now="$(now_us)"
        if [ $((now - start)) -ge "$max_us" ]; then
            log "stopped: ${MAX_S}s bound reached without a healthy brain ($drops drops) - the unit's own restart/alarm handling takes over"
            return 0
        fi
        if drop_once; then
            drops=$((drops + 1))
            fails=0
        else
            fails=$((fails + 1))
            if [ "$fails" -ge "$MAX_DROP_FAILURES" ]; then
                log "stopped: $MAX_DROP_FAILURES consecutive drop_caches failures (sudo stopped working?) after $drops drops"
                return 0
            fi
        fi
        sleep "$INTERVAL_S" 2>/dev/null || sleep 1
    done
}

run_pre() {
    # The ExecStartPre mode. Exits 0 on every path.
    if [ "${BRAIN_COLD_START_DISABLE:-0}" = "1" ]; then
        log "disabled (BRAIN_COLD_START_DISABLE=1): nothing done"
        return 0
    fi
    if ! have_sudo; then
        log "SKIPPED: passwordless sudo is not available, so the page cache cannot be dropped. If the start fails on NvMap error 12 run: sudo sh -c '$DROP_CMD'  (repeatedly, while it starts)"
        return 0
    fi
    if healthy; then
        log "skipped: $HEALTH_URL already answers - a brain is up, nothing to protect"
        return 0
    fi
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    supersede_old_loop
    if drop_once; then
        log "page cache dropped before the start"
    else
        log "the first drop_caches failed (the loop will keep trying)"
    fi
    if [ ! -d "$STATE_DIR" ] || [ ! -w "$STATE_DIR" ]; then
        log "no loop: state dir $STATE_DIR is not writable (the single drop above is all that was done)"
        return 0
    fi
    # A detached loop: own session, no inherited stdout/stderr (the log file), so neither systemd nor a capturing caller waits on it.
    if command -v setsid >/dev/null 2>&1; then
        setsid bash "$SELF" --loop </dev/null >>"$LOGFILE" 2>&1 &
    else
        bash "$SELF" --loop </dev/null >>"$LOGFILE" 2>&1 &
    fi
    disown 2>/dev/null || true
    log "loop detached (log: $LOGFILE)"
    return 0
}

case "${1:-}" in
    --loop) run_loop ;;
    *) run_pre ;;
esac
exit 0
