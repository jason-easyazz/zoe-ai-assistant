# Omi phase-2 firmware: owner runbook (flash, pair, recover)

Nothing here has been run. Every step is an owner step, done with the pendant in hand next to the panel. An agent never
flashes, pairs or connects to the owner's pendant. Read [README.md](README.md) sections 1 to 3 first.

## A. Before anything (once)
1. **Buy a probe.** A SEGGER J-Link (the EDU Mini is enough for personal use), or an nRF5340 DK used through its
   "Debug out" header. You also need a way onto the pendant's SWD pins: Omi's flashing cable, or pogo pins on the
   test pads (TP4/TP5 and the 2x3 header in the mainboard schematic).
   - Open the case and confirm you can reach the pads **before** the first flash.
   - Install `nrfjprog` or `nrfutil device` and J-Link on a laptop or the Jetson.
   - Download the stock **3.0.21** `merged.hex` and `merged_CPUNET.hex` from the `Omi_CV1_v3.0.21` GitHub release and keep
     them. They are the way back.
   - A second CV1 to flash first is the real safeguard. Your call.
2. **Decide** (README section 3):
   - **Flash path:** OTA, app only (no probe needed to flash, but needed to recover), or SWD (needed for the hardened
     bootloader).
   - **Hardened bootloader** (your own MCUboot key, SWD only): yes or no.
   - **APPROTECT:** yes or no.
3. **Drain the pendant.** Let `omi-sync` finish a clean sync next to the panel (`journalctl --user -u omi-sync` shows
   `pending=0`). The new ring layout drops undrained audio at the first boot.

## B. Key ceremony (only for the hardened bootloader)
- Generate the key **offline, outside any repository**:
  - On the laptop: `imgtool keygen -k ~/zoe-omi-keys/zoe-mcuboot-rsa2048.pem -t rsa-2048`.
  - Then `chmod 600` it and make two offline backups (an encrypted USB stick and the household vault).
- **If you lose this key, the pendant can only be updated over SWD again.** Never paste it into chat, a PR, the repo or a
  log.
- Build with `ZOE_FW_SIGNING_KEY=~/zoe-omi-keys/zoe-mcuboot-rsa2048.pem bash labs/omi-firmware/build.sh`. The
  `merged.hex` then carries an MCUboot that trusts ONLY your key.
  - **This build can only go on over SWD.** The stock bootloader rejects its OTA image, which is what you want.
- Without the ceremony, `build.sh` signs with the upstream public dev key, the one the stock bootloader trusts. Use that
  for the OTA path.

## C. Build and check the image (any time; no device)
1. `bash labs/omi-firmware/fetch.sh && bash labs/omi-firmware/build.sh`. Note the `SHA256SUMS`.
2. `grep -E "ZOE_BLE_BONDED_ONLY|ZOE_RING_ENCRYPT|MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT|BT_SMP_SC_PAIR_ONLY" ~/.cache/zoe-omi-fw/out/app.config`
   must show all four `=y`.
3. Sanity check: `omi.signed.bin` must be smaller than the app slot in `boards/omi/pm_static.yml`, and its MCUboot TLVs must
   verify under the key you meant. The press-to-record research section 10 has the recipe.

## D1. Flash over SWD (recommended for the first time: it is also the recovery path)
1. Pendant on its charger. Probe attached.
2. Run `nrfjprog --recover`. This wipes both cores, the bootloader, the bonds and the settings. The ring on the NAND stays,
   and the new firmware re-initialises it.
3. `nrfjprog -f NRF53 --coprocessor CP_NETWORK --program merged_CPUNET.hex --verify`
4. `nrfjprog -f NRF53 --coprocessor CP_APPLICATION --program merged.hex --verify --reset`
5. **Check the pendant's Bluetooth address is unchanged** with a read-only scan from the panel. It is expected to come from
   the chip (FICR), but this is [U]. If it changed, update `OMI_PENDANT_ADDRESSES` on the Pi and re-assign the pendant in
   Settings (the device id is derived from the address).
6. Go to section E.

## D2. Flash over the air, app core only (needs a follow-up in panel-bluetooth first)
- The panel's updater insists on the official two-image package. The app-only "custom pin" mode the press-to-record
  research describes (section 5.3, item 5) is not built yet.
- Until it is, do not hand-drive an SMP tool against the pendant. Use D1, or wait for that PR.
- When it exists, the steps are the existing supervised update (`docs/knowledge/bluetooth-settings.md`: on the charger,
  battery at least 50 %, median RSSI better than -75 dBm, no retries), with **image 0 only** = `omi.signed.bin` from this
  build.
- The update itself does not need a bond: stock SMP is open. Every update **after** it does need one.

## E. First pairing (panel and pendant side by side)
**Pairing a pendant WIPES it.** Every new or replacement bond crypto-wipes the pendant: its audio key is destroyed and its
ring emptied. **Sync before re-pairing**: let `omi-sync` finish a clean visit first. The panel refuses while omi-sync still
holds unsynced recordings, unless forced.

