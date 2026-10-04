---
type: research
title: Presence without new sensors — camera at 1 fps plus phone Bluetooth (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document
description: Deep-research record for owner decision Q19 ("Presence without new sensors (camera at 1 fps plus phone Bluetooth)"). Read-only trace of what Zoe already knows about presence with file:line (the kiosk heartbeat and panel_presence_tier, the 3-minute sleep card and its two votes, the room-toggle gate, the voice daemon's wake/VAD/follow-up windows, the enrolled-but-never-called face module, the dropped presence-events table, the orphaned presence-detection.js, what Home Assistant exposes with no hardware), what presence is FOR (sleep gate, proactive spoken lane, brief-on-arrival, the P1 orb, speaker-gate context; kid mode does not exist), then the field — person/face detectors on a Pi 5 CPU with measured numbers, Frigate's motion-first pattern, the USB-power trap and the Pi 5's 600 mA / 1.6 A budget, BLE presence (IRK, iOS address rotation, Bermuda, ESPresense, the companion app's Android-only BLE transmitter, classic-Bluetooth paging), Wi-Fi/DHCP trackers and sleeping phones, and the honest "buy one mmWave sensor" comparison with prices — a noisy-OR fusion design with decay and the two fail-safe directions, RAM/CPU budgets per box, a one-week measurement plan with negative controls and no household data leaving the box, a flag-dark phased build, and four decisions for the owner.
---

# Presence without new sensors — camera at 1 fps plus phone Bluetooth (2026-10-04)

Research date: 2026-10-04. The item is owner decision **Q19**, ticked for research, verbatim:
*"Presence without new sensors (camera at 1 fps plus phone Bluetooth)"*. It is pinned as **P8**
in [IDEAS.md](../IDEAS.md) and row P8 of
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md), and
it is the software half of **B4.5** (camera / HA device presence) and of the
[panel-identity plan](../architecture/panel-identity-plan.md) Phases 2.5 and 3.3. Its consumers
are **P1** (the pull inbox, [pull-not-push-inbox-2026-10-04.md](pull-not-push-inbox-2026-10-04.md)),
**W2** (speak-first gating, which the owner has turned OFF) and the panel sleep card. Sources are
cited inline and listed in §9. **[unverified]** marks a claim from a secondary source or a number
not measured on our hardware; **[estimate]** marks a number I derived from a measured one on a
different board.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are
untouched; nothing here adds Jetson RAM or runs on the Jetson's hot path; everything ships
flag-dark; no live service was run, restarted or queried; the Pi was not touched; no `.env` was
read; no household data is quoted (no member names beyond the owner's, no entity ids that name a
room's occupant, no addresses, no MACs). **Nothing was built.**

The owner's standing rules this record is written under: Zoe is **pull, not push** — she must not
speak unprompted (`ZOE_PROACTIVE_SPOKEN=0`, 2026-09-29); the panel is **voice first, touch second,
no keyboard**; **understand before you change**. Presence here is therefore a *gate and a prior*,
never a trigger for speech.

## 0. TL;DR

- **Zoe already has three presence signals and uses two of them.** (1) The kiosk heartbeat: the
  touch executor binds/syncs `ui_panel_sessions` every 5 s with `is_foreground: true`
  **unconditionally** (`touch-ui-executor.js:326-347`, `:2254-2255`), so `panel_presence_tier`
  (`proactive/presence.py:52-100`) can only say "the panel is on" (`bound_guest`) or "a member
  signed in with a PIN and has been talking" (`owner`) — it has never been able to say "someone is
  standing there". (2) The sleep card: a plain 3-minute inactivity timer (`home.html:1200`) that,
  at the moment of drifting to the night clock, asks two live questions — is music playing, is a
  light/switch/input_boolean **on** in this panel's room (`panel_config.py:376-416`) — and fails
  toward *sleeping* on every unknown. (3) Touch and voice activity (`home.html:1269`, the daemon's
  wake/VAD/follow-up windows), which reset the timer and refresh the session row but are recorded
  nowhere as "a human was here at 14:02".
- **The camera is enrolled but idle.** `zoe_face_id.py` (SCRFD det_500m + MobileFaceNet, ~15 MB,
  all on the Pi, frames never leave) is complete, three profiles exist (feature audit 2026-09-25
  row 19), **and the voice daemon has never called `identify_face`** — the only caller in the tree
  and in git history is the enrolment flow. Face-ID is a server flag (`ZOE_FACE_ID_ENABLED`,
  default off, threshold 0.45) with no runtime producer. So "camera at 1 fps" is not "turn up a
  dial"; it is the first continuous camera duty the panel would ever run — straight into the
  trap the enrolment flow documents: the PanaCast and the Jabra speaker share the Pi's USB power
  budget and **every camera drop on 2026-07-19 followed a TTS playback** (`zoe_enroll_flow.py:27-31`,
  `:121-136`, `:143-146`). The Pi 5 gives USB peripherals **600 mA** on a 3 A supply and **1.6 A**
  only with a 5 A PD supply and `usb_max_current_enable` ([Raspberry Pi docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html));
  the PanaCast needs a port "with an electrical current higher than 500 mA"
  ([Jabra](https://www.jabra.com/supportpages/jabra-panacast-20/8300-119/faq/Do-I-need-a-separate-power-adapter-for-the-Jabra-PanaCast-20)).
  **Power is the prerequisite, not the detector.**
- **The detector itself is cheap.** Frigate's pattern — frame-difference first, run a model only
  on motion, 5 fps is already "correct for the vast majority of cameras"
  ([Frigate](https://docs.frigate.video/configuration/motion_detection),
  [camera setup](https://docs.frigate.video/frigate/camera_setup)) — at 1 fps on a Pi 5 is a few
  ms of greyscale differencing per second. When something moves: YuNet face detection is
  **6.23 ms** at 160×120 on a Pi **4B** ([opencv_zoo benchmark](https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md));
  MediaPipe person detection **105.6 ms** at 224×224 on a Pi 4B (same table); YOLO26n (NCNN)
  **67 ms** at 640 on a Pi 5 ([Ultralytics](https://docs.ultralytics.com/guides/raspberry-pi/));
  our own SCRFD-500MF is 28.3 ms single-thread at 640×480 on a desktop core
  ([insightface](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md)),
  so ~100–150 ms on a Cortex-A76 [estimate]. None of this touches the Jetson.
- **Phone Bluetooth is the "who", and it has one hard step.** Phones rotate their BLE address
  about every 15 minutes ([Bluetooth SIG](https://www.bluetooth.com/blog/enhancing-device-privacy-and-energy-efficiency-with-bluetooth-randomized-rpa-updates/),
  [novelbits](https://novelbits.io/bluetooth-address-privacy-ble/)); to follow one you need its
  **IRK** — on iOS from a Mac's Keychain, on Android from a rooted `bt_config.conf` or an HCI
  snoop log ([HA Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/)),
  or by pairing the phone once to an ESP32 running [irk-capture](https://github.com/DerekSeaman/irk-capture)
  / [ESPresense enrol](https://espresense.com/guides/enrolling-devices/). Android has no IRK
  hand-off at all; its settled path is the companion app's **BLE transmitter** (iBeacon), which is
  **Android-only, off by default and costs battery**
  ([companion sensors](https://companion.home-assistant.io/docs/core/sensors/)). The no-app, no-key
  fallback is **classic-Bluetooth paging by MAC** every ~6 s (room-assistant: "do not need to be
  paired", "minor hit on the battery", "random false not_home"
  ([room-assistant](https://www.room-assistant.io/integrations/bluetooth-classic.html))). The Pi 5
  has BT 5 / BLE onboard, on the **same chip and antenna as Wi-Fi** (same page) — a scanner there
  competes with the kiosk's Wi-Fi.
- **Wi-Fi/DHCP trackers answer "home or away", not "in this room", and lie when phones sleep.**
  HA's ping/nmap trackers default to `consider_home` 180 s and warn *"Phones may turn off Wi-Fi
  when they are idle. A single ping tracker may not be reliable on its own."*
  ([HA ping](https://www.home-assistant.io/integrations/ping/),
  [nmap](https://www.home-assistant.io/integrations/nmap_tracker/)). The companion app is better
  (geofence enter/exit, an SSID sensor, iOS "significant change" at least every 15 min
  ([companion location](https://companion.home-assistant.io/docs/core/location/))) and is **already
  loadable**: our `homeassistant/configuration.yaml` uses `default_config:`, which loads
  `mobile_app`, `bluetooth`, `dhcp`, `usb` and `zeroconf`
  ([HA default_config](https://www.home-assistant.io/integrations/default_config/)) — but this
  house has **44 entities, zero `binary_sensor`, and the owner's `person` entity reads `unknown`**
  (`tests/test_panel_sleep_gate.py` docstring, verified live 2026-07-24).
- **The honest comparison.** A device-free mmWave sensor that catches a *still* person costs
  **US$17.90–24.90 / A$38** (Sonoff SNZB-06P/P24, Zigbee), **US$38 / A$80** (Everything Presence
  Lite, ESPHome, also a BLE proxy), **US$15–25** DIY (LD2410 + ESP32, ESPHome-native), or
  **US$82.99 / A$197** (Aqara FP2). Our sleep-gate resolver is a **domain tuple and a
  device-class check** away from consuming one (`panel_config.py:380`, `_AWAKE_DOMAINS`). It
  needs no camera, no phone, no USB power, no key extraction, and works in the dark.
- **Design (flag-dark, 0 Jetson RAM):** two scores per panel — `anyone_here` (room) and
  `owner_near` (who) — each a noisy-OR of per-signal probabilities with per-signal exponential
  decay; the kiosk heartbeat, touch, voice, music and room toggles are the free inputs today; the
  camera tick and BLE are the two new producers, both on the Pi, both reporting **scores only**.
  Two fail-safe directions, stated once: *anything that could speak assumes someone is listening*
  (so unknown ⇒ guest-safe or silent, never "nobody's here, say the private thing"); *anything that
  could capture assumes nobody consented* (so unknown ⇒ no escalation of camera or ambient duty).
  Presence never enqueues speech; it gates the sleep card, the P1 orb and the existing proactive
  tiers.
- **Go, in this order:** power prerequisite measured → shadow camera tick (motion only, JSONL,
  no POST) → sleep-gate third vote → BLE "owner near" → companion-app "owner home". Or skip the
  camera and BLE phases entirely for a A$38 sensor — that is Decision 1.

## 1. Our system — what Zoe already knows, read-only (file:line)

### 1.1 The kiosk heartbeat and `panel_presence_tier`

- `services/zoe-ui/dist/js/touch-ui-executor.js:326-347` — `bindPanel()` POSTs
  `/api/ui/panel/bind` and `syncState()` POSTs `/api/ui/state/sync`, **both with
  `is_foreground: true` hard-coded**; `:2254-2255` — actions are polled every 2 s and the sync
  runs every 5 s for as long as the page is open. Nothing in the executor reads
  `document.hidden` or touch activity before deciding what to send.
- `services/zoe-data/routers/ui_actions.py:20-31` — the server treats the row as abandoned after
  **300 s** without a sync ("a live panel binds/syncs every ~5s while a real owner is present, so
  this 300s window is ~60× that cadence"); `:71-87` — `_note_owner_presence` fires the
  brief-on-arrival hook only for a **member's own session** (guests and device tokens never count).
- `services/zoe-data/proactive/presence.py:29` — freshness window `ZOE_PRESENCE_WINDOW_S`, default
  **900 s**; `:47-49` — three tiers: `owner` (a member signed in with a PIN and kept fresh by their
  own turns), `bound_guest` (the kiosk guest on a panel whose default binding names the member —
  documented as *"the member's panel is on, NOT the member is there"*), `absent` (otherwise, and
  on any error). `:96-100` — a DB error reads as absent: *"Presence is a gate for OPTIONAL
  behaviour … a DB hiccup must read as 'nobody there'"*.

What this means: the one presence primitive the server has is an **identity-confirmed session**
proxy. It is right for "may Zoe say something private" and useless for "is anyone in the room".

### 1.2 The sleep card: a 3-minute timer with two live votes

- `services/zoe-ui/dist/touch/home.html:1192-1200` — the sleep surface is reached after
  `IDLE_SLEEP_MS = 180000` of no touch/voice; the backlight never fully powers off
  (`off_enabled=false` on the panel agent); the window is overridable from the display-preferences
  endpoint (`routers/system.py:2456-2467`: `idle_seconds` 120, `off_seconds` 900,
  `sleep_seconds` 180; `:2471` panel agent on port 8765).
- `home.html:1201-1267` — `armIdleSleep`: a live conversation (`_convLive`) holds the panel
  awake; at expiry it races two requests against a 4 s timer and takes **one** decision:
  `GET /api/music/now-playing` (`state === 'playing'` ⇒ stay) and
  `GET /api/panels/{id}/sleep-gate` (`block` ⇒ stay). *"With no votes at all `_stay` is false, so
  an unreachable server still falls through to sleeping."*
- `services/zoe-data/routers/panel_config.py:376-416` — `resolve_sleep_gate`: for every entity
  bound to the panel's room (`routers/rooms.py:183`, `room_entity_ids_for_panel`), if its domain is
  in `_AWAKE_DOMAINS = ("light", "switch", "input_boolean")` and its state is `on`, block sleep.
  The docstring is the ground truth for this whole record: *"This house has ZERO
  motion/presence/occupancy entities (44 entities, no `binary_sensor`), so the room's own toggles
  are the only honest presence signal available."* `:469-491` — `_entity_index` pulls the **full**
  entity list from the HA bridge on every call (no cache; `None` when HA is unreachable).
  `:603-618` — the route is unauthenticated on purpose (the kiosk is a guest).
- `home.html:1269` — any `pointerdown` marks `_userTookOver` and re-arms the timer; `:4102` — voice
  wakes the panel off the sleep clock; `:3224`, `:4455` — the orb tap POSTs `/activate` to the
  daemon on `localhost:7777` (`no-cors`, #1829).
- Lock-in: `services/zoe-data/tests/test_panel_sleep_gate.py` (direction of failure = sleep) and
  the browser gate `services/zoe-ui/dist/test_touch_sleep_gate.js` (the two-vote race in real
  Chromium; not in CI).

So the sleep card already *is* a presence consumer with the right failure direction, and it
already has a slot for a third vote.

### 1.3 The voice daemon's windows and flags (`scripts/setup/zoe_voice_daemon.py`)

- Wake: openWakeWord with a 2-confirm window; `on_wake` (`:1574-1587`) fires three fire-and-forget
  threads — the beep, `_wake_panel_agent` (`POST 127.0.0.1:8765/wake {hold_s: 20}`, `:1562-1571`)
  and `_notify_wake_background` (`POST /api/voice/wake {panel_id}`, `:1551-1559`). That wake POST
  is the only per-event "a human just spoke here" the server receives, and it is used for UI only.
- Busy state the daemon already tracks, which any camera duty must respect: `_tts_process`
  (`:389`, set while aplay runs), `_recording_active` (`:471`, set around every capture,
  `:2943-2961`), `_ignore_wake_until` (post-play cooldown, `:3074`, `:3151-3158`), and the
  announcement poll (`ANNOUNCE_POLL_S` 5 s, `:3083-3091`, played rows ACKed `:3166`).
- Ambient capture (`:1430-1480`, `AMBIENT_CAPTURE_ENABLED`, off): Silero VAD over the always-open
  wake stream, segments POSTed to `/api/voice/ambient`; it already skips while TTS, recording or
  cooldown are active (`:1452-1458`). This is the template for a camera tick's scheduling — same
  three guards, same "never during playback" rule.
- Health: `GET :7777/health` returns `{status, service, panel_id, uptime_s, wake_phrase}`
  (`:3218-3236`); `POST /activate` sets the orb-tap event (`:3240-3250`). A presence score would
  naturally ride on this JSON for local debugging before it is ever POSTed anywhere.
- Speaker-ID: `_speaker_claim_for_turn` (`:2096`), shadow by default (`SPEAKER_ID_SHADOW`), scored
  in a background thread since #1760; the claim is attached to the turn when active
  (`:2246-2258`). This is the "who" the voice path already produces; BLE would be a second, weaker
  "who" that works between turns.

Config knobs that exist: `FACE_ID_ENABLED`, `FACE_CAMERA_INDEX`, `FACE_CAPTURE_FRAMES` (4),
`FACE_CAPTURE_SPAN_S` (0.8), `FACE_DET_THRESHOLD` (0.5), `FACE_MIN_PX` (60 default; 36 on the live
panel per the ops record), `FACE_MODEL_DIR`, `FACE_ID_SYNC_TTL_S` (`zoe_face_id.py:39-56`).

### 1.4 The camera: a complete module with no runtime caller

- `scripts/setup/zoe_face_id.py:1-23` — pipeline per wake-word capture: `cv2.VideoCapture` →
  3–5 frames over ~1 s → SCRFD `det_500m.onnx` at **640** letterbox (`:131`) → best face by
  area × score with `MIN_FACE_PX` (`:329-347`) → ArcFace alignment → `w600k_mbf.onnx` 512-d →
  cosine against the synced profile cache → `(user_id, raw score)` **claim**; the server applies
  the threshold. *"Frames are process-local and discarded after embedding; only embeddings ever
  leave the Pi."*
- `:364-399` — `capture_frames` **opens and releases the device on every call** under a
  non-blocking lock. `:401-434` — `identify_face` returns `None` unless `FACE_ID_ENABLED`.
- Callers: `grep -rn identify_face` finds the definition and `zoe_enroll_flow.py:115` (the
  enrolment flow loads the module by path). `git log -S identify_face -- zoe_voice_daemon.py` and
  `-S zoe_face_id` return **nothing**: the daemon never imported it. The server side
  (`routers/face_id.py:48-57`, `ZOE_FACE_ID_ENABLED` default `false`, `ZOE_FACE_ID_THRESHOLD`
  0.45) has enrol / sync / list / delete, and no identify consumer. The feature audit says the
  same from the other side: row 19, *"UNTESTABLE (panel off) … 3 profiles … Policy says it must
  be off until a delete UI exists"* ([feature-audit-2026-09-25.md](../knowledge/feature-audit-2026-09-25.md)).
- The two traps, both recorded by the person who hit them (`zoe_enroll_flow.py`): `:27-31`
  *"forcing 720p on the Jabra PanaCast browned out the Pi's USB port on 2026-07-19 (USB disconnect
  + connect-debounce failure needing a physical replug). Never set CAP_PROP_FRAME_WIDTH/HEIGHT"*;
  `:121-126` *"The Jabra PanaCast drops off the USB bus when opened/closed in quick succession …
  first open fine, second open within seconds → USB disconnect needing a physical replug"*;
  `:143-146` *"the PanaCast camera and the Jabra speaker share the Pi's USB power budget, and
  speaking while streaming video browns the camera out (observed live 2026-07-19 — every camera
  drop followed a TTS playback)"*. The ops memory adds: a powered hub before any per-turn face
  check.
- Policy already written: [biometric-retention-policy.md](../knowledge/biometric-retention-policy.md)
  — embeddings only, no frames, no images, kept until deleted; it explicitly scopes *out*
  ambient capture (W6). Occupancy scores are a third thing and need one paragraph (§3.6).

### 1.5 Dead ends already in the tree (do not resurrect)

- `services/zoe-ui/dist/touch/js/presence-detection.js` — a MagicMirror-style idle/ambient module
  with a **simulated PIR** (`Math.random() > 0.95` per second, `:68-81`) and a `pirSensorPin`
  placeholder. **No HTML or JS loads it** (grep over `dist/` finds only itself). It is an orphan.
- `panel_presence_events` — a per-event table (`panel_id, event_type, payload, confidence`) created
  in the 0001 schema and **dropped in migration 0028** because no writer remained and *"Presence
  events are documented as not persisted"*. The runbook says to drop its backup table after
  verification. The design below keeps that decision: **last-state per panel, in memory, no event
  log** on the Jetson; the only durable record is the 7-day JSONL of scores on the Pi.
- The panel-identity plan already specified the camera tick as *"every ~30 s, single frame,
  face-count/frame-diff only — no identity, no frames leave the Pi"* with
  `ZOE_PRESENCE_SOURCES` as the flag (`panel-identity-plan.md:101-104`, `:113`) and the HA device
  tracker as Phase 2.5 (`:74-85`). This record keeps those names.

### 1.6 What Home Assistant exposes today, with no new hardware

- `homeassistant/configuration.yaml` loads `default_config:` plus `auth_oidc`, `template`,
  `input_boolean`, `input_number`, scenes/scripts/automations and one custom component
  (`custom_components/zoe_conversation`). `default_config` brings **Bluetooth, DHCP discovery,
  Mobile app, SSDP, USB, Zeroconf** and friends
  ([HA default_config](https://www.home-assistant.io/integrations/default_config/)).
  The two stock blueprints under `homeassistant/blueprints/` already reference `mobile_app`
  notifications. So the **companion app** path is one phone install away, and a
  `device_tracker.<phone>` + `sensor.<phone>_ssid` would appear without touching the repo.
- Live state (read-only, from the test docstring, verified 2026-07-24): **44 entities, zero
  `binary_sensor`**, the owner's `person` entity `unknown` — a person entity with **no tracker
  attached**. HA's person integration resolves home-state from connection trackers first when at
  home and position trackers first when away ([HA person](https://www.home-assistant.io/integrations/person/)),
  so attaching the phone tracker is all it needs.
- The bridge (`services/homeassistant-mcp-bridge/main.py:37-38`, `HA_BASE_URL` / `HA_ACCESS_TOKEN`)
  is **REST-only pull** — `/entities`, `/entities/{id}`, `/devices/control`; no event
  subscription. `routers/ha_control.py:64-75` already filters by domain, so
  `GET /api/ha/entities?domain=device_tracker` works the day a tracker exists.
- HA's own Bluetooth integration is **not** a free path for us: HA runs in Docker on the Jetson,
  and the integration needs an adapter on *that* host plus D-Bus and BlueZ ≥ 5.43 in the container
  ([HA bluetooth](https://www.home-assistant.io/integrations/bluetooth/)); whether the Orin's M.2
  card exposes Bluetooth is an open question the Omi plan also left open
  (`omi-integration-plan.md:364`, `:450`). The radio we *know* we have is the Pi 5's.

### 1.7 What presence is FOR — every consumer, with the code path

| Consumer | Where it reads presence today | What it needs that it does not get |
|---|---|---|
| **Sleep card** (idle screensaver) | `home.html:1201-1267` → `/sleep-gate` → `panel_config.py:376-416` (room toggles) + `/music/now-playing` | "someone is in the room but every light is off and nothing is playing" — e.g. daylight, a lit room from another switch, a person reading. Today that person watches the night clock appear. |
| **Proactive spoken lane** (`_maybe_speak_notification`, `engine.py:292-350`) | `panel_presence_tier` — `owner` speaks the message, `bound_guest` speaks only the guest-safe teaser, `absent` logs `outcome=absent` | Off by owner decision. If it ever returns, it needs "someone *else* may be listening" — exactly the fail-safe direction below. |
| **Brief-on-arrival** (`arrival.py:1-40`, gate `:431-440`) | `panel_presence_tier == owner` on THIS panel, 07:00–11:00, not quiet hours, no turn in the last 2 min | Off (needs `ZOE_PROACTIVE_SPOKEN`). Its docstring already says *"Face/voice claims have no server-side record yet, so they do not count"* — a timestamped claim record is what would let a non-PIN arrival count. |
| **First conversation of the day** (`brief_first_turn.py:1-10`, `ZOE_BRIEF_ON_FIRST_TURN`) | None — it is **turn-driven**: the `[Today]` block rides into the member's first brain turn | Nothing. It is already the pull-shaped version of the brief; presence must not change it. |
| **P1 orb "has something"** ([pull-not-push §3.3](pull-not-push-inbox-2026-10-04.md)) | Proposed: show the state only when the panel's bound member is plausibly there; open question 1 of that record says "no orb for a guest-only panel" | `owner_near` — the *who* half. Without it the orb lights for whoever walks past. |
| **Speaker-gate context** ([speaker-gate §2.7](speaker-gate-rebuild-2026-10-04.md)) | The voice claim per turn; the follow-up window picks up TV/side talk | A prior: "the owner's phone is near this panel" raises the owner hypothesis before the first clip; "two faces in frame" caps confidence below step-up (identity plan `:141`). |
| **Kid mode** | **Does not exist.** `grep -rli 'kid_mode\|kids_mode\|child_mode'` over `services/`, `docs/` returns nothing. | Nothing to gate. Noted so nobody designs for a feature that is not there. |
| **Panel identity step-up** (`panel-identity-plan.md:92-115`) | Planned: confidence decays unless "presence intact"; a door/motion event marks `presence_broken` | The camera tick's `person_count` change is the planned `presence_broken` source for a house with no door sensors. |

## 2. Field — what runs on a Pi 5, what phones leak, what a sensor costs

### 2.1 Person / face presence from a camera on a Pi 5 CPU

**Frigate's shape is the one to borrow.** *"Motion detection is just used to determine when
object detection should be used"*: frames are differenced, thresholded (default 30), grouped by
contour area (default 10 px), and only the moving region goes to the detector
([Frigate motion](https://docs.frigate.video/configuration/motion_detection)). The detect stream
runs at **5 fps** by default — *"correct for the vast majority of cameras"* — and the model's
native **320×320** is the resolution to feed, because *"higher resolutions do not improve the
detection accuracy because the additional detail is lost in the resize"*
([Frigate camera setup](https://docs.frigate.video/frigate/camera_setup)). Frigate also says the
plain CPU detector *"is not recommended for general use"* and that a Coral needs ~10 ms inference
to keep up with multiple cameras ([Frigate detectors](https://docs.frigate.video/configuration/object_detectors/))
— which is a statement about **5 fps × several cameras**, not about **1 frame per second on one
camera when something moved**. Our duty cycle is ~1/5 of one Frigate camera before motion
gating, and a fraction of that after.

**Measured detector costs (all CPU, all public tables):**

| Detector | Input | Board | Mean ms | Source |
|---|---|---|---|---|
| YuNet (face) | 160×120 | Raspberry Pi 4B | **6.23** (int8 6.68) | [opencv_zoo benchmark](https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md) |
| MediaPipe person detection | 224×224 | Raspberry Pi 4B | **105.6** | same |
| MediaPipe pose | 256×256 | Raspberry Pi 4B | 116.2 | same |
| EfficientDet-Lite0 (MediaPipe object detector) | 320×320 | Raspberry Pi 5 | **~35** [unverified, blog] | [mlsysbook / medium](https://jeffzzq.medium.com/object-detection-on-the-raspberry-pi-5-463ba0f11d1e) |
| YOLO26n NCNN / ONNX / OpenVINO | 640 | Raspberry Pi 5 | **67.0 / 126.0 / 104.6** | [Ultralytics Pi guide](https://docs.ultralytics.com/guides/raspberry-pi/) (YOLO11n ≈ 147 ms ONNX, "6.79 → 7.79 FPS") |
| SCRFD-500MF (our `det_500m`) | 640×480 | AMD Ryzen 9 3950X, 1 thread | **28.3** (11.4 at 320×240) | [insightface model zoo](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md) |
| SCRFD-500MF | 640 letterbox | Pi 5 Cortex-A76 | **~100–150** [estimate: 3–5× the desktop single-thread figure] | derived |

Reading: a Pi 5 is roughly 2× a Pi 4B on these workloads [unverified], so YuNet at 160×120 is
~3 ms and the MediaPipe person detector ~50 ms per *motion* frame. Face detection alone misses a
person with their back to the panel; a person detector sees a torso. For "is anyone in the room"
the cheapest honest stack is **frame-diff every second, YuNet or SCRFD at 320 on motion, person
detector only if we later prove faces miss too much** (measurement E3 in §5). Everything here is
ONNX/OpenCV already in `pi-requirements.txt` (`opencv-python-headless`, `onnxruntime`); YuNet
would be a new 100 KB model file, the person detector a new ~3 MB one.

**What does not fit.** YOLO at 640 is a 67–126 ms per-frame cost for a class list we do not
need; OpenVINO is x86-first; a Coral or Hailo HAT is "a new sensor" by another name. MediaPipe's
Python package pulls its own runtime (~tens of MB) alongside onnxruntime — acceptable on a Pi
with 5.65 GB free, but only if YuNet proves insufficient.

**The power trap, with numbers.** The Pi 5 gives USB peripherals **600 mA** on a 3 A supply and
**1.6 A** with a 5 V 5 A PD supply (or `usb_max_current_enable=1` in `config.txt`)
([Raspberry Pi docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)).
Jabra's own FAQ says the PanaCast works only on a USB 2.0 port *"that has an electrical current
higher than 500mA, or a USB 3.0 port"*
([Jabra](https://www.jabra.com/supportpages/jabra-panacast-20/8300-119/faq/Do-I-need-a-separate-power-adapter-for-the-Jabra-PanaCast-20)).
A USB speakerphone playing audio draws its own peak on the same budget. On a 600 mA budget the
camera *streaming* while the speaker *plays* is exactly the brown-out the enrolment flow logged.
Two consequences for any 1 fps design:

1. **Opening `VideoCapture` starts the UVC stream and its power draw whether or not frames are
   read.** "1 fps" lowers CPU, not current. The choice is between *streaming all the time* (the
   brown-out risk during every reply) and *opening/closing around TTS* (the quick-reopen
   disconnect). Neither is safe on 600 mA; both should be safe on 1.6 A — and that is a
   measurement (§5 E1), not an assumption.
2. The schedule must be: **stop the stream before `_tts_process` starts, re-open no sooner than
   N seconds after it ends and never within M seconds of the last open** — the ambient-capture
   guards (`:1452-1458`) plus a hysteresis the enrolment flow learned the hard way.

### 2.2 Phone Bluetooth / BLE presence

**Address randomisation is the whole problem.** A phone advertising over BLE uses a Resolvable
Private Address that rotates — the specification's recommendation is every **15 minutes**, which
iOS follows ([Bluetooth SIG](https://www.bluetooth.com/blog/enhancing-device-privacy-and-energy-efficiency-with-bluetooth-randomized-rpa-updates/),
[novelbits](https://novelbits.io/bluetooth-address-privacy-ble/),
[Argenox](https://argenox.com/library/bluetooth-low-energy/demystifying-ble-addresses)). A
listener that holds the phone's **IRK** can resolve each new address by recomputing the 24-bit
hash; a listener without it sees a different stranger every quarter hour.

**Getting the IRK** ([HA Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/)):
- iOS: on a Mac signed into the same iCloud account, Keychain Access → the "Bluetooth" entry for
  the phone's MAC → *Remote IRK* (base64). ESPresense notes that on iOS 17+ the IRK sometimes does
  not transfer to a pairing peer and the Keychain route is the fallback
  ([ESPresense enrol](https://espresense.com/guides/enrolling-devices/)).
- Android: `/data/misc/bluedroid/bt_config.conf` on a **rooted** device, or an HCI snoop log read
  in Wireshark (`btsmp.id_resolving_key`). ESPresense is blunter: *"Android does not provide an
  IRK and rotates its BLE MAC address, making direct pairing impossible"* — their settled Android
  path is the **companion app's BLE transmitter** (iBeacon with a stable UUID).
- Either OS, hands-free: pair the phone once to an ESP32 running
  [irk-capture](https://github.com/DerekSeaman/irk-capture) (ESPHome; *"IRKs are generally
  permanent … a one-time capture per device"*; some devices refuse) or to an ESPresense node in
  enrol mode. That ESP32 is a **~A$10 part used once** — which is why Decision 2 below asks the
  phone question first.

**The companion app's BLE transmitter** is **Android-only**, **disabled by default**, and *"can
impact battery life, particularly if used with Transmit Power set to High"*
([companion sensors](https://companion.home-assistant.io/docs/core/sensors/)). Not an option for
an iPhone owner.

**Classic Bluetooth paging (no app, no key).** room-assistant's `bluetooth-classic` integration
*"sends out connection requests to the device addresses you configure on rotation and then checks
the signal strength of the response"*, every **6 s**; devices *"do not need to be paired"*; costs
*"a minor hit on the battery life"*; *"random false not_home"* happen and need timeout tuning;
and on a Pi *"Bluetooth and WiFi are on a shared chip and antenna"*
([room-assistant](https://www.room-assistant.io/integrations/bluetooth-classic.html)). This is
the path that was built *for* iPhones. It needs the phone's **classic** BT MAC (Settings → About)
— household data that lives only in the Pi's env file, never in the repo. HA removed its own
`bluetooth_tracker` integration ([HA](https://www.home-assistant.io/integrations/bluetooth_tracker/)
now reads "removed"); the technique still works at the BlueZ level. Reported iPhone flakiness in
room-assistant's tracker ([issue #270](https://github.com/mKeRix/room-assistant/issues/270): BLE
presence only while the Bluetooth settings page is open) is about BLE *advertising*, not classic
paging — but it is a warning that an idle iPhone is a quiet one. **Measure before trusting**
(§5 E4).

**Room-level BLE in HA: Bermuda, not ESPresense.** Bermuda is an HA integration that places a
BLE device in an *area* from the RSSI seen by ESPHome Bluetooth proxies, Shelly Gen2+ devices or
a local adapter (the last *"limited functionality; lacks packet timestamping"*); iPhones work via
the Private BLE Device integration *"without additional setup"*; it creates `device_tracker`
entities you can attach to a `person` ([Bermuda](https://github.com/agittins/bermuda)). The
2025-12 field guide calls ESPresense *"much harder to configure and tweak"* and Bermuda *"very
reliable with iPhones"* ([derekseaman](https://www.derekseaman.com/2025/12/home-assistant-track-whos-in-each-room-with-esphome-bermuda-ble.html))
[unverified, one author]. The identity plan already chose Bermuda over ESPresense for this reason
(`panel-identity-plan.md:37`). **But** Bermuda runs inside HA and needs HA to see Bluetooth —
either an adapter on the Jetson (open question, Docker D-Bus work) or ESPHome proxies (new
hardware, and the Everything Presence Lite *is* one). For a **single panel** the simpler shape is a
scanner **on the Pi**, posting a score to zoe-data — Bermuda becomes the right tool the day there
is a second room.

**The Pi 5 radio.** Bluetooth 5 / BLE onboard ([Raspberry Pi docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html));
HA's integration lists the Pi 3B+/4B's CYW43455 as supported but *"connected via the UART bus
which may limit their performance"* ([HA bluetooth](https://www.home-assistant.io/integrations/bluetooth/));
BlueZ passive scanning needs ≥ 5.63 with experimental features ([same page](https://www.home-assistant.io/integrations/bluetooth/),
[bleak #1612](https://github.com/hbldh/bleak/discussions/1612)). Indoor range is "same room,
maybe the next" — a few metres through drywall [unverified; measure]. The Pi already runs BlueZ
for the Omi lab (`labs/omi-receiver/README.md:71-77`, `bleak 0.22.3`), so the stack is proven on
this panel.

### 2.3 Wi-Fi / DHCP device trackers

- HA ping tracker: `consider_home` **180 s** default, ping every 30 s, and the warning *"Phones
  may turn off Wi-Fi when they are idle. A single ping tracker may not be reliable on its own."*
  ([HA ping](https://www.home-assistant.io/integrations/ping/)). nmap: *"Modern smart phones will
  usually turn off WiFi when they are idle. Simple trackers like this may not be reliable on their
  own."* ([HA nmap](https://www.home-assistant.io/integrations/nmap_tracker/)).
- Companion app: iOS "significant change" updates arrive on cell-tower change, *"a significant
  amount of time has passed (usually a couple hours)"*, or at minimum every 15 minutes; zone
  geofences fire enter/exit; an iBeacon can stand in for location; Android's fused location
  updates every 1–3 min ([companion location](https://companion.home-assistant.io/docs/core/location/)).
  The **SSID sensor** (`sensor.*_ssid`, iOS; `wifi_connection`, Android) reports the current
  network name ([companion sensors](https://companion.home-assistant.io/docs/core/sensors/)).
- Verdict: these give **`owner_home`** (house-level, minutes of lag, false "away" when the phone
  sleeps) — a prior for the P1 orb and for identity, never a room signal. Free to turn on, zero
  repo change, and the only one of the three that also fixes the `unknown` person entity.

### 2.4 The honest "buy one sensor" comparison

| Option | What it detects | Price | HA path | Notes |
|---|---|---|---|---|
| **LD2410 + ESP32 (DIY)** | moving + **still** target, 9 gates, ~6 m | ~US$5–10 module, **US$15–25** all-in ([guide](https://bishalkshah.com.np/blog/esp32-mmwave-presence-sensor-home-assistant), [calvin.me](https://calvin.me/diy-mmwave-presence-detectors/)) | ESPHome-native: `has_target`, `has_moving_target`, `has_still_target` ([ESPHome LD2410](https://esphome.io/components/sensor/ld2410/)) | Jason flashes it once; the identity plan already named it (`:130`) |
| **Everything Presence Lite** | LD2450: up to 3 targets with x/y, zones, lux | **US$38** ([shop](https://shop.everythingsmart.io/products/everything-presence-lite)), **A$80.45** ([Pakronics](https://www.pakronics.com.au/products/everything-presence-lite-ss114993300)) | ESPHome over Wi-Fi; **also a Bluetooth proxy** ([CNX](https://www.cnx-software.com/2025/05/08/everything-presence-lite-esp32-based-mmwave-presence-sensor-tracks-up-to-three-targets-simultaneously/)) | One box does mmWave *and* the BLE proxy Bermuda wants |
| **Sonoff SNZB-06P / P24** | 5.8 GHz / 24 GHz radar, light sensor; P24 adds 7 zones | **US$17.90 / US$24.90** ([Sonoff](https://sonoff.tech/en-us/products/sonoff-senseguard-presence-core-24ghz-zigbee-human-presence-sensor-snzb-06p24)), **A$38.22** P24 ([Amazon AU](https://www.amazon.com.au/SONOFF-SNZB-06P-Microwave-Precision-Assistant/dp/B0CNGKFG9Y)) | **Zigbee** — needs a coordinator this house may not have [unverified] | Cheapest finished unit; adds a radio stack if there is no Zigbee today |
| **Aqara FP2** | multi-zone, multi-person, local | **US$82.99**, **A$197** ([Aqara US](https://us.aqara.com/products/presence-sensor-fp2), [smarthome.com.au](https://www.smarthome.com.au/product/aqara-fp2-presence-sensor/)) | Wi-Fi; the Samantha plan's own citation (`samantha-evolution-plan.md:623`) | The premium answer; overkill for one panel room |
| **PIR** | motion only — a reader goes "absent" | ~A$10–20 | Zigbee/ESPHome | The sleep card's exact failure mode; not recommended |
| **Echo-style ultrasound** (existing speaker + mic) | Doppler of movement, ≥ 32 kHz | A$0 | — | Rejected: our mic path is 16 kHz (`SAMPLE_RATE`), and *"detecting low-SNR events often means high false-positive rates"*; misses still people ([Amazon Science](https://www.amazon.science/blog/the-science-behind-ultrasonic-motion-sensing-for-echo)) |

What a mmWave sensor buys that the camera + phone cannot: it sees a **still** person in the
**dark** with no face to the panel, no phone in pocket, no USB current, no key extraction, no
bystander-camera question, and it is live the moment HA sees it. What it does not buy: *who*.
The code change on our side is small and already lock-in-tested: `resolve_sleep_gate`
(`panel_config.py:380-416`) iterates the room's entities by domain; adding `binary_sensor` with a
`device_class in {occupancy, motion, presence}` check gives the sleep card its third vote, and the
same entity reaches the fusion as a p≈0.95 input.

### 2.5 Privacy posture — what the field does and what our note must say

- The Nest Hub Max line: familiar-face data on newer devices is *"stored directly in their
  internal memory"*, per-camera enable/disable, delete-per-profile
  ([Google Nest](https://support.google.com/googlenest/answer/9268625)). Amazon's Echo presence is
  ultrasound precisely so there is no camera ([Amazon Science](https://www.amazon.science/blog/the-science-behind-ultrasonic-motion-sensing-for-echo)).
  Frigate processes everything locally and stores *clips*; we store **nothing**.
- The bystander literature is consistent: people are more worried about cameras in **other
  people's** homes than their own, and guests *"are often unaware of the smart home devices"*
  ([Windl et al. 2022](https://www.medien.ifi.lmu.de/pubdb/publications/pub/windl2022theskewed/windl2022theskewed.pdf),
  [systematic review, ACM 2025](https://dl.acm.org/doi/10.1145/3731755)); the preferred remedy is
  a visible **awareness mechanism** rather than a policy page
  ([Marky et al.](https://www.researchgate.net/publication/360331111_It_would_probably_turn_into_a_social_faux-pas_Users'_and_Bystanders'_Preferences_of_Privacy_Awareness_Mechanisms_in_Smart_Homes))
  [secondary summaries].
- Our posture, binding for every phase in §6: **no frame leaves the Pi; no frame is written to
  disk, ever; the tick emits numbers only** (`motion_score`, `face_count`, `person_count`,
  `best_face_px`) — never an embedding, never an identity (identity stays on the wake-word
  path the person chose to trigger); **a visible "eye" dot on the panel whenever the camera stream
  is open**, and a one-tap "camera off" that is honoured in the daemon, not the UI; **BLE stores
  one key/MAC in the Pi's env**, never in Postgres, never in a log line; the Jetson keeps **last
  state only** (no event table — 0028 stays dropped); the Pi keeps a 7-day rotating JSONL of
  scores for the measurement, then deletes it.
- The emotional-safety / privacy note (one paragraph, panel settings + Telegram link, written
  before the first flag flips): *"Zoe can tell whether someone is in front of the panel, not who
  — unless you enrolled your face and said 'Hey Zoe'. Nothing is recorded: no photo, no video, no
  clip, only a number from 0 to 1 that fades in a minute. The eye dot is on whenever the camera is
  looking; tap it to turn the camera off. Your phone is noticed only if you gave Zoe its key, and
  only as 'near this panel'."* And the two promises the fusion enforces (§3.4): Zoe never
  decides "nobody's here" in order to say something private, and never decides "someone's here"
  in order to look harder.

## 3. Design — two scores, noisy-OR, decay, two fail-safe directions

### 3.0 First: what we are NOT building

No new table. No speech on presence. No identity from the camera tick. No continuous video.
No key or MAC in the repo. No change to `brief_first_turn` (already pull-shaped). No change to
the direction the sleep gate fails. Bermuda / ESPHome proxies only if a second room appears.

### 3.1 Flags (all default OFF, read per call; Pi env unless prefixed `ZOE_`)

| Flag | Where | Effect |
|---|---|---|
| `PRESENCE_TICK_ENABLED` | Pi daemon | open the camera stream (with the TTS guards and the re-open hysteresis), frame-diff at 1 fps, face detect on motion; **shadow**: scores to `~/.zoe-voice/presence_metrics.jsonl` and `/health`, no POST |
| `PRESENCE_TICK_POST` | Pi daemon | POST the score to `/api/panels/{id}/presence` (device token) |
| `PRESENCE_TICK_FPS` / `PRESENCE_DETECT_EVERY_S` | Pi daemon | 1.0 / 10 — detector cadence when there is no motion |
| `PRESENCE_CAMERA_QUIET_AFTER_TTS_S` / `PRESENCE_CAMERA_MIN_REOPEN_S` | Pi daemon | 5 / 30 — the hysteresis from §2.1 |
| `PRESENCE_BLE_ENABLED`, `PRESENCE_BLE_IRK` **or** `PRESENCE_BT_CLASSIC_MAC` | Pi env only | the scanner; one of the two identifiers, never both logged |
| `ZOE_PRESENCE_SOURCES` | zoe-data | comma list of producers the fusion may read: `session,touch,voice,music,room,camera,ble,ha` (the identity plan's name, `:113`); empty = today's behaviour |
| `ZOE_SLEEP_GATE_PRESENCE` | zoe-data | the third vote in `resolve_sleep_gate` from `anyone_here` (and from a `binary_sensor` occupancy entity if one ever exists) |
| `ZOE_PRESENCE_ORB_GATE` | zoe-data | the P1 orb "has something" state requires `owner_near ≥ 0.7` |

### 3.2 Signals

| # | Signal | Producer (exists?) | Says | p when fresh | half-life |
|---|---|---|---|---|---|
| S1 | touch (`pointerdown`, orb tap) | kiosk → `/api/ui/state/sync` would need a `last_touch_at` field (today: nothing) | someone **at** the panel | 0.98 | 120 s |
| S2 | voice (wake POST, turn, follow-up VAD speech) | daemon `/api/voice/wake` (exists), turn (exists), VAD-in-window (new: one counter) | someone in the room | 0.95 | 300 s |
| S3 | identity-confirmed session | `panel_presence_tier == owner` (exists) | the member, at the panel | 0.9 (owner) | the 900 s window |
| S4 | music playing | `/api/music/now-playing` (exists, already a vote) | the room is in use | 0.6 | none (live) |
| S5 | room toggles on | `resolve_sleep_gate` (exists, already a vote) | someone is up | 0.5 | none (live) |
| S6 | camera tick | **new** (daemon) | motion in view / faces in view | 0.7 motion, 0.9 face, 0.95 two frames in a row | 60 s |
| S7 | phone near (BLE IRK / classic paging RSSI) | **new** (daemon or sibling unit) | the **owner's** phone within a few metres | 0.8 at RSSI ≥ −70 dBm, 0.5 at ≥ −85 [tune in §5] | 180 s |
| S8 | phone home (HA `device_tracker` / SSID) | **operator**: companion app; reader = `/api/ha/entities?domain=device_tracker`, 30 s TTL (identity plan `:85`) | the owner is in the house | 0.4 | none (live, lagging) |
| S9 | mmWave occupancy (if bought) | HA `binary_sensor` | someone in the room, still or moving | 0.95 | 30 s |

### 3.3 Fusion

Per panel, two numbers recomputed on read (no scheduler), from the last-state dict:

```
p_i(t)      = p_i · 0.5 ** ((t − t_i) / halflife_i)          # per-signal decay
anyone_here = 1 − Π_i (1 − p_i(t))      over S1,S2,S4,S5,S6,S9      # noisy-OR
owner_near  = 1 − Π_j (1 − p_j(t))      over S3,S7, and S2 when the turn carried an
                                        accepted owner voice claim; S8 as a prior
                                        (owner_near ≤ 0.5 unless S8 says home or S8 is absent)
```

Noisy-OR because every signal is *evidence for* presence and none is evidence *against* it
(a camera that sees nobody says "nobody in view", not "nobody in the room"); it is monotone,
explainable in one line, and degrades gracefully when a producer is off (a missing term is 0).
Absence is only ever *decay*. The identity plan's `C ← max(C_decayed, fused)` with exponential
decay (`:99`) is the same idea for one signal; this is its many-signal form. Bands: `≥ 0.7`
present, `0.3–0.7` unsure, `< 0.3` absent — the same three-band shape the plan uses for identity.

### 3.4 Fail-safe — the two directions, stated once

| If the system could… | …then on "unsure" it assumes | So that it never |
|---|---|---|
| **speak** (announcement, brief, orb content, step-up) | **someone is listening, and not necessarily the member** — treat as `bound_guest` | says a private thing to an empty-looking room that is not empty |
| **capture** (open the camera, raise the tick rate, run the detector, ambient audio) | **nobody consented / nobody is there** — stay at the minimum duty | escalates looking because it thought it saw someone |
| **sleep** (the night clock) | **nobody is there** — the existing direction, unchanged | latches awake on a hung request (the 2026-07 class) |

Concretely: `_maybe_speak_notification` keeps reading `panel_presence_tier` (identity-confirmed)
and `anyone_here` can only *demote* (two faces ⇒ guest-safe), never promote; the camera tick's
detector runs on motion or every 10 s — `anyone_here` never shortens that; the sleep gate's third
vote is `anyone_here ≥ 0.7` **and** the score is fresher than 60 s, else the vote is "no".

### 3.5 Where it lives

- Pi: one `presence_tick.py` beside `zoe_face_id.py` (reuses `detect_faces`, `pick_best_face`,
  `MIN_FACE_PX`; one long-lived `VideoCapture` under the existing `_camera_lock`; the three busy
  guards from ambient capture; the re-open hysteresis), one `presence_ble.py` (bleak passive
  scan with an `or_pattern` on the resolved address, or a `hcitool`-style page by MAC every 6 s),
  both as daemon threads behind their flags, both writing the JSONL and the `/health` fields.
- Jetson: `services/zoe-data/presence_fusion.py` — **pure functions** (signals in, two scores
  out, injectable clock; table-driven `ci_safe` tests: each signal alone, decay, the two fail-safe
  rows, a stale score, HA unreachable) — plus a last-state dict keyed by `panel_id` behind
  `POST /api/panels/{id}/presence` (device-token, scores only, 413 anything with an image field)
  and a read used by `resolve_sleep_gate`, the P1 orb and `arrival`'s gate
  (`tier == owner` **and** `anyone_here` not contradicting it with two faces).

### 3.6 The one policy sentence to add

[biometric-retention-policy.md](../knowledge/biometric-retention-policy.md) scopes out ambient
capture; it needs a third scope: *occupancy scores are activity data, not biometrics; they are
numbers, kept 7 days on the panel for measurement and as last-state only on the server; no
frame is ever stored or transmitted.* Same shape as the `panel_presence_events` retention note
the 0028 migration carries.

## 4. RAM / CPU budget per box

| Box | Component | RSS | CPU | Basis |
|---|---|---|---|---|
| **Pi 5** (5.65 GB free, ~⅓ core busy — [speaker-gate §2.5](speaker-gate-rebuild-2026-10-04.md)) | `VideoCapture` stream at native res, 1 grab/s | ~10–30 MB buffers [unverified] | <1 % | V4L2 copy only |
| | greyscale diff at 320×240 | 0 | ~2–5 ms/s [estimate] | Frigate's own method |
| | YuNet at 160×120 on motion | +~5 MB model, +~30 MB session [unverified] | 6.23 ms per motion frame on a Pi 4B ([opencv_zoo](https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md)) | measured |
| | SCRFD det_500m at 320 (alternative; already fetched) | ~15 MB models, +~50–80 MB session [unverified] | ~30–60 ms per motion frame [estimate] | 11.4 ms at 320×240 on a desktop core |
| | bleak passive scanner | ~25–40 MB (Python + dbus) [unverified] | ~0 | idle D-Bus listener |
| | classic paging by MAC | 0 (subprocess) | ~0; **radio** shared with Wi-Fi | room-assistant |
| | **USB current** | — | the real budget: 600 mA → 1.6 A | §2.1 |
| **Jetson** (MemAvailable 0.42–0.56 GB, [memory-pressure profile](../knowledge/memory-pressure-profile-2026-10-03.md)) | fusion + last-state dict | ~0 (one dict, pure math) | ~0 | no model, no table |
| | HA device_tracker read | 0 | one `/entities` pull per 30 s when `ha` is in `ZOE_PRESENCE_SOURCES` | `_entity_index` already pulls the full list per sleep-gate call |
| **Phone** | BLE advertising it already does (iOS) / classic page replies / companion app | — | "minor hit" (classic paging), "can impact battery" (Android transmitter) | room-assistant, companion docs |

Total Jetson cost: zero models, zero tables, one dict. Total Pi cost: well under 150 MB and a
few percent of one core — if the power budget holds, which is E1.

## 5. Measurement plan — one week, the owner's phone and the panel, nothing leaves the box

Everything below is logged to `~/.zoe-voice/presence_metrics.jsonl` on the Pi (scores,
timestamps, counters — no frames, no addresses) and summarised by a script that prints
aggregates; only the aggregates are pasted into the closeout record. Targets are stated here,
before measuring (the "verify your instruments" rule: every claim gets a negative control that
must go red).

| # | Experiment | Measures | Target | Negative control |
|---|---|---|---|---|
| **E1 — power** (first, alone) | with the stream open: 50 TTS replies + 20 announcements over two days, on (a) the current supply, (b) a 5 A PD supply / `usb_max_current_enable=1` or a powered hub | `dmesg` USB disconnect count; `vcgencmd pmic_read_adc` rails [unverified on this image]; daemon camera-open failures | **0 disconnects in 48 h** on (b) | (a) must reproduce ≥1 disconnect, or the trap is not what we think and the whole premise is cheaper than feared |
| **E2 — the tick as a presence instrument** | one week shadow: `motion_score`, `face_count` at 1 fps | lead time: how many seconds before each touch/voice event the score was already ≥ 0.7; false-present minutes while the phone geofence says away and no touch/voice for ≥ 30 min | lead ≥ 20 s on ≥ 80 % of touches; false-present < 2 % of away minutes | lens covered for one day ⇒ motion and faces must read 0 (a stale frame buffer would not) |
| **E3 — faces vs persons** | for every motion event, did a face appear within 10 s? | share of "someone there" episodes with no face | if > 30 % lack a face, add the person detector in phase 2 | — |
| **E4 — BLE / paging** | RSSI every 6 s; iPhone idle vs screen-on vs settings page open | detection rate while the owner is at the panel (touch as ground truth); RSSI at 1 m / 3 m / next room | present ≥ 90 % of panel-touch minutes; next-room RSSI distinguishable by ≥ 10 dB | phone BT off for an hour ⇒ absent within one half-life; a second household phone must **not** match (IRK) |
| **E5 — sleep card** | shadow the third vote for a week: minutes the card *would* have been blocked by `anyone_here` while the room was dark and silent | the owner's complaint ("the sleep card keeps coming up") measured, not felt | ≥ 1 avoided false-sleep per day, 0 latch-awake incidents | force the Pi POST to fail for a day ⇒ the gate must fall back to today's two votes |
| **E6 — Wi-Fi/companion** | HA `device_tracker` state vs phone-at-panel truth | lag of home→away and away→home; false-away minutes while touching the panel | false-away < 5 % | airplane mode ⇒ away within `consider_home` |

Pi guard rails during the week: CPU and RSS of the daemon sampled per minute; the replay gate
re-run once with the tick on (no regression in STT/TTFA); the voice stack's memory protection
drop-ins untouched.

## 6. Phased build — flag-dark, each phase its own PR

0. **Prerequisite (operator, no code):** E1. Buy nothing yet; if the current supply is 3 A, the
   5 A PD supply (~A$20) or a powered hub is the cheapest "sensor" in this record. Decide the
   phone question (Decision 2). Write the privacy note (§2.5) and the policy paragraph (§3.6).
1. **PR 1 — shadow tick + fusion math.** `presence_tick.py` (motion diff, YuNet-or-SCRFD on
   motion, JSONL, `/health` fields, the hysteresis), `presence_fusion.py` pure + tests, the
   eye-dot CSS state on the orb (hidden until the daemon reports `camera_open`), a `camera off`
   control honoured by the daemon. Flags: `PRESENCE_TICK_ENABLED`. Nothing POSTs. Runs E2/E3.
2. **PR 2 — the third vote.** `POST /api/panels/{id}/presence` (scores only), last-state dict,
   `resolve_sleep_gate` reads `anyone_here` behind `ZOE_SLEEP_GATE_PRESENCE`, with the
   `binary_sensor` occupancy device-class branch written at the same time (so a bought sensor is
   a config change, not a PR). `test_panel_sleep_gate.py` gains the stale-score and
   POST-failure rows; the browser gate gains the third request in the race. Runs E5.
3. **PR 3 — owner near.** `presence_ble.py` (one identifier in Pi env), `owner_near`, the P1 orb
   gate (`ZOE_PRESENCE_ORB_GATE`), `arrival`'s two-faces demotion. Runs E4.
4. **PR 4 — owner home.** `ha` in `ZOE_PRESENCE_SOURCES`: a 30 s-TTL reader of
   `device_tracker` + SSID; the `person` entity gets its tracker (operator). Runs E6.
5. **Later, only if a second room appears:** Bermuda + an ESPHome proxy per room (the
   Everything Presence Lite is both proxy and mmWave).

Each PR: byte-identical with its flags off; one day-sim ask where the sim can see it (the orb
gate and the sleep gate can be driven synthetically); `flag-inventory.md` regenerated.

## 7. Go / no-go against the VISION principles

| Principle | Verdict | Why |
|---|---|---|
| 1 Rocks fixed | GO | no model on the Jetson; the Pi gets a 100 KB face detector or reuses the one it has |
| 2 Local, private, fast | GO with the §2.5 posture | nothing leaves the Pi but numbers; the hot path is untouched; the eye dot makes it visible |
| 3 Lab-prove before prod | GO | seven flags, default off; E1 before any stream is opened on the live panel; a shadow week before any vote |
| 4 Build it to STICK | GO | pure fusion with table tests; the sleep-gate tests extended in the same direction they already fail; negative controls per experiment |
| 5 Capture, don't lose | GO | the owner's complaint becomes a counter (E5); P8 moves from a pin to a plan |
| 6 Borrow the piece | GO | Frigate's motion-first, the identity plan's decay, Bermuda's IRK path, room-assistant's paging — no framework |
| 7 Right tool, right place | GO | the camera module, the ambient guards, the sleep-gate resolver and `panel_presence_tier` all exist; the Pi does the work |
| 8 Voice first, touch second, no keyboard | GO | nothing to type; the only new tap is "camera off" |
| 9 Understand before you change | this record | every consumer traced; the face module's missing caller and the orphan JS found before design |
| Owner rule: never speaks unprompted | GO, strengthened | presence can only *demote* speech (guest-safe) and gate the orb; no path here enqueues an announcement |

**No-go items inside the idea:** continuous video at any fps on a 600 mA budget; a person
detector before E3 says faces miss too much; any identity from the tick; an event table; an IRK
or MAC anywhere but the Pi env; Bermuda before a second room; ultrasound.

## 8. Decisions for Jason

1. **Buy one mmWave sensor instead?** For about **A$38** (Sonoff P24, if there is a Zigbee
   coordinator) or **A$80** (Everything Presence Lite, Wi-Fi, also a Bluetooth proxy) the panel
   gets "someone is in this room, even sitting still in the dark" with no camera duty, no USB-power
   risk and no phone key; our sleep-gate code is a few lines from using it. The camera + phone
   path costs no money but costs a power-supply check, a week of measurement and the bystander
   question. My recommendation: **sensor for "anyone here", phone for "owner near", camera stays
   wake-word-only** — unless you want the camera path for its own sake.
2. **Which phone, and may Zoe have its key?** iPhone ⇒ the IRK comes from a Mac's Keychain (or a
   one-off pairing to a A$10 ESP32); Android ⇒ no key, only the companion app's beacon (battery
   cost) or classic-Bluetooth paging by MAC (no app, "minor" battery hit, occasional false
   "away"). The key or MAC lives only in the Pi's env file. Without this answer there is no
   "owner near".
3. **Install the Home Assistant companion app on your phone?** Zero repo change, fixes the
   `unknown` person entity, gives "home / away" and "on home Wi-Fi" to the orb and to identity.
   It is a location-sharing decision, so it is yours.
4. **Is the panel's supply 5 A, and may we add a powered hub?** Every camera plan in this record
   is conditional on E1 passing. If the answer is "3 A and no hub", the camera half of Q19 is a
   no-go until that changes, and Decision 1 becomes the whole plan.

## 9. Next steps if GO

1. Operator: E1 (power) on the live panel — two days, `dmesg` counts, nothing else changes.
2. Operator: Decisions 2–3; the key/MAC into the Pi env (never pasted into chat or a PR).
3. PR 1 (shadow tick + fusion math + eye dot + camera-off), deployed from a worktree to the Pi
   per the panel deploy recipe; shadow week; E2/E3 aggregates into a closeout record under
   `docs/knowledge/`.
4. PR 2 (third vote, with the `binary_sensor` branch pre-written) — or, if Decision 1 is "buy",
   skip PR 1 and ship PR 2's `binary_sensor` branch alone with the sensor bound to the panel's
   room in `room_devices`.
5. PR 3 / PR 4 in that order; Bermuda only with a second room.
6. Update [IDEAS.md](../IDEAS.md) P8 and the B4.5 row of the beat-the-bar tracker to point here.

## 10. Sources

**Our tree (read-only):** `scripts/setup/zoe_voice_daemon.py`, `scripts/setup/zoe_face_id.py`,
`scripts/setup/zoe_enroll_flow.py`, `scripts/setup/fetch_face_models.sh`,
`scripts/setup/pi-requirements.txt`, `services/zoe-data/proactive/presence.py`,
`services/zoe-data/proactive/arrival.py`, `services/zoe-data/proactive/engine.py`,
`services/zoe-data/brief_first_turn.py`, `services/zoe-data/routers/ui_actions.py`,
`services/zoe-data/routers/panel_config.py`, `services/zoe-data/routers/rooms.py`,
`services/zoe-data/routers/ha_control.py`, `services/zoe-data/routers/face_id.py`,
`services/zoe-data/routers/system.py`, `services/zoe-data/alembic/versions/0028_drop_panel_presence_events.py`,
`services/zoe-data/tests/test_panel_sleep_gate.py`, `services/zoe-ui/dist/touch/home.html`,
`services/zoe-ui/dist/js/touch-ui-executor.js`, `services/zoe-ui/dist/touch/js/presence-detection.js`,
`services/zoe-ui/dist/test_touch_sleep_gate.js`, `services/homeassistant-mcp-bridge/main.py`,
`homeassistant/configuration.yaml`, `docs/architecture/panel-identity-plan.md`,
`docs/architecture/samantha-evolution-plan.md`, `docs/architecture/omi-integration-plan.md`,
`docs/knowledge/feature-audit-2026-09-25.md`, `docs/knowledge/biometric-retention-policy.md`,
`docs/knowledge/synthetic-users-and-proactive-recipients.md`,
`docs/knowledge/memory-pressure-profile-2026-10-03.md`, `docs/research/pull-not-push-inbox-2026-10-04.md`,
`docs/research/speaker-gate-rebuild-2026-10-04.md`, `docs/research/companion-field-vs-samantha-2026-10-03.md`,
`docs/IDEAS.md`.

**Camera / detectors**
- Frigate motion detection — https://docs.frigate.video/configuration/motion_detection
- Frigate camera setup (5 fps, 320×320) — https://docs.frigate.video/frigate/camera_setup
- Frigate object detectors (CPU not recommended, Coral ~10 ms) — https://docs.frigate.video/configuration/object_detectors/
- OpenCV Zoo benchmark (YuNet 6.23 ms, MPPersonDet 105.6 ms on Pi 4B) — https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md
- Ultralytics Raspberry Pi guide (YOLO26n Pi 5: NCNN 67 ms, ONNX 126 ms; YOLO11n 6.79 FPS) — https://docs.ultralytics.com/guides/raspberry-pi/
- insightface model zoo (SCRFD-500MF 28.3 ms single-thread @640×480; buffalo_sc) — https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md
- MediaPipe EfficientDet-Lite0 on Pi 5 (~35 ms, blog) — https://jeffzzq.medium.com/object-detection-on-the-raspberry-pi-5-463ba0f11d1e
- Raspberry Pi documentation (Pi 5 USB 600 mA / 1.6 A, `usb_max_current_enable`, Bluetooth 5/BLE) — https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- Jabra PanaCast 20 power FAQ (>500 mA or USB 3.0) — https://www.jabra.com/supportpages/jabra-panacast-20/8300-119/faq/Do-I-need-a-separate-power-adapter-for-the-Jabra-PanaCast-20

**Bluetooth / BLE**
- Bluetooth SIG, randomized RPA updates — https://www.bluetooth.com/blog/enhancing-device-privacy-and-energy-efficiency-with-bluetooth-randomized-rpa-updates/
- Novel Bits, BLE address privacy (15-minute rotation on iOS) — https://novelbits.io/bluetooth-address-privacy-ble/
- Argenox, demystifying BLE addresses — https://argenox.com/library/bluetooth-low-energy/demystifying-ble-addresses
- Home Assistant Private BLE Device (IRK; iOS Keychain; Android methods) — https://www.home-assistant.io/integrations/private_ble_device/
- Home Assistant companion app sensors (BLE transmitter Android-only; SSID sensor) — https://companion.home-assistant.io/docs/core/sensors/
- Home Assistant companion app location (significant change, geofence, iBeacon) — https://companion.home-assistant.io/docs/core/location/
- Bermuda BLE trilateration — https://github.com/agittins/bermuda
- Derek Seaman, ESPHome + Bermuda guide (2025-12) — https://www.derekseaman.com/2025/12/home-assistant-track-whos-in-each-room-with-esphome-bermuda-ble.html
- irk-capture (ESPHome IRK capture) — https://github.com/DerekSeaman/irk-capture
- ESPresense enrolling devices (iOS pairing, Android no IRK) — https://espresense.com/guides/enrolling-devices/
- room-assistant Bluetooth Classic (paging by MAC, 6 s, battery, shared antenna) — https://www.room-assistant.io/integrations/bluetooth-classic.html
- room-assistant issue #270 (iPhone BLE only while scanning) — https://github.com/mKeRix/room-assistant/issues/270
- Home Assistant Bluetooth integration (adapters, Docker D-Bus, BlueZ versions, passive scan) — https://www.home-assistant.io/integrations/bluetooth/
- Home Assistant bluetooth_tracker (removed) — https://www.home-assistant.io/integrations/bluetooth_tracker/
- bleak passive scanning discussion — https://github.com/hbldh/bleak/discussions/1612

**Wi-Fi / HA**
- Home Assistant ping tracker (consider_home 180 s; phones turn off Wi-Fi) — https://www.home-assistant.io/integrations/ping/
- Home Assistant nmap tracker — https://www.home-assistant.io/integrations/nmap_tracker/
- Home Assistant default_config contents — https://www.home-assistant.io/integrations/default_config/
- Home Assistant person — https://www.home-assistant.io/integrations/person/

**Sensors and prices**
- ESPHome LD2410 component — https://esphome.io/components/sensor/ld2410/
- LD2410 + ESPHome guide (2026, cost) — https://bishalkshah.com.np/blog/esp32-mmwave-presence-sensor-home-assistant ; DIY mmWave — https://calvin.me/diy-mmwave-presence-detectors/
- Everything Presence Lite (US$38) — https://shop.everythingsmart.io/products/everything-presence-lite ; AU price — https://www.pakronics.com.au/products/everything-presence-lite-ss114993300 ; CNX overview — https://www.cnx-software.com/2025/05/08/everything-presence-lite-esp32-based-mmwave-presence-sensor-tracks-up-to-three-targets-simultaneously/
- Sonoff SNZB-06P24 (US$24.90) — https://sonoff.tech/en-us/products/sonoff-senseguard-presence-core-24ghz-zigbee-human-presence-sensor-snzb-06p24 ; AU — https://www.amazon.com.au/SONOFF-SNZB-06P-Microwave-Precision-Assistant/dp/B0CNGKFG9Y
- Aqara FP2 (US$82.99) — https://us.aqara.com/products/presence-sensor-fp2 ; AU — https://www.smarthome.com.au/product/aqara-fp2-presence-sensor/
- Amazon Science, ultrasonic motion sensing for Echo — https://www.amazon.science/blog/the-science-behind-ultrasonic-motion-sensing-for-echo

**Privacy**
- Google Nest, familiar face detection (on-device storage on newer models) — https://support.google.com/googlenest/answer/9268625
- Windl et al. 2022, The Skewed Privacy Concerns of Bystanders in Smart Environments — https://www.medien.ifi.lmu.de/pubdb/publications/pub/windl2022theskewed/windl2022theskewed.pdf
- Bystander Privacy in Smart Homes: A Systematic Review (ACM 2025) — https://dl.acm.org/doi/10.1145/3731755
- Marky et al., Users' and Bystanders' Preferences of Privacy Awareness Mechanisms in Smart Homes — https://www.researchgate.net/publication/360331111_It_would_probably_turn_into_a_social_faux-pas_Users'_and_Bystanders'_Preferences_of_Privacy_Awareness_Mechanisms_in_Smart_Homes
