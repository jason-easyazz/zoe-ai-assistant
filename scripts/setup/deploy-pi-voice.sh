#!/usr/bin/env bash
# ============================================================
# deploy-pi-voice.sh — Deploy Zoe voice daemon to Raspberry Pi
# Run from the Jetson: bash scripts/setup/deploy-pi-voice.sh
#
# LIVE PANEL DEFAULTS: the zoe-touch panel runs the daemon as user `pi`
# with files in /home/pi/.zoe-voice/ under the USER service zoe-voice
# (ssh alias `zoe-pi`; restart with `systemctl --user restart zoe-voice`).
# These defaults target that live layout. For a fresh Pi provisioned by
# pi_voice_daemon_install.sh under a different user, override PI_USER /
# PI_DAEMON_DIR / PI_VENV explicitly.
# ============================================================
set -euo pipefail

PI_HOST="${PI_HOST:-192.168.1.61}"
PI_USER="${PI_USER:-pi}"
PI_DAEMON_DIR="${PI_DAEMON_DIR:-/home/pi/.zoe-voice}"
PI_VENV="${PI_VENV:-/home/pi/.zoe-voice/venv}"

echo "==> Deploying Zoe voice daemon to ${PI_USER}@${PI_HOST}"

# 1. Rsync daemon files to Pi
echo "==> Syncing daemon files..."
rsync -avz --progress \
  scripts/setup/zoe_voice_daemon.py \
  scripts/setup/pi-requirements.txt \
  scripts/setup/zoe-voice.service \
  "${PI_USER}@${PI_HOST}:${PI_DAEMON_DIR}/"

# 2. Install Python dependencies on Pi
echo "==> Installing Python dependencies on Pi..."
ssh "${PI_USER}@${PI_HOST}" "
  cd ${PI_DAEMON_DIR}
  ${PI_VENV}/bin/pip install -r pi-requirements.txt --quiet || \
  python3 -m pip install -r pi-requirements.txt --quiet
  echo 'Dependencies installed.'
"

# 3. Install/update the systemd service
echo "==> Updating systemd service..."
ssh "${PI_USER}@${PI_HOST}" "
  if [ -d /etc/systemd/system ] && [ \"\$(id -u)\" -eq 0 ]; then
    # System-level service
    cp ${PI_DAEMON_DIR}/zoe-voice.service /etc/systemd/system/zoe-voice.service
    systemctl daemon-reload
    systemctl enable zoe-voice
    systemctl restart zoe-voice
    systemctl status zoe-voice --no-pager
  else
    # User-level service (non-root)
    mkdir -p ~/.config/systemd/user
    # Adjust service for user-level (remove User=zoe, Group=audio)
    sed '/^User=/d; /^Group=/d' ${PI_DAEMON_DIR}/zoe-voice.service > ~/.config/systemd/user/zoe-voice.service
    systemctl --user daemon-reload
    systemctl --user enable zoe-voice
    systemctl --user restart zoe-voice
    systemctl --user status zoe-voice --no-pager
  fi
"

echo ""
echo "==> Deployment complete!"
echo "    Check Pi logs: ssh ${PI_USER}@${PI_HOST} 'journalctl -u zoe-voice -f'"
echo "    Health check:  curl http://${PI_HOST}:7777/health"
echo ""
echo "NOTE: If SSH fails, copy files manually and run:"
echo "  pip install -r pi-requirements.txt"
echo "  sudo systemctl restart zoe-voice"
