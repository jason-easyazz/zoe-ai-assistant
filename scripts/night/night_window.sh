#!/bin/bash
# night_window.sh - the 12B night window, and its last safety net.
#
#   scripts/night/night_window.sh --dry-run        the arithmetic for every lever combination, which fits TODAY, the plan; changes nothing
#   scripts/night/night_window.sh                  the window (the timer runs this at 02:50; hard cap 65 min; over by 03:55)
#   scripts/night/night_window.sh --trial          12B-only measurement: K1-K5 on the 12B and on the 4B at 32k, then restore
#   scripts/night/night_window.sh --restore-only   put everything back (idempotent)
#
# It stops zoe-data, the router, Kokoro and the 4B brain for the length of the window and ALWAYS wakes them again, brain first. Read
# docs/knowledge/night-window.md first. The work is in night_window.py (tested with every service command shimmed); this wrapper adds the
# last net: if the driver is killed hard, the marker file it leaves behind makes this script run --restore-only on the way out.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
NIGHT=${NIGHT_DIR:-$HOME/.zoe/night-window}
PY=${NIGHT_PY:-$HOME/.zoe/venvs/zoe-data-py312/bin/python}
MARKER="$NIGHT/WINDOW_OPEN"

export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1 ORT_DISABLE_TELEMETRY=1

restore() {
  if [ -e "$MARKER" ]; then
    echo "[night_window] the window marker is still present: waking everything" >&2
    "$PY" "$HERE/night_window.py" --restore-only
  fi
}
case " $* " in
  *" --restore-only "*|*" --dry-run "*|*" --summary "*) ;;          # these never need the net (a dry run and a summary change nothing; restore-only IS the net)
  *) trap restore EXIT; trap 'exit 130' INT; trap 'exit 143' TERM ;;
esac

"$PY" "$HERE/night_window.py" "$@"
exit $?
