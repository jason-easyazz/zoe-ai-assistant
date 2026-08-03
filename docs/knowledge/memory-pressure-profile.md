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

> **STATUS 2026-08-03 — the two `flue-*` sidecars were MISSED by that fix and are
> now capped in their templates.** `flue-zoe-brain` is not "lab": under
> `ZOE_BRAIN_BACKEND=flue` it is the **top** dispatch lane (flue > core > legacy),
> and it was running `MemoryMax=infinity` / `MemorySwapMax=infinity` while 87% paged
> out. `flue-zoe-telegram` had no memory directives at all. Measurements, chosen
> caps and the operator apply/rollback sequence are in
> [the 2026-08-03 section below](#2026-08-03--flue-sidecar-caps-apply-runbook).
> Pinned by `tests/unit/test_systemd_memory_protection.py` so the gap cannot
> silently reopen for the next unit.

> **STATUS 2026-08-03 (later) — `zoe-data` was the same gap a third time, and the
> purest form of it.** Its `MemoryLow=2G` + `CPUWeight`/`IOWeight` `300` were real
> and live but existed ONLY as untracked local drop-ins, so the tracked template
> carried no protection at all. Measured with that floor in force: **96% paged
> out** (`VmRSS` 40 MB vs `VmSwap` 1,056 MB). It hosts **in-process Moonshine
> STT**, so "primary backend API" is also the voice path.
> [Section below](#2026-08-03-later--zoe-data-the-protection-was-real-live-and-tracked-nowhere)
> — including the six-unit natural experiment showing that only `MemorySwapMax=0`
> works on this box, and why the leaf `MemoryLow` values never could.

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

---

## 2026-08-03 — flue sidecar caps (apply runbook)

The #1409 pass fixed the units it knew about. Both `flue-*` sidecars shipped
uncapped and stayed that way, so this is the same finding a second time: the
protection was applied per-unit by hand and nothing enforced the *class*.

### Measured, read-only, on the live Orin

Box at sample time: **15 Gi total, 190 Mi available, 11 Gi of 57 Gi swap in use.**
Both units had been up 3h15m (restarted 11:28 / 11:30 AWST).

| | `flue-zoe-brain` (PID 1562) | `flue-zoe-telegram` (PID 6584) |
|---|---|---|
| `VmRSS` | **2.0 MB** | **6.1 MB** |
| `VmSwap` | **70.4 MB** | **65.2 MB** |
| `VmHWM` (peak RSS) | **132.8 MB** | **145.9 MB** |
| cgroup `memory.current` | 10.4 MB | 19.7 MB |
| cgroup `memory.swap.current` | 72.1 MB | 68.2 MB |
| effective `MemoryLow` | 512M *(local drop-in)* | **0** |
| `MemoryMax` / `MemorySwapMax` | `infinity` / `infinity` | `infinity` / `infinity` |

**87%** of the brain lane and **78%** of the Telegram bridge were on disk.

Two facts from the same sample do the load-bearing work:

- **`MemoryLow` is not swap immunity — proven here, not argued.** `flue-zoe-brain`
  has carried `MemoryLow=512M` as a drop-in since 2026-07-09 and was *still* 87%
  swapped. In the same instant `llama-server` (`MemoryLow=6G` **+
  `MemorySwapMax=0`**) held 6.38 GB resident and **0** swap. The swap directive is
  what holds the line; the floor only changes reclaim ordering.
- **`MemoryMax` is safe on these two in a way it is not on `llama-server`.** Both
  are pure userspace Node — verified **0** CUDA/NvMap mappings in
  `/proc/<pid>/maps` — so cgroup accounting is complete and a ceiling genuinely
  bounds them. The "hard ceiling + no swap turns a spike into an OOM kill" caveat
  is about a 5.6 GB unified-memory process, not a ~130 MB sidecar.

Worth knowing when reading any `MemoryLow` on this box: every ancestor cgroup
(`user.slice` → `user-1000.slice` → `user@1000.service` → `app.slice`) has
`memory.low=0`, and `/sys/fs/cgroup` is mounted with `memory_recursiveprot`. The
leaf floors are set, the branches above them are not.

### Chosen caps

| Unit | `MemorySwapMax` | `MemoryLow` | `MemoryMax` | why |
|---|---|---|---|---|
| `flue-zoe-brain` | `0` | `512M` | **`2G`** | floor matches the value already live, so the template converges with the box instead of being overridden by it; ceiling is a leak BACKSTOP, sized from runtime behaviour rather than a `VmHWM` multiple — see the correction below |
| `flue-zoe-telegram` | `0` | `256M` | **`1G`** | floor lower than the brain's because a cold Telegram reply is a slow message not a voice fault; ceiling half the brain's — this bridge long-polls small JSON rather than streaming model output, so it has far less external-buffer exposure, and `Restart=always` makes a kill cheap |

> **CORRECTED 2026-08-03 (same day, red-team review).** The ceilings above were
> originally `1G` / `768M`, sized as a multiple of `VmHWM`. **That method was
> wrong** and the reasoning is kept here because the wrong version is seductive:
> `VmHWM` was sampled on an already-starved box, so it is a *lower* bound on the
> unstressed peak and every multiple of it inherits the error. Re-read
> `memory.current` after a week of normal traffic before trusting any of these.

### What actually decides throttle-vs-kill on a Node unit (measured)

The observed peak is the wrong quantity. The right one is how the runtime behaves
*under* the ceiling — measured on this box 2026-08-03 with transient
`systemd-run --user --scope` probes:

| cgroup `MemoryMax` | V8 `heap_size_limit` |
|---|---|
| 512M | 259 MB |
| 768M | 396 MB |
| 1G | 524 MB |
| 2G | 1048 MB |
| *(uncapped)* | **4144 MB** |

**V8 reads the cgroup limit and sizes its JS heap to ~51% of it.** So a JS-heap
runaway hits V8's own limit before the cgroup fires. Be precise about what that
buys: V8 heap exhaustion still usually **terminates** the process (`FATAL ERROR:
JavaScript heap out of memory`). The gain is a *logged, attributable* failure
with a stack — not survival, and not a graceful degradation.

**External memory is not in that budget.** Buffers, ArrayBuffers and stream
chunks live outside V8's old space, so the self-limiting does not cover them. A
`Buffer.alloc` loop under `MemoryMax=1G` + `MemorySwapMax=0` was **SIGKILLed by
the cgroup — exit 137**, not caught by V8.

Read that as a demonstrated **risk**, not a workload profile: it proves external
buffers *can* reach the ceiling unmediated. Whether the brain sidecar's streamed
llama-server responses actually allocate that way at volume has **not** been
measured. It is the plausible exposure that justifies real headroom — re-read
`memory.current` after a week of normal traffic and tighten if it stays flat.

### Why these two keep a ceiling when llama-server and zoe-data do not

**Not because a kill here is harmless — it is not.** There is **no core-lane
failover**, contrary to what this runbook said before 2026-08-03:
`brain_dispatch.use_flue_brain()` selects the lane from `ZOE_BRAIN_BACKEND`
alone, with no health check and no re-dispatch, so with that env set to `flue`
(**live**) a dead sidecar makes `zoe_flue_client` return a canned "trouble
reaching my brain" string for every turn until systemd restarts it. The reasons
are narrower, and they are trades:

- **Blast radius is still strictly smaller than zoe-data's.** Brain turns fail;
  the panel, Home Assistant, TTS, Multica and the rest of the API keep serving.
  A zoe-data kill takes all of it at once.
- **Recovery is automatic and cheap** — `Restart=always` + `RestartSec=5` on a
  small Node process, against zoe-data's cold reload of Moonshine + fastembed +
  Chroma (and, under swap denial, a 3.16 GB startup transient).
- **Uncapped is worse than capped.** V8 would size its heap to **4144 MB**; on a
  15.6 GB box where llama-server holds ~6.4 GB, an unbounded sidecar that
  *cannot be swapped* threatens the brain **rock** — a strictly worse outage
  than the sidecar's own death.

For `zoe-data` none of the first two hold, which is why it is in
`MEMORY_MAX_EXEMPT` instead.

`tests/unit/test_systemd_memory_protection.py` now requires `MemoryMax` ≥ **3×**
`MemoryLow`: the floor is sized to hold the working set, so a backstop needs at
least two more floors above it for allocation the floor never covered. The
threshold is 3 and not 2 deliberately — the brain's original `512M`/`1G` was
*exactly* 2×, so a 2× rule would have ratified the bug it exists to catch.
Bounded, non-growing workloads (kokoro's ~2.3 GB CUDA-resident model) are a
documented exception in `TIGHT_CEILING_OK`.

Headroom: this adds 768 MB to the box's soft-protected total (llama 6G + kokoro
3G + zoe-data 2G = 11G → **11.75G of 15.6G**). `MemoryLow` is a protection
*ceiling*, not a reservation — it costs nothing while unused.

### Apply — operator steps

Nothing below is done by an agent, and none of it is urgent: the units are
*already* running degraded, so waiting for a genuinely quiet moment costs nothing.

**1. Check the box can absorb a restart.** This is the real precondition.

```bash
free -h                      # want >1 GB available, NOT the 190 MB of the sample
systemctl --user is-active llama-server kokoro-tts zoe-data
```

`MemorySwapMax=0` means the restarted process must fault its ~150 MB working set
into **RAM that cannot be pushed back out**. At 190 MB available that allocation
competes directly with the voice stack, and the loser is chosen by the kernel.
150 MB is small, so the risk is modest — but it is not zero, and the whole point
of the change is that these pages can no longer be relieved by swap. Restart at an
idle time.

(Setting `memory.swap.max=0` on the *running* cgroup does not force existing swap
back in — the kernel just stops swapping out from then on. The pressure moment is
the restart, not the reload.)

**2. Write the drop-ins — do NOT copy the templates over the installed units.**
The installed copies carry host-specific edits (`llama-server`'s binary/model
paths are the reason this rule exists). Mirror the tracked values in a drop-in:

```bash
mkdir -p ~/.config/systemd/user/flue-zoe-brain.service.d
cat > ~/.config/systemd/user/flue-zoe-brain.service.d/memory.conf <<'CONF'
# Live brain lane (ZOE_BRAIN_BACKEND=flue) must not be paged out. Measured
# 2026-08-03: VmRSS 2.0 MB vs VmSwap 70.4 MB (87% swapped) under MemoryLow alone.
# Tracked in scripts/setup/systemd/flue-zoe-brain.service.
# MemoryMax is a leak BACKSTOP, not a working limit: V8 self-limits its JS heap
# to ~51% of it, but external Buffers/stream chunks are outside that budget and
# would be SIGKILLed by the cgroup (exit 137, measured at 1G).
[Service]
MemorySwapMax=0
MemoryLow=512M
MemoryMax=2G
CONF

mkdir -p ~/.config/systemd/user/flue-zoe-telegram.service.d
cat > ~/.config/systemd/user/flue-zoe-telegram.service.d/memory.conf <<'CONF'
# Channel bridge had NO memory directives, so cgroup memory.low was 0. Measured
# 2026-08-03: VmRSS 6.1 MB vs VmSwap 65.2 MB.
# Tracked in scripts/setup/systemd/flue-zoe-telegram.service.
[Service]
MemorySwapMax=0
MemoryLow=256M
MemoryMax=1G
CONF

systemctl --user daemon-reload
```

The brain's existing `memory.conf` held only `MemoryLow=512M`; the heredoc
replaces it and keeps that value, so the redundant
`~/.config/systemd/user.control/flue-zoe-brain.service.d/50-MemoryLow.conf`
(written by an old `systemctl set-property`, same 512M) stays consistent and can
be left alone.

**3. Restart the brain — and know that there is NO core-lane fallback.**

> **CORRECTED 2026-08-03.** An earlier version of this step said "zoe-data
> dispatches flue > core > legacy, so while `:3578` is down chat should keep
> answering on the `core` lane." **That is wrong**, and it is the kind of wrong
> that makes an operator misread a real outage as expected behaviour. Verified in
> code: `brain_dispatch.use_flue_brain()` selects the lane from
> `ZOE_BRAIN_BACKEND` **alone** — there is no health check and no re-dispatch.
> With the env set to `flue` (**confirmed live**: present in the running
> zoe-data process env and in `services/zoe-data/.env`), a down sidecar means
> `zoe_flue_client` catches the transport error and yields a canned string:
>
> > `Sorry, I had trouble reaching my brain just now. Could you try again?`
>
> "flue > core > legacy" is a **configuration precedence evaluated once**, not a
> runtime failover chain. So `:3578` down = every brain turn fails, for the whole
> window. Plan the restart accordingly.

That canned string is also the thing that makes the check below *decidable* — it
is an exact literal, so which lane served a turn is observable rather than
inferred. Do NOT use `systemctl --user restart` for this: it waits for the
replacement to come up, so the window is too short to observe and you can end up
"verifying" a fallback that never happened.

```bash
# 0) Known-good BEFORE: a real brain turn must give a real answer.
#    (/health only proves zoe-data is up — it never touches the brain lane.)
curl -sf http://localhost:3578/health && echo "flue lane UP"

# 1) STOP (not restart) so the down-window is yours to inspect.
systemctl --user stop flue-zoe-brain

# 2) Confirm the lane is actually down — connection refused, not a slow 200.
curl -s -o /dev/null -w '%{http_code}\n' --max-time 3 http://localhost:3578/health || echo "refused = down"

# 3) Drive a REAL brain turn (panel, Telegram, or /api/chat) and ASSERT the lane.
#    Expected while stopped — the literal fallback sentinel:
#      "Sorry, I had trouble reaching my brain just now. Could you try again?"
#    Any other coherent reply means something ELSE served it: re-read
#    ZOE_BRAIN_BACKEND in the RUNNING process before trusting this runbook.
tr '\0' '\n' < /proc/$(systemctl --user show -p MainPID --value zoe-data)/environ \
  | grep ZOE_BRAIN_BACKEND

# 4) Bring it back and confirm the flue lane serves again.
systemctl --user start flue-zoe-brain
curl -sf http://localhost:3578/health && echo "flue lane UP"
```

Then drive one more real turn: a **coherent answer instead of the sentinel** is
the proof the flue lane is serving. If the sentinel persists after `:3578` is
healthy, that is a client/token problem, not a memory one — roll back (step 6)
and investigate `zoe_flue_client` separately.

**4. Post-restart checks.**

```bash
systemctl --user status flue-zoe-brain --no-pager      # active (running)
curl -sf http://localhost:3578/health                  # brain lane back up
systemctl --user show flue-zoe-brain \
  -p MemorySwapMax -p MemoryLow -p MemoryMax
#   expect MemorySwapMax=0, MemoryLow=536870912, MemoryMax=2147483648
```

Checking the unit is active is not the same as checking it is being *used* —
what exists is not what runs. Step 3 already gives the decidable version of that
check (sentinel vs coherent answer); this step only confirms the caps landed.

**5. Restart Telegram** (independent, no brain impact):

```bash
systemctl --user restart flue-zoe-telegram
systemctl --user show flue-zoe-telegram -p MemorySwapMax -p MemoryLow -p MemoryMax
```
Send the bot a message to confirm the bridge reconnected.

**6. Confirm the fix took, ~an hour later.** The success signal is swap that
stops growing:

```bash
grep -E 'VmRSS|VmSwap' /proc/$(systemctl --user show -p MainPID --value flue-zoe-brain)/status
# VmSwap should be 0 and stay 0
```

### Rollback

```bash
rm -f ~/.config/systemd/user/flue-zoe-brain.service.d/memory.conf
rm -f ~/.config/systemd/user/flue-zoe-telegram.service.d/memory.conf
systemctl --user daemon-reload
systemctl --user restart flue-zoe-brain flue-zoe-telegram
```

Removing the drop-in reverts to the installed unit. If the *template* has also
been installed on this host, the caps live there too — check
`systemctl --user cat flue-zoe-brain` and remove the block from
`~/.config/systemd/user/flue-zoe-brain.service` as well, or the rollback is
incomplete.

---

## 2026-08-03 (later) — zoe-data: the protection was real, live, and tracked nowhere

Same day, same finding, third unit — and the cleanest example of the class.
`zoe-data.service` had **no memory directives in its tracked template at all**,
while the live box ran with genuine protection assembled entirely from untracked
local drop-ins:

| file | contents |
|---|---|
| `~/.config/systemd/user/zoe-data.service.d/memory.conf` | `MemoryLow=2G` ("Protect STT (Moonshine) + TTS (Kokoro) working set…") |
| `~/.config/systemd/user/zoe-data.service.d/priority.conf` | `CPUWeight=300`, `IOWeight=300` |
| `~/.config/systemd/user.control/zoe-data.service.d/50-{CPUWeight,IOWeight,MemoryLow}.conf` | the same values again, from an old `systemctl set-property` |

All of it dated 2026-07-09 and none of it in git. A rebuild, a reimage, or a
second host silently drops the lot — the #1409 finding restated exactly.

**zoe-data is on the voice path, not merely the API path.** Moonshine STT is an
in-process singleton (`routers/voice_tts.py`, warmed at startup), so a swapped
zoe-data is a swapped STT: the first utterance after idle waits on ~1 GB faulting
back off the NVMe swapfile before transcription begins.

### Measured, read-only, on the live Orin

2026-08-03 15:04 AWST. zoe-data PID 18936, up 3h21m, `NRestarts=0` — so the
drop-ins (July) were in force for this process's entire life. Box: 15.3 GB total,
**346 MB available**, 10.4 GB of 57.6 GB swap in use.

| | value |
|---|---|
| `VmRSS` | **40 MB** |
| `VmSwap` | **1,056 MB** |
| `VmHWM` (peak RSS this boot) | 1,474 MB |
| threads | 73 |
| cgroup `memory.current` / `memory.swap.current` | 56.9 MB / **1.25 GB** |
| effective `MemoryLow` | `2G` *(untracked drop-in)* |
| `MemoryMax` / `MemorySwapMax` | `infinity` / `infinity` |
| `CPUWeight` / `IOWeight` | 300 / 300 |

**96% paged out, with the 2 GB floor in force the whole time.**

### The natural experiment — six units, one instant

This is the strongest evidence on the box for the doctrine, because all six were
sampled in the same second under the same pressure:

| unit | `MemoryLow` | `MemorySwapMax` | VmRSS | VmSwap |
|---|---|---|---|---|
| `llama-server` | 6G | **0** | 6,053 MB | **0** |
| `kokoro-tts` | 3G | **0** | 2,198 MB | **0** |
| `zoe-data` | 2G | *infinity* | 40 MB | **1,056 MB** |
| `flue-zoe-brain` | 512M | *infinity* | 1 MB | 71 MB |
| `flue-zoe-telegram` | 0 | *infinity* | 11 MB | 63 MB |
| `serena-mcp` | 0 | 2G | 127 MB | 0 |

Every unit that denies swap holds **0** swap. Every unit that does not is
**96–98%** out — and the floor makes no difference across 6G, 2G, 512M and 0.

**The mechanism, which explains why the floors never worked here:** a cgroup's
*effective* `memory.low` is capped by its ancestors', and every ancestor on this
box is `0` — `user.slice`, `user-1000.slice`, `user@1000.service` and `app.slice`
all read `memory.low=0`. So a leaf floor computes to 0 whatever it says.
`memory.swap.max` carries no such propagation; it is enforced per-cgroup. That is
the concrete reason `MemorySwapMax=0` is the line that holds. (Fixing the
ancestors — protection on `app.slice` — would make the floors mean something and
is the obvious follow-up, but it is a host-wide change with a blast radius well
beyond this branch. Not done here.)

### Decision

| directive | value | why |
|---|---|---|
| `MemorySwapMax` | **`0`** | The only directive measured to work on this hierarchy. zoe-data hosts in-process STT; 96% paged out is the voice-latency bug in a different costume. |
| `MemoryLow` | **`2G`** | Matches live, so the template converges with the box. ~1.8× the ~1.1 GB steady anon footprint. Kept for reclaim ordering and for the day the ancestor slices get protection — honest about being soft, not load-bearing on its own. |
| `CPUWeight` / `IOWeight` | **`300`** | The other half of the untracked drop-in. Memory guards keep zoe-data resident; weights keep it *scheduled* once resident. Restoring one without the other looks protected and still loses to the agent fleet. |
| `MemoryMax` | **none — documented exemption** | See below. |

**Why no ceiling, and why that is not llama-server's reason.** zoe-data's cgroup
accounting is *complete* — verified **0** CUDA/NvMap mappings in
`/proc/18936/maps`; STT is ONNX Runtime on CPU (`libmoonshine.so` +
`libonnxruntime` with only the shared provider, no CUDA provider loaded). So
unlike llama-server, a `MemoryMax` here would genuinely bound the cgroup. It is
declined on **blast radius**: zoe-data is the entire product surface (chat,
voice, panel, the Multica poll loop) and it is spiky — ~1.1 GB steady against a
**3.16 GB `VmHWM`** recorded 2026-07-06, a 3× idle-to-peak spread driven by
startup warmups and first turns. A hard ceiling *plus* denied swap converts one
of those ordinary transients into a cgroup OOM kill, i.e. a total outage; today
the same transient merely gets slow. Swap denial alone still has a release valve
(the kernel reclaims file pages); denial plus a ceiling has none. llama-server's
exemption is about a ceiling being *unreliable*; zoe-data's is about a ceiling
being *unsafe*. Both are recorded with their rationale in
`MEMORY_MAX_EXEMPT` (`tests/unit/test_systemd_memory_protection.py`), which now
fails on an exemption that states no reason.

Headroom is unchanged in practice: the 2G floor was already counted in the budget
above while living only in a drop-in. Tracking it changes what a rebuild
reproduces, not what the box allocates.

### Apply — operator steps (delta from the flue runbook above)

Same shape as the flue apply, with one materially different risk. **The standing
operator authorisation to restart zoe-data does not make this an agent step** —
the sizing is agent work, the apply is the operator's.

**1. Make room FIRST — do not just wait for a quiet moment.** zoe-data is the
product; a restart is a visible outage of chat, voice and the panel, not a lane
failover. There is no fallback to verify here because there is nothing to fall
back to.

The `>1 GB available` gate used for the flue sidecars **does not transfer, and
copying it here would be the error.** It was sized for their combined ~150 MB.
zoe-data must fault a **~1.1 GB steady working set into RAM that can no longer
be pushed back out**, and its startup transient is larger still — `VmHWM`
**3.16 GB** was recorded 15 minutes after a restart on 2026-07-06 (warmups +
first turns), *with* swap available to absorb it. Under `MemorySwapMax=0` that
absorption is gone. So a 1 GB gate is below even the steady figure, never mind
the transient.

Measured on the live box while writing this (2026-08-03): **430 MB available**,
2.5 GB buff/cache, 12.3 GB used. Starting from there, quiesce the dev tooling —
all of it is agent-fleet infrastructure, none of it is Zoe runtime:

```bash
# The single largest reclaimable consumer: the shared Serena MCP server.
# Dev tooling by design (Nice=10, OOMScoreAdjust=500 — it exists to yield).
systemctl --user stop serena-mcp          # measured 1045 MB RSS

# Stray per-agent Serena stdio spawns. Doctrine says exactly ONE server should
# exist; 5 extra were live at sample time (~180 MB visible). >1 = a misconfigured
# agent, see scripts/AGENTS.md. Close those agent sessions.
pgrep -af "serena start-mcp-server" | wc -l

# Per-agent codebase-memory servers (8 live at sample time, capped 512M/768M
# each by codebase_memory_capped.sh). They exit with their agent — close the
# sessions rather than killing the servers.
pgrep -fc codebase-memory

# Host-side Claude Code sessions (232 MB RSS + 233 MB swap at sample time).
pgrep -fc ccd-cli

free -m                                   # RE-CHECK after quiescing
systemctl --user is-active llama-server kokoro-tts
curl -sf http://localhost:8000/health
```

**Gate: ≥2.5 GB available before restarting**, and stop if you cannot get there
— the two rocks (llama-server ~6.4 GB, kokoro ~2.2 GB) are themselves
`MemorySwapMax=0`, so they cannot yield to make room and the kernel's only
remaining victim is whichever process asks next. Quiescing the tooling above
released roughly 1.5-2 GB in practice, which is the difference between the
sampled 430 MB and a safe window.

That gate is deliberately below the 3.16 GB startup `VmHWM`: file-backed cache
(2.5 GB at sample time) is reclaimable under pressure and covers the gap, and no
ceiling is set on zoe-data, so a transient overshoot degrades the box rather than
killing the process. If you want the conservative version, wait for ≥3.5 GB.

**2. Write the drop-in** (never `cp` the template over the installed unit — the
installed copy carries host-specific `Environment=` lines this template does not
have). This *replaces* the existing `memory.conf` and folds in `priority.conf`:

```bash
mkdir -p ~/.config/systemd/user/zoe-data.service.d
cat > ~/.config/systemd/user/zoe-data.service.d/memory.conf <<'CONF'
# Protect STT (Moonshine) + TTS working set so audio synthesis doesn't stutter
# when the box is under agent load. Measured 2026-08-03: VmRSS 40 MB vs VmSwap
# 1056 MB (96% swapped) under MemoryLow=2G alone — only MemorySwapMax=0 holds.
# No MemoryMax on purpose (spiky production surface; a ceiling + no swap = OOM
# kill = total outage). Tracked in scripts/setup/systemd/zoe-data.service.
[Service]
MemorySwapMax=0
MemoryLow=2G
CPUWeight=300
IOWeight=300
CONF

rm -f ~/.config/systemd/user/zoe-data.service.d/priority.conf   # folded in above
systemctl --user daemon-reload
```

The stale `~/.config/systemd/user.control/zoe-data.service.d/50-*.conf` files
(`CPUWeight`, `IOWeight`, `MemoryLow`, from an old `systemctl set-property`) hold
the *same* values, so they stay consistent and can be left alone. They do **not**
carry `MemorySwapMax`, so they cannot override the line that matters — but if you
ever retune `MemoryLow`, retune it there too or the two disagree.

**3. Restart and verify.**

```bash
systemctl --user restart zoe-data
curl -sf http://localhost:8000/health
systemctl --user show zoe-data \
  -p MemorySwapMax -p MemoryLow -p MemoryMax -p CPUWeight -p IOWeight
#   expect MemorySwapMax=0, MemoryLow=2147483648, MemoryMax=infinity, 300/300
```

Then drive **one real voice turn** — the point of the change is STT latency, and
a green `/health` does not exercise Moonshine.

**4. Confirm ~an hour later.** The success signal is swap that stays at zero:

```bash
grep -E 'VmRSS|VmSwap' /proc/$(systemctl --user show -p MainPID --value zoe-data)/status
# VmSwap should be 0 and stay 0; VmRSS should now sit near the ~1.1 GB real
# working set instead of the 40 MB that meant "almost entirely on disk".
```

A rising `VmRSS` is the *expected* outcome, not a regression — the memory was
always in use, it was just on the swapfile.

### Rollback

```bash
rm -f ~/.config/systemd/user/zoe-data.service.d/memory.conf
systemctl --user daemon-reload
systemctl --user restart zoe-data
```

This drops `CPUWeight`/`IOWeight` too (they were folded into the same file);
the `user.control/50-*.conf` copies restore both weights and the 2G floor, so the
rollback lands on the pre-change live state rather than on nothing. As with the
flue units, if the *template* has also been installed on this host the block
lives there as well — check `systemctl --user cat zoe-data`.

### `hermes-agent` — checked, and moot

Checked in the same pass for the same template/live drift:
`~/.config/systemd/user/hermes-agent.service.d/kanban-worker-lean.conf` exists
(2026-06-11) against a tracked template carrying no memory directives. **It is
`disabled` and `inactive`** — consistent with the unit being paused since
2026-06-21 — so there is no live protection to lose and no swapped process to
rescue. Left untouched deliberately: writing caps for a unit nobody runs is
speculative sizing with no measurement behind it. If hermes-agent is ever
re-enabled, size it from a fresh `VmHWM` and add it to `NO_SWAP_UNITS` then.
