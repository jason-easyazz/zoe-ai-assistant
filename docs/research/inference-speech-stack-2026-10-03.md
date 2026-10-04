---
type: research
title: Inference + speech stack audit vs the field (2026-10-03)
date: 2026-10-03
status: research-only — no code, flag or unit changed by this document
description: Read-only on-box audit of Zoe's brain (llama.cpp b11194, Gemma 4 E4B-QAT + MTP), router head, Moonshine STT, Kokoro TTS, Pi wake/VAD/endpoint and audio transport, compared against llama.cpp upstream and the 2025–2026 field, ending in a measured, ranked action list. The rocks are untouched; every action optimises around them and carries its measurement gate.
---

# Inference + speech stack audit vs the field (2026-10-03)

Scope: what runs on the Orin and the Pi panel today, measured from the live processes, compared
with what upstream and comparable projects do, ranked by expected gain. **The rocks are fixed**
([CANONICAL.md](../CANONICAL.md), [VISION.md](../VISION.md) principle 1): Gemma 4 E4B-QAT + MTP
on llama.cpp, Moonshine v2 Medium, Kokoro. Nothing here proposes swapping them.

Method: read-only. Process command lines, `/props`, `/metrics`, `/slots`, unit files and drop-ins,
the llama-server journal since its last start (2026-09-27 20:00, ~9,870 requests), CMake caches,
GGUF metadata, package versions in each venv, the Pi's env file and audio server (over ssh, read
only). No inference request was sent to the live brain, nothing was restarted, and no harness was
run: MemAvailable was 0.28–0.50 GB during the audit, below every bench's headroom rule. Upstream
facts come from `gh api` against `ggml-org/llama.cpp` and the cited web pages. **[unverified]**
marks a claim I could not confirm at a primary source or on the box. Prior research this builds
on, and does not repeat: [panel-ttfa-breakdown-2026-09-28.md](../knowledge/panel-ttfa-breakdown-2026-09-28.md),
[brain-flags-tuning-2026-09.md](../knowledge/brain-flags-tuning-2026-09.md),
[ecosystem-watch-2026-09-26.md](../knowledge/ecosystem-watch-2026-09-26.md),
[moonshine-0-1-5-upgrade.md](../knowledge/moonshine-0-1-5-upgrade.md),
[b1-speculative-turn-start.md](../architecture/b1-speculative-turn-start.md), program items B1/B5/B6
in [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md).

## 0. TL;DR

