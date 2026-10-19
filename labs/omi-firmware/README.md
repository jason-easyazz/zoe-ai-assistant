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
| Host tests (`host-test/`): the pure ring-crypt code (IV layout, CTR against an independent reference, re-key, the persisted monotonic salt allocator) and the REAL `zoe_ble_sec.c` against stubbed Zephyr (gesture-only pairing, fail-closed stock wipe, crypto-wipe on new bond with a power cut before each modelled persistent step, unreadable records, a failed ring clear, reconnect never wipes; scope in section 2.1) | **29/29** (incl. the LED state/priority/pattern tests); 28 firmware source mutations all red [V] |
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
That is why the owner needs a probe before the first flash. Phase-2 firmware adds ways to get locked out, each recoverable
only over SWD:
- the **bond is lost and the gesture is broken**: SMP refuses the Pi as well. There is no automatic pairing window, not
  even for first setup (a deliberate choice: a stolen or reset pendant must not be pairable without its button), so the
  host-tested gesture is the only way to pair.
- the **stock-era bond wipe fails at first boot**: the pendant then refuses to advertise at all (fail closed, a 2 s buzz)
  rather than run with a phone bond that could still read it.

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
  - the **gesture** only: two double-taps within 2.5 s (four quick taps). It opens for 60 s with a short buzz; bonding gives
    a long buzz. **There is no automatic window, not even for first setup.**

  A pairing request that does not ask to bond is refused, and a session-only pairing is disconnected: it is bond or nothing. One bond (`MAX_PAIRED=1`). A new pairing in the window replaces the old one (`CONFIG_BT_KEYS_OVERWRITE_OLDEST`).
- **The first boot of this firmware deletes every stock-era bond**, for example a phone the Omi app once paired. A
  settings marker makes this happen once. If the deletion fails, the pendant does not advertise.
