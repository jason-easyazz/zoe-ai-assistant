---
type: research
title: Omi CV1 press-to-record firmware - can we ship it, how, and what it risks (2026-10-13)
date: 2026-10-13
status: research-only; nothing built, flashed or connected to. The pendant was never touched. Recommendation in section 0; owner decisions in section 8.
description: Evidence-backed answer to "when you press the button it records" on the owner's Omi CV1 (nRF5340, stock 3.0.21). Establishes that the bootloader trusts MCUboot's public default RSA-2048 dev key (the private half is committed in the Omi repo), so a custom OTA image is signable by anyone; maps the button, mic, codec, storage and LED code with file:line at the v3.0.21 tag; sizes the smallest firmware change; checks the nRF Connect SDK build on this Jetson and via GitHub Actions; lays out the no-revert recovery story (SWD) and the security consequence of a public key; and ranks the alternatives that need no custom firmware.
tags: [omi, firmware, mcuboot, ota, signing, press-to-record, consent, nrf5340, research]
---

# Omi CV1 press-to-record firmware - can we ship it, how, and what it risks

Research date: 2026-10-13. Owner request, verbatim: *"i need new firmware, when you press the button it records"*.
Context: the pendant is an Omi CV1 (nRF5340) now on stock **3.0.21**; stock firmware records whenever it is powered (the
T5838 mic sleeps after ~10 s of quiet) and a 3 s hold powers it off; `services/omi-sync` drains the SD ring over BLE.
This is **research only**: no build, no flash, no connection to the pendant. The source read is the upstream tag
**`Omi_CV1_v3.0.21`** of `BasedHardware/omi` (via `opensrc`); every `file:line` below is relative to
`omi/firmware/omi/` at that tag unless it starts with `omi/firmware/` or `docs/`.

Evidence tags: **[verified]** I read the source or ran the check this session; **[secondary]** a vendor doc, a PR body or a
search result; **[unverified]** inferred, or could not be measured without the device. Nothing here is household data.

## 0. Answer first

- **OTA custom firmware is possible, and signing is not a barrier.** The bootloader trusts MCUboot's *public default*
  development key `root-rsa-2048.pem`, and Omi committed that file (with its private half) in their repo. The shipped
  3.0.21 images carry that key's hash and verify under it [verified, section 1]. Anyone can sign an image the pendant's
  MCUboot accepts.
- **The change is small.** Press-to-record is a runtime gate in front of the codec (about 40-80 lines across `button.c`,
  `main.c` and one new tiny module), a haptic buzz and an LED state. Storage, the ring protocol, the 3 s power-off and the SMP
  (OTA) server are left byte-for-byte alone, so `omi-sync` keeps working [verified code map, section 2].
- **No official release offers it.** No release up to 3.0.21 and nothing on upstream `main` has a record toggle, a "record
  only on button" mode or a GATT setting for it; two open upstream PRs remap what taps do in the *phone app* only
  (section 3). The GATT "mic gain level 0 = mute" is **not** a mute: it writes `0x00` to the PDM gain register, which is
  **-20 dB**, not silence [verified, section 6].
- **Cheapest path:** patch the app core only, build it on a GitHub-hosted x86 runner by mirroring upstream's own CV1 workflow
  in a small fork (section 4), push **only image 0** (the app) through the existing `panel-bluetooth` updater after a reviewed
  "custom pin" edit. The net-core image is not touched (its secure-boot key is a per-build random key we cannot match, and we
  do not need to).
- **The real risk is recovery, not signing.** The bootloader is overwrite-only: there is no revert and no serial/USB recovery
  mode in the build (section 5). A signed image that boots but breaks BLE/SMP can only be fixed over **SWD**. The mainboard
  schematic shows a 2x3 debug header plus test pads; the retail case access and the pad geometry are **[unverified]**.
  **Do not flash until the owner has a J-Link (or an nRF5340 DK used as a probe) and has confirmed they can reach the pads.**
- **A security finding to read before anything else (section 5.4):** because the signing key is public and the SMP service
  needs no pairing (plan 0.3a), *anyone in BLE range can push a validly-signed image to this pendant* - including to a stock
  one. Press-to-record does not fix that; it is a separate hardening decision (our own bootloader key via SWD).
