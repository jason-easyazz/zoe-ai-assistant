---
type: Runbook
title: The 12B night window (owner's page)
description: A nightly window in which Zoe sleeps (voice, the app, Telegram brain replies and the live 4B are down for about an hour), the 12B runs the night jobs (the nightly digest, zoe-nightly-dreaming, the night-mind pass) at a bigger context, and everything is woken again, brain first, each unit's own /health polled. What sleeps and when, the RAM arithmetic and the levers, the safety properties, how to dry-run, trial, install, abort and read the night report, the first real attempts (2026-10-09: the 12B did not load; why that is not a RAM-arithmetic problem) and the ordered next experiments.
tags: [night, 12b, dreaming, brain-window, runbook, owner, memory, night-mind]
timestamp: 2026-10-09T02:30:00+08:00
---

# The 12B night window

Owner priority, 2026-10-09 01:30: "using the 12B for dreaming and improving Zoe during the night needs to be a priority." Research: `docs/research/night-mind-2026-10-09.md` (section 8.3), `docs/research/memory-bakeoff-decision-2026-10-08.md` (the 12B preflight arithmetic). Built on the bake-off runner's pieces (`scripts/perf/zmb/bakeoff.py`: the `Host` seam, the brain-window lock, `compact_memory` / buddyinfo, `clone_command` / `deep_clone_command`), not beside them.

**Status: built and tested; the 12B LOADS with unified memory plus `--ngl 24` (03:06 on 2026-10-09: 14 s to healthy, 5.4 tok/s decode), but no K numbers exist for it yet and no night job has run on it.** Five earlier attempts (01:57-02:06) and one at 03:02 (unified memory, full offload) died on the 12B's own `cudaMalloc`; every attempt restored the box healthy. Nothing is installed; the timer does not exist on the host. Read section 8 before installing.

## 1. The commands

```bash
scripts/night/night_window.sh --dry-run          # FIRST. Reads the box, prints the arithmetic for every lever combination, which fits TODAY, the generated 12B command, the plan. Changes nothing.
scripts/night/night_window.sh --trial            # manual, any hour, about 25 min: 12B alone, K1-K5 on it and on the 4B at 32k, restore
scripts/night/night_window.sh                    # the window itself (the timer runs this at 02:50; a manual run outside 01:30-03:40 needs --anytime)
scripts/night/night_window.sh --restore-only     # put everything back (idempotent; a window that is still running is left alone)
scripts/night/night_window.sh --speed-sweep      # manual, any hour, hard cap 45 min: one 12B per config of the speed grid, one restore at the end (section 11)
scripts/night/night_window.sh --cells-only       # manual, any hour, cap 40 min: load the 12B, one speed probe, ONLY the night mind's own cells (K1-K12), restore (section 12)
scripts/night/install_night_window.sh --dry-run  # then without --dry-run: the OPERATOR installs the two units (section 6)
```

Levers and pins (defaults are the 2026-10-09 sweep's winner, section 11): `--model auto|qat|q4km`, `--ctx 8192|16384|32768` (default 8192), `--kv q8_0|q4_0` (default q8_0), `--ngl N` (default 34; 38 is the sweep's faster winner but was refused twice the same evening, section 11 'Why -ngl 34'), `--batch-size N` / `--ubatch-size N` (512 / 128), `--no-mlock`, `--fit-off` / `--fit-default`, `--binary PATH` (default: the b11194 build), `--speed-sweep`, `--cells-only`, `--sweep-stages`, `--sweep-fresh`, `--keep-zoe-data`, `--cap-min`, `--end-by HH:MM`, `--jobs digest,dreaming,night_mind`, `--night-mind-cmd "..."`, `--no-fallback`, `--retry-load`, `--job-reserve-mib`, `--margin-mib`. Every external command goes through the bake-off `Host` seam, so `tests/unit/test_night_window.py` (see the test file) runs the real step logic with a double.

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

Per job the report records wall time, prompt and generated tokens (the 12B's `/metrics` counters, before and after), the MemAvailable low (sampled every 5 s), the status (`ok`, `timeout`, `degraded` rc 3, `failed(rc)`, `killed`, `skipped` = the optional script is absent, `no time` = a requested job never ran for lack of time: exit 5 unless the 4B covered it) and the `NIGHT_JOB ...` lines the job printed (counts, never text; the full output is in `~/.zoe/night-window/logs/`, mode 0600, and is not copied anywhere).

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

An ad-hoc experiment script that stopped the services outside this tool was refused by the permission system, correctly. Two more `--trial` runs through the tool were then authorised by the coordinator (owner asked for it tonight):

| 03:02 | `--ctx 16384 --kv q8_0`, `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` (the new lever, default ON on a Jetson), full offload (`--ngl` 99) | **did not load**: the same `cudaMalloc failed: allocating 6637.69 MiB` at 12.4 s. NvMap's own figure beforehand: largest allocatable IOVMM block 10,593 MiB, so NvMap did not predict the failure |
|---|---|---|
| 03:06 | the same plus **`--ngl 24`** | **loaded**: healthy 14.1 s after the start; MemAvailable 12,649 MiB before, 3,964 MiB after; served ctx 16384 |

Measured at 03:06 (one fixed 1,630-token probe): **12B prefill 125.4 tok/s, decode 5.42 tok/s** (24 layers on the GPU); the 4B at 32k, same probe: prefill 680 tok/s, decode 35.8-36.9 tok/s. The 12B's ZMA reflection pass **timed out at 720 s with no result** (the trial's budget was sized for a faster model; the trial now scales it by the measured decode speed and reports a phase without a result as an aborted outcome, exit 3), so there are **no K1-K5 numbers for the 12B**. MemAvailable low during that pass: 1,251 MiB (floor 1,200: the jobs beside a 12B at this offload are tight). The 4B at 32k scored K cells 2/5 (K2 thread recall PASS, K3 useful answers PASS; K1 observations-are-true, K4 invalidated-fact-not-restated, K5 user-stated-never-restated FAIL), identical on both runs, 24 model calls, about 6,070 prompt / 2,700-2,930 generated tokens in 120-130 s; run 2's ZMA@32k record has the same shape. Restore after the 15.6-minute run: brain 905 s down (the whole window; 12 s to healthy once started), Kokoro +16 s, router +3 s, zoe-data +9 s.

**Not isolated:** whether it was unified memory or the partial offload that made it load (the 03:02 run had unified memory and failed; `--ngl 24` without unified memory was never tried). Next: `--ngl 24 --no-unified`, then raise `--ngl` in steps of 4 with unified memory on to find how much of the 12B can sit on the GPU (decode speed rises with it).

### When the 12B will not load: the next experiments, in order

Each is one `--trial` (about a minute of silence when it fails, 25 minutes when it works), run by the owner or an agent the owner has authorised for a brain stop:

1. **Partial offload** (WORKS with unified memory, section 8 above): `scripts/night/night_window.sh --trial --ngl 24` (roughly half the weights on the GPU, the rest in ordinary RAM, so the single CUDA buffer falls to about half of 6.6 GB, near the 4B's 3.2 GB buffer that loads; the layer count was not read, so raise `--ngl` in steps of 4 until it fails). Decode will be slower; the window's job timeouts scale from the measured speed.
2. **Launch like June did**: from an interactive shell, outside the user manager's `app.slice` (the 2026-06-20 success was `nohup llama-server ...` in an ssh session; every attempt tonight was a `systemd-run --user` unit in `app.slice`). A one-off: stop the four units with the tool's `--trial`, or by hand, then run the parked ExecStart (ctx 4096, with the mmproj) by hand and compare.
3. **Read NvMap before and after**: `sudo cat /sys/kernel/debug/nvmap/iovmm/free_size` and `.../iovmm/clients` with everything stopped, and `/sys/module/nvmap/parameters/{enable_page_pools,pool_size}` (page pools: 500,975 pages configured); try `echo 1 | sudo tee /sys/module/nvmap/parameters/shrink_page_pools` before the load.
4. **Cold boot with the window's stops done before anything else** (`systemctl --user stop` the four units at the top of a fresh boot, then the load): the June clean-boot attempt failed too, at 9.2 GB free, so this is a last resort.
5. Upstream: the parked unit's mmproj / audio path is not needed for the night jobs and is already dropped; a 12B at IQ4 or Q3_K (a smaller buffer) is a rocks-rule question for the owner.

## 9. Reading the night report

`~/.zoe/night-reports/<date>.md` (and `.json` with every event; `<date>-trial.md` for a trial; a second run the same day gets `-HHMM`), mode 0600 in a 0700 directory. One page: the outcome line (`ok`, `refused: ...`, `aborted: ...`) with the exit code (0 ok, 2 refused, 3 aborted, 4 restore failed, 5 a job did not finish anywhere), the levers and the measured MemAvailable / MemFree before and after the load, the speed probe, the jobs table (section 5), the wake table (healthy, attempts, seconds down), notes (which fallback ran and why), and the last 30 timeline events. `~/.zoe/night-reports/ALARM` exists only while a restore is incomplete. The bare log of each run is `~/.zoe/night-window/run-<stamp>.log`.

## 10. What is unverified

Listed in `docs/knowledge/open-problems.md` (the 2026-10-09 night-window lines): the 12B load; every job on the 12B (the digest runner's `--check` passed against the real database; no job has run end to end on a loaded 12B); the timeouts and the 12B's speed on this build (June's 11-13 tok/s is for 4-9 token replies); the night-mind entry point; the first timer-started run; `FREED_FACTOR`; the big-block threshold.

## 11. 12B speed sweep 2026-10-09

Owner question (11:45): the live 4B was squeezed with a measured flag research (build b11194, `--load-mode mmap+mlock`, q8_0 KV, ...); the 12B had only ever run with the parked unit's flags (5.4 tok/s). `scripts/night/night_window.sh --speed-sweep` (manual, any hour, hard cap 45 min, the same brain-window lock and the same single stop / single restore as the trial; `scripts/night/speed_sweep.py` holds the grid, the probes and this table's renderer) loads one transient 12B per config, runs two fixed probes (a 1.6k-token prompt for 96 tokens, and the **night shape**: a 2,800-token prompt for 320 tokens, the night-mind pass's real size) and records what loaded. The rows below are `~/.zoe/night-window/sweep-state.json`, merged over two invocations (stages 0-4, then 5-8; the 15:12 report holds stages 5-8), printed by `python3 scripts/night/speed_sweep.py ~/.zoe/night-window/sweep-state.json`. Columns: prefill and decode are llama-server's own `timings` in tok/s; "nvmap MiB" is NvMap's size for the 12B's process; "MemAvail low" is the lowest MemAvailable seen from the start of that config (for a config that did not load it is the box before the failure, not a use).

### The table (26 rows: 24 run, 2 skipped by the family rules)

| id | config | loaded | load s | 1.6k prefill | 1.6k decode | night prefill | night decode | nvmap MiB | MemAvail low MiB | result |
|---|---|---|---|---|---|---|---|---|---|---|
| C0 | parked qat ngl 30 ctx 8192 kv q8_0 b512/ub128 mlock uma | yes | 14.2 | 129.7 | 2.06 | 141.2 | 5.46 | 4643 | 3192 | ok |
| 1a | b11194 qat ngl 99 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | 12509 | cudaMalloc of 6638 MiB failed (out of memory) |
| 1b | b11194 qat ngl auto ctx 8192 kv q8_0 b512/ub128 mlock fit on uma | NO | - | - | - | - | - | - | 12530 | cudaMalloc of 6638 MiB failed (out of memory) |
| 1c | b11194 qat ngl 99 ctx 8192 kv q8_0 b512/ub128 mlock fit off no-uma | NO | - | - | - | - | - | - | 12424 | cudaMalloc of 6638 MiB failed (out of memory) |
| 1d | b11194 qat ngl 99 ctx 8192 kv q8_0 b512/ub128 mmap (no lock) fit off uma | NO | - | - | - | - | - | - | 12364 | cudaMalloc of 6638 MiB failed (out of memory) |
| 1e | b11194 qat ngl 30 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 12.2 | 143.6 | 6.23 | 149.5 | 5.70 | 4641 | 3765 | ok |
| 1f | b11194 qat ngl 30 ctx 8192 kv q8_0 b512/ub128 mlock fit off no-uma | yes | 12.2 | 148.2 | 6.14 | 153.4 | 5.67 | 4641 | 3871 | ok |
| 2a | b11194 q4km ngl 30 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 14.2 | 134.6 | 5.69 | 137.0 | 5.28 | 4882 | 3417 | ok |
| 2b | b11194 q4km ngl 28 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | - | not run: the config before it loaded |
| 3a | b11194 q3km ngl 30 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 10.2 | 135.9 | 4.66 | 139.4 | 4.48 | 4122 | 4509 | ok |
| 3b | b11194 q3km ngl 34 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 10.2 | 147.7 | 4.80 | 152.7 | 4.86 | 4565 | 4631 | ok |
| 3c | b11194 q3km ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 10.2 | 161.7 | 5.20 | 166.8 | 5.15 | 5006 | 4668 | ok |
| 3d | b11194 iq4xs ngl 32 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 10.3 | 157.3 | 6.82 | 164.1 | 6.35 | 4702 | 4071 | ok |
| 4-32 | b11194 qat ngl 32 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 14.2 | 151.8 | 6.32 | 155.9 | 5.99 | 4906 | 3821 | ok |
| 4-34 | b11194 qat ngl 34 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 12.2 | 159.6 | 6.40 | 164.5 | 6.13 | 5157 | 3947 | ok |
| 4-38 | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 14.3 | 175.4 | 6.84 | 182.4 | 6.89 | 5671 | 3978 | ok |
| 4-42 | b11194 qat ngl 42 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | 10513 | cudaMalloc of 181 MiB failed (out of memory) |
| 4-48 | b11194 qat ngl 48 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | - | not run: an earlier config of the ngl family ended it |
| 5a | b11194 qat ngl 38 ctx 8192 kv q4_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | 10349 | cudaMalloc of 57 MiB failed (out of memory) |
| 5b | b11194 qat ngl 40 ctx 8192 kv q4_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | 10404 | cudaMalloc of 57 MiB failed (out of memory) |
| 6a | b11194 qat ngl 38 ctx 8192 kv q8_0 b2048/ub512 mlock fit off uma | yes | 14.3 | 220.2 | 4.57 | 236.9 | 6.65 | 5763 | 3635 | ok |
| 6b | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub256 mlock fit off uma | yes | 14.3 | 206.1 | 6.86 | 216.2 | 6.03 | 5715 | 3753 | ok |
| 6c | b11194 qat ngl 38 ctx 8192 kv q8_0 b256/ub64 mlock fit off uma | yes | 14.5 | 113.9 | 6.54 | 122.3 | 5.71 | 5668 | 3821 | ok |
| 7a | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off -t 6 uma | NO | - | - | - | - | - | - | 10453 | cudaMalloc of 159 MiB failed (out of memory) |
| 7b | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off -t 4 uma | yes | 14.7 | 157.1 | 3.28 | 177.8 | 5.59 | 5671 | 3707 | ok |
| 7c | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off no-kv-offload uma | yes | 14.3 | 161.9 | 5.35 | 168.5 | 4.55 | 5458 | 3747 | ok |

Stage 8 (a 12B draft / MTP head) had nothing to test: no draft GGUF exists under `~/models/gemma4-12b*`.

### The winner (the window's default, except `-ngl`: see 'Why -ngl 34')

**4-38: llama.cpp build b11194, QAT q4_0 GGUF, `-ngl 38`, ctx 8192, KV q8_0, `-b 512 -ub 128`, mlock (`--load-mode mmap+mlock` on this build), `--fit off`, unified memory on.** Night-shape decode **6.89 tok/s**, prefill **182.4 tok/s** (1.6k probe: 6.84 / 175.4); loaded in 14.3 s, MemAvailable 4,469 MiB after the load (the floor is 1,200), served ctx 8192. The parked unit's own flags (control C0: b9733, `-ngl 30`) measured **5.46 / 141.2** in the same run, so the default is +26 % decode and +29 % prefill. These values are `NightCfg`'s defaults and the `--trial` defaults, and `llm_spec` writes every one of them into the generated command explicitly (`--n-gpu-layers`, `--batch-size`, `--ubatch-size`, `--fit off`, the b11194 binary with its renamed flags, `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` on a Jetson) on top of the parked unit's ExecStart (`llama-server-12b-deepbrain.service.disabled`, read by preflight and never edited), so a change to that file cannot change what the window runs. Each is overridable: `--ngl` / `NIGHT_NGL`, `--ctx` / `NIGHT_CTX`, `--kv` / `NIGHT_KV`, `--batch-size` / `NIGHT_BATCH`, `--ubatch-size` / `NIGHT_UBATCH`, `--binary` / `NIGHT_LLAMA_BINARY` (`parked` = the parked unit's b9733), `--fit-default` / `NIGHT_FIT_OFF=0`, `--no-mlock` / `NIGHT_NO_MLOCK=1`, `--no-unified` / `NIGHT_NO_UNIFIED=1`. Because ctx and KV are now pins of the lever choice (8192, q8_0), the window no longer "chooses the biggest set that fits" (section 4's order applies only when both are unpinned, which needs explicit overrides); the night jobs' prompts are 3-8k tokens.

### Why `-ngl 34` is the default (2026-10-09 evening) and 38 is only selectable

The sweep chose 38 on speed alone (night-shape decode 6.89 tok/s against 6.13 at `-ngl 34`, row 4-34). The same evening, with the 38 default, two real trials did not start:

| run | `-ngl` | what the window measured before the start | result |
|---|---|---|---|
| 20261009-182205 | 38 | MemFree 12,991 MiB, but only 6,006 MiB in free blocks of 2 MB and up after six compactions (the smallest 12B wants 7,390 of each) | `cudaMalloc` of 159 MiB failed; RAM when it failed: 2,312 MiB in free 2 MB+ blocks |
| 20261009-182549 | 38 | MemFree 12,961 MiB, 7,068 MiB in 2 MB+ blocks at the last reading before the start (after two compactions; the smallest 12B wants 7,390) | `load_model: failed to load model`; MemFree 5,574 MiB, 2,070 MiB in 2 MB+ blocks when it failed |
| 20261009-182701 | **34** | MemFree 13,071 MiB, one compaction (order 9+ free blocks 1,059 -> 2,124) | **loaded first time**, healthy in 15 s, MemAvailable 4,190 MiB after the load, prefill 158.7 / decode 6.24 tok/s |

Two out of two refusals at 38 and one clean load at 34 on the same box in the same ten minutes: the layers on the GPU set the size of the CUDA allocations that must be found in 2 MB+ blocks (sweep rows 4-42 and 5a/5b/7a died on the same small `cudaMalloc` family at more layers), and 38 sits on the edge of what a fragmented Tegra can serve. A night that does not start measures nothing and puts Zoe to sleep for the attempt. 34 costs **11 %** of decode (6.89 to 6.13 in the sweep; 6.24 measured in the real run) and about 10 % of prefill (182 to 164.5). The default is therefore the layer count that loads (`NightCfg.ngl`, `NIGHT_NGL`, `--ngl`); `--ngl 38` (or `NIGHT_NGL=38`) selects the faster setting when the box has just been rebooted or is known to be unfragmented. The load-failure guard (section 8) still counts a failed 38.

### The ceiling: full offload is impossible on JetPack 6.2.1

Four variants of full offload (`-ngl 99` / auto) all died on the same single allocation: **1a** (b11194, fit off), **1b** (fit on, no `-ngl`), **1c** (without unified memory), **1d** (mmap without the lock) each: `cudaMalloc of 6638 MiB failed (out of memory)` (the 12B's one weight buffer, 6,637.69 MiB in section 8), with 12.4-12.5 GB of MemAvailable on the box. Newer build, load mode, fit and unified memory do not lift it. The most layers that load is between 38 and 42: `-ngl 42` died on a 181 MiB cudaMalloc (row 4-42), so 4-48 was not tried. The speed therefore tops out where the CPU-side layers dominate; **the lever is JetPack 7.2** (not tested here: nothing in this sweep touches the JetPack).

### What was rejected, with the numbers

- **Full offload** (1a-1d): does not load, above.
- **KV q4_0** (5a at `-ngl 38`, 5b at `-ngl 40`): both die on `cudaMalloc of 57 MiB`; q4_0 KV is not a way to buy layers here. q8_0 stays.
- **More layers, `-ngl 42`**: does not load (181 MiB cudaMalloc). Up the ladder: ngl 30 5.70 (1e, b11194), 32 5.99, 34 6.13, 38 **6.89** night-shape decode.
- **Bigger batches** (`-b 2048 -ub 512`, 6a): night prefill 236.9 tok/s but night decode 6.65 (1.6k decode 4.57); the night jobs are decode-bound, so the default stays at 512 / 128. `-ub 256` (6b): 216.2 / 6.03. `-b 256 -ub 64` (6c): 122.3 / 5.71. If a future job is prefill-dominated, `--batch-size 2048 --ubatch-size 512` is the documented lever.
- **Threads and KV placement**: `-t 6` (7a) does not load (159 MiB cudaMalloc); `-t 4` (7b) 5.59 night decode (1.6k decode 3.28); `--no-kv-offload` (7c) 4.55.
- **Q4_K_M file** (2a, `-ngl 30`): 5.28 night decode against 5.70 for the QAT file at the same layers (1e); 2b was skipped because 2a loaded.
- **IQ4_XS** (3d, `-ngl 32`, re-quantised offline from the QAT file): night decode 6.35, prefill 164.1, below the winner (6.89 / 182.4), and **not default-eligible** (owner quality call: never requantise below the QAT). **Q3_K_M** (3a/3b/3c at ngl 30/34/38): 4.48 / 4.86 / 5.15 night decode, same exclusion. Both were speed probes only.
- **Unified memory**: not a speed lever at ngl 30 (1e uma 5.70 vs 1f no-uma 5.67); it stays on because it is what made the 12B loadable on 2026-10-09 (section 8).
- **A newer build alone** (C0 5.46 -> 1e 5.70 at the same `-ngl 30`) is worth about +4 %; the rest of the gain is the layers (38 instead of 30).

Nothing here changes the K1-K5 situation (section 8): the speed is known, a 12B reflection pass at it has still to be scored by a `--trial`.

## 12. `--cells-only` and the night-mind cells' budget (2026-10-09)

**What it is.** `--trial` runs three measurements in order: the ZMA-arm reflection pass with its K cells (about 18 minutes on the 12B; a memory arm the bake-off rejected), then the night mind's own cells (`zoe-night-mind.py --cells`, K1-K12 on lab stores: the measurement that matters for the night), then optionally the 4B at 32k. `--cells-only` is a trial that loads the 12B, takes the one speed probe, runs ONLY the night mind's cells, and restores: no ZMA-arm pass, no embeddings shim, no 4B phase. The dry-run table and the report show the mode (`MODE cells-only`; report title `(trial, cells-only)`, file `<date>-cells[-HHMM].md/.json`, JSON key `trial_mode`).

```bash
scripts/night/night_window.sh --dry-run --cells-only     # reads the box, prints the mode, the command, the cells budget at the last measured speed; changes nothing
scripts/night/night_window.sh --cells-only               # the run (any hour; stops Zoe's voice, zoe-data and the 4B for about 25 minutes, then restores)
scripts/night/night_window.sh --cells-only --ngl 38      # the sweep's faster -ngl, if the box was just rebooted
```

**Why the watchdog was wrong.** The first full trial of the evening (run 20261009-182701) loaded the 12B at `-ngl 34` (prefill 158.7, decode 6.24 tok/s), spent 1,055 s on the ZMA pass, then ran the night-mind CLI under a FIXED 420 s watchdog. One member's pass is three model calls, about 150-200 s on the 12B, so the CLI was killed (rc 124, `no cells object in the output`) after two passes and the report showed only that error row. PR #1946 had scaled the CLI's per-call timeouts from the measured rates; the window's own kill was left a constant. The class: every budget in the window derives from the measured rate and the time the cap leaves.

**The formula** (`scripts/perf/zmb/cells_budget.py`, used by the window AND the CLI; same family as `night_mind.timeout_for`):

- a call costs `prompt_tokens / prefill_tok_s + output_tokens / decode_tok_s`; EXPECTED output is `0.6 x max_tokens` (measured: a 3-call pass took 150-200 s at 6.24 tok/s), WORST case is `night_mind.timeout_for` itself (output at the cap, +20 s);
- which calls a cell makes is measured on the lab household: MOMENTS calls (prompt about 700 tokens, cap 640) and THREADS calls (about 600, cap 450); K1, K7, K8: 2+1; K9, K9f, K10, K11: 1+1; K12: 1+0; K2-K6 read K1's pass and make none;
- **expected** = sum over the cells; **needed** = 45 s startup + 1.25 x expected; **worst** = sum of the worst cases;
- **room** = `cap_min - reserve_min - elapsed` (minus 300 s in a full `--trial` for the 4B phase that still follows the 12B's cells);
- **selected** = the longest prefix of K1..K12 whose *needed* fits `room - 60 s`; **CLI budget** (`--cell-budget`) = `min(room - 60, 45 + worst)`; **watchdog** = `min(room, CLI budget + 60)`. Nothing fits -> no CLI run, an error row that says why.

**At the 12B's last measured speed (6.24 decode / 158.7 prefill):** one MOMENTS call 65.9 s, one THREADS call 47.1 s, K1/K7/K8 178.9 s each, K9/K9f/K10/K11 113.0 s each, K12 65.9 s: **1,055 s expected for the 13 cell runs**, 1,364 s needed with the slack and startup, 2,068 s worst case. A 40-minute cap with the 12-minute restore reserve leaves about 1,640 s after a ~40 s load: everything fits, CLI budget 1,580 s, watchdog 1,640 s. At a slower speed the plan drops the tail (K8..K12 first) and says so in the log, the report and `cells_plan` of the JSON; give it `--cap-min 55` for the full set. A full `--trial` after an 18-minute ZMA pass has under 100 s left, which is why the cells-only mode exists.

**What the CLI does with the budget.** Before each cell it asks `fits_next(elapsed, budget, cell)`; a cell that would overrun is not started (it and the rest are `SKIP`, reason `cell_budget`, listed in `cells.skipped_budget`) and the one JSON line is still printed. Each finished cell logs `NIGHT_CELL id=K1 verdict=PASS wall_s=..` on stderr, so even a run the watchdog kills leaves its verdicts: the window recovers them from the log (`partial`, with the reason). The report's "Night-mind cells" table lists every verdict, the reason of every ERROR/SKIP, PASS/FAIL/SKIP/ERROR counts, the model calls and tokens, and the budget line; the JSON carries the same under `trial.12B.night_mind_cells`.

**Still fixed (not cells):** the 4B@32k phase's own ZMA pass keeps its 420 s timeout and a full `--trial`'s ZMA pass keeps `trial_timeout_12b()`; neither was part of this change.
