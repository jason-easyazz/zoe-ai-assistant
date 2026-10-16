---
type: architecture-plan
date: 2026-09-25
updated: 2026-10-17
status: 🔨 owner chose "record on the pendant, sync when in range of Zoe" (2026-10-12) — phase 1 built flag-dark (services/omi-sync + zoe-data omi_recordings); the real pendant runs firmware 3.0.18 (below the ring protocol) so the 3.0.18 file path is opt-in and a firmware update is the recommended route (section 4.10); hardware-only facts still UNVERIFIED (section 0.3a); the live-stream path (sections 4a-6) is now the LATER path, not the first
owner: W6 (attributed ambient capture) — samantha-evolution-plan.md; program block B9 below
audience: Jason + every agent picking up the Omi work
---
# Omi wearable → Zoe: integration plan

> **Update 2026-10-17.** The pendant was updated to firmware **3.0.21** on 2026-10-13 (supervised DFU from the panel) and
> `omi-sync` drains it on the **ring** path. The 3.0.18 legacy-file path and the codec `0x0f` question below are history for
> this unit. The end-to-end gap audit (BLE exposure while away, silent overwrite, backups, search, the law, what the day
> review must call into) is [`docs/research/omi-gap-audit-2026-10.md`](../research/omi-gap-audit-2026-10.md).
>
> **One line (2026-10-12).** The pendant pairs with **Zoe, not the Omi phone app**. Jason records on
> the pendant away from home (on stock firmware "recording" = the pendant is powered on); when he
> comes within Bluetooth range of the panel Pi, a small flag-dark service drains the pendant's SD
> ring, decodes Opus to 16 kHz, uploads each recording to zoe-data, and frees the ring **only after
> zoe-data confirms it stored that recording**. Moonshine transcribes it locally; it is filed as
> **his** recording — reviewable, deletable, kept out of fact extraction until reviewed. Nothing
> touches Omi's cloud. Section 0 is the decision and its evidence; section 4 is the design and the
> operator runbook.
>
> *The earlier one-liner (live-stream to `/api/voice/ambient`, speaker-gated `ambient_memory`) is
> sections 4a-6 below — still the later "ambient" path, no longer the first thing built.*

## 0. Owner decision 2026-10-12 — record on the pendant, sync when in range of Zoe

### 0.1 The decision

The pendant **pairs with Zoe (the panel Pi), not the Omi phone app.** Jason records on the pendant
away from home; when he is within Bluetooth range of the panel Pi, Zoe downloads what the pendant
stored, transcribes it locally with Moonshine, and files it as **his recordings** (reviewable,
deletable). Fully private: no audio and no transcript goes to Omi. On stock firmware "recording" is
simply *the pendant is powered on* (a 3 s hold powers it off); a true press-to-record toggle is a
later firmware phase (section 4.9).

This **supersedes the live-stream bridge as the first thing built.** The live path (sections 4a-6:
BLE notify stream → `/api/voice/ambient`, speaker gate, `ambient_memory`) is kept as the later
"ambient" path and keeps its own gates; nothing in it is deleted by this decision.

### 0.2 Why the phone app is rejected (evidence recorded 2026-10-12)

| Claim | Evidence |
|---|---|
| The store app sends transcripts to Omi **even with a custom STT** configured | Observed in the owner's 2026-10-12 investigation of the store build (custom STT is a setting — `app/lib/pages/settings/transcription_settings_page.dart` — but the conversation/transcript still posts to Omi's conversation backend). Recorded here as the owner's finding; not re-derived line-by-line in this PR. |
| Offline (SD ring) audio goes **only** to Omi's servers | The phone app's ring sync writes each downloaded chunk into its local WAL "so it reaches the cloud" (`app/lib/services/wals/ring_storage_sync.dart`, the flush comment before `inFlightFlush`) and the WAL upload is the `/v1/sync-local-files` route on Omi's backend (`app/lib/backend/http/api_edge_throttle_telemetry.dart`). There is no local-only sink. |
| The **custom server option is compiled out of release builds** | `app/lib/pages/settings/developer.dart:389` shows the custom-backend field only `if (!kReleaseMode)`, and `app/lib/env/backend_url_override.dart:27` restores a saved override only when `runtimeAllowed = !kReleaseMode`. A store build always talks to `https://api.omi.me/` (`app/lib/env/env.dart`, `productionApiBaseUrl`). Self-hosting would mean building and sideloading our own app. |
| The "data protection" encryption is **server-side**, not end-to-end | `backend/utils/encryption.py`: the per-user key is `HKDF(ENCRYPTION_SECRET, salt=uid)` — derived from a master secret held in the backend's environment. Omi can read what it "protects". |

Together with the earlier findings (section 3, option B: the self-hosted backend still needs
Firebase/GCS/Pinecone/OpenAI; one bonded central only) the conclusion stands: **no phone app in
the path.** Un-pairing it is an operator step (4.7).

### 0.3 Protocol and hardware facts — verified vs still to verify

**Verified from Omi's MIT source on 2026-10-12** (`omi/firmware/omi/src/lib/core/storage.c`,
`app/lib/services/devices/ring_protocol.dart`, `omi_connection.dart`,
`app/lib/services/wals/PENDANT_RING_CUSTODY.md`; full list in `services/omi-sync/THIRD_PARTY_NOTICES.md`):

- The storage service/characteristics (the earlier plan marked these UNVERIFIED — now confirmed):
  service `30295780-4301-eabd-2904-2849adfeae43`; `…81` is **write + notify** (commands in, ACK/INFO/READ data
  out); `…82` is **read + notify**, returning a 16-byte status (four little-endian u32: used bytes, unread
  records, free bytes, rtc-valid).
- Commands (first byte, big-endian integers): `0x10` INFO, `0x11` READ `[start:u64][count:u32?]`, `0x12`
  ADVANCE `[seq:u64]`, `0x13` CLEAR (**never sent by Zoe**), `0x03` STOP; custody firmware adds `0x14`
  (live-persist opt-in, unused here) and `0x15` ADVANCE_ID `[ring_id:u64][seq:u64]`.
- Notifications: `0x01` ACK `[status]` (6 invalid command, 9 storage not ready, 10 seq out of range, 11 ring-id
  mismatch), `0x02` INFO (31 bytes legacy; 41 with custody tail `[caps][version][ring_id]`), `0x03` DATA
  (raw bytes, **not aligned to record boundaries**), `0x04` DONE `[status][next_seq:u64]`, `0x05` READ_BEGIN
  `[start:u64][count:u32]`.
- A record is **444 bytes = 4-byte big-endian timestamp + 440-byte payload** packed `[size:1][frame:size]…`
  with zero padding; bit 31 of the timestamp means "seconds since boot, RTC unset". Opus codec id **21** on the
  CV1 = 320 samples / 20 ms at 16 kHz (the lab receiver's finding, unchanged).
- Ring commands need firmware **≥ 3.0.20** (DIS `2A26`); the sync service never sends a ring command below it
  (3.0.18, which is what the owner's pendant runs, gets the separate opt-in legacy path — 0.3a). `main`'s `omi.conf`
  reports `CONFIG_BT_DIS_FW_REV_STR="3.0.21"`.
- Time sync: write a `uint32` **little-endian** epoch to `19b10031-…`; read it back from `19b10032-…`.
- **Stock firmware advances its own ring pointer as the link layer acknowledges data** (`sync_checkpoint_advance` in
  `storage.c`) — the custody contract's `CAP_APP_ACK_RECLAIM` (firmware never frees on its own; only ADVANCE
  frees) exists in a firmware PR, not in the source snapshot read. That is why the Pi keeps a durable spool
  (4.2) rather than trusting the pendant to hold what it has already delivered.

### 0.3a Real-device bring-up, 2026-10-12 (VERIFIED on the owner's pendant, read-only)

From the panel Pi (`zoe-pi`, Pi 5, BlueZ 5.66); nothing was written to any application characteristic. Evidence: the
bring-up record `~/.zoe/agent-tools/omi_bringup_2026-10-12.md` (btmon traces, full GATT table) and its probe script.

- **The pendant is `Omi CV 1`, hardware `5.0`, FIRMWARE `3.0.18`** — BELOW the 3.0.20 the ring protocol needs. The source
  tree's `3.0.21` is not what this unit runs. The ring commands therefore do not apply to it (4.2 explains the other path;
  4.10 the update option). Static random address, advertises service `19b10000-…`.
- **Pairing is not needed and nothing is bonded** (`Paired: no`). The pendant's SMP Security Request is answered "not
  supported" by BlueZ and the link carries on; the GATT table has no encrypted permissions. Do not register an agent.
- **A BlueZ-initiated connection fails at this range** — every one of 24 attempts ended `Connection Timeout 0x08`. Cause: the
  panel sees the pendant's adverts at **-88 to -96 dBm**, and BlueZ creates the link with a **420 ms supervision timeout**,
  which expires during the dozens of ATT round-trips of service discovery. **The recipe that worked 4/4:** create the LE link
  yourself with a long supervision timeout — `sudo hcitool lecc --random <addr>` (32 s timeout, 18.75 ms interval) — then
  attach bleak to the already-connected device, no scan, no pairing; retry with backoff (1 attempt in 6 stalls for a
  minute). The pendant then negotiates ATT MTU 498, data length 251, a 15 ms interval and a 4 s timeout on its own. This is
  implemented in `ble_link.connect` (`OMI_SYNC_LECC`, `OMI_SYNC_LECC_PREFIX`). `hcitool` is deprecated; a better
  mechanism (BlueZ `main.conf` `[LE] ConnectionSupervisionTimeout` after clearing the per-device params, or an mgmt
  `conn-param` load) was **not tried** — the command is configurable so it can be swapped. A USB BLE dongle with an
  external antenna, or a few metres' less distance, would help reliability more than any of it.
- One connection at a time (`CONFIG_BT_MAX_CONN=1`): **if the phone app is connected, Zoe cannot be.** Unpairing the phone
  app stays a required operator step.
- **Services verified present** (UUIDs match the source): storage `30295780-4301-eabd-2904-2849adfeae43` with `…81`
  (write + notify) and `…82` (read + notify); audio `19b10001` (read, notify) / codec `19b10002` (read); button
  `23ba7925` (read, notify); time sync `19b10031` (write) / `19b10032` (read); settings `19b10011/12/13`; features
  `19b10021`; haptic `cab1ab96` (write); MCUmgr **SMP service `8d53dc1d-1db7-4cd3-868b-8a527460aa84`** (OTA — never touched).
- **Device clock is stale**: `19b10032` read `2026-06-25 05:45 UTC` and ticking in real seconds, i.e. never synced by a
  phone. Recording timestamps from this unit are therefore months behind unless the clock is set (4.2, "times").
- Features bitmask `0x1EC` = button, battery, haptic, **offline storage**, LED dimming, mic gain (no speaker, no
  accelerometer, no USB). Battery 93-94 %.
- **Audio notifications: 0 packets in 5 s in a quiet room** (twice). Expected from the hardware VAD (`T5838` AAD sleeps the
  mic after 10 s under the threshold) — not a fault, and not what this design reads anyway (it reads the stored files, not
  the live stream). Do not treat silence as failure.
- **Codec characteristic reads `0x0f` (15).** The source says `CODEC_ID 21` (`0x15`) for the CV1 and `20` for the DevKit
  at every revision I could read (`config.h` has not changed since 2025-10-07, `transport.c` returns that macro) — **id 15
  is not defined in any revision I could find**, and the app has no mapping for it. Unresolved. The sync service therefore
  refuses an unknown codec id unless the operator sets `OMI_SYNC_ASSUME_CODEC` (21 expected), derives durations from the
  decoded audio rather than the id, and **holds** any recording whose frames do not decode (the raw records stay in the
  spool; nothing is uploaded or lost).

**What firmware 3.0.18 speaks** (read from `storage.c` at the commit that shipped it, `5cee930fbb`, 2026-05-04; the
app's `storage_sync.dart` / `omi_connection.dart` are the client): the **multi-file SD protocol**, not the ring. Status
read `[used_bytes][file_count][0][0]` (LE u32); `0x10` LIST_FILES → one notification `[count]([ts u32 BE][size u32 BE])*`;
`0x11` READ_FILE `[index][offset u32 BE]` → notifications `[ts u32 BE][440 bytes]` then a single byte `100`; `0x12`
**DELETE_FILE** (the same byte that is ADVANCE on the ring protocol!); `0x03` STOP. **The firmware deletes each file
itself the moment its last byte is notified** (unless it is the file currently being recorded), with no app
acknowledgement and no way to defer. So on 3.0.18 the app **cannot** gate the free: "never free before a confirmed save"
holds only as far as the **Pi's durable spool** (every block fsynced before anything else; the spool outlives the
pendant's copy). That is why this path is **opt-in** (`OMI_SYNC_LEGACY_FILES=1`, default off), covers exactly 3.0.18 (3.0.19
changed `storage.c` and was not read), never sends 0x12, and why the preferred fix is the firmware update (4.10).
Files can be resumed (we pass the offset of the next unspooled block); an interrupted file is not deleted.

