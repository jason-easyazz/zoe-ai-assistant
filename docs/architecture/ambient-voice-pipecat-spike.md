---
type: spike-plan
status: decided (2026-07-06) — feasibility half run 2026-07-05; verdict converted to a decision in ambient-voice-migration-plan.md (NO-GO Pipecat-now / GO fallback); remaining live measurements fold into that plan's Phase A3
owner: ambient-voice (VISION pillar 2)
decides: docs/adr/ADR-ambient-voice-framework.md
---

> **Results so far** live in the ADR's [`## Spike results`](../adr/ADR-ambient-voice-framework.md)
> section. Feasibility validated (installs light, reuses Jetson torch, Kokoro+Gemma+Smart
> Turn+SmallWebRTC present) with three frictions (Moonshine not a native service; numpy-2.x
> vs Jetson-torch ABI; a memory-saturated box). Verdict so far: **partial GO, leaning toward
> the "borrow Smart Turn + build barge-in on LiveKit" fallback.** M1/M3/M4/M6 still need a
> controlled window + a human mic and were **not** run against the live brain.

# Spike — Pipecat on the Jetson with Zoe's rocks

**Goal:** prove or kill the [ADR](../adr/ADR-ambient-voice-framework.md)'s provisional
pick (Pipecat for the ambient agent layer) with **measured data on real hardware**, not
vibes. Stand up a minimal Pipecat voice loop on the Orin wiring **our existing Moonshine
STT + Kokoro TTS + Gemma brain**, and compare **barge-in, turn-detection, latency, and
RAM** against the current custom LiveKit agent.

Runs like every Zoe increment: **lab-prove before prod**, behind isolation, non-destructive.

## Non-goals (explicit — do NOT do these in the spike)
- **No phone / SIP** — telephony is a separate ADR/increment.
- **No Wyoming satellites** — room hardware is a separate increment.
- **No changes to the live voice path** — `voice_livekit.py`, the LiveKit service, and the
  rocks' configs are **untouched**. The spike runs in parallel on a spare port.
- **No replacing the brain** — Gemma stays; this is turn-based (STT→LLM→TTS), not S2S.
- Not production-hardening; not multi-user; not the full ambient UX. Minimum to measure.

## Forbidden
- Editing `services/zoe-data/routers/voice_livekit.py`, `livekit_aiortc.py`, or restarting
  `zoe-data`/`flue-zoe-brain`/the LiveKit container.
- Installing Pipecat into the zoe-data runtime env (use an **isolated venv**).
- Touching `ZOE_KOKORO_*`, Moonshine, or `llama-server` configuration.
- Leaving the spike process or its port running after measurement (clean up).

## Environment (isolated, parallel)
- **Isolated venv** at `~/.spikes/pipecat-voice/` (never pollute zoe-data deps).
- **Pin a known-good Pipecat version** — the `SmallWebRTCTransport` audio regression
  (~v0.0.62) is a documented rough edge; pick a release verified clean and record it.
- Reuse the running rocks (read-only): Gemma at `http://127.0.0.1:11434/v1` (OpenAI-compat),
  Kokoro ONNX at `/home/zoe/models/kokoro-v1.0.onnx` (+ `voices-v1.0.bin`), Moonshine as
  Pipecat's `moonshine` STT extra.
- Spare port (e.g. `:8788`) for the SmallWebRTC signalling + a tiny static test page.

## Steps
1. **Isolate + install.** Create the venv; `pip install "pipecat-ai[moonshine,kokoro,silero,webrtc,local-smart-turn]==<pinned>"` (the on-device Smart Turn v3 extra is `local-smart-turn`, not `turn`; **re-confirm every extra name against the pinned version's PyPI metadata** before running — extras get renamed between releases). Confirm ARM64 wheels resolve on the Orin.
2. **Wire the pipeline** (a ~100-line script): `SmallWebRTCTransport` (browser mic in / audio out) → **Moonshine STT** → **Gemma LLM** (OpenAI-compat @ :11434) → **Kokoro TTS** (point at our ONNX) → transport out. Enable **Smart Turn v3** turn detection and **interruption** (barge-in) in the pipeline config.
3. **Serve a one-file test page** for SmallWebRTC and talk to it from a laptop/phone browser on the LAN.
4. **Drive the measurements** below (record every number in a results table).
5. **Write up** the results as a short section appended to the ADR (mirroring the
   graphiti/hindsight bake-off ADRs), and flip the ADR **Status** to Accepted or Rejected.
6. **Tear down** — stop the process, free the port, leave a note; keep the venv only if
   proceeding.

## Measurements (the decision data)
| # | Metric | How | Target / compare |
|---|---|---|---|
| M1 | **Barge-in** | interrupt Zoe mid-sentence 10×; does TTS stop promptly + a new turn start? | works cleanly; time-to-stop < ~300 ms. Current LiveKit agent: **cannot** (drops audio) |
| M2 | **Turn detection** | 15 utterances incl. mid-thought pauses; does it wait vs cut off? | Smart Turn beats current energy-RMS VAD on false-cutoffs + false-waits |
| M3 | **Latency** (warm) | end-of-speech → first TTS audio out, median of 10 | ≤ current voice path (~1.8 s brain median; aim similar or better) |
| M4 | **RAM** | incremental RSS of the Pipecat process, steady state | fits the ~1.8 GB-free budget alongside Gemma; ideally < LiveKit's ~560 MB media server |
| M5 | **Rock fidelity** | Moonshine transcription accuracy + Kokoro voice on a fixed script | unchanged from today's pipeline |
| M6 | **Audio quality/stability** | 5-min continuous session on `SmallWebRTC` | clean, no robotic/choppy artefacts on the pinned version |

## Success criteria (GO — accept the ADR, plan the migration)
All of: **M1** barge-in clean · **M2** Smart Turn ≥ energy VAD · **M3** latency ≤ current ·
**M4** RAM within budget · **M5** rocks intact · **M6** audio clean.

## Kill criteria (NO-GO — fall back to "borrow the piece" on LiveKit)
Any of: SmallWebRTC audio unusable on Tegra (M6) · RAM blows the budget (M4) · latency
materially worse (M3) · Moonshine/Kokoro can't be wired natively (M5). Fallback:
extract Smart Turn v3 as a standalone model and **hand-build barge-in into the existing
LiveKit agent** — keeping LiveKit, gaining the two missing pieces the harder way.

## Timebox
1–2 focused sessions. If it isn't showing GO/NO-GO signal by then, that itself is a
signal (integration friction on this hardware) that favours the fallback.

## Deliverable
A results table + verdict appended to the ADR, and this file marked `status: done` with
the outcome. Then either an **ambient-voice migration plan** (GO) or a **barge-in-on-
LiveKit increment** (NO-GO) — each its own PR, flag-gated, lab-proven, as usual.
