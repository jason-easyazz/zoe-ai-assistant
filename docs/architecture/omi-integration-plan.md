---
type: architecture-plan
date: 2026-09-25
status: 📝 proposal — research done, nothing built, no flags exist yet
owner: W6 (attributed ambient capture) — samantha-evolution-plan.md; program block B9 below
audience: Jason + every agent picking up the Omi work
---
# Omi wearable → Zoe: integration plan

> **One line.** Treat the Omi pendant as a *roaming microphone for Zoe*, not as a product:
> pendant → BLE → a small Linux receiver → Opus decode + VAD → the **existing**
> `/api/voice/ambient` ingest on the Orin (Moonshine) → **owner-only, speaker-ID-gated**
> `ambient_memory` rows → the existing admission gate + consolidation. Nothing touches
> Omi's cloud, the phone app is not in the loop, and a stranger's voice leaves no trace.
> This is the delivery vehicle for **W6** and it inherits every W6 prerequisite.

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

## 4. Recommendation (one architecture)

```
 Omi CV1 ──BLE (Opus 16k/32kbps, 20 ms)──► omi-bridge (on the Pi panel; later a Pi Zero 2 W dock)
                                              │  bleak notify → strip 3-byte header → opuslib decode → 16 kHz PCM
                                              │  Silero VAD segmenter (same knobs as AMBIENT_*), button/battery events
                                              ▼
                        POST /api/voice/ambient  {audio_base64 (WAV 16k), panel_id, room, duration_seconds,
                                                  source:"omi", device_id, capture_session_id}
                                              │   (existing endpoint, device-token auth; Moonshine transcribe;
                                              ▼    raw audio discarded after STT — already the contract)
                     speaker gate (NEW, W6 step 1+2): embed segment → /identify semantics → margin rule
                        │ owner match (consented profile, margin ≥ 0.10) → INSERT ambient_memory(... user_id=owner,
                        │                                                   speaker_id, source='omi', expires_at)
                        │ enrolled+consented other → attribute to them (only if they opted into ambient)
                        └ unknown / below margin / multi-speaker → DISCARD (no row, no audio, count only)
                                              │
                                              ▼
                    existing admission gate → idle consolidation → recall with a distinct [ambient] citation
```

Why this shape:

- **Every box already exists except one.** `/api/voice/ambient` (`services/zoe-data/routers/voice_tts.py`)
  already does base64-WAV → Moonshine → `ambient_memory` and already refuses to store
  ownerless rows (P-F4). `ambient_memory` already has FTS (migration 0002), user scope
  (0017) and a `speaker_id` index that the insert path does not populate — which is
  W6 step 1 verbatim. `/api/voice/identify` already scores an embedding against
  *consenting* profiles only. The Pi daemon already segments room audio with Silero and
  posts it. The new code is: a BLE input thread, one `source`/`device_id`/`speaker_id`
  write, and the gate.
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
| Orin, resident | **0 new resident models.** Moonshine is already loaded; per-segment cost is the same as a panel ambient segment |
| Orin, per segment | Moonshine ~0.3–0.6 s for a 3–8 s segment (warm); embedding ~0.1–0.3 s CPU. Caveat: the server-side resemblyzer path pulls **CPU torch** on first use if torch is not already resident (~0.3–0.5 GB) — that is the B0.7 note "CPU torch for Resemblyzer". Prefer the sherpa-onnx embedder (B4.1) for the gate; it is ~30 MB and needs no torch |
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
4. **Multi-speaker segments are discarded** in v1 (the pendant hears Jason *and* the
   guest; Moonshine would transcribe both). Per-utterance ID is all W5 has; diarization
   (pyannote-grade who-spoke-when) is the named later upgrade in plan §8.5, not v1.
5. **The wearer controls capture with the button, with haptic acknowledgement**: long
   press toggles capture (haptic double-pulse on, single on off); the bridge only posts
   while the session is on; LED state mirrors it. Single tap is reserved for
   push-to-talk to Zoe (§8, Q7). This is Limitless's "Consent Mode" made physical.
6. **Retention window on ambient rows** (`expires_at`, default short — proposal 7 days
   — configurable per person, 1 day → forever as Limitless does), purged by the idle
   consolidator; promotion into `facts` goes only through the existing admission gate.
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
  the outside. Options, in order of preference: (i) build the `omi-zoe` firmware
  variant with offline storage **off** (a one-line Kconfig change in an open, UF2/OTA-
  flashable firmware); (ii) keep stock firmware but wipe the ring (`0x13 RING_CLEAR`)
  on every connect and never drain it; (iii) accept it. This is decision **B9.0**.

## 6. Phases and gates (lab first, flag-dark)

