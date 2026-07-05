# ADR: Ambient Voice — framework for always-there voice + phone

## Status

**Proposed — decision pending a time-boxed Pipecat spike** (see
[`docs/architecture/ambient-voice-pipecat-spike.md`](../architecture/ambient-voice-pipecat-spike.md)).
This records the landscape research and the *provisional* choice so the spike has a
clear hypothesis to prove or kill. Nothing live changes until the spike lands.

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

## Links

- Spike plan: [`ambient-voice-pipecat-spike.md`](../architecture/ambient-voice-pipecat-spike.md)
- North star: [`docs/VISION.md`](../VISION.md) (pillar 2)
- Current agent: `services/zoe-data/routers/voice_livekit.py`, `services/zoe-data/livekit_aiortc.py`
