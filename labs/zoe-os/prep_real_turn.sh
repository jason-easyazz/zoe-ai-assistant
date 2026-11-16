#!/bin/bash
# Build the lab Flue app once, in DAYLIGHT, so the guarded real run only has to start it.
# Read-only on the live sidecar: it COPIES that directory's node_modules (same pins, no network, no npm) and
# never writes there. Heavy (a vite build): wait for memory, and never while a probe runs.
#     bash labs/zoe-os/prep_real_turn.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/brain-lab-app"
LIVE_DEPS="${OSLAB_DEPS_FROM:-/home/zoe/assistant/labs/flue-zoe-brain-2x/node_modules}"
FLOOR_MB="${OSLAB_PREP_FLOOR_MB:-1500}"
mem() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }
if [ "$(mem)" -lt "$FLOOR_MB" ]; then echo "prep: MemAvailable $(mem) MiB < $FLOOR_MB; not building" >&2; exit 4; fi
if pgrep -f '^([^ ]*/)?python[0-9.]*( +-[^ ]+)* +[^ ]*(voice_regression_probe|measure_voice|measure_tts|measure_cold_first_turn)\.py' >/dev/null; then
  echo "prep: a voice probe is running; not building" >&2; exit 4; fi
[ "$(systemctl --user is-active kokoro-tts)" = active ] || { echo "prep: speech service is not active; not building" >&2; exit 4; }
[ -d "$LIVE_DEPS" ] || { echo "prep: no dependency tree at $LIVE_DEPS" >&2; exit 4; }
[ -d "$APP/node_modules" ] || cp -a "$LIVE_DEPS" "$APP/node_modules"
cd "$APP"
env -i PATH="$PATH" HOME="$HOME" nice -n 15 node node_modules/vite/bin/vite.js build
test -s dist/server.mjs && echo "prep: built $APP/dist/server.mjs"
