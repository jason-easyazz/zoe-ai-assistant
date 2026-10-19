#!/usr/bin/env bash
# Install and enable the Zoe Voice Daemon as a systemd service
# Run: sudo bash install-voice-service.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/zoe-voice.service"
ENV_EXAMPLE="$SCRIPT_DIR/zoe-voice.env.example"
SYSTEM_ENV="/etc/zoe-voice.env"

echo "=== Zoe Voice Daemon Installer ==="

# Check we're running as root
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run with sudo: sudo bash $0"
    exit 1
fi

# Copy env file if it doesn't exist
if [[ ! -f "$SYSTEM_ENV" ]]; then
    # The example is the PANEL's configuration as code (its active lines are the live Pi's tuned values:
    # pulse audio, the ResNet34 speaker model, /home/pi paths). A fresh host starts from the CODE defaults:
    # every active line is commented out except the three every host must set, and the tuned values stay
    # visible as comments to opt into one by one.
    sed -E -e 's/^([A-Z][A-Z0-9_]*=)/#\1/' -e 's/^#((DEVICE_TOKEN|ZOE_URL|PANEL_ID)=)/\1/' "$ENV_EXAMPLE" > "$SYSTEM_ENV"
    chmod 640 "$SYSTEM_ENV"
    echo "Created $SYSTEM_ENV — please edit it and add your DEVICE_TOKEN before enabling the service."
    echo "  nano $SYSTEM_ENV"
else
    echo "Env file already exists: $SYSTEM_ENV"
fi

# Copy service file
cp "$SERVICE_FILE" /etc/systemd/system/zoe-voice.service
chmod 644 /etc/systemd/system/zoe-voice.service

# Reload systemd
systemctl daemon-reload

echo ""
echo "=== Service installed. Next steps: ==="
echo "1. Edit the env file:  sudo nano $SYSTEM_ENV"
echo "2. Set DEVICE_TOKEN to your panel device token"
echo "3. Enable and start:   sudo systemctl enable --now zoe-voice"
echo "4. Check status:       sudo systemctl status zoe-voice"
echo "5. View logs:          sudo journalctl -u zoe-voice -f"
echo ""
echo "Wake word: 'Hey Jarvis' (default) or place hey_zoe.onnx in $SCRIPT_DIR for 'Hey Zoe'"
