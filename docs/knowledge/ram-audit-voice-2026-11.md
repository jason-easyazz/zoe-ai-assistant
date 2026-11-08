---
type: reference
title: RAM audit follow-up (voice path) - brain --cache-ram 1024 to 512, Kokoro post-warm growth
description: Measured 2026-11-08 on the live Orin from the 2026-11-08 RAM audit. Brain prompt-cache baseline and why 512 MiB is expected to hold; Kokoro's RSS history (no post-warm leak, the "1.5 to 2.8 GB" is a one-off warm-up step) and the one remaining lever (idle CUDA-pool trim), shipped default-off.
---

# RAM audit follow-up, voice path (2026-11-08)

Source: `~/.zoe/agent-tools/ram-audit-2026-11-08.md` items 5 and 2. The rocks are untouched:
only a cache cap and an optional allocator trim change. Companion record for the non-voice
items: [ram-audit-nonvoice-2026-11.md](ram-audit-nonvoice-2026-11.md).

**Outcome**

| item | decision | est. saving | state |
|---|---|---|---|
| brain `--cache-ram` 1024 to 512 | adopt in the template; operator applies the unit in a replay-gated window | up to ~0.5 GB once the cache is full | NOT applied live |
| Kokoro bounded growth | no leak exists; ship an idle CUDA-pool trim **default off** (`ZOE_KOKORO_IDLE_TRIM_S`), A/B harness included | unmeasured (needs a throwaway Kokoro, 2.3 GB, MemAvailable never reached the 3 GB gate while Kokoro served) | flag dark |

## 1. Brain `--cache-ram`

Instruments (read-only): `/metrics`, the journal of `llama-server.service` (the brain is the
b11982 build; log lines at default verbosity are slot selection, per-task `prompt eval time`,
and `making room for prompt cache entry` evictions). `scripts/maintenance/brain_cache_report.py`
prints all of it as JSON.

Baseline, brain PID 864820 (up since 2026-11-07 02:57, ~16 h of audit data at 19:00 on 11-08, journal back to 11-06):

| measure | value |
|---|---|
| `prompt_tokens_cached_total` / (cached + processed) | 2,947,220 / (2,947,220 + 509,320) = **85.3 %** (audit read 85 %) |
| tasks in the retained journal | 2,999 |
| tasks that re-prefilled >= 1000 tokens (main-turn miss) | 268 = **8.9 %** |
| tasks that re-prefilled 300-999 tokens (aux miss) | 275 = 9.2 % |
| cache evictions since the process started | **10**, all after 2026-11-08 14:51 |
| evicted entry sizes | 6.2 - 59.1 MiB (mean 24.8, total 247.6 MiB) |
| `n_tokens_max` | 5,827 of 8,192 |

What this says about 512 MiB:

- The 1024 MiB cap **first filled about 36 hours after start** (first eviction 14:51 on 11-08,
  process start 02:57 on 11-07). Entries are lazy and evicted oldest-first, so until then the
  cap was never binding.
- Every eviction so far removed a small aux one-shot (6-59 MiB), never a ~81 MiB main-turn
  entry (a main turn is refreshed on each use, so it is not the oldest).
- ~1024 MiB filled over ~36 h, about 28 MiB/h averaged over the whole life. At that average a
  512 MiB cap evicts entries older than about 18 h. At the busiest observed rate (100 MiB in
  30 s during a probe burst at 15:01) it is a few minutes, but that burst is harness traffic,
  not conversation.
- Cost of a miss is known: ~4 s on one turn (B6.6: cache off = +4.1 s every repeat turn;
  one cold turn here).

Caveat, stated plainly: cache **hits are not logged** at default verbosity, so this is an
indirect argument (eviction sizes + fill time), not a direct hit-retention simulation. The
adoption therefore carries a revert rule and a 24 h read.

**Change.** `scripts/setup/systemd/llama-server.service` `--cache-ram 512`; `tests/unit/
test_llama_server_unit_flags.py` pins 512 with `--swa-full`. The installed unit's effective
ExecStart is the host drop-in `llama-server.service.d/80-swa-full.conf` (a full `ExecStart=`
override with `--cache-ram 1024`); its non-comment ExecStart lines are identical to the
template, so applying the template and deleting that drop-in is the whole change.

