# Multi-signal user identity on the touch panel (voice + camera), Pi-heavy compute

## Context

Jason wants the touch panel (Pi "zoe-touch" @192.168.1.61) to learn users' voice **and** visual characteristics to help prove identity. Constraint he added explicitly: **run as much of the process as possible on the Pi to ease stress on the Jetson** (Orin NX 16GB is memory-critical — few hundred MB free steady-state).

Exploration found the voice half already built and dormant: the panel daemon (`scripts/setup/zoe_voice_daemon.py`) computes resemblyzer 256-dim voice embeddings locally and POSTs to `/api/voice/identify` (cosine match server-side, `speaker_profiles` table, `ZOE_SPEAKER_ID_THRESHOLD=0.82`), gated by `SPEAKER_ID_ENABLED=false`. Identity merge precedence lives at `services/zoe-data/routers/voice_tts.py:2671-2703`; panel auth (device-token → bound_user, fail-closed guest, PIN step-up) in `services/zoe-data/routers/panel_auth.py`. No camera/vision code exists anywhere; the live brain has no vision tower and must not get one (RAM).

## Architecture principle (per Jason's constraint)

**All biometric compute on the Pi; Jetson = storage + policy only.** Designed for a **multi-panel household**: each panel is a stateless biometric sensor + UI carrying its own compute — adding panels adds zero Jetson load. The one thing kept central is the (tiny) **house identity session**, because that's what makes N panels feel like one system:

- Profiles enroll once, sync to every panel (same `/profiles/sync` feed, keyed by device token).
- Identity sessions are **house-level, room/panel-tagged**: server tracks per-user `{confidence, last_seen_panel, last_evidence}` — a few hundred bytes, float math only, no models. Verified at the kitchen panel → the living-room panel inherits warm (slightly discounted) confidence instead of re-challenging in every room; presence continuity transfers with the person (panel-local motion/occupancy breaks it per room).
- House-scoped signals (phone-on-network, door sensors via HA) apply to all panels at once from one place instead of being replumbed into N Pis.
- Policy (thresholds, never-rebind, audit) lives in one spot; a panel can only *claim* scores, never grant itself scopes.

- Pi: mic capture, voice embedding (resemblyzer, already there), camera capture, face detection + embedding (ONNX on CPU), **and local cosine matching** against a synced profile cache.
- Jetson: Postgres source-of-truth for profiles (`speaker_profiles`, new `face_profiles`), a profile-sync endpoint the Pi pulls from, and the **policy decision only** (thresholds, fusion, PIN-equivalence, never-rebind) — microseconds of float comparison, no models, no media bytes ever leave the Pi.
- The Pi sends per turn: `{voice_user_id, voice_score, face_user_id, face_score}` (claims + scores). The server validates against policy; the panel never grants itself scopes.

