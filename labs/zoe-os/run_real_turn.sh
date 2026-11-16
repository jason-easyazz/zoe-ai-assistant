#!/bin/bash
# Guarded real-turn runner (see real_turn.py). Refuses unless every guard passes; takes
# /tmp/zoe-voice-harness.lock itself and releases it on any exit.
#     bash labs/zoe-os/run_real_turn.sh --i-own-the-slot            # the real run (22:00-01:50 only)
#     bash labs/zoe-os/run_real_turn.sh --dry-run --i-own-the-slot  # mock llama, safe by day
exec env -i PATH="$PATH" HOME="$HOME" XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}" python3 -B "$(dirname "$0")/real_turn.py" "$@"
