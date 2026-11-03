---
type: Architecture / design
title: "The identity and presence engine: a household score card for who is here (design, 2026-10-14)"
status: DESIGN for the owner. Design only. No code, flag, service, unit, table or live path was changed. Nothing here is decided until the owner answers section 11. Every phase ships flag-dark and shadow-first.
date: 2026-10-14
description: One engine that keeps, for each panel (room) and each person, a decaying, explainable confidence built from evidence events (PIN, signed-in session, voice match, face match, phone near, touch, a different voice, leaving the room). It maps that confidence to five Zoe assurance levels lined up with the action-agent tiers T0-T5 and the NIST AALs, lets each member choose how strict Zoe is with a per-user security setting that has hard floors no setting can lower, adds a consent-first way to notice a new voice and be introduced to a visitor, and splits what Zoe knows about a person into three layers (a household directory, each member's private knowledge, and the person's own) so one member's knowledge is never relayed. Research, citations and the legal notes are in docs/research/identity-presence-scorecard-2026-10.md.
evidence_labels: "[src] file:line read in this worktree (origin/main b02a04a7b) | [branch] read from an unmerged PR branch (#2024) | [research] a finding cited in the companion research record, with its own source and tag | [proposal] a design choice made here | [unverified] not confirmed"
---

# The identity and presence engine (design, 2026-10-14)

Owner request, 2026-10-14 (gist, verbatim parts in quotes):

- "security ... was always going to be a score card, taking into account factors: when did the person last
  login, have they left the room, is their phone in range, does their voice match, does the camera/visual
  reference match the user, etc."
- "Zoe needs to be aware of who is there, and I want her to flag new people she hears ... I can introduce
  Zoe to someone and she recognises that's a different person talking and starts building out an idea of
  who they are."
- "this should be in the users' login settings so they can turn things on and off and make Zoe as secure
  as they like; some users might want no password, password, PIN, voice, visual, etc."

Companion research record: [identity-presence-scorecard-2026-10.md](../research/identity-presence-scorecard-2026-10.md)
(standards, shipped products, presence, unknown speakers, anti-spoofing, law, and the full code survey).
This design builds on, and does not replace, two earlier records:
[panel-identity-plan.md](panel-identity-plan.md) (July: the Pi-heavy biometric split and a z-score fusion
sketch) and [presence-without-sensors-2026-10-04.md](../research/presence-without-sensors-2026-10-04.md)
(the noisy-OR occupancy design). Where this doc differs from them, it says so and why.

## 0. Answer first

1. **The score card is the right model, and it can stay small.** One pure-function module on the Jetson
   (no model, no new resident RAM worth measuring) keeps, per panel, a short list of **evidence events**
   and computes on read, for each candidate person, a **log-odds score in bits**: a prior plus the
   decayed, capped contribution of each event. "+1 bit" means "twice as likely". It is the
   likelihood-ratio fusion the biometrics literature recommends [research §4], and it is explainable
   line by line: *"I heard your voice 20 seconds ago (strong), you unlocked with your PIN 6 minutes ago,
   and nobody else has spoken since."*
2. **Occupancy and identity are two different questions.** "Is anyone here?" stays the noisy-OR score from
   the presence record. "Who is this?" is the new log-odds score. Presence never proves identity. It only
   slows or speeds the decay of identity, and a break in presence (a new voice, someone arriving) knocks
   identity down.
3. **Five assurance levels, ZAL0-ZAL4**, map onto the action-agent tiers (T0-T5). Hard floors no setting
   can lower: money and T4 promotion need ZAL4 (panel PIN plus an approval on the member's own phone);
   security changes, enrolling or removing biometrics, forgetting a person and other members' data need
   ZAL3 or more; anything for a minor or a guest stays T5. **Nothing reachable at the panel alone is NIST
   AAL2.** NIST SP 800-63B-4 says outright "Biometric comparison based on voice SHALL NOT be used", and
   Google calls Face Match "not a security feature" [research §1, §2]. So voice "as a password" is a
   household-convenience level (ZAL2) the owner chooses on purpose. It is never a factor for the floors.
4. **Per-user security lives in each member's settings**: which factors they allow (PIN, voice, face,
   phone, or none on the panel) and how sure Zoe must be per category (private reads, own changes,
   memories, drafts and approvals). There are three presets, Relaxed, Balanced (the default) and Strict,
   with an advanced view on desktop. Loosening a setting needs ZAL3; tightening one never needs more than
   ZAL2. Kids' settings are set by an admin, and they can only be stricter.
5. **Unknown people are noticed but not kept.** A voice that matches nobody becomes an **ephemeral
   in-memory cluster on the Pi**. It is never written to disk and never sent anywhere, and it is gone
   after 30 minutes of silence or a restart. Zoe flags it quietly: a "new voice" chip on the panel, and an
   answer if asked "who's here?". There is no unprompted speech by default.
   **"Zoe, this is Sam"** binds that cluster to a provisional person in the people graph. Sam's own spoken
   consent, checked as coming from Sam's voice, is required before a visitor voiceprint is stored. A
   visitor profile is personalisation only (it never confers member scope) and expires after a set time
   since Sam was last heard. Children: no visitor voiceprints by default.
   **Whose is Sam?** Three layers (§5.5): a household directory everyone sees (name, relation, and the
   consented voiceprint); each member's own knowledge of Sam, private by default; and Sam's own statements.
   Zoe never relays one member's private knowledge to another member or to Sam.
6. **Phases:**
   - **Phase 0**: the engine, the policy table, the explanation and shadow mode, using only the signals
     that exist today (PIN, signed-in session, voice verdict, voice session, touch, a different voice).
   - **Phases 1-2**: the settings, then the engine acting.
   - **Phases 3-4**: unknown voices, then introductions.
   - **Phases 5-6**: new sensors (phone, mmWave, face).
   - Each phase is flag-dark and says what changes for the user.
7. **Is it too complicated?** The engine is small. The real costs are **calibration** (each factor needs
   household shadow data before it may count) and **settings sprawl** (presets solve most of it). The
   minimum valuable product is Phases 0-2 with today's signals and three presets.

## 1. What Zoe already has (code survey summary)

Full inventory with file:line in the research record §10. The facts that shape the design:

| Signal | State | Where [src] |
|---|---|---|
| Panel PIN (zoe-auth passcode, argon2, 4-8 digits) | live; challenge TTL 120 s, 5 attempts, 300 s lockout | `services/zoe-data/routers/panel_auth.py:36-38`, PIN route `:613-916`; `services/zoe-auth/core/passcode.py:30-58` |
| PIN freshness = "member at this panel" | live; `ui_panel_sessions.last_seen_at` within the idle-logout window (default 900 s, `system_preferences.panel_idle_logout_s`, touch offers 15 min-12 h) | `routers/voice_tts.py:2069-2174`; `routers/system.py:2512-2537`; `touch/home.html:3897-3908` |
| Password sessions | live; STANDARD 480 min, PASSCODE 60 min, GUEST 30 min, remember-me 30 days | `services/zoe-auth/core/sessions.py:57-96` |
| Roles | zoe-auth: `admin`, `user`, `family_member`, `child`, `guest`; zoe-data capability matrix knows only `admin`/`user`/`guest`, and **`family_member` and `child` canonicalise to the guest matrix** | `services/zoe-auth/models/database.py:18-23`; `services/zoe-data/guest_policy.py:127-132,213-218` |
| Voice ID, Resemblyzer | implemented on the Pi, **disabled by default** (`SPEAKER_ID_ENABLED` false); when enabled with `SPEAKER_ID_SHADOW` on (its default), scores are logged but never attached to the turn | `scripts/setup/zoe_voice_daemon.py:408-428,2655-2706` |
| Server voice claim acceptance | device-token only, threshold `ZOE_SPEAKER_ID_THRESHOLD` (code default 0.82), consent row required, tri-state verdict | `routers/voice_tts.py:6474-6574` |
| ResNet34 switch (`ZOE_SPEAKER_MODEL`, `model_id`) | **PR #2024 open, not on main**; FAR 1 % thresholds 0.233 / 0.265 (short / long clip) | [branch] `docs/knowledge/speaker-resnet34-switch.md` |
| Voice-verified session | flag-dark `ZOE_VOICE_SESSION`; per-panel, in-memory, TTL 120 s (10-900); a different voice closes it; step-up tier always needs the PIN | `services/zoe-data/voice_session.py:71-73,99-117,381-470`; `lexicons_data/voice_session_policy.json` |
| Identity freshness | on main, default on (#2026): a member identity comes only from this turn, a fresh PIN session, the caller's own session or an open voice session | `routers/voice_tts.py:3451-3600`; `routers/AGENTS.md` |
| Face ID | server storage flag-dark (`ZOE_FACE_ID_ENABLED`, threshold 0.45); the Pi module exists, but **only the enrolment flow calls it** (no runtime producer); the USB power budget is shared with the speaker | `scripts/setup/zoe_face_id.py:39-47,369-434`; `routers/face_id.py:48-62`; `scripts/setup/zoe_enroll_flow.py:27-31,121-146` |
| Restraint | `ZOE_RESTRAINT` shadow; a sensitive class is withheld when the speaker verdict is False | `services/zoe-data/restraint.py:40-72` |
| Presence primitive | `panel_presence_tier`: owner / bound_guest / absent (window 900 s); the kiosk heartbeat always sends `is_foreground: true`, so touch is invisible to the server | `proactive/presence.py:28-100`; `dist/js/touch-ui-executor.js:674,690` |
| Sleep gate | the room's own toggles only; Zoe does not currently consume any presence or occupancy sensor; unknown means sleep | `routers/panel_config.py:382-416` |
| Phone / BLE presence | none. Omi-sync connects to the pendant about every 2 min (`OMI_SYNC_COOLDOWN_S` 120) but records no "seen"; panel-bluetooth reads RSSI for the settings list only | `services/omi-sync/omi_sync/config.py:91-93`, `runner.py:86-92`; `services/zoe-data/bluetooth_broker.py:260-272` |
| Home Assistant presence | Zoe does not currently consume any HA presence entity (`person`, `device_tracker`, `binary_sensor`); entities are reachable through `GET /api/ha/entities?domain=` | `routers/ha_control.py:72-90` |
| People graph | `people` (+ `is_partial` stubs, `introduced_by_person_id`, `how_we_met`) + `person_relationships` (temporal, authority, evidence); a `people_introduce` intent creates or finds a person and asks follow-ups | `alembic/versions/0001:87-102`, `0007`; `routers/voice_tts.py:3099-3139,4290-4313`; `person_extractor.py:301-317` |
| Settings UI | desktop: Password, PIN, Sessions, Speaker Identity (enrol + delete); touch estate: signed-in name, idle-logout, switch user, sign out; **no face UI anywhere, no security preferences anywhere** | `dist/settings.html:579-600,730,808-832`; `dist/touch/home.html:3809-3926` |

**Gaps the engine fills:**

- There is no evidence ledger. There is no fusion or decay: every signal is binary.
- Touch and VAD activity are not recorded.
- No proximity source exists, and no unknown-voice handling.
- No per-user security preference exists.
- There is no child capability tier: `child` collapses to guest.

## 2. The engine

### 2.1 Two questions, two scores

| Question | Score | Method | Fails toward |
|---|---|---|---|
| **Is anyone here?** (occupancy, per panel or room) | `anyone_here` ∈ [0,1], plus `voices_heard` (count of distinct live voices) | noisy-OR of decayed presence signals, exactly as [presence-without-sensors §3.3](../research/presence-without-sensors-2026-10-04.md) | for speech, **someone is listening**; for capture, **nobody consented** |
| **Who is this?** (identity, per panel and per candidate person) | `L(p)` in bits, `P(p) = 2^L / (1 + 2^L)` | prior + Σ over groups of capped, decayed log-likelihood ratios (§2.3) | **guest** (unknown is never the member) |

They interact in exactly two ways:

1. **Continuity.** While presence is intact (the same voice keeps talking, touch keeps happening, nobody new
   has been heard), identity decays slowly. When presence breaks, it decays fast.
2. **Others present.** When someone else is observably present, a member's **private content is neither shown
   nor spoken in the room**, whatever their level. One member's personal scope is never exposed to another
   person (charter §3).
   - **Observably present** means any of: another voice heard on this panel in the last 10 minutes (another
     member, a visitor match or an unknown cluster, i.e. `voices_heard ≥ 2`); a recognised visitor; or a
     people count above 1 from a counting sensor, once one exists.
   - **What Zoe does instead:**
     - She says it is private and sends it to the member's own device (Telegram today; push later): "That's
       private and someone else is here, so I've sent it to your phone."
     - With no linked device: "That's private, and someone else is here. Ask me again when you're alone."
   - **Only if the member's own setting allows it** (`others_present: ask`, default `never`), she asks first:
     "Someone else is here. Want me to read it out anyway?" She reads it only on a "yes" in the member's own
     verified voice. Even then no private card is put on the screen.
   - **Honest limit:** a person who stays silent is invisible to voice-only presence. A member who wants more
     can choose `private_answers: phone_only`, and a counting sensor (mmWave, camera count) closes the gap
     later.
   - This is Nest's attention idea, taken further ([panel-identity-plan](panel-identity-plan.md)) [research §2].

### 2.2 Evidence events

Every producer emits the same small record. It holds no audio, no frame, no embedding, no MAC and no key:

```
EvidenceEvent {
  panel_id, room_id?,             # where
  subject: member:<user_id> | visitor:<person_id> | unknown:<cluster_tag> | anyone
  source:  pin | password_session | voice | face | phone_ble | phone_home | omi_seen
         | touch | voice_activity | different_voice | presence_break | lock
  group:   KNOW | VOICE | FACE | DEVICE | CONTEXT         # for caps (2.3)
  llr_bits: float                 # signed; from the source's calibration (2.4)
  t: monotonic + wall time
  half_life_s: float              # per source, from the policy table
  detail: short enum for the explanation ("strong", "weak", "short clip") - never a raw score in user text
}
```

**The store** is an in-process ring per panel: the last N = 64 events or 30 minutes, whichever is smaller.
It is in memory only, the same model as `voice_session._SESSIONS`, and is lost on restart. Restart is
fail-safe: everyone becomes guest until re-proven. In shadow mode a JSONL line per decision goes to
`~/.zoe-logs/identity_shadow.jsonl`, kept 14 days. It holds levels, top candidates, bits per group and
the live decision, never a raw biometric score paired with an identity, and never audio or vectors.

### 2.3 Fusion: capped, decayed log-likelihood ratios

For a panel `k`, a candidate `p`, at time `t`:

```
age          = t − t_e
c_e(t)        = llr_e                              if age ≤ hold_e      # full strength while fresh
              = llr_e · 0.5 ** ((age − hold_e) / h_e(t))   otherwise    # then decays toward 0 bits
G_g(p, t)     = clip( Σ_{e in group g, subject p} c_e(t),  lo_g, hi_g )
L(p, t)       = prior(p) + Σ_g G_g(p, t)
P(p, t)       = 2^L / (1 + 2^L)
h_e(t)        = half_life_e · (continuity ? 1 : 0.25)          # presence intact: slow; broken: fast
```

`hold_e` keeps an event at full strength for the window it is meant to cover: the member's voice-session
TTL for a voice accept (default 120 s, today's session) and the idle-logout window for a PIN. Without it, a
pure exponential would drop a just-accepted voice below ZAL2 within seconds.

- **Why log-likelihood ratios.** The likelihood-ratio test is the Neyman-Pearson-optimal way to combine
  scores, and LR-based score fusion is the multimodal-biometrics literature's recommended default
  (Nandakumar, Chen, Dass, Jain, IEEE TPAMI 2008) [research §4].
  - LLRs add when evidence is independent, so mixed kinds of evidence go on one scale: "a PIN is
    worth 11 bits, a strong voice match 6.6 bits, a phone nearby 1.5 bits".
  - Every number has a meaning a person can check. +10 bits is about 1000 to 1.
- **Why not the July plan's z-score weighted sum.** That sum needs hand-tuned weights, has no
  probabilistic meaning, and cannot put a PIN or a phone on the same scale as a biometric. It solved
  face plus voice; this has to solve eight sources. *Kept from it:* a degraded modality contributes
  nothing rather than a zero score, `max`-style refresh on new evidence, the three bands and the
  never-rebind rule.
- **Why caps per group.** Plain summation assumes the sources are independent. Mine are not:
  - two voice matches 10 s apart are mostly the same evidence;
  - a phone over BLE and the same phone on home Wi-Fi are one fact.

  Clipping each group (VOICE at most +12 bits, DEVICE at most +3, KNOW at most +17) is the simple,
  explainable guard against double counting. A full dependence model (copulas, a GMM per sample) is
  the literature's answer, and it needs data we do not have [research §4]. Within a group, a newer
  event of the same source replaces the older one instead of adding to it.
- **Negative evidence is first class.**
  - A scored-but-rejected voice for `p` is about −4 bits for `p`.
  - A `different_voice` event (a voice confidently matched to someone else, or an unknown cluster
    taking the turn) is −20 bits for `p`. In practice it closes `p`'s identity at that panel, as the
    voice session already does.
  - `phone_home = away` for `p` is −5 bits when no leave-lock is on.
  - **A `lock` is absorbing.** It discards **every** earlier event for `p`, including a fresh PIN: on that
    panel for a room leave, and on every panel for a phone leaving home. Only evidence that arrives after the
    lock counts, so `p` falls straight to the prior.
    - The engine also ends `p`'s PIN panel session, so the identity-freshness chain cannot bring it back.
      This needs the sign-out hook zoe-data lacks today (`docs/knowledge/open-problems.md`, 2026-10-13
      "Panel sign-out").
    - A lock is emitted by the spoken "lock" or "sign me out", and by a **qualifying leave event when the
      member turned the matching leave-lock on**:

      | Setting | Qualifying leave event |
      |---|---|
      | `lock_when_my_phone_leaves` | the phone's home tracker turns to away; or the phone was near this panel and has been absent for two consecutive scan windows (at least 60 s) |
      | `lock_when_i_leave` | a counting or occupancy sensor reports the member's room empty for at least 60 s; or a presence break after which neither the member's voice nor their phone is observed again within 60 s |

    - Without the setting, the same events are ordinary evidence: −5 bits and faster decay.
- **Prior.** The prior probability is `1 / (N_members + 1)`: one share per enrolled member plus one for
  "someone else". As log odds that is `log2(p / (1 − p)) = −log2(N_members)`. In a four-member household it
  is −2 bits (P = 0.2). A member who is away by a trusted signal gets an extra −5 bits.
- **Computed on read.** There is no scheduler and no timer thread. The score is recomputed when a turn,
  card or action asks, from the ring and an injectable clock. This is pure and table-testable.

### 2.4 Calibration: where each source's bits come from

A source contributes **0 bits until it is calibrated**. That is the single most important safety rule in
this design.

| Source | How its bits are derived | Starting values (to be replaced by shadow data) |
|---|---|---|
| PIN at this panel | `−log2(guess probability within the allowed attempts)`. A 4-digit PIN with 5 tries is `5/10^4`, so ~11 bits; 6 digits gives ~17.6. This assumes no observation (shoulder-surfing), and the panel is a shared wall screen, so cap KNOW at the PIN's own value | 11 bits (4 digits), 13.3 (5), 17.6 (6); hold = the idle-logout window, then half-life 60 s |
| Signed-in password session on the member's own device | evidence for **that device's** principal, not for the panel; it never enters a panel score | n/a at the panel |
| Voice, ResNet34 (acting model) | `log2(TPR(s)/FAR(s))` from a per-score calibration (linear or logistic on the centred cosine, i.e. the BOSARIS/Platt idea [research §4]) fitted on the shadow week; rejection gives `log2(FRR/(1−FAR))` | two operating points: at the FAR 1 % threshold ≈ log2(0.95/0.01) = 6.6 bits (ZAL1 evidence only); at the derived ZAL2 point (FAR 0.05 % for four members, §3.1) ≈ 10.8 bits; these are worst-case values AT the threshold, and most genuine accepts score well above it, so their calibrated LLR is higher; reject ≈ −4.3; hold = the member's `voice_session_ttl_s` (default 120 s), then half-life 60 s |
| Voice, Resemblyzer | the same recipe, its own curve; owner FRR at FAR 1 % is ~11 % [record] | retired 2026-10: the server has no Resemblyzer path and the override flag is gone |
| Face (MobileFaceNet + liveness) | the same recipe, after a face shadow week; no liveness means 0 bits | 0 until calibrated |
| Phone near (BLE IRK, RSSI band) | the device can be in someone else's pocket, so this is weak by policy | +1.5 bits, cap the DEVICE group at +3; half-life 180 s |
| Phone home (HA companion / tracker) | a prior shift, not identity: home is 0 bits, away is −5 | current tracker state, no decay |
| Omi seen by the panel | possession of a wearable, about every 2 min | +1 bit; half-life 300 s |
| Touch / voice activity (anyone) | occupancy, not identity; it **pauses identity decay** (continuity) | 0 bits to identity |
| Presence break (new voice, people count up, door) | sets `continuity = false` for 10 minutes | n/a |

### 2.5 Explainability

`explain(panel, person)` returns the groups sorted by contribution. Each line is from a fixed phrase table
(lexicon data, translatable) with the age of the event:

> "I'm fairly sure it's you, Alex: I recognised your voice a few seconds ago, you unlocked this panel with
> your PIN six minutes ago, and nobody else has spoken since. For payments I'd still need your phone."

Rules:

- The explanation goes **only to the person it is about, at ZAL2 or above**. Anyone else gets "I'm not sure
  who you are yet."
- It never discloses another person's signals, for example "Sam's phone is here".
- It never shows a raw score in user text. Bits and groups appear only in the admin debug view.
- "Why do you think I'm Alex?" and "Who do you think I am?" are deterministic tiers in `fast_tiers`. They are
  not brain prompts and are answered from `explain()`, so the model cannot invent a reason.

## 3. Policy: assurance levels mapped to action tiers

### 3.1 Zoe assurance levels

| ZAL | Name | Reached when | Roughly |
|---|---|---|---|
| ZAL0 | Anyone | always | the guest of today |
| ZAL1 | Recognised | `L(p) ≥ 3.2 bits` (P ≥ 0.90) and no conflicting voice since the last evidence | Google/Amazon "personal results" [research §2] |
| ZAL2 | Verified | `L(p) ≥ 6.63 bits` (P ≥ 0.99) **and** at least one authenticator-class event within its hold window (a voice accept by the acting model **at the ZAL2 operating point derived below**, a face accept with liveness, or a PIN), and no `different_voice` since | today's voice session or fresh PIN session |
| ZAL3 | Stepped-up | a **KNOW** event (PIN at this panel) within the last 5 min (`ZOE_IDENTITY_STEPUP_WINDOW_S`) plus ZAL2 | today's step-up |
| ZAL4 | Two-device | ZAL3 plus an approval on the member's **own phone** bound to the exact payload (Telegram Approve or passkey, per the action-agent approvals object) | the closest a home gets to NIST AAL2 [research §1] |

**The voice operating point for ZAL2 is derived from the false-ZAL2 target, not picked.**

- **The target, with its conditions.** Fewer than **1 false ZAL2 per 1,000 private-scoped turns**: turns that
  ask for a member's private reads, writes or memory.
  - "False" means the engine granted member `p` while someone other than `p` spoke.
  - Close-relative lookalikes are excluded here; they are handled per pair by R1.
- **The arithmetic.** An impostor turn is compared with every enrolled member profile it does not belong to.
  So, per private-scoped turn:

  ```
  P(false ZAL2) ≈ f_other × N_cmp × FAR_profile
    f_other = share of private-scoped turns spoken by someone other than the member they would unlock
    N_cmp   = profiles compared that are not the speaker's: N_profiles − 1 for a member speaking,
              N_profiles for a visitor or stranger (visitor profiles never confer ZAL2)
  ```

- **For a four-member household,** pessimistically assume `f_other = 0.5` (half of all private asks come from
  someone else; real use should be far lower) and `N_cmp = 3`. The target needs
  `FAR_profile ≤ 1e-3 / (0.5 × 3) ≈ 0.067 %`.
  - FAR 1 % gives about 15 false grants per 1,000 such turns, so it is out.
  - The measured FAR 0.1 % point gives about 1.5 per 1,000, which still misses.
  - The design operating point is therefore **FAR 0.05 % per profile** (about 0.75 per 1,000 under these
    conditions). The engine recomputes the required FAR from the enrolled profile count, so a larger household
    gets a stricter point automatically.
  - The margin rule (the best profile must beat the runner-up) and the `different_voice` closure lower the
    real rate further. They are not counted here.
- **What #2018 measured**
  ([ResNet34 record](../research/speaker-resnet34-optimisation-2026-10.md), relative evidence; owner labels
  inferred):
  - At FAR 0.1 % with the recommended config, FRR is **1.85 %** on the owner strict core with 5-clip enrolment,
    and **16.7 %** on the public far-field set.
  - The record warns that its FAR 0.1 % rows rest on a few hundred distinct impostor clips, so they are order
    of magnitude only.
  - **Nothing was measured at 0.05 %.** Demonstrating it (rule of three, `3/n ≤ 5e-4`) needs at least 6,000
    impostor trials. These come from the public impostor corpus against each profile plus household
    cross-member trials, in the lab and the shadow fortnight, **before** ZAL2-by-voice acts.
- **What it costs the owner's intent, honestly.** An identified voice still unlocks everyday private reads
  such as the calendar, but the stricter point lowers the true-accept rate.
  - Expect the owner-core miss rate to rise from about 0.3 % (FAR 1 %) to a few percent.
  - Far-field asks (across the room, noise) will fail more often, plausibly one in six or worse.
  - The fallback is cheap: "say it again so I know it's you" (one more accepted turn), or the PIN.
  - An accept at the looser FAR 1 % point still counts as ZAL1 evidence: Zoe greets the member by name and
    personalises, but does not open private data.
- **The bits follow.** At FAR 0.05 % and TPR ≈ 0.9 an accept is worth `log2(0.9 / 0.0005) ≈ 10.8 bits`. With a
  −2-bit prior that is 8.8 bits, above the restored P ≥ 0.99 bar (6.63 bits), so the ZAL2 posterior no longer
  needs a household-size special case. The VOICE group cap rises to +12 bits to hold it. A weak signal alone
  (DEVICE ≤ 3 bits) never reaches ZAL2.

The real gate for ZAL2 is the authenticator-class event at the derived operating point. The posterior bar is
a sanity floor that contrary evidence (a rejected voice, a phone confirmed away) can pull a member below.

Honest NIST mapping [research §1]:

- SP 800-63B-4 §3.2.3: "Biometrics SHALL only be used as part of multi-factor authentication with a physical
  authenticator". It requires an FMR of "one in 10000 or better", PAD for face, and **"Biometric comparison
  based on voice SHALL NOT be used."** [verified, research §1]
- Even ignoring the voice exclusion, our voice at the FAR 1 % operating point is 100 times looser than the
  FMR floor, and the FAR 0.1 % point is 10 times looser. A PIN typed on a shared wall panel is a single
  memorised secret.
- NIST also says risk or fraud indicators "do not impact or change the AAL". A score card is an indicator
  layer that decides *when to ask*. It never replaces a factor for the floors.
- Timing reference: AAL2 reauthentication within 24 h overall and 1 h of inactivity; AAL3 within 12 h and
  15 min. ZAL3's 5-minute PIN window is stricter than either, on purpose, because the panel is a shared
  device.
- So ZAL0-ZAL3 are household-convenience levels. Only ZAL4 adds a possession factor (the phone). Even then
  it is AAL2-like, not certified, because a Telegram approval is an account login, not a bound authenticator.
  A passkey would be.

### 3.2 Categories and floors

| Category (examples) | Action-agent tier | Default (Balanced) | **Hard floor (code, no setting lowers it)** | Member may choose |
|---|---|---|---|---|
| Public household (weather, timers, music, lights, general questions) | T0 public | ZAL0 | ZAL0 | (household setting: what guests may do) |
| Personalisation (greet by name, "my playlist", tone) | T0 | ZAL1 | ZAL1 for any name use | ZAL1-ZAL3 |
| Private reads (my calendar, lists, reminders) | T0 private | ZAL2 | **ZAL1: always an identified member.** Personal scope is never shown to an unidentified person (charter §3). To let anyone see something, the member moves it to shared scope explicitly ("share my calendar with the household"); the assurance setting never declassifies data | ZAL1-ZAL3 |
| Memory recall about me | T0 private | ZAL2 | ZAL1 | ZAL1-ZAL3 |
| My own reversible writes (add to list, reminder, calendar) | T1 | ZAL2 | ZAL1 | ZAL1-ZAL3 |
| Erasing my own data (delete event, forget a fact about me) | T1 erase | ZAL2 | ZAL2 (whole-account erase: ZAL3) | ZAL2-ZAL3 |
| Drafts (email draft, call script, cart) | T2 | ZAL2 | ZAL2 | ZAL2-ZAL3 |
| Approve-each outward actions (send, call, submit) | T3 | ZAL3 | **ZAL3** | ZAL3-ZAL4 |
| Money of any amount; T4 standing-approval promotion | T3/T4 + payment | ZAL4 | **ZAL4** | ZAL4 |
| Security and admin (door lock, alarm, add or remove a user, change roles, change PIN, **loosen** a security setting) | security | ZAL3 + admin role where admin | **ZAL3** (+ admin) | ZAL3-ZAL4 |
| Enrol or remove biometrics (voice, face, visitor profiles) | security | ZAL3 | **ZAL3**; exception: a person can always remove their own biometrics from their own signed-in device | ZAL3 |
| Forget a person (`memory_forget_entity`) | T1 erase, other-person | ZAL3 | **ZAL3** | ZAL3 |
| Another member's data | - | refused | **refused unless that member granted it; then ZAL2 for the requester, and never through voice alone for writes** | (the data owner grants) |
| Anything for a minor or a guest, outward | T5 | refused | **refused** | - |

The effective level for a category is
`max(code_floor, household_floor, role_floor, member_choice)`, computed in one function and pinned by a
table test. The policy table is **data**:
`services/zoe-data/lexicons_data/identity_policy.json`, beside the existing `voice_session_policy.json`
(same shape: intents, Skybridge pairs, phrase tiers → category), so there is one classification source.
`voice_session_policy.json`'s step-up list becomes the `ZAL3` rows. A check that cannot run fails closed
to the floor's step-up.

Two asymmetries that keep it safe and humane:

- **Tightening is cheap, loosening is not.** Making yourself stricter needs ZAL2. Loosening anything needs
  ZAL3 (your PIN), from your own device or the panel.
- **Lower friction is earned, never assumed.** A level can only be reached through calibrated evidence. An
  uncalibrated source is 0 bits, so a member's "Relaxed" choice cannot be satisfied by a sensor that has
  not proven itself.

## 4. Per-user security settings

### 4.1 Data model

One JSON document per member in `user_preferences` (key `security_profile`), plus one site document in
`system_preferences` (key `site_security`; scope-neutral by charter §5, the UI calls it "Household"). There
are no new tables for settings.

```
security_profile {                      # per member; written only by that member (or an admin for a minor)
  preset: relaxed | balanced | strict | custom
  factors:                              # what may count as evidence for ME
    pin: bool                           # default true if a PIN exists
    voice: bool                         # requires a consented voice profile
    face: bool                          # requires consented face profiles + liveness
    phone: bool                         # phone presence (only once a phone source exists)
  levels: { personalisation, private_reads, memory, own_writes, erase_own, drafts, approvals }  # ZAL each
  session:
    voice_session_ttl_s: 60..900        # how long a recognised voice keeps me signed in (today 120)
    lock_on_other_voice: true           # fixed true; shown, not editable (floor)
    lock_when_i_leave: bool             # needs an occupancy or phone source; a qualifying leave = absorbing lock (2.3)
    lock_when_my_phone_leaves: bool     # phone away / out of range = absorbing lock, discards even a fresh PIN
    others_present: never | ask         # default never: private answers are not spoken or shown with others present
    private_answers: room | phone_only  # phone_only: private answers always go to my own device
  explain: bool                         # may Zoe answer "why do you think I'm me?" (default true)
}

site_security {                         # admin only, ZAL3
  floors: { <category>: ZAL }           # can only RAISE the code floors
  guests: { smart_home: bool, music: bool, timers: bool, intercom: bool }
  notice_new_voices: off | chip | greet # default chip
  introductions: off | on               # default off until Phase 4 ships
  visitor_voice_days: 30..365           # default 180 since last heard
  children: { visitor_voiceprints: false, own_voice_enrol: false, own_face_enrol: false }
}
```

**"No password" members.**

- A member can choose `factors = {}` on the panel. Zoe then never shows their **personal** data there,
  because nothing can identify them. If they want something visible to whoever is in the room, they share it
  explicitly (it becomes shared scope, like a family calendar today). The assurance setting never
  declassifies data.
- The code floors still stand. Money, security, biometrics and forgetting people need ZAL3 or ZAL4, so
  that member must either set a PIN or do those things from their own signed-in phone or desktop.
- zoe-auth accounts keep a password for their own devices. "No password" means **no panel friction**, not
  an account with no credential.

**Presets** (what most people should ever touch):

| | Relaxed | **Balanced (default)** | Strict |
|---|---|---|---|
| Private reads | ZAL1 (Zoe's fairly sure) | ZAL2 (Zoe's sure: voice or PIN) | ZAL3 (PIN every time, 5 min) |
| Own changes | ZAL1 | ZAL2 | ZAL3 |
| Memory about me | ZAL1 | ZAL2 | ZAL2 |
| Voice keeps me signed in | 10 min | 2 min | 1 min, voice is personalisation only |
| Lock when I leave / my phone leaves | off | on (when a source exists) | on |

**Kids** (role `child`, or `member_modes.minor`):

- An admin sets the profile. The child can tighten it but never loosen it.
- Default is Strict for private reads (a PIN, or nothing if there is no PIN), with voice and face
  enrolment off (`ZOE_ENROLL_OFFER_KIDS` off today).
- Never visitor introductions by a child. Never T2 or above.
- This needs the role fix in §9 R9 first: `child` must stop collapsing to guest in `guest_policy`.

**Guests** have no profile. A guest is the absence of an identity, not a lesser identity
([emotional-safety-note §5](../governance/emotional-safety-note.md)). The household document says what any
guest may do.

### 4.2 UI sketches

**Desktop**: `settings.html`, a new nav item **Account → Sign-in & security**, above the existing Password,
PIN and Speaker Identity sections (they stay as they are and are linked from here):

```
Sign-in & security                                     [How sure is Zoe it's you?  ● Verified]

How strict should Zoe be with your things?
  ( ) Relaxed    (•) Balanced    ( ) Strict    ( ) Custom...
  Balanced: Zoe shows your calendar, lists and reminders when she recognises your voice
  or you've unlocked with your PIN. Payments always need your phone.

What can prove it's you?                                 (each needs its own setup)
  [x] PIN on the panels            set up ✓       [Change PIN]
  [x] My voice                     enrolled ✓     [Manage voice]   (ResNet34, 5 clips)
  [ ] My face                      not set up     [Set up at a panel]
  [ ] My phone nearby              not available yet

Custom (shown when Custom is chosen):
  My calendar, lists, reminders  [Zoe's fairly sure ▾ | Zoe's sure | Always my PIN]
                                 (to let anyone see it, share it with the household instead)
  Adding or changing my things   [ ... ]
  What Zoe remembers about me    [ ... ]
  Drafts (emails, messages)      [Zoe's sure | Always my PIN]
  Sending / booking / approving  [Always my PIN | PIN + my phone]
  Payments                       PIN + my phone (always)                      (locked)
  Security, voices, forgetting   Always my PIN (always)                        (locked)

Staying signed in
  After Zoe hears me, stay signed in for   [2 min ▾]
  Sign me out when someone else speaks     always (locked)
  Sign me out when I leave the room        [on]  (needs a presence source; signs you out even after a PIN)
  When someone else is in the room         [Send private answers to my phone ▾ | Ask me first]

[Why does Zoe think it's me right now?]   -> the explain() text
Loosening any of these asks for your PIN.
```

**Household** (admin only, under User Management): guests may [lights] [music] [timers]; notice new voices
[Off | Show a "new voice" chip | Greet them]; introductions [Off | On]; forget visitors' voices after
[180 days]; children: visitor voiceprints [never], their own voice [off]. A banner names the code floors as
"always".

**Touch (panel): voice first, touch second, no keyboard** (VISION principle 8). Nothing here needs typing
except the PIN pad.

- Spoken:
  - "Zoe, how secure am I?" → "You're on Balanced: I show your calendar when I recognise your voice.
    Want it stricter?"
  - "Make me stricter" or "Always ask for my PIN for my calendar" → Zoe repeats the change and applies it
    (tightening, ZAL2).
  - "Relax my settings" or "Stop asking for my PIN" → Zoe asks for the PIN (loosening, ZAL3), then repeats
    it back.
  - "Why do you think I'm me?" → the explanation.
  - "Lock" or "sign me out" → the existing lock.
- Touch card (estate Settings → Account): three large buttons, **Relaxed / Balanced / Strict**, with one
  line each; the current level badge ("Alex · voice"); **Advanced on your phone** → QR / Telegram
  "send to my phone" (the `auth_handoff` pattern), which opens the desktop page on the phone.
- No per-category grid on the wall.
- Members never run code. Enrolment (voice today, face later) stays Zoe-driven from the panel
  ([zoe-driven-voice-enrollment.md](../knowledge/zoe-driven-voice-enrollment.md)), and every setting
  above has a spoken or tapped path.

## 5. Unknown people: notice, introduce, learn, forget

### 5.1 Notice a new voice: ephemeral, on the Pi

- **Where.** In the panel daemon, which already computes a speaker embedding every turn (Resemblyzer today,
  ResNet34 after #2024). Phase 3 requires ResNet34 to be acting: open-set rejection at Resemblyzer's
  accuracy would flag the owner as a stranger about one turn in nine [record].
- **Rule.** Best member score below that member's threshold **and** below every visitor threshold counts as
  unknown. This is open-set identification [research §7].
- **Clusters.** Unknown embeddings go to an in-RAM online clusterer: join the nearest cluster if its
  cosine to the cluster mean is at or above the FAR 1 % threshold, else start a new one.
  - At most 8 clusters.
  - A cluster dies 30 min after it was last heard.
  - All clusters die on a daemon restart.
  - **They are never written to disk, never logged as vectors, and never sent off the Pi.** The server only
    learns `unknown:<tag>`, a random per-cluster tag that is meaningless after expiry, plus a count.
- **Why not a diarisation model.** pyannote-class pipelines are built for recordings, cost hundreds of MB and
  seconds of compute, and need a GPU to be quick [research §7]. Our turns are already single-speaker
  segments with an embedding, so a per-turn embedding plus online clustering is enough for "a different
  person is talking". Overlapping speech inside one turn is out of scope (stated limit).
- **What Zoe does** (`notice_new_voices`):
  - `off`: nothing.
  - `chip` (default): a small "New voice" chip on the panel while the cluster lives. "Zoe, who's here?"
    answers a count to anyone ("I can hear two people, and I don't know one of them"), and names only to a
    verified member.
  - `greet`: once per cluster, at a natural turn end, "Hi, I'm Zoe. I don't think we've met." This is
    opt-in because the owner does not want unprompted speech
    (the spoken brief is off by owner decision, `ZOE_PROACTIVE_SPOKEN=0`,
    [state-of-zoe review](../knowledge/state-of-zoe-review-2026-09-25.md)).
- **Effect on identity.** An unknown cluster taking a turn is a `different_voice` event. It closes every
  member's ZAL2 on that panel and raises `voices_heard`. This is the security half of the feature, and it
  is useful even if introductions never ship.

### 5.2 "Zoe, this is Sam": the introduction flow

```
member (ZAL2+, adult, introductions on):  "Zoe, this is Sam."
Zoe: "Hi Sam, nice to meet you."                    # people_introduce intent (exists) creates or finds the person
     [binds the most recent unknown cluster heard on this panel in the last 5 min,
      if exactly one; if none or several: "Sam, say hello so I know your voice."]
Zoe (to Sam): "Would you like me to remember your voice, so I know it's you next time?
               I keep a voice signature, never a recording, and you can say 'Zoe, forget me' any time."
Sam: "Yes."                                         # must come FROM the bound cluster (speaker check);
                                                    # the introducer saying "yes" does not count
Zoe (to Sam): "And may I remember things you tell me about yourself, like what you like to eat?
               That's separate, and you can say no."
Sam: "Sure."                                        # second, separately stamped consent; same speaker check
Zoe: "Done. Nice to meet you, Sam."
```

- **Consent is the visitor's own, spoken, checked as their voice, and stamped** (`consent_at`,
  `consent_by='self'`). A "no", silence for 20 s, or a "yes" from the wrong voice means no voiceprint.
- **Two consents, asked and stamped separately:** the voiceprint (`visitor_voice_profiles.consent_at`) and
  remembering what Sam says about themself (`person_directory.self_facts_consent_at`). Either can be given
  without the other, and either can be withdrawn on its own ("Zoe, stop remembering things about me"). Sam
  can still be a person in the household directory and in the introducer's own knowledge (§5.5).
- **Minors.** If the introduction names a child relationship (son's friend, niece, "she's eight"), if the
  introducer is a minor, or if Sam says they are under 18, Zoe does **not** offer to remember the voice
  (`children.visitor_voiceprints = false`). An admin can turn this on per household only with a lawyer's
  advice [research §9].
- **The provisional person** is two records (§5.5):
  - a `person_directory` entry (household layer) with `status = provisional` until the introducer confirms
    it, holding only the name and relation to the household;
  - the introducer's own `people` row linked to it (knowledge layer), with `how_we_met` = "introduced at the
    kitchen panel", `first_met_date` and `is_partial = 1` until confirmed. This row is **personal** by
    default for introduction-born rows.
- **The visitor voiceprint** (Phase 4, a new table, not `speaker_profiles`):
  `visitor_voice_profiles(directory_id FK person_directory ON DELETE CASCADE, model_id, embedding_blob, consent_at NOT NULL, consent_by, introduced_by, created_at, last_heard_at, expires_at)`.
  - Why separate: `speaker_profiles.user_id` is a foreign key to `users`, and member rows confer member
    scope. A visitor match must **never** do that, and putting both in one table invites one sync feed or
    one matcher to blur the line.
  - It is built from the bound cluster's embeddings that were already in RAM (no audio). The ResNet34 recipe
    applies: 5 clips of at least 2 s, outlier rejection. If fewer are available, Zoe keeps collecting during
    this conversation, with consent already given, and stores nothing until 5 exist.
  - It syncs to panels on its own device-token-only feed (`/api/visitors/voice/sync`).
  - It is updated later only through the anchor-gated adaptation rule (#2002) and only from matches well
    above threshold. Apple's "Personalized Hey Siri" caps implicit enrolment at 40 vectors and warns that
    "wrongly accepted utterances from impostors could corrupt the profile". An introduction that bound the
    wrong cluster must be undoable: "Zoe, that wasn't Sam" deletes the profile [research §2, §7].
- **Exception, owner decision 2026-10-14:** a member's *learned voiceprint* of a person they tag in their own Omi
  pendant recordings is NOT consent-gated. It is a different object: private to that member, used only to suggest
  tags in that member's own recordings, never synced to panels, never evidence for any assurance level, and it
  expires unconfirmed. Guardrails: [memories-space.md](memories-space.md) §2.6. It never becomes this visitor
  profile; an introduction still enrols the visitor afresh with their consent.

### 5.3 Building up who they are

- When a turn matches a visitor profile, it is attributed `visitor:<directory_id>`. It **runs as guest
  scope**, always: ZAL0 for household data. It gets personalisation only ("Hi Sam").
- What Sam says about themself ("I'm vegetarian") goes to **Sam's own layer** (`person_self_facts`, §5.5).
  It is kept only if Sam gave the second, separate consent (`self_facts_consent_at`); without it nothing Sam
  says about themself is stored. Provenance `self`, authority below member-stated. It is never written as a member's
  fact, and never used to supersede one.
- What a member says about Sam goes to **that member's knowledge layer** (their own `people` row and
  memories), private by default (§5.5).
- Affect: none. A visitor is a guest for the emotional-safety rules: no affect score, no drift row.
- Recall follows §5.5's visibility rules.

### 5.4 Forgetting

| What | When it goes |
|---|---|
| An un-introduced voice cluster | 30 min after it was last heard, or a restart. **Nothing was ever written, so there is nothing to delete.** Negative control: grep the Pi's disk and the server logs for the cluster tag and any vector after a session |
| A declined introduction | no voiceprint is ever stored; the directory entry and the introducer's knowledge row remain, for the introducer and an admin to keep or delete |
| A visitor voiceprint | `visitor_voice_days` (default 180) after it was last heard; "Zoe, forget me" in Sam's own recognised voice (ZAL1 for Sam's own profile is enough, because deleting your own biometric is a right); an adult member's "forget Sam's voice" (ZAL3); or an admin removing Sam from the directory (cascade). A delete is pushed to the panels at once, like `voice_profile_forget` |
| Facts Sam stated (Sam's own layer) | "Zoe, forget me" (with the voiceprint), or the directory entry's removal (cascade) |
| What a member knows about Sam | only that member's own forget (existing permanent-forget rules); see §5.5 for what an admin removal does and does not touch |

The retention difference from members ("kept until deleted", [biometric-retention-policy §2](../knowledge/biometric-retention-policy.md))
is deliberate. A member can see and delete their own profile in Settings. A visitor cannot, so a visitor
profile expires on its own. There is shipped precedent: Amazon deletes a Visual ID that has not been
recognised for 18 months [research §2, secondary]. The retention policy needs a new §8 for visitors when Phase 4 ships.

### 5.5 Whose is the person? Three layers (owner question, 2026-10-14)

Owner: *"if you introduce Zoe to someone, is this person's identity that Zoe starts building out only stored
in that user's storage, or available to the whole system?"*

**Answer: both, in separate layers, and Zoe never moves knowledge between them on her own.**

**Today [src]:**

- `people` rows default to `visibility = 'family'` (`services/zoe-data/VISIBILITY_MATRIX.md:13`,
  `alembic/versions/0001_initial_schema.py:97`), but each row belongs to its creator (`user_id`).
- The free-text `notes`, `preferences` and `relationship` fields on that shared row are therefore visible to
  every member. Whatever one member tells Zoe about a person can land where everyone sees it.
- The introduce intent looks only at the introducer's own rows (`routers/voice_tts.py:3107-3110`,
  `WHERE user_id=?`), so two members who meet the same person make two unrelated rows.
- There is no household-level "this person exists" record.

**The three layers:**

| Layer | Holds | Who sees it | Who writes it | Store |
|---|---|---|---|---|
| **1. Household directory** | that the person exists; display name; relation to the household ("family friend", "Alex's cousin"); status (provisional or confirmed); the visitor voiceprint, only with the person's own consent, because any panel must be able to recognise them | every member at ZAL1+; never a guest; the voiceprint itself is read only by matchers (device-token sync), never shown | an adult member introducing (provisional), the introducer or an admin confirming; any adult member editing the relation (history kept) | new `person_directory`; `visitor_voice_profiles` FK to it |
| **2. Each member's knowledge** | what that member told Zoe about the person: notes, how they met, their own relationship ("my old boss"), facts, memories | **that member only**, by default; others only through an explicit share | that member | the member's own `people` row (`user_id` = member, `visibility = 'personal'` for introduction-born rows) linked by a new `people.directory_id`; their memories (already `user_id`-scoped) |
| **3. The person's own** | what the person says about themself, kept only with their consent | the person themself, when recognised (personalisation); members only if the person says "you can tell everyone" | the person, by voice | new `person_self_facts` (FK to the directory, cascade) |

**Data model (Phase 4 migration; nothing built):**

```
person_directory (
  id uuid PK, scope_id text NOT NULL,          -- the shared scope this entry belongs to (one value today)
  display_name text NOT NULL, relation_to_scope text NULL,
  status text CHECK (status IN ('provisional','confirmed')),
  created_by text FK users, created_at timestamptz, confirmed_by text NULL, confirmed_at timestamptz NULL,
  member_user_id text NULL FK users ON DELETE SET NULL,     -- set if the person later becomes a member here
  self_facts_consent_at timestamptz NULL,                    -- the person's own, separate consent for layer 3
  deleted_at timestamptz NULL )
person_directory_relation_history (directory_id FK CASCADE, relation, set_by, valid_from, invalid_at)
                                                            -- invalidate-never-delete, like the people graph
people.directory_id  uuid NULL FK person_directory ON DELETE SET NULL      -- layer 2 link
visitor_voice_profiles (directory_id FK person_directory ON DELETE CASCADE, ...)   -- section 5.2
person_self_facts (id, directory_id FK CASCADE, text, category, stated_at, consent_at NOT NULL)
person_knowledge_shares (owner_user_id, people_id | memory_id, share_to ('member'|'shared_scope'),
                         shared_with_user_id NULL, granted_at, revoked_at NULL)
```

No sentinel owner such as a fake household `user_id` is introduced. A guest sentinel is the absence of an
identity, never an owner (`routers/AGENTS.md`). That is why the directory is its own table.

**Scope-neutral by charter.** [ZOE_DESIGN_PRINCIPLES.md](../governance/ZOE_DESIGN_PRINCIPLES.md) §3 and §5 forbid
baking `home` / `family` / `household` into schema column names, auth or tool signatures, and require a
scope field from day one. So the directory is keyed by `scope_id` (the instance's shared scope; one value
today), the relation column is `relation_to_scope`, and the settings key is `site_security`. "Household" is
the word the UI and this prose use for a home install; a business install would say "site".

**Visibility rules** (each pinned by a table test when built):

1. **The directory is household-wide.** Name, household relation and status are visible to members. Guests
   and unrecognised speakers get nothing: "Who's Sam?" from a guest answers "I'm not sure who you mean".
2. **Member knowledge is private by default.** It is shared only by the owner's explicit act, per fact or per
   person: "share what I know about Sam with Jordan" or "with the family". Each share is a grant row and can
   be revoked. The default is a per-user setting (below).
3. **Zoe never relays one member's private knowledge** about a person to another member, or to that person.
   - The brain context for member B is built only from B's layer 2, the directory, and grants made to B.
   - "What did Alex tell you about me?" from Sam answers "That's Alex's to share."
   - This must hold in recall, summaries, the night mind and proactive offers, not just in direct questions.
4. **Sensitive classes stay with the speaker even when the record is family-visible.**
   - The restraint classes (`health`, `money`, `family_conflict`, `grief`, `other_member`, `affect`;
     `services/zoe-data/restraint.py:72`) are never written to the directory and never included in a
     whole-person share.
   - Sharing one needs a per-fact share that names the fact.
   - **When the person is recognised in the room** (a visitor match, or any `voices_heard >= 2`), Zoe withholds
     facts about that person from speech, even to the member who owns them, and offers to show them on the
     member's phone. The identity engine feeds restraint here.
5. **Layer 3 is the person's.** It is used only to personalise for them. It moves into a member account only
   with their consent if they later become a member here (below).

**Forgetting, per layer:**

- **A member forgets their own knowledge** ("forget what I told you about Sam"). This uses the existing
  permanent-forget rules on their own rows and memories. The directory entry and other members' knowledge
  are untouched.
- **An admin removes the person** (ZAL3, admin). The directory entry, voiceprint, relation history, layer 3
  and every share grant pointing at it are deleted. Members' own knowledge rows lose their `directory_id`
  (`SET NULL`) but stay theirs: an admin cannot delete another member's private memory (the "other members'
  data" floor, §3.2). Zoe tells the admin so: "Removed Sam from the household. What others told me privately
  stays theirs."
- **The person says "forget me"** in their own recognised voice. This deletes the voiceprint and layer 3, and
  marks the directory entry name-only (provisional, no voice) for members to keep or remove. Members'
  knowledge is untouched. Whether a visitor can demand more under Australian law is a lawyer question
  (research §9).

**Per-user settings interplay** (adds to §4.1):

- `security_profile.people_sharing.default` = `private` (default) or `family`. This is the default for new
  layer-2 knowledge. `family` writes only non-sensitive classes as household-visible; sensitive classes stay
  private regardless (rule 4).
- The levels already in §4.1 apply:
  - reading the directory: `personalisation` (ZAL1);
  - reading your own knowledge about someone: `memory` (ZAL2 by default);
  - sharing, revoking, introducing: `own_writes`;
  - removing a person from the directory: admin, ZAL3.
- `site_security.introductions` turns layer 1 creation by voice on or off. `children.*` governs kids
  (below).

**Edge cases:**

- **A child introduces someone** ("Zoe, this is my friend Jo").
  - Zoe is polite ("Hi Jo!") but creates **no directory entry and no voiceprint**.
  - The child's own knowledge layer may note "Jo is my friend" under the minor rules (no emotional
    profiling; [emotional-safety-note §4](../governance/emotional-safety-note.md)).
  - A parent (admin) can later add Jo to the directory without a voiceprint ("Zoe, Jo is Riley's friend from
    school").
  - Optional household setting `children.introductions_pending = true`: a child's introduction creates a
    *provisional* entry that only an admin can confirm. Never a voiceprint.
- **Two members disagree about the relationship.** Alex says "Sam is my cousin", Jordan says "Sam is my
  colleague".
  - These are layer-2 facts and both are true from each member's side: each lives in its owner's row, with no
    conflict and no overwrite.
  - Only the directory's `relation_to_scope` is shared. An edit by any adult supersedes the old value with
    history (invalidate-never-delete, as `person_relationships` already does with `valid_from` / `invalid_at`
    / authority).
  - If two adults set it differently within a week, Zoe asks instead of picking ("Alex calls Sam family, you
    called Sam a colleague. Which should the household see?"). An admin's choice settles it.
- **The same person introduced twice.** When Jordan introduces "Sam" and the voice matches Sam's existing
  visitor profile, or the name matches a directory entry, Zoe links Jordan's new knowledge row to the
  existing entry ("I've met Sam before"). The directory is household-visible, so saying so leaks nothing
  private. Two different Sams get two entries, told apart by relation; Zoe asks "the Sam who's Alex's cousin?"
  only when both are plausible.
- **The person is a member of another household's Zoe.** The charter holds cross-site identity sharing
  deliberately **open** (§7: personal-Zoe vs site-Zoe topology, and whether a shared "you" profile exists
  across instances). This design neither builds nor forecloses it.
  - Today Sam's directory entry, voiceprint and consent belong to this instance's scope (`scope_id`) and
    expire here. Nothing leaves the box by default (VISION principle 2).
  - Keying everything by `scope_id` and consent per scope is what keeps both topologies possible.
  - Any future cross-site link would need Sam's own consent at both sites and a charter decision first.
- **The person later becomes a member here.**
  - `person_directory.member_user_id` is set.
  - The visitor voiceprint is **deleted, not promoted**. A conversation-built voiceprint never becomes a
    member credential, so the new member enrols through the guided 5-clip flow.
  - Layer 3 moves into their account memory only if they say yes.
  - Other members' knowledge stays where it is.
- **A member leaves the household.** Their user deletion already cascades their own rows and biometrics. The
  directory entries they created stay with the household, re-owned by an admin (`created_by` keeps history).

## 6. Evidence sources roadmap

Jetson cost is the engine only (pure math, a ring of at most 64 small events per panel, well under 1 MB)
for every row. The Jetson's free memory is tight (a few hundred MB steady in the
[2026-10-03 memory-pressure profile](../knowledge/memory-pressure-profile-2026-10-03.md), a dated observation),
so **no source may add a model to the Jetson**.

| Source | Status | What it needs | Pi cost | Effort |
|---|---|---|---|---|
| PIN at the panel | exists | emit `pin` events from the PIN success path | 0 | S |
| Fresh panel session / idle window | exists | read as KNOW-group continuity | 0 | S |
| Voice verdict (per turn) | implemented, disabled by default (Resemblyzer; shadow logs scores without attaching them when enabled); ResNet34 in #2024 | emit `voice` events with calibrated bits; shadow week labels | ResNet34 standalone ~122 MB peak, p50 ~70 ms [branch] | S (after #2024) |
| Voice session | exists, flag-dark | becomes a consumer of the engine (ZAL2 = session open) | 0 | M |
| Different voice / speaker change | partly exists (the session closes on another voice) | emit `different_voice`; unknown cluster tags from the Pi | KB | S |
| Touch activity | **missing**: the kiosk sends `is_foreground: true` always | add `last_touch_at` to the 5 s `state/sync` | 0 | S |
| Voice activity (wake, VAD in window) | exists on the Pi, not recorded | one counter in the turn payload | 0 | S |
| Unknown-voice clusters | new | Pi in-RAM clusterer (§5.1) | < 1 MB | M |
| Visitor profiles | new | migration, sync feed, matcher | ~0 | M |
| Phone home/away | operator: HA companion app | read `person`/`device_tracker` with a 30 s TTL | 0 | S + operator |
| Phone near (BLE IRK / classic paging) | new | Pi scanner thread; IRK or MAC in the Pi env only; shares the radio with Wi-Fi, omi-sync and panel-bt (one flock) | 25-40 MB [unverified, presence record] | M + operator |
| Omi pendant seen | partly exists (omi-sync connects about every 2 min, records nothing) | emit `omi_seen` (device hash only) | 0 | S; the wearer is assumed, not proven |
| mmWave occupancy | hardware, A$38-80 | HA `binary_sensor`; sleep gate and `anyone_here` | 0 | S + purchase |
| Face (camera on wake) | module exists, no producer; B4.3 undecided; **USB power prerequisite** | daemon hook parallel with STT, liveness, shadow week | ~15 MB models + 50-80 MB session [unverified] | L |
| Diarisation model (pyannote-class) | not recommended | - | hundreds of MB, seconds per minute of audio | - |

## 7. Measurement

**Per factor.** Report FAR and FRR at the operating points the policy uses, on household shadow data
(labelled with the `(boot, seq)` handle the speaker shadow already uses), with bootstrap intervals. Paper
numbers (ResNet34: owner FRR ~0.3-0.5 % at FAR 1 %; public far-field FRR ~5 % at FAR 1 % [record]) are
starting points only.

**Fused, the numbers that matter:**

- **False ZAL2**: the engine reported ZAL2 or above for `p` while the truth was not `p`.
  - Target: 0 in every lab cell.
  - Live: below 1 per 1,000 private-scoped turns, under the conditions stated in §3.1. Report `f_other` and
    `N_cmp` as measured, not assumed.
  - Per profile, the voice FAR at the ZAL2 point must be demonstrated (at least 6,000 impostor trials) before
    ZAL2-by-voice acts.
- **False ZAL3**: the same at ZAL3. It must be 0, since ZAL3 requires a PIN.
- **Friction**: PIN prompts per member per day, and turns where a verified member was told "say it again so
  I know it's you". Target: lower than today with Balanced.
- **Stale identity**: seconds from a presence break, or another voice, until the previous member drops below
  ZAL2. Target: under 2 s (the next turn).
- **Unknown detection**: open-set reject rate for non-members (visitors, TTS, radio). Owner false "new
  voice" rate below 1 per day.

**Shadow plan.** `ZOE_IDENTITY_ENGINE=shadow` for 14 days, after the ResNet34 shadow week:

- the engine computes a level per turn beside the live decision and logs the disagreement;
- nothing acts;
- the owner labels a sample;
- go/no-go on the targets above.

**Lab cells with negative controls.** Every cell has a control that must fail if the defence is removed.

| Cell | Setup | Must happen | Negative control |
|---|---|---|---|
| Lookalike | a family member (or a closest-voice public speaker) speaks after the owner | `different_voice`; the owner's ZAL2 closes; no private card | disable the `different_voice` event → the cell must fail |
| Replay | the owner's voice played from a phone speaker | voice bits capped at VOICE `hi`; no ZAL3 (no PIN); payments impossible | set the KNOW cap to the VOICE value → a replay reaches ZAL3 → the cell fails |
| Cloned voice (TTS of the owner) | a Kokoro/XTTS-style clone from public clips | the same as replay; record whether ResNet34 accepts it (ASVspoof says it often does [research §8]) | n/a: this measures, it does not gate |
| Phone in someone else's pocket | the owner's phone near the panel, a guest speaks | DEVICE ≤ +3 bits never reaches ZAL1 alone; the unknown voice closes the session | raise the DEVICE cap to 10 → the cell fails |
| Stale session | the owner verified, leaves, a guest arrives within the TTL and asks "what's on my calendar" | the guest's voice → `different_voice` → PIN prompt | remove the presence break → the guest reads the calendar → the cell fails |
| Leave-lock stale session | the member unlocks with the PIN (ZAL3), `lock_when_my_phone_leaves` is on, and the phone tracker turns to away; then a private read is asked within the idle window, by voice and by tap | an absorbing `lock`: the PIN and every other earlier event are discarded, the PIN panel session is ended, and the read asks for fresh proof | replace the lock with the plain −5 bits → the PIN's 11 bits keep ZAL2 → the read succeeds → the cell fails |
| Crowded room | the member is verified (ZAL2); a second voice (member, visitor or unknown) speaks; the member asks "what's on my calendar?" | nothing private is spoken or shown; Zoe says it's private and sends it to the member's phone; with `others_present: ask`, it reads only after a "yes" in the member's own voice | let crowding cap only the display → the answer is spoken → the cell fails |
| ZAL2 operating point | impostor trials (public corpus against each profile, plus household cross-member trials) at the derived threshold | measured false-accept rate per profile at or below the derived FAR, with at least 6,000 trials | score at the FAR 1 % threshold → the false-ZAL2 rate exceeds the target → the cell fails |
| Silent stale session | the owner verified, leaves, a guest **taps** the calendar without speaking | no private card on tap without a fresh voice or PIN (the panel's HTTP session stays guest, today's behaviour) | pin it with a Playwright estate test |
| Visitor | an unknown voice speaks 5 turns | one cluster, chip shown, nothing on disk after 30 min | grep disk and logs for vectors and tags |
| Introduction consent | the member says "this is Sam", and **the member** says "yes" | no voiceprint stored | accept any "yes" → the cell fails |
| Child introduction | "this is my son's friend Jo" | no offer to remember the voice | turn the child rule off → the offer appears → the cell fails |
| Uncalibrated source | add a fake source with llr 10 and no calibration record | 0 bits | remove the calibration gate → the cell fails |
| Restart | restart zoe-data mid-session | everyone becomes guest; re-proof needed | none needed |
| No relay (§5.5) | Alex privately tells Zoe "Sam is job hunting"; Jordan asks "what's new with Sam?"; Sam, recognised, asks "what did Alex say about me?" | neither answer contains it; Sam's answer is "That's Alex's to share" | build the recall context from every member's rows → the fact leaks → the cell fails |
| Sensitive class in the room | Alex has a health fact about Sam; Sam is recognised at the panel; Alex asks about Sam | the fact is not spoken; it is offered on Alex's phone | turn off the in-room withhold → spoken → the cell fails |

## 8. Phases

Every phase is its own PR, flag-dark, ci_safe table tests on the pure functions, and the voice replay gate for
anything on the voice path.

| Phase | Builds | Flags (default) | What changes for the user |
|---|---|---|---|
| **0 Groundwork** | `identity_engine.py` (events, fusion, levels, `explain`, injectable clock); `identity_policy.json` (categories and floors, absorbing `voice_session_policy.json`'s step-up list); producers from today's sources (PIN, fresh session, voice verdict, voice session open/close, different voice); kiosk `last_touch_at`; shadow JSONL | `ZOE_IDENTITY_ENGINE=off\|shadow\|on` (off) | **Nothing.** An admin debug view shows bits per group |
| **1 Settings** | `security_profile` + `site_security` stores; desktop "Sign-in & security", touch presets card, voice phrases; "why do you think I'm me" tier; **only tightening takes effect** (raise-only on top of today) | `ZOE_SECURITY_SETTINGS` (off), `ZOE_IDENTITY_EXPLAIN` (off) | Members can choose Strict ("always ask my PIN for my calendar") and ask why Zoe thinks it's them. Relaxed is shown as "coming soon" |
| **2 Engine acts** | the voice session and PIN freshness become engine consumers; the effective level gates private reads and cards; loosening takes effect; crowding cap; lock on another voice everywhere | `ZOE_IDENTITY_ENGINE=on` | Fewer PIN prompts when Zoe is sure; faster lock when someone else talks; Relaxed works |
| **3 Notice new voices** | Pi online clusterer; `unknown:<tag>` events; "new voice" chip; "who's here?" tier | Pi `UNKNOWN_VOICE_NOTICE`; `site_security.notice_new_voices` (chip once enabled) | A chip when Zoe hears someone she doesn't know; "who's here?" answers. Requires ResNet34 acting |
| **4 Introductions** | the three people layers (§5.5): `person_directory` + relation history, `people.directory_id`, `person_self_facts`, `person_knowledge_shares`, private-by-default member knowledge with share/revoke, no-relay tests; `visitor_voice_profiles` migration; visitor sync feed and matcher; consent dialogue with a speaker check; minors rule; expiry sweep; "forget me"; retention policy §8 | `ZOE_VISITOR_VOICES` (off); `ZOE_PEOPLE_LAYERS` (off); `introductions` (off) | "Zoe, this is Sam" works; Sam is greeted by name next time; Sam can say "forget me"; what you tell Zoe about Sam stays yours unless you share it |
| **5 New presence evidence** | HA companion app reader; BLE IRK owner-near on the Pi; Omi seen; mmWave in `anyone_here`; "lock when I leave" | `ZOE_PRESENCE_SOURCES` (empty) + Pi flags | "Sign me out when I leave"; payments still need the phone; the sleep gate gets smarter |
| **6 Face** | after B4.3 and the power fix: daemon face-on-wake with liveness, a shadow week, then the FACE group; face UI (enrol and delete) first | `FACE_ID_ENABLED` (Pi), `ZOE_FACE_ID_ENABLED` | Being seen counts as evidence; still never ZAL3 |
| **7 Multi-panel** | house-level session with a cross-room discount (the July plan's idea), attention gating | - | Walking room to room keeps you recognised |

## 9. Risks and the honest "is this too complicated?"

**Is it too complicated?** Partly, and here is the honest split.

- **The engine is not.** It is a sum of clipped, decayed numbers with a table of floors: a few hundred lines
  of pure functions and a JSON file, smaller than `voice_session.py` plus its policy.
- **Calibration is the real work.** Each source needs a shadow period with labelled truth before it may
  count.
- **The settings surface is the real UX risk.** Seven categories times five levels is too much for a family.
- **Mitigations:**
  - three presets on the panel;
  - the per-category grid only on desktop;
  - code floors shown as "always";
  - and the fact that, of all this, the household only needs Phases 0-2 with today's signals to get the
    owner's two asks: "voice as a password" done safely, and "lock when someone else talks".
- **The field agrees** [research §2]:
  - Google, Amazon and Apple ship exactly the cut-down form: biometrics personalise, and a PIN, code or
    phone protects purchases and security.
  - None ship a user-tunable fused score.
  - Ours would be ahead of them on explainability and local control, not on raw security.

| # | Risk | Mitigation |
|---|---|---|
| R1 | False accept on a lookalike relative (shared voice and face traits) | calibrate with household members as each other's impostors; if two members are near-indistinguishable, the engine refuses ZAL2 by voice for both (it says so in their settings); the PIN stays |
| R2 | Replay or cloned voice | voice can never reach ZAL3; money needs the phone; ASVspoof-class detectors are not reliable enough to lean on and cost RAM [research §8]: record, don't rely |
| R3 | Double counting correlated evidence → overconfidence | group caps; within-group replace-not-add; an uncalibrated source is 0 |
| R4 | Friction: false rejects make Zoe nag | target and measure PIN prompts per day; the Balanced default matches today; never hard-lock mid-conversation (warn first, as Apple does) |
| R5 | Privacy creep from noticing visitors | ephemeral clusters by construction; chip not speech; introductions off by default; visitor consent from their own voice; expiry |
| R6 | Children | no visitor child voiceprints; kids' enrolment off; admin-only kid settings; lawyer flag |
| R7 | The explanation leaks someone else's whereabouts | explain only about the asker, only at ZAL2+, from a fixed phrase table |
| R8 | Restart or outage leaves someone "verified" | in-memory store: a restart means everyone is guest; any failed check falls to the floor's step-up |
| R9 | **Role mismatch today**: zoe-data's `guest_policy` maps zoe-auth's `family_member` and `child` to the guest capability matrix (`guest_policy.py:127-132,213-218`), while `is_guest_user` says they are not guests | decide intended semantics before Phase 1 (owner decision 13); recorded in `docs/knowledge/open-problems.md` |
| R10 | Legal: biometric data is sensitive information under the Privacy Act; visitor voiceprints; recording visitors under the WA Surveillance Devices Act | consent-first, embeddings only, expiry; **lawyer review before Phase 4 ships, and before Zoe is offered to any other household** [research §9] |
| R11 | Phone or BLE presence spoofing, or the radio shared with Wi-Fi | DEVICE is weak by policy (≤ +3 bits); one radio flock; never an authenticator |
| R12 | Calibration drift (new mic, illness, kids' voices changing) | per-source calibration versioned with `model_id`; adaptation stays anchor-gated; a monthly re-check of shadow agreement |
| R13 | One member's private knowledge about a person reaches another member or the person, through recall, a summary, the night mind or the family-visible notes on today's `people` rows | the three layers (§5.5); context built per member from their own layer, the directory and explicit grants; the no-relay lab cell; owner decision 15 on existing rows |

## 10. Where the design touches existing contracts

- `routers/AGENTS.md` identity freshness. The engine is **one more source in the same chain**, and it
  replaces the voice session's place in it. `bound_user_id` still never holds a voice-derived identity. The
  freshness rule ("identity never outlives its source") becomes "identity decays with its source" and stays
  fail-closed.
- `services/zoe-data/VISIBILITY_MATRIX.md` says `people` default to `family`. §5.5 keeps that for the
  directory layer, but makes introduction-born member knowledge `personal` by default. The matrix needs a
  row for the directory and a note on member knowledge when Phase 4 lands (owner decision 15 covers
  existing rows).
- `biometric_scope.py` stays the authority for who may list or delete profiles. Visitor profiles get the
  same scoping (the introducer and admins; the visitor by their own voice).
- [voice-verified-session.md](../knowledge/voice-verified-session.md): its step-up tier becomes the ZAL3 rows,
  and its TTL becomes the VOICE half-life and the member's `voice_session_ttl_s`.
- [action-agent tiers](../research/zoe-action-agent-2026-10.md) §6.0: "identity strength scales with the tier"
  becomes the table in §3.2. ZAL4 is the PIN plus phone it already asks for.
- [biometric-retention-policy.md](../knowledge/biometric-retention-policy.md): add the occupancy-score sentence
  from the presence record §3.6, and a visitors section in Phase 4.

## 11. Owner decisions

1. **Adopt the ZAL0-ZAL4 ladder and the hard floors in §3.2?** Especially: money = PIN plus your phone,
   always; and is "forget a person" right at ZAL3?
2. **Default preset for adults**: Balanced (voice or PIN opens your private reads) once ResNet34 has passed
   its shadow week? Or Strict until the identity engine's own shadow fortnight?
3. **Private reads floor.** This design says private reads always need an identified member (ZAL1+), and a
   member who wants something visible to anyone shares it to shared scope instead. Agree?
4. **Notice new voices**: off, a chip (recommended default) or a spoken greeting?
5. **Visitor voiceprints at all?** If yes: is self-consent by voice enough, and is 180 days since last heard
   the right default?
6. **Children**: confirm no visitor child voiceprints ever (recommended), and kids' own voice and face
   enrolment off by default (as today).
7. **"Who's here?"**: count to anyone, names only to a verified member (recommended), or names to anyone?
8. **Phone presence** (carried over from the presence record): install the HA companion app? Which phone,
   and may Zoe hold its Bluetooth key (IRK) in the Pi's env?
9. **mmWave sensor** (carried over): buy one for the panel room (A$38-80) instead of camera occupancy?
10. **Face** (B4.3): on, after the power fix and a face UI, as one more ZAL2 factor? Or off?
11. **Lawyer**: a consult before Phase 4 (visitors) and before Zoe is ever installed for another household?
12. **The explanation**: on by default for adults (recommended)? Off for kids?
13. **Roles**: should `family_member` and `child` keep falling back to the guest capability matrix in
    zoe-data, or get their own rows (`family_member` as `user`, `child` as a new restricted row)? Phase 1
    needs this.
14. **A passkey or app on the phone** as the ZAL4 factor (stronger, more work), or the Telegram Approve the
    action-agent plan already proposes?
15. **The three people layers (§5.5).** Adopt the household directory, private member knowledge and the
    person's own layer? And what happens to **existing** `people` rows? Today they are family-visible with
    free-text notes, so they would either be left as they are, or have their notes and preferences moved to
    personal (a one-off migration, reversible from history).
16. **Default for sharing what you tell Zoe about people**: private (recommended) or family?
17. **Kids introducing people**: no directory entry at all (recommended), or a provisional entry an admin
    confirms?
18. **"Forget me" from a visitor**: should it also erase what members privately know about them? This design
    says no, because it is the members' own memory. A lawyer may say otherwise.
19. **The voice operating point for ZAL2** (§3.1): accept the stricter, target-derived point (FAR 0.05 % per
    profile for four members, with more "say it again" or PIN fallbacks, especially across the room)? Or relax
    the false-ZAL2 target instead?
20. **Others present** (§2.1): send private answers to the member's phone by default (recommended), with
    "ask me first" as the member's opt-in?
