# Omi phase-2 firmware: bonded-only Bluetooth and an encrypted ring (lab)

Owner decision OD1, 2026-10-19: *"Make the encrypted firmware the top priority."* It closes gap-audit P0-1
([omi-gap-audit-2026-10.md](../../docs/research/omi-gap-audit-2026-10.md)): today anyone in Bluetooth range can read the
pendant's stored audio whenever the Pi is not connected, and on stock firmware that read frees the owner's copy.

This directory is a patch series and an overlay on the pinned upstream source. No firmware tree is vendored here. Nothing
in it is flashed, and nothing runs against a pendant. Flashing is an owner step: see [RUNBOOK.md](RUNBOOK.md).
Evidence tags: **[V]** read in source or run this session, **[S]** secondary, **[U]** unverified until the device is used.

## 0. Status (2026-10-19)

| Part | State |
|---|---|
| Firmware patch (`patches/zoe-phase2.patch`: Kconfig, CMake and 5 upstream sources) + new sources (`overlay/`) on upstream `Omi_CV1_v3.0.21` (`f12031d1`) | **written; applies cleanly** to a fresh fetch [V] |
| Compile in the pinned toolchain (`build.sh`: Zephyr CI image `v0.26.13`, NCS v2.9.0, sysbuild + MCUboot, on this aarch64 box) | **compiled, all four images, no warnings in our code** (section 5) [V] |
| Host tests of the pure firmware code (`host-test/`: ring-crypt IV layout, CTR against an independent reference, re-key; the pairing gesture) | **8/8** [V] |
| Pi: `omi-sync` bonded-only mode (`OMI_SYNC_REQUIRE_BOND`), panel-bluetooth `omi_bond`, zoe-data broker op | built, unit-tested, flag-dark |
| Panel UI button + spoken guidance for the pairing | **not built** (follow-up; the op and the API exist) |
| App-only OTA "custom pin" mode in panel-bluetooth (needed for the OTA flash path) | **not built** (follow-up) |
| Anything on the real pendant | **nothing** |

## 1. Facts

**Hardware.** Omi CV1 (DIS model `Omi CV 1`, hardware rev `5.0`) [V, plan 0.3a]: a Nordic **nRF5340**, with an application
core and a network (radio) core. It also has a T5838 microphone, a haptic motor, one button and a **soldered SD NAND**
(`sd_card.c`, LittleFS comment in `omi.conf`). The audio ring is a raw region of 480 MiB, about 34 h at the codec ceiling [V].