- **Crypto-wipe on every new bond** (owner decision 2026-10-19: *"the pendant can only pair with one device; if it's stolen
  and someone tries to pair it, wipe it as part of the process"*). When a pairing that would create or replace the bond is
  accepted, and before it may complete, `zoe_ble_sec.c` runs these steps in order:
  1. persist two intents ("the audio key must go", "the other bonds must go");
  2. revoke the stored-audio key: unusable in RAM at once, erased from flash;
  3. delete every other bond;
  4. clear the bond intent.

  The SD worker then destroys the key in PSA, generates a fresh one from the CSPRNG (no CSPRNG means nothing more is ever
  stored), empties the ring under it, and only then clears the key intent. A boot with an intent still set finishes the
  wipe before advertising or loading the old key. So after a power cut at any step the pendant should never hold the old
  key alongside a new bond.
- Every read of the flags, the key and the salt reservation tells "absent" apart from "unreadable", and an unreadable
  record fails closed:
  - the BLE init does not advertise;
  - the key is neither loaded nor replaced, and the ring stays blocked.
- If the ring cannot be emptied under a new key, the ring is not mounted, the key is dropped, and the intent stays set,
  so the next mount repeats the whole wipe.
- When the salt space is exhausted, no new key is made.
- What the harness proves, and what it does not:
  - **It covers** the real `zoe_ble_sec.c` and the shared pure decisions (`zrc_key_decision`, `zrc_new_key_epoch`,
    `zrc_finish_new_key`, the salt allocator). It cuts power before each persistent operation, at the granularity it
    models: each settings write or delete, each `bt_unpair`, the ring clear and the stored bond. It also covers unreadable
    records and a failed ring clear.
  - **It does not model:**
    - a torn write inside one settings record;
    - the Zephyr bond store's internal steps;
    - SD sector behaviour;
    - the real `sd_card.c` / `zoe_ring_crypt_psa.c`. The harness re-states their order through the same pure functions.
      Those files are compiled (section 5) and reviewed, not executed.
  - A reconnect with the existing bond never wipes, because no pairing happens.
  - **A thief who pairs gets an empty, freshly keyed pendant.** So does the owner on every re-pair: **sync before
    re-pairing.**
  - Physical extraction (SWD without APPROTECT) stays out of scope.
- **When a bond exists, the pendant asks every central to encrypt** (a Security Request). The bonded Pi encrypts with its
  stored key; anyone else is refused pairing. A connection that has not encrypted within 30 s, outside the window, is
  dropped. So a stranger cannot sit on the single connection and lock the Pi out (`CONFIG_ZOE_UNENCRYPTED_KICK_S`).
- Untouched: the 3 s power-off, the single tap (reserved for press-to-record), partitions, sysbuild, MCUboot, the net core,
  and every byte of the ring protocol on the wire. DIS reports `3.0.21.1`; the 4th component is the Zoe build number.
  `omi-sync` reads the first three, so it still takes the ring path.

### 2.1b The status LED (`overlay/omi/src/zoe/zoe_led*`; owner design 2026-10-19)
`zoe_led_logic.h` is pure and host-tested. It maps inputs to a state by priority, then the state to a colour and pattern.
`zoe_led.c` drives the three PWM channels on a work item. Steady states refresh once a second; animations refresh at
50-100 ms. Stock's `set_led_state()` is turned off.

| Priority | LED | Means |
|---|---|---|
| 1 | off | the pendant is off (3 s hold) |
| 2 | **red, fast blink** (+ a 2 s buzz) | fault: stock-bond wipe failed (it is not advertising), crypto/flash fail-closed (nothing is being stored), or a pairing refused while the window was open |
| 3 | **blue > green > purple > yellow, cycling** | pairing window open (after the four taps), 60 s |
| 4 | **white quick flashes** | crypto-wipe in progress (a new bond) |
| 5 | **three green blinks** | just paired |
| 6 | **green breathing** / **solid green** | charging / fully charged (>= 98 %) |
| 7 | **red slow blink** | warning: storage >= 90 % full, battery < 15 %, or the clock is not set |
| 8 | **blue blinking** | syncing to the panel |
| 9 | **dim steady blue** (or white, a setting) | normal: recording / idle |

- **Normal is dim on purpose.** 15 % PWM duty draws about 15 % of the channel's full-on current, against stock's 50 %
  default dim ratio. That is about 70 % less LED current in the state the pendant spends almost all its time in. This is
  PWM arithmetic, not a measurement.
- **The normal colour is a persisted setting:** `zoe/lc`, 0 = blue (default), 1 = white.
  - The bonded Pi can read and write it over an **encrypted-only** characteristic: service
    `3f6d1a40-8c2e-4b7a-9d15-6e2c0b8f7a10`, characteristic `...7a11`, one byte.
  - The Pi-side control (a Settings switch) is a follow-up.
- **Choices the owner did not specify:**
  - "Paired" ranks below the wipe and above charging.
  - A pairing refused because the window was shut (a stranger) does NOT light the fault. Only a refusal inside an open
    window does, so strangers cannot make the pendant flash.

### 2.2 Audio encrypted at rest (`overlay/omi/src/zoe/zoe_ring_crypt*`; the `sd_card.c` hunks): feasible, built
- The nRF5340 app core has Arm CryptoCell-312, which NCS exposes through PSA Crypto (`nrf_security`) [V: the Kconfig
  symbols exist in the fetched NCS 2.9.0]. The cost is small:
  - Audio is written at no more than about 4 kB/s. Each 444-byte packet is encrypted once, when it is appended.
  - A drain decrypts at BLE speed.

  Measuring on the device is part of the runbook [U].
- **AES-128-CTR**, with a 16-byte device key generated on the pendant at first use and kept in the internal-flash settings
  store. It is never sent anywhere.
  - IV = `salt(4) || seq(8) || block(4)`. The salt is **not random**: it is a per-key **monotonic epoch counter**, one
    value per batch, stored in the batch header's `reserved0`. Values are reserved in blocks of 1024, and each block's
    upper bound is persisted **before** any value in it is used. So after any crash the next salt is above every salt ever
    handed out, and the counter only moves up, across keys too. `seq` is the ring sequence.
  - If the reservation cannot be persisted, no salt is issued and ring writes stop. There is no weak fallback: the salt
    used to fall back to `k_cycle_get_32()`, which could repeat.
  - Losing the reservation record forces a new key.
  - A batch re-opened after a reboot is **re-keyed** to a new salt. So, under one key, a (salt, seq) pair covers exactly
    one plaintext. This is guaranteed by construction, not probable. It holds
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
- **`services/panel-bluetooth`**, op **`omi_bond`** (`PANEL_BT_BOND_ENABLED`, off, plus `confirm` and `ack_wipe`: the
  owner acknowledges that the pendant erases its stored audio when it pairs). It is refused while omi-sync still holds
  unsynced records, unless `force` is given. This is the one place
  a bond is made (`panel_bt/bond.py`):
  - it refuses a pendant that does not report Zoe firmware.
  - it makes the adapter pairable only for this pairing: `PairableTimeout` 60 is written and **read back**; if that
    fails, the adapter is never made pairable. Then `Pairable` true, then false again.
  - it pairs with no agent: LE SC "Just Works" from the initiator.
  - it counts success only when BlueZ reads `Paired` and `Bonded` true **and** the link is encrypted.
  - `replace` removes this pendant's old BlueZ entry and nothing else.
- **`services/zoe-data`**: the broker accepts `omi_bond` (admin only, audited, `confirm` and `ack_wipe` required; it
  forwards only `confirm`, `ack_wipe`, `replace` and `force`).

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

## 5. Compile result (latest: 2026-10-19 10:08, this box, aarch64, `build.sh` defaults, upstream public dev key)
- **All four images built**: MCUboot, the app (ours), the net core `ipc_radio` and `b0n`. Our sources
  (`zoe_ble_sec.c`, `zoe_ring_crypt*.c` and the `sd_card.c` / `transport.c` hunks) compiled with **no warnings**. The
  warnings in the log are upstream's own (`battery.c`, `transport.c`, `imu.c`). No Kconfig warning touches a symbol this
  patch sets.
- This rebuild includes:
  - the crypto-wipe on new bond and the persisted salt allocator;
  - the gesture-only pairing and the fail-closed stock wipe;
  - fail-closed record reads and the blocked ring on a failed clear;
  - the status LED (`zoe_led.c`).

  Earlier builds (03:46, 09:09, 09:48) compiled the earlier revisions.
- `app.config` shows every symbol as set:
  - `CONFIG_ZOE_BLE_BONDED_ONLY=y`, `CONFIG_ZOE_RING_ENCRYPT=y` and `CONFIG_ZOE_LED=y` (no `ZOE_FIRST_SETUP_WINDOW_S`:
    that window is gone)
  - `CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT=y`
  - `CONFIG_BT_SMP_SC_PAIR_ONLY=y`, `CONFIG_BT_SMP_APP_PAIRING_ACCEPT=y` and `CONFIG_BT_SMP_ALLOW_UNAUTH_OVERWRITE=y`
  - `CONFIG_BT_SETTINGS=y`, `CONFIG_BT_KEYS_OVERWRITE_OLDEST=y` and `CONFIG_BT_MAX_PAIRED=1`
  - `CONFIG_NRF_SECURITY=y` and `CONFIG_PSA_WANT_ALG_CTR=y`
  - `CONFIG_BT_DIS_FW_REV_STR="3.0.21.1"`
- **App image** `omi.signed.bin`: 262 040 bytes (stock 3.0.21: 248 644), with a 261 192-byte payload.
  - App flash use is 27.7 % of the 960 KB MCUboot primary slot.
  - TLVs (checked on the 03:46 build, same signing path): magic `0x96f3b83d`, the image hash verifies, and the key-hash
    TLV is `fc5701dc…`. That is the public dev key the stock bootloader trusts, so this is an OTA-acceptable image.
- Output SHA-256 sums, first 8 hex digits (the full sums are in `~/.cache/zoe-omi-fw/out/SHA256SUMS`): `omi.signed.bin`
  `70237a33`, `dfu_application.zip` `da4dd52e`, `merged.hex` `1819353f`, `merged_CPUNET.hex` `dd2f0b7c`.
  - Builds are not byte-reproducible: timestamps differ between runs.
  - `dfu_application.zip` also carries a net-core image from this build. **Never push that net-core image over the air**
    (README section 1).
- About 10 minutes cold on this box, with 2 ninja jobs in a 1.5 GB container. The `MemAvailable >= 2000 MB` gate
  refused it for hours: daytime and night memory on the Jetson sits around 1.5-2.0 GB.

## 6. Unverified until the device is used
- That Just Works pairing from BlueZ 5.66 completes with no agent once the adapter is made pairable.
- That `hcitool con` shows `ENCRYPT` on the bonded link.
- That the 30 s kick leaves the Pi's lecc-plus-bleak connect (slow at range) enough time.
- The battery cost of the ring encryption, and drain throughput.
- That an app-only OTA leaves the stock net core working.
- SWD access to the retail unit.