1. **The brain's TTFT has a ~135 ms structural floor that no flag sweep so far has touched.** For a
   short suffix (the normal cached voice turn), prompt eval is a step function of the number of
   `llama_decode` calls, not of tokens: 1–2 new tokens = **61 ms**, 5–6 = **116 ms**, 11–31 =
   **~196 ms** (n≈1,400 short requests). The cause is in b11194's server: Gemma 4 is an SWA model,
   so the server splits every prompt at the last user-message start and again 4 tokens before the
   end to create SWA context checkpoints (`server-context.cpp` ~L3460–3640; upstream #24176, #20288).
   Each split is one extra full forward pass (~55–60 ms). `--swa-full` turns checkpoints off
   (`n_swa = 0` → `do_checkpoint = false`). Cost: ~+150 MiB of KV (computed from the GGUF), but
   it also removes the checkpoints that make one cache-ram entry 172 MiB. **Action 1.**
2. **RAM is still the gate.** llama-server holds 7.8 GB RSS, of which **1.51 GiB is the
   `per_layer_token_embd` table, mlocked on the CPU side**. llama.cpp now has `--lazy-mode on`
   (#27794, upstream measurement on this exact model family: −1.2 GB peak RSS, −8 to −11 % decode
   on a desktop GPU) and a Gemma 4 row-prefetch fix after our build (#29599, 2026-09-30). With
   action 1's smaller cache entries, the two together could free ~2 GB, which is the precondition
   for the streaming-STT *benchmark* (≥ 1.5 GB: the in-process replay loads a second Moonshine
   copy; production streaming would reuse the warm singleton) and for the brain-window policy's 2 GB quiet-headroom rule
   (`voice-pipeline.md` brain-window recipe, step 0 — a policy so a replay burst cannot kill the
   running brain; the probe's own floors are 700 MB for `--stt remote` and 1,500 MB in-process,
   `voice_regression_probe.py::resolve_min_mem`). **Actions 3–4.**
3. **The cheapest end-of-speech win is already built**: B1.1 speculative turn-start at 320 ms
   (offline: −320 ms median, −244 ms mean per turn, 14.3 % cancel). Code and offline gate are done;
   what remains is the rest of the flip list in the b1 doc: live Pi proof, a head-bound replay
   PASS, RAM flat across the replay, then an operator-only flag-on panel week. **Action 2.** Smart Turn as a veto was already measured as *not* a win
   ([b1 doc](../architecture/b1-speculative-turn-start.md)) — do not redo it.
4. **Upstream moved under the MTP lane since b11194**: probabilistic drafting + rejection sampling
   for MTP (#27694, merged 2026-10-02; at **0.5 — the temperature Zoe sends** (the Flue lane
   injects it, `capped-completions.ts` `DEFAULT_TEMPERATURE`, matching the `zoe_agent.py` pin;
   `ZOE_BRAIN_TEMPERATURE` is unset live; the server's `--temp 0.7` is only the fallback for
   callers that send none) the author's MTP rows show **+2 to +7 % decode throughput** on Qwen
   models, +4 to +13 % at 0.7), "stop accepting draft tokens at EOG" (#29638), the failed
   restore cleanup (#27530) and the PLE prefetch (#29599). One gated rebuild collects all four.
   **Action 5.**
5. **The nightly instrument was down, and is only half repaired.** `zoe-voice-regression.service`
   timed out three nights running (2026-10-01/02/03, killed at the 30-min start timeout after only
   ~9 s of CPU, no output after the Postgres wait). Cause found and fixed in #1798: a
   `getaddrinfo('zoe.local')` hang (the retired Pi still owns the name), and the unit now forces
   the replay to loopback (`scripts/setup/systemd/zoe-voice-regression.service`). The second
   defect is still open as #1811: the gate runs the probe on `/usr/bin/python3`, not the service's
   py3.12 venv, so recall has been silently off inside every replay since B0.8. Last PASS before
   the outage: 2026-09-30 02:30 (STT 568 ms, brain 2,030 ms, e2e 2,260 ms medians, 19/19). Every
   action below is gated on that harness, so confirming it is step 0.

## 1. What we run today

All values read from the live box on 2026-10-03 unless marked.

### 1.1 Platform

| item | value | source |
|---|---|---|
| Board | reComputer (Seeed) Orin NX 16 GB "Super", 8× A78AE | `/etc/nv_tegra_release`, nvpmodel |
| L4T / JetPack | R36.4.3 image (JetPack 6.2; `nvidia-l4t-core` packages at 36.4.7), driver 540.4.0, CUDA 12.6.68, cuDNN 9.3.0, TensorRT 10.3 | `/etc/nv_tegra_release`, dpkg |
| Power mode | `MAXN_SUPER`; `jetson-clocks-max.service` enabled (runs `jetson_clocks` at boot) | `nvpmodel -q`, systemd |
| GPU clock | devfreq min = max = cur = **1173 MHz** (pinned) | `/sys/class/devfreq/17000000.gpu` |
| CPU governor | `schedutil`, cur 1984 MHz at sample | sysfs |
| EMC clock | not readable as user **[unverified that it is pinned]** — `sudo jetson_clocks --show` | — |
| Memory at audit | MemAvailable **0.28–0.50 GB**, swap used 2.3 GB | `/proc/meminfo` |

### 1.2 Resident memory of the voice path

| process | VmRSS | detail |
|---|---|---|
| llama-server (brain) | **7.81 GB** | RssAnon 5.78 GB (GPU weights copy, KV, compute buffers, MTP, `--cache-ram` up to 2 GiB, slot checkpoints); RssFile 2.03 GB = CPU-side tensors, **VmLck 1.95 GB** (mlock) |
| kokoro-tts | 1.95 GB | mostly CUDA context + torch |
| zoe-data (Moonshine in-process) | 1.10 GB | |
| functiongemma-router | 0.38 GB | CPU |

GGUF tensor sizes (gguf-py read of the live file): transformer blocks **2,118.5 MiB**,
`per_layer_token_embd` **1,512 MiB**, `token_embd` 360 MiB, `per_layer_model_proj` 14.8 MiB.
The PLE table and `token_embd` are input tensors and stay on the CPU side; together they match
RssFile/VmLck.

### 1.3 Brain — `llama-server.service`

| item | value |
|---|---|
| Build | `/home/zoe/llama.cpp-b11194/build-jetson`, b11194 `9f70b2cec` (2026-09-26) |
| CMake | `CMAKE_CUDA_ARCHITECTURES=87`, `GGML_CUDA_FA=ON`, `GGML_CUDA_GRAPHS=ON`, `FA_ALL_QUANTS=OFF` (default list includes `q8_0-q8_0`), `GGML_NATIVE=ON`, OpenMP on |
| Model | `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (4.22 GB) + `mtp-gemma-4-E4B-it.gguf` (60 MB) |
| Arch (GGUF) | 42 blocks, 18 shared-KV layers, SWA window 512, pattern 5 SWA : 1 global, KV heads 2, head dim 256 (SWA) / 512 (global) |
| Spec | `--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-p-min 0.6 --spec-draft-ngl 99` |
| Context | `--ctx-size 8192 --parallel 1` (1 slot), `--cache-type-k q8_0 --cache-type-v q8_0`, `--flash-attn on` |
| Cache | `--cache-ram 2048`, `--ctx-checkpoints` default **32**, `--cache-reuse` 0 (off), `--swa-full` off, `-b 2048 -ub 512` (defaults) |
| Load | `--load-mode mmap+mlock`, `--fit off`, `--n-gpu-layers 99`, threads default (log: `n_threads = 8`), `--poll` default 50 |
| Sampling | `--temp 0.7 --top-k 64 --top-p 0.95`, `--reasoning off`, `--jinja` |
| Unit guards | `MemorySwapMax=0`, `MemoryLow=6G`, `LimitMEMLOCK=infinity`, `CPUWeight/IOWeight=400` |

Measured from `/metrics` and the journal (since 2026-09-27 20:00):

| metric | value |
|---|---|
| Prompt tokens served from cache | 10.12 M cached vs 2.33 M processed → **81 % reuse** |
| MTP | 185,192 drafted, 115,780 accepted → **62.5 %**; 3.05 drafted and **1.91 accepted per draft**; conditional accept by position 0→3: 80 / 62 / 70 / 79 % |
| Decode | lifetime 34.5 tok/s aggregate (190,101 tok / 5,514 s); `/metrics` window 32.7 tok/s; per-request ms/token p10 19 / p50 47 / p90 81 (short replies dominate) |
| Cold prefill | 2,789 tokens in 4.29 s (**650 tok/s**); 2,048–8,192-token bin median 4.10 s |

**Prompt-eval time vs new (uncached) tokens**, 9,870 requests, binned:

| new tokens | n | median ms | reading |
|---|---|---|---|
| 1–2 | 49 | **61** | one decode call |
| 5–6 | 1,356 | **116** | two calls (body + last 4) |
| 11–13 | 259 | **199–214** | three calls (pre-user / user body / last 4) |
| 8–31 (bin) | 1,138 | 196 | |
| 128–255 | 2,598 | 406 | |
| 256–511 | 3,263 | 615 | |
| 2,048+ | 82 | 4,102 | cold prefill |

The step at 2 → 5 → 11 tokens, flat within each step, is the checkpoint split (§2.1). The
2026-09-28 panel breakdown's "llama prompt-eval ~190–220 ms for 17–37 tokens" inside the
**386 ms brain TTFT** is this floor.

### 1.4 Router head — `functiongemma-router.service`

FunctionGemma-270M Q8_0 on **CPU** (`--n-gpu-layers 0 --threads 4`), `--ctx-size 1024
--cache-ram 64 --parallel 1`, `MemoryMax=1G`. Runs the **old b9733 build** (`~/llama.cpp`,
`f449e0553`, 2026-06-20), not b11194. Fine as is (§4); noted because a llama.cpp upgrade plan has
to name both binaries.

### 1.5 STT — Moonshine in zoe-data

`moonshine-voice` **0.1.3** in `~/.zoe/venvs/zoe-data-py312` (ORT 1.23.2 CPU, numpy 1.26.4),
`ZOE_MOONSHINE_ARCH=MEDIUM_STREAMING`, `Transcriber.transcribe_without_streaming` on the whole
clip after the endpoint (`routers/voice_tts.py::_run_moonshine`). Measured live: **0.25 s per
second of clip**, median 0.71 s on panel turns (TTFA breakdown); last nightly probe median
568 ms. 0.1.5 is held for a measured ~3× decoder-step regression on aarch64
([moonshine-0-1-5-upgrade.md](../knowledge/moonshine-0-1-5-upgrade.md) §9).

### 1.6 TTS — Kokoro sidecar

`kokoro` 0.9.4 / `misaki` 0.9.4, torch **2.8.0** (CUDA 12.6, cuDNN 9.3) in `~/.zoe/venvs/kokoro-py310`,
`KPipeline(lang_code="a", device="cuda")`, voice `af_sky`, fp32. `PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync`
(Jetson NVML workaround). **`torch.cuda.empty_cache()` runs before every synthesis**
(`_blocking_synthesize`). Phrase cache (400 hot phrases on disk, RAM LRU). Drop-ins: arena cap,
dedicated venv, `MemoryMax=4G`. Measured: ~0.27 s fixed + length-linear; 305 ms for 20–55 chars,
396 ms for ~100 chars. The ONNX Runtime CUDA backend was measured and rejected (B5.1, #1715).

### 1.7 Panel (Raspberry Pi 5, 8 GB, Bookworm)

| item | value |
|---|---|
| Audio server | **PulseAudio** (not PipeWire), `module-suspend-on-idle` loaded |
| Device | **Jabra Speak 750** (full-duplex, on-board AEC) as default sink + source |
| Wake | openWakeWord 0.6.0, custom `hey_zoe.onnx`, threshold 0.45, confirm 3 in 1.0 s |
| VAD | Silero via `torch.hub` (torch 2.11, cache from 2026-04-14); `silero-vad` 6.2.1 also installed |
| Endpoint | `VAD_ENDPOINT_ENABLED=1`, `ZOE_VAD_TAIL_MS=640`, `VAD_ENDPOINT_SILENCE_S=0.8` → measured 720–880 ms speech-end → close |
| Speculative turn | `ZOE_SPECULATIVE_TURN` unset (off) |
| Clean tail | `ZOE_VAD_CLEAN_TAIL_MS` unset (off) |
| Other | `RECORD_SECONDS_MAX=12`, `POST_PLAY_COOLDOWN_S=0.4`, barge-in on (threshold 0.75), speaker-ID on (shadow scoring off the critical path since #1760), `ZOE_VOICE_STREAM=1` |
| Transport | HTTP `POST /api/voice/turn_stream` with the whole WAV; reply is streamed b64 WAV per sentence. The LiveKit lane (`voice_livekit.py`, Smart Turn v3.2 at the silence window) is on-demand and not what the panel uses. |

## 2. What the field does differently

### 2.1 llama.cpp: what upstream has that we do not use, and what changed after b11194

**SWA checkpoints are the TTFT floor.** For a model with `n_swa > 0`, b11194's server creates
SWA context checkpoints during prompt processing. To do that it ends a batch (a) at the start of
the last user message (`pos == last_user_pos` always breaks), (b) at the start of other user
messages past `--checkpoint-min-step`, and (c) at `4 + n_ubatch` and `4` tokens before the end
(PR #20288, 2026-03-10: "make 2 checkpoints near the end of the prompt"). Every break is a separate
`llama_decode`, so the weight read is paid again. Upstream users hit the same mechanism at large
scale (#25320, #25213: prompts processed in per-message chunks; maintainer advice: raise
`--checkpoint-min-step` or disable checkpointing). For Zoe's short cached suffix it costs two extra
forward passes. `--checkpoint-min-step` cannot remove the last-user and last-4 breaks;
`--swa-full` does: it sets `n_swa = 0` in the server, and `do_checkpoint` requires `n_swa > 0`
(or a model that cannot partially remove sequence tails, which Gemma's iSWA cache can).

*Memory of `--swa-full`, computed from the GGUF.* 24 layers own KV (42 − 18 shared): 20 SWA,
4 global. One SWA layer at q8_0 = 2 × 2 heads × 256 × 34/32 B = 1,088 B/token, so 20 layers =
21.3 KiB/token. At ctx 8192 the SWA cache grows from ~1,024 cells (~21 MiB) to 8,192 (~170 MiB):
**≈ +150 MiB** on the GPU side. In exchange, the checkpoints go: a slot keeps up to 32 × 10.6 MiB
(≈ 340 MiB), and a cache-ram entry for a 2,788-token turn falls from the measured 172 MiB
(13 checkpoints, brain-flags-tuning §1) to ≈ 81 MiB (2,788 × (21.3 + 8.5) KiB). The earlier
"swa-full = 30 → 1,536 MiB" figure in [brain-speed-tuning.md](../architecture/brain-speed-tuning.md)
§2 was measured on E2B at a much larger context; at ctx 8192 + q8_0 on E4B the number is an order of
magnitude smaller **[computed; applied and probe-measured 2026-10-04 23:43-23:51: 18/18 OK, no latency regression, brain 1586 ms / e2e 1775 ms; then `--cache-ram 1024` on top: 1491 / 1813 ms, MemAvailable ~2.0 -> 4.4 GB]**. `--swa-full` also re-enables `--cache-reuse`
(the iSWA `get_can_shift()` size check passes), which stays optional. Two load-bearing places still
carry the E2B-era figure as the reason the flag is off: the comment block in
`scripts/setup/systemd/llama-server.service` ("~50x SWA cache growth … unaffordable") and the
prompt-prefix rule in `services/zoe-data/AGENTS.md`. The AGENTS.md sentence is qualified in this
change (measured on E2B at a larger context; computed ≈ +150 MiB on the live E4B at ctx 8192 + q8_0,
unmeasured — the omission stands until a brain window measures it). The unit comment is a
voice-path file (`voice_gate_check.py` pattern) and is deliberately NOT touched here: the action-1
PR that flips the flag is replay-gated and must rewrite that comment with the *measured* number.

**Lazy per-layer embeddings.** `--lazy-mode on` (#27794, merged 2026-08-27; renamed in #27969)
reads PLE rows from the mmap on demand instead of keeping the table resident. The PR author
measured it on `gemma-4-E4B-it` Q4_K_M: peak RSS **7.37 → 6.16 GB**, decode **105.8 → 94.2–97.3
tok/s (−8 to −11 %)**, prefill 571–583 → 514–563 tok/s. `auto` only applies above 4 GiB, so our
1.5 GiB table is fully resident and mlocked today. **#29599** (merged 2026-09-30, after b11194)
adds `madvise` prefetch of PLE rows for Gemma 4 and roughly doubles lazy-mode prefill on the
reported hardware. Jetson numbers: none published **[unverified]**. The open question for Zoe is
whether lazy pages interact badly with the "voice stack must never page" rule: they are clean
file pages, so under pressure the kernel drops them and the next use is a major fault from NVMe.

**Speculative decoding after b11194.**
- **#27694** (merged 2026-10-02): `--spec-draft-sampling probabilistic` makes the MTP drafter sample
  and the target verify by rejection sampling (Leviathan et al.), preserving the output
  distribution. Default stays `greedy`. Author's sweep (11 temperatures 0.0–1.0, two machines),
  MTP rows **at 0.5 — the temperature Zoe actually sends** (Flue lane `DEFAULT_TEMPERATURE`, matching
  the `zoe_agent.py` pin; `--temp 0.7` on the server is only the fallback): Qwen3.5-4B
  **+4.7 % accepted length / +5.5 % throughput** (RTX 5090; +6.5 % / +6.8 % on DGX Spark),
  Qwen3.5-9B +5.5 % / +4.3 %, Ornith-1.5-9B +2.8 % / +2.4 %, Qwen3.6-27B +3.1 % / +3.4 %,
  Qwen3.6-35B-A3B +5.2 % / +4.5 %. The 0.7 rows are higher (4B +5.9 % / +5.2 %, 9B +13.3 % /
  +12.5 %) but are not Zoe's operating point. So the Qwen evidence at 0.5 is **+2 to +7 %
  throughput**; the Gemma 4 E4B transfer is **unmeasured** **[unverified for E4B]**.
- **#29638** (merged 2026-09-29): stop accepting draft tokens after an EOG. Before it, accepted
  tokens past the stop token stayed in the slot, so `f_keep` read < 1.000 and hybrid models
  re-processed the whole previous answer (#28049). On Gemma's iSWA cache a 1–2-token tail is
  removable, so the cost here is small **[inference]**; it is still a correctness fix on our lane.
- **#27530** (merged 2026-09-26): cleans K/V after a failed state restore — relevant because
  `--cache-ram` restores on every turn.
- Still open: **#29467** (abort when a request fails under memory pressure with prefix reuse —
  the one regression class to heed on a 16 GB box), **#27489** (MTP's duplicate compute arena).

**Already in b11194 and on:** CUDA graphs (`GGML_CUDA_GRAPHS=ON`, journal shows ~25k graph
reuses), FA on with q8_0 K/V (#25148), SM87 MMQ crossover (#28285), Gemma 4 FA tuning (#29152),
CUDA graph for the MTP draft (#28549).

**Not available on Tegra:** zero-copy weights. ggml-cuda forces `integrated = false`
("Temporarily disabled due to issues with corrupted output (e.g. #15034)", `ggml-cuda.cu` L308),
so the 2.1 GiB of block weights is copied into CUDA memory rather than used from the page cache.
`GGML_CUDA_ENABLE_UNIFIED_MEMORY` exists but switches to `cudaMallocManaged`, which does not
remove the copy. No upstream fix is in flight (#25384, #27311 are about memory reporting and an
AMD ring buffer). Do not spend time here.

**Measured elsewhere on Orin-class hardware.** NVIDIA forum (2026-06-06, cortexist fork): Gemma 4
E4B Q4_K_M on Orin NX ~13 tok/s without MTP → ~18 (sometimes 20+) with MTP
([forum](https://forums.developer.nvidia.com/t/llama-cpp-mtp-lifted-llm-performance-of-jetson-orin/372493)).
Zoe's live 25–35 tok/s on the same module is already well above the published figures, so the
decode path is not where the field is ahead.

### 2.2 STT (Moonshine — rock) — streaming during recording

- **Upstream state.** PyPI `moonshine-voice` is still 0.1.5 (2026-08-24); nothing has merged to
  `main` since. A `dev-v0.1.6` branch is 8 commits ahead with the **#229 fix** (Silero state no
  longer leaks across concurrent streams, `4d38510f`, 2026-09-30) and the close-while-streaming
  crash fix (#223), both unreleased ([compare](https://github.com/moonshine-ai/moonshine/compare/main...dev-v0.1.6)).
- **Fixes 0.1.3 does not have** (in 0.1.5, which is held for speed): **#218**, where medium-streaming
  logged "Memory is empty" and *dropped hypotheses* when fed short chunks quickly (closed
  2026-08-21), and **#216/#217**, ~83 MB left mapped per closed file-backed `Transcriber`
  ([#218](https://github.com/moonshine-ai/moonshine/issues/218),
  [#216](https://github.com/moonshine-ai/moonshine/issues/216)). Today's batch call path does not
  hit either. A streaming lane on 0.1.3 would hit #218. That gives streaming STT a version
  dilemma: 0.1.3 lacks the fix, and 0.1.5 has the ~3× decoder-step regression.
- **What streaming buys, per upstream.** The Moonshine v2 paper (arXiv 2602.12241, 2026-02-12)
  uses a sliding-window encoder (80 ms lookahead). TTFT is "largely independent of utterance
  duration", and Medium's response latency on an M3 is 258 ms
  ([paper](https://arxiv.org/abs/2602.12241)). The README measures end-of-speech → final
  transcript for Medium Streaming at **269 ms on Linux x86 and 802 ms on a Raspberry Pi 5**. The
  Orin's A78AE cores sit between those **[no Orin figure published]**. Zoe's batch path is
  0.25 s per second of clip (0.71 s median, 2.0–2.2 s on 8–10 s clips). The gain is largest on
  exactly the turns that hurt.
- **The API detail that decides the gain** (0.1.3 source, `core/transcriber.cpp`). Speculative
  decoding verifies the previous hypothesis in one batched pass and decodes only the divergent
  tail. So `update_transcription()` has to run *during* recording. A lane that only calls
  `add_audio()` and then `stop()` gets an incremental encoder but a full greedy decode at the end.
  Older streaming `.ort` files (before 2026-08-23) silently drop samples on chunks that are not
  multiples of 1,280 samples (`5a82d123`). The daemon's `CHUNK_SIZE=1280` already meets that.
- **Runtime.** Moonshine is CPU-only by design: its fused `com.microsoft` ops fragment any
  compiling execution provider. The only threading knob is `MOONSHINE_ORT_SINGLE_THREAD`, which
  was measured slower ([moonshine-0-1-5-upgrade.md](../knowledge/moonshine-0-1-5-upgrade.md) §9).
  There is no GPU or ORT-GPU route for the rock, and the cp312 zoe-data venv has no Jetson
  `onnxruntime-gpu` wheel anyway (jp6/cu126 serves cp310 only). The lever is the streaming
  *pattern*, not the runtime.

### 2.3 TTS (Kokoro — rock) — first chunk and resident size

- **Upstream is dormant.** `kokoro` 0.9.4 and `misaki` 0.9.4 (both 2025-04-05) are still the
  latest. The last commits were 2025-08-06 and 2025-08-11. The half-precision checkpoint request
  is unanswered ([#230](https://github.com/hexgrad/kokoro/issues/230)). No weights after 2025-04.
- **First-chunk tricks.** `KPipeline` yields one result per `split_pattern` segment (default
  `\n+`). Clause streaming is the evidence-backed trick (kokoro-pi: "first audio ~6× sooner on
  CPU"; Kokoro-FastAPI: ~300 ms first chunk on GPU), with more intonation seams as chunks get
  smaller ([kokoro-pi](https://github.com/zreecespieces/kokoro-pi),
  [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)). **Zoe already does this one level
  up**: `_extract_first_unit` hands Kokoro one sentence or clause at a time
  (`_FIRST_UNIT_CLAUSE_MIN = 60`). The remaining step is a shorter first clause (TTFA item 5).
- **torch.compile / CUDA graphs / fp16.** torch.compile on Kokoro was blocked by a data-dependent
  SDPA mask until PyTorch PR #150403 (issue closed 2025-06-18)
  ([#149570](https://github.com/pytorch/pytorch/issues/149570)). Nobody has published a speedup,
  on Jetson or elsewhere. The duration predictor's variable-length output means CUDA graphs need
  shape bucketing, and Inductor compilation costs RAM the box does not have **[inference]**. No
  measured fp16/bf16 numbers exist for PyTorch Kokoro. Both are unproven bets, not actions.
- **TensorRT.** `jetson-voice-engine` (pushed 2026-09-11) reports that the full Kokoro graph does
  not build as one TRT 10.3 engine (STFT and dynamic shapes). Its hybrid TRT+ORT gets **RTF
  0.54–0.55 on an Orin Nano** ([repo](https://github.com/suharvest/jetson-voice-engine)), slower
  than Zoe's PyTorch CUDA path. jetson-containers' `kokoro-tts-*` packages publish no numbers.
- **Resident size.** The live sidecar is 1.95 GB VmRSS, of which **RssFile is 12 MB**. The
  libraries are not what is resident; it is anonymous heap plus device allocations. torch 2.8
  already enables `CUDA_MODULE_LOADING=LAZY` (the string is set in `libtorch_cuda.so`; NVIDIA has
  defaulted to it since R535). Under `backend:cudaMallocAsync`, which is required on Jetson for the
  NVML assert, PyTorch documents `garbage_collection_threshold` / `max_split_size_mb` /
  `roundup_power2_divisions` as ignored. **No env-knob shrink is available.** The only
  memory lever with Zoe evidence (ONNX CUDA, −0.5–1 GB) was rejected on quality and speed.
- **One on-box oddity worth a measurement, not a claim.** `_blocking_synthesize` calls
  `torch.cuda.empty_cache()` before *every* synthesis (added for a first-request OOM after
  warm-up). With the async allocator that returns the pool to the driver, so every turn
  re-allocates. Nobody has measured how much of the ~0.27 s fixed cost is allocation versus misaki
  G2P versus the forward pass, and that split decides which lever is worth anything.

### 2.4 Panel: wake, VAD, turn-taking, echo, audio server

- **Wake.** openWakeWord's last release is still 0.6.0 (2024-02-11); commits continue (Python
  3.10+, `ai-edge-litert`, 2025-10/12) ([releases](https://github.com/dscripka/openWakeWord/releases)).
  microWakeWord now runs on a Pi: `pymicro-wakeword` 2.5.0 (2026-09-17) ships Linux ARM64 wheels,
  10 ms frames ([PyPI](https://pypi.org/project/pymicro-wakeword/)); ESPHome documents the v2
  models at ≤ 0.16 false accepts/hour on DiPCo ([docs](https://esphome.io/components/micro_wake_word/)).
  Zoe uses a custom `hey_zoe` model, so switching means retraining. That is worth doing only if the
  live false-accept or false-reject rate is a problem. Wake latency is not on the TTFA path.
- **VAD.** Silero v6.0 (2025-08) → v6.2 (2025-12-10; better on child, muffled and phone-quality
  voices) ([version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models)).
  A third-party comparison puts Silero v6.2 at 97.3 % F1 with 132 ms onset / 333 ms offset, ahead
  of TEN VAD (95.1 %, 358 ms offset) ([voxrt](https://voxrt.com/vad-comparison), undated,
  directional only). The Pi loads whatever `torch.hub` cached on 2026-04-14 (v6.x) through torch.
  The server side already pins v6.0 ONNX with the 64-sample context fix. Nothing to chase.
- **End of turn.** Pipecat runs Silero with `stop_secs=0.2`, then Smart Turn v3.x on the whole
  turn, with a 3 s fallback ([docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview)).
  Smart Turn v3.2 (2026-01-07) is ~8 MB int8, 12.6 ms on x86 and 59.8 ms on ARM cloud cores
  ([Daily](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/),
  [v3.2](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/)).
  LiveKit Agents defaults to `min_endpointing_delay` 0.5 s / max 3.0 s (0.3 / 2.5 s with its audio
  turn detector), **`preemptive_generation` on by default** (the LLM starts on the final transcript
  before the turn is confirmed), and `false_interruption_timeout` 2.0 s with resume
  ([tuning](https://docs.livekit.io/agents/logic/turns/tuning/)). Its new audio Turn Detector v1 /
  v1-mini (2026-06-17) is under the LiveKit Model License
  ([blog](https://livekit.com/blog/solving-end-of-turn-detection)).
  *Zoe already has the PIECE that matters*: B1.1 is LiveKit's preemptive generation, adapted to the
  panel's HTTP lane. Zoe also measured Smart Turn as a veto on the panel corpus: not a win at
  ~63 ms per score on the Orin
  ([b1 doc](../architecture/b1-speculative-turn-start.md)). The borrowable piece left is LiveKit's
  **false-interruption resume**: pause TTS on barge-in and resume if nothing is transcribed within
  ~2 s. This is a UX item, not latency.
- **Echo.** The Jabra Speak 750 is full-duplex with on-board AEC
  ([Jabra Speak2 / 7x0 specs](https://www.jabra.com/_/media/Jabra_VXi_Product-Documentation/Jabra-Speak2-75/Technical-specifications/RevC/EN-Speak2-75-Tech-Spechs-180924.pdf)),
  and TTS goes out through the same USB device, so the hardware reference is correct. Software AEC
  (`module-echo-cancel aec_method=webrtc`, PipeWire `libspa-aec-webrtc`, webrtc-audio-processing
  2.1) would cancel against an already-cancelled path. Do not stack it **[inference; no vendor
  guidance found]**.
- **Audio server.** Pi OS Bookworm defaults to PipeWire; this panel runs PulseAudio, which is fine.
  One relevant knob: `module-suspend-on-idle` is loaded, so the Jabra sink suspends after idle and
  the first reply of a conversation pays the USB/ALSA resume. The TTFA breakdown measured
  aplay + Pulse sink start at **70–90 ms** with 64 ms sink latency, but it did not separate a
  suspended sink from a running one. PipeWire's equivalent is WirePlumber's
  `session.suspend-timeout-seconds` (default 5 s;
  [note](https://davejansen.com/disable-wireplumber-pipewire-suspend-on-idle-pops-delays-noise/)).

### 2.5 Horizon: full-duplex / audio-native (no swap proposed)

- **Kyutai Moshi**: full-duplex speech-to-speech, 160 ms theoretical / ~200 ms practical
  ([arXiv 2410.00037](https://arxiv.org/abs/2410.00037)). Kyutai STT 1B has a 0.5 s delay with
  semantic VAD; TTS 1.6B and Unmute were open-sourced 2025-07-03 ([kyutai.org/stt](https://kyutai.org/stt/),
  [repo](https://github.com/kyutai-labs/delayed-streams-modeling/)). All sized for datacentre GPUs.
- **Sesame CSM-1B** (Apache 2.0, 2025-03-13), Mimi codec at 80 ms frames; ~250 ms first audio on
  an RTX 4090 **[unverified, secondary sources]** ([HF](https://huggingface.co/sesame/csm-1b)).
- **NVIDIA Nemotron Speech ASR 0.6B** (2026-01-05), cache-aware streaming with chunk latency
  80–1,120 ms, median time-to-final 24 ms
  ([HF blog](https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents)). That is
  the streaming pattern Moonshine's `MEDIUM_STREAMING` already offers, which is the point of §2.2.
- **Audio-native Gemma — the rock itself.** Gemma 4 E2B/E4B take up to 30 s of audio through a
  ~300M conformer ([Google](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/));
  llama.cpp merged Gemma 4 audio input in #21421 (2026-04-12, non-streaming, BF16 mmproj
  recommended) ([PR](https://github.com/ggml-org/llama.cpp/pull/21421)). This stays B5.5
  (paralinguistics on flagged turns), after the RAM actions below. On the Orin it also needs
  `-DGGML_CUDA_NO_VMM=ON` (#29142: the 32 GB VMM reserve fails on the iGPU for multimodal).

## 3. Ranked action list

Gain is user-perceived (end of speech → first sound) or resident MB. "Rock-safe" = same model
files, same weights, same voices. Every voice-path row is replay-gated against
`~/.zoe-voice-samples` under `flock /tmp/zoe-voice-harness.lock`
([voice-pipeline.md](../knowledge/voice-pipeline.md)); brain rows use the brain-window recipe in
[brain-flags-tuning-2026-09.md](../knowledge/brain-flags-tuning-2026-09.md) (one flag vs the live
set, same-session control, Kokoro paused for headroom).

**Step 0 (prerequisite, not ranked): confirm the nightly instrument is back, on the right
interpreter.** The three-night timeout (2026-10-01/02/03) is diagnosed and fixed — #1798, see
`docs/knowledge/incident-runbook.md` ("Fixed in #1798"): the replay no longer resolves
`zoe.local`, and the unit pins `ZOE_REPLAY_BASE_URL` to loopback. Do not re-investigate it.
What is still required before any row below can cite the harness as its gate:
(a) one nightly run that lands a trend entry (first candidate: the 04:30 run after the live unit
was reinstalled from the repo); (b) #1811 merged and the unit reinstalled, so the probe runs on
the service's py3.12 venv (today it runs on `/usr/bin/python3`, where recall is silently off);
(c) a fresh baseline recorded on that interpreter (`--update-baseline`, Kokoro paused; the
probe's floor is 700 MB for `--stt remote`, 1,500 MB in-process — pausing Kokoro frees ~1.9 GB,
which clears it) — the existing bar was recorded with recall off, so drift against it is not
comparable. Two separate memory rules apply and should not be conflated: the probe's mode-specific
floor above, and the brain-window policy's ≥ 2 GB quiet headroom (`voice-pipeline.md`, step 0 of
the brain-window recipe), which exists so a replay burst cannot kill the running brain. At
MemAvailable 0.3–0.5 GB with Kokoro up, the box is below the 700 MB floor for the nightly (which
does not pause Kokoro) and below the brain-window policy; action 3 is what buys durable headroom. Effort: operator steps + one merge. Rock-safe: n/a.

| # | change | expected gain | risk | rock-safe? | how to measure (harness + gate) | effort / kind |
|---|---|---|---|---|---|---|
| 1 | **`--swa-full` on the brain** (no SWA checkpoints → no split prefill) | **−120 to −135 ms brain TTFT per voice turn**: an 8–31-token suffix drops from ~196 ms (3 decode calls) to ~61 ms (1 call). Also ~−110 ms on every 128–511-token tool-round suffix (2 fewer forward passes). RAM: ≈ +150 MiB KV, −up to 340 MiB of slot checkpoints, cache-ram entries ~−50 % (172 → ≈ 81 MiB for a 2.8 k-token turn) | Medium. Changes cache invalidation (SWA rollback becomes free, no checkpoint restore). SWA layers attend over a full-size cache: estimated ≤ 3 % decode cost at ~3 k context **[inference]**. #29045 (iSWA `seq_pos_max`) is open upstream | Yes, flag only | Brain window, one flag vs the live set, same-session control. (a) prompt-eval-vs-new-tokens bins from the journal (recipe in the appendix): success = the 5–31-token bins collapse to the 1–2-token step (~61 ms). (b) `VOICE TIMING brain_ttft_ms`. (c) decode ms/token is flat. (d) RssAnon at load and after 24 h. (e) replay PASS: said-vs-did not regressed, brain median not slower. Negative control: flag off restores the 116/196 ms steps | Flag (1 line in `scripts/setup/systemd/llama-server.service`) + 1 brain window |
| 2 | **Flip B1.1 speculative turn-start at 320 ms** (`ZOE_SPECULATIVE_TURN`, Pi + server) | **−320 ms median, −244 ms mean** per turn, end of speech → POST (offline over 1,171 corpus clips; 14.3 % cancel) | Medium. The live cancel rate is unknown, and a cancelled turn spends one STT + a brain start | Yes | The full flip list in the B1.1 runbook ([b1 doc](../architecture/b1-speculative-turn-start.md), "Flip criteria"), in order, any miss keeps it dark: (1) offline cancel estimate < 30 % with ≥ 250 ms median saving (met); (2) Phase 2 landed (met, #1742); (3) replay PASS bound to the head under the harness flock; (4) RAM flat — `mem_available_mb` before/after the replay within noise; (5) an operator-only live panel week with the flag on at both ends: cancel share < 30 %, `endpoint_wait_delta_ms` ≥ 250 ms median on committed turns, zero double-speak, zero duplicate writes, zero said-X-did-Y | Flag (built, flag-dark) |
| 3 | **Free RAM around the brain: (a) re-size `--cache-ram` after #1; (b) `--lazy-mode on` for the PLE table on a build with #29599** | (a) Entries halve, so 2048 MiB holds ~2× the entries (fewer ~4 s cold misses), or 1024 MiB keeps today's coverage for **−1 GiB**. (b) **−1.2 to −1.45 GB RSS** (1,512 MiB table mlocked today; upstream measured −1.2 GB peak on E4B) | (a) Low once #1 is measured. (b) Medium-high: upstream desktop decode −8 to −11 % before the prefetch fix (≈ +1–3 ms/token here **[unverified on Orin]**). Lazy pages are evictable, which breaks the "brain never pages" property: under pressure a cold row is an NVMe major fault on the hot path | Yes | (a) 24 h `-lv 4` `cache state` occupancy + hit rate, chat T2/T3 TTFT (brain-flags-tuning §1 method). (b) Brain window on the new build: decode ms/token, cold prefill tok/s, `majflt` of the llama-server PID across a replay (`/proc/<pid>/stat` field 12), RssFile/VmLck before and after, replay PASS. Run (b) under a deliberately loaded box too, because that is when the fault cost shows | (a) flag. (b) upgrade (#5) + flag |
| 4 | **Streaming STT during recording** (Moonshine `create_stream` / `add_audio` / `update_transcription`, chunked upload lane) | **Up to ≈ −0.44 s median, up to −1.7 to −1.9 s on 8–10 s turns — an UPPER BOUND, unverified on Orin.** Arithmetic: live batch is 0.25 s/s of clip (0.71 s median, 2.0–2.2 s on 8–10 s clips); streaming makes finalize near-constant, and the only published end-of-speech → final figures for Medium Streaming are 269 ms (x86) and 802 ms (Pi 5). The Orin lands somewhere in that span, so the median outcome ranges from ≈ −0.44 s (x86-like) to a slight regression (Pi-5-like); the long-turn gain holds in both cases. Treat the number as a ceiling until the in-process measurement in the next column exists | Medium-high. Transport change; streaming vs batch transcripts can differ; **0.1.3 lacks the #218 hypothesis-drop fix, 0.1.5 is 3× slower per decoder step** (wait for `dev-v0.1.6`, or measure #218 on 0.1.3 first); **memory: the 1.5 GB floor belongs to the in-process BENCHMARK** (`measure_voice.py --stt inprocess` loads a second Moonshine copy; the TTFA doc's bench OOM'd at 650 MB), not to production — the live path already holds one warm singleton transcriber (`voice_tts.py`) and streaming would reuse it; its incremental stream-state memory is **unmeasured** and must be measured before #3 is treated as a prerequisite for the feature itself | Yes, same model, different call pattern | First measure finalize latency in-process when ≥ 1.5 GB is free (benchmark floor: second Moonshine copy; the TTFA doc's bench OOM'd at 650 MB), recording the stream-state RSS delta on the singleton at the same time. Then replay gate on said-vs-did with the corpus fed in 1,280-sample chunks, calling `update_transcription` every 0.5 s. STT stage must not regress on short clips | Code (daemon + new zoe-data ingest route) |
| 5 | **Rebuild llama.cpp at current master (≥ b11377), then a separate one-flag window for `--spec-draft-sampling probabilistic`** | Probabilistic MTP, author's sweep **at 0.5, the temperature Zoe sends** (Flue lane injects it; the server's `--temp 0.7` is a fallback only): **+2 to +7 % decode throughput** on the Qwen/Ornith MTP rows (Qwen3.5-4B, the closest size: +5.5 % on RTX 5090, +6.8 % on DGX Spark). On a ~2 s brain turn that is ≈ **−40 to −130 ms reply duration, −10 to −30 ms first sentence** if it transfers; the Gemma 4 E4B transfer is **unmeasured** **[unverified on E4B]** and the one-flag window below measures it at the live 0.5. Also #29638 (EOG draft overhang), #27530 (failed-restore cleanup), #29599 (enables #3b) | Medium-high. 183 commits, both binaries (brain + router head) to consider, #29467 (abort under memory pressure + prefix reuse) still open. The flag changes sampling *mechanics* but preserves the output distribution | Yes | The B0.4 rebuild recipe in [voice-pipeline.md](../knowledge/voice-pipeline.md) ("Brain build + flags"): same flags first, replay PASS, decode/prefill parity. Then the flag alone: MTP accept rate from `/metrics`, decode ms/token, replay | Upgrade + flag |
| 6 | **Shorter first speakable unit** (≥ ~24 chars at `,;:` instead of `_FIRST_UNIT_CLAUSE_MIN = 60`) | −0.2 to −0.4 s first audio (TTFA item 5; unblocked by #1761) | Prosody seams: every split is a standalone Kokoro utterance | Yes | Neither the replay gate (stops before TTS) nor `measure_tts.py` (times sidecar synthesis on a complete reply) measures this. Gate = the live first-unit wait: the Pi-log/zoe-data join in [panel-ttfa-breakdown-2026-09-28.md](../knowledge/panel-ttfa-breakdown-2026-09-28.md) (`first-unit wait` column = brain TTFT → first Kokoro completion, ≥ 10 panel brain turns before/after, same session); success = first-unit wait drops by the claimed 0.2–0.4 s with TTFA moving with it, plus replay PASS (said-vs-did unchanged) and an ear check on the corpus replies for seam prosody | Code (small) |
| 7 | **Kokoro: split the fixed cost, then test dropping per-call `empty_cache()`** | Unknown until split. The ~0.27 s fixed cost is some mix of allocation, misaki G2P and forward. Removing `empty_cache` is est. 0–30 ms **[unverified]** | Low for timers. Medium for `empty_cache` removal: it was added for a first-request OOM | Yes | Add G2P / forward / alloc timers (log-only). Then ABAB with `measure_tts.py` under the harness lock: synth p50/p95, VmRSS, zero OOM retries over the corpus | Code (small) |
| 8 | **Keep the Jabra sink open on the Pi** (exempt it from `module-suspend-on-idle`) | ≤ 70–90 ms on the first reply after ≥ 5 s idle **[the suspended vs running split is unmeasured]** | Low. Possible idle hiss or power draw | Yes | The TTFA doc's `aplay -D pulse` 0.2 s silence bench, suspended vs running, 10 reps each. Then the daemon `TTFA` + sink start on live turns | Config |
| 9 | **`-ub` sweep for cold prefill** (512 → 1024/256) | Cold prefill 650 tok/s → unknown; a cold miss costs ~4.1 s (2/14 turns after #1725) | Low. A larger ubatch raises the compute buffer | Yes | `llama-bench -p 512,2048 -ub 256,512,1024` in a brain-stop window (needs a second model load), then a one-flag replay window | Flag |
| 10 | **Barge-in false-interruption resume** (LiveKit pattern: pause, resume if nothing is transcribed within ~2 s) | UX, not latency: fewer lost replies on a cough or TV | Low-medium | Yes | Barge-in log (`t+<ms>`, #1765) on live turns; count resumes vs true interruptions | Code (daemon) |

**Stacking.** #1, #2 and #6 are independent: ~0.13 + ~0.24 + ~0.3 s ≈ 0.7 s off the median turn.
#3 → #4 is the RAM chain. Streaming STT (#4) is the largest *candidate* gain left, but only as an
unverified upper bound (≤ ~0.44 s median, ≤ ~1.8 s on long turns; the Orin figure could be a slight
median regression — row 4), so it must not be prioritised on that number until the in-process
measurement exists. What #1 + #3 unblock is that *benchmark* (second Moonshine copy); whether the
production feature needs any of that headroom is unmeasured (it reuses the warm singleton).

## 4. Already right — do not touch

- **MTP depth** `--spec-draft-n-max 4 --spec-draft-p-min 0.6`. A 14-run 3×3 grid found nothing
  better (#1728). Live acceptance is 62.5 % with 1.91 tokens per draft.
- **FA on + q8_0 K/V** (B0.4, #25148). `q8_0-q8_0` is in the default compiled FA quant list, so
  the pair hits a real kernel.
- **`--ctx-size 8192`, `--parallel 1`** (p99 prompt + reply 3,280 tokens; `-np > 1` with
  draft-MTP still has #28286 cross-slot contamination upstream).
- **`--cache-ram` stays on.** 0 cost +4.1 s on repeat chat turns. Only the size moves, and only
  after #1.
- **mlock + `MemorySwapMax=0`** on every voice unit. Lazy PLE (#3b) is the one deliberate,
  measured exception it would make.
- **CUDA graphs on, GPU pinned at 1173 MHz by `jetson-clocks-max.service`, MAXN_SUPER.** Decode
  (25–35 tok/s) is already above the published Orin NX figures (13 → 18 tok/s with MTP).
- **No zero-copy / unified-memory experiments.** Upstream disables the integrated-GPU path for
  corrupted output (`ggml-cuda.cu` L308).
- **Moonshine 0.1.3 batch path and its pin.** 0.1.5 is held for a measured regression. Change it
  only together with #4.
- **Kokoro on PyTorch CUDA.** ONNX CUDA was measured and rejected (B5.1, #1715). Do not spend RAM
  on torch.compile/TRT/fp16 without someone's measurement first. Allocator env knobs are no-ops
  under `cudaMallocAsync`.
- **Smart Turn as a panel veto.** Measured not a win: at 320 ms the veto cut the mean saving per
  turn from 244 ms to 140–174 ms (63 ms per score) and barely moved cancels (14.3 → 13.5 %).
  The LiveKit lane keeps it.
- **Silero v6 with the 64-sample context** (server) and the Pi's v6 torch-hub copy. The VAD stage
  is pinned by the probe (`model md5 00bdd414…`).
- **Jabra Speak 750 hardware AEC.** No software AEC stacked on top.
- **openWakeWord `hey_zoe`.** Change it only for a measured false-accept/reject problem; wake is
  not on the latency path.
- **The router head on CPU (b9733).** 270M at Q8_0 on 4 threads keeps the GPU for the brain.
  Rebuild it only as part of #5, for one toolchain.

## 5. Sources

On-box (2026-10-03, read-only): `ps`/`/props`/`/metrics`/`/slots` on :11434 and :11436;
`~/.config/systemd/user/{llama-server,functiongemma-router,kokoro-tts,zoe-data}.service{,.d/*}`;
`journalctl --user -u llama-server` since 2026-09-27 20:00; `~/llama.cpp-b11194/build-jetson/CMakeCache.txt`;
b11194 source (`tools/server/server-context.cpp`, `common/common.cpp`, `src/llama-model-loader.cpp`,
`ggml/src/ggml-cuda/ggml-cuda.cu`); GGUF metadata via `gguf-py`; venv package lists; `/proc/<pid>/status`;
`/etc/nvpmodel.conf`; devfreq sysfs; `zoe-pi` over ssh (`pactl`, `.env.voice`, venv `pip list`).

llama.cpp (GitHub, dates are merge/creation):
- Releases b11375–b11377, 2026-10-03 — https://github.com/ggml-org/llama.cpp/releases
- #20288 two checkpoints near prompt end (2026-03-10) — https://github.com/ggml-org/llama.cpp/pull/20288
- #25320 / #25213 per-message prompt chunking (2026-07) — https://github.com/ggml-org/llama.cpp/issues/25320
- #27794 lazy tensor read, E4B numbers (2026-08-27) — https://github.com/ggml-org/llama.cpp/pull/27794
- #28160 lazy-mode auto regression on iGPU (closed) — https://github.com/ggml-org/llama.cpp/issues/28160
- #29599 PLE prefetch for Gemma 4 (2026-09-30) — https://github.com/ggml-org/llama.cpp/pull/29599
- #27694 probabilistic draft + rejection sampling for MTP (2026-10-02) — https://github.com/ggml-org/llama.cpp/pull/27694
- #29638 stop accepting draft tokens at EOG (2026-09-29) / #28049 — https://github.com/ggml-org/llama.cpp/pull/29638
- #27530 K/V cleanup after failed restores (2026-09-26) — https://github.com/ggml-org/llama.cpp/pull/27530
- #29467 rollback failed request's batch (open) — https://github.com/ggml-org/llama.cpp/pull/29467
- #27489 MTP compute-buffer reuse (open) — https://github.com/ggml-org/llama.cpp/pull/27489
- #29045 iSWA seq_pos_max (open, 2026-09-17) — https://github.com/ggml-org/llama.cpp/issues/29045
- #21468 cache reuse for Gemma 4 (closed) — https://github.com/ggml-org/llama.cpp/issues/21468
- #29142 VMM reserve fails on Orin iGPU for multimodal (open) — https://github.com/ggml-org/llama.cpp/issues/29142
- #21421 Gemma 4 audio input (2026-04-12) — https://github.com/ggml-org/llama.cpp/pull/21421
- NVIDIA forum, MTP on Orin NX (2026-06-06) — https://forums.developer.nvidia.com/t/llama-cpp-mtp-lifted-llm-performance-of-jetson-orin/372493

Moonshine:
- PyPI history — https://pypi.org/pypi/moonshine-voice/json
- main…dev-v0.1.6 (2026-09-30 #229 fix) — https://github.com/moonshine-ai/moonshine/compare/main...dev-v0.1.6
- #218, #216 — https://github.com/moonshine-ai/moonshine/issues/218 , https://github.com/moonshine-ai/moonshine/issues/216
- Moonshine v2 paper (2026-02-12) — https://arxiv.org/abs/2602.12241
- README latency table (cached `~/.opensrc/repos/github.com/moonshine-ai/moonshine/0.1.1/README.md` L192–199)

Kokoro / PyTorch / Jetson:
- hexgrad/kokoro #230 (2025-07-02, open) — https://github.com/hexgrad/kokoro/issues/230
- PyTorch #149570 torch.compile on Kokoro (closed 2025-06-18) — https://github.com/pytorch/pytorch/issues/149570
- PyTorch 2.8 CUDA allocator notes — https://docs.pytorch.org/docs/2.8/notes/cuda.html
- CUDA lazy loading default (2023-07-06) — https://developer.nvidia.com/blog/nvidia-cuda-toolkit-12-2-unleashes-powerful-features-for-boosting-applications/
- jetson-voice-engine (2026-09-11) — https://github.com/suharvest/jetson-voice-engine
- jetson-containers kokoro-tts — https://github.com/dusty-nv/jetson-containers/tree/master/packages/speech/kokoro-tts
- kokoro-pi — https://github.com/zreecespieces/kokoro-pi ; Kokoro-FastAPI — https://github.com/remsky/Kokoro-FastAPI
- Jetson AI Lab index jp6/cu126 — https://pypi.jetson-ai-lab.io/jp6/cu126/+simple/onnxruntime-gpu/

Panel / turn-taking:
- openWakeWord releases (v0.6.0, 2024-02-11) — https://github.com/dscripka/openWakeWord/releases
- pymicro-wakeword 2.5.0 (2026-09-17) — https://pypi.org/project/pymicro-wakeword/ ; ESPHome micro_wake_word — https://esphome.io/components/micro_wake_word/
- Silero VAD version history — https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models
- VAD comparison (undated, directional) — https://voxrt.com/vad-comparison
- Smart Turn v3 (2025-09-11) / v3.2 (2026-01-07) — https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/ , https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/
- Pipecat Smart Turn overview — https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview
- LiveKit turn tuning — https://docs.livekit.io/agents/logic/turns/tuning/ ; turn detector — https://livekit.com/blog/solving-end-of-turn-detection
- PipeWire echo-cancel — https://docs.pipewire.org/page_module_echo_cancel.html
- Jabra Speak2 75 tech sheet — https://www.jabra.com/_/media/Jabra_VXi_Product-Documentation/Jabra-Speak2-75/Technical-specifications/RevC/EN-Speak2-75-Tech-Spechs-180924.pdf
- WirePlumber suspend timeout — https://davejansen.com/disable-wireplumber-pipewire-suspend-on-idle-pops-delays-noise/

Horizon:
- Moshi — https://arxiv.org/abs/2410.00037 ; Kyutai STT — https://kyutai.org/stt/ ; Unmute/DSM — https://github.com/kyutai-labs/delayed-streams-modeling/
- Sesame CSM-1B — https://huggingface.co/sesame/csm-1b
- Nemotron Speech ASR (2026-01-05) — https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents
- Gemma 4 (2026-04-02) — https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/

## Appendix — the prompt-eval step measurement (re-runnable, read-only)

```bash
journalctl --user -u llama-server --no-pager --since "<last restart>" \
  | grep -oE 'prompt eval time = +[0-9.]+ ms / +[0-9]+ tokens' | awk '{print $5, $8}' > /tmp/pe.txt
python3 - <<'PY'
import statistics as st
rows = [tuple(map(float, l.split())) for l in open("/tmp/pe.txt")]
for lo, hi in [(1,3),(5,7),(8,16),(16,32),(32,64),(128,256),(256,512),(2048,8192)]:
    xs = [ms for ms, n in rows if lo <= n < hi]
    if xs: print(f"{lo}-{hi-1} tok: n={len(xs)} median={st.median(xs):.0f} ms")
PY
```

For #1 the BEFORE is: 1–2 tok ≈ 61 ms, 5–6 ≈ 116 ms, 8–31 ≈ 196 ms. Success after `--swa-full`
means those bins converge on the single-call cost.
