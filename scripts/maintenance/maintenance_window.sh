#!/usr/bin/env bash
# maintenance_window.sh - open / close a PLANNED maintenance window for the health check (zoe_health.py).
#
#   maintenance_window.sh open <test|replay|deploy|other> <minutes>   # 1..120 minutes; prints the until epoch
#   maintenance_window.sh close                                         # removes the marker (absent is fine)
#
# While the marker is valid, zoe_health.py skips the probes a planned window takes down on purpose (WINDOW_TAKES:
# the speech service) instead of sending the owner an outage and a recovery. Call `open` BEFORE stopping anything and
# `close` AFTER the service is back. The marker is JSON {"until": <epoch>, "kind": "<kind>"} and nothing else, mode
# 0600, written atomically (temp file + rename). The health check ignores a marker that has expired, is malformed or
# is more than 2 hours ahead, so a forgotten marker silences nothing for long; this writer refuses more than 120.
# Directory: $MAINTENANCE_WINDOW_DIR, default ~/.zoe/maintenance-window (the same override the health check reads).
# Exit: 0 done, 2 usage / refused.
set -euo pipefail
umask 077
DIR="${MAINTENANCE_WINDOW_DIR:-$HOME/.zoe/maintenance-window}"
MARKER="$DIR/OPEN"
MAX_MIN=120

usage() { echo "usage: maintenance_window.sh open <test|replay|deploy|other> <minutes 1-$MAX_MIN> | close" >&2; exit 2; }

case "${1:-}" in
  open)
    [ $# -eq 3 ] || usage
    kind=$2 minutes=$3
    case "$kind" in test|replay|deploy|other) ;; *) echo "maintenance_window: unknown kind '$kind'" >&2; usage;; esac
    case "$minutes" in ''|*[!0-9]*) echo "maintenance_window: minutes must be a whole number" >&2; usage;; esac
    minutes=$((10#$minutes))
    if [ "$minutes" -lt 1 ] || [ "$minutes" -gt "$MAX_MIN" ]; then
      echo "maintenance_window: refused: $minutes minutes is outside 1-$MAX_MIN" >&2; exit 2
    fi
    mkdir -p "$DIR"
    chmod 700 "$DIR"
    until=$(( $(date +%s) + minutes * 60 ))
    tmp=$(mktemp "$DIR/.OPEN.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    printf '{"until": %d, "kind": "%s"}\n' "$until" "$kind" >"$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$MARKER"
    trap - EXIT
    echo "$until"
    ;;
  close)
    [ $# -eq 1 ] || usage
    rm -f "$MARKER"
    ;;
  *) usage ;;
esac
