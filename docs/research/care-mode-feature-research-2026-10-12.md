---
type: Research / program record
title: "Zoe feature research - born from Dad's recovery, built for every Zoe user (care mode / per-user presets)"
status: DRAFT for the owner. Research only. Nothing here is built or decided until the owner says so.
date: 2026-10-12
description: Care mode / per-user presets - medications, curated memory, help and emergency, orientation, voice tuning, family digest, video calling. Research prompted by a family member's hospital recovery; written to generalise to every Zoe user.
---

# Zoe feature research — born from Dad's recovery, built for every Zoe user

> **Research only.** Nothing is built or changed. Coordinated on Opus; research agents ran on Sonnet.
> **Reframe (Jason's feedback):** these are **standalone Zoe features**, each useful to ordinary users. "Care mode" is not one big system. It is a **preset**: a bundle of settings across independent features, switched on for one person. Each feature is researched and scoped **on its own**, and gets its own deep dive and later its own PR train.

## How the pieces fit
```
            ┌──────────── Care preset (Dad) ────────────┐
            │ turns on + tunes ↓                          │
 Medications   Curated memory   Video calls   Help/Emergency   Orientation   Family digest   Voice tuning
 (anyone)      (anyone, kids)   (household)   (everyone)       (anyone)      (opt-in)        (per user)
            │                                              │
            └──── Kid preset / Guest preset reuse the same switches ────┘
```

## A clinical finding that shapes the care preset (not the features)
Confusion after a long hospital stay is most likely **persistent delirium**. In one review of over-50s, about 45% were still delirious at discharge, 33% at 1 month and 21% at 6 months. It **fluctuates and often improves**. So the care preset needs a **level dial that steps support down** as he recovers, and **sudden worsening must reach family** as a signal. HELP (the Hospital Elder Life Program) components are orientation, routine, sleep, fluids and meals, glasses and hearing aids, and family presence. Several of the features below can deliver them at home.

---

## Feature 1 — Medications (any user)
**For everyone:** a household medicines feature, not a care feature. It covers:
- a parent tracking a child's antibiotics course;
- an adult on daily tablets;
- a pet's medication;
- "did I take my vitamin?";
- refill reminders ("10 days left, reorder?").

**Today:** recurring reminders only (`reminder_service.py`, `reminder_recurrence.py`, `proactive/triggers/reminders.py`). Nothing is medication-specific. There is no dose log, no voice "I took it", and no repeat or escalation. Reminders aren't spoken, and quiet hours (22:00–07:00) defer them.

**Core design (all users):**
- **Data:**
  - `medications`: whose (user, child, pet), name, dose, plain-words purpose, times RRULE, with food, course end date, supply count, PRN min gap and daily max.
  - `dose_events`: scheduled → reminded → taken / skipped / missed, with ack source and time.
  - Each medication fires through the existing reminder scheduler as `category='medication'`.
- **Voice, deterministic and not LLM-guessed:**
  - "I took my tablets" → logged.
  - "Did I take my tablets?" → answered from the log.
  - "What do I take today?"
  - "Give Mia her antibiotics. Done." → logged for the child.
- **Touch:** a ✓ on the reminder card, and a medicines page in the UI (list, history, supply).
- **Refill:** a supply countdown that adds to the shopping list or a reminder.
- **Hard rule layer for everyone:** never suggest doubling up, no dose changes, no medical advice. Refer to the pharmacist or GP.

**Settings, so care is just tuning:**
| Setting | Default user | Care preset |
|---|---|---|
| Spoken reminder on panel | optional | on |
| Repeat if unacknowledged | off / 1× | 2× at +10 and +20 min |
| Quiet-hours exemption | off | on (night volume) |
| Missed-dose alert to someone | off | on, after 30 min, via Telegram |
| "Did I take it?" when no record | "No record of it today" | "Check your pack slot. If unsure, don't take another, I'll tell Jason." |
| Confirm prompt style | open | yes/no |

**Evidence:**
- Dispensers like Hero and Livi (about $100–130 plus $30–99/month) lock doses and alert carers.
- Evidence that reminders alone help adherence in dementia is thin.
- Zoe complements a blister pack or dispenser; it never replaces it.
- Missed-dose rules depend on the drug and are the pharmacist's call. PRN medicines need a gap and a daily max.

**Open for its deep dive:**
- Is "whose medicine" a person, or a people-graph node (child or pet)?
- What does the UI page look like?
- Privacy: medication is a `restraint.py` sensitive topic. Speaking it on a shared panel needs to respect who's listening.

---

## Feature 2 — Curated memory: populate someone's Zoe (any user, incl. kids)
**For everyone:** an admin or parent can **seed and curate another member's memory**:
- family tree and who's who (with photos);
- where they live, school, teacher's name, allergies, routines, favourite things;
- for Dad: why he was in hospital, his life story.

This is huge for:
- **young kids**, who can't tell Zoe these things themselves;
- **new members onboarding**, a fast start instead of weeks of learning;
- **elderly parents**, as family-curated anchors;
- **guests and carers** ("Nana's carer Sue comes Tuesdays").

**Today:**
- Memory is strictly per-user (`memory_service._memory_visible_to_user`, `:1086`).
- An admin can review, export and forget another user's rows (`routers/memories.py:221-262, 1296, 1313`) but **cannot create** a row in someone else's memory. `/proposals` always writes as the caller (`:151`).
- The provenance ladder already has an **operator tier** (rank 6, `memory_authority.py:100`) that inference can't overwrite. Nothing writes to it for another user.
- The pinned, LLM-free card (`user_model_card.py`, ≤1400 chars) is the right always-in-context home. But it is built only from the user's own rows, and "Current" facts expire after 70 days.
- The people graph has dated edges but **no `deceased` concept**.

**Core design (all users):**
- **"Curate for member"** admin/parent API + UI: write operator-class rows to member X, recording the curator.
- **Anchor section in the pinned card:** curated facts are always in context, never expire, never overwritten by inference. This matters because the 4B brain under-fires `recall_memory` (`zoe_flue_client.py:624`).
- **Structured onboarding forms:** family members (relationship, photo), home, school or work, routine, likes, health notes. These write people-graph edges plus anchor rows.
- **Who-added-what transparency:** the member can see what Zoe knows about them and who added it, and it can be revoked (where age-appropriate).
- **`deceased_at` plus an optional handling note** on people, for everyone, so Zoe never says "Grandpa's coming over".

**Per-user memory policy**, a general feature that presets just set:
| Policy knob | Adult (default) | Kid | Care | Guest |
|---|---|---|---|---|
| Who may curate | self | parents | family admins | — |
| Learn from own speech | yes | yes, lower trust | **quarantined**: confused claims never stored as facts | no |
| Pinned anchors | optional | yes | yes | — |
| Affect / emotional memory | on | per household rule | on (mood hints only) | off |
| Family-visible observations | no | parent view | counts and trends only | — |
| Sensitive-topic scripts | optional | optional | yes (e.g. deceased spouse) | — |

**Care-specific memory tiers** (built on the above): anchors · today log ("has anyone been today?") · reminiscence (stories, photos) · sensitive scripts · family-only observations.

**Evidence and opinion:**
- Life-story work and "This is me" anchors have the best practical support. Reminiscence gives small but real benefit, and individual sessions beat group sessions.
- Letta/MemGPT's "small labelled pinned blocks + archival recall" is the same shape as Zoe's card plus recall. That validates the approach.
- No mainstream assistant offers **third-party curation with provenance**. Zoe already has most of the machinery, so this is a real differentiator.
- Biggest risk (care): letting confused speech outrank curated anchors. `hold_the_fact.py` would defend the mistake. Biggest risk (everyone): curating *over* someone's own voice. That's why transparency and revocation matter.
- Ethics: never assert a falsehood, never confirm a dead person is alive. Acknowledge, redirect, follow the family script.

**Open for its deep dive:** where the UI lives, how the forms map onto people graph and anchors, the kid-specific policy (child privacy, ages), and the interaction with the forget shield.

---

## Feature 3 — Video calling and drop-in (household feature)
**For everyone:**
- **panel↔panel intercom** ("Zoe, call the kitchen"; "dinner's ready" video drop-in);
- **panel↔phone** ("Zoe, call Jason", which rings his phone);
- **calls between houses** (grandparents' panel ↔ yours);
- **kids calling a parent** from the panel without a phone.

Echo Show drop-in is the reference product.

**Today:**
- LiveKit server is deployed (`docker-compose.yml:194`, on-demand, about 560 MB).
- The kiosk already lazy-loads livekit-client 2.5.0 (`touch/home.html:4255`).
- nginx proxies `/livekit/` signalling.
- There is a token endpoint and Telegram send-to-phone (`routers/handoff.py`).
- **It is audio-only for the voice agent today**, but calls can bypass the agent entirely.

**Effort:** panel↔phone **M**, in-house panel↔panel **M**, cross-house **M-L**.

**Hard problems:**
1. **Media across houses:** Cloudflare Tunnel doesn't carry WebRTC UDP, so it needs TURN/TLS on 443.
2. **Pi device contention:** the voice daemon owns the only ALSA input, and face ID grabs the camera. The PanaCast and Jabra **share USB power** (TTS during video already dropped the camera), so a **powered hub is mandatory**. A "call active" handoff must pause wake word, ambient capture, face ID, music, TTS and idle timers.
3. **The Pi 5 has no hardware H.264 encoder.** Plan on about 480p–540p at 15–24 fps. Must be measured.

**Options:**
- **LiveKit Cloud free tier:** 5,000 participant-min/month, capped (about 40 hours of 1:1). TURN included, zero Jetson RAM. **MVP recommendation.**
- **Self-hosted LiveKit with embedded TURN:** the later swap via config URL.
- P2P with coturn is fully private but re-implements reconnection and bitrate handling.
- Jitsi is too heavy.

**Settings:**
| Setting | Default | Care preset |
|---|---|---|
| Who can call this panel | household | allow-list of family |
| Auto-answer (drop-in) | off / household-internal only | allow-list, with voice announce + 5-second countdown |
| "Call X" by voice | on | on, big Hang-up |
| Camera banner, max duration | always / 60 min | always / 30 min |

**Phases:**
- 0: hub, `getUserMedia` test in kiosk, Pi CPU measurement (S).
- 1: panel→phone MVP (M).
- 2: voice "call X" plus ring/answer (M).
- 3: drop-in with safeguards (M).
- 4: cross-house panels (M-L).
- 5: optional self-host (M).

**Riskiest unknowns:** Pi encode CPU vs the voice daemon, mic/camera handoff, USB brown-outs, remote reachability, drop-in consent.

---

## Feature 4 — Help and emergency (everyone)
**For everyone:**
- "Zoe, help", "I've fallen", "call an ambulance", "there's a fire"
- a child home alone
- someone hurt in the garage

Every household benefits.

**Today:** `distress_handoff.py` (on) covers self-harm, abuse and danger. It leaves an in-app note to one contact, sends no Telegram, and has no "I've fallen" or "help" priority path. There is no medical-advice guardrail.

**Core design (all users):**
- A **priority intent that bypasses the LLM**. It asks once ("Are you hurt? Say yes or no"), immediately alerts the household's emergency contacts on Telegram (and push to other panels), and gives emergency-services guidance (000).
- **Household emergency contacts** setting, with alerts across panels: "Dad needs help in the bedroom" spoken on every panel in the house.
- **Medical guardrail for everyone:** no diagnosis, no dose advice, refer to GP, pharmacist or 13 HEALTH, and escalate red-flag symptoms.

**Settings:** the care preset adds a confusion and fear lexicon ("where am I", "I'm scared"), a no-activity-by-10am alert, and a scam guard (no money or personal-detail actions by voice).

**Evidence:** no voice fall-detection studies exist, and wearable fall detectors are mostly false alarms. So react to what is said, and don't infer. Zoe is **not** a monitored response service; say that at setup. A pendant alarm stays the real emergency path for Dad.

---

## Feature 5 — Orientation and day awareness (anyone, preset-tuned)
**For everyone:**
- an always-correct date and time in the brain's context, so "what's on today" and "is it a school day?" stay grounded;
- a richer "today" clock and next-up screen;
- a morning brief.

**Today:** the brain only calls `get_time` when it chooses to. `brief_first_turn.py` (flag-dark) and `morning_checkin.py` exist. The touch home scene has a big clock with no AM/PM, no year, and dims at night.

**Core design:** a light per-turn date/time/next-item context block for all users (cheap, and fixes a class of grounding misses). An optional "Today" panel scene.

**Care preset adds:**
- place ("you're at home") and the reason-for-recovery wording;
- today's visitors;
- an always-readable orientation screen with a big Talk-to-Zoe button;
- a morning anchor brief with a glasses and hearing-aid prompt;
- patient repetition, with the repeat count sent to family as a signal.

Evidence: reality orientation (modest) delivered passively, without arguing (validation-style) on emotionally loaded beliefs.

---

## Feature 6 — Family digest and signals (opt-in, any member)
**For everyone:** opt-in sharing between household members. Examples:
- a teen sharing "home safe" signals;
- an adult sharing "tablets taken" with a partner;
- elderly parent → children.

**Design:** **signals, never transcripts**:
- first and last activity
- doses taken or missed
- meals and fluids
- mood valence
- repetition counts

Instant alerts plus an evening digest plus a weekly trend (useful to a GP tracking delirium recovery). This follows Alexa Together's "first activity of the day" model. The member can see exactly what is shared.

**Consent:** proportionate and least-intrusive. Involve the person, decide with the family, and revisit as he recovers.

---

## Feature 7 — Per-user voice tuning (any user)
**For everyone:**
- per-user TTS speed and voice (kids may want a faster or different voice, older users slower);
- per-user end-of-speech patience;
- verbosity level.

**Today:** the voice is household-wide (`voice_settings.py`), and there is no per-user speed. Kokoro accepts `speed` (`tts_waterfall.py:248`). `voice_delivery.py` auto-slows only for empathy and night.

**Care preset:** speed about 0.85, longer silence window (dementia speech roughly doubles ASR WER, and older users need more time to start), and yes/no confirmations for medicines. **Voice-path changes are replay-gated.**

---

## Smaller features from the research (each standalone)
| Feature | Everyone | Care angle | Size |
|---|---|---|---|
| Hydration / meal prompts with "done" | habit tracking | HELP component, goes in the digest | S |
| Appointment prep and recall | "bring your Medicare card" | capture "what the doctor said" for family | S–M |
| Step-by-step task guidance | recipes, appliances | tea, washing machine | M |
| Visitor / doorbell announcements | "Sarah's at the door" | stranger and scam safety | M |
| Photo-frame idle mode | family photos | reminiscence, who's who | S–M |
| Voice messages between members | leave a message on a panel | family → Dad | S |
| Per-panel Home Assistant scope | multi-site homes, granny flat | his house must never control Jason's | M |

## Care preset = settings over the features
`member_modes.mode='care'` plus a care_level dial (high, medium, light) that sets:
- Medications: spoken, repeats, quiet-hours exemption, missed-dose alerts
- Memory policy: curated anchors, quarantined learning, scripts, family observations
- Calls: allow-list drop-in
- Help: confusion lexicon, no-activity alert, scam guard
- Orientation: screen and morning anchor
- Digest: on
- Voice tuning: slower, more patient

The **kid preset** reuses most of the same switches: parent-curated memory, medications for a child, calls to parents, Help.

## Suggested order for individual deep dives
1. **Curated memory** (biggest general value, most existing machinery, kids and Dad both win)
2. **Medications** (highest stakes for Dad, broadly useful)
3. **Help / emergency** (everyone, small-to-medium)
4. **Video calling** (epic, and needs hardware phase 0 first)
5. Orientation → voice tuning → digest → the smaller features

Each deep dive produces its own design doc: data model, UI, API, settings, risks, tests, and an effort estimate. It is done one at a time, with no build until you say so.

---
---

# OVERNIGHT DEEP DIVES (2026-10-11/12)
Each section below is a standalone design study. All research is read-only, nothing is built, and Sonnet agents were coordinated by Opus. Repo claims carry file:line. External claims carry URLs, and vendor figures should be re-checked before relying on them.

---

## DD-P — Presets architecture + smaller features

### Things to know first
- None of the care features (medications, orientation, care mode, sundowning) exist anywhere in the repo.
- **Doc drift:** `services/zoe-data/AGENTS.md:44` describes `ambient_briefing.py` and `GET /api/skybridge/briefing`, but neither exists in this worktree. Confirm on the live host.

### P1. Where per-member settings live today (5 scattered mechanisms)
| Mechanism | Where | Holds |
|---|---|---|
| `member_modes` | `alembic/0035_persona_layer.py:44`, `persona_layer.py:158,543-578` | mode, **minor** (the only real minor flag in zoe-data), updated_by/at |
| `user_preferences.prefs` JSON | `alembic/0001:340`, `user_prefs.py` | free-form keys (memory_opt_out, telegram_id, preferred_name…), no schema, no audit |
| `role_capability_matrix` | `guest_policy.py:126,213` | per **role**, not per member |
| `panels` + `display_preferences` | `0021`, `0026`, `routers/system.py:2488` | per **panel** |
| Env flags | `proactive/engine.py:34`, `distress_handoff.py:200` | household-global |

Key findings:
- zoe-auth has `child` and `family_member` roles (`zoe-auth/models/database.py:21-24`), but zoe-data collapses everything except admin and user into `guest` (`guest_policy.py:213-217`).
- `member_modes` rows mean "opted into the persona layer". Adding care columns there would mix a consent record with preferences.
- Audit is only a log line (`routers/persona.py:93…`).
- `services/AGENTS.md` says household concepts belong in skills and scopes, not kernel code, so this should be a dedicated module.

### P2. Recommended design: a preset is data, and features read resolved keys
**The principle:** a preset is a named dict of values, applied as a snapshot with a provenance tag. **Features never read a preset name.**

**Migration 0044** (the latest existing migration is `0043_people_graph_invariants`):
- `member_settings(user_id, key, value_json, source 'preset'|'explicit', updated_by, updated_at, PK(user_id,key))`
- `member_profile(user_id PK, preset adult|kid|care|guest-visitor, care_level high|medium|light, care_level_since, review_due_at, …)`
- `member_settings_audit(id, user_id, key, old_json, new_json, actor, actor_role, via, reason, at)`

**Design choices:**
- Preset definitions live in a versioned code file (`member_presets.py`). Households can view them but not edit them.
- Applying the kid preset writes `member_modes.minor` through the existing `persona_layer.save_member_mode`.

**Illustrative preset matrix:**
| Key | adult | kid | care/high | care/med | care/light | guest |
|---|---|---|---|---|---|---|
| persona.mode | (opt-in) | kid | helper | helper | companion | none |
| memory.policy | auto | curated | curated | curated | auto-with-review | none |
| meds.prompts | off | off | on+escalate | on | gentle | off |
| calls.allow_list | open | circle | circle | circle | open | none |
| help.button | off | on | always visible | on | optional | off |
| orient.cards | off | off | on | on | off | off |
| digest.to_circle | off | on | on | weekly | off | off |
| voice.rate/repeat | default | default | slow+repeat | slow | default | default |
| night.mode | household | household | care-night | care-night | household | household |

**care_level dial:**
- Each level is a different set of values for the same keys.
- **Stepping down is never automatic.** Zoe *proposes* it ("steady 3 weeks — step down?") and a human confirms.
- **Stepping up is one tap.**
- `review_due_at` prompts the admin, never Dad.
- **The dial is never driven by inferred confusion or mood.** `emotional-safety-note.md` §6 forbids retained affect scores.

**Precedence:**
1. Preset values.
2. Explicit settings, which survive a preset reapply or a level change. The UI marks them "customised" with a reset option.
3. **Safety floors**, which no one can override: a minor gets no companion or mentor mode (`persona_layer.py:318-334`), guests get no affect rows, and distress hand-off stays on.

**Who can write:**
- Admin: anything.
- Member: their own non-protected keys.
- A new **care circle** role: may raise care_level and edit meds and the call allow-list, but may not lower protections.
- Recommendation: at care/high, Dad can say "stop" to the current prompt, but cannot switch off meds or help. This is your decision.

**Single resolver** (`member_settings.py`):
- Interface: `resolve(user_id, key, panel_id=None)` plus a cached `snapshot()`.
- Layering order: defaults → preset → explicit → floors → panel overlay (for panel-scoped keys such as ha.scope and night.mode).
- If a lookup fails, it falls back to the safest value for that key.
- Guests always resolve to the guest preset and never get rows.
- A **key registry** declares each key's type, default, failure value, writers, and whether it is protected or widens access.
- A test greps for preset-name literals outside the presets file.

**Audit:** a single writer, `set_member_setting(actor,user,key,value,via,reason)`, writes the row and its audit entry in one transaction and logs ids only.

**Admin UI:**
- A "People" section with a preset picker, a care-level control, and the resolved keys with their sources.
- A plain-language "What Zoe will do for Dad" summary and a history tab.
- Dad's panel shows a read-only "what's on" card. Nothing is editable there.

**Governance** (emotional-safety-note):
- The kid preset's persona can't be wired for minors until the owner flips `MINORS_GET_PERSONA` (`persona_layer.py:442`).
- Care tone rules that go beyond the four existing modes are persona changes and need a note update.
- Voice rate is a TTS parameter, not persona.
- No "confusion score". Meds and activity timestamps are logs of actions, and they need a §6 sentence before they go to a digest.
- Consent is captured when the preset is applied ("on behalf during recovery, review on date X").
- Flag-dark under `ZOE_MEMBER_PRESETS`.

**Rollout:**
1. Tables, resolver and audit (flag off).
2. Admin API and UI.
3. The first consuming feature.
4. The care_level dial.
5. Kid persona wiring (gated on the owner's flip).

### P3. Smaller standalone features (each everyone-first)
**a) Hydration and meal prompts with "done" — S, or M with escalation and digest.**
- Reminders exist (`alembic/0001:136`, `reminder_recurrence.py`, `reminder_scan.py`, `routers/reminders.py:316`).
- Gap: one terminal state, so "done" and "skipped" look the same, plus no per-occurrence log and no escalation.
- Design:
  - Add `reminder_occurrences(reminder_id, due_at, status done|skipped|snoozed|missed, at, via)`.
  - "Done" maps to the panel's last announced prompt.
  - The category picks a delivery profile through the resolver.
  - The quiet window becomes a resolved value.
- This is the **foundation for medications**.
- Risk: nag fatigue. Cap the repeats, and "stop" always works.

**b) Appointment prep and recall — M.**
- Reuses `calendar_service.create_event_record` (health categories `:33-41`), notes, the proactive triggers and `arrival.py`.
- Design:
  - A prep checklist from keyword templates (GP, physio, blood test: Medicare card, medication list, fasting).
  - An `appointment_prep` trigger the evening before and 1 hour before.
  - A single "how did it go?" afterwards, captured verbatim as a user-stated note tagged to the event.
  - "What did Dr Lee say?" reads it back with its source, using `provenance_answers.py`.
- Never summarise or interpret medical content.

**c) Step-by-step task guidance — M, or L with photo authoring.**
- The touch cook mode exists (`touch/cooking.html:601-640`), but recipes live in browser localStorage (`:387`). Voice `recipe_search` only opens the page (`intent_router.py:1321`). Timers are real (`:4490`).
- Design:
  - Server-side `guided_tasks`.
  - Panel session state so next/repeat/back/stop resolve deterministically in fast_tiers.
  - Family-authored appliance how-tos with step photos.
  - Care mode adds "ready for the next step?".
