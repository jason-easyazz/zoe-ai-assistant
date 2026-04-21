# Raspberry Pi Touchscreen Stack (192.168.1.61)

This document is the source of truth for the physical touchscreen device at `192.168.1.61` (`zoe-touch`).

## Device Identity

- Hostname: `zoe-touch`
- IP: `192.168.1.61`
- OS: Debian 12 (bookworm)
- User: `pi`
- Architecture: `aarch64`
- Role: kiosk touch panel client for Zoe UI

## What This Device Does

The touchscreen does not host the full Zoe backend stack. It runs Chromium in kiosk mode and loads the touch dashboard from the Zoe server:

- Active URL: `https://192.168.1.218/touch/dashboard.html?panel_id=zoe-touch-pi&kiosk=1`
- Purpose:
  - display touch UI
  - send panel actions/events
  - run wake/voice client workflow on panel side

## Software Stack on Device

- Chromium kiosk session with remote debugging enabled (`9222` on localhost)
- X/desktop autostart entries for rotation and kiosk boot behavior
- TouchKio launcher scripts under `/opt/TouchKio/`
- Optional watchdog + audio defaults (see rollout report)

Reference rollout details: `docs/guides/zoe_touch_pi_rollout_report_2026-03-23.md`.

## Canonical Runtime Configuration (Observed)

Chromium process is launched with the following important arguments:

- `--kiosk`
- `--remote-debugging-port=9222`
- `--remote-debugging-address=0.0.0.0`
- `--user-data-dir=/home/pi/.config/chromium-kiosk`
- `--touch-events=enabled`
- `--ignore-certificate-errors`
- URL target: `https://192.168.1.218/touch/dashboard.html?panel_id=zoe-touch-pi&kiosk=1`

## Files and Configurations that Define this Device

### On the touchscreen (`192.168.1.61`)

- `/opt/TouchKio/start-kiosk.sh`
- `/opt/TouchKio/config.json`
- `/home/pi/.config/autostart/zoe-kiosk.desktop`
- `/home/pi/.config/autostart/display-rotation.desktop`
- `/home/pi/.config/autostart/force-rotate.desktop`
- `/home/pi/force-rotate.sh`
- `/etc/chromium/policies/managed/zoe-touch.json` (if policy-managed)
- `/usr/local/bin/zoe-kiosk-watchdog.sh` and related service/timer (if enabled)

### In this repository (authoritative source)

- `services/zoe-ui/dist/touch/` (all touchscreen pages)
- `services/zoe-ui/dist/sw.js` (cache/version behavior)
- `services/zoe-ui/dist/js/touch-ui-executor.js` (panel action execution)
- `services/zoe-ui/dist/js/auth.js` (kiosk auth bypass behavior)
- `services/zoe-data/routers/panel_auth.py`
- `services/zoe-data/ui_orchestrator.py`
- `services/zoe-data/routers/chat.py` (voice/panel orchestration path)
- `services/zoe-ui/nginx.conf` (routing/proxy behavior)

## Managed Templates Added for This Device

The repo now includes touchscreen deployment templates in:

- `scripts/setup/touchscreen/`

These files are intended to mirror and version-control the runtime config used on `192.168.1.61`.

## Operational Rules

- Treat `192.168.1.61` as a dedicated kiosk endpoint.
- Do not run unrelated services on this device.
- Any kiosk/autostart/rotation changes must be reflected in `scripts/setup/touchscreen/`.
- Any touch UI behavior changes must be reflected in `services/zoe-ui/dist/touch/`.

## Update + Verification Flow

1. Update and push code from this repo.
2. Ensure `zoe-ui` on server is serving updated files.
3. Force reload kiosk (CDP or browser restart on touchscreen).
4. Verify:
   - page loads `touch/dashboard.html`
   - actions acknowledge in panel flow
   - service worker version matches expected
   - kiosk restarts correctly after reboot

## Ownership

This device profile is part of the production stack and should be treated as configuration-managed infrastructure, not ad-hoc local setup.
