#!/usr/bin/env bash
# ============================================================
# Zoe Touch Panel — Pi Voice Daemon Installer
# ============================================================
# Run this on the Raspberry Pi (zoe-touch-pi) as the normal user.
# Prerequisites: Python 3.9+, pip, portaudio19-dev, git
#
# What this installs:
#   1. openwakeword  — wake word detection ("hey zoe")
#   2. whisper.cpp   — speech-to-text (via Zoe Jetson API)
#   3. zoe-voice-daemon.py — the main daemon that ties it together
#   4. systemd unit  — auto-start + watchdog restart
#
# Usage:
#   bash pi_voice_daemon_install.sh --zoe-url https://zoe.local --panel-id zoe-touch-pi
#
# Required env / flags (set in .env.voice after install):
#   ZOE_URL         base URL of the Jetson (https://zoe.local)
#   PANEL_ID        unique ID for this Pi (zoe-touch-pi)
#   DEVICE_TOKEN    issued via: POST /api/panels/{panel_id}/token  (admin auth)
#   AUDIO_DEVICE    ALSA device index, e.g. hw:2,0 (run: arecord -l to list)
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

### ---- defaults --------------------------------------------------------
ZOE_URL="${ZOE_URL:-https://zoe.local}"
PANEL_ID="${PANEL_ID:-zoe-touch-pi}"
DEVICE_TOKEN="${DEVICE_TOKEN:-}"
AUDIO_DEVICE="${AUDIO_DEVICE:-default}"
INSTALL_DIR="${HOME}/.zoe-voice"
WAKEWORD_MODEL="hey_zoe"  # model name for openwakeword

usage() {
    echo "Usage: $0 [--zoe-url URL] [--panel-id ID] [--device-token TOKEN] [--audio-device DEV]"
    exit 1
}

### ---- parse args -------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        --zoe-url)      ZOE_URL="$2"; shift 2 ;;
        --panel-id)     PANEL_ID="$2"; shift 2 ;;
        --device-token) DEVICE_TOKEN="$2"; shift 2 ;;
        --audio-device) AUDIO_DEVICE="$2"; shift 2 ;;
        -h|--help)      usage ;;
        *) echo "Unknown arg: $1"; usage ;;
    esac
done

echo "==> Installing Zoe voice daemon on $(hostname) (panel: $PANEL_ID)"
echo "    Zoe URL : $ZOE_URL"
echo "    Install : $INSTALL_DIR"

### ---- system dependencies ---------------------------------------------
echo "==> Checking system packages..."
MISSING=""
for pkg in portaudio19-dev python3-pip python3-venv ffmpeg; do
    dpkg -s "$pkg" &>/dev/null || MISSING="$MISSING $pkg"
done
if [ -n "$MISSING" ]; then
    echo "    Installing:$MISSING"
    sudo apt-get install -y $MISSING
fi

### ---- virtual environment ---------------------------------------------
echo "==> Setting up Python venv..."
mkdir -p "$INSTALL_DIR"
python3 -m venv "$INSTALL_DIR/venv"
# shellcheck source=/dev/null
source "$INSTALL_DIR/venv/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet \
    openwakeword \
    pyaudio \
    numpy \
    requests \
    websocket-client

### ---- openwakeword model download -------------------------------------
echo "==> Downloading wake word model..."
python3 - << 'PYEOF'
import openwakeword
# Download bundled models (includes 'hey_jarvis', 'alexa', etc. as reference)
openwakeword.utils.download_models()
print("    openwakeword models ready.")
PYEOF

### ---- install the voice daemon script ---------------------------------
echo "==> Installing daemon script..."
cp "$SCRIPT_DIR/zoe_voice_daemon.py" "$INSTALL_DIR/zoe_voice_daemon.py"
chmod +x "$INSTALL_DIR/zoe_voice_daemon.py"

### ---- write .env.voice ------------------------------------------------
ENV_FILE="$INSTALL_DIR/.env.voice"
if [ ! -f "$ENV_FILE" ]; then
    echo "==> Writing $ENV_FILE"
    cat > "$ENV_FILE" << EOF
ZOE_URL=${ZOE_URL}
PANEL_ID=${PANEL_ID}
DEVICE_TOKEN=${DEVICE_TOKEN}
AUDIO_DEVICE=${AUDIO_DEVICE}
SAMPLE_RATE=16000
CHUNK_SIZE=1280
RECORD_SECONDS_MAX=8
SILENCE_TIMEOUT_S=1.5
WAKEWORD_THRESHOLD=0.35
# Set to 1 to log max wakeword score every 5s (verify mic + model sensitivity)
WAKEWORD_DEBUG=0
# Set to false if using self-signed TLS cert on Jetson
VERIFY_SSL=true
# Optional TTS ack phrase when wake word fires ("" to disable)
ZOE_WAKE_ACK_PHRASE=
EOF
    echo "    IMPORTANT: Edit $ENV_FILE and set DEVICE_TOKEN before starting."
else
    echo "    $ENV_FILE already exists — not overwritten."
fi

### ---- write wrapper script -------------------------------------------
cat > "$INSTALL_DIR/run.sh" << 'RUNSH'
#!/usr/bin/env bash
set -a
# shellcheck source=/dev/null
source "$(dirname "$0")/.env.voice"
set +a
exec "$(dirname "$0")/venv/bin/python" "$(dirname "$0")/zoe_voice_daemon.py"
RUNSH
chmod +x "$INSTALL_DIR/run.sh"

### ---- systemd user service --------------------------------------------
SYSTEMD_DIR="${HOME}/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
SERVICE_FILE="$SYSTEMD_DIR/zoe-voice.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Zoe Voice Daemon (wake word + STT)
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/run.sh
WorkingDirectory=${INSTALL_DIR}
Restart=on-failure
RestartSec=5s
# Watchdog: restart if no heartbeat in 60s (daemon should handle SIGTERM cleanly)
WatchdogSec=0
StandardOutput=journal
StandardError=journal
SyslogIdentifier=zoe-voice

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable zoe-voice.service
echo ""
echo "==> Installation complete."
echo ""
echo "Next steps:"
echo "  1. Issue a device token on the Jetson:"
echo "     POST https://zoe.local/api/panels/${PANEL_ID}/token"
echo "     (Requires admin session. See OPERATOR_RUNBOOK.md)"
echo "  2. Edit ${ENV_FILE} and set DEVICE_TOKEN=<token>"
echo "  3. Start the service:"
echo "     systemctl --user start zoe-voice"
echo "     journalctl --user -u zoe-voice -f"
echo ""
echo "Audio device list (run: arecord -l) — update AUDIO_DEVICE in .env.voice if needed."
echo ""
echo "Wake word: without hey_zoe.onnx the bundled model is **hey_jarvis** — say \"Hey Jarvis\" clearly."
echo "  Add ~/.zoe-voice/hey_zoe.onnx for a custom \"Hey Zoe\" model."
echo ""