**The Settings "Pair securely" button is not built yet** (follow-up). Until it is, this step is the owner driving the
admin API. It is not a household flow.
1. On the Pi, set `PANEL_BT_BOND_ENABLED=1` in `~/.zoe-bluetooth/.env.bluetooth` for the session, then restart
   `zoe-bluetooth`. On the Jetson, `ZOE_BLUETOOTH_SETTINGS=1`.
2. Find the panel and the pendant's device id with an admin session (the `X-Session-ID` of a signed-in admin):
   `GET /api/bluetooth/panels`, then `GET /api/bluetooth/panels/<panel>`.
3. **Tap the pendant twice, then twice again** (four quick taps). A short buzz and the LED cycling blue > green > purple >
   yellow mean its pairing window is open for 60 s.
   There is no automatic window, not even for a freshly flashed pendant.
4. Within the minute:
   `POST /api/bluetooth/panels/<panel>/devices/<id>/omi_bond` with body
   `{"confirm": true, "ack_wipe": true}`. Add `"replace": true` if this Pi paired it before, and `"force": true` only to
   pair despite unsynced recordings.
   - Poll `GET /api/bluetooth/panels/<panel>/jobs/<job>`.
   - White flashes are the wipe. A long buzz and three green blinks from the pendant mean it bonded (and wiped). The job
     answers `bonded: true, encrypted: true`.
   - "Did not accept pairing" means the window was shut: tap again.
   - "Waiting to sync" means let omi-sync finish.
5. On the Pi, in `~/.zoe-omi-sync/.env.omi`, set `OMI_SYNC_REQUIRE_BOND=all`. Restart `omi-sync`.
6. Run `python -m omi_sync check`. It must report `require_bond: true` and a normal (empty) ring status.
7. Set `PANEL_BT_BOND_ENABLED=0` again, then restart `zoe-bluetooth`.

## F. Prove it (the acceptance test, owner present)
- From a phone running a generic BLE app (nRF Connect), with the Pi's sync stopped (`systemctl --user stop omi-sync`):
  - connect to the pendant and try to read `30295781-…` (storage) or subscribe to `19b10001-…` (audio). It **must** fail
    with an authentication or encryption error and **no pairing prompt may succeed**.
  - The pendant should also drop the phone within about 30 s.
- Do the same for the SMP characteristic `da2e7828-…`.
- Restart `omi-sync`. The next visit must drain normally: a `sync[...] path=ring` line, no `refused:`.
- A record of each check goes in `docs/knowledge/open-problems.md`: remove the 2026-10-17 P0-1 line and the 2026-10-13
  OTA-key line, or narrow them to the hardened bootloader.

## LED meanings (what the owner sees)
| Priority | LED | Means |
|---|---|---|
| 1 | off | the pendant is off (3 s hold) |
| 2 | **red, fast blink** (+ a 2 s buzz) | fault: stock-bond wipe failed (it is not advertising), crypto/flash fail-closed (nothing is being stored), or a pairing refused while the window was open |
| 3 | **blue > green > purple > yellow, cycling** | pairing window open (after the four taps), 60 s |
| 4 | **white quick flashes** | crypto-wipe in progress (a new bond), until the ring is cleared under the new key |
| 5 | **three green blinks** | just paired |
| 6 | **green breathing** / **solid green** | charging / fully charged (>= 98 %) |
| 7 | **red slow blink** | warning: storage >= 90 % full, battery < 15 %, or the clock is not set |
| 8 | **blue blinking** | syncing to the panel |
| 9 | **dim steady blue** (or white, a setting) | normal: recording / idle |

## G. Recovery
| Symptom | Do |
|---|---|
| Sync logs `refused: no bond` | Do E again (sync first; the gesture, then `omi_bond` with `replace`). Re-pairing wipes the pendant |
| Sync logs `refused: ... not encrypted` | Is the pendant really yours (address)? Was it re-flashed? Re-pair with replace |
| Pairing never succeeds, or the pendant never buzzes for the gesture | Power-cycle it (3 s hold, then press) and try the four taps again. There is no automatic window: if the gesture does not work, SWD (D1) |
| No adverts after the FIRST boot of a flash, plus a 2 s buzz | The stock-era bond wipe failed and the pendant refuses to advertise (fail closed). Power-cycle once. If it persists: SWD (D1) |
| No adverts, a reboot loop, or no BLE at all after a flash | SWD: `nrfjprog --recover`, then flash stock 3.0.21 `merged_CPUNET.hex` + `merged.hex` (or this build's) |
| Want stock back | SWD as above. Over BLE only while bonded, and only with the app-only mode, never the Omi phone app |
| A lost or stolen pendant | A thief who pairs it gets an empty, freshly keyed pendant: pairing wipes it. Without pairing they cannot read it. Physical extraction over SWD is out of scope unless APPROTECT is on. Drop its bond on the Pi (owner, at the Pi: `bluetoothctl remove <address>`) |