**Measurement plan (voice path: the lander probes, nothing is hand-restarted).**

1. Before: `python3 scripts/maintenance/brain_cache_report.py` (baseline above is the reading).
2. Land the PR through `land_voice_pr.sh` (replay gate: said-vs-did and per-stage speed must
   not regress).
3. Apply the unit in the same Kokoro-paused window as any brain restart:
   `install -m 644 scripts/setup/systemd/llama-server.service ~/.config/systemd/user/`,
   `rm ~/.config/systemd/user/llama-server.service.d/80-swa-full.conf`, `daemon-reload`,
   restart under `night_busy.sh voice` (which holds voice work to 22:00-07:00) and never inside 01:50-04:55.
4. After 24 h of real use: `brain_cache_report.py --since "<restart time>"`. **Revert to 1024**
   if the >= 1000-token re-prefill share rises materially above 8.9 % (say past ~12 %) or
   the reuse ratio falls below ~80 %. Check the brain cgroup `memory.current` against the
   ~6.6 GB audit figure: the saving appears only once the cache is full (~18 h in).

## 2. Kokoro post-warm growth

Method: the 5-minute `memhist` cron log (`~/.zoe-logs/memhist`, 2026-11-06 .. 11-08), grouped
per PID.

- **Steady state is flat, not growing.** Long-lived sidecars: PID 1385602 (47 samples, ~4 h)
  2185 to 2067 MB; PID 31069 2154 to 2037; PID 1980207 2328 to 2316. Typical plateau
  2.1 - 2.25 GB.
- **The 1.3 to 2.2 GB step is first-use warm-up**, not drift: PIDs 3980038 and 4140066 started
  at 1279 / 1333 MB and were at 2180 MB within the hour, then stayed flat. That is lazily
  loaded CUDA kernels/cuDNN/cuBLAS state and the first activations, which do not go back.
- The 2.5 - 2.7 GB readings are a handful of night-window samples (PID 2165497: 2282 to 2498
  over 03:00 - 07:55 on 11-07) and one 2706 MB sample (PID 3707648, 11-07 15:15). No sample
  went to 2.8 GB; no monotonic climb over a day.
- Three candidates in the audit are already settled in the tree:
  - `MALLOC_ARENA_MAX=2` + `MALLOC_TRIM_THRESHOLD_=131072` is **already live** on the Kokoro unit
    (`kokoro-tts.service.d/40-memory-tuning.conf`, B6.6: -73 to -112 MB). The audit's "the Kokoro
    unit does not appear to have it" is wrong.
  - `torch.cuda.empty_cache()` **already runs before every synth** (`_blocking_synthesize`).
  - `PYTORCH_CUDA_ALLOC_CONF` expandable_segments / max_split_size tune the native caching
    allocator; the sidecar sets `backend:cudaMallocAsync` (Jetson NVML assertion workaround),
    so those knobs do not apply.
- The only gap left: the pre-synth trim means the **last** synth's pool stays resident until the
  next request. `ZOE_KOKORO_IDLE_TRIM_S=N` (default 0 = off) trims once after N idle seconds,
  off the hot path under `_pipeline_lock`; the next synth's own pre-trim then finds nothing, so
  total work per request is unchanged. It cannot reclaim the warm-up step above.

**Not measured, and why.** A throwaway Kokoro is ~2.3 GB; the rule is MemAvailable >= 3 GB and
the voice-harness lock. While Kokoro serves, MemAvailable sat at 0.7 - 1.6 GB all evening
(only 3.0 GB for a moment, with Kokoro paused by a landing that held the lock). So the lever is
shipped dark with `scripts/perf/measure_kokoro_idle_trim_ab.py` (interleaved off/3 s runs,
isolated cache dir, own PID only). Turn it on, in `kokoro-tts.service.d`, only if that A/B shows
RSS-after-idle lower with synth p50/p95 within noise, then replay-gate.

## Verification

- `pytest tests/unit/test_llama_server_unit_flags.py tests/unit/test_kokoro_idle_trim.py
  tests/unit/test_kokoro_cache.py tests/unit/test_kokoro_startup.py`
- `python3 scripts/maintenance/brain_cache_report.py` (read-only)