Every phase is flag-dark until its gate passes; every flag defaults off and lands in the
flag inventory via the pre-commit hook. No phase touches the panel voice turn path, so
the replay gate is *not* required — but P1+ run on the same Pi daemon, so P1's gate
includes a full replay-corpus run to prove the bridge thread does not regress it.

| Phase | Build | Gate (measured, reproducible) |
|---|---|---|
| **P0 — Lab receive** (a laptop or the Pi panel, *never* the Orin) | `scripts/setup/omi_bridge.py` (bleak + opuslib, no SDK dependency beyond those two): scan, connect, read DIS + codec id, subscribe audio, decode to WAV, log button/battery, reconnect on drop. Dump 10 min of audio | ≥10 min continuous at 3 m and through one wall; packet-number gaps < 1 %; decoded WAV plays; **Moonshine WER on 20 replay-corpus sentences read while wearing the pendant ≤ panel-mic WER + 5 pts**; battery drop/hour recorded |
| **P1 — Bridge into the daemon, flag-dark** | Bridge thread in `zoe_voice_daemon.py` behind `OMI_BRIDGE_ENABLED` (Pi env) feeding the existing ambient segmenter; posts carry `source="omi"`, `device_id`, `capture_session_id`. Server: `ambient_memory` gains `source` values, `device_id`, `speaker_id` write, `expires_at` (one Alembic migration); flag `ZOE_AMBIENT_OMI_ENABLED` (default off) → 404 for `source=omi` | `ci_safe` unit tests: flag off ⇒ no row, ever; migration up/down clean; **replay gate PASS with the bridge thread running** (proves no daemon regression); Pi RSS delta ≤ 120 MB |
| **P2 — Speaker gate, shadow** | Embed each segment on the Orin (sherpa-onnx embedder per B4.1, resemblyzer fallback), apply the margin rule, **log the verdict to the W5 shadow JSONL** — store nothing attributed yet (rows still owner-by-panel as today, or skipped) | One shadow week: owner FA/FR on the pendant; a **negative control** — a second household voice speaking alone for 10 min yields **zero** owner-attributed verdicts; multi-speaker segments correctly flagged |
| **P3 — Consent posture** | Button long-press session toggle + haptic + LED; `ambient_consent_at` per profile; retention purge job; `omi-zoe` firmware variant built and flashed **or** RING_CLEAR-on-connect, per B9.0 | A stranger test end-to-end: guest speaks near the wearer for 5 min → **no `ambient_memory` row, no temp file, no log line containing text**; toggling off stops posts within 1 s; retention purge verified with a clock-shifted row |
| **P4 — Attributed storage + promotion** | Flip the gate from shadow to act (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`); rows carry `speaker_id`; promotion through the admission gate; `[ambient:omi]` citation; rows visible/deletable on the memory page (B3.4) | `memory_recall_probe` unchanged on the existing corpus; a 20-item "said near, not to, Zoe" recall mini-set ≥ 80 % attributed recall; consolidation digest shows ambient-sourced candidates with provenance |
| **P5 — Optional: offline drain** | Only if B9.0 chose to keep storage *and* the wearer's session toggle gates the ring: on reconnect, time-sync, `RING_INFO`/`RING_READ`, decode 444-byte records, feed the same gate; keep the pendant still until the checkpoint catches up | Drain of a 30-min away-session reproduces the live path's row count ±1; interrupted drain loses nothing (advance only on confirmed bytes) |

Sequencing: **B0.1 (RAM floor) → P0 → P1**; **B4.1 (margin rule) + B4.3 (ID decision) →
P2**; P3 needs Jason's B9.0 decision and the legal check; P4 feeds **B3.9** (speaker-
cluster-gated owner attribution) and gives **B2.1** presence a second signal.

## 7. Proposed program block — B9 (for `beat-the-bar-2026-program.md`, not edited here)

```markdown
### B9 — Omi wearable: a roaming, consented microphone (W6 delivery vehicle)
Plan: [`omi-integration-plan.md`](omi-integration-plan.md). Pendant → BLE → the Pi panel
(later a Pi Zero 2 W dock) → Opus decode + Silero → existing `/api/voice/ambient` →
owner-only, speaker-gated `ambient_memory`. Nothing to Omi's cloud; the phone app is out.
- B9.0 🧑 **Consent posture decisions**: (a) firmware variant `omi-zoe` with offline SD
  storage OFF vs stock + `RING_CLEAR` on connect; (b) default retention window (proposal
  7 d); (c) household members who may opt into ambient; (d) a legal sanity check of the
  discard-unknown rule under the WA Surveillance Devices Act for guests. Gate: written
  answers in the plan's §8 before P3.
- B9.1 ⬜ **Lab receive** (`scripts/setup/omi_bridge.py`, bleak + opuslib, off-Orin):
  connect, decode, 10-min WAV, reconnect-on-drop (SDK bug #13290). Gate: <1 % packet
  gaps at 3 m/one wall; Moonshine WER on 20 corpus sentences ≤ panel + 5 pts; battery
  drop/h logged.
- B9.2 ⬜ **Bridge thread in the Pi daemon, flag-dark** (`OMI_BRIDGE_ENABLED`,
  `ZOE_AMBIENT_OMI_ENABLED`, both off): `source="omi"`, `device_id`, `speaker_id`,
  `expires_at` on `ambient_memory` (one migration). Gate: ci_safe tests (flag off ⇒ no
  row); replay gate PASS with the thread live; Pi RSS +≤120 MB.
- B9.3 ⬜ **Speaker gate in shadow** on the Orin (sherpa-onnx embedder per B4.1; margin
  rule; verdicts to the W5 shadow JSONL; multi-speaker ⇒ discard). Gate: one shadow
  week; negative control — another voice alone for 10 min ⇒ zero owner verdicts.
- B9.4 ⬜ **Physical consent**: long-press session toggle + haptic + LED; per-profile
  `ambient_consent_at`; retention purge in the idle consolidator; firmware per B9.0.
  Gate: stranger test ⇒ no row/file/log text; toggle-off stops posts ≤1 s.
- B9.5 ⬜ **Attributed storage + promotion** (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`): admission
  gate, `[ambient:omi]` citation, rows on the memory page with delete. Gate:
  `memory_recall_probe` unchanged; 20-item "said near Zoe" set ≥80 % attributed recall.
- B9.6 ⬜ Optional **offline drain** on reconnect (time-sync + ring read, confirmed-bytes
  advance) — only under a session-gated ring. Gate: 30-min away-session row count ±1.
- B9.7 ⬜ Optional **push-to-talk**: single tap ⇒ next segment goes to `/api/voice/turn`
  as a normal command (no speaker on CV1 ⇒ reply via the nearest panel/Telegram; haptic
  ack). Gate: replay-corpus commands via the pendant said-vs-did = panel.
Dependencies: B0.1 → B9.1/B9.2; B4.1 + B4.3 → B9.3; B9.0 → B9.4; B9.5 → B3.9, B2.1.
Not doing: Omi app/backend/webhooks/MCP (cloud), DevKit 2 purchase, diarization in v1.
```

Sequencing line for §4 of the program: `B0.1 ─> B9.1 ─> B9.2 ─> (B4.1, B4.3) ─> B9.3 ─> B9.0 ─> B9.4 ─> B9.5 ─> B3.9`.

## 8. Open questions for Jason

1. **Which device is it?** Read the BLE model string (`Omi CV 1` expected). If it is a
   DevKit 2 the storage protocol, SoC and speaker answers in §2 change.
2. **Is the Omi phone app in use now?** The pendant bonds to **one** central. Using Zoe
   means un-pairing the phone app (and the app's cloud account can be closed; nothing in
   this plan needs it).
3. **Where does the pendant spend the day?** Decides panel-as-receiver vs a Pi Zero 2 W
   dock (and which room). BLE through walls is ~5–10 m.
4. **Consent posture (B9.0):** flash the `omi-zoe` firmware with offline storage off, or
   keep stock and wipe the ring on connect? Default retention window? Which household
   members may opt into ambient attribution — and is anyone under 18 (the plan's
   strictest-retention rule)?
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
| Stock firmware records to SD regardless of Zoe | `CONFIG_OMI_ENABLE_OFFLINE_STORAGE=y`, continuous ring | B9.0: `omi-zoe` variant with storage off, or RING_CLEAR on connect; never drain without a session gate |
| Guest words retained | WA SDA s5/s9 | discard-unknown + multi-speaker discard + stranger test as a hard gate; legal check before P3 |
| Speaker gate mis-attributes | Omi's own 0.45 raw cutoff proved unreliable | margin rule + one embedder end-to-end + shadow week + negative control |
| Firehose of ambient audio | always-on mic → Moonshine load on the Orin | AAD sleeps the mic; Silero segmenter; session toggle; per-day cap flag (`ZOE_AMBIENT_OMI_MAX_MIN_PER_DAY`) |
| Orin RAM | CPU-torch resemblyzer on first use ≈ +0.3–0.5 GB | sherpa-onnx embedder (B4.1) for the gate; measure in P2; B0.1 first |
| BLE drop/reconnect | SDK never sees disconnects (#13290); one bonded central | own `disconnected_callback` + backoff; un-pair the phone app |
| Checkpoint-advance-while-transmitting bug on drain | observed on stock CV1 (omi-collector) | P5 only; advance on confirmed bytes; keep pendant still |
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
