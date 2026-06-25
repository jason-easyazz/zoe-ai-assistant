---
type: proposal
title: "Kokoro TTS time-to-first-audio — the unmeasured voice stage, measured"
status: draft
owner: jason
created: 2026-06-25
scope: services/zoe-data/routers/voice_tts.py, scripts/setup/kokoro_sidecar.py, scripts/perf/measure_tts.py
verified_against:
  - live Kokoro sidecar on 127.0.0.1:10201 (device="cpu (onnx)", voice=af_sky), measured 2026-06-25
  - scripts/perf/measure_tts.py over real brain replies sourced from ~/.zoe-voice-samples replay corpus
  - routers/voice_tts._generate_voice_stream first-chunk emitter (_extract_first_unit + _synthesize_kokoro_sidecar)
do_not_change:
  - models (Gemma 4 E4B-QAT brain + MTP drafter, Moonshine v2 STT, Kokoro TTS) are rocks — optimise around, never swap
  - this is a MEASURE/PROPOSE-ONLY record; it lands no behaviour change until Jason reviews
related:
  - ./zoe-core-turn-latency.md   # brain per-turn latency (the stage BEFORE first audio)
  - ./VOICE_PIPELINE.md
---

# Kokoro TTS time-to-first-audio — measured

> **MEASURE-ONLY.** No behaviour change lands from this doc until reviewed. The rocks
> (Gemma brain, Moonshine STT, Kokoro TTS) are untouched. Numbers below are from the
> LIVE sidecar on this Jetson Orin NX 16GB, 2026-06-25.

## What was unmeasured

We had STT (Moonshine, ~700ms median), brain (Gemma, ~2.95s) and pre-TTS end-to-end
(~3.8s) timed via `scripts/perf/measure_voice.py`. The replay harness deliberately
**stops at spoken-ready text** (`_clean_for_speech`) — it never calls TTS. So the gap
that decides how fast a reply *starts speaking* — from "first speakable clause ready"
to "first audio bytes back from Kokoro" — was never timed. This doc closes that gap.

## How the live first chunk is produced

In `routers/voice_tts._generate_voice_stream` the brain streams tokens; the emitter
snaps the **first clause** out of the buffer as early as possible via
`_extract_first_unit` (break on the first `,;:.!?` after >=12 chars, or a word
boundary by 40 chars) and synthesizes *that clause alone* — not a whole sentence —
through `_synthesize_kokoro_sidecar` (the warm sidecar on :10201, primary leg of the
TTS waterfall). The existing `voice_stage_seconds{stage="tts_first_byte"}` Prometheus
metric measures `monotonic() - t_chat_start`, i.e. it **bundles brain time + TTS**;
it does not isolate TTS-internal latency. `scripts/perf/measure_tts.py` isolates it by
importing the exact live `_extract_first_unit` and timing the same sidecar POST.

## Measured numbers (af_sky, device = CPU/ONNX)

First-unit synth latency = time from first clause ready to first audio bytes.

| cohort | n | median | p10 | p90 | min | max |
|---|---|---|---|---|---|---|
| **first unit, COLD** (cache miss — a *new* clause) | 10 | **3125 ms** | 1939 | 3616 | 1771 | 4999 |
| **first unit, COLD, repeat run** (shorter clauses) | 4 | **1634 ms** | 803 | — | 803 | 2203 |
| **first unit, cache HIT** (warmed/common phrase) | — | **~2–12 ms** | — | — | 2.3 | 12.4 |
| full reply, cold | 10 | 5039 ms | — | 7291 | 3392 | 8698 |

The wide cold range tracks clause length: 13-char "Good morning," ≈ 0.8–1.8s, a
~60-char opening clause ≈ 3s. Synthesis is roughly linear in characters.

### The sidecar phrase cache splits reality in two

`scripts/setup/kokoro_sidecar.py` keeps an LRU phrase cache (af_sky, speed 1.0,
<=240 chars) and pre-warms ~common short phrases at boot. So:

- **Common opener / cached phrase** ("I'm sorry, Jason,", "You've got nothing this
  week.", warm-list phrases) → first audio in **single-digit ms**. Effectively free.
- **Novel brain clause** (cache miss — the usual case for a real, varied reply) →
  **~1.6–3.1s median** on CPU before the first byte.

## Headline finding: TTS first audio is on CPU, and dominates the post-brain wait

`/health` reports `device: "cpu (onnx)"`. This is a **deliberate config choice**, not
a failure: `ZOE_KOKORO_BACKEND` defaults to `onnx`, which runs Kokoro on CPU (~600MB)
to free the ~2.3GB the PyTorch/CUDA build would hold. On a 16GB box already at ~11.6GB
used (~600–800MB free), that RAM is the constraint — the "~150ms warm GPU" promised in
the `voice_tts.py` / sidecar comments describes the **non-default `pytorch` backend**,
which is not what runs.

Net effect on a cache-miss reply:

```
STT ~0.7s  +  brain ~2.95s  →  [first clause ready]  +  TTS first chunk ~1.6–3.1s
                                                          └── unmeasured until now ──┘
```

The first **spoken** word can land ~1.6–3.1s *after* the brain's first clause — a
delay comparable in size to the entire brain turn, and previously invisible because
the only metric (`tts_first_byte`) folds it into brain time and because cached test
phrases hide it behind instant cache hits.

## Safe optimization opportunities (propose-only; no behaviour change here)

1. **Warm the cache with high-frequency *openers*, not just whole phrases.**
   `_extract_first_unit` makes the first chunk a short clause, yet the sidecar warm
   list (`_WARM_PHRASES`, `scripts/setup/kokoro_sidecar.py:80`) pre-caches *full*
   phrases. Pre-caching the handful of recurring opening clauses Zoe actually starts
   with ("I'm sorry, Jason,", "Sure thing,", "Good morning,", "You've got…") would
   turn the *first* chunk into a cache hit (single-digit ms) on the most common turns,
   even when the tail is novel. Seam: `_WARM_PHRASES` + `_blocking_synthesize`.
   *This is a data/warm-list change, not a path change — lowest risk.*

2. **Shrink the first clause further for the first chunk only.** `_FIRST_UNIT_MIN_CHARS=12`
   / `_FIRST_UNIT_SOFT_CAP=40` (`voice_tts.py:755`). Because cold synth is ~linear in
   characters, emitting a shorter first unit (e.g. the first 1–2 words) would cut
   time-to-first-audio proportionally, at the cost of a slightly choppier opening.
   Worth an A/B; behaviour-affecting, so out of scope for this measurement task.

3. **Isolate the metric.** `tts_first_byte` currently = brain + TTS. A separate
   `tts_synth_first_chunk` gauge (measured *inside* `_emit_sentence` around the
   `_synthesize_kokoro_sidecar` call, `voice_tts.py:4056`) would let us watch the TTS
   stage on the live panel without a replay. One-line instrumentation, no behaviour
   change — but it touches `voice_tts.py`, owned elsewhere this task; flag for that owner.

4. **Backend tradeoff is a RAM decision, not a TTS bug.** If/when the RAM budget frees
   up (e.g. brain/STT footprint drops), flipping `ZOE_KOKORO_BACKEND=pytorch` would
   move synth to CUDA and collapse the cold first-chunk from seconds to the promised
   ~150ms. That is a memory-budget call for Jason, recorded here so the tradeoff is
   explicit — not a swap of the rock.

## How to reproduce

```bash
# under the harness lock so it can't OOM against a sibling replay:
flock /tmp/zoe-voice-harness.lock -c \
  'ZOE_PERF=1 python3 scripts/perf/measure_tts.py --replies-file <replies.txt> --cold \
     --service-dir services/zoe-data --json tts.json'
# or source replies live from the corpus (needs a reachable brain):
#   ... measure_tts.py --run-replay --last 10 ...
```

The probe hits the already-running sidecar over HTTP (the same call the live path
makes); it never loads a second Kokoro model in-process and never mutates Zoe state.
