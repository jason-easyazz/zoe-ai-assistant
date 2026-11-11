#!/usr/bin/env bash
# prepare-pi-fresh.sh — Full first-time setup of a fresh Raspberry Pi OS panel.
#
# Usage (run from Jetson, repo root):
#   scripts/setup/touchscreen/prepare-pi-fresh.sh \
#     [--host <PANEL_IP>] [--user pi] [--ssh-key ~/.ssh/zoe_pi_key]
#
# Requires: Pi is reachable over SSH (ethernet or Pi Imager pre-configured WiFi).
# Result:   Pi is fully configured and reboots into the first-boot provisioning flow.

set -euo pipefail

REMOTE_HOST="${ZOE_PANEL_HOST:-}"   # panel address: --host or ZOE_PANEL_HOST (no default baked in)
REMOTE_USER="pi"
SSH_KEY="${HOME}/.ssh/zoe_pi_key"
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "Usage: $0 [--host HOST] [--user USER] [--ssh-key PATH]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)    REMOTE_HOST="$2"; shift 2 ;;
    --user)    REMOTE_USER="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2";     shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

[[ -n "${REMOTE_HOST}" ]] || { echo "Set --host or ZOE_PANEL_HOST"; usage; }

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"
if [[ -f "${SSH_KEY}" ]]; then
  SSH_OPTS="${SSH_OPTS} -i ${SSH_KEY}"
fi

ssh_cmd() { ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "$@"; }
scp_cmd() { scp ${SSH_OPTS} "$@"; }

echo "=== Preparing Zoe Touch Panel at ${REMOTE_HOST} ==="

# ── 1. System packages ───────────────────────────────────────────────────────
echo "Step 1: Installing required packages..."
ssh_cmd "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  network-manager \
  chromium-browser \
  lightdm openbox \
  xdotool unclutter \
  python3 \
  curl wget"

# ── 2. Create /opt/TouchKio/ ─────────────────────────────────────────────────
echo "Step 2: Creating /opt/TouchKio/..."
ssh_cmd "sudo mkdir -p /opt/TouchKio/wifi-portal /opt/TouchKio/systemd && \
         sudo chown -R ${REMOTE_USER}:${REMOTE_USER} /opt/TouchKio"

# ── 3. Copy bundle ───────────────────────────────────────────────────────────
echo "Step 3: Copying touchscreen bundle..."
scp_cmd -r "${BUNDLE_DIR}/"* "${REMOTE_USER}@${REMOTE_HOST}:/opt/TouchKio/"

# ── 4. Install systemd services ──────────────────────────────────────────────
echo "Step 4: Installing systemd services..."
ssh_cmd "sudo cp /opt/TouchKio/systemd/zoe-kiosk.service /etc/systemd/system/ && \
         sudo cp /opt/TouchKio/systemd/zoe-kiosk-watchdog.service /etc/systemd/system/ && \
         sudo cp /opt/TouchKio/systemd/zoe-kiosk-watchdog.timer /etc/systemd/system/ && \
         sudo systemctl daemon-reload && \
         sudo systemctl enable zoe-kiosk.service zoe-kiosk-watchdog.timer"

# ── 5. LightDM autologin ─────────────────────────────────────────────────────
echo "Step 5: Configuring LightDM autologin..."
ssh_cmd "sudo sed -i 's/#autologin-user=/autologin-user=${REMOTE_USER}/' /etc/lightdm/lightdm.conf 2>/dev/null || true && \
         sudo sed -i 's/#autologin-session=/autologin-session=openbox/' /etc/lightdm/lightdm.conf 2>/dev/null || true"

# ── 6. Openbox autostart ─────────────────────────────────────────────────────
echo "Step 6: Installing Openbox autostart..."
ssh_cmd "mkdir -p /home/${REMOTE_USER}/.config/openbox && \
         cp /opt/TouchKio/openbox-autostart.sh /home/${REMOTE_USER}/.config/openbox/autostart && \
         chmod +x /home/${REMOTE_USER}/.config/openbox/autostart"

# ── 7. Permissions ───────────────────────────────────────────────────────────
echo "Step 7: Setting permissions..."
ssh_cmd "sudo chmod +x /opt/TouchKio/*.sh /opt/TouchKio/wifi-portal/*.sh && \
         sudo chmod 644 /opt/TouchKio/config.json /opt/TouchKio/*.html /opt/TouchKio/*.js /opt/TouchKio/*.py 2>/dev/null || true && \
         sudo chown -R ${REMOTE_USER}:${REMOTE_USER} /opt/TouchKio"

# ── 8. NetworkManager for WiFi AP ────────────────────────────────────────────
echo "Step 8: Enabling NetworkManager..."
ssh_cmd "sudo systemctl enable NetworkManager 2>/dev/null || true && \
         sudo systemctl start NetworkManager 2>/dev/null || true"

# ── 9. Chromium policy (kiosk mode, ignore cert errors) ─────────────────────
echo "Step 9: Installing Chromium managed policy..."
ssh_cmd "sudo mkdir -p /etc/chromium/policies/managed/ && \
         echo '{\"ChromeOsMultiDisplayMode\": 0, \"AllowDinosaurEasterEgg\": false}' | \
         sudo tee /etc/chromium/policies/managed/zoe-touch.json > /dev/null"

# ── 10. Reset provisioning flags ─────────────────────────────────────────────
echo "Step 10: Removing .provisioned flag (triggers first-boot flow)..."
ssh_cmd "rm -f /opt/TouchKio/.provisioned /opt/TouchKio/.setup_wifi_done"

echo ""
echo "=== Setup complete! ==="
echo "Reboot the Pi to start the first-boot provisioning flow:"
echo "  ssh ${REMOTE_USER}@${REMOTE_HOST} 'sudo reboot'"
