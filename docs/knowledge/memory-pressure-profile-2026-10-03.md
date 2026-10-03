---
type: Reference
title: Memory Pressure Profile (2026-10-03) — W3 measurement + ranked reclaim plan
description: Read-only re-run of the 2026-07-06 memory-pressure methodology on the live Orin NX 16GB (per-process VmRSS/VmSwap/smaps_rollup, cgroup memory.current/swap.current, zram mm_stat, Mlocked, what loads inside zoe-data), the ledger against 2026-07-06, a ranked W3 reclaim list with measured sizes and risk, and the W3 DoD verdict — swap is under 6 GB, but available memory is lower than on 2026-07-06, so the gate is not met in substance.
tags: [memory, performance, swap, zram, profiling, jetson, zoe-data, kokoro, llama-server, w3]
timestamp: 2026-10-03T22:35:00+08:00
---

# Memory Pressure Profile (2026-10-03)

This record is the W3 measurement asked for in
[`samantha-evolution-plan.md`](../architecture/samantha-evolution-plan.md) §3 W3 and §7 item 4.
It uses the same method as the [2026-07-06 profile](memory-pressure-profile.md). Everything
here was read-only: nothing was restarted, killed, reconfigured or written on the box.

**Headline.** Swap is **2.1–2.7 GB**, so the "< 6 GB" half of the DoD is met. The reclaims
executed since 07-06 add up to far more than 2 GB. But **MemAvailable is 0.42–0.56 GB**,
against 2.1 GB on 2026-07-06 and the 2.5–2.7 GB idle that B0.1 recorded on 2026-09-27. The
freed RAM has been re-absorbed by three things:

1. Swap denial pinned the voice stack resident. This was deliberate and correct.
2. The brain's `--cache-ram 2048` prompt cache has filled since 2026-09-27, adding
   **+2.2 GB**.
3. One live agent session costs about **1 GB**.

So the gate's purpose, headroom for W4/W6, is **not** met. See [the DoD verdict](#dod-verdict).

## Method (reproducible, all read-only)

- Host: `free -m`, `/proc/meminfo` (`MemAvailable`, `Mlocked`, `Unevictable`), `/proc/swaps`,
  `/sys/block/zram*/{disksize,comp_algorithm,mm_stat}`, `/proc/sys/vm/swappiness`, one
  `tegrastats` sample, `vmstat`.
- Per process: `/proc/<pid>/status` (`VmRSS`, `VmSwap`, `VmHWM`, `VmLck`, `RssAnon`, `RssFile`),
  `/proc/<pid>/smaps_rollup`, `/proc/<pid>/smaps` grouped by mapping, and the `maps` library
  inventory.
- Per unit or container: cgroup `memory.current`, `memory.swap.current`, `memory.stat` (`anon`,
  `file`), and the `memory.{low,max,swap.max}` values in force. `docker stats --no-stream`
  adds an independent view.
- On Tegra, `VmRSS` includes NvMap (GPU) pages and `smaps_rollup` `Rss` does not. The gap
  between the two is reported as the process's GPU share.
- `/sys/kernel/debug/nvmap` is **not readable without root**. GPU attribution is therefore
  the `VmRSS − smaps Rss` gap only.
