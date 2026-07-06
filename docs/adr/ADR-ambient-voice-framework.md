# ADR: Ambient Voice — framework for always-there voice + phone

## Status

**Decided 2026-07-06 — fallback-first.** The GO/NO-GO call + executable plan live in
[`docs/architecture/ambient-voice-migration-plan.md`](../architecture/ambient-voice-migration-plan.md):
NO-GO on a full Pipecat migration now; GO on the "borrow the piece" fallback (Smart Turn
v3 + hand-built barge-in on the existing LiveKit agent); Pipecat parked with explicit
re-open triggers. Spike feasibility half ran 2026-07-05; live/loaded measurements
(M1/M3/M4/M6) close during the plan's Phase A3 window. See
[`## Spike results`](#spike-results-2026-07-05) below and
[`docs/architecture/ambient-voice-pipecat-spike.md`](../architecture/ambient-voice-pipecat-spike.md).
The frictions found **narrow the recommendation** (see Verdict). Nothing live changed.

## Context

VISION pillar 2 — an **always-there, real-time presence** — is the next Samantha
frontier after memory (pillar 1, delivered). "Ambient" means: continuous, wake-free
listening; natural turn-taking; **interruptible** (barge-in); low latency; and,
adjacent, **phone calls** (Zoe answering/placing calls).

### What's live today
- A **custom LiveKit agent** already exists and is wired at boot
  (`services/zoe-data/routers/voice_livekit.py`, started in `main.py` via
  `start_livekit_agent()`). It joins a `zoe-voice` room, runs **server-side energy
  VAD**, and is **always-listening — no button press**, looping
  IDLE→LISTENING→PROCESSING→COOLDOWN→IDLE.
- On Jetson Tegra ARM64 the **native LiveKit SDK cannot initialise a PeerConnection**,
  so the agent runs on **aiortc** (pure-Python WebRTC, `livekit_aiortc.py`). This means
  the LiveKit **Agents framework** (its turn-detection / adaptive-interruption models)
  is **not usable** on this hardware — the custom agent reimplements a subset by hand.
- The LiveKit **media server runs on-demand** (`ZOE_LIVEKIT_ONDEMAND=true`), reaped
  after idle to free ~560 MB — because RAM is tight.

### The gaps to Samantha-grade (grounded in the code)
1. **No barge-in — half-duplex.** `voice_livekit.py` **drops incoming audio while
   PROCESSING/COOLDOWN** ("ignore incoming frames") — you cannot interrupt Zoe. This is
   the single biggest gap between "walkie-talkie" and "conversation."
2. **Crude endpointing** — energy-RMS VAD (`_rms()`), not a turn-detection model;
   false-triggers on background speech/TV.
3. **No phone calls** — no SIP anywhere.
4. **On-demand vs always-on** — RAM tension for a truly always-there device.

### Non-negotiable constraints (from VISION)
- **Local / private / on-device** — Jetson Orin NX 16 GB ARM64; nothing leaves the box.
  (Rules out all cloud voice-agent platforms.)
- **The rocks are fixed** — Gemma 4 E4B brain, **Moonshine v2 STT**, **Kokoro TTS**.
  Optimise *around* them; never swap. (Rules out end-to-end speech-to-speech models that
  *replace* the brain.)
- **Hot path fast**, RAM-frugal (~1.8 GB free after Gemma ~4.9 GB).

## Options considered

Deep = examined in detail; surveyed = read enough to rule in/out.

| Category | Option | Depth | Verdict for us |
|---|---|---|---|
| Agent/pipeline | **Pipecat** | deep | **Provisional pick.** Native **Moonshine + Kokoro** services (same ONNX models we run); Gemma via OpenAI-compat; **Smart Turn v3** + automatic **barge-in**; ARM64/Jetson-proven; lighter (`SmallWebRTCTransport`, no media server); transport-agnostic |
| | LiveKit Agents | deep | Incumbent transport; great SIP. But native SDK fails on Tegra → aiortc → **Agents framework (turn/interruption) unusable here** → we hand-build the ambient smarts |
| | TEN Framework | deep | **Rejected** — C++-fast, but requires Docker **+ an Agora (cloud) account** + Go server; cloud dep breaks local/private |
| | Vocode / Dograh / RoomKit | surveyed | No advantage over Pipecat for us |
| Full-duplex S2S *(paradigm shift)* | Moshi / Step-Audio R1.1 / Ultravox | deep | Most *naturally* conversational (true overlap/interruption) — but **replace the Gemma brain rock** and need **≥24 GB VRAM**. **Watch as the future paradigm; not viable on a 16 GB Jetson now** |
| Telephony / SIP | SIP trunk (Twilio/Telnyx/…) | — | **Unavoidable** — a carrier trunk is required to reach real phones |
| | LiveKit SIP / **Jambonz** / Asterisk+FreeSWITCH | deep/surveyed | Bridge choices; decide when we build phone. Jambonz (MIT, self-hosted) or LiveKit SIP (already have it) are cleanest |
| Room hardware | **Wyoming satellites** (ESP32 $13–45, Voice PE $59) | deep | We already run `wyoming-piper`. Cheap, local, always-listening mics-in-rooms — the *presence* layer, orthogonal to the framework |
| Cloud platforms | Vapi / Retell / Bland / OpenAI Realtime / Gemini Live | named | **Rejected** — cloud; breaks local/private |

## Decision (provisional, pending spike)

1. **Ambient agent layer → Pipecat.** It is the only option that (a) keeps all three
   rocks as *native* plug-ins, (b) runs on Jetson ARM64, (c) delivers the two missing
   pieces — **barge-in** and a real **turn-detection model (Smart Turn v3)** — out of the
   box rather than hand-built, and (d) is *lighter* than running a LiveKit media server.
2. **Phone → a SIP trunk provider + a bridge** (LiveKit SIP, already present, or Jambonz).
   Decided when phone work starts — **not** part of the ambient spike.
3. **Room presence → Wyoming satellites**, feeding the chosen agent. A later, separable
   increment.
4. **Watch → full-duplex speech-to-speech** (Moshi / Step-Audio) as the paradigm that may
   eventually replace the STT→LLM→TTS pipeline — revisit when hardware/rocks allow.

**This decision is not final until the spike proves it.** If the spike fails (see kill
criteria), the fallback is **"borrow the piece"** (VISION principle 6): pull Smart Turn
v3 as a standalone model and **hand-build barge-in into the existing LiveKit agent**,
keeping LiveKit.

## Consequences

- **New dependency to adopt + operate** (Pipecat) — real DevOps/maintenance cost; the
  spike exists precisely to justify it before committing.
- **Rocks preserved** — Moonshine/Kokoro/Gemma stay; no north-star violation.
- **Non-destructive** — LiveKit stays exactly as-is until Pipecat is proven; the spike
  runs in parallel on a spare port, touching nothing live (respects "don't break what
  works").
- **Low lock-in** — Pipecat is transport-agnostic (can even run *on* LiveKit), so a wrong
  call is cheap to reverse.
- **Sequencing** — ambient (barge-in) first; phone (SIP) and room hardware (Wyoming) are
  later, separable increments, each its own ADR/PR.

## Spike results (2026-07-05)

Ran the **safe (feasibility) half** of the spike on the live Orin, non-destructively
(isolated venv `~/.spikes/pipecat-voice`, 267 MB, `--system-site-packages` to reuse the
Jetson-native CUDA torch). Live services stayed healthy throughout (zoe-data / flue-brain /
llama-server all 200). The **live/loaded** half was **deliberately NOT run** — see why below.

**What passed (GO signals):**
- **Install is light + ARM64-clean.** `pipecat-ai 0.0.108` (the *latest* — the earlier
  "v1.4/v1.5" note was wrong; it's still 0.0.x) installs by **reusing the system Jetson
  torch** (only pulls `torchaudio` + small deps — **no 2 GB torch download**). Isolated
  venv = 267 MB.
- **Key pieces import + are present:** `KokoroTTSService` (our TTS rock, native) ✓,
  `OpenAILLMService` (→ Gemma on `:11434`) ✓, **`LocalSmartTurnAnalyzerV3`** (the
  differentiating turn/barge-in model) ✓, `SmallWebRTCTransport` (lightweight, no media
  server) ✓, `SileroVADAnalyzer` ✓.

**Frictions found (these narrow the recommendation):**
1. **Moonshine STT is NOT a native Pipecat service** (corrects this ADR's research). 0.0.108
   ships `whisper`, `riva`, `nvidia`, `kokoro`, `piper`… but **no `moonshine`**. Pipecat's
   `whisper` service *does* match our live `.env` (`ZOE_WHISPER_MODEL=base.en`, CUDA), so
   STT is covered either by Whisper or a **custom Moonshine wrapper** — but "native rocks"
   is only fully true for **Kokoro**, not Moonshine.
2. **numpy-2.x vs Jetson-torch ABI conflict.** Pipecat 0.0.108 forces **numpy 2.x**; the
   Jetson's torch 2.8.0 is built against **numpy 1.x** → runtime warning *"module compiled
   with NumPy 1.x cannot run in NumPy 2.2.6, may crash."* Imports survive, but this is a
   real stability risk to resolve (version-pin or a numpy-1.x path) before trusting it
   under load.
3. **Env conflicts → must be its own process.** Pipecat pulls numpy 2.x + newer
   pydantic/httpx/websockets that conflict with zoe-data's pins — so it can **never** live
   in zoe-data's env; it's a separate isolated service (which is the right shape anyway).
4. **The box is memory-saturated — the binding constraint.** Measured **1.1–2.6 GB free**
   with swap already **23 GB deep** (llama-server holds 5.4 GB). There is **no headroom to
   run a Pipecat stack in parallel** with the live LiveKit path — a migration would have to
   **replace** the current voice path, not run alongside it.

**Not yet answered (needs a controlled window + a human at a mic):** barge-in quality (M1),
audio quality on `SmallWebRTC`/Tegra (M6), real end-to-end latency (M3), loaded RAM (M4).
Running a full loaded stack now would risk **OOM-killing the live brain** the operator is
actively using (Telegram), and barge-in/audio need a person speaking + interrupting — so
those measurements are **deferred to a maintenance window**, not forced against live.

### Verdict: PARTIAL GO — but the frictions tilt toward the fallback

Feasibility is real: it installs light, keeps Kokoro + Gemma + Smart Turn + a lightweight
transport. **But** three of the four gaps (Moonshine not native, numpy/torch ABI, and a
memory-saturated box with no room for parallel) mean a **full Pipecat migration is more
disruptive than the ADR first assumed** — it needs an isolated service, a custom Moonshine
wrapper (or a Whisper swap), an ABI fix, and a *replacement* of the LiveKit path.

The **"borrow the piece" fallback now looks stronger**: **Smart Turn v3 is a standalone,
importable model** — we can pull *just that* into the existing LiveKit agent and hand-build
barge-in there, getting the same two wins (real turn detection + interruption) with far
less disruption and no env/ABI/memory upheaval. **Recommendation shifts to: prototype
barge-in + Smart Turn on the existing LiveKit agent first**, and only pursue a full Pipecat
migration if that proves insufficient. Final call still needs the live barge-in session
(same script serves either path).

## Links

- Spike plan: [`ambient-voice-pipecat-spike.md`](../architecture/ambient-voice-pipecat-spike.md)
- North star: [`docs/VISION.md`](../VISION.md) (pillar 2)
- Current agent: `services/zoe-data/routers/voice_livekit.py`, `services/zoe-data/livekit_aiortc.py`