**Still UNVERIFIED — to be measured on the device with the owner present (4.7, `python -m omi_sync check`):**

1. That 3.0.18 behaves exactly like the `storage.c` at `5cee930fbb` on the wire — **the whole legacy drain is unproven on
   hardware** (it is tested only against a fake pendant built from that source). In particular the LIST notification
   carries every file only if it fits one notification (MTU-3 = 495 bytes → at most 61 files; what the firmware does with more
   is unknown), and whether each READ notification really is one 440-byte block at MTU 498 (a different size aborts the
   read and sends STOP rather than misalign).
2. Whether files recorded while the clock was unset are renamed to real timestamps when the clock is written
   (`sd_notify_time_synced`'s comment says so) — i.e. whether `OMI_SYNC_SET_DEVICE_TIME=1` repairs the times of audio already on
   the card, or only of audio recorded afterwards. Until then such recordings carry `time_quality: estimated`.
3. What codec `0x0f` is (above) and whether the frames in the files decode with libopus as Opus FS320.
4. Whether the SD files fill up / what the card's real capacity in hours is (the legacy status read reports `free_bytes = 0`
   — the firmware has a TODO there), and how many files accumulate per day (the 60 s "continue threshold" splits files).
5. Real drain throughput over the marginal link (~14 MB/h of Opus), and whether the pendant keeps recording to the card
   while connected.