- **Without custom firmware** the best honest option is a Pi-side policy ("keep only what was marked"), but the button is
  *not* reliable for that: presses with no central connected are lost, never stored (section 6). It does not meet "recording
  happens only after a press".

## 1. Signing - which key the CV1 accepts [verified]

### 1.1 Config

| Fact | Evidence |
|---|---|
| App-core MCUboot is signed with `${APP_DIR}/../bootloader/mcuboot/root-rsa-2048.pem` | `omi/firmware/omi/sysbuild.conf:8` (`SB_CONFIG_BOOT_SIGNATURE_KEY_FILE`) |
| That file is **in the repo**, with its private half (`-----BEGIN RSA PRIVATE KEY-----`, 1675 bytes) | `omi/firmware/bootloader/mcuboot/root-rsa-2048.pem` |
| It is **byte-identical** to MCUboot's own dev key | `diff` against `mcu-tools/mcuboot` `root-rsa-2048.pem`: identical; both sha256 `1fc912d30251b821f251e127d4daf7ba9338dd5c04e5af100abfb5b7c7d4c022`. MCUboot's Kconfig defaults to this file for RSA-2048 (`boot/zephyr/Kconfig:471`) |
| Upstream says so itself | `omi/firmware/scripts/ci/README.md:67`: "MCUboot signing uses the committed key" |
| Mode: overwrite-only, downgrade prevention, two updatable images, net core secure boot | `sysbuild.conf:12-17` (`SB_CONFIG_SECURE_BOOT_NETCORE=y`, `MCUBOOT_UPDATEABLE_IMAGES=2`, `MCUBOOT_MODE_OVERWRITE_ONLY=y`); `omi/sysbuild/mcuboot.conf:5-6` |
| Signature type: RSA-2048, PSS (the TLV is 256 bytes) | from the image TLVs below |

### 1.2 The shipped image (`Omi_CV1_OTA_v3.0.21.zip`, sha256 `9fb93885...d3674`, matches the plan's pin)

I parsed the MCUboot TLVs of both images (`omi.signed.bin` = image 0 app core, `ipc_radio.bin` = image 1 net-core radio):

| Check | `omi.signed.bin` | `ipc_radio.bin` |
|---|---|---|
| Magic `0x96f3b83d`, header 512 B, version `0.0.0.0` | yes | yes |
| Key-hash TLV `0x01` | `fc5701dc6135e1323847bdc40f04d2e5bee5833b23c29f93593d00018cfa9994` | same |
| `sha256(PKCS#1 DER public key of root-rsa-2048.pem)` | equal to the TLV | equal |
| Image-hash TLV `0x10` equals sha256(header+image) | yes | yes |
| RSA-PSS (MGF1-SHA256, salt 32) signature TLV `0x20` verifies under the **repo key's public half** | yes | yes |
| Negative control: flip one signature byte | rejected | rejected |
| A fresh signature over a modified image, made with the committed private key, verifies | yes | yes |

The same key hash `fc5701dc...` is in the 3.0.18, 3.0.20 and 3.0.21 OTA zips: the key has not changed across releases.
**The device actually trusts it:** the factory full-flash image `Omi_CV1_v3.0.21.hex` contains the MCUboot bootloader, and that
bootloader embeds the exact PKCS#1 public key at flash offset `0xc660` (modulus at `0xc669`; searched in the hex). MCUboot is
immutable over OTA, and the owner's pendant already boots images signed by this key (3.0.18 to 3.0.21 today), so acceptance on
the device follows. [Acceptance on *this unit* is inferred from its successful update, not measured.]

### 1.3 Net core (b0n / NSIB) - a different key, and we do not need it

`SB_CONFIG_SECURE_BOOT_NETCORE=y` (`sysbuild.conf:13`) puts NSIB's "b0n" on the radio core. The inner net-core image is wrapped
in an NSIB validation block (magic `0x281ee6de` at `0x400` and `0x2a9f4` of `ipc_radio.bin`) signed with an NSIB key. Omi sets
no `SB_CONFIG_SECURE_BOOT_SIGNING_KEY_FILE`, so the NCS build **generates a throw-away key per build**
(`nrf/cmake/sysbuild/debug_keys.cmake:20-37`, "Using generated NSIB public/private key-pair"). Measured: the embedded NSIB
public-key hash differs in every release (3.0.18 `cc62fefc...`, 3.0.20 `c4bd988a...`, 3.0.21 `58c4c25e...`), and the
provisioning block in each `*_CPUNET.hex` carries that build's own hash (3.0.21: `58c4c25e...`). Consequences:

