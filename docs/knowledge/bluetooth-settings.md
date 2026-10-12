---
type: Reference
title: Settings > Bluetooth devices (ZOE_BLUETOOTH_SETTINGS, flag-dark)
description: A general Bluetooth section in Settings - pick a panel, scan, connect, disconnect, forget and update - with per-device drivers (generic; Omi pendant with battery, firmware and a gated firmware update). How it is built, why it pulls instead of proxies, the update safety facts from the supervised 2026-10-12 attempt, the operator deploy steps and the rollback.
tags: [bluetooth, settings, omi, panel, firmware, dfu, flag-dark, ui]
timestamp: 2026-10-12T23:00:00+08:00
---

# Settings > Bluetooth devices

**Owner ask (2026-10-12):** "build something in settings to scan, connect, disconnect and update, should it just be a Bluetooth setting section with those abilities in case we want to connect other devices?" Yes - general, with per-device drivers. Household members never run a command: everything is a button, big enough for a finger on the panel.

**Status: built, flag-dark, NOT deployed.** `ZOE_BLUETOOTH_SETTINGS` (zoe-data, default off) and `PANEL_BT_ENABLED` / `PANEL_BT_OTA_ENABLED` (each panel Pi, default off). The firmware-update path is **unverified on hardware** beyond a read-only `info` stage - see "What is verified".

## Shape

```
household (desktop Settings / panel Settings)                zoe-data                         each panel Pi
  js/bluetooth-settings.js  --admin session-->  routers/bluetooth.py  <--device token--  services/panel-bluetooth (panel_bt)
  scan / connect / disconnect /                 bluetooth_broker.py      long-poll + report   BlueZ (bleak 3.0.2),
  forget / update + progress                    per-panel command queue                       drivers: generic, omi
                                                audit log (JSONL)                             shared radio lock with omi-sync
```

- **Pull, not proxy.** The panel's agent opens **no listening socket**. It long-polls `POST /api/bluetooth/agent/poll` with the panel's device token (the same one the voice daemon and omi-sync use) and posts outcomes to `/agent/report`. Why: zoe-data stores only token *hashes*, so it could not authenticate a call *to* a panel; the existing panel agent on :8765 is unauthenticated, bound to `0.0.0.0` and not in this repo; and "never open to the LAN without auth" is satisfied most simply by not opening anything. The "proxy to a chosen panel" is therefore a per-panel command queue in `bluetooth_broker.py` (in memory - jobs live minutes; the durable record is the audit log).
- **One component, two surfaces.** `dist/js/bluetooth-settings.js` renders both the desktop Settings section (`settings.html`, admin group, nav item hidden until `GET /api/bluetooth/config` answers 200) and the touch estate Settings section (`touch/settings.html`, hidden until the admin check passes and the server says the feature is on). New file only: no shared `js/` file changed, no desktop-to-touch link. Touch buttons are >= 48 px.
- **A device's address never leaves the Pi.** The agent speaks in `device_id` = 12 hex of a salted SHA-256. zoe-data, the browser and the audit log only see that id. The address lives in `~/.zoe-bluetooth/known.json` (0600) and the scan cache.
- **Auth.** Household routes: `require_admin` **and** not a device token (a token resolving to an admin-ish user is refused). Panel routes: a valid device token only; the panel id comes from the token, and a panel can only report on its own jobs. Everything a panel sends is truncated/bounded before it is stored or shown, and the UI escapes it.
- **Audit.** Every connect, disconnect, forget and firmware-update request **and its outcome** is appended to `ZOE_BLUETOOTH_AUDIT_PATH` (default `~/.zoe/bluetooth-audit.jsonl`, 0600): who (user id, role), panel, device id, job id, outcome, error code. No addresses, tokens or free text. If the request line cannot be written the action is **refused** (fail closed).

## The radio is shared

One radio, and the Omi accepts **one** connection. Every radio use - a scan, an open link, a whole firmware update - holds an advisory `flock` (`~/.zoe-ble-radio.lock`; the Bluetooth agent and `omi_sync.ble_link` both take it; the file names the holder). `omi-sync` does not connect while the agent holds it (it reports "not reachable" and retries on its own timer), which is what stops a sync from taking the pendant's single connection in the gap after a firmware reset. A "Connected" device is a **lease**: released `PANEL_BT_LEASE_S` (120 s) after the UI stops touching it and at the latest `PANEL_BT_MAX_HOLD_S` (600 s) after it opened, so a forgotten Connected can never starve recording sync. Connecting a second device releases the first.

