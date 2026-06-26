---
type: Reference
title: Zoe Voice Pipeline
description: The end-to-end voice path (STT → brain → TTS), how it's measured, and the regression corpus — plus the load-bearing caveat that the warm replay harness understates real live latency.
tags: [voice, stt, tts, performance, testing]
timestamp: 2026-06-26T00:00:00Z
---

# Zoe Voice Pipeline

How a spoken turn flows through Zoe, and how we measure it without regressing. The three stages are
**rocks** — never swap them, only optimise around them (see [CANONICAL.md](../CANONICAL.md) and
`VISION.md` principle 1). Topology/ports live in [runtime-topology.md](runtime-topology.md).

## The path

1. **STT — Moonshine v2 Medium.** Runs **in-process inside `zoe-data` on CPU** (onnxruntime GPU
   discovery fails on Tegra). Warmed at startup via `warm_moonshine` (`services/zoe-data/main.py`,
   `routers/voice_tts.py`). A **faster-whisper** worker exists as a secondary/fallback
   (`ZOE_WHISPER_DEVICE=cuda`) — Moonshine is the primary rock.
2. **Brain — Gemma 4 E4B-QAT + MTP**, host-native `llama-server` on `:11434`.
3. **TTS — Kokoro**, sidecar on `127.0.0.1:10201`, via a waterfall in `routers/voice_tts.py`:
   **Kokoro → Edge TTS → espeak-ng** (each falls back to the next).

Per-stage timings are exported to Prometheus as `zoe_voice_stage_seconds`
(`services/zoe-data/voice_metrics.py`), scraped at `:8000/metrics`.

## Measuring it — the replay harness

Jason's saved WAVs at **`~/.zoe-voice-samples`** (245 clips as of 2026-06-26) are a **permanent
regression corpus**. Replay-gate **every** voice change; the said-vs-did mapping must not regress —
"can't do it" on a sample is a bug, not an excuse.

- Harness: `scripts/perf/measure_voice.py` + `scripts/perf/measure_tts.py` (set `ZOE_PERF=1`); they
  wrap `services/zoe-data/tests/replay_samples.py`.
- **Always run under `flock /tmp/zoe-voice-harness.lock`** — two Kokoro loads (~2.3 GB each) will OOM
  the memory-tight box.

## The caveat that bites (read this)

The replay harness uses **warm models and stops *before* TTS**, so **its numbers UNDERSTATE real live
latency** — sometimes by a lot. Don't quote harness timings as live performance. As measured live
(2026-06-26, will drift — re-measure, don't cite as fixed): STT ~1.9 s (p90 ~8 s), brain ~4.8 s,
first-audio ~5 s p50 / ~12 s p90. Two live-only effects the warm harness misses: **memory-starved
cold STT** (warmup skipped under pressure) and **wake-word bleed** on the first command. Honest
*measurement* over guessing (`VISION.md` principle 4) — when you change the path, measure live, not
just the harness.