- Never improvise gas or electrical steps. Replay-gated.

**d) Visitor and doorbell announcements — M, or L with Frigate.**
- No doorbell, camera or Frigate integration exists today.
- HA → Zoe ingress pattern exists (`homeassistant/automations.yaml`, bridge `/voice/wake` with `X-HA-Voice-Token`, `main.py:723-740`).
- Design:
  - A token-gated `POST /api/visitor/event` that enqueues a site-scoped announcement with a 30 s TTL.
  - An optional snapshot via `agent_safety.guarded_urlopen`, kept on the LAN and never persisted.
  - No face recognition in v1 (biometric policy).
- Legal: surveillance considerations.

**e) Photo-frame idle mode — M.**
- `touch/js/photo-slideshow.js` exists but **is included by no page**.
- Photos are only a journal column, and there is no upload route.
- Design:
  - `household_photos(… caption, site)` plus an upload route (admin or circle, kept out of `dist/`).
  - An authenticated serve route and a "frame" idle scene with photo, big date, weather and a today strip.
  - Captions double as orientation ("Sam and the dog, Christmas"). Each site gets its own photos.
- Evidence: delirium guidance recommends family photos and date cues (LHSC), and GrandPad does the same.

**f) Voice messages between members — M.**
- `voice_announce` (claim `:146`, played ACK) carries text, not audio. There is no audio store.
- Design:
  - `voice_messages(from,to,audio_path,transcript,delivered/played/expires)`.
  - Delivered when the recipient is present (`panel_presence_tier`): "Message from Sam — play it?"
  - The sender comes from the authenticated session, never a voice-ID guess.
  - 30-day retention after play, and a "delete" command.
  - Never auto-plays to `bound_guest`.

