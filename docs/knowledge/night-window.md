---
type: Runbook
title: The 12B night window (owner's page)
description: A nightly window in which Zoe sleeps (voice, the app, Telegram brain replies and the live 4B are down for about an hour), the 12B runs the night jobs (the nightly digest, zoe-nightly-dreaming, the night-mind pass) at a bigger context, and everything is woken again, brain first, each unit's own /health polled. What sleeps and when, the RAM arithmetic and the levers, the safety properties, how to dry-run, trial, install, abort and read the night report, the first real attempts (2026-10-09: the 12B did not load; why that is not a RAM-arithmetic problem) and the ordered next experiments.
tags: [night, 12b, dreaming, brain-window, runbook, owner, memory, night-mind]
timestamp: 2026-10-09T02:30:00+08:00
---

# The 12B night window

Owner priority, 2026-10-09 01:30: "using the 12B for dreaming and improving Zoe during the night needs to be a priority." Research: `docs/research/night-mind-2026-10-09.md` (section 8.3), `docs/research/memory-bakeoff-decision-2026-10-08.md` (the 12B preflight arithmetic). Built on the bake-off runner's pieces (`scripts/perf/zmb/bakeoff.py`: the `Host` seam, the brain-window lock, `compact_memory` / buddyinfo, `clone_command` / `deep_clone_command`), not beside them.

**Status: built and tested; the 12B has NOT yet loaded on this box.** Five real attempts on 2026-10-09 (section 8) all died on the 12B's own `cudaMalloc` with 12.2 GB available, and every one restored the box in about 60 seconds. Nothing is installed; the timer does not exist on the host. Read section 8 before installing.

## 1. The commands

```bash
scripts/night/night_window.sh --dry-run          # FIRST. Reads the box, prints the arithmetic for every lever combination, which fits TODAY, the generated 12B command, the plan. Changes nothing.
scripts/night/night_window.sh --trial            # manual, any hour, about 25 min: 12B alone, K1-K5 on it and on the 4B at 32k, restore
scripts/night/night_window.sh                    # the window itself (the timer runs this at 02:50; a manual run outside 01:30-03:40 needs --anytime)
scripts/night/night_window.sh --restore-only     # put everything back (idempotent; a window that is still running is left alone)
scripts/night/install_night_window.sh --dry-run  # then without --dry-run: the OPERATOR installs the two units (section 6)
```

Levers and pins: `--model auto|qat|q4km`, `--ctx 16384|32768`, `--kv q8_0|q4_0`, `--ngl N`, `--no-mlock`, `--fit-off`, `--binary PATH`, `--keep-zoe-data`, `--cap-min`, `--end-by HH:MM`, `--jobs digest,dreaming,night_mind`, `--night-mind-cmd "..."`, `--no-fallback`, `--retry-load`, `--job-reserve-mib`, `--margin-mib`. Every external command goes through the bake-off `Host` seam, so `tests/unit/test_night_window.py` (see the test file) runs the real step logic with a double.

## 2. What sleeps, when, and what is unavailable

| Step | Unit | Stopped | Woken (in this order) | Healthy means |
|---|---|---|---|---|
| 1 | `zoe-data.service` | first | **fourth** | `:8000/health` `status: ok` |
| 2 | `functiongemma-router.service` | second | **third** | `:11436/health` `status: ok` |
| 3 | `kokoro-tts.service` | third | **second** | `:10201/health` `pipeline_loaded` AND `device: cuda` (a CPU Kokoro "works" and chops every reply) |
| 4 | `llama-server.service` (the 4B) | last | **first** | `:11434/health` `status: ok` |

Only units that were **active before** are stopped, and only those are woken. Measured down-times (2026-10-09, five real stop / restore cycles): the brain 31-34 s, Kokoro 48-52 s, the router 51-55 s, zoe-data 55-67 s (from the moment each was stopped).

**Unavailable while Zoe sleeps (about an hour a night):** the panel's voice (wake word, STT, replies, TTS), the web app and every API behind zoe-data (chat, lists, calendar, music, home control), Telegram brain replies (the Flue sidecar's brain is the 4B on `:11434`), the hourly health probes' zoe-data checks (they report red; `zoe-health` only reports), and anything else that calls zoe-data. Nothing is lost: Telegram messages queue at Telegram, and the recurring jobs that need zoe-data (the 03:00 digest, below) are run by the window itself.

