---
type: Reference
title: Kokoro streaming first chunk - can a streaming vocoder make first audio constant-time? (2026-10-10)
description: Profile of where Kokoro's 0.20-0.46 s first chunk goes on the Orin (CUDA, idle and while llama-server decodes), and paired A/B of a windowed (streaming) decoder, smaller first units, empty_cache, cudnn.benchmark and fp16 against the Little Gemma / piper vits-streaming idea. Verdict - no lever adopted; the windowed decoder is fast but not audio-equivalent because of InstanceNorm.
tags: [voice, tts, kokoro, latency, ttfa, streaming-vocoder, measurement, little-gemma]
timestamp: 2026-10-10T09:00:00+08:00
---

# Kokoro streaming first chunk (2026-10-10)

**Idea** (Little Gemma paper; `cortexist/piper1-gpl`, branch `vits-streaming`): split the vocoder so the first PCM arrives after one decoder window, making first audio constant-time in clause length (about 0.10 s for piper). Zoe's Kokoro (`kokoro-tts.service`, PyTorch CUDA, :10201) takes 0.20-0.46 s idle and 0.29-0.76 s while the brain decodes. Kokoro is a rock (CANONICAL), so the question was only whether the same trick works *around* it. Same model, same voice (`af_sky`), kokoro 0.9.4, torch 2.8.0.

**Verdict: REJECT every lever; nothing shipped to the sidecar.** The streaming decoder has the right latency shape and the wrong audio. Details below; probe: `scripts/perf/kokoro_first_chunk_probe.py` (opt-in, refuses to run beside the live sidecar).

## Where the time goes

Through the live sidecar API (`POST /synthesize`, `speed=1.001+` so the phrase cache is bypassed and nothing is persisted), n=20 per cell after 3 discarded, text order shuffled. Wall ms, median [p10-p90]:

| first unit | idle | llama-server decoding (200-token completions in a loop) |
|---|---|---|
| 5 words | 210 [206-229] | 309 [296-333] |
| 10 words | 260 [254-283] | 400 [379-422] |
| 15 words | 316 [310-328] | 493 [483-531] |
| 25 words | 461 [457-476] | 756 [737-796] |

Cost is a fixed floor of about 170 ms plus about 12 ms per word idle; contention with the brain adds 50-70 % (GPU time-slicing, not CPU).

Stage split in a private Kokoro (sidecar stopped inside the lock), synced per stage, medians of 5, idle ms:

| words (frames) | g2p | bert | duration | F0/N | text enc | source | decoder pre-gen | **generator** | copy to CPU |
|---|---|---|---|---|---|---|---|---|---|
| 5 (99) | 5 | 24 | 16 | 20 | 6 | 3 | 20 | **92** | 0.4 |
| 10 (159) | 6 | 25 | 21 | 22 | 8 | 3 | 22 | **130** | 0.4 |
| 15 (219) | 7 | 25 | 26 | 24 | 9 | 3 | 24 | **172** | 0.5 |
| 25 (373) | 9 | 27 | 37 | 28 | 12 | 3 | 28 | **282** | 0.7 |

About 100 ms is a text-side floor (misaki G2P, ALBERT, duration LSTM, F0/N, text encoder), nearly flat in length. The iSTFTNet **generator** (upsampling + AdaIN resblocks) is 45-65 % of the call and the only part that grows with length (about 0.75 ms per 25 ms frame). Under brain decode every stage slows (25 words: generator 282 to 477 ms). `cpu_copy` and `_pcm_to_wav` are noise. A hand-written forward that mirrors `KModel.forward_with_tokens` is **bit-identical** to `KPipeline` at a fixed seed (log-spectral distance 0.000), so the staged timings are the real model, not a re-implementation.

## Lever (b): windowed (streaming) decoder - fast, but not the same audio

Decode the generator over frame windows (W frames of 25 ms plus C frames of context each side) with the harmonic source computed once for the whole utterance (so excitation phase is continuous) and only the cropped middle kept. First PCM = text side (whole utterance, needed for durations) + source + first window + copy.

Latency, ms median, n=5 interleaved, W=8 C=8: **idle 173 / 245 / 177 / 200** for 5 / 10 / 15 / 25 words versus 201 / 289 / 294 / 438 for the whole call; **under decode 209 / 306 / 276 / 300** versus 291 / 495 / 492 / 733. So it IS constant-time (about 175-200 ms idle, 210-300 ms loaded) and saves 54 % idle / 59 % loaded at 25 words, 0 at 5 words.

Audio equivalence is the failure. Log-spectral distance (dB, bins above -60 dB) of the windowed concatenation against the same-noise whole decode, one utterance per length, fixed seeds:

| arm | LSD vs whole | RMS ratio |
|---|---|---|
| **control: whole decode, different excitation-noise seed** | **2.4-2.8** | 1.00 |
| fp16 decoder vs fp32 | 1.25-1.31 | 1.00 |
| W=24 C=16 (best) | 9.5-10.9 | 0.85-0.96 |
| W=16 C=16 | 10.6-11.6 | 0.88-0.96 |
| W=8 C=8 | 15.3-16.9 | 0.82-0.93 |
| negative control: C=0 (hard concatenation) | 22.4-28.8 | 0.68-0.78 |