- Three samples were taken: 22:21:49, 22:29:55 and ~22:33 AWST. Host uptime was 53 d (boot
  2026-08-11 07:44). One agent session (Claude Code `ccd-cli` + its `codebase-memory-mcp`)
  was live throughout, because something has to take the measurement. Its cost is broken
  out [below](#dev-tooling-on-the-box-not-zoes-cost-but-on-the-box).

## Host snapshot

| | 2026-07-06 | **2026-10-03** |
|---|---|---|
| physical RAM | 15.3 GB | 15.29 GiB (15,655 MB) |
| free / **available** | 2.1 / **2.1 GB** | 0.59–0.71 / **0.42–0.56 GB** |
| buff/cache | 3.0 GB | 2.85–2.90 GB, of which **1.87 GB is the brain's mlocked GGUF** (unevictable) |
| swap used | **11.8 GB** of 57.6 GB | **2.14 → 2.57 → 2.66 GB** of 53.2 GB |
| swap devices | 50 GB `/swapfile` (prio −2) + **8 × 978 MB zram** | 50 GB `/swapfile` (prio −2, ~0.19 GB used) + **8 × 244 MiB zram** (prio 5, all 100% full) |
| zram physical cost (`mm_stat` `mem_used_total`) | **≈3.9 GB** holding ~6.6 GB (1.7:1) | **503 MiB** holding 1,952 MiB (**3.9:1**), `mem_used_max` 687 MiB |
| `Mlocked` | 1.95 GB | 1.95 GB (1,953,804 kB) |
| `Unevictable` | — | 1.99 GB |
| swappiness | — | 10 |
| tegrastats | — | `RAM 12687/15655MB (lfb 1x2MB) SWAP 2298/53152MB`, GR3D 0% |

**Correction to the brief.** zram is **not** "a single 244 MB device". It is **eight 244 MiB
devices**, which is 1.95 GiB of swap capacity in total.

How it changed: B0.1 in the
[beat-the-bar program](../architecture/beat-the-bar-2026-program.md), 2026-09-27. The
operator shrank each device live (`swapoff` → `zramctl --reset` → write `disksize` →
`mkswap` → `swapon -p 5`), then made it stick across reboots by changing `/ 2 /` to `/ 8 /`
in `/etc/systemd/nvzramconfig.sh` (file mtime Sep 27 10:04). The devices date from the
2026-08-11 boot, so the resize was done live and has never been through a reboot. The stock
NVIDIA script divides RAM by 2 and then by 8 devices, which gave 978 MB per device; the
edited script divides by 8 and then by 8, which gives 244 MiB. The repo records this only
in docs: `git log -S zram` → #1680, #1687, #1718, #1748. No tracked file carries the config.

**`lfb 1x2MB`** means the largest free contiguous block is 2 MB. That is the fragmentation
behind `NvMapMemAllocInternalTagged: error 12` on a CUDA (re)start while `MemAvailable`
looks non-zero. See [voice-pipeline.md](voice-pipeline.md).

**Low headroom shows up as IO, not swap.** During the samples, `vmstat` showed sustained
**290–440 MB/s block reads** with 12–45% iowait, against only 0.2–6 MB/s of swap-in. That
is file-page refault churn: `workingset_refault_file` is 6.4 × 10¹⁰ since boot, and
zoe-data's `RssFile` read **0–1 MB**, so its code pages had been evicted. Part of this may
be the agent session's `codebase-memory` index. Leaf attribution needs root, because
user-scope `io.stat` is incomplete.

## Ownership (per process, sample 1)

`TOT = VmRSS + VmSwap`. **GPU** is `VmRSS − smaps Rss` (NvMap). The cgroup figures are the
unit's `memory.current` / `memory.swap.current`.

| Process (unit) | PID | VmRSS | VmSwap | VmHWM | VmLck | GPU (NvMap) | cgroup cur / swap | caps in force (`low`/`max`/`swap.max`) |
|---|---|---|---|---|---|---|---|---|
| llama-server brain, Gemma 4 E4B+MTP `:11434` (`llama-server`) | 902394 | **7,629 MB** | **0** | 7,640 | 1,908 | **2,909 MB** | 3,935 / 0 | 6G / ∞ / **0** |
| Kokoro sidecar `:10201` (`kokoro-tts`) | 376832 | **1,907 MB** | **0** | 2,156 | 0 | 710 MB | 1,828 / 0 | 3G / 4G / **0** |
| zoe-data `:8000` (`zoe-data`, py3.12 venv) | 358156 | **1,071 → 1,171 MB** | **0** | 1,461 | 0 | 0 | 1,181 / 0 (incl. 2 `pi` children) | 2G / ∞ / **0** |
| ↳ 2 × `pi` RPC children of zoe-data | 375087, 376411 | 48 + 43 MB | 0 | 118 / 122 | 0 | 0 | (in zoe-data) | (zoe-data's) |
| FunctionGemma router `:11436` (`functiongemma-router`) | 830863 | 367 MB | **0** | 675 | 0 | 76 MB | 287 / 0 | 768M / 1G / **0** |
| flue-zoe-brain-2x `:3579` | 341226 | 104 MB | 0 | 136 | 0 | 0 | 108 / 0 | 512M / 2G / 0 |
| flue-zoe-telegram `:3582` | 1409086 | 80 MB | 0 | 113 | 0 | 0 | 86 / 0 | 256M / 1G / 0 |
| homeassistant (container) | 4133 | 132 MB | **311 MB** | 439 | 0 | 0 | 143 (anon 131) / **321** | none |
| music-assistant `mass` (container) | 891285 | 129 MB | **234 MB** | 833 | 0 | 0 | 147 (anon 128) / **254** | none |
| omnigent server + host (container, root uv tool) | 1400068, 1400231 | 19 + 32 MB | 127 + 56 MB | 159 / 94 | 0 | 0 | 332 (anon 45, file 279) / **197** | none |
| ytmusic-potoken `node build/main.js` (container) | 872008 | 48 MB | 61 MB | 213 | 0 | 0 | 85 (anon 44) / 61 | none |
| homeassistant-mcp-bridge `:8007` (container) | 3599412 | 48 MB | 3 MB | 51 | 0 | 0 | 59 / 4 | none |
| zoe-database pgvector (container) | — | — | — | — | — | — | 68 (anon 21) / 90 | none |
| github-runner `Runner.Listener` | 1393948 | 45 MB | 30 MB | 99 | 0 | 0 | 52 / 49 | none |
| dockerd + containerd | 1386, 1041 | 31 + 24 MB | 37 + 12 MB | — | — | — | 108 / 130 + 102 / 35 | none |
| **dev:** `codebase-memory-mcp` (one, capped scope) | 3312734 | **506 MB** | **767 MB** | 506 | 0 | 0 | 512 / **768** (at its swap cap; 656k `memory.high` events, 0 OOM) | high 512M / max 768M / swap.max 768M |
| **dev:** `ccd-cli` 2.1.286 (the measuring session's host) | 3312643 | 319 → 411 MB | 1 → 74 MB | 353 | 0 | 0 | session scope 560 | none |
| **dev:** `serena-mcp` (shared) + `jedi-language-server` | 2671375, 2684169 | 189 + 43 MB | 0 | 194 | 0 | 0 | 230 / 0 | 0 / 2G / 0 |

**Docker total** (cgroup `anon`): ≈ **454 MB** resident anon and ≈ **717 MB** swap, spread
over 12 running containers. LiveKit is `Exited (137) 5 days ago`, which means the on-demand
reap still works.

**Gone since 07-06.** `openclaw-gateway` (was 94 MB + 392 MB swap) and the Hermes gateway
(was 54 MB + 167 MB swap) are not running. The 19-process `ccd-cli` fleet is down to one.
The box is already headless (`multi-user.target`), so the state-of-Zoe "headless target"
step yields nothing.

## Per-process detail

### llama-server brain — confirmed as the plan states, plus one fact the operator must see

- Measured `VmSwap` **0**, `VmLck` 1.91 GB, `memory.swap.max=0`. The plan's "do not touch"
  stands, and nothing below changes the rock's model or flags.
- Composition from `smaps`:
  - mlocked GGUF mapping: **1,872 MB** (plus 36 MB for the MTP draft)
  - `[heap]`: **2,578 MB** (glibc brk heap)
  - anonymous: 145 MB
  - dmabuf: 79 MB
  - NvMap: ~2.9 GB (weights, KV and compute on the GPU)
- **The brain grew +2.2 GB since its 2026-09-27 load.**
  [brain-flags-tuning-2026-09.md](brain-flags-tuning-2026-09.md) measured this build at
  **5,450 MB right after load** and 5.89 GB after 57 min. Today it is **7,629 MB**, which is
  also its `VmHWM` (7,640 MB), so it has risen monotonically.
- That growth matches the host-RAM **prompt cache** `--cache-ram 2048` reaching its cap.
  - The source confirms it is a lazily filled `std::list` of slot states, evicted at the
    limit (`tools/server/server-task.cpp` in `~/llama.cpp-b11194`).
  - A 2 GB cap plus ~0.2 GB of other heap fits the 2.58 GB `[heap]`.
  - This is an inference from RSS plus source. The occupancy lines themselves log at
    `SRV_TRC`, so they are invisible at the default verbosity. The 24 h occupancy read listed
    as a B0.4/B6.6 follow-up has still not been done.
- This one flag accounts for the gap between B0.1's "idle MemAvailable 2.5–2.7 GB" and
  today's figure.

### Kokoro sidecar (PyTorch on CUDA)

- At sample time: `VmRSS` 1,907 MB, made up of 1,197 MB `smaps` Rss (anon 1,183 MB) and
  ~710 MB NvMap. `VmHWM` 2,156 MB. Up 3.5 days. Swap 0.
- This is the *fresh* end of the range #1715 measured for PyTorch: 2.07–2.14 GB fresh, and
  2.7–3.0 GB after heavy use.

### zoe-data (py3.12 venv, Moonshine in-process)

- `VmRSS` 1,071–1,171 MB, almost all anonymous (`[anon]` 997 MB + `[heap]` 65 MB). `VmHWM`
  1,461 MB. Swap 0. 91 threads.
- File-backed RSS is ~0, so its library code pages were evicted under pressure (see
  *Host snapshot*).
- `MALLOC_ARENA_MAX=2` and `MALLOC_TRIM_THRESHOLD_=131072` are **confirmed in the live
  `/proc/358156/environ`**.
- The arena fix is still in force. Thirteen anonymous regions over 16 MB account for 774 MB.
  The largest are 171 MB and 133 MB, which fits ORT session arenas and model buffers rather
  than glibc fragmentation.
- No CUDA or NvMap mappings. **No torch** is mapped.

## What loads memory inside zoe-data (measured 2026-10-03)

Library inventory comes from `/proc/358156/maps`. The packages with mapped `.so` files are:
psycopg2-binary, numpy (OpenBLAS), pillow, sqlalchemy, aiohttp, asyncpg, onnxruntime 1.23.2,
`moonshine_voice` (its own bundled `libonnxruntime`), tokenizers, `chromadb_rust_bindings`
(chromadb **1.5.9**, the B0.8 store), grpc, cryptography, pydantic-core, uvloop and
py-rust-stemmers. There is **no `torch`**.

| Resident component | Evidence | Size |
|---|---|---|
| **Moonshine v2 Medium streaming** (the STT rock, in-process singleton) | `ZOE_MOONSHINE_ARCH=MEDIUM_STREAMING`. The `.ort` weights are read into anonymous memory, with no file mapping and no open fd. Bundle: `adapter` 3.6 + `cross_kv` 11.6 + `decoder_kv` 147 + `encoder` 94.7 + `frontend` ~12 MB | **~270 MB of weights** + ORT arenas |
| fastembed `bge-small-en-v1.5` (quantized), semantic router | `/tmp/fastembed_cache/models--qdrant--bge-small-en-v1.5-onnx-q`, 66 MB blob | ~66 MB + session |
| Chroma MiniLM-L6-v2, one cached `_ZoeMiniLM` (`memory_service._drawers_embedding_function`) | `~/.cache/chroma/onnx_models/.../model.onnx`, 90 MB | ~90 MB + session |
| Chroma HNSW (2 collections, `~/.mempalace`) | `data_level0.bin` 36.2 MB + 2.3 MB, held open | ~40 MB |
| Python heap, everything else | remainder of the ~1.07 GB anonymous | **~0.5 GB** |

**The engineering harness (W3.5 fence-out target).** These are the modules named in
tech-debt Wave 4: `greploop_guard`, `pipeline_*` (11), `multica_*` (10), `executors/*`,
`worktree_bootstrap`, `omnigent_issue_executor`, `pi_executor`, `kanban_phase_budget` and
others. That is **31 files and 615 KB of source**. Measurements:

- **Third-party imports (AST scan):** only `httpx`, `pydantic`, `apscheduler` and `asyncpg`.
  All four are shared with the rest of zoe-data. The harness pulls in **no heavy dependency
  of its own**.
- **Code-object footprint:** compiling all 31 files under `tracemalloc` in a separate
  stdlib-only interpreter keeps **2.0 MB** of code objects. Executed module namespaces add
  roughly another 2–3×, so the resident import cost is **≲10 MB**.
- **Runtime state:** the Multica poll is idle. The live log reads
  `multica_poll: runtime dispatch pause active (paused)`.
- **Not purely engineering:** `multica_autopilot_sync` runs a **product** job every few
  minutes (`autopilot: reminder_scan complete`). A fence-out would have to keep that in
  process or move it with care.

**Conclusion for W3.5:** fencing the harness out frees essentially nothing at steady state.
Its real value is **peak isolation**. Harness work spawns git, pytest and agent subprocesses
inside zoe-data's cgroup, which is `MemorySwapMax=0`, so the voice path takes the burst.
That is still worth doing, but it should be re-scoped from "RAM reclaim" to "blast-radius
isolation", and it must **not be counted toward the 2 GB**.

**The two `pi` children** (93 MB resident, unswappable because they sit inside zoe-data's
cgroup):

| | `375087` | `376411` |
|---|---|---|
| cwd | `/home/zoe/assistant` | `services/zoe-core` |
| spawned by | `pi_intent_classifier` (`ZOE_PI_INTENT_TRANSPORT=rpc`, `ZOE_PI_INTENT_SHADOW_ENABLED=true`) | `zoe_core_client` (`--mode rpc`) |

Both were spawned lazily about 28 min after zoe-data started. The brain lane is
`ZOE_BRAIN_BACKEND=flue`, but `zoe_core_client` is still imported by `brain_dispatch`,
`expert_dispatch`, `voice_tts` and `voice_livekit`. Whether the core child is reachable on a
live path needs a code trace before anyone retires it.

## Dev tooling on the box (not Zoe's cost, but on the box)

| | count | resident | swap |
|---|---|---|---|
| `ccd-cli` sessions | **1** (the measuring session; 07-06 had 19) | 319–411 MB | 1–74 MB |
| `codebase-memory-mcp` | **1** (one per agent session, via `codebase_memory_capped.sh` scope: `MemoryHigh=512M`, `MemoryMax=768M`, `MemorySwapMax=768M`) | 506 MB, pinned at `memory.high` | **767 MB, at its swap cap** |
| `serena` | **1** shared server (doctrine holds: `pgrep` shows exactly one) + jedi LS | 232 MB | 0 |
| Claude remote `srv` | 1 | 25 MB | 0 |

**What one agent session costs.** It is about **0.85–0.95 GB resident** (ccd-cli plus its
codebase-memory), plus ~0.77 GB of swap. That swap mostly lands in zram, where it costs
about 0.2 GB of real RAM at 3.9:1. The shared Serena adds 0.23 GB whenever any agent is
attached. **So "~1 GB per agent session" is confirmed by measurement.**

Every headroom number in this record was taken with that session present. With no agent on
the box, the estimate is MemAvailable ≈ **1.5–1.7 GB**: today's 0.42–0.56 GB plus
~0.85–0.95 GB resident, plus ~0.2 GB of zram slots. That figure is an estimate and was not
measured, because measuring it would require stopping the session.

## Ledger against 2026-07-06

| Owner | 07-06 RSS + swap | 10-03 RSS + swap | Δ footprint | Δ resident |
|---|---|---|---|---|
| zram physical cost | 3.9 GB | 0.50 GB | — | **−3.4 GB** (B0.1) |
| ccd-cli fleet / dev tooling | 1.0 + 3.59 = 4.59 GB | ccd-cli 0.32 + cbm 0.51/0.77 + serena 0.23 = 1.83 GB | **−2.76 GB** | +0.06 GB |
| openclaw + hermes gateways | 0.15 + 0.56 = 0.71 GB | 0 | **−0.71 GB** | −0.15 GB |
| llama-server brain | 5.35 + 4.14 = 9.49 GB | 7.63 + 0 | −1.86 GB | **+2.28 GB** (swap denial + full prompt cache) |
| Kokoro | ~0 + 0.63 GB (07-06 was swapped/idle) | 1.91 + 0 | +1.28 GB | **+1.91 GB** (swap denial; CUDA-resident) |
| zoe-data (+ pi children) | 0.09 + 1.00 = 1.09 GB | 1.16 + 0 | +0.07 GB | **+1.07 GB** (swap denial) |
| router, flue-2x, flue-telegram | 0.07 GB (flue 1.x) | 0.55 GB | +0.48 GB | +0.54 GB (router is new since 07-06) |
| HA + MA + omnigent + docker rest | ~0.5 + ~0.9 swap | ~0.45 + ~0.72 swap | ≈0 | ≈0 |
| **MemAvailable** | **2.1 GB** | **0.42–0.56 GB** | | **−1.6 GB** |
| **swap used** | **11.8 GB** | **2.1–2.7 GB** | | |

The voice stack is about **+5.8 GB more resident** than on 07-06. That is the point of
#1409 and the 2026-08-03 caps: on 07-06 it was 96% paged out and replies came back chopped.
The reclaims since then (zram, the agent fleet, the retired gateways) total **≈6.9 GB of
footprint and ≈3.5 GB of RAM**. They paid for that pinning and for the brain's prompt
cache, and left about 1.6 GB *less* headroom than before.

## Ranked reclaim list (cheapest-and-safest first)

"RAM" means resident memory freed, which is what moves MemAvailable. "Swap" frees zram
capacity at about 0.26 GB of RAM per GB, until other cold pages refill it.

| # | Item (W3 mapping) | RAM | swap | Risk | Who decides |
|---|---|---|---|---|---|
| 1 | **Keep agent sessions off the box when no engineering is running** (dev-tooling hygiene, W3.1 continuation). One session ≈ ccd-cli + codebase-memory; Serena is shared. | **~0.85–0.95 GB** per session (+0.23 GB Serena if none attached) | ~0.8 GB | zero product risk | operator habit; **not a product reclaim** |
| 2 | **Music Assistant + ytmusic-potoken reap** (W3.3). Usage signal: last playback in the MA log was 2026-09-29, idle 4 days. | **~0.18 GB** (anon 128 + 44 MB, plus file) | ~0.31 GB | low–medium. A music request pays an MA cold start. The listening-journal observer polls MA every 300 s and must tolerate it being down. The panel AirPlay speaker goes through MA. | product (music cold-start latency) |
| 3 | **Omnigent container idle-reap** (new; review fleet, not Zoe runtime) | ~0.05 GB anon (+0.28 GB file cache) | ~0.20 GB | zero product risk; cross-review waits on a start | operator |
| 4 | **Retire or lazy-exit the 2 `pi` RPC children** (new) | **~0.09 GB**, unswappable today | 0 | low for the shadow classifier (log-only); unknown for the core child until traced | flag / code (operator) |
| 5 | **zram off, swapfile only** (W3.4b; W3.4 itself is **done**, see below) | up to **~0.50 GB** | moves 1.95 GB of compressed pages to NVMe | medium: slower swap-in for the *unprotected* processes only, since the voice units hold 0 swap. `swapoff` needs RAM to drain, so use a quiet window. | operator (root) |
| 6 | **Brain prompt cache `--cache-ram 2048 → 1024`** (not a W3 row; the plan says do not touch the brain) | up to **~1.0 GB** | 0 | Rock launch flag. B6.6's simulation lost 1/40 cache hits at 1024 MiB on the old build. b11194 entries are ~2.7× larger, so the miss rate is unmeasured; each miss costs a ~2.7k-token re-prefill, ~4–5 s. **Needs the 24 h occupancy read first.** | operator (brain window, replay-gated) |
| 7 | **Kokoro → ONNX Runtime** (#1715, draft) | **0–0.35 GB today** (PyTorch is at its fresh 1.91 GB; ONNX fp16 measured 1.54 GB ready → 1.89 GB after 60 synths). Up to ~1 GB only against a long-running PyTorch at 2.7–3.0 GB. | 0 | **high:** fp16 returns NaN (silence) on 3/32 phrases; first audio on novel text is 1.4–1.9× slower; CPU EP fails RTF < 0.3 | **product.** Same model and voices, so it passes `test_tts_rock_is_kokoro`, but `docs/CANONICAL.md`'s TTS row says "PyTorch on CUDA" and would have to change; replay gate required. #1715 itself concludes "not a win". |
| 8 | **Home Assistant reap** (W3.3) | ~0.13 GB | ~0.32 GB | **NOT acceptable.** HA is the house controller: localtuya devices, the `zoe_conversation` and satellite wake/turn event-bridge automations, and the `homeassistant-mcp-bridge` that zoe-data polls (`GET :8007/entities` every few seconds). A reaped HA is a house that ignores its switches, for ~0.13 GB. | house: **recommend closing this half of W3.3 as "won't do"** |
| 9 | **Harness fence-out** (W3.5) | **≈0 steady state** (≲10 MB of modules; poll paused) | 0 | medium (prod refactor, replay-gated) | re-scope as peak isolation; **do not count toward 2 GB** |
| 10 | zoe-data restart | transient only (1.07–1.17 now vs 1.46 HWM; regrows) | 0 | outage of the whole product | not a reclaim |

### zram (W3.4): effectively done

The 2026-07-06 candidate ("3.9 GB of RAM stores compressed swap") is resolved. zram now costs
**503 MiB**, down from ≈3.9 GB, which returned ≈3.4 GB. It is full and compresses at 3.9:1.
Overflow goes to the NVMe swapfile, which holds only ~0.19 GB, and swap-in at sample time
was 0.2–6 MB/s.

The only zram lever left is item 5 (zram off, ~0.5 GB). The §6 row should be closed as done
via B0.1, with this record as the evidence.

## Projected total and DoD verdict {#dod-verdict}

**Safe product-side items with no product or house decision** (3 + 4) come to about
**0.14 GB**. Adding **MA reap** (2, a product call on music cold start) gives about
**0.32 GB**. Adding **zram off** (5, operator/root) gives about **0.8 GB**.

| Scenario | Est. MemAvailable |
|---|---|
| Today, agent session present | 0.42–0.56 GB |
| + items 2–5, session present | **~1.2–1.4 GB** |
| + items 2–5, no agent session on the box | **~2.1–2.5 GB** |
| + item 6 (`--cache-ram 1024`) | **+ up to ~1.0 GB** on any of the above |

**DoD as literally written** ("≥2 GB freed and swap < 6 GB at steady state"):

- Swap is **2.1–2.7 GB**, which is **< 6 GB ✅**.
- "≥2 GB freed" is met if "freed" means reclaims executed: zram returned −3.4 GB of RAM,
  and the agent fleet plus retired gateways returned −3.5 GB of footprint.

**DoD as the gate exists for** (§7: "124 MB available … brain CUDA-OOM … three deploy-gate
failures"): **NOT met.**

- MemAvailable is **0.42–0.56 GB**, lower than the 2.1 GB of 2026-07-06.
- The nightly replay gate skips below 700 MB. It logged 40 consecutive SKIPs before 09-25,
  passed 09-25 → 09-29, and has **timed out every night 09-30 → 10-03**: 30 min, 9 s of CPU,
  no output after the Postgres wait. That is a separate defect and is not diagnosed here.
- W4 needs 100–300 MB *resident during utterances* on top of this.

Per §7 an agent may not call this close enough, so **W4.1 stays blocked** and the
shortfall goes to Jason.

**Is ~2 GB of headroom reachable without touching the brain?** Only by counting the
agent-session quiesce (item 1), which is operational hygiene rather than product reclaim:

- **With an agent session on the box, no:** ~1.2–1.4 GB at best.
- **The lever that closes the gap on its own is item 6**, the brain's prompt cache. It is
  a launch flag of the rock, which the plan reserves.

**Decisions for the operator:**

1. **Which metric is the W3 gate?** Recommendation: MemAvailable ≥ 2 GB at steady state with
   the voice stack resident, measured both with and without one agent session, so the
   literal "freed vs 07-06" reading cannot open W4.1 on a 0.4 GB box.
2. **`--cache-ram`:** run the 24 h occupancy read (B6.6 follow-up), then decide 2048 vs
   1024. This is the single largest swing since B0.1.
3. **Music Assistant reap** (W3.3 product call on cold start) and **close HA reap as won't-do**.
4. **zram off** (root, quiet window).
5. **Kokoro ONNX:** a product decision, which #1715's own numbers do not support today.
6. **Agent hygiene:** whether engineering sessions may stay resident on the product box.

## Repo-only zero-risk reclaim?

**None found.**

- Every unit on the voice path already carries `MemorySwapMax=0`, pinned by
  `tests/unit/test_systemd_memory_protection.py`.
- codebase-memory and Serena are capped.
- Each remaining item changes live behaviour (a reap, a flag, a rock launch flag or a root
  swap change), so it is an operator decision rather than a zero-risk repo change.
- A Music Assistant `mem_limit` would be a *cap*, not a reclaim. MA currently holds 128 MB
  of anon, against ~1.8 GB on 2026-09-25 before #1723 re-created it, so a cap only becomes
  worth having if MA regrows.

Related: [2026-07-06 profile](memory-pressure-profile.md) ·
[brain flags tuning](brain-flags-tuning-2026-09.md) ·
[Kokoro ONNX evaluation (#1715, draft)](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1715) ·
[state-of-Zoe review §3](state-of-zoe-review-2026-09-25.md) ·
[voice pipeline](voice-pipeline.md)