**Why zoe-data is stopped by default** (the research listed it as optional): (1) RAM: it holds 1.0-1.4 GB (cgroup `memory.current`), and the 12B needs about 1.3 GB more than the box has with only the 4B and Kokoro stopped (run 2: 8,003 MB available against 9,262 MB needed); (2) **the nightly digest is not a timer job, it is a loop inside zoe-data** (`routers/system.py` `_memory_digest_loop`: sleep until 03:00, `run_nightly_digest_pass`, then the evolution NOTICE and MEASURE phases). With zoe-data running and the brain down at 03:00 that loop would hit a dead model and log `extractor_errors` (a ZERO-EFFECT ALERT). Stopped, the loop is simply not alive, so the window runs the same body itself, standalone (`scripts/night/jobs/night_digest.py`) on the 12B; (3) the jobs are standalone processes and need nothing from it except the weekly index-compaction trigger, which asks zoe-data over HTTP: dreaming runs with `--skip-compaction` and `zoe-nightly-dreaming.py --only-compaction` runs after zoe-data is back (without that, a Sunday's compaction would be silently lost: "index health unavailable" returns 0). `--keep-zoe-data` exists; it is refused when the window would cross 03:00 (its digest loop) or the Sunday 04:00 consolidation.

After a restart zoe-data's digest loop sleeps until the NEXT 03:00 and its in-process "last run" gauges read never-ran, so `GET /api/system/memory-loops/status` shows the digest `stale` until tomorrow 03:00 even though the window ran it. The night report is the truth. If zoe-data was down across 03:00 and the 12B digest did not finish, the window runs the digest again on the 4B after the wake; if zoe-data was back before 03:00, its own loop still fires and the window does not duplicate it.

## 3. The night, reconciled with the timers that already exist

| Time (local) | What | Notes |
|---|---|---|
| 02:00 (+0-5 min) | `zoe-training` | LoRA is off (`ZOE_ENABLE_LORA_TRAINING=false`): about 20 s of data prep. With LoRA on it stops the brain and zoe-data itself; the window refuses while its lock file's pid is alive |
| 02:30 (+0-10 min) | `zoe-backup` | Postgres first, then the palace tarball |
| 02:32 | `zoe-dreaming` | **absorbed**: the installer disables `zoe-dreaming.timer`; the window runs `zoe-nightly-dreaming.py` itself (12B first, then the 4B if the 12B could not). Two runs would double the work and race for the model |
| 02:40 (+0-5 min) | `zoe-memory-export` | its own header wants "a copy BEFORE the digest mutates the store"; the window starts after it, so the export is now a copy before EVERYTHING mutates |
| **02:50 (+0-2 min)** | **`zoe-night-window`** | waits (up to 20 min) for training / backup / export / dreaming to be inactive, up to 30 min for the panel to be quiet (10 min since the last voice turn) |
| 03:00 | (zoe-data's digest loop) | not alive: stopped (the window runs the body) |
| **03:55** | **the window must be over** | hard cap 65 min, shrunk to fit; zoe-data must be back before the Sunday 04:00 consolidation loop and the voice gate |
| 04:10-04:52 | voice gate (Serena restart 04:15, gate 04:30) | a blackout: the window never overlaps it, and a 4B fallback job is cut at 04:10 |

The timer is `Persistent=false` on purpose: a missed 02:50 must not fire at the next boot in the middle of the day. The script also refuses to start outside 01:30-03:40 without `--anytime`, and a refusal for the hour does not start any fallback job.

## 4. The RAM arithmetic and the levers

All figures in MiB (the bake-off divided the file by 1e6 and compared it with MemAvailable in MiB, which overstated every need by about 320 MiB: its -1,259 was really about -936). `need = model file + KV + compute 600 + floor 1,200`, plus 700 for the job processes that run beside the 12B (`NIGHT_JOB_RESERVE_MIB`), measured on MemAvailable after the stops and the compaction. KV = (8,192 x ctx + 189,000,000) x bytes/element (f16 2, q8_0 1.0625, q4_0 0.5625); the constants equal the bake-off's (a test pins that).

The window **chooses by the first row that fits, in this order**: the QAT q4_0 file (6,653 MiB; trained for q4_0 and the smaller file) before Q4_K_M (7,039 MiB; only on `--model q4km`, or if the QAT file is missing); then KV q8_0 before q4_0 (a q4_0 cache costs long-context accuracy; the jobs' prompts are 3-8k tokens, so 16k at q8_0 beats 32k at q4_0); then the larger context. A pin that does not fit is never relaxed. `--parallel 1` always (llama.cpp #28286: draft-MTP with more slots leaks between requests; the 12B has no draft, but one slot is also what the jobs want).

Today's dry run (2026-10-09 02:09, the box as it was, default stop set; "predicted" = MemAvailable now + 0.8 x what the four units hold):

| Levers | model | KV | compute | floor | NEED (load) | + jobs | avail | margin | fits |
|---|---|---|---|---|---|---|---|---|---|
| qat ctx 32768 KV q8_0 | 6653 | 464 | 600 | 1200 | 8916 | 9616 | 10833 | +1217 | **chosen** |
| qat ctx 16384 KV q8_0 | 6653 | 328 | 600 | 1200 | 8780 | 9480 | 10833 | +1353 | fits |
| qat ctx 32768 KV q4_0 | 6653 | 245 | 600 | 1200 | 8698 | 9398 | 10833 | +1435 | fits |
| qat ctx 16384 KV q4_0 | 6653 | 173 | 600 | 1200 | 8626 | 9326 | 10833 | +1507 | fits |
| q4km ctx 32768 KV q8_0 | 7039 | 464 | 600 | 1200 | 9303 | 10003 | 10833 | +830 | fits |

With zoe-data kept the same box predicts 10,006 (QAT at 32k q8_0 +390). It varies by the hour: at 01:49 the box had only 5.6 GB of predicted headroom because 4.6 GB was held by agents, tests and pinned pages ("everything ELSE holds ..." in the dry run names it).

**Calibration of the prediction.** Five real stop cycles measured MemAvailable after the stops at 12,220-12,383 MiB against predictions of 10,493-12,959: within about +-3 % for most; the 0.8 factor is conservative to accurate. The real run does not rely on it: it measures after the stops and chooses on that. The prediction only (a) prints the table and (b) refuses BEFORE anything is stopped when even "every stopped unit frees all it holds" cannot fit the smallest set.

**Memory the arithmetic cannot see (the finding of section 8).** The 12B's weights are ONE CUDA buffer. The window therefore also requires MemFree (not just MemAvailable) >= the buffers and, as a hypothesis the first success will calibrate, MiB in free blocks of 2 MB and up (`/proc/buddyinfo` order >= 9); it repeats `sync; drop_caches; compact_memory` up to six times to get there, logs each figure, and records NvMap's own answer (`sudo -n cat /sys/kernel/debug/nvmap/iovmm/free_size`, read-only). None of these is a gate: the load is still attempted.

## 5. The jobs, the model URL and the accounting

Order: **digest, dreaming, night-mind** (dreaming's phase 1 is documented as "after fact extraction"; the night-mind reads what they stored).

| Job | Command | 12B timeout | If it fails on the 12B |
|---|---|---|---|
| `digest` | `scripts/night/jobs/night_digest.py` (the body of `_memory_digest_loop`: `run_nightly_digest_pass`, `record_digest_run`, then the evolution NOTICE and MEASURE phases; prints COUNTS ONLY) | 25 min | runs again on the 4B after the wake, but only if zoe-data was down across 03:00 |
| `dreaming` | `scripts/maintenance/zoe-nightly-dreaming.py --skip-compaction` | 20 min | runs again on the 4B (the full script, compaction trigger included) |
| `night_mind` | `scripts/maintenance/zoe-night-mind.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens <served ctx> --all-members --decode-tok-s <measured>` once that file exists; `--night-mind-cmd` / `NIGHT_MIND_CMD` overrides (`{model_url}`, `{model_url_v1}`, `{ctx_tokens}`, `{decode_tps}`) | 25 min | none (new; 12B only; exit 2 = model unreachable, nothing written) |

Every job gets `GEMMA_SERVER_URL=http://127.0.0.1:11500/v1` (memory_digest's `_GEMMA_URL` reads it; `{model_url}` is the same without `/v1`), `MEMORY_DIGEST_MODEL`, `MALLOC_ARENA_MAX=2`, and **`ZOE_DIGEST_LLM_TIMEOUT_SCALE`**: the digest's model calls were sized for the 4B (45 s for a 512-token extraction already needs about 11 tok/s). The scale is `ceil(1.5 x 11.4 / measured decode tok/s)` from one fixed 1.5k-token speed probe the window runs when the 12B is healthy, in the job processes only; the live service never sets it (its timeouts are unchanged byte for byte, a test pins that every call goes through `_llm_timeout`). The night-mind entry point and its contract (flags, one JSON object on stdout, exit codes) are on `feat/night-mind-v1-reflection-pass` (not merged at the time of writing; read from the other agent's worktree, never edited): until `scripts/maintenance/zoe-night-mind.py` exists, or `--night-mind-cmd` is set, the job is reported `skipped`, never faked. Open question for when it lands: that contract says the nightly digest already calls `night_mind.run_for_user` in line (at the digest's own context), so a window that also runs the standalone pass at 16k/32k would reflect twice; decide which one a window night keeps. The trial additionally scores the pass's own cells (`zoe-night-mind.py --cells`, K1-K12) on each model when the file exists.

Per job the report records wall time, prompt and generated tokens (the 12B's `/metrics` counters, before and after), the MemAvailable low (sampled every 5 s), the status (`ok`, `timeout`, `degraded` rc 3, `failed(rc)`, `killed`, `skipped`) and the `NIGHT_JOB ...` lines the job printed (counts, never text; the full output is in `~/.zoe/night-window/logs/`, mode 0600, and is not copied anywhere).

## 6. Install, abort, uninstall

```bash
scripts/night/install_night_window.sh --dry-run   # prints every step
scripts/night/install_night_window.sh             # adds zoe-night-window.service/.timer to ~/.config/systemd/user, daemon-reload,
                                                  # disables zoe-dreaming.timer (the window absorbs it), enables the window timer, records what it changed
scripts/night/install_night_window.sh --check     # read-only status
scripts/night/install_night_window.sh --uninstall # removes the two units, re-enables zoe-dreaming.timer if the install disabled it
```

The installer adds two new files and flips one timer; it edits no existing unit and no drop-in. It warns when the parked 12B unit, the model file, the py312 venv or passwordless sudo is missing. The service is `Type=oneshot`, `TimeoutStartSec=6000`, and `ExecStopPost=night_window.sh --restore-only` runs on every way out (success, failure, timeout, kill).

**Abort a running window:** `systemctl --user stop zoe-night-window.service` (or Ctrl-C a manual run): the script's own restore path runs; the stop's `ExecStopPost` runs `--restore-only` behind it, which does nothing if the window's process is still alive and wakes exactly the marker's units if it is not. Hard kill / reboot: `scripts/night/night_window.sh --restore-only` (the marker `~/.zoe/night-window/WINDOW_OPEN` lists each unit BEFORE it is stopped).

## 7. Safety properties (each has a test that goes red when it is removed)

Refuses before stopping anything: the brain-window lock (`/tmp/zoe-brain-window.lock`, shared with landings, the samantha bar and the bake-off) is held; the hour (01:30-03:40, `--anytime` overrides); fewer than 35 minutes before 03:55; a nightly timer job or LoRA training is running (waits 20 min first); the panel had a voice turn in the last 10 minutes (waits 30 min); fragmented RAM and no passwordless sudo; a missing parked unit / binary / model file / inactive brain; even "every stopped unit frees ALL it holds" cannot fit the smallest lever set; **the 12B failed to load on the last two windows on this boot** (`~/.zoe/night-window/load_failures.json`; `--retry-load`, `--trial` or a reboot resets it) so Zoe is not put to sleep every night for a load that fails.

During: the MemAvailable floor (1,200 MiB) is checked every 5 s once the 12B is loaded and kills the running job and ends the window if breached; the hard cap; every exception, signal and refusal after the first stop runs the restore. Wake: each unit's own `/health` (not `systemctl is-active`), one retry with a restart (a fresh compaction before the brain), then **loud**: `*** RESTORE FAILED ***` lines, `~/.zoe/night-reports/ALARM` naming the units and the exact commands, the marker kept so `--restore-only` retries exactly those, exit 4 (a failed unit in `systemctl --user`). The bake-off's restore never checked anything but the brain. A unit that is down never stops the others from being woken.

## 8. The first real attempts (2026-10-09 01:57-02:06) and why the 12B did not load

Five `--trial` runs, each: preflight ok, four units stopped, RAM compacted, **12,220-12,383 MiB available, 12,647-12,757 MiB free**, the 12B started from the parked unit's command, healthy never reached, aborted, everything restored healthy in 55-67 s. Every one died the same way, about 13 s after the process started:

```
NvMapMemAllocInternalTagged: 1075072515 error 12      (twice)
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 6637.69 MiB on device 0: cudaMalloc failed: out of memory
alloc_tensor_range: failed to allocate CUDA0 buffer of size 6960120064
```

`CUDA0: Orin (15655 MiB, 12229 MiB free)` was printed one line earlier. The same signature is in `~/zoe_12b_postboot_server.log` (2026-06-20 09:21, clean boot, 9,257 MB free); the same afternoon `~/zoe_12b_up.sh` did load it (free 11,960 MB after repeated drop_caches / compaction, `--ctx-size 4096`, `--mmproj`, started from an interactive shell) and measured about 11-13 tok/s decode and 116-139 tok/s prefill. Tried tonight, all with the same result: the parked binary (b9733, fit on), `--fit off` added, `--no-mlock`, the b11194 binary (flags translated: `--load-mode mmap+mlock`, `--reasoning off`), a second compaction round that raised the free blocks of 2 MB and up from 6.4 GB to 8.2 GB. So the failure is not the RAM arithmetic (MemAvailable and MemFree both clear the need by 4-5 GB) and not a flag; the largest single CUDA allocation this kernel's NvMap will grant looks capped somewhere between the 4B's 3.2 GB buffer (which loads) and the 12B's 6.6 GB one. `/sys/kernel/debug/nvmap/iovmm/free_size` ("Max allocatable IOVMM memory") tracks MemAvailable when read live (1.9-2.3 GB with the 4B running) and is now recorded by every window at the moment of the load, which will say whether NvMap itself reports a smaller block than the model buffer.

An ad-hoc experiment script that stopped the services outside this tool was refused by the permission system, correctly; no further stops were attempted. The trial therefore has **no K numbers for the 12B**, and none for the 4B at 32k (the first version of the trial aborted before it; the version in this PR measures the 4B at 32k even when the 12B does not load). The 4B at 32k reference is run 2's record (`docs/research/bakeoff-run-20261008-1805.md`: ZMA@32k and HMA@32k carried K1 FAIL, K2 PASS, K4/K5 FAIL; K3 PASS for ZMA only).

### When the 12B will not load: the next experiments, in order

Each is one `--trial` (about a minute of silence when it fails, 25 minutes when it works), run by the owner or an agent the owner has authorised for a brain stop:

1. **Partial offload**, the cheapest and the most likely: `scripts/night/night_window.sh --trial --ngl 24` (roughly half the weights on the GPU, the rest in ordinary RAM, so the single CUDA buffer falls to about half of 6.6 GB, near the 4B's 3.2 GB buffer that loads; the layer count was not read, so raise `--ngl` in steps of 4 until it fails). Decode will be slower; the window's job timeouts scale from the measured speed.
2. **Launch like June did**: from an interactive shell, outside the user manager's `app.slice` (the 2026-06-20 success was `nohup llama-server ...` in an ssh session; every attempt tonight was a `systemd-run --user` unit in `app.slice`). A one-off: stop the four units with the tool's `--trial`, or by hand, then run the parked ExecStart (ctx 4096, with the mmproj) by hand and compare.
3. **Read NvMap before and after**: `sudo cat /sys/kernel/debug/nvmap/iovmm/free_size` and `.../iovmm/clients` with everything stopped, and `/sys/module/nvmap/parameters/{enable_page_pools,pool_size}` (page pools: 500,975 pages configured); try `echo 1 | sudo tee /sys/module/nvmap/parameters/shrink_page_pools` before the load.
4. **Cold boot with the window's stops done before anything else** (`systemctl --user stop` the four units at the top of a fresh boot, then the load): the June clean-boot attempt failed too, at 9.2 GB free, so this is a last resort.
5. Upstream: the parked unit's mmproj / audio path is not needed for the night jobs and is already dropped; a 12B at IQ4 or Q3_K (a smaller buffer) is a rocks-rule question for the owner.

## 9. Reading the night report

`~/.zoe/night-reports/<date>.md` (and `.json` with every event; `<date>-trial.md` for a trial; a second run the same day gets `-HHMM`), mode 0600 in a 0700 directory. One page: the outcome line (`ok`, `refused: ...`, `aborted: ...`) with the exit code (0 ok, 2 refused, 3 aborted, 4 restore failed, 5 a job did not finish anywhere), the levers and the measured MemAvailable / MemFree before and after the load, the speed probe, the jobs table (section 5), the wake table (healthy, attempts, seconds down), notes (which fallback ran and why), and the last 30 timeline events. `~/.zoe/night-reports/ALARM` exists only while a restore is incomplete. The bare log of each run is `~/.zoe/night-window/run-<stamp>.log`.

## 10. What is unverified

Listed in `docs/knowledge/open-problems.md` (the 2026-10-09 night-window lines): the 12B load; every job on the 12B (the digest runner's `--check` passed against the real database; no job has run end to end on a loaded 12B); the timeouts and the 12B's speed on this build (June's 11-13 tok/s is for 4-9 token replies); the night-mind entry point; the first timer-started run; `FREED_FACTOR`; the big-block threshold.