Connection recipe (ported from `omi_sync/ble_link.py`, verified on the real pendant 2026-10-12): `sudo -n hcitool [-i hciN] lecc [--random] <addr>` first (32 s supervision timeout; BlueZ's 420 ms default dies at panel range), then bleak attaches; no pairing, no bond, no agent; retry with backoff; `cmd 0x08 0x000E` cancels a stuck create-connection.

**Adapter choice.** The panel's built-in radio is weak (about -91 dBm even right beside the panel - likely the enclosure shielding the Pi's antenna). `PANEL_BT_HCI=hci1` selects a USB Bluetooth adapter (validated `hciN`, used for scan, connect and `hcitool -i`). The UI recommends moving the pendant closer **or** a USB adapter whenever the signal gate fails.

## Drivers

`panel_bt/drivers/` - a registry keyed by advert (service UUID, local name, manufacturer ids). The generic driver matches everything at the lowest score, so every device is connectable.

| driver | match | gives |
|---|---|---|
| `generic` | anything | connect / disconnect / forget; Device Information (maker, model, firmware, hardware) and Battery - only the characteristics the device has. Serial number is deliberately not read. |
| `omi` | service `19b10000-e8f2-537e-4f6c-d104768a1214` (or name `Omi`) | model, firmware, battery, **charging** (`19b10013`), recording-sync status (read-only from omi-sync's spool), update check and **Update firmware** |

Adding a device: a new `Driver` subclass (`match`, `info`, optional `update_check` / `run_update`) registered in `DriverRegistry`; no UI change for connect/disconnect/forget/info.

## Omi firmware update - the facts the design rests on

From the supervised 2026-10-12 attempt (`/home/zoe/.zoe/agent-tools/omi_dfu_2026-10-12.md`; it stopped safely at the signal gate, nothing was written):

- **Overwrite-only bootloader** (MCUboot mode 2, downgrade prevention on, multi-image net-core update). After the reset MCUboot COPIES the new image over the old one. **There is no revert and no test mode**; test and confirm are the same thing; **a power loss during the copy is unrecoverable over Bluetooth.** The UI says this in words, requires an explicit "I understand it cannot be undone" tick, and repeats "Do not move or unplug for 2 minutes" after the restart.
- **Two images** in the official OTA zip: 0 = app core `omi.signed.bin`, 1 = net-core radio `ipc_radio.bin`. Both are uploaded; **both secondary-slot hashes are verified against the zip** before anything is marked; the reset is sent only if exactly two images are pending.
- **No settings-partition erase** (Omi's own app sends it; it may change the static-random address omi-sync addresses). We never send it; a test pins that.
- **Pinned release, no cloud.** `panel_bt/data/omi_firmware.json` pins the official GitHub release asset (v3.0.21: URL, size 424966, SHA-256 `9fb93885...d3674`, minimum firmware 3.0.6, model "Omi CV 1"). Nothing calls `api.omi.me` (a test pins that); there is no "latest" lookup. The download is accepted only if it hashes to the pin; each image must be a well-formed MCUboot image whose own SHA-256 TLV matches. Moving to a newer firmware is a reviewed edit of the pin.

**Safety gates** (`drivers/omi_dfu.py` `evaluate_gates`; each blocks, each has a test; configuration may make them stricter, never weaker): updates switched on for the panel; model matches the pin; firmware readable, older than the pin and not older than the release's minimum; **on its charger** (`19b10013` = 1); **battery >= 50 %**; **median advert RSSI better than -75 dBm** over >= 5 samples with <= 15 dB swing (measured fresh when Update is pressed, 12 s); no cool-down from a failed attempt; nothing else using the radio.

**Procedure** (stops on the first doubt; **never retries**): preflight (free our own link, sample the signal) -> download + verify -> connect + facts + remaining gates -> SMP session, refuse if a secondary slot is already pending -> upload image 0 then 1 (cancel allowed until here) -> verify both secondary hashes -> mark both permanent -> verify exactly two pending -> re-check charger + battery -> reset -> wait for the advert (<= 300 s) -> reconnect, expect the pinned version -> confirm the running image if still unconfirmed. A failed attempt starts a cool-down (`PANEL_BT_OTA_COOLDOWN_S`, default 600 s, persisted so a crash loop cannot become a retry loop); a refused gate or a cancel does not.

### What is verified

- Verified in CI (`tests/unit/test_panel_bluetooth_update.py`, fake radio + fake MCUboot): every gate blocks; the pinned release is verified; the stage order; no reset without two verified, pending images; no retry; cool-down; cancel only before the install is ordered; the post-restart paths.
- Verified on the real pendant: the connection recipe, DIS/battery reads, the SMP `info` stage (MTU 498, buffer 2475, bootloader mode 2, image list) - by the operator's staged tool.
- **UNVERIFIED on hardware:** `upload`, `mark`, `reset` and `post` through `panel_bt/smp_session.py` (a port of the staged tool, written against the smpclient 7.3.0 source). **The first real run must be supervised.** Also unmeasured: how long the post-reset copy takes on the real pendant (the 300 s wait is the staged tool's figure).
- After 3.0.18 -> 3.0.21 the pendant leaves the legacy file protocol (3.0.18 only) and speaks the ring protocol (>= 3.0.20): omi-sync switches path by itself (DIS 2A26), but re-run `python -m omi_sync check` after the first update.

## Flags and settings

| flag | where | default | meaning |
|---|---|---|---|
| `ZOE_BLUETOOTH_SETTINGS` | zoe-data | off | routes mounted (`/api/bluetooth/*`); off = 404 and the UI hides the section |
| `ZOE_BLUETOOTH_AUDIT_PATH` | zoe-data | `~/.zoe/bluetooth-audit.jsonl` | audit log |
| `PANEL_BT_ENABLED` | panel Pi | off | the agent runs at all (off = exits 0) |
| `PANEL_BT_OTA_ENABLED` | panel Pi | off | firmware updates allowed; leave off until one supervised update |
| `PANEL_BT_HCI` | panel Pi | BlueZ default | adapter, e.g. `hci1` |
| `PANEL_BT_LEASE_S` / `_MAX_HOLD_S` / `_SCAN_S` | panel Pi | 120 / 600 / 10 | link lease, hard hold cap, scan length |
| `PANEL_BT_OTA_MIN_BATTERY` / `_MIN_RSSI` / `_COOLDOWN_S` / `_MAX_S` | panel Pi | 50 / -75 / 600 / 1200 | floors (raise only), cool-down, overall time limit |
| `PANEL_BT_OTA_PIN_FILE` | panel Pi | bundled pin | alternate pinned-release file |
| `ZOE_BLE_RADIO_LOCK` | panel Pi (both services) | `~/.zoe-ble-radio.lock` | the shared radio lock |

## Operator steps (nothing here is done by the PR)

1. Merge; sync the Jetson from `origin/main` with `git merge --ff-only`; on the Jetson set `ZOE_BLUETOOTH_SETTINGS=1` and restart zoe-data (poll `/health`). Optionally `ZOE_BLUETOOTH_AUDIT_PATH`.
2. From a worktree of merged main: `DRY_RUN=1 bash scripts/setup/deploy-pi-bluetooth.sh --install-unit` to read the plan, then without `DRY_RUN`. It creates `~/.zoe-bluetooth/` (own venv: bleak 3.0.2 + smpclient 7.3.0), an env **template** (flags off), ships the two omi-sync files for the shared lock (omi-sync is not restarted unless `--restart-omi-sync`), and installs the user unit if absent. It never touches zoe-voice, the :8765 agent, the kiosk or an existing env file.
3. On the Pi edit `~/.zoe-bluetooth/.env.bluetooth`: `PANEL_BT_ENABLED=1`, `DEVICE_TOKEN` (the panel's device token), `ZOE_URL`; `systemctl --user enable --now zoe-bluetooth`. The panel appears in Settings > Bluetooth devices within a poll.
4. Smoke test from the Pi: `python -m panel_bt scan` (prints ids, names, signal, driver - never addresses). Do not scan/connect the pendant while someone else is using it.
5. Firmware updates: set `PANEL_BT_OTA_ENABLED=1` only for a supervised first update with the pendant ON ITS CHARGER next to the panel (or a USB adapter). After it: `python -m omi_sync check`.

## Rollback

`systemctl --user disable --now zoe-bluetooth` on the Pi (omi-sync is unaffected; with no lock holder its connect path is unchanged); unset `ZOE_BLUETOOTH_SETTINGS` and restart zoe-data (routes vanish, the UI hides the section). To remove completely: `rm -rf ~/.zoe-bluetooth ~/.config/systemd/user/zoe-bluetooth.service`, and restore the `.bak-<ts>` copies of the two omi-sync files if wanted. A firmware update itself cannot be rolled back (see above).

## Not done / follow-ups

- **Voice** ("connect my pendant", "is my pendant connected") is not wired: it would add an intent on the voice path (replay-gated). The broker is the seam - an intent would call the same `enqueue` for a named panel. Pinned in `docs/IDEAS.md`.
- A push channel for progress (the UI polls: 1 s while a job runs, 5 s idle).
- Several simultaneous links (the agent holds one; connecting another releases the first).
- The pairing flow for devices that need a bond: the agent never pairs (a test pins it); a driver that needs it would be a deliberate, reviewed addition.

Code: `services/panel-bluetooth/` (agent), `services/zoe-data/bluetooth_broker.py` + `routers/bluetooth.py`, `services/zoe-ui/dist/js/bluetooth-settings.js`, `services/omi-sync/omi_sync/radio_lock.py`. Tests: `tests/unit/test_panel_bluetooth_{agent,update}.py`, `tests/unit/test_omi_sync_radio_lock.py`, `services/zoe-data/tests/test_bluetooth_settings{,_ui_harness}.py`, `services/zoe-ui/dist/test_bluetooth_settings.js`.
