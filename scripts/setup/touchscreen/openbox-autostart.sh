#!/usr/bin/env bash
# Openbox autostart — installed to ~/.config/openbox/autostart by prepare-pi-fresh.sh

# Disable screen blanking and DPMS power management
xset s off
xset s noblank
xset -dpms

# Hide cursor after 0.5s idle
unclutter -idle 0.5 -root &

# Determine boot path
if [[ ! -f /opt/TouchKio/.provisioned ]]; then
  # First boot (or re-provisioning) — orchestrate WiFi + QR flow
  exec /opt/TouchKio/first-boot.sh
else
  # Normal operation
  exec /opt/TouchKio/start-kiosk.sh
fi