**Firmware.** Stock **3.0.21** since 2026-10-13: the panel updated it over BLE from 3.0.18, confirmed by DIS afterwards
(the operator's DFU record) [V]. `omi-sync` gates on DIS `2A26`: 3.0.18 uses the legacy file protocol, 3.0.20 and later
use the ring protocol (`ring_protocol.supports_ring`) [V].

**Bluetooth today** (`omi.conf` at the tag) [V]:
- `CONFIG_BT_MAX_CONN=1` and `CONFIG_BT_MAX_PAIRED=1`.
- `CONFIG_BT_SMP=y`, but with no pairing callbacks.
- Every Omi characteristic is plain `BT_GATT_PERM_READ`/`_WRITE`: audio, storage, settings, features, time, button and
  haptic (`transport.c:144-287`, `storage.c:80-94`, `button.c:47-51`, `haptic.c:45`).
- The MCUmgr SMP (OTA) service uses NCS's default `MCUMGR_TRANSPORT_BT_PERM_RW`, so it is open too
  (`samples/common/mcumgr_bt_ota_dfu/Kconfig`).

**How firmware gets onto it** [V unless marked]:
- **Over the air, over BLE.** MCUmgr SMP through `services/panel-bluetooth`. This path is proven: 3.0.18 to 3.0.21 on
  2026-10-13, both images, about 16 s.
  - MCUboot trusts the PUBLIC default RSA-2048 key, which Omi committed with its private half. Any image signed with it is
    accepted ([press-to-record research](../../docs/research/omi-press-to-record-firmware-2026-10.md) section 1).
  - The bootloader is **overwrite-only**: no test slot, no revert, no serial or USB recovery (section 5.1).
  - The net-core image is signed with a per-build random NSIB key. We cannot produce one this unit accepts, so an OTA of
    our build is **app core only (image 0)**. Our changes are all host-side and app-core, so the net core does not need to
    change.
- **SWD** with a J-Link or an nRF5340 DK used as a probe. It needs Omi's "special flashing cable" or access to the pads
  [S: upstream `Flash_device.mdx`; physical access is U]. APPROTECT is not enabled in the build, so a probe should attach
  [U].

**Recovery.** A bad image that still boots with working BLE can be fixed with another OTA. One that breaks BLE or SMP, or
reboot-loops, can **only** be recovered over SWD: `nrfjprog --recover`, then flash both cores' hex files from one build.
That is why the owner needs a probe before the first flash. Phase-2 firmware adds one way to get locked out: if the
**bond is lost and the gesture is broken**, SMP refuses the Pi as well, so SWD is the only way back. Two things soften
this:
- The first-setup window: while the pendant holds no bond at all, pairing is open for 120 s after every boot.
- The host-tested gesture.

**The Pi** (read-only reads, 2026-10-19) [V]: panel Pi 5, kernel 6.12, BlueZ 5.66.
- The adapter supports LE Secure Connections (`btmgmt info`: `secure-conn`).
- It is **not bondable at rest**: `Pairable` false, `AlwaysPairable` and `JustWorksRepairing` unset, so the defaults apply.
  This is why the stock pendant's Security Request is answered "not supported".

## 2. Design

### 2.1 Bonded-only Bluetooth (`overlay/omi/src/zoe/zoe_ble_sec.*`; the `transport.c`/`storage.c`/`button.c`/`haptic.c` hunks of `patches/zoe-phase2.patch`)
- **Every Omi characteristic and CCC requires an encrypted link** (`BT_GATT_PERM_*_ENCRYPT` through one `ZOE_PERM_*`
  macro). That covers audio, storage, settings, features, time, button and haptic. **The SMP (OTA) service requires one
  too** (`CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT`). A stranger can no longer read audio, send CLEAR, change the mic
  gain, set the clock or push an image signed with the public key. DIS and Battery stay open, as on stock.
- **LE Secure Connections only** (`CONFIG_BT_SMP_SC_PAIR_ONLY`). **Pairing is refused unless the window is open**
  (`CONFIG_BT_SMP_APP_PAIRING_ACCEPT`, callback `pairing_accept`). The window opens in two cases:
  - the **gesture**: two double-taps within 2.5 s (four quick taps). It opens for 60 s with a short buzz; bonding gives a
    long buzz.
  - **first setup**: while the pendant holds no bond at all, the window is open for 120 s after boot.

  A pairing request that does not ask to bond is refused, and a session-only pairing is disconnected: it is bond or nothing. One bond (`MAX_PAIRED=1`). A new pairing in the window replaces the old one (`CONFIG_BT_KEYS_OVERWRITE_OLDEST`).
- **The first boot of this firmware deletes every stock-era bond**, for example a phone the Omi app once paired. A
  settings marker makes this happen once.
- **When a bond exists, the pendant asks every central to encrypt** (a Security Request). The bonded Pi encrypts with its
  stored key; anyone else is refused pairing. A connection that has not encrypted within 30 s, outside the window, is
  dropped. So a stranger cannot sit on the single connection and lock the Pi out (`CONFIG_ZOE_UNENCRYPTED_KICK_S`).
- Untouched: the 3 s power-off, the single tap (reserved for press-to-record), partitions, sysbuild, MCUboot, the net core,
  and every byte of the ring protocol on the wire. DIS reports `3.0.21.1`; the 4th component is the Zoe build number.
  `omi-sync` reads the first three, so it still takes the ring path.

### 2.2 Audio encrypted at rest (`overlay/omi/src/zoe/zoe_ring_crypt*`; the `sd_card.c` hunks): feasible, built
- The nRF5340 app core has Arm CryptoCell-312, which NCS exposes through PSA Crypto (`nrf_security`) [V: the Kconfig
  symbols exist in the fetched NCS 2.9.0]. The cost is small:
  - Audio is written at no more than about 4 kB/s. Each 444-byte packet is encrypted once, when it is appended.
  - A drain decrypts at BLE speed.

  Measuring on the device is part of the runbook [U].
- **AES-128-CTR**, with a 16-byte device key generated on the pendant at first use and kept in the internal-flash settings
  store. It is never sent anywhere.
  - IV = `salt(4) || seq(8) || block(4)`. The salt is a fresh random value per batch, stored in the batch header's
    `reserved0`. `seq` is the ring sequence.
  - A batch re-opened after a reboot is **re-keyed** to a new salt. So one (salt, seq) pair never covers two plaintexts,
    not after a CLEAR, a truncated tail or a torn write either. `host-test/` proves the hazard and the fix.
- Fail closed:
  - no key, no mount: nothing is written in plaintext.
  - a packet that fails to encrypt is dropped.
  - a NEW key (settings erased) clears the unreadable ring instead of serving garbage to the Pi.
- **Ring layout version `0x5A01`.** Stock firmware treats an encrypted ring as invalid, and the reverse, and re-initialises
  it, so neither ever reads the other as audio.
  - **Drain the pendant before flashing.** Undrained audio is dropped at the first boot.
  - The old plaintext stays physically on the NAND until the ring overwrites it, about 34 h of recording.
- **What it protects:** a desoldered NAND or a raw dump of the SD region. **What it does not:** an attacker with SWD reads
  the internal flash, including this key and the bond, unless APPROTECT is enabled. APPROTECT is an owner decision (section
  3). It survives `nrfjprog --recover` as a recovery path, but recovery then erases everything. The Pi still receives
  plaintext, over the encrypted link: the wire protocol is unchanged.
- Not used: the KMU. Without APPROTECT it adds nothing against SWD, and it makes a re-flash lose the key for no gain.

### 2.3 The Pi
- **`services/omi-sync`**, `OMI_SYNC_REQUIRE_BOND` (off; `all` or a list of addresses). It is read-only and never pairs:
  - **before connecting**, BlueZ must read `Paired` **and** `Bonded` true for the pendant. If not, no connection is
    attempted.
  - **after subscribing**, the kernel link must show `ENCRYPT`. On phase-2 firmware the subscribe itself needs
    encryption, which BlueZ starts with the stored key.
  - **then** the bond must still be there.

  Anything else tears the link down before one storage command, and is logged as a refusal (`NotBonded` /
  `InsecureLink`). It never falls back to an open link.
- **`services/panel-bluetooth`**, op **`omi_bond`** (`PANEL_BT_BOND_ENABLED`, off, plus a `confirm`). This is the one place
  a bond is made (`panel_bt/bond.py`):
  - it refuses a pendant that does not report Zoe firmware.
  - it makes the adapter pairable only for this pairing: `PairableTimeout` 60, then `Pairable` true, then false again.
  - it pairs with no agent: LE SC "Just Works" from the initiator.
  - it counts success only when BlueZ reads `Paired` and `Bonded` true **and** the link is encrypted.
  - `replace` removes this pendant's old BlueZ entry and nothing else.
- **`services/zoe-data`**: the broker accepts `omi_bond` (admin only, audited, `confirm` required; it forwards only
  `confirm` and `replace`).

### 2.4 Who holds which key
- **The ring key**: generated on the pendant, never leaves it, and lives in no repository.
- **The bond (LTK)**: created by the pairing, held by BlueZ on the Pi (`/var/lib/bluetooth`, root) and in the pendant's
  settings.
- **The OTA signing key**: for the stock bootloader it is the public dev key. There is nothing secret to protect, and
  build.sh's default uses the copy in the fetched upstream tree. For the hardened path (an own MCUboot key, flashed over
  SWD) the owner generates a key outside the repo (RUNBOOK, key ceremony). `build.sh` refuses a key path inside the repo.

