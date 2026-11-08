---
type: reference
title: RAM audit follow-up (non-voice) - router CUDA context (rejected), ORT CPU arena off for the embedders (adopted)
description: Measured 2026-11-08 on the live Orin from the 2026-11-08 RAM audit. Hiding CUDA from the FunctionGemma router did NOT save RAM (it cost +155 MB RSS and changed one answer in 30), so it is not shipped. Turning the ORT CPU arena off for fastembed and Chroma's MiniLM saves ~111 MB RSS with matching vector fingerprints and latency within noise, and ships on by default.
---

# RAM audit follow-up, non-voice (2026-11-08)

Source: `~/.zoe/agent-tools/ram-audit-2026-11-08.md` items 4 (router) and 6 (ORT arena).
Companion record for the voice-path items: [ram-audit-voice-2026-11.md](ram-audit-voice-2026-11.md).
Rule applied: test it, ship it only if it works. Every figure below is from a throwaway
process on a spare port or in a fresh child process, started and killed by the harness
(own PID only), after the MemAvailable >= 2.5 GB gate; nothing live was restarted, killed or
edited.

| item | audit estimate | measured | decision |
|---|---|---|---|
| Router: `CUDA_VISIBLE_DEVICES=` | -0.25..0.35 GB | **+155 MB RSS (+250 MB PSS)**, 1 of 30 answers changed | **rejected, not shipped** |
| ORT CPU arena off (fastembed + Chroma MiniLM) | -0.1..0.3 GB | **-111 MB RSS** (-86..-95 MB HWM), vector fingerprints matched | **adopted, default on** (`ZOE_ORT_CPU_ARENA=1` reverts) |

## 1. Router CUDA context - falsified

Harness: `scripts/perf/measure_router_cuda_ab.py` (the live unit's exact command line,
b9733 `build-jetson-new`, GGUF r2 Q8_0, `--ctx-size 1024 --cache-ram 64 --n-gpu-layers 0
--parallel 1 --threads 4 --mlock --jinja`, port :11446). It sends 30 fixed utterances x 3
rounds through the real grammar path (`router_two_stage.build_grammar` + `_post_sidecar`,
temperature 0) and reads `/proc/<pid>/{status,smaps,maps}`.

| variant | idle RSS / PSS | RSS / PSS / HWM after 90 requests | VmLck | p50 / p95 | load | answers |
|---|---|---|---|---|---|---|
| as live (CUDA visible) | 583 / 355 MB | **607 / 380 / 607 MB** | 271 MB | 293 / 394 ms | 2.1 s | reference |
| `CUDA_VISIBLE_DEVICES=` | 744 / 605 MB | **762 / 623 / 762 MB** | 271 MB | 230 / 339 ms | 2.5 s | 29 of 30 identical |

- The audit's premise was only half right. The CUDA libraries *are* mapped (libcuda, cublas,
  cublasLt, cudart, ggml-cuda, nvrm*), but with the device hidden the process still maps all
  of them (`ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device`), so no mapping
  is saved, and the live process's CUDA-attributed anon memory is small (live smaps: ~4.7 MB
  cublasLt, ~4.5 MB dmabuf).
- With the device hidden, llama.cpp falls back to the host-memory fit/offload path (log:
  `projected to use 207 MiB of host memory`) and RSS *rises* by ~155 MB. The CUDA path keeps
  prefill buffers off the process's resident pages. Mechanism for the exact bytes not chased:
  the A/B already shows the lever goes the wrong way.
- Speed was better without CUDA (p50 230 vs 293 ms), but one probe changed text
  (`home{...room: "living the room"}` vs `"living the best"`), i.e. the CPU path is not
  numerically identical, and the router is on every chat turn. A faster route with a different
  answer would need a full 81-case router eval and a replay gate, for a change that costs RAM.
  Not worth it.
- The router's RSS at 0.69 GB is dominated by the mlocked 278 MB GGUF mapping plus a ~210 MB
  heap; there is no CUDA-context lever to pull. The unit stays as is.

A second, order-reversed repeat (`--order nocuda,cuda`) was started but never reached the
2.5 GB gate (MemAvailable 0.7 - 1.6 GB all evening). The direction of the effect (+155 MB, not
-250 MB) is far outside run-to-run RSS noise (the ORT runs below repeat within ~10 MB), so the
conclusion does not depend on it; rerun the harness if you want the second sample.

## 2. ORT CPU arena off for the embedders - adopted