- We **cannot** produce a net-core image that a given pendant's b0n will accept, and **must not try**. The patch changes the
  app core only; the existing net core stays. Uploading only image 0 is a normal MCUboot operation (each image index has its own
  slots); the existing updater currently insists on exactly two pending images and would need a one-image mode [unverified on
  hardware].
- How devices that were factory-provisioned with another build's hash take later net-core OTAs is not explained by the source I
  read [unverified]; it does not matter for an app-only update.

## 2. Button and recording code [verified]

### 2.1 Map (v3.0.21)

| What | Where |
|---|---|
| Button GPIO ISR sets `was_pressed`; a 25 Hz work item (40 ms) debounces and classifies | `src/lib/core/button.c:73-77, 172-258, 273-277` |
| Thresholds: tap < 300 ms, double-tap window 600 ms, long press 3000 ms | `button.c:152-154` |
| Single tap event / double tap event | `button.c:219-231` (notify at `:119-137`) |
| **3 s hold = power off** (`turnoff_all()`: LED off, haptic, `transport_off`, `mic_off`, SD off, system off) | `button.c:214-238, 348-431` |
| Button GATT: service `23ba7924-0000-1000-7450-346eac492e92`, characteristic `23ba7925-...` (read, notify) | `button.c:38-52` (the audio service's `19b10003` is the *speaker* characteristic, not a button) |
| Events actually emitted on the wire: `1` single, `2` double, `5` release after a hold over 300 ms. `notify_press` (`:99`) and `notify_long_tap` (`:139`) are defined and **never called** | `button.c:99-147, 219-251` |
| Notifications are sent **only if a central is connected**; otherwise the press is dropped, nothing is queued or logged | `button.c:103-106, 111-116, 121-126, 131-136, 141-146` (`get_current_connection() != NULL`) |
| The characteristic's read value is just the **last** event code, no timestamp | `button.c:260-269` |
| Mic is started unconditionally at boot, then runs forever; AAD hardware sleep after 10 s under a threshold of 250 (`CONFIG_OMI_VAD_HOLD_MS=10000`) | `src/main.c:341-347`; `src/mic.c:439-447`; `omi.conf:222-224` |
| Mic thread to PCM callback to codec to transport | `src/mic.c:109-134`, `src/main.c:78-91` (`mic_handler` calls `codec_receive_pcm`), `:65-76` (`codec_handler` calls `broadcast_audio_packets`) |
| `mic_pause()` / `mic_resume()` exist (cooperative stop between reads) but **nothing calls them** | `src/mic.c:276-311` |
| Where audio goes: `pusher()` - connected+subscribed streams live; **no connection** writes the SD ring; **connected but not subscribed drops the audio** | `src/lib/core/transport.c:1215-1255` (storage branch `:1237-1248`, drop branch `:1249-1252`) |
| SD ring write path | `transport.c:1138-1175` (`write_to_storage`), `src/sd_card.c:1218-1285` (`write_to_file`) |
| Ring protocol commands (INFO 0x10, READ 0x11, ADVANCE 0x12, CLEAR 0x13, STOP 0x03) | `src/lib/core/storage.c:22-26` |
| Status LED: solid red when disconnected, blue when connected, red blink if the clock is unset | `src/main.c:132-176` (1 Hz loop at `:352-360`) |
| Haptic available (`CONFIG_OMI_ENABLE_HAPTIC=y`), `play_haptic_milli(ms)` | `omi.conf:217`, `src/lib/core/haptic.h:24`, used at boot `main.c:204-214` |
| Settings GATT: service `19b10010`, dim ratio `19b10011`, mic gain `19b10012`, charging status `19b10013`; the feature bitmask has no record-mode bit | `transport.c:174-206`; `src/lib/core/features.h:9-17` |
| SMP/MCUmgr OTA server on | `omi.conf:36-37` (`CONFIG_NCS_SAMPLE_MCUMGR_BT_OTA_DFU`, `..._ALLOW_ERASE_PENDING`) |

### 2.2 Smallest change for "press = start, press again = stop"

All additive; nothing in `storage.c`, `sd_card.c` or the SMP/BLE setup changes.

1. **A gate** - a new `src/lib/core/record_gate.{c,h}`: one `atomic_t recording`, `record_gate_toggle()`,
   `record_gate_is_open()`. **Boots closed** (a reboot never resumes recording - fail-closed for consent). No settings
   persistence in v1.
2. **Gate the frames** - `main.c:78` `mic_handler()`: `if (!record_gate_is_open()) return;` before `codec_receive_pcm`. This
   stops the codec, the live stream and the ring writes together, and it is a 2-line change that is easy to review. (Stronger
   privacy and less battery: also `mic_pause()`/`mic_resume()` around the toggle - **not recommended for v1** because the
   T5838 AAD sleep/wake state machine in `mic.c:63-90` was written assuming the mic runs [unverified interaction]; measure
   battery before adding it.)
3. **The gesture** - `button.c:219-224` single tap: call `record_gate_toggle()`, then immediate feedback. Leave the double tap,
   the release and the 3 s long press untouched. (Single-tap latency is ~0.3 s by the 300 ms rule at `:152, 201-211`; a
   deliberate double-tap also fires a single-tap first on this state machine - another reason to give the double tap no
   action in v1.)
4. **Feedback** - `play_haptic_milli(60)` on start, two short buzzes on stop (`haptic.h:24`), and re-purpose the idle LED:
   `main.c:132-176` shows nothing while idle (saves battery, stops the permanent red) and a slow red/green blink while
   recording, so the pendant doubles as a visible recording light. Keep the "clock not set" red blink and the charging colours.
5. **Optional, recommended**: an auto-stop timer (30 min, an owner choice) so a pocket toggle cannot record all day; and a
   one-line `pusher()` fix so audio produced while the Pi is connected-but-not-subscribed is stored instead of dropped
   (`transport.c:1237` `else if (!conn)` becomes `else if (!conn || !is_subscribed)`) - see 2.3.
6. **Keep stock SMP** unchanged: no change to `omi.conf:28-37`, `sysbuild/*`, `board` files or partitions (`boards/omi/pm_static.yml`
   is static, so a rebuild keeps the same layout).
7. Version string: `omi.conf:112` `CONFIG_BT_DIS_FW_REV_STR`. `omi-sync` parses leading digits per component
   (`ring_protocol.parse_firmware`), so `3.0.21-zoe1` still reads as ring-capable; the panel updater's `version_tuple` rejects
   it (regex `^v?\d+(\.\d+){0,3}$`), so either extend that or pick a parseable number. Decide before the first build.

Size estimate: roughly 60 lines of C plus, if wanted, a pure state machine that can be unit-tested on the host (this tag has
no host test bench for the button logic) [unverified estimate].

### 2.3 Side finding for `omi-sync` [verified in source, not on the device]

`transport.c:1237-1252`: while any central is connected, audio is stored **only if there is no connection** - a central that
is connected but not subscribed to audio notifications (as `omi-sync` is while draining the ring) makes the pusher *drop* audio
for that period. On a press-to-record build this would silently lose a press-started recording made while the Pi is attached.
The one-line fix in 2.2(5) belongs in the same patch. Stock behaviour is unchanged by this note; measure on the device first.

## 3. Is there an official or upstream option? [secondary + verified]

- Releases: `Omi_CV1_v3.0.15 ... v3.0.21` changelogs (GitHub API, 2026-10-13) mention power-off, Wi-Fi sync, sync speed, offline
  timestamps, VAD for power, charging status, bond-key removal, "fix audio sync issue". **No record toggle.** 3.0.21 (2026-08-05)
  is the latest.
- Upstream `main` (the `opensrc` cached snapshot; fetch date not recorded) is 3.0.21 with an unreleased cleanup of the software "VAD gate" into the AAD config; its
  `button.c` is identical to the tag apart from include order. **No record toggle.**
- The phone app's double-tap "pause/resume" is **app-side**: it cancels the BLE audio stream (`capture_controller.dart`
  `pauseDeviceRecording`), it writes nothing to the device. Headless (our case) it does nothing.
