---
type: research
title: Lane G - embodied and environmental intelligence, beyond-existing-technology questions, distributed cognition
date: 2026-11-03
status: lane report
description: Samantha Initiative lanes 2.6, 4.6, 9 and the distributed part of 10. How Zoe can perceive the home (presence, rooms, sound, vision), what a home world model should and should not be, which device does what, what survives without internet, and whether many small specialised models, a coordinator, memory and stored experience beat one bigger model. Research only; nothing built or changed.
evidence: docs/architecture/identity-presence-engine.md, docs/research/presence-without-sensors-2026-10-04.md, docs/research/stack-currency-and-field-2026-10-27/lane-Q-field.md, docs/research/synthesis-deep-dive-2026-10-29.md, docs/knowledge/little-gemma-engine-bench-2026-10-10.md, docs/architecture/tv-display-panel.md, docs/architecture/zoe-iphone-app.md, docs/VISION.md; live ground-truth read 2026-11-03; web pages cited inline
---

# Lane G: embodiment, environment and distributed cognition (2026-11-03)

Labels: **[Proven]** = shipped, repeatedly reproduced, or measured on Zoe's own box. **[Experimental]** = works in published or community settings, not shown in Zoe's estate. **[Speculative]** = plausible, unmeasured. Source tags: *read* = I read the page or repo doc; *search* = only a search summary (several web searches returned secondary summaries; those are flagged and nothing load-bearing rests on them alone). No source code of the external projects was inspected in this lane; "source inspected?" below is therefore "no" unless stated.

## 0. What Zoe should take from this

1. **Presence is the cheap, high-value sense; embodiment is not.** A A$5-40 mmWave sensor plus the phone-near signal already designed in `identity-presence-engine.md` gives more perceived awareness than any camera or robot work. [Proven for the sensors in the wild; Zoe-side wiring is unbuilt.]
2. **Zoe consumes no Home Assistant presence entity today** (design doc section 1: no `person`, `device_tracker` or `binary_sensor` is read). The first step is reading what already exists, not buying anything. A read-only `person`/`device_tracker` reader with a 30 s TTL is hours of work.
3. **Fuse in a small, explainable score, not a model.** The engine's capped, decayed log-likelihood-ratio design is the right shape; Home Assistant's own Bayesian sensor is the same idea and is the benchmark to compare against. No model on the Orin: the design doc's rule ("no source may add a model to the Jetson") stands and this lane agrees with it.
4. **Multi-room voice arbitration is an unsolved gap upstream.** Home Assistant has no native "nearest satellite wins"; a third-party kiosk app only just added a 100-500 ms arbitration window (search, 2026-10). Zoe has one real voice panel, so the problem is not yet hers; when a second listening device appears (TV is display-only by design, the phone is not a room), arbitrate on the server by wake-word score plus VAD energy within a short window. [Experimental]
5. **Audio event detection belongs on the Pi, as a bounded classifier that emits labels, never audio.** YAMNet-class models (Apache-2.0 reported, 521 AudioSet classes, MobileNet v1) run on a Pi CPU. Candidate uses are door/knock/alarm/glass/dog/crying-class labels as *inferred* events. Do not build before there is a household use that someone asked for. [Experimental]
6. **Local vision stays at the panel and stays tiny.** Face ID and distance-from-face-box already exist or are designed. Frigate-style whole-home camera NVR is a different product (cameras, storage, retention duties) and is **not** recommended; the AI HAT+ 2 is a possible later Pi accelerator but is a purchase to justify, not a need. [Proven that it works elsewhere; unjustified for Zoe now.]
7. **Embodiment (robots, VLAs) is research for Zoe, not engineering.** Public VLAs are impressive in their labs and weak in unconstrained homes; the closest useful thing is Zoe *driving existing actuators* (lights, locks, TV, speakers) through Home Assistant, which she already does. [Proven (HA actuation); VLA household autonomy not achievable today.]
8. **Distributed cognition: split by role, not by layers.** Sharding one model across devices (exo, Petals) trades latency for capacity and is wrong for a 14-token-reply voice assistant. Split by *task*: the Pi senses and gates, the Orin answers, the Spark does night and development work, the phone carries identity and away-from-home. [Proven pattern; sharding rejected for Zoe.]
9. **Several small specialised models already beat one big one for most of Zoe's turns, and Zoe already proves it** (router 270M beats the 4B at tool choice per the repo's own measurement; Moonshine, Kokoro, bge-small). The open question is not "one model or many" but "what is the next specialist that earns its RAM", and today the answer is *none on the Orin*, since ~500 MB is free in waking hours. [Proven for router; rest Experimental]
10. **Memory and stored experience can add more perceived intelligence per megabyte than parameters**, and the repo's own synthesis found recall is built but not firing (83% of real replies used zero memory sources). Fix that before any new perception. Skill libraries/cached plans (Voyager, Agent Workflow Memory, ExpeL) are the proven no-retraining route. [Experimental in assistants; Proven in their papers' benchmarks.]

