---
type: Reference
title: Memory Pressure Profile (2026-07-06)
description: Point-in-time evidence of who owns RAM and swap on the Orin NX 16GB host, what loads memory inside the zoe-data process, and the measured facts behind each candidate reduction — including the finding that the feared "second Chroma+embedder copy" does not exist under chromadb 0.6.3.
tags: [memory, performance, swap, profiling, jetson, chromadb, zoe-data]
timestamp: 2026-07-06T21:15:00Z
---

# Memory Pressure Profile (2026-07-06)

> **STATUS 2026-07-19 — the two biggest swap owners below are FIXED.** The voice
> stack now carries cgroup guards (`MemorySwapMax=0` + `MemoryLow`), so
> llama-server and kokoro-tts hold **0 swap** instead of the 4.14 GB / 630 MB
> recorded here (total swap 6.6 GB → 3.4 GB, measured 2026-07-19). Two findings
> from that work change how you should read this profile:
>
> - **`--mlock` was never sufficient on Tegra.** llama-server has always run
>   `--mlock` with `LimitMEMLOCK=infinity`, yet `VmLck` held only **1.95 GB of a
>   5.6 GB RSS** — mlock covers the mapped model, not the CUDA/unified
>   allocations around it. "The brain is mlocked" was never a reason its swap
>   figure below was safe.
> - **The `ccd-cli` fleet row (3.59 GB) was largely per-session Serena.** Each MCP
>   client spawned its own server **at connect time**, so the per-instance 1G/2G
>   cap bounded each member and never the fleet. One shared `serena-mcp.service`
>   replaced it.
>
> Current values, the drop-in-not-template-copy procedure, and the
> `Nice=-N`-is-silently-dropped trap live in
> [`scripts/setup/systemd/README.md`](../../scripts/setup/systemd/README.md)
> ("Memory protection"). Triage signature: [`incident-runbook.md`](incident-runbook.md).
> The snapshot below is retained as the evidence that motivated the fix — do not
> read its numbers as current.

Read-only profile of the live host taken 2026-07-06 ~21:10 (host uptime 3d 6h). Numbers are a
point-in-time snapshot — calmer than the reviewed spike (1.1–2.6 GB free, swap 23 GB deep) but the
ownership shape is the durable fact. Context: the 2026-07-04 architecture review flagged memory as
the box's #1 constraint; see the
[tech-debt remediation plan](../architecture/tech-debt-remediation-plan.md).

## Host snapshot

- 15.3 GB physical RAM; **2.1 GB free / 2.1 GB available**; 3.0 GB buff/cache.
- Swap: **11.8 GB used of 57.6 GB** — a 50 GB NVMe `/swapfile` (prio −2, 4.3 GB used) plus 8×978.5 MB
  zram devices (prio 5, ~6.6–7.7 GB of pages stored across the snapshot window).
- `Mlocked: 1953800 kB` (1.95 GB) — llama-server's `--mlock`.
- **zram costs RAM**: `mm_stat` showed `mem_used_total` ≈ 490 MB per device ≈ **3.9 GB of physical RAM
  holding ~6.6 GB of swapped pages** (~1.7:1 compression). At snapshot time ~25% of the box's RAM was
  spent storing compressed swap.

## Swap ownership (per-process VmSwap, sum = 11.8 GB)

| Process | PID(s) | VmRSS | VmSwap |
|---|---|---|---|
| llama-server (Gemma 4 E4B+MTP brain, :11434) | 1549 | 5.35 GB | **4.14 GB** |
| ccd-cli remote-agent fleet (Claude Code sessions) | 19 procs | ~1.0 GB total | **3.59 GB total** (~190–275 MB each) |
| zoe-data (uvicorn `main:app`, :8000) | 1861955 | 90 MB | **1.00 GB** |
| Kokoro sidecar (`scripts/setup/kokoro_sidecar.py`, :10201) | 1547 | 1.2 MB | **630 MB** |
| openclaw gateway (node, :18789) | 1551 | 94 MB | 392 MB |
| homeassistant (container) | 3773 | 30 MB | 370 MB |
| music-assistant (container) | 2759 | 32 MB | 325 MB |
| omnigent server+host (root uv tool) | 2 procs | ~26 MB | ~194 MB |
| hermes gateway | 684587 | 54 MB | 167 MB |
| flue-zoe-brain (node `dist/server.mjs`, :3578) | 1272274 | 6.5 MB | 71 MB |

