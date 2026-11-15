#!/bin/sh
# [code] Owner-started only. No service management, config writes or git mutation.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${SPEED_LOOP_PYTHON:-python3}" "$here/replay.py" "$@"