## 3. Threat model and what stays open
- **Closed by this design:**
  - reading or erasing stored audio, subscribing to the live stream, changing settings or the clock, and pushing an OTA
    image, from any central that is not the bonded Pi.
  - a look-alike pendant feeding the Pi audio.
  - a stock-era phone bond.
- **Just Works pairing has no MITM protection.** An attacker who is actively present during the owner's 60 s window could
  bond instead of the Pi. The Pi would then refuse the pendant and the owner would notice and re-pair, but the attacker
  would hold the bond. Mitigations: the physical gesture and the short window. Upgrade path (owner decision): a passkey
  baked per build, or an out-of-band key.
- **Physical possession plus SWD** reads the internal flash (ring key, LTK) unless APPROTECT is on. Turning APPROTECT on is
  an owner decision: recovery still works (`--recover`), but it erases the pendant.
- **The public MCUboot key** still lets anyone with physical SWD access, or with a bond, flash an image. Replacing the
  bootloader key needs SWD (RUNBOOK, hardened path).
- **Denial of service:** a stranger can hold the one connection for up to 30 s per attempt, again and again. Audio is not
  lost: it keeps recording to the ring.
- **BlueZ on a "key missing" reply** from a look-alike may delete the Pi's bond [U]. The result is fail-closed: the sync
  refuses and the owner re-pairs.
