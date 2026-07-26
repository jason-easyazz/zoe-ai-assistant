# Touch Panel Skill

Zoe drives physical kiosk panels via `panel_*` MCP tools and can SSH into them for diagnostics and repair.

## When to use this skill

- User asks about the touch screen / panel / kiosk
- Diagnosing why the panel is blank, frozen, or showing wrong content
- Restarting, updating, or re-provisioning a panel
- Controlling what shows on the panel (navigate, announce, smart-home overlay, etc.)
- Registering a new panel or managing panel tokens

---

## Current hardware (production)

| Field | Value |
|---|---|
| Hostname | `zoe-touch` |
| IP | `192.168.1.61` |
| SSH user | `pi` |
| OS | Debian 12 aarch64 |
| Panel ID | `zoe-touch-pi` |
| Zoe server | `192.168.1.218` |

**NEVER use `zoe.the411.life` from or for the panel** — Cloudflare Access blocks it with HTTP 302.

---

## On-device software stack

- `zoe-kiosk.service` — systemd service managing Chromium kiosk
- `zoe-kiosk-watchdog.timer` — restarts kiosk if dead, every 60s
- `/opt/TouchKio/start-kiosk.sh` — Chromium launcher
- `/opt/TouchKio/config.json` — `{ server_url, panel_id, token }`
- `/opt/TouchKio/.provisioned` — flag file; absent = first-boot mode
- `/opt/TouchKio/provision-server.py` — local HTTP server (WiFi + QR provisioning)
- `/opt/TouchKio/wifi-portal/` — WiFi captive portal bundle
- Chromium CDP remote debug: `localhost:9222`
- Audio: Jabra Speak 750 (`/etc/asound.conf`)

---

## Panel MCP tools

Call these directly — they queue actions for the kiosk browser poll loop:

```
panel_navigate(url, panel_id)          → Load URL full-screen
panel_clear(panel_id)                  → Return to ambient dashboard
panel_announce(text, panel_id)         → TTS on speaker
panel_set_mode(mode, panel_id)         → ambient / listening / thinking / responding
panel_request_auth(panel_id, context)  → Show PIN pad
panel_check_auth(challenge_id)         → Check PIN result
panel_show_smart_home(...)             → HA control overlay
panel_show_media(...)                  → Now-playing card
panel_browser_screenshot(url, panel_id)→ Screenshot to fullscreen
panel_ssh_exec(panel_id, command)      → Run SSH command on panel
```

Default panel: `zoe-touch-pi`. Use `panel_id="all"` for all panels.

**panel_ssh_exec** looks up IP/user/key from the panel registry DB. Use it for diagnostics instead of raw SSH.

---

## SSH commands (via panel_ssh_exec or direct)

```bash
panel_ssh_exec("zoe-touch-pi", "systemctl status zoe-kiosk")
panel_ssh_exec("zoe-touch-pi", "sudo systemctl restart zoe-kiosk")
panel_ssh_exec("zoe-touch-pi", "journalctl -u zoe-kiosk -n 50")
panel_ssh_exec("zoe-touch-pi", "cat /opt/TouchKio/config.json")
panel_ssh_exec("zoe-touch-pi", "curl -s http://localhost:9222/json/list")
panel_ssh_exec("zoe-touch-pi", "aplay -l")
panel_ssh_exec("zoe-touch-pi", "sudo reboot")
```

---

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Black screen | `systemctl status zoe-kiosk` | `sudo systemctl restart zoe-kiosk` |
| Wrong page | `cat /opt/TouchKio/config.json` | Fix `url`, restart |
| Keeps crashing | `journalctl -u zoe-kiosk -n 100` | Check disk space, watchdog |
| No audio | `aplay -l` | Replug Jabra, check `/etc/asound.conf` |
| Panel not in DB | `GET /api/panels` | `POST /api/panels/register` |
| Cloudflare 302 | Panel hitting `zoe.the411.life` | Change config to LAN IP |
| Stuck in provision | Token missing in config | Re-provision or issue token via API |

---

## Backend API

```
GET  /api/panels                           → list registered panels
POST /api/panels/register                  → register new panel
GET  /api/panels/{id}/status               → live status
POST /api/panels/{id}/token                → issue device token
GET  /api/panels/{id}/bindings             → user bindings
PUT  /api/panels/{id}/bindings             → set user bindings
POST /api/panels/provision/request         → request pairing code (first-boot)
GET  /api/panels/provision/{code}          → poll pairing status
POST /api/panels/provision/{code}/confirm  → confirm pairing (from phone)
```

---

## Browser visibility architecture

The Pi is a **thin kiosk client** — it runs Chromium pointing at Zoe's nginx. All browser automation (research, HA setup, etc.) runs headless on the Jetson. Use `panel_browser_screenshot` to show the user what Zoe is doing:

```python
panel_browser_screenshot(navigate_to="https://example.com", caption="Searching...", panel_id="zoe-touch-pi")
```

---

## Voice path

Wake word runs on Pi → STT via `POST /api/voice/transcribe` (whisper.cpp on Jetson) → `POST /api/voice/command`. Uses `X-Device-Token` header.

---

## Multi-panel support

Always call `GET /api/panels` to discover panels dynamically. Never hardcode `192.168.1.61`. The registry returns `ip_address`, `ssh_user`, `ssh_key_path`, `ssh_port`.

---

## Deploy runbook

```bash
# From Jetson repo root — deploy scripts/config to panel
scripts/setup/touchscreen/install_touchscreen.sh \
  --host 192.168.1.61 --user pi \
  --server-url https://192.168.1.218 --panel-id zoe-touch-pi

# After HTML/JS changes
docker compose restart zoe-ui
panel_ssh_exec("zoe-touch-pi", "sudo systemctl restart zoe-kiosk")
```

## Escalation

- Panel MCP tools (display/audio/navigate): handle directly
- SSH diagnostics: use `panel_ssh_exec`
- Code changes to Zoe source: escalate to OpenClaw (has bash + file editing)
- Code changes to touch HTML pages: Cursor IDE agent
