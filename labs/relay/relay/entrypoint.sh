#!/bin/sh
# frps entrypoint: the preflight is the gate. No preflight pass, no frps.
set -eu
CONFIG="${FRPS_CONFIG:-/etc/frp/frps.toml}"
if ! python3 /opt/relay/preflight.py --config "$CONFIG" --wait-plugin "${PREFLIGHT_WAIT_S:-30}"; then
    echo "relay: preflight FAILED, refusing to start frps" >&2
    exit 78
fi
exec /usr/local/bin/frps -c "$CONFIG"
