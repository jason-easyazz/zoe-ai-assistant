---
type: Research / bake-off RAM and latency optimisation
title: "Bake-off RAM and latency optimisation 2026-10-06: what shrinks the Hindsight and combined arms, measured"
status: RECORD of what was run and measured on 2026-10-06/07 in the scratch lab only (the live brain served model calls only, under its lock; the live Postgres, live store, zoe-data and /home/zoe/assistant were never touched). The adoption decision stays the bake-off's and the owner's.
date: 2026-10-07
description: Measured RAM and latency optimisation of the bake-off's Hindsight (H1) and combined Hindsight + MemPalace (HM) arms in a scratch lab - one setting at a time on the real server, one embedder for every tier, Postgres, latency - with the best combined configuration against gate G0 and the 2 GB voice floor, what is still over, and the settings applied to the runner for run 2.
evidence_labels: "[measured] I ran it, command in section 9 | [source] read in code | [derived] arithmetic on measured numbers | [inferred] reasoned, not run | [unverified] not confirmed"
---

# Bake-off RAM and latency optimisation (2026-10-07, run on the night of 2026-10-06)

Owner question: "can you optimise it at all?" The Hindsight (H1) and combined Hindsight + MemPalace (HM) arms were disqualified mainly on RAM: H1 measured 521-532 MB steady / 595-603 MB burst
(run 1), HM about 570-580 MB of servers plus the verbatim tier's own growth (770-895 MB), against the pre-registered gate G0 (<= 600 steady, <= 900 burst) and the box's 2 GB voice floor.
Stacked on PR #1902 (`bench/bakeoff-first-contact`).

## 0. Answer first

