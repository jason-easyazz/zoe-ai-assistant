# TouchKio — Zoe touch-panel kiosk config

The Zoe touch panel (a Raspberry Pi, hostname `zoe-touch`) runs Chromium in kiosk
mode, pointed at the Skybridge UI served by the Jetson. These files live on the
**panel device** under `/opt/TouchKio/` and `/etc/systemd/system/`, **not** in the
zoe-data deploy pipeline — they were previously untracked. This directory is the
source of truth so device-side changes are reviewable and reproducible.

| File | Lives on the panel at | Purpose |
|---|---|---|
| `start-kiosk.sh` | `/opt/TouchKio/start-kiosk.sh` | launches Chromium with the kiosk flags |
| `config.json` | `/opt/TouchKio/config.json` | kiosk URL (Skybridge) + display/rotation |
| `zoe-kiosk.service` | `/etc/systemd/system/zoe-kiosk.service` | systemd unit (runs as `pi`, restarts on exit) |

## Key flags (`start-kiosk.sh`)
- `--kiosk` + `--user-data-dir=/home/pi/.config/chromium-kiosk` — fullscreen, dedicated profile.
- `--remote-debugging-port=9222 --remote-debugging-address=0.0.0.0` — lets the panel be inspected/driven over the LAN (used for verification).
- `--autoplay-policy=no-user-gesture-required` — audio output without a tap.
- **`--use-fake-ui-for-media-stream`** — auto-grants the **real** microphone. In `--kiosk` mode Chromium can't show or grant the mic permission prompt, so `getUserMedia` hangs and the voice UI falls back to "type here while voice reconnects". This flag fixes that (auto-accepts with the real device). Security note: the kiosk auto-grants mic to whatever it loads — acceptable for a single-purpose panel that is meant to listen.

## Deploy to the panel
```bash
sudo cp deploy/touchkio/start-kiosk.sh    /opt/TouchKio/start-kiosk.sh
sudo cp deploy/touchkio/config.json       /opt/TouchKio/config.json
sudo cp deploy/touchkio/zoe-kiosk.service /etc/systemd/system/zoe-kiosk.service
sudo systemctl daemon-reload
sudo systemctl restart zoe-kiosk.service
```
The kiosk reloads Chromium and boots straight into Skybridge.