- Open upstream PRs `#13705` and `#14568` (customizable button actions) add a triple tap in the firmware and let the app choose
  what each tap does; "mute/unmute" stays the app-side stream pause. Issue `#19563` is about app-side mute and offline-sync
  durability. None is merged, and none gates the firmware's SD writes [secondary: PR bodies].

## 4. Build

**Pinned toolchain** [verified]: nRF Connect SDK **v2.9.0**, sysbuild + MCUboot, board `omi/nrf5340/cpuapp`
(`scripts/ci/build-cv1.sh`; the upstream guide `BUILD_AND_OTA_FLASH.md`). Outputs: `dfu_application.zip` (the OTA package),
`merged.hex`, `merged_CPUNET.hex`.

**Upstream already has the CI to mirror** [verified]: `.github/workflows/firmware_release.yml` - `workflow_dispatch`, one job
`build-cv1` on `ubuntu-latest`, image `ghcr.io/zephyrproject-rtos/ci:v0.26.13` pinned by digest, `west init -m nrfconnect/sdk-nrf
--mr v2.9.0`, `west update -o=--depth=1`, `cp omi.conf prj.conf`, `west build -b omi/nrf5340/cpuapp --sysbuild`. With `publish`
unset it is **build-only** and uploads the three files as a run artifact; no secrets are needed (the Omi Bot app token is only
minted on a publish run). The upstream README quotes ~1.5 GB of SDK download and 20-30 min cold. Licence: MIT (`LICENSE`), so a
fork is fine; keep the notice.