Camera hardware: **USB UVC webcam** (Jason's choice) via OpenCV `VideoCapture`. No continuous streaming — capture 3–5 frames on wake-word, pick best face, embed, close device, discard frames.

Face model: InsightFace **buffalo_sc** pack (SCRFD-500M detector + `w600k_mbf.onnx` MobileFaceNet-class recognizer, ~16 MB total, ~30–60 ms on Pi ARM CPU via onnxruntime — already in `pi-requirements.txt`). Prefer raw ONNX files + hand-rolled 5-point alignment if the `insightface` pip wheel is troublesome on the Pi's arch; pin SHA256 either way, install to `/home/pi/.zoe-voice/models/`.

## Research findings baked into this plan (2026-07-19 web sweep)

- **Precedent — Google Nest Hub Max (Face Match + Voice Match), Amazon Echo Show (Visual ID):** both run recognition fully on-device, store embeddings not images, enroll from multiple angles (Amazon: 5), and — crucially — **treat face/voice as personalization, never authentication**. Payments/secrets always need a PIN/code/phone push. Google's own docs admit photo + similar-family-member spoofing. Lesson kept: our "PIN-equivalent" step-up unlocks only what the panel PIN unlocks, never rebinding, and the plan stops claiming "proof."
- **Attention gating (Nest's best idea, cheap):** show personal content only when the face is recognized AND looking at the screen (head-pose from the detector's landmarks — free). Added to Phase 3 UX.
- **Demonstrated exploit to avoid:** Google Home verified only the wake word, then trusted the whole session. We already identify per-turn — keep it that way; re-verify on sensitive commands.
- **Face stack on a Pi:** Frigate's built-in face rec **requires AVX — cannot run on a Pi**; borrow its pattern not its code. Proven-on-Pi reference: Qengineering's ncnn pipeline (RetinaFace detect + ArcFace embed + anti-spoof ≈ 8–10 FPS on a Pi 4 CPU). Our buffalo_sc ONNX choice is the same model class; ncnn is the fallback if onnxruntime is slow. Adopt Frigate's threshold discipline (recognize ~0.9 / unknown <0.8 band, min face area) and enrollment hygiene (few good frontal crops, blur/pose gating, no near-duplicate training).
- **Liveness is cheap — add it:** MiniFASNetV2 (Silent-Face-Anti-Spoofing): 1.8 MB, 80×80 input, ~37 ms/frame on Pi CPU. Passive check + require N consistent frames; skip blink challenges (bad UX). Kills printed-photo and most phone-screen replays.
- **Speaker model reality check:** resemblyzer is ~4–5% EER class; ECAPA-TDNN (~80 MB ONNX) is ~1% on benchmarks — but SHORT utterances (2–5 s) degrade any model to mid-single-digits–10% EER in a noisy room. Keep resemblyzer for Phase 1 (already wired), note ECAPA-ONNX as a drop-in upgrade path. Closed-set household trick: accept only if best-match ≥ threshold AND beats the runner-up by a margin.
- **Fusion science (adopted in Phase 3):** score-level weighted sum after **z-score normalization** (`fused = w_f·z(face) + w_v·z(voice)`), weights ∝ inverse-EER and **context-adaptive** — a degraded modality (dark room, 1 s utterance) is down-weighted or dropped from the sum, never contributes a zero. Never fixed 50/50. Confidence over time = exponential decay `C(t)=C₀·e^(−λΔt)` refreshed by `C ← max(C_decayed, fused)`; 3-tier bands (high/medium/low) drive friction.
- **Calibrate against the household, not paper defaults:** the #1 real threat is family similarity (relatives share face AND voice traits), so tune thresholds using enrolled household members as each other's impostors. If two members are near-indistinguishable, PIN stays for them on sensitive actions — don't pretend.
- **Template update is the sharp knife:** rolling gallery of N high-quality embeddings per user, admission gated at well-above-threshold AND cross-modally corroborated (add a face sample only when voice also agreed), capped, with the original enrollment kept as an immutable anchor. Prevents drift and poisoning. Re-enroll kids often.
- **Presence fusion pattern (HA community consensus):** mmWave = instant "someone is here" without identity; BLE (Bermuda, not ESPresense — unmaintained) = "whose phone is here" with lag; face/voice = per-utterance who. Our design matches: cheap occupancy (HA sensors / camera tick, optionally a ~$6 LD2410 mmWave later) gates when identity work runs and keeps it sticky. Multi-person occupancy caps confidence below step-up.
- **Uncertainty UX (Apple's pattern):** below threshold, ask ("Is that you, Jason?") rather than guess; unknown face → guest mode that still does timers/weather/music, never a locked screen; warn before confidence expires rather than hard-locking mid-interaction.

## Phase 1 — Voice speaker-ID live, matching moved to the Pi

**PR 1.1 — server: profile sync + consent + payload fields**
- Add `GET /api/voice/profiles/sync` (device-token-authed) returning enrolled embeddings + user_ids for the panel's household; Pi caches to disk, refreshes on daemon start + periodic/ETag.
- Add nullable `consent_at` to `speaker_profiles` (alembic migration); enroll UI gains explicit consent checkbox; identify/sync excludes rows without consent.
- Extend the turn payload (`/api/voice/turn_stream`) with optional `voice_user_id`/`voice_score`; server-side threshold check in the existing merge at `voice_tts.py:2671-2703` (keep `/api/voice/identify` as fallback for old daemons).
- Tests: `pytestmark = pytest.mark.ci_safe` in `services/zoe-data/tests/` (enroll, sync, threshold, consent exclusion, P-F6: identified guest never out-ranks bound user).

**PR 1.2 — Pi daemon: local matching**
- In `zoe_voice_daemon.py`: replace the `/api/voice/identify` POST with local cosine vs the synced cache; attach `voice_user_id`+`voice_score` to the turn payload. Still gated by `SPEAKER_ID_ENABLED`.
- Fix or document the `deploy-pi-voice.sh` path/user discrepancy (live daemon = user `pi`, `/home/pi/.zoe-voice/`, `systemctl --user restart zoe-voice` via ssh `zoe-pi`).

**Ops enable + verification**
- Enroll Jason (3+ utterances) via the existing Voice Identity section in `services/zoe-ui/dist/touch/settings.html`; set `SPEAKER_ID_ENABLED=true` on the Pi; restart.
- Replay gate (MANDATORY, daemon touched): `scripts/maintenance/voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock` vs `~/.zoe-voice-samples` — no said-vs-did or per-stage speed regression.
- Live smoke: enrolled speaker → merge log shows identified user; unenrolled → falls to bound/panel user.

## Phase 2 — Camera + on-Pi face ID

**PR 2.1 — server: face_profiles + endpoints (storage only, no models)**
- Alembic: `face_profiles(id, user_id FK, embedding BLOB float32, model_name, dim, consent_at NOT NULL, created/updated_at, active)` — multiple rows per user for pose variety.
- New router `services/zoe-data/routers/face_id.py`: `/api/face/enroll`, `/api/face/profiles` (list/delete), `/api/face/profiles/sync` (mirrors voice sync). Flag `ZOE_FACE_ID_ENABLED=false`. ci_safe tests. **No `/api/face/identify` — matching is on the Pi.**

**PR 2.2 — Pi: capture + embed + match**
- New module `scripts/setup/zoe_face_id.py` (keep the 1868-line daemon lean): camera open/grab/close lifecycle, SCRFD detect + best-face pick, **MiniFASNetV2 liveness gate (1.8 MB, ~37 ms; N-consistent-frames)**, embed, local cosine vs synced cache with **runner-up margin rule** → `(face_user_id, face_score, liveness_ok)`. Head-pose from landmarks retained for later attention gating.
- Hook in `zoe_voice_daemon.py` on wake-word: run capture+embed **async in parallel with STT** (budget <200 ms; must never delay the turn; absent/occluded face → `None`, fail-open to voice-only). Attach fields to turn payload. Flag `FACE_ID_ENABLED=false` on the Pi.
- `pi-requirements.txt`: add opencv-python-headless (+ insightface or raw-ONNX path); model files with pinned hashes.
- Ship in **shadow mode first** (`ZOE_FACE_ID_SHADOW=true`: log scores, influence nothing) to calibrate the threshold with real panel lighting/household faces.

**Verification**: on-Pi timing check; replay gate again; live smoke (face visible → logged match; no face → normal turn); privacy check — grep request logs/DB for zero image bytes.

## Phase 2.5 — Device presence via HA ("Jason's phone is home")

A third, near-free identity signal: personal devices observed at home raise the prior that it's really Jason. HA reality check (explored): the bridge (`services/homeassistant-mcp-bridge/main.py`) already passes through **any** domain (`GET /api/ha/entities?domain=device_tracker` works today via `ha_control.py:66-75`), but this home's HA has **no presence entities yet** (all virtual helpers — no device_tracker, mobile_app, person, motion/door sensors), and the bridge is REST-only (no event subscription).

**HA-side setup (operator, no Zoe code):**
- Install the HA **companion app** on Jason's phone → gives `device_tracker.jason_phone` (home/away via wifi + GPS) plus useful sensors (`sensor.*_wifi_connection` = home SSID).
- Optionally add a router-based `device_tracker` (nmap/ping integration) as a phone-agnostic "MAC is on the LAN" cross-check.
- Define `person.jason` binding the trackers (HA already has `automatic_person_creation: true` via SSO — verify it materializes, else define statically).
- Later hardware (upgrade path): BLE room-level via Bermuda on ESPHome proxies; LD2410 mmWave for the panel room.

**Zoe-side (PR 2.5):**
- `services/zoe-data/identity_presence.py`: small helper that queries `/api/ha/entities?domain=person` (+ `device_tracker`) **at fusion time** with a short TTL cache (~30 s) — on-demand pull matches the existing request-only bridge; no new reactive infra needed for v1.
- Mapping table (config or per-user setting): user_id → HA person/tracker entity ids.
- Feed into the confidence model as a **prior multiplier, never evidence of identity by itself**: phone home → modest boost / slower decay; phone away → cap confidence below step-up (if Jason's phone left, "it must still be Jason" no longer holds); tracker unknown/stale → neutral. Flag `ZOE_PRESENCE_DEVICE_ENABLED=false`.
- v2 (only if 30 s staleness hurts): HA automation on `person.jason` state change POSTing to a new bridge ingress endpoint — mirroring the existing `rest_command.zoe_voice_wake` HA→Zoe push pattern. Do not build a websocket subscriber for v1.

**Verification:** unit tests for the mapping/prior (ci_safe); live — toggle phone wifi off → within TTL the panel confidence caps and sensitive scope re-requires PIN.

## Phase 3 — Identity confidence rating + presence continuity + PIN-equivalent step-up

### The rating model (replaces a binary match with a live confidence score)

Server-side **house identity session**, room/panel-tagged: per user `{confidence 0–1, last_seen_panel, last_voice: {score, ts}, last_face: {score, ts}, presence_intact: bool}`. A different panel reading the session applies a small cross-room discount; step-up grants are per-panel.

- **Evidence raises confidence**: each turn's `voice_score` ("can hear Jason") and `face_score` ("can see Jason") update the session via score-level fusion — **z-score normalized weighted sum** (`fused = w_f·z(face) + w_v·z(voice)`), weights inverse-EER and context-adaptive (degraded modality down-weighted/dropped, never a zero). Liveness-failed face contributes nothing. Conflict (voice=A, face=B) slashes confidence and logs.
- **Time decays confidence**: `C(t)=C₀·e^(−λΔt)` refreshed by `C ← max(C_decayed, fused)`; half-life ~ tens of minutes, tunable — decay **paused while presence is intact**. Three bands: high (step-up eligible) / medium (personalization only, sensitive → PIN) / low (guest + ask "Is that you, Jason?").
- **Presence continuity** ("no one entered or left, so it must still be Jason"): once identity is established, keep it alive as long as the room state hasn't changed. Two watchers, either sufficient, both cheap:
  1. **HA sensors**: door/motion entities near the panel via the existing HA route (`/api/ha/entities`) — polled on the same short-TTL cadence as device presence (none exist in HA yet; needs hardware — until then the camera tick carries this); a door-open or new-motion-after-quiet event marks `presence_broken`. Device presence (Phase 2.5) feeds in as a prior: Jason's phone leaving home also breaks continuity.
  2. **Camera occupancy tick** on the Pi: low-rate check (every ~30 s, single frame, face-count/frame-diff only — no identity, no frames leave the Pi); person-count change → `presence_broken`. Piggybacks on the Phase 2 camera module; skipped if camera busy.
- On `presence_broken`: don't wipe identity — drop confidence to a floor (e.g. ×0.5) and resume decay; the next voice/face evidence naturally re-confirms or re-attributes. Fail-safe: if presence watchers are unavailable, behave as today (decay always on).
- The rating is exposed in the turn context (and UI indicator: "verified — can hear ✓ can see ✓ presence ✓") so downstream features can gate on `confidence ≥ X` rather than a one-shot match.

### Step-up policy

- Flags: `ZOE_IDENTITY_FUSION_ENABLED=false`, `ZOE_FUSION_STRONG_VOICE=0.90`, `ZOE_FUSION_STRONG_FACE=0.60`, `ZOE_FUSION_AGREE_VOICE=0.82`, `ZOE_FUSION_AGREE_FACE=0.45` (face thresholds are ArcFace-space cosine; calibrate from shadow data), plus `ZOE_IDENTITY_CONF_STEPUP=0.85`, `ZOE_IDENTITY_DECAY_HALFLIFE_S`, `ZOE_PRESENCE_SOURCES=ha,camera`.
- Rules (implemented in the merge at `voice_tts.py:2671-2703` + `panel_auth.py` step-up path ~:768):
  1. **Soft attribution unchanged**: voice `identified_user_id` keeps its precedence slot; face alone fills it only when voice is None and face ≥ STRONG. Preserve P-F6 exactly (soft signals never out-rank a bound panel; guest sentinel never persists).
  2. **Step-up**: session `confidence ≥ ZOE_IDENTITY_CONF_STEPUP` (reachable only via agreeing voice+face evidence) AND (user == bound/recent panel user OR panel unbound) → treat as `panel_pin_result` success via the same `ses["bound_user_id"]` path; the grant lives as long as the confidence stays above threshold (decay/presence-break naturally expires it) with a hard cap TTL, audit log `identity_stepup method=voice+face conf=…`. Unlocks exactly what PIN unlocks — nothing more. **Two agreeing biometrics may never rebind a panel bound to a different user** — that still requires PIN (spoof containment: photo+replay can't take over someone else's panel).
  3. Disagreement (voice=A, face=B, both above threshold): no step-up, log conflict, fall back to bound user.
- PRs: 3.1 pure confidence/fusion model + table-driven ci_safe tests (agree/disagree/single-signal/decay/presence-break/bound-conflict/P-F6 matrices — pure functions, injectable clock); 3.2 wire-in to merge + panel_auth step-up + audit logging, flag-gated; 3.3 presence watchers (HA entity subscription server-side; camera occupancy tick in `zoe_face_id.py` on the Pi), flag `ZOE_PRESENCE_SOURCES`; 3.4 UI "verified — hear/see/presence" indicator (reuse `panel_pin_result`-style WS broadcast, touch `dist/touch/js/biometric-auth.js`).

**Verification**: fusion/decay/presence test matrix; live — enrolled user speaking with face visible opens sensitive scope without PIN; walk out and back in past the door sensor → confidence drops, PIN required again until re-confirmed; different bound user → PIN still required; single signal → PIN required.

## Enrollment UX (Phase 2/3)

Settings "Face Identity" card beside Voice Identity: consent text → capture 3 poses (panel UI → daemon control channel; verify what the daemon exposes locally, else relay via panel WS) → POST `/api/face/enroll` → list/delete profiles. Enrollment is explicit opt-in per person (W6 consent).

## Closeout (every phase)

- DOX pass: update `services/AGENTS.md` / `scripts/AGENTS.md` as touched; record the feature in `docs/knowledge/` (voice-pipeline or a new identity note); update W5/W6 status in `docs/architecture/samantha-evolution-plan.md`.
- Greptile loop to merge each small PR; squash-only; no validate.yml edits (marker-based CI).

## Later upgrade paths (noted, not scheduled)

- ECAPA-TDNN ONNX as a drop-in speaker-model upgrade over resemblyzer (≈5× better EER on benchmarks).
- Attention gating: personal content only when recognized face is looking at the screen (head pose already computed).
- ~$6 LD2410 mmWave sensor via ESPHome as a dedicated panel-room presence source (HA-bridged).
- Cross-modally-gated rolling template gallery (auto-refresh enrollment) — only after the base system is proven; strict admission gate + immutable anchor per the poisoning literature.

## Risks / open questions

- Face threshold calibration needs real shadow data (panel lighting, household) — do not skip shadow mode.
- `insightface` wheel availability on the Pi's OS/arch — fall back to raw ONNX + manual alignment.
- Enrollment capture relay (UI → Pi daemon) control channel needs verification.
- Spoofing: photo defeats face, replay defeats voice — mitigated by requiring BOTH + never-rebind + PIN fallback; document explicitly, don't oversell as "proof."
- Profile sync cache on the Pi holds biometric embeddings on disk — keep file perms tight (0600, user `pi`).
- Presence watchers: which HA door/motion entities actually cover the panel's room needs confirming with Jason; camera occupancy tick must not fight the wake-word capture for the device (single-owner camera lifecycle in `zoe_face_id.py`).
- Multi-person rooms: presence continuity assumes the identified person is the one still there; if occupancy count > 1, cap confidence below step-up so a second occupant can't inherit Jason's session.

## Critical files

- `scripts/setup/zoe_voice_daemon.py`, new `scripts/setup/zoe_face_id.py`, `scripts/setup/pi-requirements.txt`, `scripts/setup/deploy-pi-voice.sh`
- `services/zoe-data/routers/voice_tts.py` (merge :2671-2703, enroll/identify :5060-5279), new `services/zoe-data/routers/face_id.py`, `services/zoe-data/routers/panel_auth.py` (:768 step-up)
- `services/zoe-data/alembic/versions/` (consent + face_profiles migrations)
- new `services/zoe-data/identity_presence.py`, `services/zoe-data/routers/ha_control.py` (read-only reuse), HA config (operator: companion app, device_tracker, person entities)
- `services/zoe-ui/dist/touch/settings.html`, `dist/touch/js/biometric-auth.js`
- `docs/architecture/samantha-evolution-plan.md` (W5/W6 status)
