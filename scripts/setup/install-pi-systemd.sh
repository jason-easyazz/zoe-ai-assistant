#!/usr/bin/env bash
# install-pi-systemd.sh
# Run on the Pi as the 'pi' user.
# Installs all Zoe systemd user units and enables them.
#
# Usage:
#   bash scripts/setup/install-pi-systemd.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UNIT_SRC="$REPO_ROOT/services/zoe-data/systemd"
UNIT_DST="$HOME/.config/systemd/user"

echo "=== Zoe Pi Systemd Unit Installer ==="
echo "Source: $UNIT_SRC"
echo "Destination: $UNIT_DST"
echo ""

mkdir -p "$UNIT_DST"

units=(bitnet-server.service gemma-server.service mempalace.service zoe-data.service)

for unit in "${units[@]}"; do
    src="$UNIT_SRC/$unit"
    dst="$UNIT_DST/$unit"
    if [[ ! -f "$src" ]]; then
        echo "ERROR: $src not found"
        exit 1
    fi
    cp "$src" "$dst"
    echo "Installed: $dst"
done

# Reload systemd user daemon
systemctl --user daemon-reload
echo ""
echo "Systemd user daemon reloaded."

# Enable all units (they will start on next login / after lingering is enabled)
for unit in "${units[@]}"; do
    systemctl --user enable "$unit"
    echo "Enabled: $unit"
done

# Enable lingering so user units start at boot (without login session)
loginctl enable-linger pi 2>/dev/null || true

echo ""
echo "=== Done ==="
echo "To start all services now:"
echo "  systemctl --user start bitnet-server gemma-server mempalace zoe-data"
echo ""
echo "To check status:"
echo "  systemctl --user status bitnet-server gemma-server mempalace zoe-data"
echo ""
echo "IMPORTANT: pironman5.service is preserved — do not modify it."