| Where | Verdict |
|---|---|
| **GitHub Actions in a small fork** (x86, hosted, free for public repos) | **Recommended.** Fork `BasedHardware/omi` (or a minimal repo with only `omi/firmware/` and a trimmed copy of that workflow), apply the patch on a branch based on the `Omi_CV1_v3.0.21` tag, dispatch the build-only run. Zero load on the Jetson. Keep the fork separate from the Zoe repo (this repo is public and takes no vendored firmware). Reproducibility check first: build the **unmodified** tag and compare its app image against the shipped `omi.signed.bin` (same size 248 644, same image-hash behaviour, same section layout; byte-identical payload is not guaranteed because of build timestamps) - a pipeline that cannot reproduce stock does not get a patched image flashed. |
| **This Jetson (aarch64)** | **Possible, not recommended now, and unproven.** The CI image has an `arm64/v8` variant (registry manifest checked; ~3.96 GB compressed, 3 layers), disk is ample (`/` 1.3 TB free), 8 cores. But free RAM is the constraint: 15.7 GB total, **~1.9 GB available** (`free -m`, 2026-10-13) with the voice brain resident; an NCS build peaks at several GB [unmeasured]. Upstream only ever runs this on x86; arm64 NCS 2.9 + Zephyr SDK inside that image is **[unverified]**. If used: a quiet window, `docker run --memory=3g`, `ninja -j2`, never during the voice harness. |
| Laptop with Nordic's `nrfutil toolchain-manager` | Fine (upstream's documented path); not a box we control here. |

## 5. Risk and recovery

### 5.1 What "overwrite-only" means for a bad image [verified in `sdk-mcuboot v2.1.0-ncs3`]

- After a reset MCUboot validates the secondary slot's signature, **copies** it over the primary, then erases the secondary's
  header and trailer (`boot/bootutil/src/loader.c:1952-1971`, under `#ifndef MCUBOOT_OVERWRITE_ONLY_KEEP_BACKUP`). There is
  **no swap state, no test image, no revert** (`loader.c:2435-2441`: "overwrite-only mode has no swap state"). The old
  firmware is gone after the copy.
- The omi build has **no serial or USB recovery** in the bootloader (`omi/sysbuild/mcuboot.conf` and
  `bootloader/mcuboot/mcuboot.conf` set no `MCUBOOT_SERIAL`).
