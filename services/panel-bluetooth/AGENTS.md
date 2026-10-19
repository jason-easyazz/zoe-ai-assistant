# services/panel-bluetooth — the panel Pi's Bluetooth agent (Settings > Bluetooth devices, flag-dark)

## Purpose

One small service per panel Pi that does what the household asks in Settings > Bluetooth devices: scan, connect, disconnect, forget, read device info, and run per-device drivers (generic; Omi pendant with battery, charging, sync status and a gated firmware update). Each panel has its own radio, so each runs its own agent. It PULLS commands from zoe-data (outbound HTTPS, the panel's device token) and opens **no listening socket**. Design, facts and operator steps: [`docs/knowledge/bluetooth-settings.md`](../../docs/knowledge/bluetooth-settings.md). It runs as its OWN process and unit (`scripts/setup/systemd/zoe-bluetooth.service`, operator opt-in, never auto-enabled; deploy: `scripts/setup/deploy-pi-bluetooth.sh`) — **not** inside `zoe_voice_daemon.py` and not in omi-sync.

## Ownership

- `panel_bt/model.py` — the wire shapes: `Advert`, `Command` + `parse_command` (whitelisted ops, well-formed ids), `AgentError` + error codes, the opaque `device_id` hash (16 hex; THE SAME derivation as `omi_sync.uploader.device_hash`, so the id Settings > Bluetooth shows for a pendant is the id its uploads carry and the id zoe-data's `omi_devices` registry assigns to a member — pinned by `tests/unit/test_omi_device_identity.py`), `version_tuple`.
- `panel_bt/config.py` — env config; the update floors are CLAMPED here (battery >= 50, median RSSI better than -75) so configuration can only be stricter.
- `panel_bt/backend.py` — the radio behind `BleBackend` / `BleLink`; `BleakBackend` is the real one (the `hcitool lecc` recipe ported from omi-sync, adapter selection `PANEL_BT_HCI`, RSSI sampling).
- `panel_bt/agent.py` — `PanelBluetoothAgent`: executes commands, owns the single live link + its lease, the radio lock, the outbox, the poll loop.
- `panel_bt/radio_lock.py` — the advisory `flock` shared with `omi_sync.radio_lock` (same path, same semantics).
- `panel_bt/known.py` — the Pi-local known-devices file (the ONLY place an address is persisted; 0600).
- `panel_bt/zoe_link.py` — the only network path (poll/report, stdlib urllib).
- `panel_bt/drivers/` — `base.py` (interface + standard DIS/Battery reads), `generic.py`, `omi.py` (info, sync status, update check), `omi_release.py` (the pinned release: load, download-verify, MCUboot image checks), `omi_dfu.py` (the gates + the staged procedure, SMP behind an injected `SmpSession`), `__init__.py` (`DriverRegistry`).
- `panel_bt/bond.py` — THE ONE place on the Pi that creates a Bluetooth bond: `omi_bond`, an Omi pendant on Zoe phase-2 firmware (`labs/omi-firmware`), owner-run. Refuses a pendant whose DIS version is not Zoe's (4 parts); makes the adapter pairable ONLY for the pairing (`PairableTimeout` 60 written AND read back - else never `Pairable` - then `Pairable` true, false again in `finally`); pairs with no agent (LE SC Just Works, initiator); success = BlueZ `Paired` AND `Bonded` read true AND the link `ENCRYPT`ed; `replace` removes THIS pendant's old BlueZ entry only, and stops unless that is confirmed (the call succeeds or the device was unknown, AND `Paired` no longer reads true). Pinned by `tests/unit/test_omi_bond.py`.
- `panel_bt/smp_session.py` — the real smpclient 7.3.0 session (UNVERIFIED on hardware beyond the read-only info stage; lazy import).
- `panel_bt/data/omi_firmware.json` — THE pinned official release (URL, size, SHA-256, images). Changing it is a reviewed change.
- `requirements.txt` — `bleak==3.0.2`, `smpclient==7.3.0`, installed into the agent's OWN venv (omi-sync stays on bleak 0.22.3).

## Local Contracts

- **No inbound socket, ever.** Auth is the panel's device token on outbound calls. A device's BLE address never leaves the Pi: everything sent speaks `device_id`. The Omi driver's "Assigned to" control (which household member a pendant's recordings sync to) is a zoe-data/UI concern (`/api/omi/devices`): this agent knows nothing about owners and sends no user id.
- **One radio.** Every scan / open link / update holds `RadioLock`; omi-sync yields to it. A link is a lease (`PANEL_BT_LEASE_S`, hard cap `PANEL_BT_MAX_HOLD_S`). One live link at a time. One task at a time (`status` and `update_cancel` are always answered).
- **No pairing, no agent, no bond - with one reviewed exception.** Devices are connected as omi-sync connects the pendant. The exception is `omi_bond` (`bond.py`): an Omi pendant on phase-2 firmware, flag `PANEL_BT_BOND_ENABLED` (default OFF), an explicit `confirm` AND `ack_wipe` (the pendant crypto-wipes its stored audio on every new bond), a refusal while omi-sync still holds unsynced records unless `force`, the radio lock, and the pendant's own button window (two double-taps). After it, the pendant's storage, audio and SMP need that bond, so `omi-sync` (`OMI_SYNC_REQUIRE_BOND`) and this agent's firmware update ride it. Any other device that needs a bond is a new reviewed addition, never a driver doing it quietly.
- **Updates: every gate blocks, nothing retries.** The Omi update proceeds only through `omi_dfu.run_omi_update`: all gates, a verified pinned package, both secondary hashes checked before marking, exactly two pending before the reset, the first doubt stops it, a failure starts a persisted cool-down. The bootloader is overwrite-only: there is NO revert, so the UI must keep saying so.
- Flag-dark: `PANEL_BT_ENABLED` unset/0 → `python -m panel_bt run` exits 0 without importing bleak. `PANEL_BT_OTA_ENABLED` (default OFF) separately gates firmware updates; `PANEL_BT_BOND_ENABLED` (default OFF) gates `omi_bond`.

## Forbidden

- Never open a listening socket (no aiohttp server, `http.server`, `bind`/`listen`) — pinned by a static test.
- Never pair, remove a device, or touch the adapter's `Pairable` anywhere but `bond.py`; never unpair / trust / register a BlueZ agent anywhere (bond.py included); never call `write_gatt_char` directly (SMP goes through smpclient inside `smp_session.py` only) — pinned.
- Never send an erase-settings request, a storage-service command (`30295781`), `CMD_RING_CLEAR` or the legacy `DELETE_FILE`. Never touch Omi's cloud (`api.omi.me`), and never fetch firmware from anywhere but the pinned URL, nor use it unless it hashes to the pinned SHA-256.
- Never import `omi_sync`, `zoe-data` modules, `labs/` or the voice daemon; no audio/PulseAudio/ALSA code (it must never touch zoe-voice).
- Never log or send a device address, the device token, or free text from a device beyond bounded name/model fields.
- Never weaken a gate floor in code or config; never retry a failed update automatically; never read the DIS serial number.

## Work Guidance

- Keep decisions in `omi_dfu` (pure gates) and `agent`; `backend` and `smp_session` stay thin. A new behaviour gets a fake-radio test and an Edit-based negative control (the test files list the mutations).
- Third-party API: check `opensrc path pypi:bleak@3.0.2` and `pypi:smpclient@7.3.0` before changing `backend.py` / `smp_session.py`; the Omi facts come from `~/.opensrc/repos/github.com/BasedHardware/omi` and the operator's staged tool record.
- Hardware-only facts stay marked UNVERIFIED in `docs/knowledge/bluetooth-settings.md` until a supervised run closes them.

## Verification

- `pytest tests/unit/test_panel_bluetooth_agent.py tests/unit/test_panel_bluetooth_update.py tests/unit/test_omi_sync_radio_lock.py tests/unit/test_omi_bond.py -q` (all `ci_safe`; no hardware, no network, no bleak/smpclient).
- zoe-data side: `pytest services/zoe-data/tests/test_bluetooth_settings.py -q`; UI: `node services/zoe-ui/dist/test_bluetooth_settings.js`.
- On the Pi, read-only: `python -m panel_bt status`; `python -m panel_bt scan` (ids only).

## Child DOX Index

No child AGENTS.md files.