Docker containers total ~200 MB RSS (`docker stats`: zoe-database 51 MB, music-assistant 35 MB,
omnigent 24 MB, homeassistant 22 MB, everything else <20 MB each). **No LiveKit agent or container
was running** — the on-demand reap (below) had it stopped.

## Per-process detail

### llama-server (PID 1549) — #1 owner of both RAM and swap

- `VmRSS` 5.35 GB but `smaps_rollup` `Rss` only 2.10 GB — the gap is GPU/nvmap unified-memory
  accounting on Tegra (pages counted in `VmRSS` without backing smaps entries).
- `Locked: 1953800 kB` — `--mlock` pins ~1.95 GB (model file pages), **but 4.14 GB of anonymous
  memory is swapped out anyway** (`VmSwap`), i.e. mlock does not cover the draft-model/KV/compute
  buffers. `VmHWM` 7.0 GB.

### zoe-data (PID 1861955)

- Restarted 20:59; **15 minutes later**: `VmRSS` 90 MB, `VmSwap` 999 MB (≈1.1 GB anonymous footprint,
  mostly swapped out at idle), 79 threads — and `VmHWM` already **3.16 GB**, so the process still
  balloons past 3 GB transiently (startup warmups + first turns) before the kernel swaps it down.
- `MALLOC_ARENA_MAX=2` and `MALLOC_TRIM_THRESHOLD_=131072` are **still in force** — systemd unit
  (`systemctl --user cat zoe-data.service`, lines 48–49) and confirmed in the live
  `/proc/1861955/environ`. The earlier 3.2 GB→365 MB arena fix is in place; today's footprint is
  model/data driven, not glibc arena bloat.

### Kokoro sidecar (PID 1547)

- Backend is **PyTorch on CUDA, ~2.3 GB** (`ZOE_KOKORO_BACKEND=pytorch` in `kokoro-tts.service`).
  This memory is **load-bearing and must not be reclaimed**: the ONNX/CPU backend (~600 MB) is
  slower than real time (RTF ~1.0–1.8x vs 0.08x on CUDA), so the sentence-streamed voice pipe
  starves and every reply plays back chopped into pieces. Budget the 2.3 GB; don't "save" it.
- CUDA init needs ~2.3 GB free at load. If the box is busy it OOMs (`NvMapMemAllocInternalTagged:
  error 12`) and silently degrades to CPU — the sidecar now retries, logs `DEGRADED`, and sets
  `degraded=true` on `/health`. The unit is ordered `After=llama-server.service` so the brain
  claims its mlock'd pages first.
- Measured: `VmRSS` 1.2 MB / `VmSwap` 630 MB / `VmHWM` 607 MB — at idle the kernel swaps it out
  almost entirely; it pages back in on TTS use. Effectively an involuntary "reap-by-swap".

### ccd-cli fleet

19 `~/.claude/remote/ccd-cli/2.1.197` processes (host-side Claude Code sessions), several of them
`--resume` duplicates of the *same* session id. Combined ~1 GB RSS + **3.59 GB swap** — the largest
non-model memory owner on the box, and none of it is Zoe runtime.

## What loads memory inside zoe-data (code audit, worktree @ 6891c93f)

- **Moonshine STT** — in-process singleton `Transcriber`
  (`services/zoe-data/routers/voice_tts.py:1803`, load at `:1840`), warmed at startup
  (`services/zoe-data/main.py:942`). Model cache `~/.cache/moonshine_voice` is 904 MB on disk.
- **fastembed embedder** — `TextEmbedding(model_name="BAAI/bge-small-en-v1.5")`, module singleton
  (`services/zoe-data/semantic_router.py:100`, `:129`), warmed at startup.
- **ChromaDB main client** — `memory_service._collection()` →
  `mempalace.palace.get_collection` (`services/zoe-data/memory_service.py:1097-1099`) →
  `ChromaBackend._client()` caches one `chromadb.PersistentClient` per path
  (`mempalace/backends/chroma.py:89`, mempalace 3.3.1).
- **ChromaDB audit client** — `memory_service._audit_collection()` builds a *second*
  `chromadb.PersistentClient(path=data_dir)` at the same path
  (`services/zoe-data/memory_service.py:1101-1111`, module cache `_AUDIT_CLIENTS`).
