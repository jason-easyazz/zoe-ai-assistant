---
type: Reference
title: Little Gemma vs llama-server - engine head-to-head on Zoe's workload (2026-10-10)
description: Measured on the live Orin NX with Zoe's real GGUFs and real brain prompt shape - cortexist/little-gemma (run-cuda-i8, MTP) against llama.cpp b11194 with the live unit's exact flags. Verdict REJECT as a drop-in engine swap; the numbers, the negative control, the integration costs and how to re-run.
tags: [brain, engine, little-gemma, llama-cpp, mtp, benchmark, measurement, orin]
timestamp: 2026-10-10T09:45:00+08:00
---

# Little Gemma vs llama-server on Zoe's workload (2026-10-10)

**Question.** Would swapping the serving ENGINE (llama.cpp b11194 -> [cortexist/little-gemma](https://github.com/cortexist/little-gemma) `run-cuda-i8`, MIT, ~9.9k lines C/CUDA) make Zoe better, and by how much, with the same rocks (Gemma 4 E4B-QAT `UD-Q4_K_XL` + its MTP head)?

**Verdict: REJECT as an engine swap.** Little Gemma decodes **+18% (plain) to +27% (MTP N=2)** faster than the live llama-server on Zoe-shaped chat turns, prefills ~1.5x faster, and is byte-identical to *itself* across every MTP depth - but time-to-first-token on the short turns that dominate Zoe is **unchanged (0.095 s vs 0.101 s)**, the gain does not reach first sound, and the swap would remove tools/grammar/HTTP/metrics/multi-slot that ~15 callers of `:11434` depend on. What is worth keeping is a *finding about our own flags* (below), not the engine. Nothing was deployed or flagged.

## Method

- Box: Orin NX 16 GB, MAXN_SUPER, GPU 1173 MHz fixed (read at the start and end of every arm: 1173000000 both). **Not** the paper's pinned 918 MHz; `jetson_clocks` was not run.
- Engines, same model files (`~/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` + `mtp-gemma-4-E4B-it.gguf`):
  - **llama**: a second instance of `~/llama.cpp-b11194/build-jetson/bin/llama-server` with the live unit's *effective* ExecStart (drop-ins included: MTP n-max 4 p-min 0.6, ctx 8192, parallel 1, KV q8_0, `--cache-ram 1024`, `--swa-full`, FA, `--jinja`), only `--port` changed. `llama-prod` = server sampling (temp 0.7, top-k 64, top-p 0.95); `llama-greedy` = temp 0 / top-k 1 per request. Driven over `/completion` with the prompt text rendered by the server's own chat template (so a tool-call parser cannot confound TTFT) and `cache_prompt` on (prod regime: the system prefix is cached).
  - **little-gemma**: built out of tree (`-DCMAKE_BUILD_TYPE=Release -DLG_CUDA_ARCH=87`, `nice -n 10 -j2`, inside the shared lock). `-mtp` with `LG_MTP_N` = 2/3/4, greedy; `-sys` carries the system turn once (its only prefix cache); one socket connection per conversation. `lg-n4-sel` = N=4 with a 16,384-row draft-vocab list (`LG_MTP_IDS`) built from the repo docs (the authors' lists are not published).
- Prompt: Zoe's live `ZOE_INSTRUCTIONS` evaluated from `labs/flue-zoe-brain-2x` + the three always-on tool declarations, rendered by the template: **2,550 tokens / 11,435 bytes** (95% of little-gemma's 12,000-byte `-sys` cap). 15 user turns in 9 conversations: 8 short chat turns (7-18 tokens), 3 long-answer asks, a 558-token memory-packet turn, a 1,074-token pasted-note turn, plus follow-ups. Chat replies 17-45 tokens.
- Procedure: per arm 1 warmup pass (discarded) + 2 measured passes, second pass in reversed conversation order; n = 30 turn samples per arm (15 prompts x 2). Decode tok/s = (tokens-1) / (last chunk - first chunk) client-side, turns with >= 8 output tokens; TTFT client-side from send to first byte.
- Memory = nvmap (debugfs, per pid) + `smaps_rollup` Anonymous - the Jetson-honest figure (MemAvailable under-counts nvmap).
- Two brain-stop windows, both under `flock /tmp/zoe-voice-harness.lock`, with a trap and a systemd dead-man timer that restarts the brain regardless.
- Harness: `scripts/perf/engine_bench.py` (`prep` / `window` / `summarize`). Raw outputs lived in the session scratchpad; the tables below are the artifact.

## Results (window 2, one window, every arm measured back to back)

| arm | decode tok/s (token-weighted; per-prompt median, range) | chat turns | long answers | TTFT short turn 1 / turn 2 (s) | prefill tok/s (new >= 300 tok) | nvmap+anon MB | min MemAvail MB |
|---|---|---:|---:|---:|---:|---:|---:|
| llama-prod (live flags, temp 0.7) | 22.9 (23.2, 20.3-30.5) | 23.1 | 22.8 | 0.101 / 0.107 | 612 | 3,577 (+1,954 mlocked file pages) | 3,862 |
| llama-greedy | 23.3 (23.1, 19.1-29.4) | 23.3 | 22.7 | 0.102 / 0.107 | 612 | 3,579 | 3,748 |
| lg plain (no MTP) | 27.0 (27.2, 26.4-27.3) | 27.1 | 27.2 | 0.095 / 0.092 | 925 | 4,735 | 4,174 |
| **lg MTP N=2 greedy** | **29.2** (29.0, 25.9-35.6) | 30.6 | 28.8 | 0.095 / 0.092 | 923 | 4,926 | 3,339 |
| lg MTP N=3 greedy | 26.5 (27.2, 22.6-37.0) | 28.1 | 26.6 | 0.095 / 0.092 | 925 | 4,926 | 3,563 |
| lg MTP N=4 greedy | 23.4 (24.2, 19.1-36.6) | 24.7 | 23.3 | 0.095 / 0.092 | 925 | 4,926 | 3,912 |
| lg MTP N=4 + own 16K head | 24.0 (23.8, 20.4-39.0) | 25.9 | 23.3 | 0.095 / 0.092 | 923 | 4,806 | 3,724 |
| lg MTP N=4 temp 0.7 | INCOMPLETE (see below) | - | - | 0.09 | - | - | - |

Context-heavy turns (first turn of the conversation, new tokens after the cached system prefix):

| turn | llama-prod TTFT | lg TTFT (all depths) |
|---|---:|---:|
| 558-token memory packet (563 / 566 tokens evaluated) | 0.953 s | 0.627 s |
| 1,074-token pasted note (1,079 / 1,082) | 1.758 s | 1.148 s |

Window 1 (earlier the same morning, c08 prompt still too long for little-gemma, see Limits) reproduced the two arms it completed: llama-prod 24.1 tok/s (window 2: 22.9, -5%), lg N=4 greedy 23.3 (window 2: 23.4). Its little-gemma *prefill* was 467 tok/s and the 558-token turn took 1.28 s - half of window 2 and not explained (no cache-drop loop, more memory pressure, and other agents were queued behind the lock). Treat the prefill advantage (925 vs 612) as one window's evidence, not two.

Live traffic for scale (journal, last 24 h, `llama-server` print_timing, includes other agents' load): decode median 34 tok/s over 5,057 turns of >= 8 tokens (token-weighted 41), median reply 14 tokens, prefill 643 tok/s on prompts >= 300 new tokens. The bench workload is harder (more prose, temp 0.7) than that mix; the *ratio* is what carries over.

### Byte identity

- All LG arms (plain, N=2, 3, 4, N=4 + trimmed head) produced **identical text on 15/15 turns** - the "speculation changes only tokens/s" claim and the draft-head trim claim both hold on Zoe's prompts.
- LG greedy vs llama greedy: exact on 5/15 turns, median common prefix 69 characters. The engines are not numerically identical (int8 matmuls vs llama.cpp's kernels), so a swap changes Zoe's wording on the same prompt even at temperature 0. llama-greedy vs llama-prod: 0/15 (sampling), as expected.

### Negative control (the harness can see a difference)

- Little Gemma with `-mtp` removed: 27.0 tok/s vs 23.4 (N=4) / 26.5 (N=3) / 29.2 (N=2). The metric moves in the predicted direction with the knob, and the same runs show the **surprise**: on Zoe's short chat replies MTP at the paper's recommended N=4 is *slower than plain decoding* (acceptance 0.26 by little-gemma's own count); only N=2 beats plain (+8%).
- Same texts across all depths (above) shows the arms were not accidentally different workloads.

## What this does and does not buy Zoe

- First sound is end-of-speech to first speakable clause (see [first-sound-latency-2026-10-09.md](first-sound-latency-2026-10-09.md)): STT, one brain TTFT (~0.10 s warm, **identical here**), first clause of ~6-20 tokens, Kokoro. A decode gain of 4-6 tok/s on a 20-token clause is ~0.05-0.1 s. It does not move the 3.3 / 2.8 / 3.4 s the turns take today; the levers that do are the flag-dark ones in that record.
- The prefill advantage (-0.33 s on a 558-token turn, -0.6 s on 1,074) only matters for cold or long prompts; Zoe's prefix cache re-prefills a median 1 token (live journal: median 16 new prompt tokens), so it is a tail effect.
- Memory: little-gemma 4.9 GB unreclaimable (nvmap 4.9 GB + 0.14 GB anon, plus ~2 GB of evictable file-mapped weights) vs llama-server 3.25 GB nvmap + 0.42 GB anon + 1.95 GB mlocked file pages = ~5.6 GB. Comparable, little-gemma slightly smaller; not a differentiator. Both land on a box that has ~1.2 GB MemAvailable with everything up.
- The paper's headline MTP number (38 tok/s at N=4) is not reproduced on this workload: it was measured on code/prose/French 100-word answers at a pinned 918 MHz; Zoe's turns are shorter and sampled. The +7.6% from the 16K draft head is within noise here (+2.6%, 24.0 vs 23.4).

## Integration cost (honest)

| Zoe needs | Little Gemma |
|---|---|
| OpenAI-compatible HTTP on `:11434` for ~15 callers (Flue sidecar, `zoe_agent`, `gemma_endpoint`, `intent_classifier_llm`, `night_mind`, `memory_digest`, ...) | Unix socket, one connection at a time, raw text lines. The sibling `little-gemma-tools` OpenAI adapter has a 3,500-byte input cap, no tools, no per-request sampling. |
| Tools / tool-call parsing (progressive disclosure changes the tool block per turn) | None. Control tokens pass through, parsing is the client's job. The tool block lives in the system turn, which `-sys` fixes at server start; a per-turn change means re-prefilling 1-2k tokens in the user line. |
| JSON-schema / grammar-constrained output (`ui_compose`, `structural_reader/verifier`, `night_mind`, `voice_scope`) | None. |
| Different temperature per caller on one server (judges at 0-0.1, chat 0.7) | One sampling config per process. |
| `/health`, `/metrics`, `/slots` (deploy gate, `zoe-health`, `measure_*` harnesses, the cache-ram work) | None. |
| Stateless multi-turn (the sidecar resends history) | History lives in the connection's KV; needs a session map. |
| Prompt size | Per user turn <= 4,096 tokens, **and** the line must satisfy `pos + bytes(line) + 64 < 8192` where `pos` already counts the 2,550-token system prefix: a 5,791-byte note was rejected with `context full` and the session killed (measured). Effective single-line cap with Zoe's prompt is ~5.5 KB. Live journal: 11.7% of requests reach > 3,500 tokens total and 3.8% > 4,096. Output cap 1,024 tokens. |
| Prefix cache across requests | Only `-sys`; no LCP cache, no `--cache-ram`. |
| Batching/parallelism | None (Zoe runs `--parallel 1` today for the MTP-leak bug, so this is not a regression, but there is no path to more). |
| Maintenance | One-org project, last commit 2026-09-17, ~9.9k lines we would own; llama.cpp tracks new Gemma/MTP fixes upstream. |

## Findings worth acting on (none applied)

1. **Our own MTP depth may be wrong for chat.** little-gemma shows N=2 > plain > N=4 on Zoe's chat turns; llama-server runs `--spec-draft-n-max 4 --spec-draft-p-min 0.6`. llama.cpp's drafting differs (p-min cutoff), so this does not transfer directly, but `--spec-draft-n-max 2/3` on the live flags is an untested 5-minute A/B (replay-gated, brain-stop window). Candidate for the next brain-flags pass.
2. Brain restore trap: see the open-problems entry (NvMap does not reclaim page cache; a stopped brain restarted into a full cache for 14.5 min).

## Limits of this measurement

- n = 15 prompts x 2 measured passes per arm, one window per arm (two for the two repeated ones); medians and ranges shown, no confidence intervals. Windows differed (window 1 little-gemma prefill half of window 2).
- The sampled arm (`lg-n4-t07`) did not complete: the harness waits for `<turn|>` and the model ended the 1,074-token turn on another end token, so the client timed out and that arm's rows were lost. From little-gemma's own stderr (warmup pass only, 12 turns) sampled N=4 decodes at roughly 20-27 tok/s, in line with greedy N=4; no claim is made beyond that.
- Greedy acceptance rates are each engine's own definition (llama: accepted/drafted tokens; little-gemma: per round) and are not comparable.
- The workload is synthetic in content (Zoe's real system prompt and tools, scripted household-style turns); no household messages were read or stored. Tool-call turns were avoided so the two engines' tool handling cannot confound speed.
- No replay of Jason's voice corpus: this is an engine measurement, not a voice-path change; nothing in `voice_tts.py`/`fast_tiers.py`/brain config was touched.

## Brain downtime actually taken

| window | stop | brain healthy again | note |
|---|---|---|---|
| 1 | 08:02:44 | 08:26:38 | bench ended 08:06:55 (a little-gemma load hit NvMap error 12); the unit then failed to start for 14.5 min until the page cache was dropped in a loop |
| 2 | 09:15:37 | 09:31:16 | 15.6 min; `/health` ok and zoe-data `/health` 200 confirmed |

## Re-run

```
python3 scripts/perf/engine_bench.py prep --out $OUT --repo . --ids16k          # live brain up, read-only
flock /tmp/zoe-voice-harness.lock python3 scripts/perf/engine_bench.py window \
    --out $OUT --lg-bin $LG/run-cuda-i8 --arms lg-n4-greedy,llama-prod,lg-plain-greedy,lg-n2-greedy --budget-min 19
python3 scripts/perf/engine_bench.py summarize --out $OUT
```