**g) Per-panel HA scope — M code + L ops. This is the highest-risk change.**
- The bridge URL is read from env in **8 places**: `smart_home_service.py:41`, `zoe_agent.py:84,1738`, `intent_router.py:5107`, `mcp_server.py:2860,2958`, `routers/stubs.py:68`, `routers/system.py:204`, `routers/ha_control.py:34`.
- Rooms only break ties (`smart_home_service.py:357-392`).
- `execute_intent` carries no panel id (`intent_router.py:3476,5774`).
- `/api/ha/control` is open to any authenticated caller, and `smart_home` is guest-safe.
- Design:
  - Add `sites(site_id, ha_bridge_url, ha_token_ref)` and `panels.site_id`.
  - A **single** `ha_client.for_site()` chokepoint, with a lint test that bans `ZOE_HA_BRIDGE_URL` anywhere else.
  - **Guard on the entity**: every call takes `(site_id, entity_id)`, and the site is derived from the device token on the server side.
  - A `ContextVar` carries the panel through turns.
  - Fail closed.
  - A per-panel `guest_ha` setting.
- Tests: site B asking for a site-A entity is refused on all 8 paths. Replay-gated.

**h) Care night mode — S, or M with lamp suggestion.**
- Quiet hours are a global env setting (`engine.py:34-77`). Night brightness is per panel.
- Design:
  - Resolved `night.window/volume_cap/allow` (safety-only: meds, help, expected doorbell, urgent circle messages).
  - Lower TTS gain, a dim clock and date only.
  - When Dad wakes the panel at night: "It's the middle of the night, Friday. You're at home. Would you like the hall light on?" The light comes on only on "yes".
  - Night wakes are timestamps only, never inferred confusion.
- I found no evidence supporting specific lighting interventions, so treat this as a comfort feature.

### P4. Ideas from elder-care products we hadn't listed
- **A circle of support with an escalation order** (Alexa Together allows up to 10 caregivers). This replaces the single env contact.
- **Activity-by-category feed and "no activity by X" alerts** (Care Hub).
- **Remote Assist**: family edit settings and reminders remotely.
- **Fall detection** needs hardware. Park it, or add a wearable button through HA.
- **GrandPad Family Circle**: allow-listed contacts and remote photo upload.
- **ElliQ wellness check-ins**: asking is fine, but **storing mood or pain scores conflicts with §6**.
- **Activity suggestions** (music, walks).
- **Google Family Bell-style routine bells.**
- **Post-discharge GP visit template** (SCGH guide: GP visit within 1–2 weeks, bring the medication list, ask whether hospital-started medicines can stop).
- **"I'm worried about Dad" button** for the circle: raises care_level and shows the GP and after-hours numbers. This uses human judgement instead of auto-detection.

### P5. Recommended order (presets track)
1. Resolver, registry and audit
2. Per-panel HA scope (before any second-house deploy)
3. Prompts with "done", then medications
4. Night mode
5. Photo frame and orientation card
6. Voice messages and the help circle
7. Appointment prep
8. Step-by-step
9. Doorbell
10. Kid persona wiring

The first care-capable build is items 1–4 plus the care_level dial.

### P6. Owner decisions
- Can Dad switch off protections at care/high?
- Who is in the circle, and where is it stored?
- Consent wording and review date.
- Is an activity-by-category feed acceptable under §6?
- Does the remote site run its own zoe-data, or connect over VPN?
- The `MINORS_GET_PERSONA` flip.

---

## DD-M — Curated memory (populate someone's Zoe: kids, new members, Dad)

### Headline conclusions
1. **Most of the plumbing exists.** `MemoryService.ingest(user_id=<target>, origin=…)` can already write into another member's memory with full provenance, walls and the forget shield (`memory_service.py:1408`, writer at `:1608`). **No API lets a curator use it.** `/proposals` writes as the caller (`routers/memories.py:151-194`).
2. **Correction to my earlier plan: don't use the `operator` class.** `origin="admin"` is rank 6, and "a spoken sentence never undoes an OPERATOR row" (`docs/knowledge/memory-authority.md:55`, `memory_authority.py:466`). If Mum curated "allergic to penicillin" for Dad as operator, it would outrank his own later words, which breaks the owner rule that user-stated wins. `hold_the_fact` would then defend the curated row against him (`hold_the_fact.py:511`).
3. **New class `curated`, ranked between derived (3) and user_stated (4), plus an `anchor` flag.**
   - An adult's own words still win (`memory_authority.py:466-474`).
   - The `anchor` flag only bites when the subject's policy is `own_speech=quarantine` (kid and care presets). A contradicting statement then becomes a **disputed candidate** for family review, using the existing machinery.
   - "Confused speech can't overwrite anchors" is therefore a policy branch in `find_conflict`/`_protected_conflict`, not a new subsystem.
4. **Three questions must be answered by deterministic tiers, never the 4B model:** deceased handling, "who is this?" and "has anyone been today?".
   - The model under-fires `recall_memory` (`zoe_flue_client.py:1160`).
   - The card is a background fetch with a 5-minute TTL and misses the first turn (`labs/flue-zoe-brain-2x/src/user-model.ts:6-8,100`).
   - Restraint strips grief lines from the card (`restraint.py:486`).
5. **Likely existing bug (PR0, unverified):** `MEMORY_OPT_OUT_SOURCES` (`user_prefs.py:39`) omits the voice writers `voice_regex`, `voice_turn_digest` and `voice` (`routers/voice_tts.py:3077-3093`). Users who opted out may still be learned from by voice. Any learn-from-speech policy must key on **writer class**, not a hand-kept list. Verify with a test, then fix it standalone.

### Today in the repo (key facts)
- **Walls a curator write must still pass** (all in `ingest`):
  - opt-out `:1482`
  - off-the-record `:1489`
  - identity wall `:1503`
  - PII scrub `:1536`
  - forgotten ledger `:1555-1583`
  - affect gate `:1528`
- **New provenance keys** such as `curator_id` must be added to `PROVENANCE_KEYS` (`memory_authority.py:127`).
- **Visibility:** curated rows must be `personal`, never `family` (`:1086-1098`).
- **Voice teach:** `ask_to_remember` always stores to the *speaker* (`ask_to_remember.py:259-325`). It accepts `speaker_verified=None`, so if a parent says "Mia's teacher is Mrs Lee", it lands in the parent's memory under "Work".
- **People graph:**
  - The `people` table has no photo, deceased, handling-note or linked-account column (`alembic/0001:87-102`).
  - People rows default to `visibility='family'` (`routers/people.py:162`), so a child's school contact must be made private.
  - A person row belongs to one user, so parent and child don't share a family tree.
- **Carers:** today a carer is a `guest` (roles are fixed, `guest_policy.py:126`) and guests get no memory. A carer would need a scoped pass.

### Data model (migrations 0044–0046)
- **0044:**
  - `memory_policy(user_id PK, preset, curation_mode none|grants_only|admin_default, own_speech on|quarantine|off, affect, anchors_enabled, family_observations, consent_basis self|guardian|substitute, consent_by/at, version, updated_by/at)`
  - `memory_curators(subject_id, curator_id, scope[categories], granted_by/at, accepted_at, revoked_at/by)`
  - `memory_curation_log`: ids and counts only.
  - Lookup failure fails **closed** for kid and care (quarantine) and open for adult.
- **Curated rows go in the existing MemPalace store** with these metadata fields:
  - `authority_class=curated`, `origin=curator`, `curator_id`, `anchor`, `source_excerpt` (scrubbed), `visibility=personal`.
  - Recall label: "Mum told me". Never "you told me".
- **0045** adds to `people`: `deceased_at`, `deceased_precision`, `handling_note` (≤300 chars), `linked_user_id` (so "Mum" in Mia's tree resolves to Mum's real account), `photo_id`, `curated_by`.
  - It also adds `person_media(…)` for photos stored on disk: an authenticated route, EXIF/GPS stripped, never face-matched. This needs an addendum to the biometric policy.
  - Death is a person attribute. Edges are not closed on death ("my late husband" is still a true relationship). Past tense is rendered by code from `deceased_at`.
- **0046** `care_log(subject_id, kind visit|call|care_check|meds_ack, actor, at, note, recorded_by, channel)`.
  - Written only by explicit check-in (panel "I'm here" tap, or a curator entry). Never by passive face or voice detection.

### API and the brain
- **Authorization:** a curator is an admin, or holds an *accepted* grant.
  - Minor: the admin is guardian by default. A non-admin parent needs an admin-issued grant. The child is told in plain language.
  - Adult: the subject must accept the grant. An admin can curate without acceptance only with a recorded `consent_basis=substitute`.
  - Guests and carers never curate.
- **Endpoints** under `/api/curation`: policy, grants (issue/accept/revoke), `/{subject}/facts` (wraps `ingest(origin="curator")`), people (with deceased and photo), review queue, care-log, and `/me/known` (the transparency view).
  - A curator cannot edit a `user_stated` row. They can append a note, but never change the person's own words.
- **Brain:**
  - A pinned **Anchors** section at the top of the card, ≤450 chars, never budget-cut. It is code-templated ("Mia is 7. Her teacher is Mrs Lee. Peanut allergy.") and bypasses restraint for safety facts.
  - An anchors envelope through the seam (like `zoe-persona`, `zoe_flue_client.py:468-516`) so the **first turn** has it.
  - A deterministic care tier in `fast_tiers` beside `_ask_to_remember_tier` (`:489`) answers deceased scripts and "has anyone been today?".

### UI
- **"Tell Zoe about Mia" wizard:** 5 skippable steps (about, family tree with photos, school/teacher, routines, allergies/likes, life story). Its structure mirrors the Alzheimer's Society "This is me" form.
- **Family tree upgrade:** `people.html` gets a photo slot, a "deceased" toggle, and a "How should Zoe talk about them?" choice with three non-falsifying options: gentle truth, acknowledge-and-redirect, or pass to family.
- **"What Zoe knows about me":** every curated row with curator and date, plus Correct / Remove / "That's wrong". Kids get a picture version.
- **Policy panel:** preset plus toggles plus consent basis.
- **Curator review queue:** quarantined and disputed statements, never auto-approved.
- **Voice curation** ("Zoe, Mia's teacher is Mrs Lee"). It writes only if **all four** checks pass:
  1. the phrasing parses;
  2. the name resolves to exactly one member (ambiguity triggers a question);
  3. the speaker holds a grant;
  4. `speaker_verified is True`.
  - Otherwise: "I'd need to know it's you, add it in the app."

### Policy matrix
| | Adult | Kid | Care | Guest |
|---|---|---|---|---|
| Who curates | grants they accept | admin/guardian + grants | admin + grants (consent basis recorded) | nobody |
| Own speech → memory | on | quarantine (pending until approved) | quarantine only when it contradicts an anchor | off |
| Anchors pinned / locked | optional / no | on / yes | on / yes | — |
| Affect | per gate | per gate, no per-child scores | per gate | never |
| Family-visible counts | off | off | opt-in, counts only | — |
| Deceased scripts / today log | — | — | on / on | check-in only |

### Safety and ethics
- **Child privacy:** Australian OAIC assesses capacity case by case, with about 15 as the fallback presumption. The Children's Online Privacy Code is due by 10 Dec 2026. US COPPA's 2025 amendments ban indefinite retention of children's data.
  - So: publish a retention rule for children's rows.
  - The age threshold (15 vs 16) is disputed across sources, so get legal advice before enforcing.
- **Deceased relatives:** Zoe never says a dead person is alive. Alzheimer's Society guidance is case-by-case "best interests", and "therapeutic fibbing" is contested.
- **Forget shield:** the subject's forget beats a curator write, and only the person's own re-teach lifts it. Open question: should a care subject's confused "forget Mary" be held for family review?
- **Revocation** is immediate. Rows stay, marked `curator_revoked`, and the subject chooses whether to keep, anonymise or remove them.
- **Admin export reads everything** (`routers/memories.py:1296`). The curation log and the transparency view are the counterweight.

### External landscape
- **Amazon:** Parent Dashboard; Care Hub activity feed (category only, not content).
- **Google:** Family Link with Gemini for under-13s. Parents control access, not content.
- **ChatGPT teen accounts:** parents can turn memory off but not read chats, and the teen must accept the link. This is the closest consent model. **Zoe would be the first to let parents *add* memory, with provenance.**
- **Letta:** pinned core blocks, but the agent can edit them. That is exactly the gap our `anchor` flag closes.
- **mem0:** a user scope, but no authority tiers.
- **ElliQ:** family send photos and suggest reminders that the older adult approves.