- **The feared "second full Chroma+embedder copy" does not exist under chromadb 0.6.3** (installed
  version, verified via opensrc): `SharedSystemClient._identifier_to_system` dedupes clients by path
  (`chromadb/api/shared_system_client.py:11-27`), so both clients share one System/segment manager
  and one HNSW cache; and the default embedding function is a *module-import-time default argument*
  (`chromadb/api/client.py:143`) resolving to a single shared `ONNXMiniLM_L6_V2`
  (`chromadb/utils/embedding_functions/__init__.py:50-57`). Net cost of the duplicate client is
  small.
- Real audit-path waste is CPU, not RSS: every memory mutation upserts an audit row with
  `documents=[summary]` and no embeddings (`services/zoe-data/memory_service.py:1480`), so the shared
  ONNX MiniLM embeds text that is only ever read back by metadata filters
  (`col.get(where=...)`, `memory_service.py:1273`, `:1468-1473`) — never semantically queried.
- **Distinct embedding stacks in one process = 2** (Chroma's ONNX MiniLM-L6-v2 + fastembed
  bge-small-en-v1.5), plus Moonshine's ONNX session for STT.
- **Latent in-process Kokoro**: `tts_waterfall.py:45-49` lazy-imports `kokoro_onnx` as a TTS
  fallback — if the sidecar waterfall step fails, zoe-data itself loads a ~600 MB model.
- **Engineering harness + Multica poll loop run in-process** (`services/zoe-data/main.py:312` ff.;
  named as Wave 4 "fence the engineering harness" in the
  [tech-debt plan](../architecture/tech-debt-remediation-plan.md), line 179).

## The LiveKit on-demand-reap pattern (the existing win)

`services/zoe-data/routers/voice_livekit.py:90-93`: with `ZOE_LIVEKIT_ONDEMAND=true` (default) the
LiveKit container stays stopped at boot, is `docker start`-ed on the first `/livekit-token` request,
and an idle monitor stops it again after `ZOE_LIVEKIT_IDLE_TIMEOUT_S` with no participants — keeping
the **~560 MB** WebRTC server out of memory except during actual voice-page use. Verified working:
no LiveKit process/container existed at snapshot time. The pattern (docker start/stop around a
usage signal + idle monitor task) is generic to any docker sidecar with a clear "in use" signal.

## Candidate reductions (facts observed, sized; decisions belong elsewhere)

1. **ccd-cli fleet hygiene — ~3.6 GB swap + ~1 GB RSS.** 19 host-side Claude Code session processes,
   several stale `--resume` duplicates. Operational cleanup, zero Zoe-runtime risk, no repo change.
   Largest single non-model reclaim available.
2. **Fence the harness/Multica out of zoe-data — peak isolation + O(100s of MB).** zoe-data hit
   3.16 GB `VmHWM` within 15 min of start; steady anon footprint ≈1.1 GB. Maps directly to Wave 4
   "fence the engineering harness" (tech-debt plan line 179). Medium risk (refactor of the prod
   process), replay-gate required.
3. **zram sizing — up to ~1–2 GB of RAM.** 3.9 GB of RAM currently holds compressed swap; shifting
   cold pages toward the NVMe swapfile (zram size/priority rebalance) returns RAM at the cost of
   slower swap-ins. Config-level, host-wide blast radius — measure-first.
4. **llama-server's 4.14 GB swapped anon — investigate before touching.** `--mlock` pins only the
   1.95 GB model region; the rest (draft model, KV `q8_0` cache, compute buffers at
   `--ctx-size 8192`) is swappable and currently cold. Any change is to the rock's launch flags —
   highest risk item, evidence says "cold, mostly harmless where it is".
5. **Stop embedding audit rows — CPU per memory mutation, small RAM.**
   `memory_service.py:1480` embeds audit summaries that are only ever metadata-filtered. Low risk,
   small win; also shrinks the audit collection's HNSW index growth.
6. **Generalize the LiveKit reap** (voice_livekit.py pattern) to idle docker sidecars: at snapshot,
   homeassistant (370 MB swap) and music-assistant (325 MB swap) are the visible candidates; Kokoro
   is already de-facto reaped by swap.
