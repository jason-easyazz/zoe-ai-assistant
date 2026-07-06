---
type: decision + migration-plan
status: active (2026-07-06) — decision made; Increment A (fallback path) is the executable packet
owner: ambient-voice (VISION pillar 2)
decides-from: docs/adr/ADR-ambient-voice-framework.md (spike results §), docs/architecture/ambient-voice-pipecat-spike.md
---

# Ambient voice — GO/NO-GO decision + migration plan

**The call: NO-GO on a full Pipecat migration now. GO on the ADR's named fallback —
borrow Smart Turn v3 as a standalone model and hand-build barge-in into the existing
LiveKit agent.** Pipecat is *parked, not rejected*: explicit re-open triggers below.

This is the deliverable the spike plan promised ("GO → migration plan; NO-GO → a
barge-in-on-LiveKit increment"). Nothing live changes from this document; every phase
below is flag-gated, lab-proven, and replay-gated before it touches prod.

---

## 1. The decision, grounded in what the spike actually measured

The spike ran its **feasibility half only** (2026-07-05, non-destructive, isolated venv);
its results live in the ADR's [`## Spike results`](../adr/ADR-ambient-voice-framework.md#spike-results-2026-07-05)
section. The spike's own verdict was *"PARTIAL GO — but the frictions tilt toward the
fallback"*. This document converts that tilt into the decision.

### What passed (why Pipecat stays parked, not rejected)

Quoting the spike results:

- *"Install is light + ARM64-clean"* — `pipecat-ai 0.0.108` in a 267 MB isolated venv,
  reusing the system Jetson torch (no 2 GB torch download).
- *"Key pieces import + are present: `KokoroTTSService` … ✓, `OpenAILLMService` (→ Gemma
  on `:11434`) ✓, `LocalSmartTurnAnalyzerV3` … ✓, `SmallWebRTCTransport` … ✓,
  `SileroVADAnalyzer` ✓."*

Feasibility is real. If the fallback path proves insufficient, Pipecat remains viable.

### Why NO-GO on the migration now (the measured frictions)

Three of the four frictions the spike found strike at the ADR's original case for
Pipecat ("native rocks, lighter, run it in parallel and compare"):

1. **The "native rocks" premise is only ⅓ true.** Spike: *"Moonshine STT is NOT a native
   Pipecat service … 0.0.108 ships `whisper`, `riva`, `nvidia`, `kokoro`, `piper`… but no
   `moonshine`."* The spike notes Pipecat's `whisper` service would match the old `.env` —
   but **that door is closed for us**: Moonshine v2 Medium is a rock (`docs/CANONICAL.md`),
   and faster-whisper was deliberately removed from the live voice path (#854). A Pipecat
   migration therefore requires a **custom Moonshine wrapper** — hand-built integration
   work, which was exactly the cost Pipecat was picked to avoid.
2. **Measured ABI risk.** Spike: *"Pipecat 0.0.108 forces numpy 2.x; the Jetson's torch
   2.8.0 is built against numpy 1.x → runtime warning 'module compiled with NumPy 1.x
   cannot run in NumPy 2.2.6, may crash.'"* Unresolved stability risk on the hot path.
3. **No headroom for the non-destructive path.** Spike: *"Measured 1.1–2.6 GB free with
   swap already 23 GB deep (llama-server holds 5.4 GB). There is no headroom to run a
   Pipecat stack in parallel with the live LiveKit path — a migration would have to
   **replace** the current voice path, not run alongside it."* This breaks the spike's own
   M4 premise ("fits the ~1.8 GB-free budget **alongside**") and collides with the kill
   criterion "RAM blows the budget (M4)". Replacement-not-parallel means a full migration
   carries cutover risk on the family's daily voice path — unjustified while a
   far-less-disruptive route to the same two wins exists.
4. (Also: *"Env conflicts → must be its own process"* — Pipecat can never live in
   zoe-data's env. Right shape anyway, but one more moving part.)

The fallback gets **the same two missing capabilities** — real turn detection and
barge-in — because *"Smart Turn v3 is a standalone, importable model"* (verified imported
in the spike), with **no framework adoption, no env/ABI upheaval, no voice-path
replacement**, and it keeps LiveKit — which also preserves **LiveKit SIP** as the cheap
bridge option for the later phone increment.

### Gates the spike did NOT measure (be honest about this)

The spike explicitly did not run M1 (barge-in quality), M3 (end-to-end latency), M4
(loaded RAM), or M6 (audio quality on SmallWebRTC/Tegra): *"Running a full loaded stack
now would risk OOM-killing the live brain … those measurements are deferred to a
maintenance window."* **No number exists for any of them — none is invented here.**

- The NO-GO does **not** rest on those gates; it rests on the measured frictions above.
- What closes them: the **live mic session in Phase A3 below** (a human at a mic in a
  maintenance window). The spike noted *"the same script serves either path"* — running
  it against the upgraded LiveKit agent yields M1/M2/M3 numbers for the fallback; M4/M6
  for Pipecat would only ever be measured if a re-open trigger fires (§4).

---

## 2. Increment A (GO) — Smart Turn v3 + barge-in on the existing LiveKit agent

The gaps in today's agent (`services/zoe-data/routers/voice_livekit.py`, aiortc
transport in `livekit_aiortc.py`), from the ADR:

- **Half-duplex:** the agent *"drops incoming audio while PROCESSING/COOLDOWN"* (the
  literal `# PROCESSING and COOLDOWN: ignore incoming frames (no buffering)` branch) —
  you cannot interrupt Zoe.
- **Crude endpointing:** energy-RMS VAD (`_rms()`) + a fixed 600 ms silence window; no
  turn model; false-triggers on background speech.

Non-negotiables for every phase: **rocks untouched** (Gemma 4 E4B+MTP, Moonshine v2
Medium, Kokoro); **live path untouched until its gate passes** (all new behaviour behind
flags, default OFF); **replay-gated** (`scripts/maintenance/voice_regression_probe.py`
against `~/.zoe-voice-samples`, plus `scripts/perf/measure_voice.py` /
`scripts/perf/measure_tts.py`, always under `flock /tmp/zoe-voice-harness.lock`);
said-vs-did and per-stage speed must not regress.

### Phase A0 — baseline + Smart Turn bench (lab only, no prod code paths)

- Capture the current agent's baseline: replay corpus pass/fail table + latency probe
  numbers, recorded in this doc's results table on completion.
- Stand up **Smart Turn v3 standalone** in a lab venv on the Orin; feed it recorded
  utterances (from `~/.zoe-voice-samples` + scripted mid-thought-pause clips).
- **Measure, don't assume:** (a) per-check inference latency on Orin CPU (it sits on the
  end-of-speech hot path — its cost adds directly to response latency); (b) steady-state
  RSS increment; (c) whether it imports cleanly under **zoe-data's pinned env**
  (numpy 1.x — the same ABI trap the spike hit). If it can't, it runs as a small
  subprocess/sidecar (the pattern already used for whisper/torch historically), and the
  RSS number is the sidecar's.