### PR train (each ≤400 lines; flags `ZOE_MEMORY_POLICY`, `ZOE_CURATED_MEMORY` off|shadow|enforce, `ZOE_CARE_PRESET`)
| # | PR | Size |
|---|---|---|
| 0 | Opt-out covers voice writers, tested by writer class (bug fix, verify first) | S |
| 1 | `0044` tables + `memory_policy.py` (pure, cached) | M |
| 2 | `curated` class + `curator` writer + `anchor` flag + anchor-aware `find_conflict`. **Shadow first, for a week** | M |
| 3 | Policy enforcement at `ingest` (fail-closed for kid/care) | M |
| 4 | `/api/curation`: facts, grants, policy, log | M |
| 5 | Pinned Anchors in card + seam envelope (replay-gated) | M |
| 6 | `0045` people columns + media + photo route + deceased rendering | L (split) |
| 7 | Wizard + "What Zoe knows" + policy panel | L (split) |
| 8 | Voice curation tier (replay-gated) | M |
| 9 | Care tier: scripts + `0046` care log + today answers (replay-gated) | L |
| 10 | Curator review queue + opt-in counts | M |

**Tests** (each with a negative control):
- adult's own words beat curated (control: as operator, the user's write is held);
- anchor lock;
- inference never supersedes curated;
- provenance keys;
- grant checks;
- voice-curation fallbacks;
- walls still apply;
- forget shield;
- anchors always rendered;
- deceased tense;
- today-log "I haven't been told anyone has" rather than "no one";
- visibility;
- opt-out by writer class.

**Open questions:**
- `curated` rank as float 3.5, or renumber everything?
- Can an admin curate an adult without acceptance?
- Retention rule for children's rows.
- A care subject saying "forget X".
- Confused-speech counts for adults at all?
- Carer pass scope.
- Photo policy addendum.
- Deceased scripts: are only the 3 non-falsifying options allowed?
- Does quarantine apply to a kid's explicit "remember that…"?
- Legal review.

---

## DD-O — Orientation & day awareness + DD-V per-user voice tuning

### Corrections the research found
1. **A per-turn "who and where" block already exists and is ON:** `identity_facts.py:358-387`, `[Today — household context]`, flag `ZOE_IDENTITY_BLOCK`. `zoe_flue_client.py:2005` puts it first in the latest user message. **Orientation should extend this block, not add a new one.**
2. It shares the `[Today`/`[END Today]` label with `brief_first_turn`. Both are registered for stale-block elision (`zoe_flue_client.py:829-834`, `context-blocks.ts`).
3. `_synthesize_kokoro_sidecar(text, voice=None)` has **no speed parameter** (`tts_waterfall.py:248`). Speed comes only from `voice_delivery.resolve(text)`, which looks at the text and the clock. Nothing is per-user.
4. **Speaker ID is off by default** (`zoe_voice_daemon.py:377`). Per-turn identity comes from panel binding (`routers/voice_tts.py:3321-3331`).
5. **Endpointing runs before anyone knows who is speaking** (`_Endpointer` `:829`). Per-user patience therefore has to follow the panel's bound user.
6. **Small defects worth fixing anyway:**
   - `get_time` uses the host locale and time zone, not `ZOE_TIMEZONE` (`zoe-tools.ts:311-318`).
   - **Morning check-in only fires 07:30–07:59, although the comment says until 08:30** (`morning_checkin.py:239-241`). It also reads the time zone once at import.
   - The touch clock uses the browser's time zone (`home.html:1833`).

### Orientation design
- **Settings** go in the existing `user_preferences` JSON (`user_prefs.py`), under the key `orientation`:
  - `preset`, `show_year`, `ampm`, `daypart_words`
  - carer-written `place` (≤60 chars) and `reason` (≤80 chars), validated like persona boundaries
  - `visitors` (≤5)
  - `repeat_tolerance`
  - The minor flag auto-selects the kid preset.
- **Prompt-cache analysis (important):**
  - The system prompt (~2.1k tokens) and core tools (~0.7k) are prefix-cached. The measured p50 is 2,653 tokens reused and 112 re-prefilled.
  - Putting a changing time *there* would cost up to ~4.8 s of TTFT on a miss. The repo rule is "mutate the tail, never the head".
  - **So extend `identity_block()`.** It already sits in the latest user message, so this has zero prefix impact.
  - Standard line: "It is 10:42 am on Sunday 11 October 2026 (Perth). Next: Dr Lee at 2:00 pm." That is ~12–18 tokens, ~20–30 ms.
  - The care line is ~60–90 tokens, ~100–150 ms.
  - Render from `zoe_timezone()`, rounded to the minute.
- **Risk: stale copies.** Flue replays user messages verbatim. Without `ZOE_BRAIN_ELIDE_STALE_BLOCKS` (live state unknown), old clock lines pile up, ~300 tokens over 20 turns.
  - Later option: a dedicated `zoe-now` tail envelope that is stripped from every message and re-rendered on the last one only.
- **Touch:**
  - AM/PM, and the year under the care preset.
  - Time comes from the server via `/api/system/now`, not the browser.
  - The care "Today" screen is the existing `FULL.day` scene promoted to idle: huge time, "SUNDAY" at 56px, "11 October 2026" at 40px, a daypart chip, a "Why you're home" card, the next 3 items, and visitors.
  - Text at least 28px, contrast at least 7:1, no opacity-dimming of the date. The night date for care is ≥70% opacity at 40px.
- **Care behaviours:**
  - **Morning anchor:** reuses `brief_first_turn` and its arrival claim, with a care renderer, a fixed opening frame and a per-user hour.
  - **Repetition tolerance:** deterministic. A per-user normalised question hash and a daily counter. When it fires, the line instructs "answer the same way, briefly and warmly". The count is never stored in memory.
- **Evidence:**
  - Reality orientation (Cochrane CD001119, withdrawn and replaced by the cognitive-stimulation review CD005562) gave some benefit but was criticised when done mechanically. So keep orientation ambient, never a quiz.
  - Validation therapy (CD001394): insufficient evidence, so use it as a tone guide only.
  - HELP: orientation board and schedule is one component.
  - DayClox-type clocks: no controlled studies. Present them as assistive, with no outcome claims.

### Voice tuning design
- **Setting:** `voice_profile` in `user_preferences`:
  - `voice` (null means the household voice)
  - `speed` 0.75–1.25, quantised to 0.05
  - `patience` default / patient / very_patient
  - `verbosity` default / short
  - `confirm_style` default / simple
  - `preset`
  - Resolution order: per-call, then user, then household, then env.
- **Plumbing:**
  - Resolve once per turn after `effective_user`.
  - Add `_synthesize_kokoro_sidecar(..., speed=)` plus a turn-scoped object across ~14 call sites (`voice_tts.py:2225…`, `main.py:3031-3160`), with a contextvar fallback. Test that it survives `StreamingResponse`.
  - The early opener ack (`voice_tts.py:5352`) needs panel-user resolution at `turn_stream` entry.
  - **Speed formula:** `min(profile.speed, delivery.speed)`, so a "warm" profile never speeds a care user up.
- **Endpoint patience:**
  - Add `_Endpointer(silence_s, hesitation_ms)` overrides, fed by a per-panel profile sync (reusing the `_sync_speaker_profiles` pattern).
  - Care values: +0.7–1.0 s tail, hesitation up to 1500 ms, record cap 20 s (now 12 s), initial wait 3 s, follow-up window 8 s.
  - **Disable `ZOE_SPECULATIVE_TURN` for patient profiles**, because speculation assumes a short tail.
- **Verbosity:** a one-line tail directive, as in the legacy `_VOICE_BREVITY` (`zoe_core_client.py:680`). Don't rely on the persona layer, which refuses minors.
- **Performance trap:** the Kokoro phrase cache only serves speed 1.0 (`kokoro_sidecar.py:808`).
  - A 0.85 care user misses the cache on every ack: 2 ms becomes ~210–310 ms. First units take 0.3–0.5 s.
  - **Fixes:** a speed-aware cache key (`voice|speed|text`) and a per-profile prewarm.
  - The Pi's "thinking" buffer WAVs (af_sky, 1.0) won't match a non-default voice. Make them voice-aware or suppress them.
  - Keep `voice_stitch` off for non-default profiles.
- **Evidence:**
  - CHI 2026 (arXiv 2602.23436): ASR errors are significantly higher with dementia, and pause ratio is a predictor. That justifies longer tails.
  - On speech rate: hearing loss drives difficulty with fast speech (Janse). No authoritative target rate was found.
  - So 0.85 is an ear-tuned starting point (the operator previously judged 0.84–0.88 fine and 0.80 too slow).

### Matrices
| | Default | Kid | Care | Guest |
|---|---|---|---|---|
| Date/time line | on | on | long form | date only |
| Place/reason/visitors | off | off | on | off |
| Touch | AM/PM | AM/PM | year + bigger | AM/PM |
| Morning brief | 07:30 | — | per-user, 08:00 | — |
| Speed | 1.0 | 1.05–1.1 | 0.85 | 1.0 |
| End-of-speech | today | today | +0.7–1.0 s, 20 s cap | today |
| Verbosity / confirms | default | short, playful | short / yes-no | default |

### PR trains
- **Orientation:**
  - A1: renderer + extend `identity_block` + `get_time` time-zone fix, ~250 lines, flag `ZOE_ORIENT_LINE`, replay-gated.
  - A2: prefs API.
  - A3: optional `zoe-now` envelope.
  - A4: touch AM/PM/year/server time/care Today screen/contrast.
  - A5: settings form.
  - A6: care morning anchor + **07:30 window bug fix** (could ship standalone first).
  - A7: repetition counter.
- **Voice:**
  - B1: store/API.
  - B2: per-turn resolution + `speed=` + compose rule (`ZOE_USER_VOICE`).
  - B3: speed-aware Kokoro cache + prewarm + Pi buffer phrases.
  - B4: daemon endpoint overrides + no speculation for patient.
  - B5: verbosity/confirm.
  - B6: settings UI + preview.
  - B7: one-tap care preset tying A and B together.
- **Replay gating:**
  - Run under `flock`.
  - With the flag off, the payload must be byte-identical (no `speed` key).
  - Measure per-profile first-chunk with `measure_tts.py`, plus the cache hit rate.
  - A VAD replay with a patient profile.
  - **A small consented corpus of slow/pausing speech is needed**, because today's corpus is Jason's voice only.

### Cross-cutting risks and open questions
- **Risks:**
  - A visitor at Dad's panel inherits slow speech and patience (the safe direction). Show a visible "Care mode" indicator.
  - "Why you're home" shows on a lit screen. Add a manual hide toggle.
  - Stale visitor entries expire after their day. Show nothing rather than a guess.
- **Open questions:**
  - Is elision ON live?
  - Care fields per user or per panel?
  - Who edits care fields?
  - Kid voice choice (ear test).
  - Should visitors come from the calendar?
  - Record cap 20 s?
  - Night screen year/daypart?
  - A consented slow-speech corpus?
  - Ship the 07:30 bug fix first?

---

## DD-F — Family signals & digest

### Findings that change the design
1. **zoe-data can already send Telegram to a user:** `auth_handoff._telegram_send` (`auth_handoff.py:403-415`) posts `sendMessage` with `ZOE_TELEGRAM_BOT_TOKEN` to the member's `telegram_id`. It is used only for "send to my phone" today. **Extract it into a shared `telegram_out.py`.**
2. **The bot can't receive button taps:** `allowed_updates: ['message']` (`labs/flue-zoe-telegram-2x/src/app.ts:199-205`), and there is no `callback_query` handler. Ack buttons need a bot change, and that directory auto-deploys on merge.
3. **No household or guardian model exists.** Grants need a new table.
4. **⚠ Existing leak, worth its own ticket:** panel sockets join the `"all"` channel with no user mapping (`push.py:43-52,103-105`). `distress_handoff._notify` broadcasts on `"all"` naming the member (`distress_handoff.py:264-269`), **so it reaches shared kiosk panels.** Family signals must never use that path.
5. **Governance blocks mood trends:** no per-child mood trajectories, and adults only with opt-in plus enforceable identity (`emotional-safety-note.md:92-109,135,140`). **Mood is out of v1.** The care preset uses a 1–5 wellbeing score the subject gives instead.
6. **There are no presence sensors**, and the house has zero `binary_sensor` entities. "No activity" means "no interaction with Zoe", and the wording must say so.
7. **Pull-not-push is policy.** Family → member messages ride the pull lane (orb dot, "what's up?").

### Data model (0044–0046)
- **`family_grants`:**
  - Columns: subject, viewer, signals[], channels (in_app/push/telegram), schedule, detail minimal|full, status proposed|active|paused|revoked, consent_basis self|delegate|guardian, consent_by/at, viewer_accepted_at, review_at (default 90 days), version.
  - Two-sided: the viewer must accept, and the subject can pause or revoke instantly.
