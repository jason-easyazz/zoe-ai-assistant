# Touchscreen Config Bundle (`192.168.1.61`)

This directory stores version-controlled templates for the Zoe touchscreen kiosk (`zoe-touch`).

## Files

- `config.json` -> `/opt/TouchKio/config.json`
- `start-kiosk.sh` -> `/opt/TouchKio/start-kiosk.sh`
- `zoe-kiosk.desktop` -> `/home/pi/.config/autostart/zoe-kiosk.desktop`
- `display-rotation.desktop` -> `/home/pi/.config/autostart/display-rotation.desktop`
- `force-rotate.desktop` -> `/home/pi/.config/autostart/force-rotate.desktop`

## Deploy to touchscreen

### Option A: one-command installer (recommended)

```bash
scripts/setup/touchscreen/install_touchscreen.sh \
  --host 192.168.1.61 \
  --user pi \
  --server-url https://192.168.1.218 \
  --panel-id zoe-touch-pi
```

If needed, pass an SSH key:

```bash
scripts/setup/touchscreen/install_touchscreen.sh --ssh-key ~/.ssh/zoe_pi_key
```

### Option B: manual file copy/install

```bash
PI_HOST=192.168.1.61
PI_USER=pi

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