**Yes, and the biggest part is free.** What the pre-registered gate reads (G0: PSS of the hindsight and shim units + the Postgres container, median = steady, max = burst) falls **about 93 MB (-17%) for the H1 stack and about 112 MB at burst**,
with the same extraction validity, the same recall latency, the same functional results, and no change to the arms. The HM (Hindsight + MemPalace) arm gains more, because its second ONNX session was the largest single item in its bill. Nothing here changes what the arms
*do*; every change is configuration, an import trim of packages the server never uses, or an adapter fix with a red-before-green test. Run 2 uses them by default (`BAKEOFF_LEAN=0` restores run 1's settings).

| What | Before | After | Evidence |
|---|---|---|---|
| Hindsight server (PSS) | 326.9 MB | **263.3 MB (-64)** | 200 retains / 200 recalls / storm, n = 3 baseline runs, on the model path 324 -> 257 |
| of which: import trim (MCP, Gemini, OTLP stubbed) | | -52 MB | the biggest single lever |
| of which: migration isolation / pool 1..2 / docstring-free bytecode / 1 BLAS thread | | -12.5 / -5 / -8 / -3 MB | one setting at a time |
| Postgres (`docker stats`, the gate's figure) | 68.0 MB | **54.1 MB** | page cache dominates; the pool (8 -> 2 backends) is what moved it; the flags did not |
| Embeddings shim (serving median) | 160.8 MB | **145.6 MB** (peak 173.2 -> 159.9) | full graph optimisation + one malloc arena; single-text latency 35.7 -> 28 ms |
| **H1 stack, G0 steady (sum of parts)** | **556 MB** | **463 MB** | run 1 measured 521-532; the decomposition reproduces it |
| **H1 stack, G0 burst** | **591 MB** | **479 MB** | run 1 measured 595-603 |
| HM verbatim tier: driver PSS after the first embed | 251 MB | **91 MB** (-160) | MemPalace's own `openai-compat` backend pointed at the shim: ONE embedder for both tiers |
| HM driver peak over the 21 HM cells | 391 MB | **133 MB (-66%)** | shared embedder + releasing each palace's cached Chroma client on close (4.8 MB leaked per palace opened) |
| HM verbatim recall p95 | 75.4 ms | **35.0 ms** | the shim batches unpadded; Chroma's function pads to 256 tokens |
| HM voice-lane cache hit, 4,020 cached rows | 35.0 ms | **0.16 ms** | an inverted index replaces tokenising every row on every turn |

**Against the gates** [derived from the parts above unless marked]: H1 steady 463 MB and burst 479 MB against 600 / 900: both pass with a margin of 137 / 421 MB (before: 44 / 309); a real-shim composite run of the shipped server settings read
**424.3 MB steady / 433.4 MB burst** [measured]. HM was disqualified at 770-895 MB; with the same savings on its servers (570-580 -> about 482) and the verbatim tier hosted as designed (in zoe-data, shared embedder, +14 MB measured in the HM record)
it is about **496 MB steady, inside the 600 MB gate**; as the bench measures it (a separate driver) the second ONNX session no longer exists and the driver's peak is 133 MB instead of 391. **Against the box's 2 GB voice floor it is still over** [derived]: the box's idle MemAvailable was
1.76 GB median today (1.13-2.23 GB range), the optimised stack needs about 463 MB, so MemAvailable would sit near 1.30 GB. What is still over, and what would close it, is section 7.

**The finding the owner should read before run 2** [measured; 3 clean runs (2 bge-small, 1 MiniLM) and 3 more MiniLM attempts stopped by the memory floor: a small sample, a large effect]: the shim can serve **MiniLM, zoe-data's own embedder, with bit-identical vectors (cosine 1.000000 against Chroma's function)** and the same pure-retrieval hit@5 (240/240 direct, 235-239/240 paraphrase on 12 seeds, bge and MiniLM tied), and it is faster
(single text 22.6 vs 28 ms, batch of 8 -45%). But **at the Hindsight level, on a 200-row store, hit@5 on the lab's needles is 26/40 direct (40/40 paraphrase) for bge-small and 20/20 direct (20/20 paraphrase) for MiniLM**: the D axis of run 2 would compare Z0e (MiniLM) with H arms on bge-small, a confound that favours Z0e.
It is NOT changed by default (the default stays bge-small, so run 2 is comparable with run 1); `BAKEOFF_SHIM_MODEL=minilm` is the one-word switch, and it is the owner's call.

**Honest limits.** The box's own MemAvailable swung 1.13-2.23 GB with nothing of mine running, so the 1.5 GB floor stopped 28 of 64 stack attempts (all logged). No full-size (200-retain) run through the live brain could be completed under the floor: the model path was proven with 8 verbatim retains plus the adapter's H1 and H2 flows (about 20 model calls) on both servers,
identical validity and results. The real window was not run. Section 7 lists the rest.



## 1. How it was measured, and what limited it

**The instrument** (`scripts/perf/zmb/ram_opt.py`, new): one configuration per run. It starts its own scratch Postgres container (`zoe-ropt-pg`, :55433), its own embeddings server (:11511) and its own
`hindsight-api` (:18889) as `systemd-run --user` units with the SAME environment the window generates (`bakeoff.hindsight_env`, the egress audit hook on), drives 200 retains, 200 recalls
(warm-up), 50 timed recalls (p50 / p95 and hit@5 on the lab's 20 needles, direct and paraphrase) and a concurrent recall storm (8 threads x 10; in chunks mode also 4 writer threads), and reports the
window's own number: PSS summed over the hindsight and shim units' cgroups plus `docker stats` of the Postgres container, **median = steady, max = burst** (`bakeoff_measure.Sampler`'s rule). It adds the
anonymous / file-backed split of the server's PSS and the Postgres cgroup's anonymous / page-cache split. Every process it starts is stopped on every exit path (a SIGTERM handler too).

**The memory floor, and what it did to the experiment design [measured].** The brief set MemAvailable >= 1.5 GB as a hard floor. The box's own MemAvailable, with NONE of my processes running, swung
between 1,133 and 2,230 MB (82 readings, one a minute while a run was waiting for room, 22:40-02:20; other sessions' test lanes and builds), so a watchdog at 1,500 MB (1 s polling, kills and removes everything it started) aborted
**28 of the 64 attempts** (every one logged in `ram-opt/*.json` as `breach`; 35 ran clean), and an admission check refuses to start a stack unless MemAvailable has held floor + expected footprint for 8 s. Consequences, stated plainly:

* The full real stack (server + real shim + Postgres, ~530 MB) rarely fits under the floor at all. **Footprint runs therefore swap the real shim for a 13 MB stub embedder (`stub_embed.py`) and add the real shim's PSS from
  its own run.** PSS is additive across processes and the G0 figure IS that sum, so this is exact for RAM; it is NOT valid for recall quality or for the embedding share of recall latency, which are measured separately with the real shim [measured, both].
* Footprint screening used `chunks` extraction (no model call) so a 200-retain run takes 90 s and fits between the box's dips; the LLM path was then measured on the lean and the default servers (section 2.4). Baseline reproduction: the stub-embedder
  baseline (server 326 + Postgres 69 + stub 13 + the real shim's 133-167) is 541-575 against run 1's 521-532 steady, so the decomposition reproduces the figure the window measured [derived].
* The 90-minute-window arithmetic is untouched: nothing here ran inside a window.


## 2. Experiment 1: the Hindsight server process, one setting at a time

Every row is a full 200-retain / 200-recall / 50-timed-recall / storm run on a fresh Postgres, chunks extraction, stub embedder (so the stack steady figure below is server + Postgres + 13 MB;
the real shim is added in section 6). "Server PSS" is the hindsight-api cgroup's PSS median over the workload; the split in brackets is anonymous / file-backed. n = 2 where two clean runs exist.

| config | what changed | server PSS (anon/file) | Postgres (docker stats; cgroup anon/file) | stack steady (stub embedder) | burst | after storm | recall p50/p95 ms | storm 80 recalls, s | retains ok |
|---|---|---|---|---|---|---|---|---|---|
| s-base (n=2) | screening baseline: stub embedder, window defaults | 325.9 (298.6/27.3) | 69.3 | 408.5 | 424.9 | 433.4 | 67.1/100.9 | 2.0 | 200/200 |
| s-pgmin | Postgres minimal flags only | 324.0 (298.0/25.9) | 68.7 | 406.2 | 435.7 | 436.3 | 67.5/100.2 | 2.0 | 200/200 |
| s-arena2 | MALLOC_ARENA_MAX=2 on the server | 324.1 (298.3/25.8) | 67.3 | 404.8 | 406.6 | 435.6 | 68.1/99.0 | 2.0 | 200/200 |
| s-pymalloc | PYTHONMALLOC=malloc on the server (the heap-scrub setting) | 342.0 (317.3/24.7) | 64.0 | 419.3 | 456.5 | 457.4 | 68.2/107.1 | 2.1 | 200/200 |
| s-migiso | MIGRATION_ISOLATION=true: alembic, sqlalchemy and psycopg2 stay out of the server | 313.4 (295.9/17.5) | 68.8 | 395.3 | 397.1 | 426.0 | 66.6/101.3 | 2.0 | 200/200 |
| s-pool2 | asyncpg pool 1..2 instead of 1..8 | 321.1 (297.8/23.2) | 54.1 | 388.1 | 391.3 | 391.4 | 67.8/98.8 | 2.0 | 200/200 |
| s-opt2 | PYTHONOPTIMIZE=2 (no docstrings, no asserts) | 318.2 (294.3/24.0) | 70.3 | 401.9 | 434.8 | 422.3 | 66.6/102.6 | 2.0 | 200/200 |
| s-nodbg | PYTHONNODEBUGRANGES=1 (no per-instruction position tables in code objects) | 322.3 (296.4/26.3) | 79.5 (32.0/104.0) | 415.1 | 436.0 | 424.6 | 69.4/99.6 | 2.1 | 200/200 |
| s-thr1 | OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1 | 321.7 (298.2/23.4) | 56.9 (24.9/61.8) | 391.6 | 427.7 | 428.3 | 66.4/102.6 | 2.0 | 200/200 |
| s-quiet | loop watchdog and the in-process worker off | 322.2 (297.9/24.3) | 61.5 | 396.8 | 398.8 | 428.1 | 66.9/101.6 | 2.0 | 200/200 |
| s-conc | concurrency caps sized for one household: recall 2, connections per recall 2, embedding requests 2, worker slots 2 | 322.3 (297.9/24.5) | 66.8 (19.9/101.3) | 402.5 | 419.3 | 420.0 | 67.0/101.4 | 2.8 | 200/200 |
| s-ssl | SSL_CERT_FILE points at a one-certificate bundle (a loopback-only server never verifies a public CA) | 317.4 (294.4/23.0) | 58.9 (24.5/77.9) | 389.6 | 391.1 | 427.0 | 67.9/101.1 | 2.0 | 200/200 |
| s-pg96 | Postgres container capped at 96 MB (page cache is reclaimed instead of kept) | 321.5 (298.1/23.4) | 59.1 (24.8/62.4) | 394.1 | 426.4 | 424.9 | 69.4/102.0 | 2.0 | 200/200 |
| s-lean | lab import trim: MCP, Gemini and the OTLP exporter stubbed | 272.9 (253.6/19.5) | 59.9 | 345.4 | 349.0 | 376.9 | 67.5/98.2 | 1.9 | 200/200 |
| c-safe | every config-only setting that helped (no import stubs, no Postgres change) | 304.9 (286.6/18.3) | 52.8 (20.0/79.9) | 370.9 | 372.8 | 366.3 | 67.7/102.7 | 2.7 | 200/200 |
| c-lean | c-safe + the import trim | 261.1 (240.4/20.7) | 64.4 (20.0/115.9) | 339.3 | 340.9 | 341.1 | 66.3/99.1 | 2.6 | 200/200 |
| c-lean-pg | c-lean + Postgres minimal flags | 263.1 (240.0/23.1) | 60.1 (19.3/99.4) | 336.3 | 338.4 | 338.5 | 64.7/100.5 | 2.6 | 200/200 |

Reading it [measured unless marked]:

* **Baseline**: the server is 326 MB PSS, 299 of them anonymous (the Python heap); Postgres reads 69 MB on `docker stats`. Stub + the real shim's 133-167 MB puts the stack at 541-575 MB, against the 521-532 MB the window
  measured in run 1: the decomposition reproduces the number the gate used.
* **Did nothing**: `MALLOC_ARENA_MAX=2` (324.1 vs 325.9: the server is one asyncio loop, not the multi-threaded process the arena fix helped in zoe-data; it stays out), the Postgres "minimal" flag set (68.7 vs 69.3; see section 4),
  `LLM_MAX_CONCURRENT=1` (already in the env example since the G0 install: the clone has one slot) and `--workers 1` / uvicorn settings (the default is already 1 worker, no reload, no access log [source: `hindsight-api --help`]),
  the loop watchdog and in-process worker off (-3 MB and **H2 / H0's consolidation needs that worker**: not applied), OpenBLAS / OMP one thread (-3), `PYTHONNODEBUGRANGES` (-3, inside the noise).
* **Hurt**: `PYTHONMALLOC=malloc` makes the server **+16 MB** (342.0) and the burst +32 MB. The HM verbatim driver must run with it (the heap-scrub cell F6 needs it); the Hindsight server does not and must not.
* **Concurrency caps** (recall 2, connections per recall 2, embedding requests 2, worker slots 2) did not lower steady or burst RSS and made the 80-recall storm 34% slower (2.0 -> 2.8 s): not applied.
* **Helped, config only**: migration isolation (`HINDSIGHT_API_MIGRATION_ISOLATION=true`) **-12.5 MB** (alembic, SQLAlchemy and psycopg2 stay in a child process [source: `migrations.py _should_isolate_migrations`]; file-backed -10);
  a 1..2 asyncpg pool instead of 1..8: **-5 MB on the server and -14 MB on Postgres** (six fewer backends); `PYTHONOPTIMIZE=2` -8 MB; a one-certificate `SSL_CERT_FILE` -8 MB (lab only: a loopback-only server never verifies a public CA; not applied, it would need a generated PEM).
* **Helped, import trim** (`lean_imports/sitecustomize.py`): stubbing the MCP stack (`fastmcp` + `mcp`, 24 MB and 198 modules), the Gemini provider and Google auth (9 MB) and the OTLP exporter **-52 MB** (272.9). The import-only RSS probe
  (`ram-opt/import_attrib.py`) shows where it comes from: 187 MB with everything, 136 MB with the stubs; the rest of the import floor is `hindsight_api` itself (33), `openai` (17, needed), `numpy` (10, needed), `dateparser` (7), `aiohttp` (7), FastAPI (5), httpx (4).
  The stubs are inert objects installed before `hindsight_api` imports; the module **chains the egress audit hook** (only one `sitecustomize` is found on `sys.path`; a test pins that the hook still logs `hook-loaded`).
* **Combined**: every config-only setting that helped (`c-safe`) is **-21 MB on the server (304.9) and -16 on Postgres**; with the import trim (`c-lean`) **-65 MB on the server (261.1)**; stack steady 408.5 -> 339.3 (-17%), burst 424.9 -> 340.9 (-20%).
  The final shipped set (`s-fb`, section 2.3) is the one that went through the functional checks.
* **Where the remaining 261 MB is** [measured + inferred]: 136 MB is the import floor; about 125 MB is server start-up and serving (pools, 77 FastAPI routes' Pydantic validators, tokenizers, per-request buffers), 240 MB of it anonymous Python heap. I did not find a switch for that part.

### 2.1 The floor: how little memory the server survives in

| MemoryMax on the server's unit (MemorySwapMax=0) | result | server PSS | note |
|---|---|---|---|
| 350 MB | survives: 200 retains / 200 recalls / storm, 0 errors | 262.4 | stack steady 336.3 (stub embedder) |

One attempt at 300 MB (the same lean settings) never made the server healthy in 240 s (the cgroup limit counts page cache and the import reads 1.1 GB of files); its result file was lost to a cleanup bug that left the stack running (fixed, with a test); 325, 270 and 240 MB were queued and not reached before the floor ended the session.
So: **between 300 and 350 MB is the floor of the lean server's cgroup; 350 MB runs the whole workload** [measured for 350, 300; the rest unmeasured].

### 2.2 Concurrency safety of the 1..2 pool

A connection pool of two could deadlock when a writer holds a connection while a reader waits for one. The chunks-mode storm of the lean runs puts **4 writer threads (8 retains each) next to the 8 reader threads (10 recalls each)**: 0 errors, no timeout,
80 recalls + 32 retains in 2.98 s on the lean server against 2.96 s on the default server with the same writers (and 80 readers-only recalls in 2.0 s on both). [measured]

### 2.3 The functional check on the shipped set

`ram_opt.py --functional` runs the REAL adapter's H1 flow on the server under test: four owner-taught turns through the Zoe gate, a recall that must find the replaced fact, `forget("Priya")` (the recall must stop finding her), the stats export, and the bank delete.
* `s-fb` (lean): H1 flow ok (written 4, recall finds the fact True, forgotten True, bank deleted True); storm of 8 readers + 4 writers: 80 recalls + 32 retains in 2.98 s, 0 errors.
* `s-base` (default): H1 flow ok (written 4, recall finds the fact True, forgotten True, bank deleted True); storm of 8 readers + 4 writers: 80 recalls + 32 retains in 2.96 s, 0 errors.

The identical result on the default and the lean server is the point: the import stubs, the 1..2 pool, migration isolation and docstring-free bytecode change no behaviour the adapter uses (chunks mode, no model call; the model path is in 2.4). [measured]

### 2.4 The model path (the live brain)

Live brain (`:11434`, one slot, under `flock /tmp/zoe-brain-window.lock`, panel quiet, anchored `pgrep`), **8 verbatim retains + the adapter's H1 and H2 flows (about 20 model calls)** per server; the stub embedder; scratch Postgres:

| server | retains valid (LLM trace) | retain p50 / p95 | recall p50 / p95 (50 queries) | H1 flow | H2 consolidation | server PSS | stack steady / burst |
|---|---|---|---|---|---|---|---|
| s-base (default) | 8/8 (trace 8/8) | 3039 / 4476 ms | 63.8 / 101.2 ms | ok | completed, 6 rows, no timeout | 324.1 | 382.8 / 403.2 |
| s-fb (lean) | 8/8 (trace 8/8) | 3052 / 4681 ms | 64.1 / 97.1 ms | ok | completed, 6 rows, no timeout | 257.0 | 305.0 / 309.0 |

The lean server is **67 MB smaller on the server and 78 MB on the stack** on the model path, with the same validity, the same retain latency (the model, not the server, is the 3 s), the same recall latency and the same functional result. [measured]

Why only 8 retains, not 200: every longer attempt (9 attempts of 40-200 retains) and four of the 8-retain attempts were stopped by the 1.5 GB floor in their first seconds (start-up: the import, the migrations and the shim's model load; the sample timelines in `ram-opt/*.samples.json` show the breach at t = 0-10 s, before the first model call), so a long live-brain run could not be completed under the floor. The server's RSS on the model path equals its RSS on the chunks path within 3 MB (326 vs 324 default, 263 vs 257 lean), which is what lets the 200-retain screening above stand for it. [measured]

## 3. Experiment 2: one embedder for everything

### 3.1 The shim itself

The shim (bge-small int8 ONNX, 2 intra-op threads, arena off) was 133 MB at idle but **161-174 MB median while serving** (max 173-185, VmHWM 179-192) under the traffic a Hindsight server sends it (300 single texts, 100 batches of 8, 20 long texts).

| config | what changed | PSS idle | PSS serving (median) | PSS max | VmHWM | single p50/p95 ms | batch-8 p50/p95 ms | long p50/p95 ms | runs |
|---|---|---|---|---|---|---|---|---|---|
| h-shim-base | the shim as it is (bge-small int8, 2 threads, basic graph optimisation) | 131.8 | 167.5 | 179.3 | 185.3 | 35.7/43.9 | 127.2/144.9 | 235.9/287.2 | 2 |
| h-shim-bge | serve bge-small explicitly | 131.0 | 159.1 | 167.8 | 173.4 | 35.2/43.2 | 127.8/144.1 | 234.0/283.7 | 1 |
| h-shim-t1 | one ONNX thread | 130.8 | 155.4 | 161.1 | 166.7 | 48.3/59.1 | 201.1/233.4 | 412.3/508.4 | 1 |
| h-shim-opt-all | full graph optimisation | 142.0 | 151.2 | 164.1 | 169.7 | 27.8/35.5 | 124.0/140.4 | 227.8/277.7 | 2 |
| h-shim-opt-off | no graph optimisation | 132.9 | 159.2 | 170.9 | 176.6 | 34.3/42.6 | 126.4/142.9 | 233.6/285.5 | 1 |
| h-shim-a1 | MALLOC_ARENA_MAX=1 only (no trim threshold) | 130.9 | 155.2 | 166.6 | 176.6 | 36.2/44.4 | 132.6/148.6 | 239.3/291.9 | 2 |
| h-shim-arena1 | MALLOC_ARENA_MAX=1 and a low trim threshold | 131.1 | 139.4 | 152.7 | 166.0 | 37.5/47.2 | 177.0/205.3 | 352.2/431.4 | 1 |
| h-shim-best | full graph optimisation + MALLOC_ARENA_MAX=1 | 142.0 | 145.6 | 159.9 | 166.4 | 28.0/36.0 | 125.0/141.7 | 233.1/288.2 | 2 |
| h-shim-minilm | serve Chroma's MiniLM (zoe-data's live embedder) instead of bge-small | 154.0 | 164.6 | 193.4 | 232.4 | 22.7/25.6 | 69.3/82.1 | 152.6/192.1 | 1 |
| h-shim-best-minilm | full graph optimisation + MALLOC_ARENA_MAX=1, serving MiniLM | 153.4 | 157.3 | 177.6 | 203.1 | 22.6/24.7 | 68.5/80.3 | 146.3/182.6 | 1 |

* **Full graph optimisation (`ZMB_ORT_OPT=all`)**: idle +10 MB, but **serving -9 / -16 MB and peak -13 / -17 MB, and single-text latency -22% (35.7 -> 28 ms p50)** in both runs. The old comment "basic: about -30 MB at load" was measured against `disable`/`extended` before the arena was off; the serving figure is what the gate sees.
* **`MALLOC_ARENA_MAX=1`**: serving -12 MB, post-traffic back to 131 MB (the shim trims after every request). With the low trim threshold added it saved more (139.4) but cost +40% batch latency: not shipped.
* **Both together (`h-shim-best`)**: serving 145.6, max 159.9, VmHWM 166.4 (vs 167.5 / 179.3 / 185.3): **-22 MB serving, -19 MB peak, -22% single-text latency**. One intra-op thread is no gain and +35% latency.
* Applied to the window's shim unit by default (`BAKEOFF_LEAN`), as `ZMB_ORT_OPT=all` + `MALLOC_ARENA_MAX=1` (`embed_shim.py` reads `ZMB_ORT_THREADS` / `ZMB_ORT_OPT`; defaults unchanged).

### 3.2 Could one model serve every tier? (MiniLM in the shim)

The shim can now serve Chroma's MiniLM (`--model minilm`; before, a MiniLM directory handed to `--model-dir` was pooled by the CLS token: right dimension, wrong vectors, a red-before-green test pins the fix).

* **Same vectors as zoe-data's embedder** [measured]: the shim's MiniLM against `chromadb`'s own `ONNXMiniLM_L6_V2` on 120 texts: cosine min 1.000000, mean 1.000000. The shim serving MiniLM IS zoe-data's embedder over HTTP.
* **Quality** [measured, `pilot/embed_quality_probe.py`, 12 seeds x the lab's 20 needles, direct and paraphrase, pure retrieval over needles + distractor facts]:

| distractors per seed | bge-small (shim today) direct / paraphrase | MiniLM direct / paraphrase |
|---|---|---|
| 180 | 240/240, 238/240 (0.992) | 240/240, 239/240 (0.996) |
| 700 | 240/240, 235/240 (0.979) | 240/240, 236/240 (0.983) |

  Indistinguishable on these sets (one question in 240). The lab's needles are easy (the subject's name is in both the fact and the question), so this says "no loss", not "better".
* **Cost and gain** [measured]: MiniLM is fp32 (90 MB) against bge's int8 (33 MB): shim serving 157.3 vs 145.6 (+12), peak 177.6 vs 159.9 (+18), VmHWM 203 vs 166 (+37) with the same tuning; **latency is far better: single text 22.6 ms vs 28 ms (and vs 35.7 ms today), batch of 8 68.5 vs 125 ms (-45%)**: MiniLM has six layers, bge-small twelve.
* **Hindsight-level recall (the REAL server, the real shim, chunks mode, a 200-row store of 20 needles + 180 near-miss / preference facts, hit@5 over Hindsight's own fused ranking)**: bge-small **26/40 direct, 40/40 paraphrase** over 2 run(s) against MiniLM **20/20 direct, 20/20 paraphrase** over 1 run(s) (per run, bge: 11/20, 15/20; MiniLM: 20/20). This is NOT the pure-retrieval tie above: Hindsight fuses vector, text, graph and temporal signals, and with bge-small's tight similarity space the direct question's answer is pushed out of its top five far more often than with MiniLM. Run 1 / the first-contact probe (26 rows) read 20/20 on bge; on the 200-row store it does not. **The bake-off's D axis compares Z0e (MiniLM) with H arms on bge: an embedder confound the owner should know about before reading run 2** [measured, see the caveat on sample size].
* **Conclusion**: one model for all tiers is feasible with no recall loss on these sets. Under adoption the cleanest form is no separate shim at all: a `/v1/embeddings` route in zoe-data over its existing MiniLM session
  (it already exists; the stack then loses the shim's whole 146-160 MB, and Hindsight, MemPalace and Zoe's own recall share one vector space). It is **not** made the bench default: it would move every H arm's recall away from run 1's (`BAKEOFF_SHIM_MODEL=minilm` switches it for a deliberate experiment).

### 3.3 The shared embedder for the HM verbatim tier

MemPalace 3.10.0 has an `openai-compat` embedding backend [source: `mempalace/embedding.py`]. The adapter now sets it (`ZMB_HM_EMBEDDER_URL`, loopback only, refused otherwise; a red-before-green test pins it and that the environment is restored): the verbatim tier asks the shim Hindsight already uses and the driver loads no ONNX session.
The real adapter over the real library (`pilot/hm_ram_probe.py`, 200 turns, 200 queries, a forget with the palace rebuild; the scrubbing allocator the forget cell needs is ON in both):

| | own MiniLM session (the bench's default) | shared embedder (the shim) | change |
|---|---|---|---|
| driver PSS after imports (chromadb + mempalace) | 61.8 MB | 65.5 MB | 3.7 MB (+6%) |
| driver PSS after the first embed ("warm") | 251.2 MB | 91.1 MB | -160.1 MB (-64%) |
| driver PSS after 200 turns, 200 recalls, a forget + palace rebuild | 248.6 MB | 96.2 MB | -152.4 MB (-61%) |
| driver peak (VmHWM) | 258.1 MB | 104.2 MB | -153.9 MB (-60%) |
| verbatim recall p50 | 70.7 ms | 32.0 ms | -38.7 ms (-55%) |
| verbatim recall p95 | 75.4 ms | 35.0 ms | -40.4 ms (-54%) |
| ingest of 200 turns | 17.6 s | 9.1 s | -8.6 s (-49%) |

* The driver's own ONNX session was the whole bill: **+189 MB at its first embed (61.8 -> 251.2 MB PSS)**; shared, it is 91 MB at the same point and **-155 MB at the end**. The shim, already running for Hindsight, reads 138 MB during the same traffic (it did not grow).
* The verbatim recall got **faster: p50 70.7 -> 32.0 ms, p95 75.4 -> 35.0 ms**, and a 200-turn ingest 17.7 -> 9.1 s: the shim batches unpadded and with full graph optimisation, where Chroma's function pads every text to 256 tokens (the HM record's 39 ms per document).
* hit@5 on the 40 needle questions: 40/40 local, 39/40 shared (bge-small and MiniLM disagree on one paraphrase; the sets in 3.2 say the models tie).
* **The 21 HM cells on the real library, both modes** (`hm_cells.py --store library`; same verdicts, controls and targets):

| run | graded | controls checked / red | target still failing | driver PSS peak over the cells |
|---|---|---|---|---|
| own session, before the palace-release fix | graded 17/17 | 18 / all red | ['HM-F5.forget.stt-misspelling'] | 391.1 MB |
| shared embedder, before the fix | graded 17/17 | 18 / all red | ['HM-F5.forget.stt-misspelling'] | 215.0 MB |
| own session, after the fix | graded 17/17 | 18 / all red | ['HM-F5.forget.stt-misspelling'] | 284.4 MB |
| shared embedder, after the fix | graded 17/17 | 18 / all red | ['HM-F5.forget.stt-misspelling'] | 132.7 MB |
* Applied by default in the window (`BAKEOFF_HM_SHARED_EMBEDDER=0` switches it off).
* In the production design (V hosted inside zoe-data, sharing its embedder) the HM record measured +14 MB; this probe shows the other shape, a separate driver, no longer pays a second session either.

## 4. Experiment 3: Postgres

The brief asked for `shared_buffers` / `work_mem` minimal for a 256 MB cap. Measured (the container's own cgroup, 200-row stores, the window's `docker stats` figure):

| config | Postgres, `docker stats` (the gate's figure) | cgroup anonymous / page cache | what it shows |
|---|---|---|---|
| s-base (n=3) | 68.0 MB | 29.5 / 67.1 MB | run 2 settings: shared_buffers 64 MB, 40 connections, pool 1..8, 256 MB cap |
| s-pgmin (n=1) | 68.7 MB | n/a / n/a MB | shared_buffers 16 MB, 20 connections, work_mem 2 MB, no JIT, no parallel workers, 128 MB cap |
| s-pg96 (n=1) | 59.1 MB | 24.8 / 62.4 MB | the minimal flags in a 96 MB container |
| s-pool2 (n=1) | 54.1 MB | n/a / n/a MB | pool 1..2 only |
| c-lean-pg (n=1) | 60.1 MB | 19.3 / 99.4 MB | lean server + minimal flags |
| s-fb (n=1) | 52.0 MB | 18.9 / 68.9 MB | the lean server's run: pool 1..2 (shipped) + minimal flags + 96 MB cap (NOT shipped) |

* **The flags do nothing to the gate's figure.** `shared_buffers` 64 -> 16 MB, 40 -> 20 connections, `work_mem` 4 -> 2 MB, JIT off, no parallel workers: 68.7 vs 69.3 MB. A 200-row store touches a few hundred kB of the buffer pool.
* **What `docker stats` counts is mostly page cache, not Postgres.** The cgroup's `memory.stat` splits it: **15-32 MB anonymous (the backends and their work memory), 25-116 MB file cache** (the data files initdb and the migrations wrote, the WAL segments). Page cache is reclaimable and does not
  reduce MemAvailable the way the same number of anonymous bytes would, but the gate reads it, and it moves 52-80 MB from run to run for the same configuration (the noise floor of this number is about +-8 MB).
* **What did move it: connections.** The pool of 8 opens up to eight backends; a pool of 1..2 cut the anonymous part from 29.5 (s-base with the functional flow) to 18.9-23.5 MB and the `docker stats` figure by about 14 MB (54.1 vs 69.3). That is applied (through the pool), not through a Postgres flag.
* **A smaller container cap** (96 MB) reads 59 MB because the kernel reclaims cache at the cap instead of keeping it: a smaller number for the same real memory. It is **not applied**: the window's physical-erase cells (`VACUUM FULL`, `docker cp` of the data directory, WAL switching) were not run under a 96 MB cap, and a cap that makes
  Postgres thrash is a worse failure than 10 MB of gauge. The compose file stays at 256 MB and the run 1 / 2 flags.


## 5. Experiment 4: latency

* **Recall p95 on 50 queries** (Hindsight server only, stub embedder): 98-103 ms in every configuration (p50 65-69): none of the RAM changes costs recall latency; the lean set's p95 is 99.1 (c-lean) / 102.1 (s-fb) against 100.9. With the real embedder the query embedding adds the shim's single-text latency: **bge today ~36 + 100 = ~140 ms (first contact measured 141), tuned bge ~28 + 100 = ~128 ms, MiniLM ~23 + 100 = ~123 ms** [derived from two measured parts].
* **The HM two-lookup overhead on the real tiers**: (the two-tier probe did not complete under the memory floor; the verbatim lookup alone is measured in 3.3: p95 75.4 -> 35.0 ms, and the HM-L2 cell passes on the real library in both modes)
* **The voice lane's write-behind cache hit path** (`HMArm.packet(lane="voice")`) was O(rows): it re-tokenised EVERY cached row on EVERY voice turn, twice under `real_latency`. The report's "0.01 ms" came from cells that cache five rows.
  Measured on doubles (`ram-opt/cache_probe.py`): **1.62 ms at 220 rows, 8.2 ms at 1,020, 35.0 ms at 4,020** (one quarter of turns). Fixed with an inverted token index built at refresh time (same hits, same order, a test pins both and that a query tokenises only itself; red before green):
  **0.014 / 0.041 / 0.159 ms** at the same sizes, 0.445 ms at 10,020. The refresh itself (off the voice path) costs 2.2 / 11 / 66 / 123 ms at 220 / 1,020 / 4,020 / 10,020 rows, and it runs after EVERY `ingest()` call, so one-turn-at-a-time writes are O(N) each:
  the production write-behind must debounce it (it is not on the turn path in the design; the bench does it inline). [measured; the debounce is an [inferred] recommendation, not built]
* **Unchanged on purpose**: the 50 ms-class numbers of the chat lane (two lookups) depend on the real tiers; see the line above.

## 6. The best combined configuration against the gate and against the 2 GB floor

What "best" means here: the shipped set (`BAKEOFF_LEAN`, default ON): server = migration isolation + pool 1..2 + import trim + `PYTHONOPTIMIZE=2` + one BLAS thread; shim = full graph optimisation + one malloc arena (bge-small, unchanged model); Postgres = unchanged flags and cap, pool 1..2 on the server side.

| | hindsight-api | Postgres | shim | **steady** | **burst** | vs G0 (600 / 900) |
|---|---|---|---|---|---|---|
| run 1, measured by the window (reference) | | | | 521-532 | 595-603 | pass |
| run-2 settings, parts summed | 326.9 | 68.0 | 160.8 | **556** | **591** | pass (44 / 309 MB margin) |
| **shipped set, parts summed** | 263.3 | 54.1 | 145.6 | **463** | **479** | pass (137 / 421 MB margin) |
| shipped server + real shim + 96 MB / minimal-flag Postgres, one composite run [measured] | 253.5 | 45.4 | 124.6 | 424.3 | 433.4 | pass |
| HM (Hindsight + MemPalace), run 2 as measured: servers 570-580 + verbatim tier growth | | | | 770-895 | | **FAIL** (> 600) |
| HM, shipped settings, verbatim tier in zoe-data on the shared embedder (+14 MB, HM record) [derived] | | | | ~496 | | pass |

* Components are measured separately and summed (PSS is additive and the window's figure is that sum): "steady" is the median of the server, the Postgres container and the shim's serving median; "burst" is the stub-embedder stack's max with the real shim's measured max added.
  The sum reproduces run 1's measured burst (591 vs 595-603) and sits 30 MB above its steady (the shim in a window serves lighter traffic than my 420-request test: 124.6 MB median in the composite run).
* **G0 (<= 600 steady, <= 900 burst)**: H1 passes before and after; HM, as the owner designed it (verbatim tier inside zoe-data on the shared embedder: +14 MB measured), is about 496 MB after, against 770-895 before.
* **Against the box's 2 GB voice floor** the question is MemAvailable after the stack is running. Today's idle MemAvailable was 1.76 GB median (1.13-2.23 range, 82 readings with none of my processes running); the optimised H1 stack is 463 MB, so about 1.30 GB would remain:
  **still under 2.0 GB by 0.70 GB at the median**. Folding the embedder into zoe-data (a `/v1/embeddings` route over the MiniLM session it already holds: the shim's 145.6 MB disappears) leaves 317 MB and about 1.44 GB: closer, **still short of 2.0 GB by 0.56 GB at the median**,
  and the shortfall is the box's, not Hindsight's (the HM record's reclamation list: `--lazy-mode`, `--cache-ram`, the Kokoro sidecar's 2.25 GB).

## 7. What is still over, and what was not done

* **Still over the 2 GB voice floor** (above). The gate G0 is met; the floor is the box's, and no setting found here is large enough to close it alone.
* **What remains of the server (263 MB)** is the interpreter heap: 136 MB of imports that cannot be stubbed without breaking the engine (`hindsight_api`, `openai`, `numpy`, `dateparser`, FastAPI, Pydantic, SQLAlchemy in the migration child) and about 125 MB of start-up and serving state I found no switch for.
  A smaller engine would be a change to Hindsight, not to this lab.
* **The shim (146 MB serving)** is an ONNX Runtime session (about 80 MB of library) plus the 33 MB model; only folding it into zoe-data removes it (section 3.2).
* **The import trim is a lab-grade hack** (inert stubs for `fastmcp`, `mcp`, `google.genai`, `google.auth`, `opentelemetry.exporter.otlp`): an engine upgrade can start using one of them. Under adoption, install a Hindsight without those extras or lazy-load them. It is on for the window because the functional checks pass identically and the egress hook is chained;
  `BAKEOFF_LEAN=0` is the escape.
* **Not applied, deliberately**: `PYTHONMALLOC=malloc` on the server (+16 MB), `MALLOC_ARENA_MAX=2` (no effect), the concurrency caps (storm +34%), the in-process worker off (H2 / H0 need it), a 96 MB Postgres cap (gauge only, risky for the erase cells), a one-certificate `SSL_CERT_FILE` (-8 MB, needs a generated PEM), MiniLM as the default embedder (an embedder confound is a decision, not a tweak).
* **Not measured**: a 200-retain verbatim run through the live brain on the lean server under the floor (8 retains + the H1 / H2 flows done); the concise-mode (H2 / H0) extraction path at scale; the HM two-tier latency on the REAL tiers (Hindsight + the library together: four attempts, all stopped by the floor; the verbatim lookup alone and the HM-L2 cell are measured, section 3.3 / 5); the real window; the Gemma clone's RAM next to the lean stack.
* **A design caveat found on the way**: `HMArm._refresh_cache` runs after every `ingest()` and is O(rows) (2.2 ms at 220 rows, 66 ms at 4,020): fine as a nightly write-behind, wrong inline per turn. The production write-behind must debounce it. [inferred]
* **The H arms' recall is embedder-dependent** (section 3.2): worth one deliberate decision before reading run 2's D axis.

## 8. What was applied (so run 2 uses it)

| Where | Change | Test (red before green) |
|---|---|---|
| `scripts/perf/zmb/bakeoff.py` | `Cfg.lean` (`BAKEOFF_LEAN`, default on): migration isolation, pool 1..2, the import trim (chained after the egress hook), `PYTHONOPTIMIZE=2`, one BLAS thread on the server; `ZMB_ORT_OPT=all` + `MALLOC_ARENA_MAX=1` on the shim unit. `Cfg.shim_model` (`BAKEOFF_SHIM_MODEL`, default auto = unchanged). `Cfg.hm_shared_embedder` (`BAKEOFF_HM_SHARED_EMBEDDER`, default on) | `test_zmb_bakeoff.py` (3 new) |
| `scripts/perf/zmb/bakeoff_measure.py` | the HM driver is started with `ZMB_HM_EMBEDDER_URL` = the window's shim | same |
| `scripts/perf/zmb/lean_imports/sitecustomize.py` (new) | inert stubs for packages the loopback server never uses; chains `egress_audit/sitecustomize.py` | `test_zmb_ram_opt.py` |
| `scripts/perf/zmb/embed_shim.py` | `--model auto/bge/minilm` (a MiniLM directory was pooled by the CLS token), `ZMB_ORT_THREADS`, `ZMB_ORT_OPT` | `test_zmb_embed_shim.py` (3 new) |
| `scripts/perf/zmb/arms/mempalace_verbatim.py` | `ZMB_HM_EMBEDDER_URL`: MemPalace's `openai-compat` backend against a loopback shim (refuses a remote URL; restores the environment); `close()` releases the palace's cached Chroma client | `test_zmb_hm_arm.py` (3 new) |
| `scripts/perf/zmb/arms/hm.py` | the voice cache is indexed at refresh; a query tokenises only itself | `test_zmb_hm_arm.py` (2 new) |
| `scripts/perf/zmb/ram_opt.py`, `stub_embed.py`, `pilot/{embed_quality,hm_ram,palace_release}_probe.py` (new) | the lab and its probes | `test_zmb_ram_opt.py` (11) |
| `docs/knowledge/bakeoff-howto.md` | a "RAM settings" section | |
| `/home/zoe/.zoe/bakeoff-2026-10/hindsight.env.example` (outside the repo; the old file is kept as `.pre-ropt`) | the pool and migration-isolation values the runner now sets | |

## 9. Reproduce (all under `/home/zoe/.zoe/bakeoff-2026-10/`; the lab refuses to start under the memory floor)

```bash
R=/home/zoe/.zoe/venvs/zoe-data-py312/bin/python; Z=scripts/perf/zmb          # from a worktree of this branch
$R $Z/ram_opt.py --list                                                       # every configuration
$R $Z/ram_opt.py --config s-base --mode chunks --wait-min 25 --attempts 4     # baseline (stub embedder, 200 retains / 200 recalls / 50 timed / storm)
$R $Z/ram_opt.py --config s-fb   --mode chunks --functional --wait-min 25     # the shipped server set + the adapter's H1 flow
$R $Z/ram_opt.py --config s-fb   --mode verbatim --retains 8 --recalls 20 --functional   # live brain: lock + panel-quiet + anchored pgrep
$R $Z/ram_opt.py --shim --config h-shim-best                                  # the shim alone, 420 requests (also h-shim-base, h-shim-minilm, ...)
$R $Z/ram_opt.py --hm shared --config h-shim-best                             # the HM driver on the shared embedder  (--hm local = its own session; --hm-cells = the 21 cells)
$R $Z/ram_opt.py --config f-b --mode chunks --seed q1 --tag q1               # real shim, Hindsight-level hit@5 (f-b-minilm = MiniLM)
$R $Z/ram_opt.py --table 'ropt-*.json'                                        # one line per result
/home/zoe/.zoe/bakeoff-2026-10/hindsight-venv/bin/python $Z/pilot/embed_quality_probe.py --seeds 12 --model bge|minilm [--distractors 700]
/home/zoe/.zoe/bakeoff-2026-10/mempalace-venv/bin/python  $Z/pilot/embed_quality_probe.py --parity                   # shim MiniLM vs Chroma's function
bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh $Z/pilot/palace_release_probe.py none|own|all --palaces 24
/home/zoe/.zoe/bakeoff-2026-10/ram-opt/run_attrib.sh /home/zoe/.zoe/bakeoff-2026-10/ram-opt/import_attrib.py [ZMB_LEAN_STUBS=...]   # import RSS by package
```

Result files: `ram-opt/ropt-*.json` (+ `.samples.json`, one row per second), the scripts `queue*.sh`, `build_doc.py` (this document's tables are generated from the JSON).

## 10. Lanes and what was stopped

Lanes, each its own process, with a MemAvailable watchdog (`ram-opt/lane.sh`, kills under 1,450 MB; the box's own idle dips reached 1,428-1,468 MB during them) [measured, 2026-10-07 02:5x]:

* **`scripts/perf/zmb services/zoe-data/tests/test_zmb_lab.py`: 86 passed** (39 s, min MemAvailable 1,605 MB).
* **`tests/unit -m ci_safe` with `ZMB_HINDSIGHT_URL=http://127.0.0.1:1`: 2,797 passed, 10 skipped, 54 deselected; 17 failed, none of them a test of this change**:
  * **15 in `test_zmb_bakeoff.py` fail identically on the unmodified PR #1902 tree** (run here with the original sources and tests swapped in: 15 failed, 2,778 passed): `ImportError: cannot import name 'name_pattern' from 'memory_forgotten'` raised inside `measure()`, a cross-file module-state pollution in the full lane; each of them passes when run alone (`pytest tests/unit/test_zmb_bakeoff.py`: 106 passed with this change).
    Not caused by, not fixed by, and not hidden by this PR; it needs its own look.
  * 2 more in the same file read the box's real processes (`pgrep` for a running voice probe, the voice harness lock) and failed in one full-lane run while another session was using the box; they pass alone (5 passed).
  * `test_real_library_runs_the_same_cells_with_the_same_controls` (the real MemPalace library in a subprocess, about 300 MB) was stopped by the lane's own memory watchdog in two runs (the lane's min MemAvailable 1,428 MB) and is deselected from the third; it passes alone (13 s) with the new close() release and the shared-embedder code in place.
* The new tests alone: `test_zmb_ram_opt.py` 11 passed; the 11 new tests in `test_zmb_embed_shim.py` / `test_zmb_hm_arm.py` / `test_zmb_bakeoff.py` are **red on the original sources and green on the new ones** (11 failed -> 11 passed, `scratchpad/redgreen2.sh`); `test_zmb_ram_opt.py`'s cleanup test is red with the old `join` (checked by reverting that line).


Everything the lab started was stopped after every run (and on kill: the SIGTERM handler, the pre-clean of this lab's own container and unit names, the stack stopped before any result is summarised, a test for the case that failed once): at the end `docker ps -a` shows no `zoe-ropt-*`, `systemctl --user` no `zoe-ropt-*` unit,
no `hindsight-api`, `embed_shim.py`, `stub_embed.py` or `ram_opt.py` process, the brain lock is free, and the scratch data directories are removed. The live brain, zoe-data, the live Postgres, the live palace and `/home/zoe/assistant` were never touched; the live brain served only the model-path runs (8 retains + the adapter's H1 / H2 flows, once per server) and the attempts that were stopped earlier; every one of them took `/tmp/zoe-brain-window.lock`, waited for panel quiet and checked the anchored `pgrep` / harness lock.