- **`family_signals`:** enums and counts only, **with no free-text column by design**. Medication is a slot (am/noon/pm/night), never a drug name.
- **`family_daily`** rollup: first/last activity, turns, doses, meals/fluids, repeat counts by *intent class*, night activity, optional wellbeing score.
- **`family_deliveries`** ledger: exact body sent, seen_at, ack. This is the single source for the transparency view.
- **`family_messages`:** family → member text, voice or today-log line.
- **Signal registry:**
  - `home_safe` (explicit, for anyone).
  - `first/last_activity`: kids only if the guardian opts in and the child can see it.
  - `no_activity`: care only.
  - Doses: kids guardian-only and visible to the child.
  - Meals/fluids are confirmed by the subject.
  - `repeat_count`, `night_activity` and `wellbeing`: care only, **never** for minors.
  - `emergency`: everyone; a minor is always told.
- **Digest is deterministic, no LLM.**
  - The 4B model is a single slot behind live voice.
  - A summariser could invent a "she's fine".
  - The digest must be provably free of conversation content. A test checks the module imports no model client.
  - Baselines are 14-day medians.
  - Example: "Mum: first activity 7:42, last 20:15. Morning tablets taken, evening not yet confirmed. Asked the date 6 times (usual 1–2)."
- **Retention:** raw events 14 days, daily rollup 180 days, deliveries 90 days, messages 30 days. Revoke means stop collecting and offer deletion. Hook into the forget cascade.

### Delivery
- **A new `family/deliver.py`, not `fire_notification`.** That path is wrong here because it LLM-composes, creates a chat deep link, defers emergencies in quiet hours, and has no Telegram.
- **Flow:** ledger row → notifications row → `send_push_to_user` → Telegram. **Never `"all"`.** Use a dedicated `family:<viewer>` WebSocket channel instead.
- **Telegram:**
  - Plain text, no Markdown on dynamic data.
  - About 1.1 s spacing per chat; honour 429 `retry_after`.
  - Coalesce the same alert for 30 min; one digest per day.
  - Footer: "Sent because Mum shares this with you."
  - Minimal wording by default, never drug names or quotes.
  - Tell users that bot messages transit Telegram's cloud.
- **Quiet hours:** digest and non-urgent alerts are held. `emergency` and urgent `dose_missed` go through.
- **Acks:**
  - Phase 1: a URL button opening a signed "Seen / I'm calling" page (no bot change).
  - Phase 2: inline buttons via `callback_query`.
  - An ack stops escalation and tells the other viewers ("Dana is calling").
  - An unacked emergency goes to the next viewer after N minutes. Never auto-call.
- **Access:**
  - Only the viewer sees deliveries. **Admin-by-role gets 403.**
  - The subject sees everything sent about them.
  - Guests and `bound_guest` see nothing.
  - The distress cooldown is an in-process dict that resets on restart. Move the family path to the DB.

### UI
- **Settings section** next to Telegram (`settings.html:1006`): person, preset, signals, channel, digest time. Plain consent text ("they will not see what you say to Zoe"). The viewer accepts. Review date and a big Pause button.
- **"What my family sees":** a live digest preview from the same renderer, plus the last 30 sent bodies verbatim. A test asserts what's shown equals what was sent. Also available by voice ("what does my family see?") as a deterministic tier.
- **Viewer page and GP export:** a print page with 8 weeks of counts, labelled "counts from interaction with a home assistant, not clinical", generated on demand by the subject or delegate.
- **Family → member:**
  - Composer on the page plus a Telegram `/tell Mum …`. Voice notes to the bot are already transcribed (`routers/telegram_media.py`).
  - Delivered through the pull inbox and orb dot. A today-log line shows on the day card.
  - **Voice messages:** v1 transcribes and Zoe speaks it ("Dana says…"). v2 stores the real audio, which needs a daemon change, but real voice is most of the emotional value.

### Privacy and ethics
- **Signals, never transcripts**, enforced by the schema plus a test.
- **Adults:** subject-initiated, viewer-accepted, per signal, revocable, reviewed. The viewer is told when sharing is paused, without a reason.
- **Impaired capacity:** consent is the person's whenever they can give it. A delegate grant needs a named adult, the subject present where possible, and the subject still sees the view and can say "stop". The legal delegate threshold (WA guardianship) is for counsel.
- **Teens:** no silent monitoring; every passive signal is visible to the teen.
- **Law:** the Privacy Act's APPs likely don't apply to household affairs (s 16, s 7B), so the repo's own policy is the standard.
- **Clinical:** never write "delirium" or "decline". Ask the GP whether the counts are actually useful before building the weekly trend.

### External
- Alexa Together (activity feed; "no longer available" since 21 May 2025; reason unknown).
- Life360 "No Show" (grace period plus a pre-set expectation).
- **Apple Check In** (subject-initiated; the best model for teen "home safe").
- Google Family Link.
- **ElliQ caregiver app** (consent-gated last activity plus messaging; the closest match).
- Hero (dose alerts).

### Matrix, PR train and tests
| | Default | Kid | Care |
|---|---|---|---|
| Grant created by | subject | guardian (shown to child) | subject or delegate |
| Signals | home_safe, emergency | home_safe, guardian-opt-in activity, doses, emergency | all except mood |
| Channels | in_app, push | in_app, push | + Telegram |
| Digest | off | off | 19:30 nightly + weekly Sunday |
| Review | 90 d | 30 d | 30 d |

**PR train** (flag `ZOE_FAMILY_SIGNALS` off|shadow|on, plus `ZOE_FAMILY_TELEGRAM` and `ZOE_FAMILY_MESSAGES`):
1. Registry, presets and validators + `family_grants` migration.
2. Grants API with two-sided accept.
3. Collector from `chat_messages` + daily rollup (shadow; no voice-path edit).
4. Delivery core with dedicated WebSocket channel and quiet hours.
5. Telegram sender extracted + URL ack page.
6. Deterministic evening digest.
7. Settings UI + "what my family sees".
8. `no_activity` baseline + emergency through the family path.
9. Bot `callback_query` acks.
10. Family → member via the pull lane (check replay-gate patterns).
11. Weekly trend + GP export.
12. Voice-message audio + panel playback.

**Tests:** no free text (control: add a text column and it fails), minor limits, digest equals delivery, viewer isolation, **no panel leak** (control: the `"all"` path), revoke, quiet hours, Telegram 429/400/token-never-logged, determinism, baseline gating.

**Open questions:**
- Do an adult's distress pings reach children without opt-in?
- Tell a child in the moment that a trusted adult was notified?
- Delegate legal threshold?
- Is the wellbeing score an "affective record" under the note?
- Are the counts useful to the GP?
- Notify the viewer on pause?
- Do outside carers need `users` rows?
- Bot auto-deploy for acks?
- Family-admin ownership for kids?

---

## DD-Rx: Medications (any user: self, child, pet; care preset on top)

### Findings that change the design
1. **Correction to my earlier plan: don't build meds on `reminders`.** The `reminders` table has one `acknowledged` bit, a single `due_time` and one RRULE (`alembic/0001:136-155`). It has no subject, no end date and no dose history. Acknowledging is owner-only (403 at `routers/reminders.py:381-400`). Acknowledging a recurring reminder records nothing about the occurrence (`:402-418`).
2. **`_fire_reminder` silently voids any job whose `item_id` isn't a reminder row** (`proactive/triggers/reminders.py:89-90`). Meds therefore need their own fire path.
3. **Quiet hours defer, they don't exempt.** `fire_notification` reschedules to 07:00 (`engine.py:99-137`) unless `force_send` is set, and `_fire_reminder` never passes it. **A 22:30 dose arrives about 8.5 hours late.**
4. **The slow loop skips quiet hours and runs every 300 s** (`engine.py:399-400,31`). That is too coarse for +10 and +20 minute repeats, and blind overnight.
5. **Likely bug (inferred from reading the code, not run): creating a reminder doesn't schedule it.** `create_reminder_record` only inserts (`reminder_service.py:187-221`) and the scan schedules it later. A reminder created at 22:30 for 23:00 isn't scanned until 07:00, when it's already past and gets skipped (`reminder_scan.py:309-323`).
6. **`fired=1` means "push attempted", not "heard".** It is set even with zero push subscribers (`engine.py:195-207`). The real proof of delivery is `voice_announcements.played_at` (`voice_announce.py:231-247`).
7. **A safety net already exists.** `_distress_tier` runs first (`fast_tiers.py:769-776`), and the distress lexicon catches "took / too many / pills" (`lexicons_data/en.json:782`). Accidental double doses need their own branch.
8. **Pets are never people rows** (`named_relations.py:25`, `people_roles.py:18`), so medications must own the pet subject. The child flag is `member_modes.minor`. Following the repo's one-writer convention, all writes go through a single `medication_service.py`.

### Data model (`0044`; check open branches for a clash first)
- **`medications`**
  - Ownership: `owner_user_id`; `subject_kind self|person|pet`; `subject_person_id`; `subject_label`.
  - What it is: `name`; `strength_text`; `form_text`; `instructions_text`, stored verbatim and never interpreted; `kind scheduled|prn`.
  - Schedule: `times_json` (several times per day); `days_rrule`, reusing the recurrence subset; `start_date`/`end_date` for a course.
  - Limits: `prn_min_gap_min` and `prn_max_per_day`, both entered by the user from the label; `high_risk`; `late_after_min`; `missed_after_min`.
  - Sharing: `visibility personal|family`.
  - Supply: `supply_count`, `units_per_dose`, `refill_threshold_days`, `last_refill_prompt_at`.
  - Lifecycle: `active`, `deleted`, timestamps.
- **`dose_events`**, append-only and never deleted:
  - Timing: `due_at_utc`, `local_date`, `local_time`, `tz`.
  - State: `state due|reminded|taken|skipped|missed|uncertain|superseded`; `taken_at_utc`.
  - Who and how: `logged_by_user_id`; `source voice|touch|telegram|rest|backfill`.
  - Escalation: `step`, `next_action_at`, `claimed_at`.
  - `ack_evidence`, e.g. the announcement id plus `played_at`.
  - `idem_key UNIQUE` (`med|date|time`), which is the double-log guard.
  - Corrections create a new row and supersede the old one.
- **Who may log:** any member with access, recorded in `logged_by`. This deliberately differs from reminders' owner-only rule, so either parent can log a child's dose.
- **Supply:** decremented on `taken` only. Days left = supply / (units × doses per day). Below the threshold it calls `list_service.add_item_to_list` (`list_service.py:134`), de-duplicated per window. Always worded as an estimate: "about 4 days left, check the pack".
- **Recommendation: a separate scheduler with shared delivery.**
  - A 60-second APScheduler `medication_tick` (`scheduler.py:149`) materialises today's doses idempotently.
  - It advances any `next_action_at <= now`, using the atomic claim pattern (`reminders.py:54-65`).
  - It reuses `_send_push`, `voice_announce` and `_speak_on_panel` through a small `deliver(user,text,guest_safe,force)` helper. It does not use `fire_notification`.
  - No dual-write into `reminders`.

### Dose state machine and escalation
- **Window:** late after 60 min. Missed at the earlier of 120 min or half the interval to the next dose. For `high_risk` medicines the window is not widened and the alert comes sooner. Windows only control when Zoe stops reminding, never what the person should do.
- **Time zones:** store wall time plus zone, with the same DST gap/fold handling as `reminder_recurrence.py:198-216`. Perth has no DST, but tests must use a DST zone. Travel stays on household wall time in v1, as a stated limitation.
- **Double log:** a repeat "I took it" gets "Already logged at 8:05", and nothing is written.
- **"I think so":** recorded as `uncertain`, never `taken`.
- **Escalation ladder:**
  - T+0: push and panel card; spoken only if enabled and at the owner tier.
  - T+10: repeat.
  - T+20: final repeat.
  - T+30: family alert (care preset only), at most one per dose and three per day.
  - End of window: `missed`, terminal.
- **Hard caps:** three prompts per dose. Nothing after `missed`. No minor gets "missed" pushes. Any surface that logs the dose silences the rest.