- **Gate A0:** model runs on ARM64; latency + RSS numbers recorded; a deliberate
  fit-check against the measured 1.1–2.6 GB free (§3). If Smart Turn v3 itself doesn't
  fit or is too slow on CPU, **stop** — that finding also invalidates the Pipecat path
  (which uses the same model) and sends us back to the drawing board on endpointing.

### Phase A1 — Smart Turn endpointing behind a flag

- `ZOE_TURN_DETECTOR=smart_turn|energy` (default `energy` = today's behaviour, bit-for-bit).
- Smart Turn replaces only the **LISTENING→PROCESSING commit decision** (is the user
  done, or mid-thought?); energy VAD stays as the cheap speech/silence pre-gate.
- **Gate A1:** replay corpus green (no said-vs-did regression); latency probe shows no
  per-stage regression with the flag ON in the lab; spike-M2-style check — 15 utterances
  incl. mid-thought pauses, Smart Turn must beat energy-VAD on false-cutoffs + false-waits.

### Phase A2 — barge-in behind a flag

- `ZOE_BARGE_IN_ENABLED=true|false` (default `false`).
- Change the half-duplex core: **keep consuming frames during PROCESSING/COOLDOWN**
  instead of dropping them. On confirmed user speech while Zoe is speaking (energy gate +
  Smart Turn confirmation, to avoid Zoe barging in on herself via mic echo): stop TTS
  output (flush the outbound track), cancel the in-flight turn, transition to LISTENING
  seeded with the buffered frames.
- Echo/false-barge robustness is the known hard part on an open-mic panel — measure the
  false-barge rate in lab with TTS playing before any prod enable.
- **Gate A2:** replay corpus green (flag OFF path proven byte-identical; flag ON path
  lab-clean); no latency regression flag-OFF.

### Phase A3 — live acceptance session (maintenance window, human at mic)

The deferred spike measurements, run against the upgraded agent (flags ON, in a window,
with rollback = flip the env flags + restart `zoe-data`):

| Gate | Script (from the spike plan) | Bar |
|---|---|---|
| M1 barge-in | interrupt Zoe mid-sentence 10× | clean stop + new turn; time-to-stop < ~300 ms |
| M2 turn detection | 15 utterances incl. mid-thought pauses | beats energy VAD on false-cutoffs + false-waits |
| M3 latency | end-of-speech → first TTS audio, median of 10 | ≤ current path |
| corpus | full `~/.zoe-voice-samples` replay + speed probe | zero said-vs-did regressions; no per-stage speed regression |

- **Pass → flags ON in prod** (operator-authorized restart, per the deploy runbook);
  results table recorded here; PLANS.md updated.
- **Fail on barge-in/turn quality despite correct integration → that is a re-open
  trigger for Pipecat (§4)**, because it would mean the *approach* (Smart Turn + hand-built
  interruption on aiortc) is insufficient, not just the implementation.

Each phase is its own small PR through the normal Greptile loop.

---

## 3. RAM budget — the binding constraint (16 GB unified, Orin NX)

From [`docs/knowledge/runtime-topology.md`](../knowledge/runtime-topology.md) and the
spike's measurements. Memory is unified (CPU+GPU share 16 GB) and the box runs
memory-tight.

| Resident | Measured/documented RSS |
|---|---|
| llama-server (Gemma 4 E4B-QAT+MTP, mlocked) | ~5.2 GB per topology doc; **5.4 GB observed during the spike** |
| Kokoro TTS sidecar (`kokoro-tts.service`, :10201) | **~2.3 GB per load** |
| zoe-data (FastAPI + Moonshine STT in-process, CPU) | a few hundred MB (`MALLOC_ARENA_MAX=2`; ~3.2 GB without it) |
| LiveKit media server (on-demand, reaped when idle) | ~560 MB while up |
| Containers (auth, Postgres, UI/nginx, HA, tunnel, …) | the rest |
| **Headroom** | **spike-measured 1.1–2.6 GB free, swap already 23 GB deep** |

Hard rules that follow:

- **Never two Kokoro loads.** Two Kokoro instances (~2.3 GB each) **OOM the box** — this
  is why every voice-harness run takes `flock /tmp/zoe-voice-harness.lock`. Any future
  Pipecat service must **point at the existing Kokoro sidecar** (or otherwise guarantee a
  single load), never instantiate its own.
- **No parallel voice stacks.** The headroom number is why a Pipecat migration would be a
  *replacement cutover*, not a side-by-side bake-off — and a key reason for today's NO-GO.
- **Every new resident model publishes its measured RSS before any prod enable** (Smart
  Turn v3 in Phase A0 included). No speculative loads; startup warmups already skip
  themselves under memory pressure.

---

## 4. Pipecat: parked, with explicit re-open triggers

Re-open a Pipecat migration only if one of these fires:

1. **Phase A3 fails on quality** — Smart Turn + hand-built barge-in on aiortc can't reach
   the M1/M2 bar despite correct integration.
2. **The aiortc transport itself becomes the bottleneck** (audio artefacts / stability at
   always-on duty cycles that a framework transport would solve).
3. **The RAM picture changes materially** (hardware upgrade, or the brain/TTS footprint
   shrinks), making parallel bring-up or comfortable replacement feasible.

If re-opened, the migration prerequisites (all from the spike's findings) are:

- **Own isolated service** (own venv/systemd user unit — Pipecat can never live in
  zoe-data's env; measured dependency conflicts).
- **Custom Moonshine STT wrapper** — the rock stays; Pipecat's `whisper` service is not
  an option (Moonshine is canonical; whisper removed from the live path in #854).
- **numpy/torch ABI resolved and load-tested** (pin to a numpy-1.x-compatible set or a
  Jetson torch built for numpy 2.x) — the *"may crash"* warning must be gone, not ignored.
- **Single-Kokoro guarantee** (§3) — reuse the sidecar.
- **Cutover, not parallel:** a scheduled window; the LiveKit path stays intact and
  env-flag-selectable for instant rollback; parity gate = the full M1–M6 table from the
  [spike plan](ambient-voice-pipecat-spike.md) **plus** the replay corpus + latency probe.
  The live LiveKit voice path is not touched until that parity gate passes.

---

## 5. Deliberately deferred (separable increments, per the ADR + PLANS)

- **Phone / SIP** — its own ADR when phone work starts. Trunk provider + bridge (LiveKit
  SIP — which Increment A *preserves* by keeping LiveKit — or Jambonz). Not part of this
  plan.
- **Wyoming room satellites** — the rooms/presence hardware layer (ESP32 / Voice PE
  feeding the agent). Orthogonal to the framework choice; later increment.
- **Full-duplex S2S (Moshi / Step-Audio)** — watch item only; replaces the brain rock and
  needs ≥24 GB. Not viable on this hardware; revisit if the rocks/hardware ever change.

## Status ledger

- 2026-07-06 — decision recorded (this doc): NO-GO Pipecat-now / GO Increment A. Next
  action: **Phase A0** (baseline capture + Smart Turn v3 standalone bench).
- Results tables for A0/A1/A2/A3 get appended here as phases complete.