6. Battery life with continuous SD writes (the vendor's 10-14 h is a claim).
7. Whether the DFU in 4.10 works from the Pi at this range (the OTA streams ~425 KB over the same marginal link).
8. For ring firmware (once updated): everything the earlier list asked — the RTC across power cycles, capacity,
   `dropped_records`, and whether INFO byte 31 advertises the custody capabilities (expected `0x00` on stock).

### 0.4 Consequences for the consent posture (read this before wearing it near other people)

The older sections made a storage-OFF firmware variant **mandatory** for the no-trace guarantee (section 5,
B9.0). **This design requires the ring ON**, so that guarantee is **not offered** on stock firmware: the pendant
records whatever is said near it while powered, and the ring holds it until the Pi drains it. What the
design does instead:

- It is the **owner's device, worn by the owner**; recordings are attributed to the owner only
  (`ZOE_OMI_OWNER_USER_ID`), and only the owner can list, read or delete them (admin does not widen it).
- Every transcript is stored **`speaker_state='unverified'`, `reviewed=0`** (other voices may be in it) and **nothing
  else reads the table** — no fact extraction, recall, digest or prompt — until a reviewed-promotion step exists
  (guarded by a static test). *The day review in [`memories-space.md`](memories-space.md) §4-§5 is that step, as
  designed: named readers only (the static test's allow-list grows by name), approval before every write, and learned
  voices of tagged people as a recorded owner exception to the section-5 `ambient_consent_at` rule (§2.6 there).*
- **Audio is deleted right after transcription** unless he keeps it; any WAV that remains expires after 7 days
  (`ZOE_OMI_AUDIO_RETENTION_DAYS`); transcripts live until he deletes them; "forget" (a name or the whole user)
  redacts/erases them (4.4).
- The legal question (WA Surveillance Devices Act 1998 s5/s9 for guests; Q5 in section 8) is **still open and is not
  answered by this design.** Until it is, the honest posture is: wear it alone / with people who know, and prefer
  the phase-2 press-to-record firmware (4.9) before wearing it in company.

## 1. Goal

Jason bought an [Omi](https://www.omi.me/) pendant (open hardware/firmware/software by
BasedHardware, [github.com/BasedHardware/omi](https://github.com/BasedHardware/omi), MIT).
Bring it into Zoe so that things Jason says *near* Zoe — anywhere in the house, not only
at a panel — become attributed ambient memory, under the rules that already bind Zoe:

- **Local-first, nothing leaves the house unless opted in** (`docs/VISION.md`; canonical
  STT is Moonshine, canonical brain is Gemma 4 — the pendant supplies audio, never a
  transcript from someone else's cloud).
- **Consented capture is a pillar, not a follow-up** — W6 in
  [`samantha-evolution-plan.md`](samantha-evolution-plan.md) (Limitless "Consent Mode"
  is the named prior art; Bee's always-on default is the anti-pattern), and the W6 gate
  addition: a legal sanity check for *guests* under the WA Surveillance Devices Act.
- **Biometrics only with a stored consent stamp**, matching excludes non-consenting rows
  at the source ([`biometric-retention-policy.md`](../knowledge/biometric-retention-policy.md) §3).
- **Lab first, flag-dark, replay-gated; RAM is the ceiling** (beat-the-bar program §0).

Non-goals: replacing the panel voice path; a second memory system; using Omi's app,
backend, "apps" marketplace, MCP or CLI; any cloud STT.

## 2. What the device actually is (verified 2026-09-25)

Two very different products share the name. Check which one is on the desk first:
the BLE Device Information *Model Number* string is **`Omi CV 1`** for the consumer
pendant (`CONFIG_BT_DIS_MODEL` in
[`omi/firmware/omi/omi.conf`](https://github.com/BasedHardware/omi/blob/main/omi/firmware/omi/omi.conf)).

| | **Omi (consumer, "CV1")** — what omi.me sells for $129 | **DevKit 2** |
|---|---|---|
| SoC | **nRF5340** dual-core (`BOARD=omi/nrf5340/cpuapp` in [`CMakePresets.json`](https://github.com/BasedHardware/omi/blob/main/omi/firmware/omi/CMakePresets.json)); nRF Connect SDK v2.9 (Zephyr) | Seeed XIAO **nRF52840** Sense; NCS v2.7 |
| Radios | BLE 5.x (2M PHY, coded PHY, +8 dBm TX power configured); an **nRF7002 Wi-Fi 6** chip is on the board ([hardware doc](https://docs.omi.me/doc/hardware/OmiConsumer.md), `omi_nrf5340_cpuapp.dts`) but **stock firmware does not enable Wi-Fi** (no `CONFIG_WIFI` in the app build) | BLE only |
| Mics | dual **T5838 PDM** with hardware acoustic-activity-detect: mic sleeps (~20 µA) after 10 s below threshold and wakes on sound (`CONFIG_OMI_ENABLE_T5838_AAD=y`, `mic.c`) | one PDM mic |
| Storage | **SD ring buffer, records continuously while powered** (`CONFIG_OMI_ENABLE_OFFLINE_STORAGE=y`, `storage.c`) | 8 GB, file-based, "standalone recording mode" |
| Button / haptic / speaker | button **yes**, haptic **yes**, **speaker no** (`CONFIG_OMI_ENABLE_SPEAKER=n`) | button yes, **speaker yes** |
| Battery | "10 to 14 hours" ([omi.me](https://www.omi.me/)) | ~150 mAh, similar claim |
| Firmware | open, MIT, flashable: UF2 drag-drop and BLE OTA via mcumgr (`CONFIG_NCS_SAMPLE_MCUMGR_BT_OTA_DFU=y`); [build doc](https://docs.omi.me/doc/developer/firmware/Compile_firmware.md), [flash doc](https://docs.omi.me/doc/get_started/Flash_device.md); complete PCB/Altium/STEP/BOM under MIT ([hardware files](https://docs.omi.me/doc/hardware/consumer/index.md)) | same toolchain, UF2 |
| BLE topology | **`CONFIG_BT_MAX_CONN=1`, `CONFIG_BT_MAX_PAIRED=1`** — exactly one central at a time. If the Omi phone app is connected, Zoe's receiver cannot be, and vice versa | same |

Live firmware at time of writing: `CONFIG_BT_DIS_FW_REV_STR="3.0.21"`, HW rev 5.0.

### 2.1 The BLE protocol (what a receiver has to speak)

Source of truth: [`sdks/device/dart/lib/uuids.dart`](https://github.com/BasedHardware/omi/blob/main/sdks/device/dart/lib/uuids.dart)
(mirrored from the Flutter app's `models.dart`), the [protocol doc](https://docs.omi.me/doc/developer/Protocol.md),
and the firmware (`transport.c`, `codec.c`, `config.h`, `storage.c`).

| Service / characteristic | UUID | Notes |
|---|---|---|
| Omi audio service | `19b10000-e8f2-537e-4f6c-d104768a1214` | |
| Audio data (notify) | `19b10001-…` | **3-byte header** — `u16` packet number (LE) + `u8` fragment index — then one Opus frame |
| Codec id (read) | `19b10002-…` | `0` PCM16 16 kHz · `1` PCM16 8 kHz · `10/11` µ-law · **`20` Opus 16 kHz** (default since fw 1.0.3) |
| Button service / trigger (notify) | `23ba7924-…` / `23ba7925-0000-1000-7450-346eac492e92` | events: `1` single tap · `2` double · `3` long (~3 s) · `4` down · `5` up ([DevKit2 testing doc](https://docs.omi.me/doc/developer/DevKit2Testing.md)) |
| Storage service / data / control | `30295780-…` / `30295781-…` / `30295782-4301-eabd-2904-2849adfeae43` | CV1 ring buffer: `0x10 RING_INFO`, `0x11 RING_READ seq[,count]`, `0x12 RING_ADVANCE`, `0x13 RING_CLEAR`, `0x03 STOP_SYNC`; fixed 444-byte records with a device timestamp; read checkpoint auto-persisted every 2 s of *confirmed* bytes |
| Time sync write / read | `19b10031-…` / `19b10032-…` | write epoch seconds so stored records get wall-clock stamps |
| Battery | `0x180F` / `0x2A19` | notify since fw 1.5 |
| Device information | `0x180A` (`0x2A24` model, `0x2A26` fw, `0x2A27` hw) | `Omi CV 1` / `3.0.21` / `5.0` |

Audio encoding (consumer `config.h` + `codec.c`): `opus_encoder_init(16000, 1, VOIP)`,
**32 kbps VBR, complexity 3, 20 ms frames (320 samples)**, `OPUS_SIGNAL_VOICE`, DTX off,
FEC off. So ~50 notifications/s of ~80 bytes ≈ **4 KB/s ≈ 32 kbit/s** per pendant —
trivial for any receiver. Link params: L2CAP MTU 498, DLE 251, preferred connection
interval 7.5–15 ms, latency 0.

### 2.2 Existing open clients

- **Official Python SDK** — [`omi-sdk`](https://docs.omi.me/doc/developer/sdk/python.md),
  source [`sdks/python`](https://github.com/BasedHardware/omi/tree/main/sdks/python).
  `bleak==0.22.3` + `opuslib` (+ `pyogg`); `listen_to_omi(mac, uuid, cb)` and
  `OmiOpusDecoder` (strips the 3-byte header, decodes at 16 kHz with a 960-sample max
  frame). Runs on Linux/BlueZ. Its `transcribe()` is Deepgram — ignore it; the `whisper`
  extra is optional. Known bug: listeners never observe disconnects
  ([#13290](https://github.com/BasedHardware/omi/issues/13290)) — a receiver must
  register bleak's `disconnected_callback` itself and reconnect.
- **Other device SDKs** in-repo: Dart, Rust (`sdks/rust/omi-device`), TypeScript, Go, C++,
  Swift ([overview](https://docs.omi.me/doc/developer/sdk/sdk.md)).
- **[`j2h4u/omi-collector`](https://github.com/j2h4u/omi-collector)** — a self-hosted
  *Linux* systemd service that drains the **CV1 offline ring buffer** over BLE with stock
  firmware and writes `records.bin` + `manifest.json` bundles. No live streaming.
  **PolyForm Noncommercial** (source-available, not OSI) — fine to *read* for the
  ring-buffer protocol and gotchas, **never vendor**. Its two field notes matter:
  the device clock drifts minutes ahead of host time, and the stock firmware
  **advances its persisted read checkpoint while transmitting**, so a transfer
  interrupted by walking away can lose records — keep the pendant near the receiver
  until a drain finishes.
- **[`kbdevs/omibutfree`](https://github.com/kbdevs/omibutfree)** ("Omi Local") — a
  Flutter **iOS-only** replacement app: BLE → local Whisper / sherpa-onnx → SQLite. Proof
  that fully-local works on the phone; not usable on Linux.
- Omi's own **macOS desktop** app does on-device diarization + "remembered voices"
  ([PR #13055](https://github.com/BasedHardware/omi/pull/13055)) — useful as a design
  reference (§5), not as a component.

## 3. Options compared (ranked for local-first)

| | (A) **Pendant → BLE → Linux receiver → Zoe** | (B) Omi phone app + self-hosted Omi backend | (C) Omi app + Omi cloud + "integration app" webhooks |
|---|---|---|---|
| Audio leaves the house? | **No.** BLE to a box in the house; Moonshine on the Orin | Phone → *your* backend. But the backend is not local: it **requires** Google Cloud/Firebase/Firestore, GCS buckets, Redis, Pinecone, OpenAI, Google + Apple OAuth, ngrok ([Backend Setup](https://docs.omi.me/doc/developer/backend/Backend_Setup.md)) | **Yes.** Audio streams to Omi's cloud (Deepgram/Parakeet/Modulate STT, OpenAI), transcripts stored in GCP/Firebase ([privacy policy](https://help.omi.me/en/articles/13162549-omi-privacy-policy)); webhooks fire *from* their cloud ([Integrations](https://docs.omi.me/doc/developer/apps/Integrations.md)) |
| STT | Moonshine (the rock) | Serving STT is "**Parakeet and Modulate only**"; hosted Deepgram is disabled ([transcription doc](https://docs.omi.me/doc/developer/backend/transcription.md)). The Parakeet service ([`backend/parakeet`](https://github.com/BasedHardware/omi/blob/main/backend/parakeet/README.md)) is NVIDIA NeMo TDT-0.6b / RNNT-1.1b on GKE GPUs, reached via `HOSTED_PARAKEET_API_URL`. One *could* stand a Moonshine shim behind that URL, but everything around it (Firestore, Pinecone, OpenAI for memory extraction) stays mandatory | Deepgram / Parakeet / Modulate |
| Speaker ID | Zoe's own (resemblyzer today; sherpa-onnx ERes2Net/CAM++ + margin rule = B4.1) | Omi's speech profile (pyannote WeSpeaker ResNet34-LM, cosine 0.45; a parallel SpeechBrain ECAPA post-process — [#12765](https://github.com/BasedHardware/omi/issues/12765) calls it "not reliable enough to trust") | same, in their cloud |
| Consent model | Zoe's (owner-gated, discard unknown, retention window) | Omi's (none built in — a consent *script* blog post, [recording-consent-governance](https://www.omi.me/blogs/workflows/recording-consent-governance)) | same |
| Moving parts we own | one small daemon (bleak + opuslib + Silero VAD) + one column | a Flutter app build pointed at `API_BASE_URL` + a FastAPI backend + 8 cloud services | an HTTPS endpoint on the public internet receiving PCM16 chunks "every N seconds" with `?uid=` ([AudioStreaming](https://docs.omi.me/doc/developer/apps/AudioStreaming.md)) |
| Memory | Zoe's Postgres+Chroma, admission gate, consolidation | Omi's Firestore + Pinecone; a second memory system to reconcile | Omi's, plus a copy pushed at us |
| Verdict | **Recommended.** Smallest, fully local, reuses W6 scaffolding | **No.** "Self-hosted" here means "your GCP project", not "your house"; violates the VISION rule, duplicates memory, and is ~10× the surface | **No, not even as a stop-gap.** It is the exact ambient-audio-in-the-cloud pattern the local-first rule exists to prevent; a stop-gap that trains the habit of leaking is worse than waiting |

Sub-options inside (A) — the receiver:

| Receiver | Cost | BLE | Fit |
|---|---|---|---|
| **The existing Pi 5 touch panel** (`zoe-pi`, runs `zoe_voice_daemon.py`) | **$0** | BT 5.0/BLE on-board | **First.** The daemon already has the Silero-VAD ambient thread, `_api_post`, device-token auth, and the W5 shadow-metrics writer; the Omi bridge is one more input thread. Range is the question (a BLE pendant reaches ~5–10 m indoors through walls; a panel is fixed) |
| Raspberry Pi Zero 2 W ("dock" in the room Jason works in) | ~$15 + PSU/case ([spec](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/): 4×A53, 512 MB, BT 4.2 BLE) | yes | **Second**, only if the panel's placement doesn't cover where the pendant lives. bleak+opuslib+Silero-ONNX fit in <150 MB; no speaker-ID on it (embedding happens on the Orin) |
| The Orin itself | $0 if its M.2 card has BT (open question §8) | maybe | Avoid: keeps BlueZ + another daemon on the box whose RAM is the program's ceiling; the receiver should be dumb and elsewhere |

## 4. Recommendation — record on the pendant, sync when in range (built 2026-10-12, flag-dark)

### 4.1 Shape

```
 Omi CV1 (records to its SD card while powered; talks to the panel Pi, NOT to a phone; no bond)
     │   in BLE range of the panel Pi
     ▼
 omi-sync  (services/omi-sync, panel Pi, own process + unit, OMI_SYNC_ENABLED)
     │  hcitool lecc (long supervision timeout) → bleak → DIS 2A26 → firmware ≥ 3.0.20: ring INFO/READ;
     │  3.0.18 only with OMI_SYNC_LEGACY_FILES=1: LIST_FILES/READ_FILE → spool (SQLite, fsync) → segment → Opus→16 kHz WAV
     │  POST /api/omi/recordings  (X-Device-Token; X-Omi-Key = ring.firstSeq-lastSeq; X-Omi-Sha256)
     ▼
 zoe-data  (omi_recordings.py, ZOE_OMI_SYNC_ENABLED + ZOE_OMI_OWNER_USER_ID)
     │  verify sha256 → fsync WAV → INSERT omi_recordings (source='omi', provenance='recording', unverified)
     │  200 {ok, sha256 echo}  ◄── this answer is the ONLY thing that lets the Pi free the pendant
     ▼
 Pi: delete the spooled records + staged WAV → (ring firmware only) ADVANCE the ring to the confirmed frontier → "I've synced N" notice
 zoe-data (background, off the voice path): Moonshine in ~20 s chunks → transcript on the row → WAV deleted (unless kept)
```

### 4.2 Pi side — `services/omi-sync/` (its own contract: `services/omi-sync/AGENTS.md`)

- **A separate process**, not a thread in `zoe_voice_daemon.py`: a BlueZ fault cannot reach the voice path. Unit template
  `scripts/setup/systemd/omi-sync.service` (user unit, operator opt-in, `Nice=10`, 300 MB cap, swap 0). Flag
  `OMI_SYNC_ENABLED` default off: the process logs once and exits 0.
- **Connection** (`ble_link.connect`): no scan, no pairing, no agent. `sudo -n hcitool lecc --random <addr>` creates the LE
  link with a 32 s supervision timeout (BlueZ's own 420 ms default kills it at this range — 0.3a), bleak attaches to the
  already-connected device, up to 2 attempts per cycle in the run loop (5 for `check`) with the half-open link dropped
  (`hcitool ledc`) between tries; a failed cycle is the normal "not in range" answer. A failed `lecc` is always followed by
  LE Create Connection Cancel (`hcitool cmd 0x08 0x000E`): `hcitool` gives up WITHOUT cancelling, the controller keeps
  initiating, every later `lecc` gets `Input/output error`, and the stale initiation re-creates an owner-less link the instant
  any link drops (2026-10-15: 11 h of failed syncs). An LE link to the pendant that is already up (`hcitool con`) is cleared
  (cancel, then `ledc`) before our own `lecc`: bleak cannot attach to it (it scans first; a connected pendant is silent). Needs privilege for `hcitool`:
  a narrow sudoers line (4.7) or `OMI_SYNC_LECC_PREFIX`. `OMI_SYNC_LECC=0` is the plain bleak connect.
- **Which path** (`engine.Drain.run`, from DIS `2A26`): **≥ 3.0.20 → `ring`** (below); **exactly 3.0.18 → `legacy`, only with
  `OMI_SYNC_LEGACY_FILES=1`** (the firmware frees files itself — 0.3a); anything else is **refused before a single storage
  command is sent** (`0x12` means ADVANCE on one protocol and DELETE_FILE on the other, so an unverified version gets
  GATT reads only). An unknown codec id is refused unless `OMI_SYNC_ASSUME_CODEC` names one.
- **Ring session**: refuse an unknown record size (≠ 444) → `0x10 INFO` → read from `max(ring read pointer, highest seq already
  spooled + 1)` → reassemble 444-byte records from the unaligned DATA stream, **committing each batch of 64 to the spool
  before anything else** → verify READ_BEGIN/DONE agree with what arrived → segment, decode, upload → ADVANCE.
- **Legacy session** (3.0.18, opt-in): `LIST_FILES` → for each file oldest first, `READ_FILE [index][offset of the next
  unspooled 440-byte block]` → every notification must be exactly `[ts][440 bytes]` (else STOP and keep what arrived — a
  misaligned stream is never guessed at) → the block is stored in the spool as a 444-byte record (so segmentation, decoding
  and upload are shared) under the label `f<hex ts>`, block number = seq → on `100` re-`LIST` (the firmware deleted the finished
  file, indices shift) and **seal** the label if the file is gone. The file still being recorded is not deleted by the
  firmware and stays open (its tail is held until quiet); a file is the firmware's own recording unit, so it is split
  only by length, never by a wall-clock gap between sessions. No ADVANCE exists on this path. No delete is ever sent.
- **Time**: all records of a legacy file hang off ONE anchor (the file's timestamp corrected by the clock offset, or the sync
  time minus the file's length when the clock is unusable), and the audio itself supplies the spacing.
- **Writes to the pendant**: exactly one, flag-gated and OFF by default — `OMI_SYNC_SET_DEVICE_TIME=1` writes the host epoch to
  `19b10031` **once at the start of a session** when the clock is more than 2 s off (or unreadable), after the offset was
  measured (so audio already on the card is still corrected by the old offset). Everything else the service writes is the
  list/read/advance/stop commands above.
- **Undecodable audio is held**: if more than 20 % of a recording's frames fail to decode (or none do) it is not uploaded; the raw
  records stay in the spool and `undecodable` shows in the session summary.
- **Custody rule** (ported from Omi's own `PENDANT_RING_CUSTODY.md`): audio is freed from the pendant only after Zoe
  confirms it stored that recording. `engine.advance_target` is a pure function of confirmed state: the end of the
  contiguous run that starts at the pendant's read pointer (so a record never received is never freed), capped at the
  first record still waiting for Zoe. A dropped link, a refused READ, an inconsistent DONE, Zoe down, a rejected
  recording: **none advances anything.** `CMD_RING_CLEAR` has no encoder in the code and is asserted absent on the wire.
- **Stock-firmware caveat (both paths):** stock firmware frees audio on delivery — records as the link layer acknowledges
  them on the ring, a whole file at its last byte on 3.0.18 — so the spool, not the pendant, is the custody there (0.3a).
  The one residual exposure is a crash (or a lost SD card) on the Pi between BlueZ receiving a notification and the batch
  commit (bounded by the 64-record batch) — and, for 3.0.18, the Pi's disk holding the only copy until Zoe confirms. Custody firmware (INFO caps `0x01`, ring_id `0x08`) is
  supported when advertised: ADVANCE becomes the incarnation-scoped `0x15`, and a ring restart is a new
  incarnation. Stock firmware has no incarnation id, so a write pointer that falls *below* a seq already held is
  treated as a new ring (`ring_label`).
- **Idempotent resume:** records are keyed `(device, ring, seq)`; a re-sent record is ignored, a confirmed range is a
  tombstone, and a recording key is `ring.firstSeq-lastSeq` — content-determined. A drop mid-drain followed by a
  resume yields byte-identical recordings to an uninterrupted drain (tested against a control run).
- **Segmentation** (`recordings.segment`, deterministic): a recording ends at a seq hole, a wall-clock gap > `OMI_SYNC_GAP_S`
  (90 s) or `OMI_SYNC_MAX_RECORDING_S` (600 s ≈ 19 MB of WAV). The newest recording stays **open** (not uploaded, not
  freed) until it has been quiet for the gap, or hit the cap; a tail we have not caught up with (a dropped READ) is held
  however old it is, so a recording is never uploaded truncated and split in two.
- **Times:** the record's own epoch is trusted only if plausible; the clock offset (device − host) is read at connect and
  corrected for (`time_quality: device | corrected`); an unset RTC (uptime flag, or a pre-2023 epoch, or a ≥ 12 h offset)
  falls back to `estimated` (receive time minus the audio still queued behind the record). The quality travels with the
  recording. **The owner's pendant is 109+ days behind** (0.3a), so until its clock is set (`OMI_SYNC_SET_DEVICE_TIME`,
  a flag-gated write — 4.2 "Writes to the pendant") everything it already holds will be `estimated`: the right order and
  length, but only "around the time of the sync".
- **Local files:** `~/.zoe-omi-sync/spool.sqlite3` (records, mode 0600, WAL + `synchronous=FULL`) and `outbox/` (staged WAVs,
  deleted on confirmation). The pendant's address never leaves the Pi — uploads carry a one-way hash of it.

### 4.3 Zoe side — `services/zoe-data/omi_recordings.py` + `routers/omi_recordings.py` (migration 0045)

- `POST /api/omi/recordings` — **device-token only** (a session cannot upload). Attributed to `ZOE_OMI_OWNER_USER_ID`
  (a device token authenticates the device, not a person; unset → 503) — or, with the registry on (4.11), to the owner the
  `omi_devices` registry names for `X-Omi-Device`; an unregistered pendant gets 409 `device_not_registered`. Verifies the body's SHA-256, validates a
  16 kHz mono 16-bit WAV, writes the file (tmp + fsync + rename, 0600), inserts the row, and only then answers
  `{"ok": true, "sha256": <echo>}`. Idempotent by `(user, device, key)`; the same key with different bytes is a 409,
  never an overwrite; a key the owner already deleted is acknowledged (tombstone), never resurrected.
- `GET/PATCH/DELETE /api/omi/recordings[/{id}]` — **owner only** (admin role does not widen it). With the registry on (4.11): any signed-in member, own rows only.
- Each row: `source='omi'`, `provenance='recording'`, the **recording time from the pendant timestamps**
  (`started_at`/`ended_at`, `time_quality`), `gap_before`, `speaker_state='unverified'`, `reviewed=0`.
- **Transcription** (background `run_loop`, flag-gated): the existing Moonshine path
  (`routers.voice_tts._transcribe_audio(…, capture=False)` — no panel push, no corpus), ~20 s chunks cut at the
  quietest 100 ms, silent chunks skipped (Moonshine hallucinates on silence). The WAV is **streamed from disk**
  chunk by chunk (`iter_wave_chunks`; a 600 s recording costs the same RAM as a 20 s one). **Defers** — leaving the
  recording pending, never failing it — when the night window is open (the `WINDOW_OPEN` marker or household-local
  02:30–04:30), within 45 s of a voice turn, or under the RAM floor; it yields the Moonshine lock between chunks,
  so a live turn waits at most one chunk. **Two levels** (hysteresis): a NEW recording needs `ZOE_OMI_MIN_MEM_MB`
  of `MemAvailable` and a panel idle for `ZOE_OMI_IDLE_S` (120 s); a job already RUNNING only yields below HALF the
  floor or to a turn in the last 45 s, and a transient closure is **waited out** (up to 5 min, Moonshine not held)
  instead of aborting — aborting threw away every transcribed chunk and restarted from zero, which stalled a 600 s
  backlog while `MemAvailable` floated around the floor. The night window / flag end a job at once.
  **Visible:** `OMI_TRANSCRIBE_DEFERRED reason=low_memory|voice_active|panel_not_idle avail= floor= backlog=` at INFO,
  at most once per reason per 10 min and only when something is waiting; `GET /api/omi/recordings` returns
  `transcription: {backlog, transcribing, deferred, reason, since, mem_available_mb, mem_floor_mb}` (counts and codes,
  no content). **The RAM floor** — measured, see 4.3a. Three attempts, then `failed` (audio kept
  until its retention expiry). No new model, no new RAM tenant. **An empty transcript is not a success** (2026-10-14: 117 of
  129 recordings synced over two days came back with no text and their WAVs were deleted, so nobody could tell a quiet room
  from a deaf pipeline): the row goes `empty_transcript`, the WAV is **kept until its retention expiry** (the owner can still
  drop or delete it), and a WARNING `OMI_EMPTY_TRANSCRIPT` logs its measured seconds / peak / RMS / rate and how many chunks
  reached Moonshine. `GET /api/omi/recordings` returns `empty_transcript_held`. Only a transcript with text deletes the audio.
- The voice path is untouched: no `VOICE_PATH_PATTERNS` file changes, so the replay gate does not front this PR.

### 4.3a The RAM floor — measured 2026-10-15 (default `ZOE_OMI_MIN_MEM_MB=800`)

Live incident: ~86 recordings synced, 34 done in 2 h, then stalled. `MemAvailable` on the 15.6 GB Jetson floats 1.0–1.4 GB at
steady state (llama-server 6.3 GB + Kokoro 2.4 GB + zoe-data 1.6 GB resident; measured 1 Hz over ~15 min: min 1010, p10 1112,
median 1163, max 1384 MB, jitter ±70 MB second to second). With a 1500 (then 1200) floor checked before EVERY chunk, any job
that started was aborted by the next dip and restarted from chunk 0 — caught in the act: a job ran 59 s at 23:42:06 and was
discarded at `MemAvailable` 1194 MB < 1200.

What one transcription costs (zoe-data RSS from `/proc/<pid>/smaps_rollup`, same 1 Hz trace, no swap-out during the job):

| | measured |
|---|---|
| steady RSS / anon before the job | 1605.9 / 1556.3 MB |
| job start transient (whole-file load: `read_bytes` + PCM copy + sample array of a 19.2 MB recording) | peak **+57 MB** RSS |
| job steady state (the held `wav` + `pcm` buffers) | **+40 MB** RSS |
| Moonshine per-chunk inference, on top of that | within noise (anon +-4 MB; the ONNX session is already resident) |
| offline, same recording shape (`tracemalloc`, 600 s WAV, no model): old read-everything | 58.8 MB peak |
| offline: streamed `iter_wave_chunks` | **2.5 MB** peak |
| swap-out during the job | none (voice stack has `MemorySwapMax=0`) |

So the cost was never Moonshine; it was the code holding the whole file, and streaming removes it (~57 MB -> ~2.5 MB; call the
incremental cost <= 10 MB with the chunk WAV, tempfile page cache and ONNX arena). The floor therefore does not pay for the job
— it keeps the voice stack out of trouble when OTHER things are using the box. The voice stack is `MemoryLow`-protected
(llama-server 6 GiB, Kokoro 3 GiB, zoe-data 2 GiB) with `MemorySwapMax=0`, so memory pressure reclaims from, and swaps, the
unprotected tenants (48 GB swap free); the kernel's own cliff is `vm.min_free_kbytes` = 256 MB.

Chosen: start floor **800 MB** (about 200 MB under the idle minimum so the backlog drains at steady state, ~3x the kernel
reserve, ~80x the job's own streamed cost); a running job yields below **400 MB** (half) and waits out shorter dips.
Judgement, not measurement: the 800 is a margin choice; the evidence is that the job costs <= 10 MB and the box never went
below 1010 MB at idle. Revisit if `OMI_TRANSCRIBE_DEFERRED reason=low_memory` appears with `avail` far above 400.

### 4.4 Retention, review, forgetting

- Audio: deleted right after a successful transcription unless `keep_audio` (PATCH, or `ZOE_OMI_KEEP_AUDIO=1`);
  anything that remains expires after `ZOE_OMI_AUDIO_RETENTION_DAYS` (default 7). Transcripts: kept until deleted.
- `DELETE` removes the audio, the transcript and the times; a content-free tombstone remains so a Pi retry is
  acknowledged. `MemoryService.delete_user` tombstones all of a member's recordings; the forget cascade
  (`forget Dana`) redacts the name to `[forgotten]` in transcripts and drops that recording's audio (the audio still
  holds the spoken name); a transcript that lands after a forget is redacted against the ledger first. All of it
  runs through `user_erase_gate`.
- **Kept out of fact extraction until reviewed** — nothing reads the table (static test). "Reviewed → promoted" is a later step.

### 4.5 Telling the owner

After a session that confirmed ≥ 1 recording the Pi calls `POST /api/omi/sync-notice {count}`; zoe-data writes one
`notifications` row (type `omi_sync`) and broadcasts `notification_created` — the existing bell path — "I've synced 3
recordings from your pendant." Rate-limited to one per minute (per owner); best-effort (losing it loses nothing). With the
registry on the body also names the pendant (`device`) and the notice goes to **that pendant's owner only** (4.11).

### 4.6 Flags and configuration

| Where | Name | Default | Meaning |
|---|---|---|---|
| Pi | `OMI_SYNC_ENABLED` | off | the service does nothing, exits 0 |
| Pi | `OMI_PENDANT_ADDRESSES` (comma list) / `OMI_PENDANT_ADDRESS` (`OMI_PENDANT_ADDR_TYPE`) | — (`random`) | the pendants' BLE addresses, visited in turn (never uploaded); the single var is the fallback; the CV1 uses a static random address (4.11) |
| Pi | `OMI_SYNC_UNREGISTERED_RETRY_S` | 900 (floor 60) | how long to wait before asking again after Zoe said the pendant is not assigned to anyone |
| Pi | `ZOE_URL`, `DEVICE_TOKEN`, `VERIFY_SSL` | — | same as the voice daemon |
| Pi | `OMI_SYNC_LECC`, `OMI_SYNC_LECC_PREFIX` | on, `sudo -n` | create the link with `hcitool lecc` (the recipe that works at range); how to run it privileged |
| Pi | `OMI_SYNC_LEGACY_FILES` | **off** | opt in to the firmware-3.0.18 file protocol (the pendant frees each file itself on delivery) |
| Pi | `OMI_SYNC_ASSUME_CODEC` | unset | decode as this codec id when the pendant reports one we do not know (it reports `0x0f`; 21 expected) |
| Pi | `OMI_SYNC_SET_DEVICE_TIME` | **off** | the one WRITE to the pendant: set its clock once per session when it is >2 s off |
| Pi | `OMI_SYNC_GAP_S` / `_MAX_RECORDING_S` / `_SCAN_INTERVAL_S` / `_COOLDOWN_S` / `OMI_SYNC_DIR` | 90 / 600 / 30 / 120 / `~/.zoe-omi-sync` | tuning |
| zoe-data | `ZOE_OMI_SYNC_ENABLED` | off | routes absent (404), no loop |
| zoe-data | `ZOE_OMI_OWNER_USER_ID` | — | whose pendant while the registry is OFF (unset = uploads 503); with `ZOE_OMI_DEVICE_OWNERS` on it is **only the one-time seed** (4.11) |
| zoe-data | `ZOE_OMI_DEVICE_OWNERS` | off | the pendant-ownership registry (`omi_devices`, migration 0047): uploads attributed by pendant, per-member views, admin assignment (4.11) |
| zoe-data | `ZOE_OMI_AUDIT_PATH` | `~/.zoe/omi-audit.jsonl` | audit log of assignments (fail-closed) |
| zoe-data | `ZOE_OMI_AUDIO_DIR`, `_AUDIO_RETENTION_DAYS`, `_KEEP_AUDIO`, `_MIN_MEM_MB` | `~/.zoe/omi-recordings`, 7, off, 800 | storage + gates |
| zoe-data | `ZOE_OMI_IDLE_S` | 120 | a NEW recording is claimed only after this many seconds without a voice turn (a running job yields only to a turn in the last 45 s) |

### 4.7 Operator runbook (the owner present; nothing here is done by the build agent)

1. **Unpair the Omi phone app** (the pendant takes ONE connection at a time; a phone app that auto-connects would hold it).
   In the app remove/forget the pendant (and, if you want the cloud copy gone, delete the Omi account/data); then in the
   phone's Bluetooth settings "Forget" the pendant. Nothing is bonded to the Pi: **do not pair** (the 2026-10-12 bring-up
   read everything with `Paired: no`, and the sync service never pairs). Make sure the pendant is powered on.
2. **Allow the link-creation command.** The service runs `sudo -n hcitool lecc --random <addr>`, `hcitool ledc <handle>` and
   `hcitool cmd 0x08 0x000E` (cancel a pending connect). Either the Pi user already has passwordless sudo (the bring-up used
   it), or add a narrow rule, e.g. in `/etc/sudoers.d/omi-sync`: `<pi-user> ALL=(root) NOPASSWD: /usr/bin/hcitool lecc *,
   /usr/bin/hcitool ledc *, /usr/bin/hcitool cmd 0x08 0x000E`; or grant the
   capabilities instead (`sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which hcitool))`, set
   `OMI_SYNC_LECC_PREFIX=""`) — **the setcap route is untried**. This is an operator change to the Pi, not done by the build.
3. **Install the service** (from a git worktree, not the live checkout): copy `services/omi-sync/omi_sync/` to
   `~/.zoe-omi-sync/app/`, `python3 -m venv ~/.zoe-omi-sync/venv`, `~/.zoe-omi-sync/venv/bin/pip install -r
   services/omi-sync/requirements.txt`, `sudo apt install libopus0`. Create `~/.zoe-omi-sync/.env.omi` (mode 0600):
   `OMI_PENDANT_ADDRESS=E4:A3:57:6C:8D:0D` (the owner's pendant, from the bring-up — use your own if it differs),
   `ZOE_URL=…`, `DEVICE_TOKEN=…` (the voice daemon's), `VERIFY_SSL=0` if self-signed — and leave `OMI_SYNC_ENABLED`
   **unset** for now.
4. **Read-only device check** — safe on the real pendant (GATT reads plus ONE list query: `LIST_FILES` on the verified 3.0.18,
   `INFO` on ring firmware; it cannot READ, ADVANCE, DELETE, CLEAR or write the clock; an unverified firmware gets GATT reads
   only):
   `cd ~/.zoe-omi-sync/app && set -a && . ~/.zoe-omi-sync/.env.omi && set +a && ~/.zoe-omi-sync/venv/bin/python3 -m omi_sync check`
   Expect `found: true`, `model "Omi CV 1"`, `firmware "3.0.18"`, `legacy_file_protocol_verified: true`, `drain_path`
   saying legacy-files is opt-in and BLOCKED on the unknown codec `15`, `storage_status` / `files` (how much is on the card
   and how many files), `device_clock.offset_s` (about −109 days ⇒ untrusted). Paste that JSON in the PR — it closes
   items of the UNVERIFIED list in 0.3a. `python3 -m omi_sync status` (no Bluetooth) shows what the config resolved to.
   **Decision point** — pick ONE: (a) update the firmware first (4.10) and use the ring path; or (b) accept the legacy path:
   its custody is the Pi's spool only, so add to `.env.omi` `OMI_SYNC_LEGACY_FILES=1`, and `OMI_SYNC_ASSUME_CODEC=21` once you
   have judged (4.10, "the codec question") that the audio decodes; optionally `OMI_SYNC_SET_DEVICE_TIME=1` (a write to the
   pendant's clock — fixes timestamps of everything recorded after it).
5. **Zoe side:** in `services/zoe-data/.env` set `ZOE_OMI_SYNC_ENABLED=1` and `ZOE_OMI_OWNER_USER_ID=<his user id>`;
   apply migration 0045 (the normal deploy path); restart zoe-data outside the 02:30–04:30 window and poll `/health`.
   Check the route exists but is gated: `curl -s -o /dev/null -w '%{http_code}' -X POST $ZOE_URL/api/omi/recordings` → `401`
   (flag off would be `404`).
6. **Enable and watch the first drain:** set `OMI_SYNC_ENABLED=1` in `.env.omi`, install the unit
   (`cp scripts/setup/systemd/omi-sync.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user
   enable --now omi-sync`), `journalctl --user -u omi-sync -f`. Expect one `sync:` line per visit
   (`path=legacy|ring uploaded=N undecodable=0 … advanced_to=…`; `advanced_to` is ring-only). A `refused=` or `undecodable=`
   is the service declining on purpose — read its `notes`. **First drain with the owner present and the pendant a few metres
   from the Pi** (marginal link). Then `GET /api/omi/recordings` as him shows the recordings; transcripts follow within a
   minute or two of the next transcription pass (not in the night window).
7. **Off / rollback:** `systemctl --user disable --now omi-sync`; unset `ZOE_OMI_SYNC_ENABLED` and restart zoe-data (the
   routes vanish; rows stay). Do **not** delete `~/.zoe-omi-sync/spool.sqlite3` casually: it can hold audio the pendant
   has already freed.

### 4.8 Tests (all `ci_safe`, no hardware)

`tests/unit/test_omi_sync_protocol.py` (ring parser on fixture bytes, firmware gate, time resolution, segmentation),
`tests/unit/test_omi_sync_engine.py` (the custody rule against a faithful fake pendant: no ADVANCE without a confirmed
upload; advance stops at the first unconfirmed recording; drop-and-resume equals an uninterrupted run, key for key and
byte for byte; stock firmware that frees records itself loses nothing; lost ADVANCE ack; ring-id and legacy commands;
never CLEAR; **the 3.0.18 legacy-file path** against a fake pendant built from the firmware source — refused by default,
unknown codec refused until assumed, never a delete/ADVANCE byte on the wire, the spool is the custody when Zoe is down,
drop-and-resume at the right block equals a clean run, the file being recorded stays open, a stale pendant clock gives
`estimated` times, misaligned blocks stop the read, undecodable audio is held; the clock is written only with the flag and only
at the start), `tests/unit/test_omi_sync_check.py` (the read-only check touches nothing, per firmware; the connection
recipe's argv; no pairing in the module; flag-dark entry point),
`services/zoe-data/tests/test_omi_recordings.py` (auth, owner-only, idempotency, tombstone, the digest-echo
confirmation checked against the Pi's own classifier, transcription gates, retention, forgetting, the static "nothing
else reads it" guard). Break-the-fix: in-test `test_control_*` monkeypatch controls plus the source mutations listed in
each file's docstring.

### 4.9 Not built here (phase 2 and later)

- **Phase 2 — press-to-record firmware.** A button toggle that gates the mic and the ring writes (haptic acknowledgement,
  LED), built from Omi's open firmware (nRF Connect SDK; UF2 / BLE-OTA flashable; the stock UF2 kept to hand). It makes
  "recording" a deliberate act, restores a real consent posture (section 5), and is the right moment to adopt the
  custody-capable firmware (`CAP_APP_ACK_RECLAIM`, `ring_id`) so the pendant never frees on its own. Needs the pendant's
  hardware facts from 0.3 first.
  **Researched 2026-10-13:** [`docs/research/omi-press-to-record-firmware-2026-10.md`](../research/omi-press-to-record-firmware-2026-10.md)
  — the bootloader trusts MCUboot's public default RSA key (committed with its private half), so an OTA image is signable; the
  change is a small gate in front of the codec plus a button toggle; the cost is that a bad image can only be recovered over
  SWD (get a probe first); the public key is also a security hole in the stock device.
- **Speaker gate on recordings** (the section-5 machinery applied to a recording instead of a live segment) and a
  **reviewed → promoted** path into the memory admission gate. Until then the table is read by nothing.
  **Designed 2026-10-14:** [`memories-space.md`](memories-space.md) — per-line You / Someone-else tagging at
  transcription time (ResNet34 + a pendant enrolment), the desktop/touch Pendant section, and the **day review**
  (candidates with exact evidence spans, from the owner's own speech, mentioned on the next turn, written only on
  approval). Docs only; nothing built.
- **Live-stream / ambient (sections 4a-6)** and push-to-talk (B9.7).
- A UI for the recordings (today: API only) — designed in [`memories-space.md`](memories-space.md) §2.4 (desktop Pendant tab first, P2).

### 4.10 Firmware update (NOT done by this PR — an owner decision) and the codec question

**Why update:** the pendant runs 3.0.18; the ring protocol (incremental, resumable, `ADVANCE`-aware, and the base of the
custody contract) needs ≥ 3.0.20, and the source's `CODEC_ID` is 21 where this unit reports `0x0f`. Latest official:
**3.0.21** (`Omi_CV1_v3.0.21`, published 2026-08-05, changelog "fix audio sync issue").

**The device offers it:** the MCUmgr **SMP** service `8d53dc1d-1db7-4cd3-868b-8a527460aa84` (characteristic
`da2e7828-fbce-4e01-ae9e-261174997c48`, write-without-response + notify) is exposed (the firmware config enables it:
`CONFIG_NCS_SAMPLE_MCUMGR_BT_OTA_DFU=y`). The phone app's update is exactly this: `GET https://api.omi.me/v2/firmware/latest?device_model=…&
firmware_revision=…&hardware_revision=…&manufacturer_name=…` returns `{"version":"3.0.21","zip_url":"https://github.com/BasedHardware/omi/
releases/download/Omi_CV1_v3.0.21/Omi_CV1_OTA_v3.0.21.zip","ota_update_steps":["battery","internet"],…}`, the app downloads the
zip, and uploads its images over SMP (`mcumgr_flutter`). The release assets (GitHub API, 2026-10-12):
`Omi_CV1_OTA_v3.0.21.zip` 424 966 bytes, `sha256:9fb93885e4d31b0836ed1d1047c9fde32b8fb477f94cd9d222246254492d3674` (plus the
two `.hex` full images, which are NOT the OTA artefact).

**Steps from the Pi (written down, none executed):**
1. Fetch the zip from the GitHub release URL directly (not from Omi's backend — that call tells Omi the model, firmware and
   hardware revision, which this design otherwise never does; to learn the URL without it, read the release page) and
   **verify the sha256 above**. Unzip: `manifest.json` names the image `.bin`s (an application image and, if present, a network-core
   image) built and signed with Omi's MCUboot key; the pendant's bootloader verifies the signature, so an unsigned/foreign image
   is refused at boot and the old one kept.
2. Pendant at >50 % battery (it is 93 %), on its charger if possible, **within ~2 m of the Pi** (the OTA moves ~425 KB over a link
   that is marginal at the panel's distance; retries are built into SMP but a stall mid-upload is the likeliest failure),
   unpaired from the phone app, nothing else connected.
3. Use an SMP client over BLE: Intercreate's `smpmgr` (Python, bleak-based; the PyPI package exists, its source is not in the
   opensrc cache) or Zephyr/Nordic `mcumgr` with the BLE transport. **The command lines are not given here on purpose — they
   were not run and their syntax was not verified;** read each tool's `--help`/docs first. Both need the same long-
   supervision-timeout link as `ble_link.connect` (create it with `hcitool lecc` first); the SMP characteristic is
   write-without-response + notify.
4. `image list` → `image test <hash>` (swap on next boot, **revert if not confirmed**) → `reset` → reconnect → check
   DIS firmware reads 3.0.21 and `python -m omi_sync check` is healthy → `image confirm`. If it does not come back or the check
   fails, there is **no revert**: this MCUboot is overwrite-only, so "test" and "confirm" are the same thing and a power-cycle does
   not bring the old image back (correction 2026-10-13; see `docs/research/omi-press-to-record-firmware-2026-10.md` 5.1).
**Risks (general MCUboot/OTA behaviour — none of this was exercised on this unit):** an upload that dies midway only touches
the secondary slot; a power loss during the swap is the one risky instant (hence battery + charger); the nRF5340 has a
network core, and a mismatch between application and network images is a way to brick it, so use ONLY the official OTA zip;
the **update changes the storage protocol** (the SD files already on the card follow whatever the new firmware does with
them — **drain the card with the legacy path first if that audio matters**); the clock and settings may reset; and the stock
3.0.18 OTA zip **is** published (`Omi_CV1_v3.0.18` / `Omi_CV1_OTA_v3.0.18.zip`, 430 318 bytes; correction 2026-10-13) and
equal-version images are accepted by the downgrade check, so going back over BLE is possible in principle (untested). The app's
developer flash page is the alternative, but it brings the phone app back into the loop.

**The codec question.** Before `OMI_SYNC_ASSUME_CODEC=21`: the cheapest check is the live stream — wear the pendant, speak,
and subscribe to `19b10001` for a few seconds with `omi_bringup_2026-10-12_probe.py` (it printed 0 packets in a quiet room; the
hardware VAD needs sound), then decode the first payloads with libopus: `opus_packet_get_samples_per_frame` tells the true
frame length (160 = 10 ms, 320 = 20 ms) regardless of the id, and a clean decode of 16 kHz speech confirms Opus. The sync service
does the same implicitly (durations come from the decoded PCM, a recording that does not decode is held, not uploaded). A
firmware update to 3.0.21 should make the id read 21.

### 4.11 Several pendants, one owner each (2026-10-13, flag-dark `ZOE_OMI_DEVICE_OWNERS`)

**Owner requirement (verbatim):** "we will need to link a certain omi, to a certain user, but the sync should sync no matter who is logged on, as long as in range of the pi, and of course only sync to the user its registered for".

- **One identity per pendant, derived on the Pi.** `device_hash(address)` = `sha256("omi-sync:" + address.lower())[:16]` (hex): what omi-sync sends as `X-Omi-Device` and keys its spool by. Settings > Bluetooth (`panel_bt.model.device_id`) previously used a *different* 12-hex hash; it now uses this exact derivation, so the id an admin sees on a pendant's row IS the upload id. Chosen over changing omi-sync because omi-sync is live (existing stored recordings and the Pi spool carry this id) while Settings > Bluetooth was not deployed — zero data migration. Two copies (two venvs) pinned equal, with a golden value, by `tests/unit/test_omi_device_identity.py`. The address never leaves the Pi.
- **Registry:** table `omi_devices` (migration 0047): `device_id` PK, `user_id` (current owner, `''` when unassigned), `label`, `registered_by`, `registered_at`, `updated_at`, `active`. Chosen over a general `ble_device_owners`: ownership only matters to attributing recordings, which is Omi-specific; generalise when a second owned device exists.
- **Attribution:** ONLY the registry, by `X-Omi-Device`, re-checked inside the owner's erase gate at write time. Never the panel's signed-in/bound user, never `ZOE_OMI_OWNER_USER_ID`, never "some admin". Unregistered/unassigned/inactive → 409 `device_not_registered` → omi-sync `NOT_REGISTERED`: keep the spool, do not advance/free the ring, retry every `OMI_SYNC_UNREGISTERED_RETRY_S` (900 s).
- **Viewing:** any signed-in non-guest human member sees/edits/deletes only their own rows; no admin override; a device token (even one bound to a member) is refused.
- **Registration:** `GET /api/omi/members|devices`, `PUT|DELETE /api/omi/devices/{device_id}` — human admin only (device tokens refused, the #2010 pattern), audited fail-closed. **Reassigning moves no existing recording**; ownership is resolved at upload time, so audio still on the pendant or in the Pi spool at the moment of change goes to the new owner (the UI says so). The admin sees ownership only — never recordings or counts. UI: the Omi driver's row in Settings > Bluetooth devices has an **Assigned to** select.
- **Seed:** `ZOE_OMI_OWNER_USER_ID` is now a one-time seed — if set and exactly one pendant has ever uploaded under that owner, that pendant is registered to them once (idempotent; any existing row blocks a re-seed, so an admin's unassign/reassign or an erase is never undone by a restart). Otherwise registration is explicit.
- **Several pendants on one Pi:** `OMI_PENDANT_ADDRESSES` (comma list; the single var is the fallback). `omi_sync/runner.py` visits each in turn; the shared radio lock is held per visit; spool, outbox and next-visit time are per pendant.
- **Forget:** `MemoryService.delete_user` → `omi_recordings.forget_user` tombstones the member's recordings and unassigns their pendants (the row stays, unowned, so the seed cannot resurrect them), scrubbing them from `registered_by`.
- Operator steps and rollback: [`docs/knowledge/bluetooth-settings.md`](../knowledge/bluetooth-settings.md) → "Operator steps for pendant ownership". Tests: `services/zoe-data/tests/test_omi_device_owners.py`, `tests/unit/test_omi_sync_rotation.py`, `tests/unit/test_omi_device_identity.py`, `services/zoe-ui/dist/test_bluetooth_settings.js`.

## 4a. The live-stream architecture (the later path — superseded as the first build by section 4)

```
 Omi CV1 ──BLE (Opus 16k/32kbps, 20 ms)──► omi-bridge (on the Pi panel; later a Pi Zero 2 W dock)
                                              │  bleak notify → strip 3-byte header → opuslib decode → 16 kHz PCM
                                              │  Silero VAD segmenter (same knobs as AMBIENT_*), button/battery events
                                              ▼
                        POST /api/voice/ambient  {audio_base64 (WAV 16k), panel_id, room, duration_seconds,
                                                  source:"omi", device_id, capture_session_id}
                                              │   (existing endpoint + device-token auth; source="omi" takes a
                                              ▼    NEW handler — the owner-by-panel insert is never used)
                     speaker gate BEFORE STT (NEW, W6 step 1+2):
                        │ sherpa-onnx pyannote-segmentation-3.0 → mixed/overlap?  → DISCARD
                        │ sub-window embeddings + B4.1 margin rule vs consented profiles
                        │ unknown / below margin                                 → DISCARD (no row, no audio,
                        │                                                           no text; numeric count only)
                        │ owner, or enrolled+consented-for-ambient other
                        ▼
                     Moonshine transcribe (only now; only when ZOE_AMBIENT_OMI_ATTRIBUTE=1)
                        → INSERT ambient_memory(user_id=<attributed>, speaker_id, scope='personal',
                                                source='omi', device_id, expires_at); raw audio discarded
                                              │
                                              ▼
                    existing admission gate (scope carried through) → idle consolidation → recall cited [ambient:omi]
```

Why this shape:

- **Every box already exists except one.** `/api/voice/ambient` (`services/zoe-data/routers/voice_tts.py`)
  already does base64-WAV → Moonshine → `ambient_memory` and already refuses to store
  ownerless rows (P-F4). `ambient_memory` already has FTS (migration 0002), user scope
  (0017) and a `speaker_id` index that the insert path does not populate — which is
  W6 step 1 verbatim. `/api/voice/identify` already scores an embedding against
  *consenting* profiles only. The Pi daemon already segments room audio with Silero and
  posts it. The new code is: a BLE input thread; a `source="omi"` handler that runs the
  gate *before* STT (the existing handler transcribes first and inserts under the panel
  owner, which is the wrong order for a pendant); one migration adding `scope`,
  `source`, `device_id`, `speaker_id`, `expires_at`; and the gate itself (segmentation
  + sub-window embeddings + margin rule).
- **The pendant is a near-field mic on the owner.** A chest-mounted mic makes the wearer
  loud and everyone else far — which is exactly the asymmetry an owner-only attribution
  gate wants (high owner recall, easy rejection of far-field voices). The panel mic has
  the opposite problem.
- **Dumb receiver, smart Orin.** No model on the receiver beyond Silero; STT and
  speaker-ID stay where the profiles and the policy live. That also keeps the receiver
  swappable (panel today, a $15 dock tomorrow).
- **No new cloud, no new memory store, no new identity stack.** The rocks are untouched.

**Budget estimates (to be measured in P0/P1, not asserted):**

| Where | Cost |
|---|---|
| BLE link | ~4 KB/s per pendant; 50 notifications/s; one bonded central |
| Receiver (Pi) | +60–120 MB RSS for bleak + opuslib + the segmenter (Silero is already resident in the daemon); <5 % of one core |
| Orin, resident | Two small ONNX models: `pyannote-segmentation-3.0` (~6 MB) + the sherpa-onnx speaker embedder (~30 MB), both CPU — tens of MB, not a new GPU tenant. Moonshine is already loaded |
| Orin, per segment | Segmentation ~0.1–0.2 s + embedding ~0.1–0.3 s CPU (before STT; every segment); Moonshine ~0.3–0.6 s for a 3–8 s segment (warm; only attributed segments). Caveat: the server-side resemblyzer path pulls **CPU torch** on first use if torch is not already resident (~0.3–0.5 GB) — that is the B0.7 note "CPU torch for Resemblyzer". Prefer the sherpa-onnx embedder (B4.1) for the gate; it is ~30 MB and needs no torch |
| Latency (not user-facing) | segment end → row committed ≈ 1–2 s |
| Power | pendant 10–14 h; AAD sleeps the mic in silence, so a quiet day lasts longer |

**What to buy: nothing to start.** Optional, only if P0 shows the panel's BLE placement
doesn't cover Jason's day: one Raspberry Pi Zero 2 W + PSU + case (~AU$40). A USB BLE
5.x dongle for the Orin is the *wrong* purchase (see receiver table). The Omi wireless
charger is a convenience, not a dependency.

## 5. Identity and consent — how Omi does it, how Zoe will

**Omi today.** Speaker identification is a per-user "speech profile": enrolment audio →
`pyannote/wespeaker-voxceleb-resnet34-LM` embedding, live matching by cosine ≥ 0.45 on
≤10 s clips, with a *separate* SpeechBrain ECAPA path for post-processing — two
incompatible embedding spaces, a raw cutoff, ~77–81 % of users skipping enrolment; the
maintainers' own fix proposal is one unified stack (ERes2NetV2 / CAM++), a centroid over
six answers, and AS-Norm / relative ranking instead of a raw threshold
([#12765](https://github.com/BasedHardware/omi/issues/12765)). Live transcripts carry an
`is_user` flag; other people are assigned by hand
(`PATCH /v1/conversations/{id}/segments/assign-bulk`, needing ten seconds of retained
speech). The privacy policy says raw audio is not retained after processing but it
*does* collect "Person Information: … names and voice samples" of people you identify,
and says nothing about bystanders. There is **no consent mode**; the guidance is a
verbal script ("I use Omi to take notes… are you ok with that?") and "pause and take
manual notes" if anyone objects. Their macOS desktop app is the best-designed piece:
on-device diarization (FluidAudio pyannote segmentation + WeSpeaker), cosine clustering
at 0.60, running-mean centroids, "This is me" enrolment, LFU-aged remembered voices
capped at 30 ([#13055](https://github.com/BasedHardware/omi/pull/13055)).

**Mapping onto Zoe (already the W6 design; the pendant sharpens it):**

1. **Attribution = the W5 speaker path with the B4.1 margin rule**, not a raw cutoff:
   distance < θ *and* ≥ 0.10 better than the second-best consented profile, else
   *unknown*. Omi's failure history is the argument for the margin rule and for one
   embedder end-to-end (enrol, gate, post-process all through the same model).
2. **Owner-only by default.** A pendant belongs to a person. Rows from it are stored only
   when the segment matches *that* person's consented profile. Other enrolled household
   members are attributed only if they have additionally opted into *ambient* (a new
   `ambient_consent_at` beside `consent_at`; revocable like speaker consent).
3. **Unknown voices are discarded, never stored unattributed.** Not audio, not text.
   A counter (`discarded_unknown`) is the only trace, for the shadow-week review.
4. **Multi-speaker segments are detected with a real detector, then discarded.** A
   per-utterance identity score cannot do this: a loud owner plus a quieter guest in one
   clip still matches the owner's embedding, and Moonshine would then transcribe both.
   So before any text exists, every segment passes two checks on the Orin:
   (a) **speaker segmentation** with the sherpa-onnx port of
   `pyannote-segmentation-3.0` ([sherpa-onnx speaker diarization](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html),
   [model release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models)) —
   a ~6 MB ONNX model whose powerset output labels overlap explicitly; a segment is
   *mixed* if more than one speaker is active or overlap frames exceed a small budget;
   (b) **embedding consistency across sub-windows** — the segment is cut into ~1.5 s
   windows, each embedded and scored with the margin rule **against the whole eligible
   set** (every profile with `consent_at` *and* `ambient_consent_at`); every window must
   resolve to the **same** eligible speaker, otherwise the segment is *mixed*. The rule
   is "one consistent eligible speaker", not "the owner": a household member who opted
   into ambient and speaks alone near the pendant yields `other-consented` and is
   attributed to *them*; only an unenrolled or non-opted-in voice, or a mix, is
   discarded. Capture is also **narrowed**: the segmenter caps
   segments at ≤8 s and stops on any speech pause ≥0.8 s, so a guest's reply tends to
   land in its own segment rather than inside the owner's. Mixed ⇒ discard before STT.
   Full who-spoke-when diarization stays the later upgrade (plan §8.5); v1 only needs
   the *one consistent eligible speaker — and which one* decision. Costs are estimates
   until P2 measures them (§6.1).
5. **The wearer controls capture with the button, with haptic acknowledgement**: long
   press toggles capture (haptic double-pulse on, single on off); the bridge only posts
   while the session is on; LED state mirrors it. Single tap is reserved for
   push-to-talk to Zoe (§8, Q7). This is Limitless's "Consent Mode" made physical.
6. **Retention window on ambient rows** (`expires_at`, default short — proposal 7 days
   — configurable per person, 1 day → forever as Limitless does), purged by the idle
   consolidator; promotion into `facts` goes only through the existing admission gate.
6b. **Every row carries a scope, and the scope is `personal`.** The design charter
   ([`ZOE_DESIGN_PRINCIPLES.md`](../governance/ZOE_DESIGN_PRINCIPLES.md) §3) names three
   scopes — personal, shared, ambient — and requires every memory write to carry one.
   A pendant transcript is **not** charter-"ambient" (that scope is open-by-design
   environment observation: sensors, room state); it is the wearer's own words, i.e.
   **personal to the attributed user**, and `source='omi'` must never be read as scope.
   Propagation contract: rows are written `scope='personal'`, `user_id=<attributed>`;
   recall filters on scope + user exactly like the existing per-user visibility;
   promotion through the admission gate carries `personal` through unchanged; the only
   way a pendant-derived fact becomes `shared` is an explicit user action on the memory
   page (B3.4). No unscoped write can exist because the column is `NOT NULL`.
7. **Provenance is visible**: recall cites `[ambient:omi]`, and the B3.4 memory page
   lists ambient rows separately with delete.

**WA Surveillance Devices Act 1998** (household is in Western Australia). s5 makes it
an offence to use a listening device to record a *private conversation*; the exception
for a *party* to the conversation requires the **consent, express or implied, of all
principal parties** (implied consent needs awareness *and* the knowledge one may
object); s9 separately prohibits publishing such a record; individuals face up to
$5,000 / 12 months ([Lavan](https://www.lavan.com.au/publications/lights-camera-illegal-evidence-should-you-be-recording-a-private-conversation/),
[Andrew Williams](https://www.andrewwilliamslawyer.com.au/can-you-record-someone-without-permission.html),
[TechSafety WA guide](https://techsafety.org.au/blog/legal_articles/legal-guide-to-surveillance-legislation-in-wa/),
[Act text](https://www.legislation.wa.gov.au/legislation/statutes.nsf/law_a1919.html)).
Two consequences for the design, neither of which is legal advice:

- The **discard-unknown rule is load-bearing legally, not only ethically** (as the
  samantha plan already predicted). Design so that a guest's words are never *retained*;
  whether transient in-RAM classification counts as "recording" is the question to put
  to the legal sanity check (§8, Q5).
- **The stock firmware itself is a listening device that records continuously to SD,
  independent of Zoe.** `CONFIG_OMI_ENABLE_OFFLINE_STORAGE=y` writes a ring buffer
  whenever the pendant is on, connected or not. That is a retention Zoe cannot gate from
  the outside, and **clearing the ring on connect is not a no-retention posture**: a
  `RING_CLEAR` deletes what came before and the ring resumes retaining the next guest's
  words immediately, and it stays on the device until the next connection if the pendant
  walks away. So the **`omi-zoe` firmware variant with offline storage OFF is mandatory
  for the no-trace guarantee** (one Kconfig line, `CONFIG_OMI_ENABLE_OFFLINE_STORAGE=n`,
  in an open, UF2/OTA-flashable firmware; `transport.c` registers the storage GATT
  service only under that `#ifdef`, so the variant also removes the service — read in
  source, not yet flashed and verified). Until the variant is flashed the pendant is
  **lab-only: worn by Jason alone, never in company.** `RING_CLEAR` is at most a lab
  hygiene step during P0, never a consent posture. This is **B9.0**, and it gates P3.

## 6. Phases and gates (lab first, flag-dark)

> **2026-10-12:** these phases (P0–P5) are the **live-stream / ambient** path. The first thing built is the
> sync-in-range path of section 4 (its own phase 1, flag-dark, gates in 4.8; phase 2 = press-to-record firmware,
> 4.9). P0's lab measurements (packet loss, WER on the live stream) are not a prerequisite for the sync service —
> the ring is read in bulk, not streamed — but the hardware numbers in 0.3 are.

Every phase is flag-dark until its gate passes; every flag defaults off and lands in the
flag inventory via the pre-commit hook. **Every phase from P1 on is replay-gated
(MANDATORY, per `AGENTS.md`)**: P1 edits `zoe_voice_daemon.py` and `/api/voice/ambient`,
P2–P4 add model work and recall behaviour inside `voice_tts.py`, and all of it shares
CPU, RAM and routing with ordinary panel turns. Each phase's PR runs
`scripts/maintenance/voice_regression_probe.py` (baseline-compared) plus
`scripts/perf/measure_voice.py` under `flock /tmp/zoe-voice-harness.lock`, with the new
code path *enabled* in the lab configuration, and said-vs-did and per-stage speed must
not regress. P0 alone is exempt because it runs on a laptop or the Pi with no Zoe code
changed. The optional B9.7 (push-to-talk into `/api/voice/turn`) is voice path by
definition and is replay-gated the same way.

Two invariants hold across all phases and are tested, not assumed:

- **No durable text before attribution is enabled.** Today's `/api/voice/ambient`
  resolves the panel's default user and inserts *every* transcript under that user
  (`voice_tts.py` ~5150–5169). For `source="omi"` that path is **replaced**, not reused:
  while `ZOE_AMBIENT_OMI_ATTRIBUTE` is off (P1–P3) the endpoint runs the speaker gate
  only — embedding, segmentation, verdict, duration — and **never calls Moonshine**, so
  no transcript string exists even in memory, and it writes only numeric JSONL metrics
  (verdict class, scores, margin, window count, duration; no audio, no text).
- **No unscoped row — and no broken panel capture.** `ambient_memory.scope` is
  `NOT NULL` with `personal` as the only value the pendant path writes (§5 item 6b).
  Making the column required also binds the **existing panel writer** (the insert at
  `voice_tts.py` ~5166, which today supplies no scope): P1 updates that INSERT to write
  `scope='personal'` in the same PR as the migration, the migration carries a
  `server_default='personal'` plus a backfill so it is safe to apply before or after
  the code lands, and the P1 gate includes a panel-capture regression test (a plain
  `source='ambient'` POST from a panel still stores a row, with `scope='personal'`).

| Phase | Build | Gate (measured, reproducible) |
|---|---|---|
| **P0 — Lab receive** (a laptop or the Pi panel, *never* the Orin) | `labs/omi-receiver/omi_bridge.py` (bleak + opuslib, no SDK dependency beyond those two): scan, connect, read DIS + codec id, subscribe audio, decode to WAV, log button/battery, reconnect on drop. Dump 10 min of audio | ≥10 min continuous at 3 m and through one wall; packet-number gaps < 1 %; decoded WAV plays; **Moonshine WER on 20 replay-corpus sentences read while wearing the pendant ≤ panel-mic WER + 5 pts**; battery drop/hour recorded |
| **P1 — Bridge into the daemon, flag-dark** | Bridge thread in `zoe_voice_daemon.py` behind `OMI_BRIDGE_ENABLED` (Pi env) feeding the existing ambient segmenter (≤8 s segments, ≥0.8 s pause); posts carry `source="omi"`, `device_id`, `capture_session_id`. Server: one Alembic migration adds to `ambient_memory` — `scope` (`NOT NULL`, charter values `personal`/`shared`/`ambient`, **`server_default='personal'` + backfill of existing rows** so the migration is safe on its own), `source` values, `device_id`, `speaker_id` write, `expires_at`; **the existing panel INSERT (`voice_tts.py` ~5166) is updated in the same PR to write `scope='personal'` explicitly**; the `source="omi"` branch of `/ambient` is a new handler that does **not** call the owner-by-panel insert; flag `ZOE_AMBIENT_OMI_ENABLED` (default off) → 404 for `source=omi` | `ci_safe` unit tests: flag off ⇒ no row, ever; `source=omi` with attribution off ⇒ Moonshine never invoked (mock asserts zero calls) and zero rows; **panel-capture regression: a plain panel `source='ambient'` POST still stores a row and it carries `scope='personal'`**; an explicit write with `scope=NULL` is rejected; migration up/down clean and applies cleanly on a copy of the live table; **replay gate PASS with the bridge thread running and the flag on in the lab config**; Pi RSS delta ≤ 120 MB |
| **P2 — Speaker gate, shadow (metrics only, no text)** | On the Orin, per segment: sherpa-onnx `pyannote-segmentation-3.0` (mixed/overlap detector) + sub-window embedding consistency (every window resolves, by the B4.1 margin rule against the whole eligible set, to the **same** consented + ambient-opted-in speaker; sherpa-onnx embedder, resemblyzer fallback). Output is a **numeric JSONL row only** (verdict ∈ {owner, other-consented, unknown, mixed}, scores, margin, windows, duration) appended to the W5 shadow file. **No Moonshine call, no transcript, no `ambient_memory` row** in this phase | One shadow week; owner FA/FR on the pendant; **negative control A** — a second household voice alone for 10 min ⇒ zero `owner` verdicts; **negative control B (mixed)** — a labelled 10-min session with the owner and a second voice talking to each other ⇒ zero `owner` verdicts on any segment the label marks as containing the second voice, and the `mixed` rate reported; **positive control C (reachability)** — an enrolled household member with `ambient_consent_at` set speaks alone for 10 min ⇒ verdicts are `other-consented` (attributed to them), not `mixed`/`unknown`, at ≥ the owner's own recall; the same recording with their `ambient_consent_at` revoked ⇒ zero `other-consented` (all `unknown`); a grep of the shadow file and the zoe-data log for any transcript-like text finds none; per-segment CPU time and RSS delta for the segmentation model recorded (§6.1); **replay gate PASS** |
| **P3 — Consent posture** | Button long-press session toggle + haptic + LED; `ambient_consent_at` per profile; retention purge job; **`omi-zoe` firmware (offline storage OFF) built, flashed and verified** per B9.0 — a hard precondition of this phase | Stranger test end-to-end: guest speaks near the wearer for 5 min → **no `ambient_memory` row, no temp file, no log line containing text, and the device ring holds nothing** (GATT discovery shows no storage service `30295780-…` on the variant; on any pendant still running stock firmware the test fails by definition); toggling off stops posts within 1 s; retention purge verified with a clock-shifted row; **replay gate PASS** |
| **P4 — Attributed storage + promotion** | Flip the gate from shadow to act (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`): only `owner` / `other-consented` verdicts proceed to Moonshine; rows carry `speaker_id`, `scope='personal'`, `expires_at`; promotion through the admission gate keeps scope; `[ambient:omi]` citation; rows visible/deletable on the memory page (B3.4) | `memory_recall_probe` unchanged on the existing corpus; a 20-item "said near, not to, Zoe" recall mini-set ≥ 80 % attributed recall; a cross-user recall test proves user B never sees user A's pendant rows; consolidation digest shows ambient-sourced candidates with provenance; **replay gate PASS** |
| **P5 — Optional: offline drain (not scheduled)** | Only on a *further* firmware variant whose ring is gated by the wearer's session toggle — never on stock: on reconnect, time-sync, `RING_INFO`/`RING_READ`, decode 444-byte records, feed the same gate; keep the pendant still until the checkpoint catches up | Drain of a 30-min away-session reproduces the live path's row count ±1; interrupted drain loses nothing (advance only on confirmed bytes) |

Sequencing: **B0.1 (RAM floor) → P0 → P1**; **B4.1 (margin rule) + B4.3 (ID decision) →
P2**; P3 needs the `omi-zoe` flash (B9.0) and the legal check; P4 feeds **B3.9** (speaker-
cluster-gated owner attribution) and gives **B2.1** presence a second signal.

### 6.1 Unverified — what this plan asserts from reading, not from measuring

Named so nobody reads a measurement into a guess. Each is closed by the phase listed.

- BLE range/packet loss from the panel's position through walls — **P0**.
- Receiver RSS (+60–120 MB) and Orin per-segment costs (Moonshine 0.3–0.6 s, embedding
  0.1–0.3 s, segmentation model ~0.1–0.2 s on CPU) — **P1/P2**. The segmentation model
  has not been run on the Orin at all.
- Whether CPU torch is already resident for the resemblyzer path (+0.3–0.5 GB if not) —
  **P2**; the plan prefers the torch-free sherpa-onnx embedder for exactly this reason.
- That `CONFIG_OMI_ENABLE_OFFLINE_STORAGE=n` removes the storage GATT service — read in
  `transport.c`, **not flashed and verified**; also whether the CV1 accepts a custom
  UF2/mcumgr image without a signing step — **B9.0**.
- Whether the sherpa-onnx segmentation + sub-window consistency checks catch a quiet
  guest under a loud owner at the rates the P2 gate demands — **P2 negative control B**.
- Whether the "same eligible speaker per window" rule keeps an opted-in household
  member's recall near the owner's on a chest-mounted mic that is *not* on them
  (far-field for everyone but the wearer) — **P2 positive control C**; today no second
  household profile is enrolled, so that control needs a consenting volunteer first.
- Whether the live `ambient_memory` table holds rows the backfill matters for (the panel
  path is flag-OFF, so it may be empty) — checked when the P1 migration is applied to a
  copy of the live table.
- Whether the Orin's M.2 card exposes Bluetooth — irrelevant to the recommended path.
- How the existing memory tables express scope today (no `scope` column was found on
  `ambient_memory`; migration 0017 added `user_id` only) — the P1 migration designs the
  column against whatever the `memories`/`facts` tables use, confirmed in P1.

## 7. Program block — B9 (registered as B9 in the program tracker, PR #1680)

> **2026-10-12:** the tracker's B9 entry now leads with **B9.1b/B9.2b, the sync-in-range service** (section 4); the
> block below is the earlier live-stream decomposition and stays as the later path (B9.2-B9.5 as written).

The program tracker `beat-the-bar-2026-program.md` lands in PR #1680; this block is the
B9 entry for it, kept here verbatim so the plan stays self-contained, and pointed to from
`docs/PLANS.md` (Ambient voice) so the work is discoverable before the tracker merges.

```markdown
### B9 — Omi wearable: a roaming, consented microphone (W6 delivery vehicle)
Plan: [`omi-integration-plan.md`](omi-integration-plan.md). Pendant → BLE → the Pi panel
(later a Pi Zero 2 W dock) → Opus decode + Silero → existing `/api/voice/ambient` →
owner-only, speaker-gated `ambient_memory`. Nothing to Omi's cloud; the phone app is out.
Every item from B9.2 on is voice-path and **replay-gated** (probe + speed harness under
`flock`, with the new path enabled in the lab config). Invariants tested in every item:
no transcript exists until B9.5 flips attribution; every row carries `scope`.
- B9.0 🧑 **Consent posture**: (a) **mandatory** — build + flash the `omi-zoe` firmware
  (`CONFIG_OMI_ENABLE_OFFLINE_STORAGE=n`); until then the pendant is worn by Jason
  alone (`RING_CLEAR` is lab hygiene, not a posture); (b) default retention window
  (proposal 7 d); (c) household members who may opt into ambient; (d) legal sanity
  check of the discard-unknown rule under the WA Surveillance Devices Act for guests.
  Gate: variant flashed and GATT discovery shows no storage service; written answers in
  the plan's §8 before B9.4.
- B9.1 🔨 **Lab receive** (`labs/omi-receiver/omi_bridge.py`, bleak + opuslib, off-Orin; #1693, scaffold — not yet run against the pendant):
  connect, decode, 10-min WAV, reconnect-on-drop (SDK bug #13290). Gate: <1 % packet
  gaps at 3 m/one wall; Moonshine WER on 20 corpus sentences ≤ panel + 5 pts; battery
  drop/h logged.
- B9.2 ⬜ **Bridge thread in the Pi daemon, flag-dark** (`OMI_BRIDGE_ENABLED`,
  `ZOE_AMBIENT_OMI_ENABLED`, both off): migration adds `scope` (NOT NULL, charter
  values, `server_default` + backfill `personal`), `source`, `device_id`, `speaker_id`,
  `expires_at`; the existing panel INSERT is updated to write `scope='personal'` in the
  same PR; the `source=omi` handler never uses the owner-by-panel insert. Gate: ci_safe
  (flag off ⇒ no row; attribution off ⇒ Moonshine never called; **panel-capture
  regression: a plain panel ambient POST still stores, with scope**; explicit NULL scope
  rejected); replay gate PASS with the thread live; Pi RSS +≤120 MB.
- B9.3 ⬜ **Speaker gate in shadow — metrics only, no text**: sherpa-onnx
  `pyannote-segmentation-3.0` mixed/overlap detector + sub-window embedding
  consistency (all windows resolve to the **same** consented + ambient-opted-in speaker
  under the B4.1 margin rule against the whole eligible set — not "the owner"); numeric
  JSONL verdicts to the W5 shadow file; no Moonshine, no rows. Gate: one shadow week;
  negative control A — another voice alone 10 min ⇒ zero owner verdicts; negative
  control B — labelled owner+guest session ⇒ zero owner verdicts on guest-bearing
  segments, mixed rate reported; positive control C — an opted-in household member
  alone ⇒ `other-consented`, and `unknown` once their ambient consent is revoked; no
  text in logs; CPU/RSS of the segmentation model recorded; replay gate PASS.
- B9.4 ⬜ **Physical consent**: long-press session toggle + haptic + LED; per-profile
  `ambient_consent_at`; retention purge in the idle consolidator; requires B9.0 flashed.
  Gate: stranger test ⇒ no row/file/log text **and** no storage service on the device;
  toggle-off stops posts ≤1 s; replay gate PASS.
- B9.5 ⬜ **Attributed storage + promotion** (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`): only
  owner/other-consented verdicts reach Moonshine; rows `scope='personal'`; admission
  gate keeps scope; `[ambient:omi]` citation; rows on the memory page with delete. Gate:
  `memory_recall_probe` unchanged; 20-item "said near Zoe" set ≥80 % attributed recall;
  cross-user recall test (B never sees A's rows); replay gate PASS.
- B9.6 ⬜ Optional **offline drain** — only ever on a firmware whose ring is gated by the
  session toggle (a further variant, not stock), time-sync + ring read, confirmed-bytes
  advance. Gate: 30-min away-session row count ±1. Not scheduled.
- B9.7 ⬜ Optional **push-to-talk**: single tap ⇒ next segment goes to `/api/voice/turn`
  as a normal command (no speaker on CV1 ⇒ reply via the nearest panel/Telegram; haptic
  ack). Gate: replay-corpus commands via the pendant said-vs-did = panel; replay gate.
Dependencies: B0.1 → B9.1/B9.2; B4.1 + B4.3 → B9.3; B9.0 → B9.4; B9.5 → B3.9, B2.1.
Not doing: Omi app/backend/webhooks/MCP (cloud), DevKit 2 purchase, full diarization
in v1 (only the binary one-speaker-and-it-is-the-owner decision).
```

Sequencing line for §4 of the program: `B0.1 ─> B9.1 ─> B9.2 ─> (B4.1, B4.3) ─> B9.3 ─> B9.0 ─> B9.4 ─> B9.5 ─> B3.9`.

## 8. Open questions for Jason

**Answered 2026-10-12:** Q2 — the phone app is **rejected** and gets un-paired (0.2, runbook 4.7 step 1). The
design choice that follows from it is made (section 0.1). Still open: Q1 (read the model string — the device
check prints it), Q3 (range from the panel), Q4/Q5 (consent posture and the legal check — now *more* pressing,
because the stock ring records in company: 0.4), and the new Q9 below.

9. **Which Pi hosts the sync service, and does the pendant live near it?** The panel Pi's placement decides how
   often the pendant is "in range" (a 5–10 m BLE radius through walls). If he only comes home to a room the panel
   does not cover, a Pi Zero 2 W dock in that room is the answer (the same service runs there; ~AU$40).

1. **Which device is it?** Read the BLE model string (`Omi CV 1` expected). If it is a
   DevKit 2 the storage protocol, SoC and speaker answers in §2 change.
2. **Is the Omi phone app in use now?** The pendant bonds to **one** central. Using Zoe
   means un-pairing the phone app (and the app's cloud account can be closed; nothing in
   this plan needs it).
3. **Where does the pendant spend the day?** Decides panel-as-receiver vs a Pi Zero 2 W
   dock (and which room). BLE through walls is ~5–10 m.
4. **Consent posture (B9.0):** the `omi-zoe` flash (storage off) is mandatory before the
   pendant is worn in company — are you willing to run custom firmware on it? Default
   retention window? Which household members may opt into ambient attribution — and is
   anyone under 18 (the plan's strictest-retention rule)?
5. **Legal sanity check:** who does it (a short paid consult vs. Jason's own read of the
   Act), specifically on transient classification of a guest's voice that is then
   discarded, and on implied consent inside the household.
6. **Does the Orin's M.2 card expose Bluetooth?** Not needed for the recommended path;
   only matters if Jason wants a receiver on the box (not advised).
7. **Push-to-talk (B9.7):** do you want the button to be a "talk to Zoe from anywhere"
   remote? With no speaker on the CV1, replies land on the nearest panel (presence) or
   Telegram — is that acceptable?
8. **Wi-Fi:** the nRF7002 is on the board but unused by stock firmware. Enabling it is a
   firmware project (Zephyr Wi-Fi + a local upload target), not a config flip. Park it
   unless BLE range proves the blocker.

## 9. Risks

| Risk | Why it is real | Mitigation |
|---|---|---|
| Stock firmware records to SD regardless of Zoe | `CONFIG_OMI_ENABLE_OFFLINE_STORAGE=y`, continuous ring; clearing on connect only deletes the past | **Superseded for the sync-in-range path (0.4):** that path *needs* the ring on and drains a stock ring by design — the mitigation is owner-only wear, unverified/unreviewed transcripts nothing reads, audio deleted after transcription, delete/forget, and the phase-2 press-to-record firmware before wearing it in company. For the live-stream path the original B9.0 (storage-off variant mandatory) still stands |
| Guest words retained | WA SDA s5/s9 | discard-unknown + a real mixed-speaker detector + no-text shadow + stranger test (incl. device ring) as hard gates; legal check before P3 |
| Shadow mode itself leaks text | today's `/ambient` inserts every transcript under the panel owner | `source=omi` gets its own handler; Moonshine is not called until attribution is on; tested with a zero-call mock |
| Speaker gate mis-attributes | Omi's own 0.45 raw cutoff proved unreliable | margin rule + one embedder end-to-end + shadow week + two negative controls |
| A quiet guest hides under a loud owner | per-utterance ID alone cannot see it | segmentation model + sub-window consistency + short segments; P2 negative control B measures the miss rate before anything is stored |
| Scope walls | charter §3: no unscoped writes; personal never crosses users | `scope NOT NULL`, pendant rows `personal`; cross-user recall test in P4 |
| Firehose of ambient audio | always-on mic → Moonshine load on the Orin | AAD sleeps the mic; Silero segmenter; session toggle; per-day cap flag (`ZOE_AMBIENT_OMI_MAX_MIN_PER_DAY`) |
| Orin RAM | CPU-torch resemblyzer on first use ≈ +0.3–0.5 GB | sherpa-onnx embedder (B4.1) for the gate; measure in P2; B0.1 first |
| BLE drop/reconnect | SDK never sees disconnects (#13290); one bonded central | own `disconnected_callback` + backoff; un-pair the phone app |
| Checkpoint-advance-while-transmitting on drain | stock firmware frees records as the link acknowledges them (`storage.c` `sync_checkpoint_advance`; also observed by omi-collector) | the **durable spool** (4.2): a record is committed locally before anything else; custody firmware (`CAP_APP_ACK_RECLAIM`) closes the residual window; ADVANCE only on Zoe-confirmed state |
| Bricking a $129 device | custom firmware | UF2 is drag-drop-recoverable and the CV1 exposes its debug interface; build the *devkit*-free minimal change (one Kconfig line) and keep the stock UF2 to hand |
| Licence contamination | omi-collector is PolyForm NC | read for protocol, write our own bridge; Omi's own SDK/firmware are MIT |
| Second identity stack creeps in | Omi's speech profiles / "People" | none of Omi's identity code is used; profiles stay in `speaker_profiles` under the retention policy |

## 10. Sources

Omi docs index: <https://docs.omi.me/llms.txt> · protocol: <https://docs.omi.me/doc/developer/Protocol.md> ·
Python SDK: <https://docs.omi.me/doc/developer/sdk/python.md> · consumer hardware:
<https://docs.omi.me/doc/hardware/OmiConsumer.md> · DevKit 2: <https://docs.omi.me/doc/hardware/DevKit2.md> ·
DevKit 2 testing (storage/button): <https://docs.omi.me/doc/developer/DevKit2Testing.md> ·
firmware build: <https://docs.omi.me/doc/developer/firmware/Compile_firmware.md> ·
backend setup: <https://docs.omi.me/doc/developer/backend/Backend_Setup.md> ·
transcription: <https://docs.omi.me/doc/developer/backend/transcription.md> ·
integrations: <https://docs.omi.me/doc/developer/apps/Integrations.md> ·
audio-bytes apps: <https://docs.omi.me/doc/developer/apps/AudioStreaming.md> ·
privacy: <https://help.omi.me/en/articles/13162549-omi-privacy-policy> ·
consent workflow: <https://www.omi.me/blogs/workflows/recording-consent-governance> ·
repo: <https://github.com/BasedHardware/omi> (files read: `omi/firmware/omi/omi.conf`,
`CMakePresets.json`, `Kconfig`, `src/lib/core/{config.h,codec.c,transport.c,storage.c}`,
`src/mic.c`, `sdks/device/dart/lib/uuids.dart`, `sdks/python/omi/{decoder.py,constants.py}`,
`sdks/python/pyproject.toml`, `backend/parakeet/README.md`) · issues/PRs: #12765, #13055, #13290 ·
community: <https://github.com/j2h4u/omi-collector>, <https://github.com/kbdevs/omibutfree> ·
Pi Zero 2 W: <https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/> ·
WA SDA 1998: <https://www.legislation.wa.gov.au/legislation/statutes.nsf/law_a1919.html>,
<https://www.lavan.com.au/publications/lights-camera-illegal-evidence-should-you-be-recording-a-private-conversation/>,
<https://techsafety.org.au/blog/legal_articles/legal-guide-to-surveillance-legislation-in-wa/>.

In-repo seams this plan builds on: `services/zoe-data/routers/voice_tts.py`
(`/api/voice/ambient`, `/api/voice/identify`), `services/zoe-data/voice_speaker_id.py`,
`scripts/setup/zoe_voice_daemon.py` (`AMBIENT_*`, W5 shadow metrics),
`services/zoe-data/alembic/versions/{0002_fts_ambient_memory,0017_ambient_memory_user_scope}.py`,
`docs/knowledge/biometric-retention-policy.md`, `samantha-evolution-plan.md` §W6/§8.5/§8.6.