### Voice: deterministic templates, the brain never answers dose questions
- **Intents:** `med_log_taken`, `med_log_skipped`, `med_status`, `med_next`, `med_add` (slot-fill then yes/no), `med_refill`, `med_prn_ask`.
- **Wiring:** added to `USER_SCOPED_INTENTS`, `_GUEST_GATED_INTENTS` and the Tier-0 defer list. Run as a direct execute, like `_execute_reminder_create_direct` (`intent_router.py:2484`).
- **Lookup errors or ambiguity get a template too, never a fall-through to the brain.** Medicine-like phrasings that match nothing get a catch-all template.
- **Memory:** dose turns are excluded from memory writers (`memory_provenance.mark_turn`, `:92`), so a dose is never "remembered" and then used to answer later.
- **Wording:**
  - Taken: "Done. I've logged your 8:00 tablets at 8:04."
  - Late: "…recorded as late."
  - Already logged: "I already have that one logged at 8:04. I won't log it again. Please don't take another."
  - No record: "I don't have a record of your 8:00 tablets today. Please check your pack before you take anything. If it isn't clear whether you took them, don't take another. Ask your pharmacist."
  - Missed: "marked missed. I can't tell you whether to take it now. The leaflet or your pharmacist can."
  - Not due yet: offer an early log with confirmation; no offer for `high_risk` medicines.
  - PRN: quotes the user's own saved gap and maximum, e.g. "next okay from 6:00pm", or "that would be your 4th today… I can't log another."
  - **Accidental double or wrong medicine:** "Please call the Poisons Information Centre now on 13 11 26. If they're drowsy, unwell, or having trouble breathing, call 000." The number is configurable, and the dose is not logged.
  - Advice requests ("should I take more", "is it safe", "double up"): "Dose, timing and missed-dose questions are for your pharmacist or doctor. I can only tell you what's been logged."
  - "I took all my tablets" must still route to distress. This needs a test.
- **Shared-panel privacy:**
  - Speech happens only at `TIER_OWNER`.
  - `bound_guest` hears "Zoe has a reminder for you. Tap your name to see it" (`engine.py:333-338` `spoken_guest_safe`).
  - Status answers need the scope gate plus `speaker_verified`.
  - Medications are `personal` by default, matching the calendar health-private rule (`calendar_service.py:23-60`).
  - "Say medicine names aloud" is off by default.

### UI
- **Desktop `medicines.html`:**
  - Per-subject list showing next dose, supply, course end and PRN guard.
  - Dose timeline with who logged it and from where.
  - Monthly strip.
  - Add/edit form with the copy "Enter what your label says. Zoe does not check doses."
- **Touch card:** big Taken and Skip buttons. The medicine name shows only at the owner tier. It posts to a new idempotent `POST /api/medications/doses/{id}/take`. It does **not** reuse the reminders widget's acknowledge call.
- **Telegram:** a family member's text reply ("Mum took her tablets") already reaches the fast tier through the bot's chat lane and is logged with `source=telegram`. Inline buttons come later, as a bot PR.

### Settings matrix (`user_preferences.prefs.medication`; no migration)
| | Default | Care | Kid (minor subject) |
|---|---|---|---|
| Push + panel card | on | on | to the parent only |
| Spoken reminder | off | on (owner tier) | off |
| Medicine name aloud | off | on at owner tier | never |
| Repeats +10/+20 | off | on | off |
| Quiet-hours exemption | off | on (doses only) | off |
| 30-min family alert | off | on (Telegram, else bell) | managing parent |
| Conservative "no record" answer | **always (safety rule, no toggle)** | always | always |
| Early-log offer | on | off for high-risk | off |
| Refill → shopping list | ask | auto | ask the parent |

A minor cannot add or edit medicines.

### Clinical safety and regulation
- **Missed-dose rules depend on the drug.** NHS SPS gives rough rules (up to 2 hours late is usually OK, never double up) with exceptions: warfarin, weekly methotrexate, the pill, insulin, anticonvulsants. Every Australian CMI leaflet has a "what to do if you miss a dose" section. The higher-risk groups are anticoagulants, anti-epileptics, psychiatric and Parkinson's medicines.
  - **So v1 never tells anyone to take a missed dose.**
  - Ticking `high_risk` is prompted by a suggested-classes data list. It shortens the alert and adds "call your pharmacist today".
- **PRN:** the user's own limits from the label. Add "check other medicines for the same ingredient" for paracetamol (healthdirect).
- **No interaction checker in v1.** A wrong or stale one is worse than none.
- **Position Zoe as an alongside tool** to a pharmacy Webster-pak and pharmacist review, never a replacement (Better Health Victoria).
- **TGA:** software is excluded from medical-device regulation as a "consumer health product" only if no function provides specific treatment or suggestions. The TGA's regulated example is an app that calculates insulin doses.
  - **This is why the design has no dose calculation, no timing advice, no interactions and no "you may take it now".**
  - That a plain reminder qualifies is the agent's reading of secondary sources. A regulatory adviser should confirm it before release.
  - Dose logs are health information. Local-first is a strength. The only thing that leaves the house is the opt-in Telegram alert. Logs record ids only, never text.

### External landscape
- **Medisafe:** Medfriend can view only; alert about 1 hour after an unmarked dose; dependent profiles.
- **Apple Health Medications (iOS 16+):** Taken/Skipped, a follow-up after 30 minutes, "remind me in 10 minutes", time-zone prompt; interactions in the US only.
- **Hero:** sound and light ×3, then app, then caregiver at about 15 minutes; tamper passcode.
- **Alexa:** a 2019 Omnicell/Giant Eagle prescription skill; unclear if it still exists.
- **Pharmacy packs:** PillPack, Webster-pak.
- **Common pattern:** a caregiver is involved 15–30 minutes after an unlogged dose.
- **Alarm fatigue:** evidence is thin and qualitative. Hence the 3-prompt cap, silence on log, one reminder per time slot rather than per medicine, and persistent time shifts ("after dinner") instead of repeated snoozes.

### PR train
Flags: `ZOE_MEDICATIONS` (off, shadow, on), `ZOE_MEDICATIONS_SPOKEN`, `ZOE_MEDICATIONS_FAMILY_ALERT`.

| # | PR | Size | Notes |
|---|---|---|---|
| 1 | `0044` + `medication_service.py` | M | Idempotent `idem_key` test |
| 2 | `medication_rules.py`, pure functions: windows, PRN, supply, DST | M | Negative control: missed never becomes "take now" |
| 3 | REST + permissions | M | Guest 403; parent can log for child; a stranger cannot |
| 4 | Tick + delivery + ladder + quiet-hours exemption | L | Claim-race test; caps; exemption-off control |
| 5 | Voice intents + templates + accidental-double + refusals + memory exclusion | L | **Replay-gated**; "took all my tablets" goes to distress |
| 6 | Spoken + privacy + `played_at` evidence | M | `bound_guest` never hears a drug name |
| 7 | Desktop + touch UI | L | Bump SW_VERSION; XSS test |
| 8 | Settings + care preset | S | |
| 9 | Supply → shopping list | S | |
| 10 | Family alert | M | Cap; non-member ack refused |
| 11 | Inline Telegram buttons | S–M | Optional |

**Risks:**
- Over-promising safety.
- Logging a dose to the wrong subject. Mitigated by a clarifying question and a read-back.
- Notification loss. The family alert keys off dose state, not delivery.
- Duplicate logs across surfaces (`idem_key`).
- A child's medicine named aloud.
- DST bugs hidden by Perth having no DST.
- A medicine phrasing the router misses, which reaches the brain (catch-all template).

