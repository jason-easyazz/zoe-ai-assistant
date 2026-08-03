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
| `flue-zoe-brain` | `0` | `512M` | `1G` | floor = 3.9x measured peak and matches the value already live, so the template converges with the box instead of being overridden by it; ceiling = 7.7x, deliberately generous because a breach OOM-kills the live brain lane |
| `flue-zoe-telegram` | `0` | `256M` | `768M` | floor = 1.75x measured peak, lower than the brain because a cold Telegram reply is a slow message not a voice fault; ceiling = 5.3x, tighter because `Restart=always` makes a kill cheap here |

`VmHWM` was sampled 3h after a restart on an **already-starved** box, so the
kernel never let either process grow. Treat both peaks as a **lower bound** on the
unstressed peak — that is why the ceilings are 5-8x rather than the usual 2x.
Re-read `memory.current` after a week of normal traffic and tighten if warranted.

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
[Service]
MemorySwapMax=0
MemoryLow=512M
MemoryMax=1G
CONF

mkdir -p ~/.config/systemd/user/flue-zoe-telegram.service.d
cat > ~/.config/systemd/user/flue-zoe-telegram.service.d/memory.conf <<'CONF'
# Channel bridge had NO memory directives, so cgroup memory.low was 0. Measured
# 2026-08-03: VmRSS 6.1 MB vs VmSwap 65.2 MB.
# Tracked in scripts/setup/systemd/flue-zoe-telegram.service.
[Service]
MemorySwapMax=0
MemoryLow=256M
MemoryMax=768M
CONF

systemctl --user daemon-reload
```

The brain's existing `memory.conf` held only `MemoryLow=512M`; the heredoc
replaces it and keeps that value, so the redundant
`~/.config/systemd/user.control/flue-zoe-brain.service.d/50-MemoryLow.conf`
(written by an old `systemctl set-property`, same 512M) stays consistent and can
be left alone.

**3. Restart the brain — expect a brief lane outage, and verify the fallback.**
zoe-data dispatches **flue > core > legacy**, so while `:3578` is down chat
should keep answering on the `core` lane. That fallback is the thing to confirm,
because a silent failure here looks like "Zoe went quiet".

```bash
# Probe chat BEFORE restarting, so you have a known-good comparison.
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' http://localhost:8000/health

systemctl --user restart flue-zoe-brain

# Immediately, while :3578 is still coming up — zoe-data must still answer.
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' http://localhost:8000/health
# ...and a real turn through the fallback lane (expect a slower but valid reply):
#   ask Zoe anything from the panel or Telegram, or drive /api/chat directly.
```

If chat fails during the window that is a **fallback bug**, not a memory bug —
roll back (step 6) and investigate `brain_dispatch.py` separately.

**4. Post-restart checks.**

```bash
systemctl --user status flue-zoe-brain --no-pager      # active (running)
curl -sf http://localhost:3578/health                  # brain lane back up
systemctl --user show flue-zoe-brain \
  -p MemorySwapMax -p MemoryLow -p MemoryMax
#   expect MemorySwapMax=0, MemoryLow=536870912, MemoryMax=1073741824
```

Then drive **one real brain turn** and confirm it is served by the flue lane, not
still falling back. Checking the unit is active is not the same as checking it is
being *used* — what exists is not what runs.

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
