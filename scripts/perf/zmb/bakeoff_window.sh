#!/bin/bash
# bakeoff_window.sh - the owner's ONE command for the Hindsight bake-off.
#
#   scripts/perf/zmb/bakeoff_window.sh              # the whole window (about 85 min, hard cap 90)
#   scripts/perf/zmb/bakeoff_window.sh --dry-run    # print every step + the generated Gemma clone command; change nothing
#   scripts/perf/zmb/bakeoff_window.sh --restore-only   # put the live brain back (idempotent)
#
# It stops llama-server.service for the length of the window (the voice stack is down), runs the bake-off against a clone, and ALWAYS puts the
# live brain back. Read docs/knowledge/bakeoff-howto.md first. The work is in bakeoff.py / bakeoff_measure.py / bakeoff_gates.py (tested
# with every service command shimmed); this wrapper only adds the last safety net: if the driver is killed hard, the marker file it leaves
# behind makes this script run --restore-only on the way out.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BAKEOFF=${BAKEOFF_DIR:-/home/zoe/.zoe/bakeoff-2026-10}
PY=${BAKEOFF_PY:-/home/zoe/.zoe/venvs/zoe-data-py312/bin/python}
MARKER="$BAKEOFF/WINDOW_OPEN"

# nothing here may reach the network or write bytecode into the checkout
export PYTHONDONTWRITEBYTECODE=1 ZOE_HARNESS=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1 ORT_DISABLE_TELEMETRY=1

restore() {
  if [ -e "$MARKER" ]; then
    echo "[bakeoff_window] the window marker is still present: restoring the live brain" >&2
    "$PY" "$HERE/bakeoff.py" --restore-only
  fi
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PY" "$HERE/bakeoff.py" "$@"
exit $?