**Open questions:**
- Teen medicines visible to a parent by default?
- Alert one contact or all admins?
- Quiet-hours exemption default?
- Per-user time zone in v1?
- Do PRN doses count against supply?
- TGA adviser review?
- Pets exempt from the health-sensitivity default?
- Webster-pak import? (Defer, since it's paper, not an API.)
- Should a pharmacist review the template wording?

---

## DD-H: Help and emergency (every household)

### Headline findings
1. **On the chat/Telegram path and the brain path, distress runs before any fast-tier intent:**
   - `fast_tiers.py:769-776` runs `_distress_tier` first.
   - `zoe_flue_client.py:1792-1799` runs `distress_handoff.handle` first.
2. **⚠ On the voice lane, three early exits run before distress** (`routers/voice_tts.py::voice_command`):
   - the speculation hold (`:3203`);
   - **the active-form cancel check, which matches cancel words as a substring** (`:3405-3410`). With a form open, "I can't **stop** the bleeding" or "fire **close** to the house" returns "Okay, cancelled." **This is a real bug, independent of this feature.**
   - a pending yes/no (`:3581`).
   - Distress first runs at the conversation tiers (`:3703`).
3. **Distress's `danger` class catches some emergencies but misses most.** Results from probing `detect()`:
   - **Caught:** "there's a fire", "call an ambulance", "call 000", "someone is breaking in", "heart attack", "dad has collapsed".
   - **Missed:** "Zoe help", "help me", "I've fallen", "I can't get up", "I have chest pain", "he is choking", "seizure", "I smell gas", "bleeding badly", "grandpa fell over", "home alone and scared", and "someone**'s** breaking in" (the apostrophe folding misses it).
   - **False positives:** "what do I do if there is a fire", "a fire in my belly for justice".
4. **Distress's contact notification is wrong for emergencies:**
   - The copy is self-harm wording.
   - It goes to one contact only.
   - It is in-app plus a WebSocket broadcast, with no push and no Telegram.
   - **A 6 h in-process cooldown means a second fall within 6 h notifies nobody**, and a restart repeats the first notice (`distress_handoff.py:39,241-273`).
5. **The announcement queue is claim-once,** so one row is spoken by one panel only (`voice_announce.py:30-39,146,200-215`). A house-wide alarm needs one row per panel plus a `PANEL_SCOPED_TRIGGERS` entry. The queue also has no priority, and `_daemon_busy` defers announcements with no bypass (`zoe_voice_daemon.py:3434`).
6. **Telegram send exists only as a special case:** `auth_handoff._telegram_send` (`:403-417`). The bot has no `callback_query` handler and auto-deploys on merge.
7. **If the Jetson is down, nothing works.** The Pi only speaks an espeak "Zoe is not available" line (`daemon:3122-3125`). Adding "If this is an emergency, call 000." is the one piece that survives an outage.

### Design
- **`emergency_detect.py` plus `lexicons_data/<lang>.json` `emergency` section.** It mirrors distress: pure, data-driven, reads no words in code, en/es first, with fold forms for contractions.
- **Tiers:**
  - **`act`** (immediate, no question): fire/smoke, gas, intruder with threat, not breathing, unconscious, severe bleeding, choking, seizure, anaphylaxis, overdose/poisoning, "call an ambulance/police/000", **chest pain, stroke signs, can't breathe**.
  - **`check`** (ask once): bare "help"/"Zoe help", "I've fallen", "can't get up", "feel faint", a child scared or alone.
  - **`topical`/`none`:** question shape ("what should I do if…", "fire drill", "fire up the grill").
  - **The asymmetry:** a guard may demote `act` to `check`, but never to `none`.
  - "Help" closure: only whole-clause, "help me/us", or "please help". Never "help me with…".
- **Recall:** distress measured about ⅔ on unseen phrasings, so expect something similar. Mitigations are the `check` tier and an optional brain tool `raise_emergency_check` for lexicon misses.
- **Dialog state machine,** persisted in `emergency_events` (not memory):
  - IDLE → (act) ESCALATED.
  - IDLE → (check) ASKING: "I'm here. Are you hurt?", with a server timer of 15 s (25 s in care mode).
  - From ASKING: yes, unclear, or silence → ESCALATED; a clear "no / I'm OK / false alarm" → CLEARED.
  - From ESCALATED: ACK → RESOLVED; a 30 min cap → EXPIRED.
  - **Unclear means escalate.** This is the opposite of `_pending_decision`, which treats unclear as "don't act", so that function must not be reused.
  - A live event intercepts the next utterance before the form/confirm tiers.
- **Fan-out, in parallel:**
  - push plus in-app (direct, `force_send`);
  - Telegram (a general sender extracted from `auth_handoff`, with an "I'm on it" inline button);
  - **every other panel** gets its own `emergency_alert` row (panel-scoped, priority claim, busy bypass, longer TTL) plus a kiosk toast;
  - success is counted by `mark_played`.
- **Announcement copy:** "Dad needs help in the bedroom" (name from `resolve_identity`, room from `panels.room_id`). For a guest: "Someone at the hall panel needs help." Never the user's own words.
- **Repeat until acknowledged:** panels every 60 s, Telegram every 2 min, stopping at ACK or 30 min.
- **On ACK:** the origin panel says "Jason has seen it and is calling you", the other panels say "Jason is on it", and the Telegram message is edited to "Acknowledged by Jason 7:42".
- **Location:** room plus the stored household address read into the 000 guidance. No false precision.
- **Dedupe and cooldowns:**
  - Further utterances from the same panel join the existing event.
  - The `check` tier gets a ~2 min suppression after CLEARED. **The `act` tier is never suppressed.**
  - A household cap of 3 events/hour, then speak-only plus an admin flag, to break TV loops.
- **Privacy:** off-record via `memory_provenance.mark_turn`. Logs carry ids/enums only. `dry`/`replay_isolation` is honoured so replays never alert real people.
- **Tables (0044):** `emergency_contacts` (≤5, at least one required before `enforce`), `emergency_events` (no transcript), and a household address in `app_settings`.

### Can Zoe call 000? **No, never. She tells people to call, and alerts humans.**
- Apple Watch can place the call itself because it is a carrier-registered device with location. Alarm monitoring is human-mediated: a monitoring centre operator calls 000.
- **I found no ACMA statement permitting or banning automated 000 dialling.** It needs an ACMA enquiry. The binding rule is the Telecommunications (Emergency Call Service) Determination 2019, amended 28 Oct 2024.
- VoIP 000 calls carry the *registered* address and fail in outages, and Zoe has no SIM.
- Google Nest emergency calling is US-only. Telstra's voice calling via Google speakers ended in Sept 2023.
- 106 is text relay for TTY users. An SMS relay route needs verifying with the NRS. Recommend the **Emergency+** app.
- **What Zoe says:** "Call 000 now. Ask for an ambulance. Say your address: {address}. Stay on the line." If no phone is reachable: "I can't call for you. I'm telling {contact} now." A child gets simpler wording.
- **Never claim a call or dispatch happened.**

### Medical-advice guardrail (all users)
- **Deterministic red flags go to `act`:** chest pain, FAST stroke signs, breathing trouble, severe bleeding, unconsciousness, anaphylaxis, overdose/poisoning, sudden severe headache or confusion.
  - Stroke Foundation: face, arms, speech, then call 000.
  - Heart Foundation: chest pain spreading to arm/neck/jaw/back plus breathlessness/sweating/nausea.
- **A fixed guard block on health-topic brain turns** (in the `soul.ts` prompt), using the existing health lexicon.
  - It makes "no medical advice beyond see a doctor" a **non-optional floor**.
  - Zoe may give general information, admit uncertainty, refer to a GP, and give **healthdirect 1800 022 222**.
  - Zoe never diagnoses, gives doses, says start/stop/skip, interprets results, says "you're fine", or talks someone out of 000.
  - A deterministic tripwire on replies runs in shadow first.
- **Numbers to verify before shipping:** Poisons 13 11 26, IDCARE 1800 595 160, the 112 wording, and the NRS SMS route.

### Care preset additions
- **Confusion lexicon** ("where am I", "what day is it" repeated, "I want to go home" while at home, "where's my [deceased]"): gentle orientation, never correct or argue. Family is told only on repetition and only with opt-in.
- **Fear lexicon** ("someone's in the house"): ask, then escalate.
- **No-activity-by-10am:** a voice nudge at 10:00, then an alert at about 10:15. It counts voice turns, real taps, and opt-in HA motion. **It does not count the panel heartbeat** (`ui_panel_sessions.last_seen_at`, which refreshes every few seconds). Away mode suppresses it.
- **Scam guard:** a deterministic lexicon (gift cards, a pressured transfer, PIN/password/Medicare number, remote access, "someone called saying…"). It gives a fixed warning: hang up, call the bank on the card's number, Scamwatch, IDCARE. Never claim detection, and never store PINs.
  - In 2023, over-65s were the only group whose losses rose: +13.3%, to $120M.
- **Door and motion sensors:** opt-in only, a consent question, high false-alarm risk.

### External
- **Alexa Emergency Assist:** a trained agent dispatches, up to 25 contacts, about $5.99/mo, US-first. It works *because* of the human agent, which Zoe will not have.
- **Nest:** US-only.
- **Apple Watch Fall Detection:** a 30 s countdown with cancel, a good pattern, but it needs a body sensor.
- **AU pendants (MePACS, LiveLife):** 24/7 AU centres, "cannot detect every fall", 4G since the 3G shutdown, funded via Support at Home/NDIS. **For Dad, recommend a pendant; Zoe is a supplement.**
- **Ring/SimpliSafe:** contacts first, then dispatch, which is the model for repeat-until-ack.
- **Fall-detection evidence:** a lab smartwatch caught 77% with 1.7% false positives. Real-world results are worse, and one older study had 83 of 84 alarms false. **Zoe catches only a fall the person can announce, never an unconscious person, and the wording must say so.**

### Settings matrix
| | Default | Kid | Care | Guest |
|---|---|---|---|---|
| Emergency tier | on once contacts set | on | on | on |
| Ask "are you hurt?" | check tier | short wording; skipped when scared/alone | check, 25 s | check |
| Act tier | no question | no question | no question | no question |
| Other panels announce | yes | yes, calm wording | yes | "Someone at {panel}" |
| Who can cancel | adult at origin | **adult only**; a child's "I'm OK" sends an info note only | person or contact | origin |
| Medical guardrail | on | on, child-safe | on | on |
| Scam / confusion / no-activity | off | off | on | off |
| Sensors | off | off | opt-in | off |

### PR train
| # | PR | Lines | Flag | Voice path? |
|---|---|---|---|---|
| 1 | Detector + en/es lexicon + labelled fixture + negative controls | ~350 | — | no |
| 2 | Shadow wiring in fast_tiers + flue | ~120 | `ZOE_EMERGENCY=shadow` | **yes** |
| 3 | 0044 contacts/events + settings + address | ~350 | — | no |
| 4 | State machine + voice hook before `:3405` + **fix substring cancel `:3410`** | ~380 | enforce, `_ASK` | **yes** |
| 5 | Per-panel fan-out + push (announce priority, panel-scoped) | ~300 | `_FANOUT` | no |
| 6 | Telegram sender + bot `callback_query` ack | ~300 | `_TELEGRAM` | bot deploy |
| 7 | ACK loop, repeats, caps, expiry, all-clear | ~250 | | no |
| 8 | Medical guardrail | ~300 | `ZOE_MEDICAL_GUARD` | **yes** |
| 9 | Care: confusion/fear + scam guard | ~350 | `ZOE_CARE_PRESET` | **yes** |
| 10 | No-activity trigger | ~300 | | no |
| 11 | Daemon: emergency line in the outage fallback + longer follow-up window | ~100 | | daemon |
| 12 | Optional: brain `raise_emergency_check` | ~250 | | **yes** |
| 13 | Optional: HA door/motion | ~350 | | no |

**Replay gate:**
- Run the probe under `flock`.
- Transcribe all 1,319 corpus files once and require **zero** emergency fires outside a reviewed list.
- Use `~/.zoe-voice-samples/quarantine-tv-falsewakes-20260719` as the TV negative set.
- Hard negatives: "help me with my homework", "play Help by the Beatles", "fire up the grill", "the fire alarm test is Tuesday", "I fell asleep on the couch", "call mum", "call the plumber".
- Blind positive sets, with honestly reported recall.

**Risks:**
- TV false alarms.
- The ⅔ recall ceiling.
- The Jetson as a single point of failure.
- A bad bot PR going live on merge.
- A missing panel-scoped trigger, so only one panel speaks.
- Names and rooms announced in front of guests.
- Over-promising.
- Delay from the speculation hold.

**Open questions:**
- Set `ZOE_HOUSEHOLD_COUNTRY=AU`? It is unset in the repo, which disables the AU numbers.
- Should "no" clear immediately?
- Who are the contacts?
- Should emergencies skip the speculation hold?
- May a child cancel? (Assumed no.)
- A wake-free "help" listener? (Excluded because of TV false wakes.)
- ACMA enquiry.
- Verify the numbers.
- **Fix the distress 6 h cooldown now, separately?**

---

## DD-C: Video calling and drop-in (household feature)

### What the code changes vs the feasibility pass
1. **The camera is blocked by a header:** `nginx.d/security-headers.inc:23` sets `Permissions-Policy: camera=()`.
   - It must become `camera=(self)`, in lockstep with `tools/audit/ensure_nginx_security_headers.py` and `tests/unit/test_nginx_delivery_layer.py`.
   - The CSP has no `worker-src`, so it blocks livekit's E2EE blob worker.
2. **The existing token endpoint can't be reused for calls** (`voice_tts.py:6439-6540`):
   - the room is hard-coded `zoe-voice`;
   - the identity is the user id, so two panels bound to the same user collide;
   - tokens live 1 h;
   - any kiosk guest is accepted.
3. **The idle reaper would kill the container mid-call.** It only tracks the agent room (`voice_livekit.py:331-352,1181-1189`). Calls need a `note_voice_activity()` heartbeat.
4. **`broadcast_to_panel` falls back to broadcasting to `"all"`** (`push.py:117-131`). A ring would reach every socket. A new `send_to_panel_strict` is needed.
5. **The announce lane is claim-any.** Add an `intercom` entry to `PANEL_SCOPED_TRIGGERS`.
6. **The kiosk already has `--autoplay-policy=no-user-gesture-required --use-fake-ui-for-media-stream`** (`start-kiosk.sh`), so there are no permission prompts.
7. **Face ID has no runtime caller today.** "Pause face ID" is only a guard for future use.
8. **The Jetson is tight right now** (`free -m`: 1.5 GB available, 2 GB swap used). The LiveKit container has **no memory cap**.
9. **The repo unit and the live Pi unit may differ:** a system unit vs `systemd --user` as `pi` with `AUDIO_DEVICE=pulse`.
10. **Kernel rule:** no `household` concepts in kernel code (`services/AGENTS.md:22`). Model call groups as data.

### Architecture
- **zoe-data owns call state, and LiveKit is only transport.** A new `routers/calls.py` (deliberately not `voice_tts.py`, which is replay-gated):
  - `POST /api/calls` with `{to: panel|user|group, kind: audio|video|announce}`
  - accept, decline, hangup
  - pending poll, history
  - guest-link, heartbeat
- **State machine:** call `created → ringing → active → ended(reason)`. Per leg: `ringing → answered|declined|missed|busy|dnd|failed|cancelled`.
  - Atomic `UPDATE … WHERE state=?` gives first-answer-wins for ring groups.
  - Ring timeout 30 s. Max duration 2 h (guests 30 min).
  - **The server hard-ends calls by deleting the room** via the admin API.
- **Tables (0044):** `calls`, `call_legs`, `call_policy` (ring/auto_answer/block, countdown, video_allowed, dnd_until), `call_guest_links`, `call_events` (metadata only).
- **Tokens per leg:**
  - Room `call-<128-bit random>`; identity `panel:<id>` or `user:<id>:<leg>`.
  - Grants limited to mic and camera: no data, no `roomCreate`, no `roomAdmin`.
  - The callee's token is minted **on accept**. The token ttl covers the ring plus a few minutes.
  - Rooms are created with `empty_timeout=60` and `max_participants=2`.
  - **A test asserts the Zoe agent token cannot join call rooms.**
- **Panel authority:**
  - A transient `call_incoming` push goes to the strict channel only and carries a per-leg `leg_secret`, which accept/decline must present.
  - The kiosk also polls `/api/calls/pending` every 3 s and on socket reconnect, to cover push gaps.
  - The screen is woken via `auth_handoff._wake_panel`.
  - A guest kiosk may only make intra-house calls. Calling phones or other houses needs a member (PIN card).
- **Phone side:**
  - A new `dist/call.html` using the already-vendored livekit-client.
  - The ring arrives as a Telegram link (`_telegram_send`) to the **public https host**, since phones need a secure context for the camera and microphone.
  - Web Push comes in phase 2. It needs `ttl` and `requireInteraction`, and iOS only delivers to Home-Screen apps.
  - Guest links use `auth_handoff.SignedTokens`: single-use, 10 min, cookie-fingerprinted, revoked on decline.
  - **Cloudflare Access:** a narrow bypass for `/call/g/*`, `/api/calls/guest/*` and `/livekit/`, with WAF rate limits. This is the weakest link. Gran's panel instead gets a service token.
- **Cheaper first steps:**
  1. **One-way intercom announce** via `voice_announce` with the `intercom` trigger. "Jason says dinner's ready" is spoken by TTS, with latency bounded by the 5 s daemon poll. No camera, no USB risk. **The safest first ship.**
  2. **Audio-only calls,** which avoid the camera power trap entirely.

### Pi device handoff: release, don't share
- **Why release:** the remote caller saying "hey Zoe" would otherwise wake the daemon, and ambient capture must never transcribe a call.
- **RING:** the kiosk rings in Chromium. "Zoe, answer" goes through the normal wake path as a `call_answer` intent.
- **ANSWER:**
  - The kiosk POSTs to `127.0.0.1:7777/call/begin`, which is already in the CSP.
  - The daemon **verifies with the server using its device token** (an attach call), so the endpoint can't be spoofed from the LAN.
  - It sets `_call_active`, kills TTS, pushes `_ignore_wake_until` forward, and closes the PyAudio stream (the orb-tap hook at `:3805`).
- **In-call:** `_call_active` is added to `_daemon_busy` and to the ambient gate. Voice commands are off.
- **END:** the stream reopens, `oww.reset()` runs, and a cooldown applies.
  - **Dead-man rule:** no kiosk heartbeat for 20 s means the daemon restores itself, so a crashed Chromium can't leave the panel deaf.
- **Hard rules:** loopback only, Origin pinning, the server attach check. Today `/activate` trusts any LAN caller on the Pi; don't copy that.
- **Chromium settings:**
  - echo cancellation, noise suppression and AGC on;
  - choose the camera by its `PanaCast` label (not `/dev/video0`);
  - 640×360 at 15 fps (the 720p setting is what browned out the camera on 2026-07-19).
- **Camera:** an owner flag that face ID checks. One handle per call, with no quick reopen (the PanaCast drops off the bus). A server-side redial limit of about 15 s.
- **Video gating:** a per-panel `video_verified_at` stays null until a **powered hub** plus a Phase-0 soak pass. Until then, audio only. The Pi 5 gives peripherals 600 mA without a 5 A PSU; the PanaCast needs more than 500 mA.

### Media and network
| Scenario | SFU |
|---|---|
| Panel ↔ panel on the LAN | local LiveKit (works today with `use_external_ip:false`) |
| Phone on home Wi-Fi | local |
| Phone off-LAN, or Gran's panel | **LiveKit Cloud** |

- The token response already carries `url` per request, so a per-call SFU choice needs no client change.
- **Cloudflare TURN doesn't fix remote → home.** A relay can't reach a private-LAN SFU.
- Self-hosting TURN needs:
  - a trusted certificate, `use_external_ip`, host networking;
  - ports 443/7881 plus UDP 50000-50200;
  - a grey-cloud DNS record and a port-forward.
  - Port **443 is already taken by nginx.**
  - **CGNAT would make it impossible.**
- **Recommendation:** phase A LAN-local, phase B LiveKit Cloud for off-LAN legs. The free plan is 5,000 participant-min/mo, about 166 fifteen-minute calls.
- **Jetson:**
  - cap the container (~300 MB with `GOMEMLIMIT`, an estimate to measure);
  - a container-only start, so calls never wake the aiortc agent;
  - **admission control:** below a `MemAvailable` floor, refuse video but still allow audio.
- **Pi encode:** the Raspberry Pi docs say the "Raspberry Pi 5 uses software video encoders". Budget 640×360 at 15 fps, 400–600 kbps VP8, single layer with a max bitrate. The panel already idles at 76–82 °C with no fan.

### Zoe in the call? **No.**
- No room joins, no media consumption, no transcripts. RAM and privacy both rule it out.
- Zoe only orchestrates: the intents, a spoken pre-announce, and missed-call notices.

### UX
- **Incoming:** caller name and avatar, big Answer/Decline buttons (≥48 px), ring tone, screen wake, a clear guest label.
- **Outgoing:** "Calling Kitchen…" with Cancel. On no answer, offer an announce.
- **In-call:** big Hang-up, mute (actually releases the mic), camera off, volume. Idle-sleep is suppressed during the call.
- **Privacy bar:** driven by the actual published tracks. A chime when media starts. **The camera never starts without the countdown or a tap.**
- **Consent levels** per panel and per caller: `block`, `ring` (default), or `auto_answer` with a countdown (care preset).
  - **No silent drop-in.** This is stricter than Alexa, where an approved contact can drop in on the whole household.
- **DND:** reuses quiet hours, plus "Zoe, do not disturb for an hour". Care contacts can break DND.
- **Missed calls:** Telegram or push, a panel badge, "You missed a call from Sam" spoken on next presence, and history (90 days, metadata only).
- **Kids:** allow-list only. No auto-answer except the care preset set up by a parent. Guest links need the parent's approval and PIN.

### External
- **Echo Show Drop In:** per-contact, per-device, chime plus light.
- **Nest Hub Max:** Meet/Duo, which outsources calling to a platform.
- **Meta Portal:** consumer sales ended in 2022 (~800k units in 2021, ~1% share, privacy backlash). A single-vendor, low-trust device died. **Local-first plus consent-first is the differentiator.**
- **FaceTime on Apple TV:** camera handoff from the phone.
- **Home Assistant:** the intercom feature request (discussion #2222) is **unanswered**. Announce is one-way. The use case is real and unserved.
- **go2rtc:** doorbells, not person-to-person calls.

### Security
- **E2EE (LiveKit `ExternalE2EEKeyProvider`):** off on the local SFU, **mandatory on Cloud legs**.
  - A random per-call key is delivered only in the authenticated response, never in a URL.
  - Needs `worker-src 'self' blob:` in the CSP.
- **Never record.** No egress service, a test asserts none is added, and no call audio goes to ambient capture.
- Identities stay non-PII in LiveKit logs. No transcripts.
- **Guest links:** single-use, 10 min, at most 3 outstanding per user, per-IP rate limits, `max_participants=2`.

### PR train
Flags: `ZOE_CALLS_ENABLED`, `_VIDEO`, `_CLOUD`, `_AUTOANSWER`.

| PR | Scope | Size |
|---|---|---|
| 0 | **Phase-0 hardware checklist** (operator on the Pi; below) | — |
| 1 | **Intercom announce** (`intercom` trigger + `POST /api/calls/announce`) | ~150 |
| 2 | 0044 + calls router + state machine + policy + strict panel sender | ~400 |
| 3 | Per-call tokens, container-only start, reaper heartbeat, agent-isolation test | ~250 |
| 4 | Daemon call mode (`/call/*`, dead-man rule), treated as voice path | ~250 |
| 5 | Kiosk call UI + nginx camera/worker headers | ~400 |
| 6 | Voice intents call_start/answer/hangup (**replay-gated**) | ~200 |
| 7 | Phone `call.html`, Telegram ring, guest links, Access runbook | ~350 |
| 8 | Care auto-answer + kids policy | ~250 |
| 9 | LiveKit Cloud + per-call E2EE | ~250 |
| 10 | Web Push (ttl, requireInteraction) | ~150 |

**Phase-0 checklist (operator):**
- **Pi audio:** `pactl info`, `pactl list short sources`, then `pactl list source-outputs` with the daemon running, before and after a `getUserMedia` page.
- **Pi camera/USB:** `lsusb -t`, `v4l2-ctl --list-devices`, `--list-formats-ext`, `vcgencmd get_throttled`, and `journalctl -b | grep -i "600 ?mA"`.
- **The trap, with and without the hub:** run `journalctl -kf | grep -iE "usb|uvc|disconnect|under-voltage"` while streaming the camera at 360p for 10 min, then again with `aplay` of a long WAV through the Jabra, then at 480p and 720p. Pass means no disconnect and the throttle bits stay clear.
- **Load:** `top -H`, `measure_temp`, and `chrome://webrtc-internals` over an ssh tunnel to :9222 (look at `qualityLimitationReason`, encode time, fps).
- **Echo:** a test call with a listener on a phone.
- **Jetson:** `free -m`, `/proc/pressure/memory`, `docker stats livekit`, `ss -ulnp | grep 500` during a call.
- **Network:** a trickle-ICE test from cellular, plus the `/livekit/` WebSocket through Cloudflare.

**Risks, in order:**
1. **USB brown-out** (audio-only until the soak passes).
2. Mic contention.
3. Jetson OOM.
4. The reaper killing calls.
5. A wider Access bypass surface.
6. Ghost rings via the "all" fallback.
7. Voice-path regression.
8. Pi thermals and CPU.
9. A spoofable daemon endpoint.
10. Phones rejecting the self-signed LAN certificate.

**Open questions:**
- Is the live daemon a user or system unit?
- **Public IP or CGNAT?**
- Is LiveKit Cloud OK for call media?
- How do call groups fit the no-household kernel rule?
- How are minors flagged while the persona layer is flag-dark?
- Access approach for the cross-house panel?
- **Powered hub plus a verified 5 A PSU?**
- Who approves a minor's contacts?
- Is Gran's device a full panel or a tablet?

---
---

# OVERNIGHT SUMMARY: read this first

## Effort at a glance
| Feature | Standard-user value | Size (PR train) | Riskiest part |
|---|---|---|---|
| **Presets / member settings** (foundation) | One resolver for all per-person settings; kid and care presets | M (5 slices) | Governance (persona note), consent records |
| **Curated memory** | Fill in a kid's or new member's Zoe; Dad's anchors | L (11 PRs) | The new `curated` authority rank; voice curation needs a verified speaker |
| **Medications** | Self, kids, pets; "did I take it?"; refills | L (11 PRs) | Own scheduler (can't reuse reminders); voice-path templates; TGA line |
| **Help & emergency** | "Help / I've fallen / fire" for every home | L (13 PRs) | Detector recall/false alarms; voice-lane ordering bug |
| **Orientation** | Date and next-item grounding for everyone; a care "Today" screen | M (7 PRs) | Prompt-cache placement (solved: tail block) |
| **Per-user voice** | Kid/care voice, speed, patience | M (7 PRs) | Kokoro cache misses at non-1.0 speed |
| **Family signals & digest** | Teen "home safe", partner, Dad → kids | L (12 PRs) | Privacy/consent; Telegram plumbing |
| **Video calling** | Intercom, "call Jason", cross-house, kids → parents | L (10 PRs + hardware phase 0) | USB power, Pi encode, network |
| Smaller (meals, appointments, step-by-step, doorbell, photo frame, voice messages, per-house HA, night mode) | Each standalone | S–M each | Per-house HA scope (8 code paths) |

## Recommended order
1. **Presets resolver** (foundation, flag-dark)
2. **Per-panel HA scope** (before any panel goes to a second house)
3. **Curated memory** (the biggest win across kids, new members and Dad)
4. **Medications**
5. **Help & emergency**
6. Orientation + voice tuning
7. Family signals & digest
8. **Video:** intercom announce first, then audio calls, then video once the Phase-0 hardware tests pass
9. Smaller features as wanted

**A first build that genuinely helps Dad** = presets + per-house HA guard + medications + help + orientation screen + night mode + curated anchors.

## Bugs and gaps found in passing (not fixed; research only)
| # | Where | What | Severity |
|---|---|---|---|
| 1 | `routers/voice_tts.py:3405-3410` | Active-form cancel matches **substrings**: "I can't **stop** the bleeding" → "Okay, cancelled" | High (safety) |
| 2 | `distress_handoff.py:264-269` + `push.py:43-52,103-105` | Distress notification broadcast on `"all"` **reaches shared kiosk panels**, naming the member | High (privacy) |
| 3 | `distress_handoff.py:39` | 6 h in-process cooldown: a second danger event within 6 h notifies nobody; a restart re-notifies | Medium |
| 4 | `user_prefs.py:39` | Memory opt-out list likely omits voice writers (`voice_regex`, `voice_turn_digest`, `voice`), so voice may still learn from opted-out users. **Unverified, needs a test** | High (memory rule) |
| 5 | `morning_checkin.py:239-241` | Fires only 07:30–07:59 (the comment says until 08:30); time zone read once at import | Low |
| 6 | `reminder_service.py:187-221` + `reminder_scan.py:309-323` | A reminder created during quiet hours for later that night is likely never scheduled (inferred, not run) | Medium |
| 7 | `proactive/engine.py:99-137` | Reminders in quiet hours deferred to 07:00 (a 22:30 reminder arrives ~8.5 h late) | Medium (by design, but wrong for meds) |
| 8 | `zoe-tools.ts:311-318` | `get_time` uses the host locale and time zone, not `ZOE_TIMEZONE` | Low |
| 9 | `services/zoe-data/AGENTS.md:44` | Documents `ambient_briefing.py` / `/api/skybridge/briefing`, which don't exist in this tree | Low (doc drift) |
| 10 | `docker-compose.yml:194-231` | LiveKit container has no memory cap on a RAM-tight box | Medium |
| 11 | `touch/js/photo-slideshow.js` | Exists but no page includes it | Low |
| 12 | Repo | `ZOE_HOUSEHOLD_COUNTRY` unset, so distress uses "your local emergency number" rather than 000/Lifeline | Medium |

Per the "fix it or note it" rule, these should go into `docs/knowledge/open-problems.md`. That's a file edit, so it's held until Jason says go.

## Biggest decisions only Jason can make
1. Should curated facts rank **below** a person's own words? (Recommended: yes, as a new `curated` rank between 3 and 4.)
2. At care/high, can Dad switch off his own protections, or only family?
3. LiveKit Cloud for off-LAN call media (with E2EE), or fully self-hosted? (Depends on CGNAT.)
4. Who are the emergency contacts and the care circle, and should `ZOE_HOUSEHOLD_COUNTRY=AU` be set?
5. A personal alarm pendant for Dad as the real emergency path (strongly recommended; Zoe is a supplement).
6. A GP and pharmacist check on what Zoe tracks and says about medicines.
