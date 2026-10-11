---
type: Reference
title: Little Gemma deep dive and the Jetson field scan (2026-10-10)
description: How Zoe compares with the other assistants that run on a Jetson (Little Gemma, Jarvis-home, dwain-barnes, Seeed), a source-level deep dive on cortexist/little-gemma (engine, benchmarks, paper, limits), why it is not a drop-in replacement for Zoe's llama-server, and the four ideas taken from it, each tested as its own experiment.
tags: [benchmark, jetson, little-gemma, latency, mtp, prefill, tts, field-scan]
timestamp: 2026-10-10T08:00:00+08:00
---

# Little Gemma deep dive and the Jetson field scan (2026-10-10)

The question was whether Zoe is the fastest and smartest AI running on a Jetson. **Fastest: no.** On the same Orin NX 16GB, cortexist/little-gemma decodes Gemma 4 E4B faster and runs a far shorter voice loop. **Smartest: no other project publishes a measure to compare against.** No other Jetson project found publishes a memory, tool or household benchmark. The claim that holds up is that Zoe is *the most complete and best-measured private household assistant running fully on one Jetson*.

## Field scan (public projects, 2026-10-10)

| project | board / model | speed | voice latency | memory / tools / home |
|---|---|---|---|---|
| [little-gemma](https://github.com/cortexist/little-gemma) | Orin NX 16GB, Gemma 4 E2B/E4B/12B QAT + MTP | E4B 38.1 tok/s (3-prompt mean, greedy), prose 33.0 | 0.65 s composed headline (E2B, piper), E4B ~0.8 s on a conversational turn | none (engine + voice demo) |
| [Jarvis-home](https://github.com/itsMustafamr/Jarvis-home) | Orin Nano 8GB, Gemma 4 E2B | - | ~3-4 s from button press to speech | lights, weather, vision; no memory |
| [dwain-barnes](https://github.com/dwain-barnes/jetson-voice-assistant) | Orin Nano Super, E2B with native audio input | TTFT 0.37-0.56 s | ~1.5 s to first sound (web UI) | context window only |
| [Seeed Local Voice Service](https://www.seeed.cc/solutions/reference-designs/jetson_voice_assistant) | Orin NX | - | 58 ms p50, measured with **no LLM** in the loop | not an assistant |
| **Zoe** | Orin NX 16GB Super, E4B QAT + MTP on llama.cpp b11194 | ~28 bench / 34.7 all-traffic (temp 0.7) | 2.8-3.4 s end of speech to sound (2.0-2.5 s with flag-dark levers) | household identity, measured memory (ZMB), two-stage router, HA + MA, night mind |

## Little Gemma, from the source

- **What it is.** A C/CUDA Gemma 4 runner of about 9.9k lines, MIT-licensed, from Cortexist LLC (Shaw and Claire Tan). It was built to teach, in the `llama2.c` spirit, and has 350 commits from 2026-06-07 to 2026-09-17. Its benchmark harness is in a private research repository. Its preprint is *"Fluent and Cohesive: Sub-Second Voice Interaction with General-Purpose Open-Weight Models on a 20-Watt Edge Device"* (2026-07-28). Sibling repos: `little-gemma-tools` (voicecat, clausecat and a Flask OpenAI adapter) and `little-gemma-cognition` (camera and mic signals turned into dated text spans; "everything reaches the model as text, the GPU belongs to the LM").
- **Why its decode is faster.** Nsight Compute shows that llama.cpp's `mul_mat_vec_q` uses only 45% of the Orin's memory bandwidth, because it is compute-bound on 8 SMs. Little Gemma's wide int8 loads reach 84%. A Q4_0 specialization (2026-09-06) adds more on top. The paper puts the E4B/12B QAT lead at 1.08-1.11x. The README's 25.9 vs 19.0 figure compares a serving probe against `llama-bench`.
- **Why its prefill is faster.** It is 1.55x llama.cpp on E4B because of cache-only prefill, which stops after the last needed KV write. It is slower on the 12B.
- **Benchmark conditions.** Greedy decoding, pinned `jetson_clocks` (GPU 918 MHz), first turn discarded, replies required to be byte-identical. All of its MTP numbers are greedy. With sampling, acceptance falls from 77% to 28-46% (E2B, temperature 1.0). Zoe samples at 0.7.
- **The 0.65 s headline.** It is built from separately measured stages (first clause 0.549 s + first streaming-piper PCM 0.10 s). The README says the one-command end-to-end run "is still to be done". It excludes the ASR final commit (~1.0 s to turn close with a live mic), uses E2B, and uses a bare voice system prompt with no recall, router or tools.

## Why it is not a drop-in brain for Zoe

- **Prompt size.** `src/run.c` caps each turn's prompt at `promptv[4096]` tokens and each conversation at `SERVE_SEQ 8192`. The OpenAI adapter rejects inputs over 3,500 bytes. Zoe's brain prompts run to p99 3,280 tokens (`brain-flags-tuning-2026-09.md`), and the adapter's byte cap is far below that.
- **No cross-request prefix cache.** It keeps a prefix only for a fixed `-sys` system prompt, so every request re-reads its whole history. Zoe's `--cache-ram` re-reads a median of 1 token, so switching would add 1.5-2.5 s of prefill per turn.
- **Missing features.** The adapter has no tool calling, no per-request sampling and no usage counts. The engine serves one conversation at a time, caps output at 1,024 tokens, and has no health or metrics endpoints.
- **Speed gain is small for our workload.** On chat prose the decode gain is ~10-20% (33.0 vs ~28 tok/s), and it is greedy-only. The project also deliberately gives up speed to keep the code readable.

## Ideas taken from it, each tested as its own experiment

| # | idea | Little Gemma's evidence | Zoe experiment | verdict |
|---|---|---|---|---|
| 0 | the engine itself | see above | head-to-head on this box, Zoe's prompts, temp 0.7 and greedy | **REJECT as a swap** ([record](little-gemma-engine-bench-2026-10-10.md), #1975). Decode is faster: MTP N=2 29.2 vs 22.9 tok/s (+27%), plain +18%. But short-turn TTFT is the same (0.095 vs 0.101 s), so first sound does not move. The paper's N=4 is slower than plain on Zoe turns. Prefill is faster (558-token packet 0.63 vs 0.95 s), on one window's evidence. Greedy text matches llama.cpp on only 5/15 turns. Blockers: no tools, grammar or HTTP; 3.8% of live requests exceed its 4,096-token cap. Side finding: the brain can fail to restart with NvMap error 12 when the page cache is full (open-problems) |
| 1 | prefill under speech | E4B TTFT after last word 2.04 -> 0.16 s | measure what is movable under speech; cache-warm / B1.1 / streaming Moonshine | **Brain prefill: REJECT** ([record](prefill-under-speech-2026-10-10.md), #1972/#1973). Fitted over 15,174 requests: 55.9 ms + 1.48 ms per new token, median 16 new tokens, so the ceiling is ~25 ms. Cache-warming recovers 14 ms because `--cache-ram` already restores prefixes. **STT under speech (streaming Moonshine): ADOPT, flag-dark** (`ZOE_STT_STREAM_UNDER_SPEECH` + Pi `ZOE_STT_STREAM_UPLOAD`): post-speech STT 0.746 -> 0.265 s median, 15 of 16 faster; negative control (all audio fed at the end) 0/16 faster; said-vs-did 40/40 identical. Stream text differs from batch on 7.6% of words vs a 2.3% batch-vs-batch floor, so it needs a panel week before the flip. B1.1 early transcript: not built here (already flag-dark) |
| 2 | model as its own clause splitter | first audio 1.21 -> 0.82 s | voice-mode prompt flag, A/B against `ZOE_FIRST_SOUND_CLAUSE`, plus a quality check | **REJECT** ([record](clause-first-prompt-2026-10-10.md), #1979 closed). The first unit shrinks from 12 to 9 words, but first sound does not move (paired +0.13 s). Tool calls on tool-shaped prompts fall from 9/10 to 4/10, followed by ungrounded answers: the paper's "confidently wrong" warning, reproduced. The mechanical `ZOE_FIRST_SOUND_CLAUSE` cut was -0.51 s paired (faster on 19/30) with tool use intact (8/10), which strengthens the case for flipping it after the ear check |
| 3 | streaming vocoder | first PCM ~0.10 s (piper) | Kokoro first-chunk levers; Kokoro stays (rock) | **REJECT** ([record](kokoro-streaming-first-chunk-2026-10-10.md), #1971): a windowed decoder gets first PCM constant-time (~200 ms idle, ~300 ms loaded) but the audio differs by 9.5-17 dB log-spectral distance, because Kokoro's AdaIN normalises over the whole utterance and piper's HiFi-GAN has no such layer. fp16, cudnn.benchmark and skipping empty_cache gain nothing. The real lever is a shorter first unit (~170 ms fixed + ~12 ms/word; 50-70% slower under brain decode), which `ZOE_FIRST_SOUND_CLAUSE` already covers |
| 4 | MTP draft-head vocabulary trim | E4B +7.6%, byte-identical | patched llama.cpp copy with a Zoe-domain d2t subset | **INCONCLUSIVE, leaning a small win** ([record](mtp-draft-vocab-trim-2026-10-10.md), #1976). The head is 83% of a draft step's matvec time: 1.49 ms full vs 0.14 ms for a 16k slice. Projected +4-5% decode, an upper bound about the size of the ±3% noise band. Zoe's replies use only 1,836 distinct tokens, so 16k covers 98.1-99.4% held-out. The 35-line patch exists but the end-to-end A/B never ran: both brain windows hit CUDA OOM at load. It needs a quiet box. Little Gemma's own 16K trim measured +2.6% at N=4 on Zoe turns (#1975), inside noise |

Each experiment writes its own record in this bundle and links it from this table.

## Outcome

- **Adopted, behind a flag that is off:** streaming Moonshine during recording (#1972 server, #1973 Pi daemon). It is a voice-path change, so it needs the replay gate and a panel week of `STT_STREAM` evidence before the flip.
- **Strengthened by this work:** `ZOE_FIRST_SOUND_CLAUSE`. Kokoro first-chunk time is ~170 ms fixed plus ~12 ms per word and 50-70% slower under brain decode, and the cut measured -0.51 s paired. It is still waiting on the ear check.
- **Open:** the MTP draft-head d2t trim needs an 18-minute A/B on a quiet box (#1976 tooling, in this PR). An untested follow-up is `--spec-draft-n-max 2`: Little Gemma's N=2 beat N=4 on Zoe turns, but the drafting logic differs and the 2026-09 sweep found n-max 3 flat.
- **Incident class found:** a cold `llama-server` restart can fail with NvMap error 12 / `cudaMalloc` OOM while the page cache is full. It hit three times on 2026-10-10. See [open-problems.md](open-problems.md).