The instrument sees the difference (the C=0 arm is worst, context helps monotonically, the run-to-run noise floor is 2.5 dB), and the best windowed arm is still 4x that floor with up to 15 % level loss. Boundary sample jumps are 2-13x the whole-decode ratio. **Cause: every `AdaIN1d` is an `InstanceNorm1d` over the time axis**, so each of the several dozen norms in the decoder and generator normalises with utterance-global statistics. A window cannot know them before the rest of the utterance is decoded. piper's HiFi-GAN has no such layer, which is why the trick is trivial there. Larger context shrinks the error only by approaching the whole utterance, which removes the saving. A fix would be approximate stats (retraining or recalibrating Kokoro), which swaps the rock. Not pursued.

Ear-check WAVs (this is a voice-path judgement the metrics cannot make; the operator should listen before anyone revisits): `/home/zoe/kokoro-ear-check-2026-10-10/` (copied from the run), per length `{5,10,15,25}w_`: `A_whole.wav` (today), `B_win_W8_C8.wav`, `B_win_W16_C16.wav`, `neg_C0.wav`, `fp16.wav`. `results.jsonl` beside them holds every number above.

Also: nothing consumes streaming today. `POST /synthesize_stream` exists but synthesises the whole text first and then yields it (it is a queue wrapper), and grep finds no caller in `services/zoe-data`. A real streaming decoder would additionally need `voice_tts` and the Pi playback path to start before the clip ends.

## Lever (a): smaller or earlier first unit

The table above is the lever: five words instead of twenty-five saves about 250 ms idle and 450 ms while the brain decodes. This is already built as `ZOE_FIRST_SOUND_CLAUSE` (flag-dark; see [first-sound-latency-2026-10-09.md](first-sound-latency-2026-10-09.md): chat -0.77 s). It needs no sidecar change, and a sidecar-side splitter would just duplicate it. The ear check on the pitch reset and the replay gate remain the blockers, not Kokoro.

## Lever (c): settings that change time, not audio

Paired, interleaved, order shuffled, n=20 per cell after 3 discarded (n=10 for cudnn). The negative control is the same arm run twice in each round, which gives the instrument's noise floor.

| lever | result | verdict |
|---|---|---|
| skip `torch.cuda.empty_cache()` before each synth (what the sidecar does) | 201 vs 197 ms (5 words) to 438 vs 429 (25 words), n=5, inside spread | no effect, REJECT |
| `cudnn.benchmark=True` | 193/238/291/431 ms first-ever shape vs 190/240/293/432 warm | no effect, REJECT (and per-new-shape autotune would only add risk) |
| fp16 decoder (autocast) | paired vs fp32, idle: +5.6 (5 words), -6.0, -14.8 (15), -26.6 ms (25); control fp32-vs-fp32 median -0.5/+0.9/+0.1/-2.6; loaded: -5, -13, -27, -60 ms | real but at most 6 % (8 % loaded at 25 words), nil on short first units; audio differs (LSD 1.3 dB, half the seed noise floor). REJECT: not worth an audio change on a rock for under 15 ms on a typical first unit |
| bf16 decoder | 199/238/286/417 ms | worse than fp16, REJECT |
| warmup | the sidecar warms once with a short phrase; first-ever-shape cost showed no cold penalty (previous row) | nothing to gain |

CUDA graphs were not tried: shapes vary with every utterance and padding changes the InstanceNorm statistics for the same reason as above. `torch.profiler` returned no CUDA events on this Jetson (no CUPTI), so the launch-bound-versus-compute-bound split rests on the generator scaling linearly with frames (compute-bound).

## What would actually move first audio

1. Turn on the first-clause unit (`ZOE_FIRST_SOUND_CLAUSE`) after the ear check and replay gate: that is the 250-450 ms this document keeps finding, with no Kokoro change.
2. The brain, not Kokoro, is the contention: a 5-word unit costs 210 ms idle and 309 ms while the brain decodes. Anything that keeps the brain quiet during the first unit helps more than any vocoder change.
3. Revisit a streaming vocoder only if Kokoro is ever replaced by a model without global normalisation (that is a rock swap, not a latency tweak).

## Method and honesty notes

- Instruments: live-API runs held the shared lock; the private-Kokoro runs stopped `kokoro-tts` inside the lock with a trap restart (two short windows; MemAvailable 3.8 GB after the stop; sidecar restored `degraded=false`, `device=cuda`, `pipeline_loaded=true` both times). The brain was running throughout.
- Under-load arm: a looping `POST /completion` of 200 tokens on the live llama-server (same GPU, cache_prompt on). It briefly perturbed the live prompt cache.
- Limits: stage profile n=5; windowing equivalence is one utterance per length with fixed seeds (the metric is deterministic given the seed, but four sentences is not a corpus); fp16 timing n=20; no ear check was possible by an agent. The replay gate (`voice_regression_probe.py`) was not run because no voice-path code changed.
- Re-measure: the probe's header carries the exact window recipe.