- Downgrade prevention compares version numbers and **rejects only a strictly older secondary** (`loader.c:1168-1220`). Every
  Omi image is `0.0.0`, so a custom image is accepted and, by the same rule, the Omi phone app's "update" would silently
  overwrite our firmware with stock if the app ever touched it. (This also means **a stock 3.0.18 image is available as a way
  back**: `Omi_CV1_v3.0.18` is published with `Omi_CV1_OTA_v3.0.18.zip`, 430 318 bytes. The plan's 4.10 sentence "no 3.0.18 image
  is known to be published" is wrong and is corrected below.)
- The panel updater's "test then confirm, power-cycle reverts" language in plan 4.10 step 4 is **wrong for this bootloader**;
  `docs/knowledge/bluetooth-settings.md` and `omi_dfu.py` already say it correctly (test = confirm, no revert).

So the failure cases are:

| Failure | Recovery over BLE? |
|---|---|
| Image does not pass signature/hash | MCUboot refuses it; the old image stays. Safe. |
| Power loss during the copy | The secondary still holds the image (header erased last), so MCUboot should retry on the next boot [reasoned from `loader.c`, **unverified on hardware**]. This is the one risky instant: charger + battery gates exist in the updater. |
| Valid, signed image that boots but has broken BLE/SMP, or crashes (the watchdog is armed first, `main.c:197`, so it reboot-loops) | **None.** SWD only. |
| Valid image with a logic bug in the gate (never records / always records) | Fixable over OTA as long as SMP works - which the patch does not touch. |

### 5.2 SWD recovery [secondary + schematic text, physical access unverified]

- Upstream's own docs: "CV1 requires a **special flashing cable** that connects to a J-Link programmer. Without this cable, the
  CV1 cannot be programmed" (`docs/doc/get_started/Flash_device.mdx:103-112`, "J-Link programmer, special SWD flashing
  cable"); the hardware docs say "Flash firmware via SWD debug port using a J-Link or nRF DK"
  (`docs/doc/hardware/consumer/index.mdx:140`).
- The mainboard v1.2 schematic text (`omi/hardware/consumer/electrical/mainboard/schematic.pdf`, `pdftotext`) shows a 2x3
  header carrying `~{RESET}`, `TXD`, `SWDCLK`, `RXD` and `SWDIO` (pin map not traced), plus test points beside `SWDCLK`, `SWDIO`
  and `~{RESET}` (TP4/TP5 and others). Where those are reachable in the retail enclosure, and what the "special cable" physically is, is
  **[unverified]**: the owner would need to look.
- Recovery recipe from upstream: `omi/firmware/FLASH_3.0.8/MAC/program_net.jlink` then `program_app.jlink` (`loadfile
  merged_CPUNET.hex` / `merged.hex` with `device nRF5340_xxAA_NET/APP`, `si SWD`, `speed 4000`), using the **latest release's**
  two hex files. Nothing in the config enables APPROTECT (`omi.conf` and `sysbuild.conf` mention none), so an SWD probe should
  connect; **[unverified]** because NCS defaults for nRF53 were not traced. A caveat: `merged_CPUNET.hex` carries the NSIB
  provisioning hash of *its own build* (section 1.3); writing a hex from another build onto a unit whose OTP is already
  provisioned could be refused or leave b0n unable to validate the stock net core **[unverified - ask Omi or test on a spare]**.
  Keep the exact net-core hex that matches the unit's provisioning if it can be identified.
- Probe: a SEGGER J-Link (EDU Mini) or an nRF5340 DK with its on-board J-Link used through the "Debug Out" header. Prices were
  not checked. The owner would need to buy or borrow one - **before** the first flash, not after.

### 5.3 Ways to reduce the risk

1. **Zero-hardware-risk steps first:** build the unmodified tag and compare to the shipped image; build the patch; verify on the
   host with the repo's own `omi_release.parse_package`-style checks (header sane, image-hash TLV matches, signature verifies
   under the root key, image size fits the slot in `boards/omi/pm_static.yml`).
2. **Make the patch only gate, never restructure** (section 2.2): a gate bug leaves BLE and SMP intact, so it can be fixed with
   another OTA. Fail-closed boot state means the worst logic bug is "does not record".
3. **A second unit** is the real safeguard - flash a spare first. An nRF5340 DK cannot stand in (different pins, no PDM mic, no
   T5838). Whether the owner has or wants a second CV1 is a decision; the price was not checked.
4. **Probe in hand, header located, hex files for stock 3.0.21 downloaded** (already in `/tmp/claude-1000/dfu/rel/` for this
   research) before the OTA.
5. **Keep the updater's gates:** on the charger, battery >= 50 %, median RSSI better than -75 dBm, no retry, cool-down
   (`docs/knowledge/bluetooth-settings.md`). Add an app-only "custom pin" mode (one pending image, hash verified against the
   pinned build, a reviewed edit like the official pin). Today it insists on two pending images.
