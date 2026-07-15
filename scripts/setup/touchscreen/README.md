# Touchscreen Config Bundle (`<TOUCH_PANEL_IP>`)

This directory stores version-controlled templates for the Zoe touchscreen kiosk (`zoe-touch`).

## Live panel state — current source of truth (verified 2026-06-26)

`config.json` here is a **byte-exact mirror of what the `zoe-touch` panel (192.168.1.61)
actually runs.** `start-kiosk.sh` and `systemd/zoe-kiosk.service` are the same live capture
**plus review hardening deltas not yet deployed to the panel** (see below). How it boots today:

- systemd **`zoe-kiosk.service`** runs as user **`pi`** (`/etc/systemd/system/zoe-kiosk.service`)
  → execs `/opt/TouchKio/start-kiosk.sh` → launches **`chromium-browser --kiosk`**.
- It loads `config.json`'s `url` = **`/touch/home.html`** (the estate — the panel chrome;
  it superseded both `dashboard.html` and the retired `skybridge.html`).
- `start-kiosk.sh` passes **`--use-fake-ui-for-media-stream`**, which auto-grants the real
  microphone — in `--kiosk` mode Chromium can't show the mic prompt, so `getUserMedia` used
  to hang and the voice UI fell back to typing. Security note: the kiosk auto-grants mic to
  whatever it loads — acceptable for a single-purpose panel meant to listen.

### Tracked hardening deltas vs the live capture (deploy to the panel to reconcile)

The launcher/unit tracked here intentionally improve on the 2026-06-26 capture; the live
panel still runs the pre-hardening versions until these are deployed:

- **DevTools loopback-only** — live panel exposes `--remote-debugging-port=9222` on all
  interfaces with `--remote-allow-origins=*` (any LAN device can drive the kiosk browser,
  which auto-grants the mic). Tracked launcher binds `127.0.0.1` and drops the allow-origins
  flag; debug from the host via `ssh -L 9222:127.0.0.1:9222`.
- **Local fallback URL** — live panel falls back to `zoe.the411.life/touch/dashboard.html`
  (Cloudflare-blocked from the panel + retired surface); tracked launcher falls back to the
  local LAN estate URL from `config.json`.
- **Chromium binary resolution** — `chromium-browser` then `chromium`, loud failure if absent.
- **Network gating** — unit waits for `network-online.target`; the launcher pings the host
  from the configured URL and exits non-zero on total failure so systemd retries instead of
  booting into a connection-error page.
- **Provision-mode recovery** — panels installed via the provisioning flow re-enter the local
  provisioning UI when `.provisioned`/token are missing. Gated on `provision-server.py`
  existing on the device, so the live panel (which does not use the provisioning flow) still
  boots straight into the kiosk.

> ⚠️ **Divergence to reconcile separately.** The deploy instructions below (Option A/B,
> `--user zoe`, `~/.config/autostart/*.desktop`) and the provisioning helpers
> (`install_touchscreen.sh`, `provision-server.py`, `provision.html`, `qrcode.min.js`,
> `wifi-portal/`) describe an **earlier provisioning/autostart setup that the current panel
> does NOT use** — it runs as `pi` via the systemd service above, and `/opt/TouchKio/` has
> none of the provisioning files. They are kept for reference; decide whether to retire or
> re-align them in a follow-up.

## Files

- `config.json` -> `/opt/TouchKio/config.json`
- `start-kiosk.sh` -> `/opt/TouchKio/start-kiosk.sh`
- `zoe-kiosk.desktop` -> `/home/zoe/.config/autostart/zoe-kiosk.desktop`
- `display-rotation.desktop` -> `/home/zoe/.config/autostart/display-rotation.desktop`
- `force-rotate.desktop` -> `/home/zoe/.config/autostart/force-rotate.desktop`

## Deploy to touchscreen

### Option A: one-command installer (recommended)

```bash
scripts/setup/touchscreen/install_touchscreen.sh \
  --host <TOUCH_PANEL_IP> \
  --user zoe \
  --server-url https://<ZOE_SERVER_IP> \
  --panel-id zoe-touch-pi
```

If needed, pass an SSH key:

```bash
scripts/setup/touchscreen/install_touchscreen.sh --ssh-key ~/.ssh/zoe_pi_key
```

### Option B: manual file copy/install

```bash
PI_HOST=<TOUCH_PANEL_IP>
PI_USER=zoe

scp scripts/setup/touchscreen/config.json "${PI_USER}@${PI_HOST}:/tmp/config.json"
scp scripts/setup/touchscreen/start-kiosk.sh "${PI_USER}@${PI_HOST}:/tmp/start-kiosk.sh"
scp scripts/setup/touchscreen/zoe-kiosk.desktop "${PI_USER}@${PI_HOST}:/tmp/zoe-kiosk.desktop"
scp scripts/setup/touchscreen/display-rotation.desktop "${PI_USER}@${PI_HOST}:/tmp/display-rotation.desktop"
scp scripts/setup/touchscreen/force-rotate.desktop "${PI_USER}@${PI_HOST}:/tmp/force-rotate.desktop"
```

Then on the touchscreen:

```bash
sudo install -m 0644 /tmp/config.json /opt/TouchKio/config.json
sudo install -m 0755 /tmp/start-kiosk.sh /opt/TouchKio/start-kiosk.sh
install -m 0644 /tmp/zoe-kiosk.desktop ~/.config/autostart/zoe-kiosk.desktop
install -m 0644 /tmp/display-rotation.desktop ~/.config/autostart/display-rotation.desktop
install -m 0644 /tmp/force-rotate.desktop ~/.config/autostart/force-rotate.desktop
```

## Notes

- Keep URL, `panel_id`, and kiosk args consistent with production server routing.
- If Chromium cache behavior changes, confirm `services/zoe-ui/dist/sw.js` version bump is included.