Where the sessions are made (all inside zoe-data):

| session | where | change |
|---|---|---|
| fastembed bge-small (semantic router stage 1) | `semantic_router._ensure_loaded` | `TextEmbedding(..., enable_cpu_mem_arena=False)` (fastembed 0.8.1 exposes exactly this option) |
| fastembed bge-small (persona drift, offline) | `persona_drift.load_bge_embedder` | same |
| Chroma `ONNXMiniLM_L6_V2` (recall palace) | `memory_service._drawers_embedding_function` | subclass overrides `model` with chroma's own recipe + `enable_cpu_mem_arena=False` |
| Moonshine STT, Silero VAD (`voice_vad.py`, `omi_diarize.py`), smart-turn (`voice_turn.py`) | - | **untouched** |

Harness: `scripts/perf/measure_ort_arena_ab.py`. Each variant is a FRESH child on the zoe-data
venv (onnxruntime 1.23.2, fastembed 0.8.1, chromadb 1.5.9) with zoe-data's allocator env
(`MALLOC_ARENA_MAX=2`, `MALLOC_TRIM_THRESHOLD_=131072`); it builds both sessions through the
real code paths, embeds 48 mixed-length texts x 4 rounds each, and prints RSS, VmSwap, VmHWM,
wall latency and a vector fingerprint (pairs 1-2 below: a partial one, the sum of the first 8 dimensions per vector; the final run hashes the full 4-decimal-rounded vectors). Arena on = the pre-change behaviour.

Two interleaved pairs, uncontended enough that VmSwap was 0 (an earlier pair ran at load 9 and
swapped, so its RSS and latency are discarded):

| run | RSS after | HWM | fastembed p50 | Chroma p50 | fingerprint |
|---|---|---|---|---|---|
| arena on (pair 1) | 434 MB | 461 | 25.1 ms | 64.5 ms | 02a9f4682606 |
| arena OFF (pair 1) | **322 MB** | 366 | 18.2 ms | 65.0 ms | 02a9f4682606 |
| arena on (pair 2) | 443 MB | 462 | 14.8 ms | 53.7 ms | 02a9f4682606 |
| arena OFF (pair 2) | **332 MB** | 376 | 20.6 ms | 63.0 ms | 02a9f4682606 |
| ORT `arena_extend_strategy=kSameAsRequested` (kept arena) | 448 MB | 467 | 25.7 ms | 70.4 ms | 02a9f4682606 |

- **Saving: -111 MB RSS in both pairs** (-86..-95 MB at the HWM). The recorded
  **vector fingerprints matched** in every run (partial in pairs 1-2, full-vector in the final run). The audit's low end (0.1 GB) is what it is
  worth; 0.3 GB was optimistic.
- **Latency is within the box's noise**: fastembed moved -6.9 ms and +5.8 ms across the two
  pairs, Chroma +0.5 and +9.3 ms at load 4-8. That is an honest "no clear regression", not a
  proven zero. CPU time per embed *is* higher without the arena (563 vs 456 ms and 556 vs
  363 ms process-CPU per call, including ORT's spinning intra-op threads), which only
  matters when the box is CPU-saturated.
- A kept-but-bounded arena (`kSameAsRequested`) saved nothing, so it is not offered.
- Revert switch: `ZOE_ORT_CPU_ARENA=1` restores fastembed's and Chroma's own sessions.

A final run of the harness on the shipped code (arena 1,0,1,0, full-vector fingerprint) is recorded in the PR body.

**Verify after deploy:** zoe-data RSS ~110 MB below its pre-deploy figure at the same age
(`ps -o rss -p $(systemctl --user show -p MainPID --value zoe-data)`), `/readyz` green, a
recall question and a routed command behave as before. If recall latency visibly regresses, set
`ZOE_ORT_CPU_ARENA=1` in the zoe-data environment and restart through the normal path.

## Verification

- `services/zoe-data`: `pytest tests/test_ort_arena.py tests/test_embed_thread_safety.py
  tests/test_router_two_stage.py tests/test_persona_drift.py tests/test_palace_drawers_opener.py`
- Harnesses (both self-gate on MemAvailable >= 2.5 GB (a floor `--min-avail-gb` cannot lower), refuse live service ports, and only kill their own PIDs):
  `python3 scripts/perf/measure_router_cuda_ab.py --order nocuda,cuda --wait-s 1800`,
  `python3 scripts/perf/measure_ort_arena_ab.py --wait-s 1800`
