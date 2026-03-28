# Zoe Touch Pi Production Rollout (2026-03-23)

## Scope Completed
- Baseline audit on `zoe-touch` (`192.168.1.61`) via SSH.
- Kiosk hardening (boot-safe start, single startup path, watchdog timer).
- Audio reliability defaults for Jabra USB device.
- Real panel bind/state-sync/action/ack validation against UI orchestrator.
- Four end-to-end manipulation journeys with trace/action IDs.
- Recovery and monitoring artifacts added.

## Baseline (Collected)
- Host: `zoe-touch` / user `pi`
- OS: Debian 12 (bookworm)
- Boot target: `graphical.target`
- Browser: Chromium `139.0.7258.154`
- Display manager: `lightdm` active
- Kiosk service: `zoe-kiosk.service` active/enabled
- Audio devices:
  - Playback: `vc4-hdmi-0`, `vc4-hdmi-1`, `Jabra Speak 750`
  - Capture: `Jabra Speak 750`

## Hardening Implemented on Pi
- Updated `/opt/TouchKio/start-kiosk.sh`:
  - Waits for network/X readiness.
  - Uses persistent profile: `/home/pi/.config/chromium-kiosk`.
  - Uses kiosk URL from config.
  - Keeps display awake and cursor hidden.
- Updated `/opt/TouchKio/config.json` URL to:
  - `https://192.168.1.218/touch/dashboard.html?panel_id=zoe-touch-pi&kiosk=1`
- Disabled duplicate desktop autostart launch:
  - `~/.config/autostart/zoe-kiosk.desktop` set `X-GNOME-Autostart-enabled=false`
- Added watchdog:
  - `/usr/local/bin/zoe-kiosk-watchdog.sh`
  - `zoe-kiosk-watchdog.service`
  - `zoe-kiosk-watchdog.timer` (every 60s)
- Added audio defaults:
  - `/etc/asound.conf`
  - `/home/pi/.asoundrc`
- Added Chromium managed policy:
  - `/etc/chromium/policies/managed/zoe-touch.json`

## Backend/UI Changes Applied
- `touch-ui-executor.js`
  - Supports URL-forced panel ID via `?panel_id=...`.
  - Retry-safe dedupe key now uses `action_id:retry_count`.
- `auth.js`
  - Touch pages skip redirect auth gate in kiosk mode (`?kiosk=1`).
- `ui_actions.py`
  - Pending actions now include `retry_count` and `max_retries`.

## Real Panel Action-Loop Verification
- Confirmed live panel session:
  - `panel_id`: `zoe-touch-pi`
  - `user_id`: `family-admin`
  - `page`: `/touch/dashboard.html`

### Trace: `touch-final-1774266312`
- `27625e518fe4435b` (`notify`) -> `success` (`queued`, `ack:success`)
- `ee8e75cb8220465f` (`refresh`) -> `success` (`queued`, `ack:success`)
- `2826563deed84ebd` (`navigate`) -> `success` (`queued`, `ack:success`)
- `116db56f93404c19` (`navigate`) -> `success` (`queued`, `ack:success`)

### Retry Behavior Verification
- Action `682d036de9074596` (`click` invalid selector):
  - First attempt: `ack:failed` (`selector_not_found`)
  - Retry queued
  - Second attempt: `ack:failed` again
  - Ledger sequence: `queued`, `ack:failed`, `retry_queued`, `ack:failed`

## Important Routing Finding (Fixed in Runtime Config)
- `zoe.the411.life` from panel was blocked by Cloudflare Access (HTTP 302 to Access login).
- Panel now targets LAN endpoint `192.168.1.218`, which is reachable and returns `200`.

## Operational Status
- `zoe-kiosk.service`: active/enabled
- `zoe-kiosk-watchdog.timer`: active/enabled
- Real panel action ack loop: functional
- E2E UI manipulation journeys: passed