## 1. Projects and papers

All entries: verdict is for *Zoe's estate and principles* (local, private, fast; rocks fixed; adopt-do-not-rebuild; pull-not-push). "Src?" = source inspected in this lane (no, throughout). Licences marked [unverified] were not read on the licence file.

### 1.1 Environmental perception

| Project | Purpose and architecture | Maturity | Strengths / weaknesses | Hardware, cost, licence | Benefit to Zoe | Verdict |
|---|---|---|---|---|---|---|
| [Home Assistant event model and `person`/`device_tracker`](https://www.home-assistant.io/integrations/person/) (read in repo docs; not re-fetched) | State machine plus event bus; `person` merges trackers into home/away; Zoe's HA is already a hidden organ | [Proven] | Free, local, already running. Phone trackers are coarse (home/away, zones) and depend on the companion app. | none; Apache-2.0 (home-assistant/core, verified) | Zero-cost first presence signal for the engine | **Adopt** (read-only) |
| [Bayesian binary sensor (HA)](https://www.home-assistant.io/integrations/bayesian/) (search) | Combines several binary observations with priors into one probability | [Proven] | Simple and inspectable, but static priors and binary inputs; no decay, no per-person identity | in HA | The comparison baseline for Zoe's own fusion; can host room-level occupancy inside HA | **Adapt** (use for room occupancy; keep identity in Zoe) |
| [mmWave presence: LD2410 / LD2450 class](https://community.home-assistant.io/t/hlk-ld2450-initial-experiments-to-connect-to-homeassistant/578878) (search, forum) | 24 GHz radar over UART to an ESP32 (ESPHome); LD2450 gives XY for up to three targets and zones; LD2410 is binary | [Proven] for occupancy; [Experimental] for zones | Detects stillness (PIR cannot); privacy-friendly (no image). LD2450 reports ghost targets and confuses a second person after the first leaves; community advice is to pair it with a still-presence sensor | A$5-40 per module + ESP32; licence n/a | Sleep gate, "anyone here", walk-away lock, lights; matches the design doc's A$38-80 row | **Adopt** one in the main living room first |
| [ESPresense](https://espresense.com/) (search) | ESP32 nodes scan BLE adverts, estimate distance, publish via MQTT; strongest node = room | [Experimental] | Whole-home room-level device tracking; per-room calibration, phones randomise MACs (need IRK/identity beacon), many nodes to place | ESP32 ~A$10 each, MQTT broker; AGPL-3.0 (verified) | Which room a phone is in, if households want it | **Experiment** later; single panel does not need it |
| [Frigate NVR](https://frigate.video/) / [repo](https://github.com/blakeblackshear/frigate) (search; Jetson tag is community-supported, TensorRT issues reported) | Local NVR with object detection on RTSP cameras, MQTT events to HA | [Proven] for camera setups | Good local person/pet/car events; needs cameras, storage, retention and consent policy, and the Orin has no spare RAM | cameras, Coral/GPU; MIT (verified) | Not needed for presence; high privacy burden | **Reject** for now |
| [Kiosk-satellite wake-word arbitration](https://newreleases.io/project/github/jxlarrea/kiosk-satellite/release/2026.10.1) (search, release notes) | Each kiosk waits 100-500 ms for others; only the nearest answers | [Experimental] (days old) | Shows the pattern and that HA itself lacks it | n/a | Pattern for a second listening device | **Adapt the idea**, do not adopt the app |
| [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) (search: MathWorks Pi example, a 2023 Pi paper) | MobileNet v1 on AudioSet, 521 classes, 16 kHz | [Proven] as a model; [Experimental] as a household event sensor | Tiny, CPU-feasible on a Pi; AudioSet labels are noisy and not tuned to a home; smoke-alarm/glass classes need testing in situ | Pi CPU; Apache-2.0 (verified: repo-root LICENSE covers /research) | Door/knock/alarm labels as inferred events | **Experiment** (E4) |
| [Raspberry Pi AI HAT+ 2](https://www.hackster.io/news/gen-ai-on-your-raspberry-pi-a-hands-on-review-of-the-raspberry-pi-ai-hat-2-3c829a8894dd) (search; no verified LLM benchmark found) | Hailo-10H, 40 TOPS INT4 (20 INT8), 8 GB onboard, PCIe, USD 130 list, retail often higher | [Proven] for vision; LLM/VLM claims unverified | Offloads vision from the Pi CPU without touching the Orin; adds PCIe/power/heat load on a wall panel that already has a USB power budget problem | USD 130-225; Hailo SDK terms [unverified] | Only if face/vision becomes real | **Reject** until a vision job exists |

### 1.2 Spatial awareness and arbitration

| Project | Purpose | Maturity | Notes | Verdict |
|---|---|---|---|---|
| Alexa/Google/Apple multi-device arbitration (proprietary; HomeView's six-person face recognition is in the repo's lane-Q, 2026-10-22) | Cloud or on-device mic-energy comparison | [Proven] commercially | Closed; nothing to inspect. Pattern: energy + SNR over a short window. | **Adapt the pattern** |
| [Home Assistant Assist satellites / Wyoming](https://community.home-assistant.io/t/wyoming-protocol-satellite/690571) (search) | Per-room satellites with area binding; each wakes independently | [Proven] single room; [Experimental] multi-room | Area awareness (which room asked) is a core strength; no nearest-wins; workarounds are mute switches or per-room wake words | **Adapt**: Zoe's panel already carries a room; use the room as context for tools ("the lights" = this room) |

### 1.3 Embodiment and robotics

| Project | Architecture | Maturity | Weaknesses and licence | Verdict |
|---|---|---|---|---|
| [RT-2](https://arxiv.org/abs/2307.15818), [SayCan](https://arxiv.org/abs/2204.01691) (arXiv, known) | LLM/VLM maps instruction to affordance-scored skills or action tokens | [Experimental] in lab kitchens | Skills pre-trained per robot; no household reliability claim | **Reject** for Zoe |
| [OpenVLA](https://arxiv.org/abs/2406.09246), [pi0](https://arxiv.org/abs/2410.24164) (arXiv, known; search summary only for numbers) | Open 7B VLA; flow-matching action model | [Experimental]; fine-tuned per embodiment | Needs a robot, teleop data and GPU; one summary claims OpenVLA beats a 55B closed model by 16.5 points on 29 tasks, which is not a household benchmark | **Reject** (watch) |
| [Gemini Robotics On-Device / v2](https://deepmind.google/models/model-cards/gemini-robotics-on-device-2/) (search, official page title) | Local VLA for DeepMind-supported robots | [Experimental]; vendor benchmarks; trusted-tester access | A per-platform success curve (6.7% to 53.3% on one small arm) is the honest scale; not a household metric | **Reject**; shows that "household robot" is a research claim |
| [Voyager](https://arxiv.org/abs/2305.16291) (search) | Skill library of executable code, iterative prompting with environment feedback | [Experimental]; Minecraft only | The *idea* (stored, verified, reusable skills) transfers to non-physical assistants | **Adapt** (see 4.4) |

### 1.4 Distributed and edge intelligence

| Project | Purpose, architecture | Maturity | Weaknesses | Verdict |
|---|---|---|---|---|
| [exo](https://github.com/exo-explore/exo) (search summaries; Tom's Hardware piece on disaggregated prefill/decode) | P2P ring of devices, layers split by memory; "adding weaker devices lowers single-request latency but raises throughput"; the 2.8x demo splits prefill (compute-bound) from decode on Spark + Mac Studio | [Experimental]; self-described alpha | Network-bound per token; designed to fit models too big for one box, which Zoe does not need; Apache-2.0 (verified) | **Reject** sharding; **note** prefill/decode disaggregation as a Spark-era idea |
| [Petals](https://arxiv.org/abs/2209.01188) (arXiv, known) | Collaborative inference over the internet, volunteer GPUs | [Experimental] | Sends activations off-box; violates local/private | **Reject** |
| Speculative decoding ([Leviathan 2022](https://arxiv.org/abs/2211.17192), [Chen 2023](https://arxiv.org/abs/2302.01318)) | Small drafter proposes, big model verifies; output unchanged | [Proven] same-device (Zoe runs MTP drafting today) | Cross-device: each draft round trip adds RTT; at ~14-token replies the verify call dominates, and the repo's own bench found MTP depth N=2 beat N=4 on short replies | **Reject cross-device**; same-device stays |
| [Small Language Models are the Future of Agentic AI (NVIDIA, 2506.02153)](https://arxiv.org/abs/2506.02153) (search only; did not open the paper) | Argues SLMs should be the default in agents, with larger models for hard steps; migration by logging calls | Position paper | Authored by a hardware vendor; numbers unchecked here | **Adapt** the logging-first migration (4.1) |
| [RouteLLM](https://arxiv.org/abs/2406.18665), [FrugalGPT](https://arxiv.org/abs/2305.05176) (arXiv, known) | Learned routers send easy queries to cheap models | [Proven] on benchmarks; cloud-pricing framing | Zoe's router already routes *tools*; routing *models* adds a second lane that RAM cannot hold | **Reject** as a lane, **adapt** as a night-window escalation rule |
| Little Gemma ([repo](https://github.com/cortexist/little-gemma), bench in `docs/knowledge/little-gemma-engine-bench-2026-10-10.md`) | Source inspected by the repo's own bench, not here | [Proven on Zoe's box] +18-27% decode, ~1.5x prefill, TTFT on short turns about equal (0.095 vs 0.10 s) | Different wording than llama.cpp; more RAM (~4.9 GB vs ~3.6 GB nvmap+anon) | Already **rejected** as a swap; relevant here because decode speed is not Zoe's TTFT bottleneck |

## 2. Environmental awareness and the home world model

### 2.1 What is achievable

| Capability | Today (no new tech) | With innovation (build, flag-dark) | With research | Not at all |
|---|---|---|---|---|
| Is anyone home / in this room | Read HA `person`/`device_tracker`; panel wake/VAD/touch (the engine's existing evidence) [Proven] | mmWave in one room, phone-near on the Pi, fused with the engine's decaying score [Experimental] | Per-person room tracking without wearing anything | Reliable counting of motionless people |
| Who it is | Voice ID, PIN, face at the panel (live or flag-dark) [Proven] | Phone as a second factor (the iPhone app's Secure Enclave key, planned) | Cross-modal identity under noise | Authentication from presence alone (the design correctly caps presence below private-data floors) |
| Which room | The panel's room binding [Proven] | BLE room scores, a second mic/speaker [Experimental] | Acoustic localisation on one mic | Sub-room position from the current hardware |
| What is happening (sounds) | Silero VAD, wake word on the Pi [Proven] | YAMNet-class labels from the Pi mic, logged as inferred [Experimental] | Household-specific sound models trained on the household's own clips (consent and storage burden) | Understanding conversation content between people without recording it (and WA surveillance-device law makes this a legal line, not just a technical one) |
| What is seen | Face box size as distance; ID at wake [designed] | A still-image describe-on-request at the panel using a model already resident (pull, not push) | Continuous scene understanding on the Pi | Whole-home vision on the Orin's free RAM |

### 2.2 What the home world model should be

**Should be: a small, typed, timestamped table of observed facts plus a separately-stored table of inferred beliefs, both with decay, both queryable by tool, neither injected unprompted.** Concretely:

- **Observed layer (facts with a source).** Rows of (entity, property, value, source, observed_at, ttl). Entities: person (by id), room (area), device, appliance. Properties: `home/away` (from HA `person`), `occupied` (mmWave), `phone_near_panel` (BLE), `light_on`, `door_open`, `last_voice_at_panel`. Source is mandatory and one of: `ha_state`, `sensor:<kind>`, `panel_event`, `user_stated`. These are already the shape of Home Assistant's state machine; Zoe should **mirror a whitelist of HA entities on demand**, not copy HA. [Proven approach: state tables are what every smart-home hub uses.]
- **Inferred layer (beliefs with a rule or model).** Rows of (claim, confidence, evidence_ids, rule, inferred_at, expires_at). Examples: "likely asleep" (late hour + bedroom still-presence + lights off), "probably cooking" (kitchen occupied + oven on). Each carries the evidence it used so it can be shown ("why do you think that?") and refuted. This matches the identity engine's explainability section (2.5).
- **The two must never merge.** A belief is never written into the observed table; a user correction of a belief is stored as `user_stated`, which outranks inference (the standing memory rule: user-stated > confirmed > inferred; inference never supersedes). Display and tool output label the layer ("sensor says" vs "I guess").
- **Pull, not push.** The world model is read when Zoe is asked or when a *rule the household created* fires (e.g. "tell me when the washing finishes"). Zoe does not volunteer observations from it; this keeps the model from becoming a surveillance feed and fits the owner's stance that unprompted speech is off.
- **Cheap.** Rows, not embeddings: tens to low hundreds of rows, SQLite/Postgres already present, zero Orin RAM, rebuilt from HA on restart. [Speculative on exact size; every part is ordinary storage.]
- **Retention.** Observations expire in minutes to hours (ttl), beliefs in hours; nothing about who-was-where is kept longer than needed for the current turn unless the user asked to remember it. The forgotten-means-forever rule applies: forgetting a person deletes presence history for them.

**Should not be:** a continuous activity log of household members (a movement diary is a surveillance artefact and a public-repo and breach risk); a vector index of camera frames; a learned latent "world model" (no measured benefit at this scale, and not explainable); a second source of truth for devices that HA already owns; a store of inferred emotional or health states about named people (the repo's earlier care-mode leak is the cautionary precedent; keep such inference out of persistent storage); or anything keyed by network identifiers (MACs, addresses) outside the Pi's env.

### 2.3 Privacy and legality notes

- Radar and BLE are image-free and audio-free; they are the lowest-privacy-cost sensors available. Audio-event labels must be computed on the Pi and discarded, emitting only a label and timestamp. [Proven design practice]
- Western Australia's Surveillance Devices Act 1998 covers private conversations: label-only audio sensing is defensible; storing ambient audio is not an area to experiment in without advice. (Same constraint as the pendant work.)
- Guests and children: presence sensors see everyone. The model records "room occupied", not "who", unless an identity event (voice, face, phone) exists and the person is enrolled. Unenrolled people are anonymous occupants.

## 2.4 Sensor choice table (what to buy or read first)

| Signal | Source | Cost | Orin RAM | Privacy cost | Identity? | Failure mode | Order |
|---|---|---|---|---|---|---|---|
| Home/away per person | HA `person` from the companion app | free | 0 | location history lives in HA, not Zoe | per person | stale when the phone sleeps or loses data | 1 |
| Panel touch, wake, VAD | Pi (exists) | free | 0 | none new | none | `is_foreground` always true today, so touch is invisible (fixable) | 2 |
| Occupancy in a room | mmWave | A$5-40 | 0 | no image or audio | none | ghosts on LD2450-class; weak on motionless bodies alone | 3 |
| Phone near the panel | BLE scan on the Pi | free | 0 | needs an identity key in the Pi env | per phone | radio shared with Wi-Fi, pendant sync; MAC randomisation | 4 |
| Sound labels | Pi classifier | free | 0 | label only, audio discarded | none | noisy classes | 5 |
| Face at wake | Pi (flag-dark) | free | 0 | embeddings of enrolled adults | per person | USB power budget | 6 |
| Room-level device tracking | ESPresense nodes | A$10 per node | 0 | movement diary risk | per device | calibration upkeep | 7 |
| Camera NVR | Frigate-class | cameras + storage | high | highest | optional | retention duties | not recommended |

Reading the table: the first four rows need no new software on the Orin and the first needs no purchase. Rows 1-4 are enough to deliver the three behaviours people notice: not speaking to an empty room, locking the screen on walk-away, and offering private results only when a known phone and a known voice agree. The identity engine already caps presence-only evidence below the floors for private data; this lane does not weaken that.

## 2.5 How Home Assistant's event model should be used

- **State is the source of truth; events are for triggers.** Zoe should read entity state on demand (pull) and subscribe only to the small whitelist that rules created by the household need. A firehose subscription would put every sensor flip in Zoe's logs, which is the movement diary this lane warns against.
- **Areas are the join key.** HA already knows which area an entity and an Assist satellite belong to; Zoe's panel already carries a room. Mapping panel room to HA area once gives "the lights" its referent for free. [Proven: HA area-aware Assist is a core design]
- **HA automations remain the actuator logic.** If a behaviour is "when X then Y" with no judgement (hall light on motion at night), it belongs in HA, runs when the Orin is restarting, and is visible in HA's own history. Zoe's role is to author, explain and audit those, not to replace them.
- **Zoe asks HA, never mirrors its database.** Mirroring creates a second copy of household state with its own retention and breach surface. Keep only the mirrored whitelist, with ttl.

## 3. Device-role map and distributed cognition

### 3.1 Roles

| Device | Role | What runs there | Never |
|---|---|---|---|
| Raspberry Pi wall panel | Ears, eyes, presence, local reflexes | Wake word, Silero VAD, speaker ID embedding (ResNet34 ~122 MB peak), face on wake, optional audio-event classifier, kiosk UI, AirPlay speaker, a small ring of presence events | LLM inference; long-term memory; policy decisions |
| Jetson Orin 16 GB | The brain, the one household service | Gemma 4 E4B QAT+MTP on one slot, FunctionGemma router, Kokoro, Moonshine, bge-small, memory, tools, HA bridge, the world-model tables | New resident models in waking hours (about 500 MB free); development jobs in household hours |
| Home Assistant | Hidden organ: actuation and raw sensor state | Entity states, automations, mmWave/BLE nodes via ESPHome/MQTT | Owning identity or memory |
| Optional ESP32 nodes | Dumb sensors | mmWave, BLE scanning | Any logic beyond reporting |
| TV display panel | Display only | Estate page, guest-scope cards | Microphone claims, trust elevation (per its design doc rule 1) |
| iPhone app (planned) | Identity factor, notifications, away-from-home path | Secure Enclave key, APNs notices, Face ID lock, intercom, pendant sync; phone location via HA companion | Holding household memory; anything the box cannot revoke |
| DGX Spark (possible) | Night and development | 12B and larger work, batch evaluation, harness runs, prefill/decode splitting later | Being required for a daytime answer |
| the411 (VPS) | Account, relay, backup, push relay | Matrix, TURN/SFU, encrypted backup, remote-access coordinator | Plaintext household data; any decision |

### 3.2 Data flows

1. Pi mic and sensors: audio is gated on the Pi (wake + VAD), the Pi emits a turn (audio or text, identity verdict) over the LAN to the Orin. Presence events (tiny JSON: source, room, time) go the same way.
2. HA to Orin: HA entity states are pulled on demand, or pushed to a small subscription for the whitelist that the observed layer mirrors.
3. Orin to actuators: tool calls through HA; to speakers via the Pi or AirPlay.
4. Orin to phone: content-free push ("notice available"), phone fetches from the box (per the iPhone design). Away-from-home: phone to the411 relay to the box.
5. Spark or Orin night window to storage: results are artefacts and proposals, merged under the nightly loop's rules; nothing from a night job reaches the live household path without the existing gate.

### 3.3 What splits well and what does not

- **Splits well [Proven]:** sensing vs cognition (Pi vs Orin); actuation vs reasoning (HA vs Orin); online vs night work (Orin vs 12B window / Spark); identity factor (phone).
- **Splits badly [Experimental/Proven against]:** one model's layers across devices; draft-on-one-box verify-on-another for 14-token replies (RTT dominates); running Zoe's brain on the phone or Pi (RAM and the rocks); routing the *same* turn to a larger remote model by default (breaks local/private/fast).
- **Prefill/decode disaggregation (Spark + a decode box)** is the one distributed technique with a published 2.8x gain (Tom's Hardware, search summary), but it targets models too big for one machine; revisit only if the 12B becomes a daytime model, which the owner has not asked for.

### 3.4 What breaks without internet

| Capability | Without internet | Why |
|---|---|---|
| Wake, STT, brain, TTS, memory, lists, timers, HA control, panel UI, AirPlay, presence | Works | All local on Pi/Orin/HA [Proven by the rocks and local-first stance; confirm per-feature in E6] |
| Web search, weather, music catalogue search (YouTube Music), Telegram, push to the phone off-LAN, remote access, relay, Matrix to other households, backup upload, APNs | Degrades or stops | Needs the outside world |
| Phone on the LAN | Works for intercom/lists/pairing (LAN name + pinned CA) | Design says LAN name works without the relay |
| Phone away from home | Offline | Needs the411 relay or Headscale path |
| Phone `person` home/away | Stale until the companion app reports | Depends on the app's network path to HA |
| Spark (if on-LAN) | Works | LAN only |
| NTP, TLS cert validation | Clock drift after reboot without NTP can break TLS and timers | Common edge failure; test it |

The honest gap: the repo has no recorded "pull the WAN cable for an hour" test of the household features. That is experiment E6.

## 3.5 Failure modes of the distributed design

| Failure | Effect | Containment |
|---|---|---|
| Orin restarts (deploys, OOM) | Panel hears but cannot answer | The daemon says "one moment" (synthesis priority 1), the Pi buffers presence events for a short window and drops older ones; HA automations keep running |
| Pi offline | No ears or eyes in that room | Phone and other surfaces still work; Zoe says the panel is unreachable rather than guessing presence |
| HA down | No actuation or sensor state | Zoe answers "I can't reach the house controls" and stops caching old sensor values past ttl; chat, memory, lists continue |
| Spark offline | Night and dev work pause | Nothing in the household path depends on it by design |
| the411 down | No remote access, relay, push to the phone off-LAN, backup upload | Local household unaffected; the backup job reports staleness |
| Clock skew after reboot with no NTP | TLS and token failures, wrong timers | Check in E6; document a time-sync fallback |
| Sensor ghosts | False "occupied" | Belief layer shows low confidence; rules need two sources for risky actions (locks, alarms) |

## 3.6 Rules for putting new cognition on a device

1. A device may hold only what it can lose without losing household memory (Pi: nothing durable beyond enrolment embeddings and a short ring buffer).
2. A device may be trusted to report observations, never to elevate trust by itself (TV rule, phone key step-up).
3. Anything crossing the LAN carries a source and a timestamp.
4. New always-on models on the Orin require a measured RAM budget and the replay gate if the voice path is touched.
5. Anything that sends content off the LAN is content-free by default (push payloads) or user-initiated.

## 4. The Phase-9 questions

### 4.1 One primary model, or several small specialists?

**Answer: several specialists around one generalist; Zoe is already that.** Evidence: the repo measured the 270M router picking tools better than the 4B brain (0.9996 vs ~14% wrong per the repo's memory notes; [Proven on Zoe]); Moonshine, Kokoro and bge-small are specialists; the NVIDIA SLM position paper argues the same direction (search only, [Experimental] as a claim). Weaknesses: every specialist is another resident process and another failure mode; synthesis shows RAM gates the whole stack. A specialist earns a slot only if it (a) removes load from the brain's single llama slot or (b) improves a measured gap. Candidates in rank: a **tiny extractor** (names, dates, entities) *if* extraction is currently stealing brain slot time (measure first); a **small reflector at night only** (the 12B window already serves this); **no new daytime resident**. Rule: log every brain call by purpose for two weeks (the SLM paper's first step) and only then pick the specialist. [Experimental]

### 4.2 Can a lightweight coordinator schedule expensive reasoning?

**Yes, and the right coordinator is a rules table, not a model.** Escalate by cheap signals: the router's confidence, whether the turn needs memory, whether the user said "think about it", the time window. Fast path = router + tools (milliseconds); default = E4B; escalated = deferred answer from the 12B in the night window ("I'll think on that and tell you in the morning", which fits pull-not-push when surfaced on request). RouteLLM and FrugalGPT show learned routers work for price/quality trade-offs in the cloud; Zoe's trade-off is RAM and time-of-day, so a table suffices. [Proven concept; Experimental in this estate.]

### 4.3 Can memory contribute more perceived intelligence than parameters?

**Probably yes at Zoe's size, and the repo's own data says the lever is unpulled.** Synthesis section 2: 83% of real replies used zero memory sources and continuity was empty on 97% of turns. A 4B model that remembers the household feels smarter than a 12B that does not, which is the standing rationale for the memory authority layer. Caveat: perceived intelligence is a user judgement; measure with the Samantha bar harness, and beware its same-Gemma judge. [Experimental, with a Proven diagnostic.]

### 4.4 Can structured experience grow capability without retraining?

**Yes, with narrow, verified forms.** Voyager stores working code as skills ([2305.16291](https://arxiv.org/abs/2305.16291)); Agent Workflow Memory induces reusable workflows ([2409.07429](https://arxiv.org/abs/2409.07429), ICML 2025, web navigation); ExpeL stores trajectories and extracted insights ([2308.10144](https://arxiv.org/abs/2308.10144), AAAI 2024). Common thread: no parameter updates. For Zoe the natural unit is a **cached household routine**: "goodnight" = these five tool calls in this order, derived from repeated confirmed turns, stored as a named routine, executed by the dispatcher without the brain. Risks: a wrong cached plan repeats; therefore plans carry a verifier (replay on the corpus) and expire. Adapters (LoRA) are the next rung but retraining conflicts with the fixed rocks; skills and routines do not. [Experimental]

### 4.5 Hybrid symbolic + neural (HA automations + LLM)

**This is the strongest practical pattern, and it matches Zoe's doctrine.** HA automations are deterministic, auditable, and survive the brain being down; the LLM's job is to *write and explain* them from speech ("when the front door opens after 10pm, turn on the hall light"), with a mandatory read-back and an explicit confirm before creation. Deterministic execution, neural authoring. Failure mode: LLM-authored YAML that is subtly wrong; mitigate with a schema, a dry-run listing and a one-tap undo. [Proven pattern in HA's own conversation/assist work (search, not re-read); Experimental for LLM-authored automation quality on a 4B.]

### 4.6 Persistent world model of a home

Answered in section 2.2: observed table + inferred table, pulled not pushed. A learned or graph-heavy model is not justified; Zoe already has a Postgres property graph for people and relationships, and rooms/devices can extend it with a few node types if needed. [Speculative on the exact schema]

### 4.7 Edge multimodal (vision at the panel, Pi 5 limits, AI HAT)

- Pi 5 CPU can run face detection/embedding and a YAMNet-class audio model, which the design doc already budgets (15 MB models + 50-80 MB session, unverified). It cannot run an LLM/VLM usefully. [Proven for small CNNs; the repo flags the figures unverified.]
- The panel's real limit is the **USB power budget** (design doc: shared between camera and speaker), not compute.
- The AI HAT+ 2 (Hailo-10H, 8 GB) is the way to add VLM-class capability on the Pi without touching the Orin, but no verified LLM/VLM numbers were found (search) and a VLM at the panel only matters if "what is on this camera right now" becomes a feature people ask for. [Speculative]
- If the live Gemma 4 E4B build accepts image input (the lanes mention audio-native Gemma paths; I did not verify image support on the served build), then "describe this still" can use the resident brain when asked (pull) at the cost of its slot; that would be cheaper than any new model. [Speculative until the served build is checked.]

### 4.8 Other unconventional questions

- **Sleeping on a problem.** The 12B night window as a deliberate "think" lane (reflection, dreaming, digest) is an existing, owner-endorsed design. [Proven in the repo]
- **Does embodiment mean a robot?** For this household, a room that senses and an assistant that acts through HA is embodiment enough; the TV and phone are *surfaces*, not bodies.
- **Does Zoe need a world model to feel present?** Presence-aware behaviour (not speaking to an empty room, quieting when someone sleeps, lighting a screen on approach) is mostly three booleans and a gate. [Experimental, low risk]

## 5. Smallest experiments

Each is read-only or flag-dark, runs off household hours where it touches voice, and needs no Orin model.

**E1. Read HA presence (hours).** Hypothesis: `person`/`device_tracker` home/away is accurate enough to be the first presence source. Implementation: read-only `GET /api/ha/entities?domain=person` once a minute into a log (names hashed, no addresses); no behaviour change. Resources: negligible. Evaluation: compare to the household's reported home/away over 7 days with a labelled diary. Success: at least 95% agreement, lag under 2 minutes. Failure: phone tracking flaky, fall back to Wi-Fi association. Effort: S.

**E2. One mmWave sensor in the living room (a weekend).** Hypothesis: radar occupancy + panel events gives a reliable "someone is in the room" and a clean sleep gate. Implementation: LD2410-class on ESP32/ESPHome to HA; Zoe reads the binary sensor in shadow mode, compared with the panel's touch/voice events. Resources: ~A$40, no RAM. Evaluation: confusion matrix vs manual labels over 3 days, count ghost on/off flips per hour. Success: false "empty" under 5% while someone is sitting still. Failure: ghosts or dropouts (known for LD2450-class), try a second sensor type. Effort: S.

**E3. Fusion vs Home Assistant's Bayesian sensor (a day after E1/E2).** Hypothesis: the engine's decayed LLR score matches or beats a hand-tuned Bayesian sensor and explains itself. Implementation: replay logged E1/E2 events through both; no live effect. Evaluation: agreement with labels, explanation readability. Success: equal accuracy, plus an explanation. Failure: the engine adds nothing, then use the HA sensor for rooms and keep the engine only for identity. Effort: M.

**E4. Audio-event labels on the Pi (2-3 days, flag-dark).** Hypothesis: a YAMNet-class model on spare Pi CPU detects 3-5 household sounds (knock/doorbell, alarm, dog, glass, running water) with usable precision. Implementation: classify the already-captured VAD segments, log label + score only, discard audio. Resources: Pi CPU at idle times (measure; the Pi must keep wake-word latency, so gate by a CPU ceiling). Evaluation: one week of labels reviewed by the household as "right/wrong" (this is the only time audio-derived labels are reviewed; no clips kept). Success: precision at least 80% on the chosen classes with no wake-word regression (replay-gated per the voice rule). Failure: noise classes dominate; drop. Effort: M.

**E5. Wake-word arbitration paper test (a day, no hardware).** Hypothesis: a 150-300 ms server-side window choosing the highest wake score + VAD energy resolves duplicate wakes. Implementation: simulate two satellites by replaying the corpus with synthetic attenuation. Success: correct nearest pick in at least 90% of cases. Failure: needs a real second device. Effort: S. Do when a second listening device exists.

**E6. WAN-cable test (one evening, household's consent).** Hypothesis: all local features in section 3.4 work for an hour offline. Implementation: block WAN at the router while following a scripted checklist (wake, timer, list, lights, music from local library, memory recall); note NTP behaviour. Evaluation: pass/fail per row with the observed message to the user. Success: no feature in the "works" row fails; every degraded feature says so in plain words. Failure: each failure becomes a ledger entry per "fix it or note it". Effort: S.

**E7. Brain-call purpose log (no new code if turn metadata exists).** Hypothesis: a measurable share of brain slot time goes to jobs a specialist could do. Implementation: tag each llama call with purpose, summarise 14 days. Success: a clear top-3; if extraction is under 10% of slot time, no specialist model is justified. Effort: S.

**E8. Cached routine from confirmed turns (a week, flag-dark).** Hypothesis: 5 repeated multi-step requests can be turned into named routines that run without the brain. Implementation: mine the dispatcher log for repeated tool sequences, propose to the household, store with a verifier. Evaluation: said-vs-did on replays; undo works. Success: fewer brain turns on those requests and no regressions. Failure: low repetition. Effort: M.

## 6. Speculative items

- **Presence as a quiet "duvet mode":** no speech and a dimmed panel when the bedroom still-presence + hour + lights-off agree; the cheapest "she understands the house" moment. [Speculative]
- **Per-room "attention" state** (panel brightens and listens harder when someone approaches, from face-box size or radar distance) feeding the distance-aware UI listed in the Apple-parity lane. [Speculative]
- **Acoustic room signature** (impulse-response fingerprint per room from the Pi speaker and mic) to tell which room a sound came from. Interesting; no evidence found; research. [Speculative]
- **Night-window "household rehearsal":** the 12B replays the day's confirmed routines against the world-model tables to find automations worth proposing. [Speculative]
- **Prefill/decode disaggregation across Orin and Spark** if a larger daytime model ever returns; requires a LAN link and a measured win. [Speculative]
- **Phone as a sensor** (Wi-Fi SSID, charging state, motion) for away-from-home context, read through HA companion data, never stored raw. [Speculative]
- **Household-trained sound classifier** from the household's own consented clips on the Spark overnight. High privacy burden; do only if E4 shows the generic model cannot work. [Speculative]
- **Robots:** revisit only when an open, Jetson-class VLA with independent household-task evidence exists; none found. [Speculative]

## Appendix: caveats on this report

- Several web searches returned secondary summaries; items marked "search" were not verified on the primary page. Licences still marked [unverified] must be read from the LICENSE file before any adoption. No code of any external project was inspected.
- Live check (read-only ground truth, 2026-11-03): the brain, router and Flue brain services were healthy; the standalone Kokoro unit showed inactive at the time of the read (the synthesis records Kokoro reloading behind deploys; I did not investigate why it read inactive). No other live system was touched.
- No household names, locations, coordinates or network identifiers are recorded here.
