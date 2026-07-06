# Zoe → Samantha: the Evolution Plan (post-memory frontier)

> **Single source of truth for the next evolution wave.** Memory (pillar 1) is
> delivered and measured (buildplan §7: 40/40 on the live Flue brain). This plan
> sequences everything *after* memory — ears, voice-first initiative, constant
> presence — grounded in the 2026-07-06 capability audit, the ambient-voice ADR's
> spike verdict, and the memory-pressure profile of the live host. A fresh session
> reads this top-to-bottom, does the **§7 NEXT ACTION**, updates **§6**, repeats.
>
> Companion docs: [`docs/VISION.md`](../VISION.md) (why) ·
> [`zoe-memory-samantha-buildplan.md`](zoe-memory-samantha-buildplan.md) (pillar 1, done) ·
> [`ADR-ambient-voice-framework.md`](../adr/ADR-ambient-voice-framework.md) (voice decision) ·
> [`docs/knowledge/memory-pressure-profile.md`](../knowledge/memory-pressure-profile.md) (RAM facts).

## 0. Operating rules (inherited, non-negotiable)

- **Rocks fixed** — Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro. Optimise around, never swap.
- **Every behaviour ships behind a flag, default OFF. Lab-prove before prod.**
- **Replay-gate every voice change** against `~/.zoe-voice-samples` via
  `scripts/maintenance/voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`.
- **One increment = one PR**, driven to merge (Greptile threads resolved, squash, auto-merge).
- Work in a dev worktree, never the live checkout. Deploy via `deploy_live.sh` only.
- **The model is the voice, not the initiative.** The 4B brain measured ~1/5 on unprompted
  surfacing; deterministic scaffolds (schedulers, triggers, presence checks) decide *when*
  Zoe acts, Gemma phrases *what she says*. Every workstream below follows this pattern.
- **Don't resurrect** the dead idle-consolidation path as "the" memory path, graphify,
  Pipecat-as-migration (see §2), or anything CANONICAL lists as retired.

## 1. The Samantha spec and where Zoe stands (audited 2026-07-06)

Samantha is seven fused capabilities. The scorecard, strongest → weakest:

| # | Capability | State | Evidence |
|---|---|---|---|
| 4 | **Knows the thread of your life** (memory) | 🟢 delivered | Buildplan §6/§7: 4/4 criteria, 40/40 live eval; relationship graph merged (dark flags); emotional thread live |
| 5 | **Initiates** (proactivity) | 🟡 built, wrong medium | 9 triggers + quiet hours + LLM composition (`proactive/engine.py`), but every path terminates in web-push — Zoe never *speaks* first |
| 1+2 | **Always there + full-duplex conversation** | 🟡 walkie-talkie | 3 voice lanes; LiveKit lane drops incoming audio during PROCESSING/COOLDOWN (no barge-in exactly where it matters); energy-RMS endpointing; "let's talk" opener (#1049) opens into a half-duplex room |
| 3 | **Hears *how* you say things** (mood from voice) | 🔴 absent | Zero prosody/affect analysis anywhere; `pyannote.audio` in requirements, unimported; everything emotional is inferred from text |
| — | **Knows *who* is speaking** (identity) | 🔴 built, dormant | Speaker ID end-to-end (enroll endpoint, resemblyzer 256-dim, cosine match, daemon integration) but default OFF, no enrolled profiles; fallback chain is badge-checking, not recognition |
| 6 | **Everywhere** (presence surfaces) | 🔴 one wall of one room | Touch panel + web + lab Telegram bot; no satellites, no SIP, no surface that leaves the house |
| 7 | **Grows** (self-evolution) | 🟡 gated, never closed | Proposal contract + weekly digest exist; the loop has never run end-to-end to a merged PR Zoe benefits from |

**Cross-cutting blocker: the box is full.** Swap 11.8–23 GB deep, 1.1–2.6 GB free
(profile snapshot). Every remaining capability is a RAM consumer. RAM reclamation is a
**named workstream (W3)** with the same status as a feature.

## 2. DO-NOT-REPEAT (evidenced traps)

- **No Pipecat migration as the default path.** The spike (ADR, 2026-07-05) found Moonshine
  is NOT native, a numpy-2/Jetson-torch ABI conflict, and no RAM headroom to run parallel.
  The ADR's own verdict shifted to **borrow Smart Turn v3 + hand-build barge-in on the
  existing LiveKit agent**. Only revisit Pipecat if W1 proves insufficient.
- **No in-turn model spontaneity bets.** Measured ~1/5. Deterministic scaffolds + Gemma phrasing.
- **No heavy new model without a RAM check first.** The Pipecat spike died on this; the
  Kokoro "2.3 GB" scare was the non-default PyTorch backend (ONNX default is ~600 MB) —
  measure, don't assume, in both directions.
- **No unattributed ambient capture in prod.** The `ambient_memory` insert path currently
  stores no `speaker_id` (`routers/voice_tts.py` ambient insert); attribution + per-speaker
  consent are prerequisites, not follow-ups (W6 depends on W5).
- **No new always-on daemons inside zoe-data.** It hit 3.16 GB VmHWM within 15 min of a
  restart; new audio/model work runs in sidecars or subprocesses (profile, "what loads
  memory inside zoe-data").

## 3. Workstreams (each = flag + gate + PR chain)

### W0 — Verify the capture pipeline is alive (verify-first; blocks nothing but trusts everything)

The buildplan's own open question: newest `chat_messages` row of *any* kind is 2026-06-22
— before the 1b deploy — so owner-stamping is unconfirmed on live organic traffic and
capture itself may have regressed. Everything above (consolidation, recall, portraits,
emotional thread) stands on capture.

- **Steps:** (1) one organic authenticated voice turn on the live panel; (2) confirm a new
  `chat_messages` row with `metadata.user_id`; (3) confirm the next idle-consolidation sweep
  picks it up (`MEMORY_IDLE_CONSOLIDATE` log line naming the user). If any step fails, root-cause
  before anything else in this plan (suspects per buildplan §6: wake-bleed empty transcripts,
  a persistence regression ~2026-06-24).
- Also watch `scripts/maintenance/check_emotional_thread.py` for the first real
  `emotional_moment` row (buildplan §7's "not a code task").
- **Effort:** hours. **Risk:** none (read-only + one turn). **DoD:** the positive-control
  turn documented in the buildplan §6, checked off there.

### W1 — Conversation-grade voice: barge-in + Smart Turn v3 + streamed TTS on the existing LiveKit agent

The highest felt-difference item: converts "let's talk" from walkie-talkie to conversation.
Executes the ADR's shifted verdict — **borrow the piece, keep LiveKit**.

- **Grounding:** `voice_livekit.py` drops incoming frames during PROCESSING/COOLDOWN (ADR gap 1);
  endpointing is energy-RMS `_rms()` (gap 2); LiveKit TTS is one whole-utterance `synthesize`
  call while `/ws/voice/` streams per-sentence (`_stream_kokoro_sentence_wavs`,
  `_extract_complete_sentences` already exist in `voice_tts.py`).
- **Smart Turn v3 facts (researched):** standalone open model ([repo](https://github.com/pipecat-ai/smart-turn),
  [weights](https://huggingface.co/pipecat-ai/smart-turn-v3)), ~8M params on a Whisper-Tiny
  trunk, int8 ONNX, **12–60 ms CPU inference** ([Daily announcement](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)) —
  RAM-cheap enough to live beside the existing agent, no Pipecat dependency.
- **Steps (each its own PR, flag `ZOE_VOICE_BARGE_IN` / `ZOE_SMART_TURN` default OFF):**
  1. Stop dropping audio during PROCESSING/COOLDOWN: keep the mic path live, run VAD on
     incoming frames, and on confirmed speech cancel TTS playback + the in-flight turn
     (the Pi daemon's Silero barge-in is the in-house reference implementation).
  2. Smart Turn v3 ONNX as the endpointer (replacing raw RMS-silence timeout) — score
     "has the speaker finished?" at candidate end-of-speech; fall back to RMS on model failure.
  3. Sentence-streamed TTS in the LiveKit lane: reuse `_stream_kokoro_sentence_wavs` so
     conversation mode gets first-audio at first-sentence, like `/ws/voice/` already does.
  4. The ADR's deferred live measurements (barge-in quality M1, latency M3, RAM M4) run as
     the lab-proof session — same script serves either path.
- **Gates:** replay-gate (mandatory, voice path); a live barge-in session with a human at a
  mic; RAM delta measured before/after. **DoD:** you can talk over Zoe mid-reply and she
  stops and listens; turn detection no longer false-triggers on pauses; first-audio latency
  in conversation mode ≤ the `/ws/voice/` lane's.
- **Effort:** the largest engineering item in the plan (1–2 weeks of PRs). **Risk:** medium —
  live voice path; everything flag-gated + replay-gated.

### W2 — Proactive → spoken: Zoe speaks first (highest leverage-per-effort)

The proactive engine is real (nine triggers incl. morning brief, emotional follow-up #1031,
quiet hours, LLM composition) but delivers only web-push. `panel_announce` already exists as
an MCP tool (`mcp_server.py`, queued panel action + TTS on the panel). Wire them together.

- **Steps (flag `ZOE_PROACTIVE_SPOKEN`, default OFF):**
  1. A **presence check** primitive: "is someone plausibly near the panel?" — recent panel
     touch/voice activity, an active voice session, or motion if HA exposes it. No presence
     signal → fall back to push (today's behaviour). This is what makes speaking-first
     appropriate rather than creepy.
  2. A spoken-delivery adapter in `proactive/` that routes a composed message to
     `panel_announce` when presence passes and quiet hours don't veto; push remains the
     fallback + receipt.
  3. Start with **exactly one trigger**: the morning brief. Watch a week. Then extend
     per-trigger (each trigger opts in; reminders next, emotional follow-up last —
     it's the most sensitive to tone and timing).
  4. Once W5 lands: suppress spoken delivery when the presence signal identifies a
     *different* enrolled speaker (don't read Jason's brief to a guest).
- **Gates:** quiet hours honoured (already in engine); presence-check false-positive rate
  observed before widening; per-trigger enable. **DoD:** Zoe says the morning brief aloud,
  unprompted, when Jason is near the panel — once a day, reliably, never at night, never
  to an empty room.
- **Effort:** days. **Risk:** low (additive adapter; push fallback unchanged).

### W3 — RAM reclamation: free ≥2 GB (the enabler; a feature-status workstream)

Facts and sizes are already measured in
[`memory-pressure-profile.md`](../knowledge/memory-pressure-profile.md) — this workstream
executes its candidate list, cheapest-and-safest first:

1. **ccd-cli fleet hygiene** — ~3.6 GB swap + ~1 GB RSS in 19 host-side Claude Code session
   processes (stale `--resume` duplicates). Operational cleanup, zero repo change, the
   largest single reclaim. Recurring: add a stale-session sweep to host maintenance.
2. **Stop embedding audit rows** (`memory_service.py` audit upsert embeds summaries that are
   only ever metadata-filtered). Small RAM, real CPU-per-mutation, low risk.
3. **Generalize the LiveKit on-demand reap** (`voice_livekit.py` docker start/stop + idle
   monitor) to homeassistant (370 MB swap) / music-assistant (325 MB swap) where a usage
   signal exists.
4. **zram rebalance** — 3.9 GB of physical RAM currently stores compressed swap; shift cold
   pages toward the NVMe swapfile. Config-level, host-wide blast radius, measure-first.
5. **Fence the engineering harness/Multica out of zoe-data** (Wave 4 of the
   [tech-debt plan](tech-debt-remediation-plan.md)) — peak isolation + O(100s MB); medium
   risk, replay-gated. Coordinate with the Flue-convergence retirement inventory rather
   than doing it twice.
6. **Do NOT touch llama-server's swapped 4.14 GB** — evidence says cold and mostly harmless;
   any change is to the rock's launch flags (highest risk, lowest need).
- **Budget:** W1 needs ~tens of MB (Smart Turn int8); W4 needs 100–300 MB resident during
  utterances; always-on LiveKit (if W1 makes conversation mode habitual) wants its ~560 MB
  back. Target: **≥2 GB reclaimed before W4/W6 ship.**
- **DoD:** post-reclaim profile (same methodology, new OKF record) showing ≥2 GB freed and
  swap < 6 GB at steady state.

### W4 — Prosody emotion sensing: give Zoe ears for *how* (the missing Samantha sense)

Everything emotional today is text-inferred. The audio is already in hand (STT path), the
substrate exists (`emotional_moment` with valence + intensity), and the recall/follow-up
machinery it feeds (emotional gate + pin, morning brief, emotional follow-up trigger) is
built and waiting. Add a small speech-emotion-recognition (SER) model scoring
valence/arousal per utterance. Additive, flag-gated, rocks untouched.

- **Model shortlist (researched; pick by measured RAM/latency on the Orin, in this order):**
  1. **Wav2Small** — distilled to 72K params (teacher-distilled from a wav2vec2 dimensional
     teacher; MobileNetV4-S variant 3.12M params, valence CCC 0.42 ≈ an 87.9M wav2vec2)
     ([paper](https://arxiv.org/pdf/2408.13920)) — the RAM-frugal default.
  2. **emotion2vec** (~19M params) — better quality, still edge-viable.
  3. **audeering wav2vec2 dimensional (MSP-Podcast)** — the quality ceiling / labelling
     reference; likely too heavy resident, usable as the lab judge for calibrating 1–2.
- **Steps (flag `ZOE_PROSODY_EMOTION`, default OFF):**
  1. RAM/latency bake-off of shortlist models as ONNX on the box (lab, isolated venv) —
     kill criterion: >300 MB resident or >150 ms per utterance.
  2. Scoring hook on the existing voice path **in a subprocess or the Kokoro-sidecar
     pattern** (§2: nothing new in-process in zoe-data), emitting valence/arousal per
     utterance into turn metadata.
  3. Fusion policy: prosody signal *corroborates or flags* — high-arousal/low-valence voice
     + neutral text ⇒ store an `emotional_moment` candidate through the **existing** write
     gate (never bypass admission); never contradict explicit text.
  4. Lab-prove on the voice corpus + acted samples; watch a week of live scores
     (log-only mode) before letting it write.
- **Gates:** W3 first (RAM); replay-gate (it touches the voice path); log-only observation
  window before writes. **DoD:** a flat "I'm fine" in a low-valence voice produces an
  emotional signal the morning brief / follow-up trigger can act on; zero hot-path latency
  regression.
- **Privacy:** on-device only, scores stored not audio (corpus saving already governed by
  `ZOE_VOICE_SAVE_AUDIO`).

### W5 — Speaker ID: enroll and switch on (the charter promise; mostly operator work)

"Every turn pivots on who is speaking" is the charter's first product rule; the mechanism
(enroll endpoint, resemblyzer 256-dim embeddings, cosine threshold
`ZOE_SPEAKER_ID_THRESHOLD=0.82`, daemon integration, settings-page listing) is built and
dormant — default OFF, zero enrolled profiles.

- **Steps:** (1) enrollment session for Jason (+ family who consent) via the existing
  endpoint/settings page; (2) enable in **shadow mode** — identify + log, don't act — for a
  week; measure match/false-accept rates against the fallback chain; (3) tune threshold;
  (4) let identification win over panel-binding for user resolution; PIN challenge stays as
  the escalation for sensitive scopes (recognition greets, badge-check gates).
- **Gates:** shadow-mode numbers before acting on identity; replay-gate.
  **DoD:** Zoe addresses Jason by name from voice alone on the panel; an unenrolled voice
  cleanly falls back to guest.
- **Effort:** days (mostly product/operator). **Risk:** low in shadow mode.

### W6 — Attributed ambient capture: from "assistant with a good log" to "was in the room"

Samantha reads the room without being addressed. The scaffolding exists
(`ambient_memory` table with FTS + a `speaker_id` index, capture endpoint in `voice_tts.py`)
but is flag-OFF and — critically — the insert path stores **no speaker attribution**.

- **Hard prerequisites:** W5 (attribution + per-speaker consent), W3 (RAM for ambient STT),
  W4 helps (emotional context on ambient snippets). Do not ship before them (§2).
- **Steps (flag stays OFF until consent design is operator-approved):**
  1. Thread `speaker_id` through the ambient insert path (schema is ready).
  2. **Consent model first-class:** only enrolled, explicitly-opted-in speakers are ever
     attributed; unknown voices are discarded (not stored unattributed); a per-room/per-panel
     kill switch; retention window on `ambient_memory` rows.
  3. Ambient→memory promotion goes through the existing admission gate + consolidation
     (ambient rows are raw signal, never direct facts).
  4. Recall integration: ambient context is scoped (existing visibility/scope primitives)
     and cited distinctly (`[ambient]`) so provenance is always visible.
- **DoD:** something said near (not to) the panel by an enrolled, consenting speaker can be
  recalled later, attributed, and a stranger's voice leaves no trace.
- **Risk:** highest in the plan (privacy) — which is why it is sequenced sixth.

### W7 — Close the self-evolution loop once (end-to-end, on something trivial)

The pipeline exists (proposal contract `zoe-evolution-proposal-contract.md`, weekly digest
trigger, the PR harness) but has never run end-to-end. Close it once: Zoe proposes a small,
real improvement (e.g. a prompt-doctrine tweak or a new replay-corpus entry) → proposal →
digest → harness picks it up → PR → Greptile → human-gated merge → deployed → Zoe's next
digest notes the improvement landed.

- **DoD:** one merged PR whose origin is a Zoe-generated proposal, with the trail
  documented; the friction points found become the backlog for making it routine.
  Human gate stays (by design).

### W8 — A surface that leaves the house (named, sequenced last, not forgotten)

Samantha's intimacy is substantially the earpiece. Full satellites (Wyoming) and phone
(SIP) are later, separable increments per the ADR. The cheap first step: **Telegram voice
notes** on the existing lab bot (#870) — inbound voice note → Moonshine → brain → Kokoro
voice reply — giving Zoe a pocket presence with zero new hardware; PWA background voice
second; Wyoming/SIP after W3 proves sustained headroom.

## 4. Sequencing & dependencies

```
W0 (verify capture)  ──────────────────────────────► trust for everything
W1 (barge-in/turn/stream) ── independent, start now ─► pillar 1+2
W2 (proactive spoken) ────── independent, start now ─► pillar 5   (W5 refines it)
W3 (RAM) ── start now, cheap items first ─┬─► gates W4, W6, always-on voice
W4 (prosody SER) ◄────────────────────────┤          ► pillar 3
W5 (speaker ID on) ── after W1 lands ─────┤          ► identity
W6 (ambient capture) ◄── needs W3+W4+W5 ──┘          ► pillar 1 (presence depth)
W7 (evolution loop) ── anytime, low urgency ─────────► pillar 7
W8 (leaves the house) ── after W3 ───────────────────► pillar 6
```

Parallelizable from day one: **W0, W1, W2, W3** (four independent lanes). W2 + W1 together
are the biggest felt change per week of work.

## 5. Acceptance bar — extend the Samantha tests

Extend `services/zoe-core/test/test_samantha_acceptance.py` (and live gates where CI can't
reach) with one criterion per workstream: **interruptibility** (talk over Zoe → she yields
within N ms), **speaks-first** (morning brief delivered aloud under presence, suppressed
without), **hears-mood** (flat-voice fixture → emotional signal), **knows-who** (enrolled
voice → correct user; unknown → guest), **attributed-ambient** (consented speaker recalled,
stranger absent), **evolved-once** (the W7 trail exists). Latency criteria stay: no hot-path
regression, ever (replay harness is the enforcement).

## 6. Status / where am I

- [ ] **W0** capture positive-control turn (buildplan §6 carry-over) — NOT STARTED
- [ ] **W1.1** barge-in on LiveKit agent — NOT STARTED
- [ ] **W1.2** Smart Turn v3 endpointer — NOT STARTED
- [ ] **W1.3** sentence-streamed TTS in conversation mode — NOT STARTED
- [ ] **W1.4** live barge-in/latency/RAM measurement session (ADR M1/M3/M4) — NOT STARTED
- [ ] **W2.1** presence-check primitive — NOT STARTED
- [ ] **W2.2** spoken-delivery adapter (morning brief only) — NOT STARTED
- [ ] **W3.1** ccd-cli fleet cleanup (operational) — NOT STARTED
- [ ] **W3.2** audit-row embedding stop — NOT STARTED
- [ ] **W3.3** reap generalization (HA / music-assistant) — NOT STARTED
- [ ] **W3.4** zram rebalance (measure-first) — NOT STARTED
- [ ] **W3.5** harness fence-out (with tech-debt Wave 4) — NOT STARTED
- [ ] **W4.1** SER bake-off (Wav2Small / emotion2vec) — NOT STARTED
- [ ] **W4.2–4** scoring hook + fusion + lab-proof — NOT STARTED (gated on W3)
- [ ] **W5** speaker-ID enrollment + shadow mode + enable — NOT STARTED
- [ ] **W6** attributed ambient capture — NOT STARTED (gated on W3+W4+W5 + consent design)
- [ ] **W7** self-evolution loop closed once — NOT STARTED
- [ ] **W8** Telegram voice notes — NOT STARTED (after W3)

## 7. NEXT ACTION (always exactly one)

→ **W0**: run the capture positive-control (one organic authenticated voice turn → new
`chat_messages` row with `metadata.user_id` → consolidation sweep picks it up). In parallel,
kick off **W3.1** (ccd-cli fleet cleanup — operational, zero risk) and start the **W1.1**
barge-in branch. Update this section when W0 resolves.