- **Never let the Omi phone app update this pendant.** Its "update" would silently restore stock (press-to-record
  research, section 8.6).

## 4. Build and test (hand-run; never CI, per labs/AGENTS.md)
```
bash labs/omi-firmware/fetch.sh            # pinned sparse fetch + overlay + patches (~19 MB, ~/.cache/zoe-omi-fw/src)
bash labs/omi-firmware/build.sh sdk        # once: the NCS v2.9.0 workspace into docker volume zoe-omi-ncs-v2.9.0 (~2.3 GB)
bash labs/omi-firmware/build.sh            # compile (refuses at MemAvailable < 2000 MB or while night_busy says pause)
nice -n 10 python3 -m pytest labs/omi-firmware/host-test -q -p no:cacheprovider
```
Outputs land in `~/.cache/zoe-omi-fw/out/` with `SHA256SUMS`: `dfu_application.zip`, `omi.signed.bin` (image 0 only is
for OTA), `merged.hex`, `merged_CPUNET.hex` (for SWD) and `app.config`. The container is capped at 2 GB with no swap, runs
2 ninja jobs, holds the test-lane lock and runs as the host user.

To change the series: edit the fetched tree under `~/.cache/zoe-omi-fw/src`, regenerate the matching patch from there
with a plain diff, and keep `overlay/` as the source of the new files.

## 5. Compile result (2026-10-19 03:46, this box, aarch64, `build.sh` defaults, upstream public dev key)
- **All four images built**: MCUboot, the app (ours), the net core `ipc_radio` and `b0n`. Our sources compiled with
  **no warnings**. The warnings in the log are upstream's own (`battery.c`, `transport.c`, `imu.c`). No Kconfig warning
  touches a symbol this patch sets.
- `app.config` shows every symbol as set:
  - `CONFIG_ZOE_BLE_BONDED_ONLY=y` and `CONFIG_ZOE_RING_ENCRYPT=y`
  - `CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT=y`
  - `CONFIG_BT_SMP_SC_PAIR_ONLY=y` and `CONFIG_BT_SMP_APP_PAIRING_ACCEPT=y`
  - `CONFIG_BT_SETTINGS=y`, `CONFIG_BT_KEYS_OVERWRITE_OLDEST=y` and `CONFIG_BT_MAX_PAIRED=1`
  - `CONFIG_NRF_SECURITY=y` and `CONFIG_PSA_WANT_ALG_CTR=y`
  - `CONFIG_BT_DIS_FW_REV_STR="3.0.21.1"`
- **App image** `omi.signed.bin`: 259 652 bytes (stock 3.0.21: 248 644), with a 258 804-byte payload.
  - The MCUboot primary slot is 0x10000-0x100000 (960 KB). App flash use is 27.5 %; app RAM is 75.3 %.
  - TLVs checked: magic `0x96f3b83d`, the image hash verifies, and the key-hash TLV is
    `fc5701dc6135e1323847bdc40f04d2e5bee5833b23c29f93593d00018cfa9994`. That is the public dev key the stock bootloader
    trusts, so this is an OTA-acceptable image.
- Output SHA-256 sums (the first 8 hex digits; the full sums are in `~/.cache/zoe-omi-fw/out/SHA256SUMS`):
  `omi.signed.bin` `10e36631`, `dfu_application.zip` `dfffe230`, `merged.hex` `3b2e70f8`, `merged_CPUNET.hex` `5ac6d59c`.
  - Builds are not byte-reproducible: timestamps differ between runs.
  - `dfu_application.zip` also carries a net-core image from this build. **Never push that net-core image over the air**
    (README section 1).
- About 10 minutes cold on this box, with 2 ninja jobs in a 1.5 GB container.

## 6. Unverified until the device is used
- That Just Works pairing from BlueZ 5.66 completes with no agent once the adapter is made pairable.
- That `hcitool con` shows `ENCRYPT` on the bonded link.
- That the 30 s kick leaves the Pi's lecc-plus-bleak connect (slow at range) enough time.
- The battery cost of the ring encryption, and drain throughput.
- That an app-only OTA leaves the stock net core working.
- SWD access to the retail unit.