6. A boot "safe mode" that skips the new code on a button hold would help only for *logic* bugs, which are already harmless; it
   cannot protect against a crash before it runs, and the bootloader cannot be changed over OTA. Not worth the code.

### 5.4 The public key is a security hole in the stock device [verified pieces, end-to-end not attempted]

- The root key is public and the images are signed with it (section 1).
- SMP is exposed (`omi.conf:36`) and, per the live bring-up (plan 0.3a), "pairing is not needed and nothing is bonded; the GATT
  table has no encrypted permissions".
- Therefore any BLE central within range can upload a validly-signed image, including one that turns the microphone into a
  covert transmitter, and the pendant's MCUboot will install it. I did **not** test this (it would need the device).
- Press-to-record does not close it. The fix is to replace the bootloader key with our own **over SWD** (Omi's immutable
  MCUboot then trusts only us), and/or require bonding for the SMP characteristic. That is a larger, SWD-only job; list it as an
  owner decision, not part of this change. Until then: treat the pendant as untrusted hardware in a hostile environment, and
  do not rely on "it is stock firmware" as an assurance.

## 6. Alternatives without custom firmware

| Option | Reliability | Verdict |
|---|---|---|
| **Pi-side "press to keep"** from the button characteristic `23ba7925` | **Poor.** Events `1` single, `2` double, `5` release are notified only while a central is connected **and** subscribed; with no connection the press is dropped, not stored, not logged (`button.c:103-106` and siblings). The characteristic holds only the latest event code with no time (`:260-269`). The pendant is out of range of the Pi most of the day by design ("sync when in range"). The Pi's single radio connection is also shared with the sync and the Bluetooth agent (`bluetooth-settings.md`). | Does not meet "record only after a press". It would be a marker that exists only when the owner is at home next to the panel. |
| **Pi retention policy** - upload only the segments that follow a marker, discard the rest | The pendant still records everything to its SD; `omi-sync` still drains it; to find the marker the Pi must transcribe the lot. The consent posture (plan 0.4, open-problems 2026-10-12) is about the pendant capturing guests, which this leaves exactly as it is. | Not an answer to the request. Useful only as a belt-and-braces **delete-by-default** rule on top of the firmware. |
| **GATT "mute" via mic gain** (`19b10012` writes 0..8; level 0 is labelled "mute") | **Does not mute.** `mic_set_gain` maps level 0 to PDM gain `0x00` (`src/mic.c:551-561`); nrfx defines `PDM_GAINL_GAINL_MinGain (0x00)` as **-20 dB gain adjustment** (and `0x28` = 0 dB, `0x50` = +20 dB) for the nRF5340 application PDM (`nrfx/bsp/stable/mdk/nrf53/nrf5340/nrf5340_application_bitfields.h:10727-10729`). Speech is only quieter. The setting persists in flash (`mic.c:246-247`). | Rejected. Also: control only while connected. |
| **Schedule-based mute window** (power the pendant off outside the owner's chosen times) | The only no-firmware control that really stops capture is the 3 s hold power-off (`button.c:214-238`), which the owner does by hand. | Fine as a habit; not a feature. |
| **Phone app** | Rejected earlier with evidence (plan 0.2); its pause is app-side only (section 3). | No. |

Conclusion: there is no stock mechanism that gates recording on the device. Press-to-record needs firmware.

## 7. Recommendation

1. **Yes, ship it, as a minimal app-core patch (section 2.2), but gate the flash on recovery.** OTA is technically open (public
   key). The risk is a bricked unit with no OTA path back.
2. **Order of work (research and CI first, hardware last):**
   1. Fork/CI: build unmodified `Omi_CV1_v3.0.21` on a GitHub x86 runner; diff against the shipped images (section 4).
   2. Write and review the patch; build it; run the host-side image checks (5.3 step 1).
   3. Extend `panel-bluetooth` with a pinned "custom build" mode (app-only, one image, hash-pinned; the existing seven gates stay)
      and fix `version_tuple` for the chosen version string.
   4. **Owner gate:** SWD probe obtained, header located, stock hex files to hand, ideally a spare unit.
   5. One supervised OTA on the charger next to the panel (the existing runbook), then a short recording test; `omi-sync
      check` after.
3. **Do not** bundle the SWD key-replacement hardening (5.4) into this change; list it separately.
4. Update `docs/architecture/omi-integration-plan.md` 4.9/4.10 and `docs/knowledge/open-problems.md` as the phase moves (done in
   this PR: pointers and the two corrections below).

## 8. Owner decisions

1. **Gesture:** single tap toggles (recommended) / double tap toggles / hold-to-record is **not** available (the 3 s hold is
   power-off and must stay).
2. **Boot state:** closed (recommended) - a reboot never resumes recording.
3. **Auto-stop** after N minutes (30 suggested), or none.
4. **Feedback:** haptic on/off, and whether the LED is a recording light (recommended) or stays as the stock status light.
5. **Recovery hardware:** buy/borrow an SWD probe (J-Link or nRF5340 DK) and check the case access **before** the first flash; a
   second CV1 as a test unit, yes/no.
6. **Fork upkeep:** accept that upstream firmware updates (new 3.0.x) are not applied automatically and must be rebased; never
   let the Omi phone app update this pendant (it would silently restore stock).
7. **Hardening (separate):** replace the bootloader key over SWD so only we can sign (5.4), yes/no/when.
8. **Legal check** for wearing it in company (plan Q5) still stands; the LED/haptic make the recording state visible but do not
   replace consent.

## 9. Corrections made to earlier text (this PR)

- `docs/architecture/omi-integration-plan.md` 4.10: "no 3.0.18 image is known to be published" - **it is published** (release
  `Omi_CV1_v3.0.18`, asset `Omi_CV1_OTA_v3.0.18.zip`); and step 4's "test then revert if not confirmed" does not apply to
  overwrite-only. Fixed in place with a pointer here.
- 4.9 phase 2 now points at this record.

## 10. Reproduce the signing check (no device needed)

```
opensrc path BasedHardware/omi@Omi_CV1_v3.0.21        # source at the tag
gh release download Omi_CV1_v3.0.21 -R BasedHardware/omi -p 'Omi_CV1_OTA_v3.0.21.zip'   # sha256 9fb93885...d3674
# parse the MCUboot TLVs (struct '<IIHHII' header; TLV area after hdr+img: key-hash 0x01, image-hash 0x10, sig 0x20)
openssl rsa -in omi/firmware/bootloader/mcuboot/root-rsa-2048.pem -RSAPublicKey_out -outform DER | sha256sum
# expect fc5701dc6135e1323847bdc40f04d2e5bee5833b23c29f93593d00018cfa9994 (= the key-hash TLV in both images)
# verify: RSA-PSS(MGF1-SHA256, salt 32) over header+image with the repo key's public half, using python-cryptography
```

Artifacts used (this box, not committed): `/tmp/claude-1000/dfu/Omi_CV1_OTA_v3.0.21.zip`, releases 3.0.18 and 3.0.20 OTA zips and
the 3.0.18 / 3.0.21 `*_CPUNET.hex` and 3.0.21 `merged` hex downloaded from the public releases.

## 11. Sources

- Omi firmware at tag `Omi_CV1_v3.0.21` (`opensrc`): the files cited above; `omi/firmware/scripts/ci/{build-cv1.sh,README.md}`;
  `.github/workflows/firmware_release.yml`; `docs/doc/get_started/Flash_device.mdx`; `omi/hardware/consumer/electrical/mainboard/schematic.pdf`.
- MCUboot: `mcu-tools/mcuboot` (`root-rsa-2048.pem`, `boot/zephyr/Kconfig`), `nrfconnect/sdk-mcuboot@v2.1.0-ncs3`
  (`boot/bootutil/src/loader.c`), NCS `nrfconnect/sdk-nrf@v2.9.0` (`west.yml`, `sysbuild/Kconfig.secureboot`,
  `cmake/sysbuild/debug_keys.cmake`, `subsys/bootloader/bl_validation/bl_validation.c`), `NordicSemiconductor/nrfx`
  (PDM gain bitfields).
- GitHub (2026-10-13): `BasedHardware/omi` releases `Omi_CV1_v3.0.15 ... v3.0.21`; PRs `#13705`, `#14568`, issue `#19563`
  [secondary].
- In-repo: `docs/architecture/omi-integration-plan.md` (0.3a, 4.9, 4.10), `docs/knowledge/bluetooth-settings.md`,
  `services/panel-bluetooth/panel_bt/drivers/omi_dfu.py`, `services/omi-sync/omi_sync/ring_protocol.py`.
