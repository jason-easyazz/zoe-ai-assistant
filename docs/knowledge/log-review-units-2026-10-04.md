---
type: Record
title: Evening log review — inference and lane units (2026-10-04)
description: Read-only inventory of the brain, router, Kokoro, Flue sidecar, Telegram lane, Serena and CI-runner logs plus the landing log and voice-probe trend for 2026-10-04 17:00-21:47 — no crashes, a stale router unit (no --mlock), a stale Kokoro start timeout, false status=143 failures, 19 min of TTS pause from landing probes, the latency trend table, root causes, fixes and remaining items.
tags: [log-review, systemd, unit-drift, router, mlock, kokoro, flue, telegram, llama-server, voice-probe, latency, landing]
timestamp: 2026-10-04T22:00:00+08:00
---

# Evening log review — inference + lane units (2026-10-04)

Read-only review of the user-manager journals for the brain, router, TTS, Flue
sidecar, Telegram lane, Serena and the self-hosted CI runner, plus the landing
automation log and the voice-probe trend, for **17:00 -> ~21:47 AWST (UTC+8)** on
2026-10-04. The box booted at 14:35, so every long-lived unit has been up ~7 h and
NONE of the inference units restarted in the window. No household data below; probe
sample text, transcripts and user utterances are deliberately not quoted.

Method: `journalctl --user -u <unit> --since "2026-10-04 17:00"`, the landing log, the probe trend,
`/proc/<pid>/status` and read-only diffs of installed units vs `scripts/setup/systemd/`.

## 1. Verdict

* **No crash, OOM, restart-loop, CUDA/NvMap error, abort storm or Telegram polling
  error in the window.** `llama-server`, `functiongemma-router`, `flue-zoe-brain-2x`,
  `serena-mcp` all show `NRestarts=0`; every process has `VmSwap: 0 kB`.
* The one probe that failed (19:58, brain median 1.63x baseline) was a transient
  slowdown right after a deploy; the re-probe 1 min later passed. No latency TREND
  across the evening (section 3).
* What the logs DO show is **self-inflicted churn and drift**, not instability:
  1. the live **router unit is stale** against its merged template (no `--mlock`,
     no `LimitMEMLOCK`, `MemoryMax` 1G vs 1280M) — the exact page-refault guard
     merged on 10-03 is not running;
  2. the live **Kokoro unit is stale** (`TimeoutStartSec=120` vs the template's
     300, while the sidecar itself waits up to 180 s for the brain);
  3. every Flue/Node unit restart is logged as a **failure** (`status=143`);
  4. Kokoro (TTS) was down **18.9 min** in the 4 h 45 min window because every
     landing probe pauses it, one landing for **5 min** while only waiting for a deploy;
  5. the landing queue burned **10 probes for 4 merges** (BEHIND/BLOCKED re-queues).

## 2. Inventory — per unit, per class

Counts are for the window; "sample" is redacted.

### llama-server (brain, :11434) — 1168 lines, 146 requests, 0 W/E

| Class | Count | First / last | Sample (redacted) | Cause / config |
|---|---|---|---|---|
| Warning / error / crash / OOM / restart | **0** | — | — | `NRestarts=0`, up since 14:35:48; no `W`/`E` lines at all |
| Slot picked by LRU (prefix miss) | 23 | 18:35:53 / 21:42:07 | `selected slot by LRU, t_last = N` | 14 are the 2 x 5-token **zoe-data warmup pairs** after each of 7 deploys (only 5 tokens evaluated, so the long prefix came from the host prompt cache -> healthy); 7 are the first turn of a probe (453-461 prompt tokens, ~0.87 s prefill, the count creeps +1-2 per hour); 2 singletons (18:35, 21:00) |
| Slot picked by LCP (prefix reuse) | 123 | — | `selected slot by LCP similarity, f_sim_best = 0.99` | healthy: `--cache-ram 2048` + `--parallel 1`, tail-only mutation |
| Large re-prefill (>1400 tokens) | 22 (2 per probe x 11) | 19:57:17 / 21:45 | `prompt eval time = 2562 ms / 1568 tokens` | probe corpus switches conversation (f_sim 0.68 / 0.91); iSWA slot cannot partially reuse beyond the checkpoint. These two turns per probe are also the only Flue `first_delta` >2 s (flue-zoe-brain-2x table below). Not a defect; documented so nobody chases it |
| MTP draft acceptance | 146 | — | `draft acceptance = 0.53 (2112 / 4004)` | 0.50-0.56 per hour, long generations 0.30-0.40; decode 26-30 tok/s (26.4 during the 19:50 deploy + CI burst). No warning is emitted below any threshold. Not actionable without a new `--spec-draft-n-max` / `p-min` A/B (replay-gated) |


**Unit vs template: identical** (`unit_drift_check.py`: no findings for
`llama-server`). `--cache-ram 2048`, `--parallel 1`, `--fit off`, `--load-mode mmap+mlock`,
`MemorySwapMax=0`, `MemoryLow=6G` are all live. `VmLck` 1.95 GB of 6.1 GB RSS (the
documented Tegra limit — mlock does not reach CUDA allocations), `VmSwap` 0.

### functiongemma-router (:11436) — 1207 lines, 116 requests

| Class | Count | First / last | Sample | Cause / config |
|---|---|---|---|---|
| `W ... restored context checkpoint` | 117 | 19:47:25 / 21:45 | `restored context checkpoint (pos_min = 0, pos_max = 30, ... size = 0.455 MiB)` | **Benign.** llama.cpp logs a prompt-prefix cache HIT at WARN. One per request; this is the system-prompt checkpoint doing its job. Do not alert on it |
| `W srv stop: cancel task` | **1** | 21:39:25 | `cancel task, id_task = N` | client gave up: the call ran ~2 s against the 1.5 s `ZOE_ROUTER_TWO_STAGE_TIMEOUT_S`. Happened during a probe, with the deploy build and CI runner active (load avg 3.4, 4 router threads). `decide()` returns None and the turn silently keeps the similarity route. See page-refault below |
| Latency | 116 | — | median 324 ms, p90 396, max 611 | `gap <10 s` median 339 ms; `gap >300 s` median 132 ms — **no cold-start / refault signature in this window** (the box had 0 swap on the router) |
| Crash / restart | 0 | — | — | up since 14:35:40 |

**Page-refault / `--mlock` (confirmed stale live unit).** Live vs template:

```
MISSING   functiongemma-router: ExecStart --mlock          template=''          live=(absent)
MISSING   functiongemma-router: LimitMEMLOCK               template=infinity    live=(absent)
DIFFERS   functiongemma-router: MemoryMax                  template=1280M       live=1G
```

Live `VmLck: 0 kB` (weights not locked), `VmSwap: 0` right now only because the box
has had no pressure since the 14:35 boot. The template change (#A1, 2026-10-03) is
correct and test-pinned (`tests/unit/test_llama_server_unit_flags.py`); it was never
applied. **Operator step: incident-runbook section 24(a).**

### kokoro-tts (:10201) — 312 lines, 11 stop/start cycles

| Class | Count | First / last | Sample | Cause / config |
|---|---|---|---|---|
| Stop/start cycles | 11 | 19:47:00 / 21:44:53 | `Stopping Kokoro TTS Sidecar` | not crashes: each landing probe pauses Kokoro to free ~2.6 GB (`land_voice_pr.sh`). Down 80-89 s each = stop -> `ready on port 10201` (≈14 s of that is import + CUDA load) |
| Longest outage | 1 | 21:34:46 -> 21:39:35 (**303 s**) | — | the helper stops Kokoro **before** `wait_deploy`, so a PR that must wait for another PR's deploy keeps TTS down ~5 min with nothing running. Total down time 1134 s = **18.9 min** of the window |
| `WARNING: Defaulting repo_id to hexgrad/Kokoro-82M` | 11 | each start | as written | `KPipeline(lang_code=...)` without `repo_id`. **Fixed** (below) |
| `HEAD https://huggingface.co/...` | 44 (4 per start) | each start | `HEAD .../kokoro-v1_0.pth "302 Found"` | online etag check although the snapshot is cached: a start-time dependency on the public internet. **Not changed** — an offline-from-cache option was prototyped and dropped (unbenchmarked benefit, not worth a new flag) |
| `FutureWarning weight_norm`, `UserWarning dropout` | 11 + 11 | each start | torch deprecation | upstream (kokoro 0.9.x + torch); no action |
| `/health` | 23 x 200, 0 non-200 | — | — | no flaps; device `cuda` on all 11 starts, `degraded` never set |
| CUDA OOM / exit 3 | 0 in window | (10:29-11:34 earlier, previous boot) | `CUDA OOM after 2 retries` | pre-window; the runbook section 5 class |

**Unit drift:** live `TimeoutStartSec=120`, template `300`. The sidecar waits up to
`ZOE_KOKORO_BRAIN_WAIT_S=180` for the brain's `/health` before its CUDA load, so a boot
where the brain is slow lets systemd SIGTERM Kokoro at 120 s and restart it
(`Restart=always`, 10 s) — a start-up loop that never gets to load. Not hit tonight
(brain was up). **Operator step: section 24(b).** The live-only `PYTHONPATH` and
`ZOE_KOKORO_BACKEND=pytorch` environment entries are harmless host edits.

### flue-zoe-brain-2x (:3579) — 130 lines, 143 turns (`FLUE_EARLY_TEXT`)

| Class | Count | Sample | Cause |
|---|---|---|---|
| `FLUE_ABORT` | **0** | — | `ZOE_FLUE_ABORT_ON_CANCEL=1` is live, but no client cancelled a stream in the window, so the abort path is **unexercised** (unverified, not "verified OK") |
| Errors / restarts | 0 | — | up since 14:35:35, 144 MB |
| `first_delta_ms` | median 339, `first_sentence_ms` median 819 | `first_delta_ms=345 first_sentence_ms=659 deltas=26` | healthy |
| `first_delta_ms` > 2 s | 22 (= 2 per probe x 11) | `first_delta_ms=2643 ... deltas=10` | exactly the two large-re-prefill turns above (2.4 s / 2.66 s ~ llama prompt eval 2.43 s / 2.56 s) |
| `status=143` on stop | (earlier days) 23 | `Main process exited, code=exited, status=143/n/a` | **Fixed** (below); not in the window itself, no stop occurred |

### flue-zoe-telegram (:3582) — 10 lines

| Class | Count | When | Sample | Cause |
|---|---|---|---|---|
| Restart | 1 | 20:05:47 | deploy restart (new voice-note code, flags off) | expected |
| `status=143` + `Failed with result 'exit-code'` | **1** (6 on 10-04, 29+ over the week across both Flue units) | 20:05:47 | `Main process exited, code=exited, status=143/n/a` | node traps SIGTERM and exits 143; systemd counts that as failure. **Fixed**: `SuccessExitStatus=143` |
| Polling errors / ETIMEDOUT / Happy-Eyeballs | **0** | — | `polling (took the bot over)` 3 s after start | the `--network-family-autoselection-attempt-timeout=1500` drop-in is live (and in the template) |


### serena-mcp (:9121) — 55 lines

All `INFO`. 2 x `POST /mcp 400` at 19:39:58, each followed at once by a fresh transport and `200`
(client re-initialising a stale session) — benign; one clean session terminate at 20:31:21.

### self-hosted CI runner — 50 lines

| Job | Succeeded | Failed |
|---|---|---|
| `deploy` | 7 (23-83 s each) | 0 |
| `replay-evidence` (voice-gate) | 6 | **12** |

No job crash, no disk warning (root volume 23% used). The 12 failures are **by design**: the
informational `voice-gate` check is red (`Replay-gate evidence missing for this head`) after every
push until the landing helper probes that exact head and re-runs it (one failed run's log read to
confirm; the rest follow the same fail -> `gate rerun` -> success pairing in the land log).

### Landing automation (`land_queue.log`)

| Event | Count | Detail |
|---|---|---|
| PRs merged | 4 | #1830 19:53, #1833 20:05, #1835 21:34, #1827 21:41 |
| Probes run | 10 completed (+1 running) | -> 2.5 probes per merge |
| `state: OPEN BEHIND` / `BLOCKED` after a passing probe | 5 | #1826 x1, #1835 x2, #1827 x2 — main moved while the probe ran; policy (sig #36) forbids `update-branch` after the probe, so the whole probe is repeated |
| Auto-merge disarmed (`voice-gate is not a required check`) | 2 | by design, forces a re-queue rather than merge unprobed |
| Probe `fail` | 1 | #1826 at 19:58, `SPEED brain_ms 2315 vs 1420 (1.63x)`; re-probed at 20:28 -> pass |
| `failed to run git: fatal: not a git repository` | 5 in the window (14 by 21:50) | the queue's own `gh pr view <n>` ran with a non-repo cwd and no `-R`, so the summary line prints an **empty state** (`queue #1826 -> `) and BEHIND/MERGED outcomes are lost. `land_queue.sh` gained a `cd` into the live checkout while this review was running; the `docs_merge_chain` helper still emits it |

## 3. Latency trend — voice probe, 2026-10-04 evening

Medians over 20 samples (18 scoreable, 2 EMPTY), baseline (recorded
2026-10-03 22:52Z): **STT 559 ms, brain 1419.5 ms, e2e 1780 ms**. All rows:
`said_vs_did_regressions = []`, `memory_recall = ok`, VAD stage `pass`.

| Time | Status | Head | STT ms (x) | Brain ms (x) | e2e ms (x) | Note |
|---|---|---|---|---|---|---|
| 19:48 | pass | 3b5829ba | 530 (0.95) | 1921 (1.35) | 1900 (1.07) | #1830, first probe after the 19:47 Kokoro pause |
| 19:58 | **fail** | 718f8f64 | 595 (1.06) | **2315 (1.63)** | 2211 (1.24) | #1826; 3 min after the 19:54 deploy; decode 26 tok/s in that 10 min bucket |
| 19:59 | pass | ba2bd8a8 | 588 (1.05) | 1707 (1.20) | 2045 (1.15) | #1833 (next in the queue), 1 min later; brain unchanged |
| 20:09 | pass | c63d18c9 | 550 (0.98) | 1563 (1.10) | 1967 (1.11) | |
| 20:28 | pass | 718f8f64 | 526 (0.94) | 1902 (1.34) | 2078 (1.17) | #1826 same head that failed at 19:58 -> passes |
| 20:47 | pass | 825666e7 | 541 (0.97) | 1662 (1.17) | 2079 (1.17) | |
| 21:06 | pass | 62c6b42b | 580 (1.04) | 1925 (1.36) | 2001 (1.12) | |
| 21:25 | pass | df0617e2 | 591 (1.06) | 1707 (1.20) | 2061 (1.16) | |
| 21:30 | pass | c9184191 | 518 (0.93) | 1744 (1.23) | 2176 (1.22) | |
| 21:39 | pass | 288f819f | 611 (1.09) | 1498 (1.06) | 2047 (1.15) | |
| 21:46 | pass | 6a20c507 | 727 (1.30) | 1679 (1.18) | 2403 (1.35) | row appended while this review ran; STT 1.30x is the highest of the evening (cause not investigated) |

Reading it:

* **No trend across the evening's restarts.** Brain x-baseline: 1.35, 1.63, 1.20,
  1.10, 1.34, 1.17, 1.36, 1.20, 1.23, 1.06, 1.18 — flat noise around ~1.2x, no
  slope. The brain process never restarted, so there is no "post-restart" step; the
  only things that restarted were zoe-data (7 deploys) and Kokoro (11 pauses).
* **The baseline is the best case**, not the median: the day's other passing rows
  (08:00-14:36) have brain 1427-2050 ms. The 1.5x SPEED gate therefore has little
  headroom (1.63x failed once on a 10 % slower decode window); a single slow bucket
  is enough. The re-probe passing 1 min later on a different head, and the SAME head
  (718f8f64) passing at 20:28, show it was transient, not a code regression.
* **EMPTY 2/20 since 14:29 (was 1/20)** is not a regression: the count flaps 0/1/2
  on IDENTICAL commits in the trend (e.g. `269bb680` on 09-28, `8a71800a` -> `2fa1ed9a`
  on 10-03); EMPTY samples are excluded from the scoreable set and per-sample data is
  not kept in the artifact, so the cause (silent clip vs STT miss) is **unverified**.
* e2e is bounded below by the two large-re-prefill turns (~2.5 s each) and the
  ~200 ms prefill floor; neither can be moved without changing prompt-prefix stability
  (`services/zoe-data/AGENTS.md`, "mutate the tail, never the head").
* Earlier today (outside the window): 10:29 `fail` OK-rate 0.263 (14 CANT_DO/ERROR,
  brain 22 ms) falls in the Kokoro `CUDA OOM after 2 retries` episode of the previous
  boot (10:29-11:34) — the runbook section 5 family; the 11:10 probe passes again.
  Not investigated further here.

## 4. Root causes and fixes

| # | Finding | Root cause | Fix in this PR | Applied to the box? |
|---|---|---|---|---|
| 1 | Router lacks `--mlock` / `LimitMEMLOCK` / `MemoryMax` 1280M | template merged, never installed; nothing compares the two | `scripts/maintenance/unit_drift_check.py` (read-only, tested) detects the whole class for 7 units | **operator**: runbook 24(a) |
| 2 | Kokoro `TimeoutStartSec` 120 < 180 s brain wait | same: template (300) never installed | detected by the same tool | **operator**: runbook 24(b) |
| 3 | `status=143` + `Failed with result` on every Flue/Node restart | node traps SIGTERM, exits 143, systemd counts failure | `SuccessExitStatus=143` in `flue-zoe-brain-2x`, `flue-zoe-telegram`, `flue-executor` templates; `tests/unit/test_node_unit_exit_status.py` pins them and fails when a new Flue unit is added unpinned | **operator**: 24(c) (`daemon-reload` only; no restart needed to take effect on the next stop) |
| 4 | Kokoro `Defaulting repo_id` warning x11 | `KPipeline(lang_code=...)` without `repo_id` | pass `repo_id` explicitly (no behaviour change); `tests/unit/test_kokoro_startup.py` | code deploys on the next Kokoro restart |
| 5 | Kokoro down 5 min while only waiting for a deploy | `land_voice_pr.sh` stops Kokoro before `wait_deploy` | not in the repo (`~/.zoe/agent-tools/`); recipe in runbook 24(d) | **operator / tool owner** |
| 6 | 10 probes / 4 merges; empty PR state in the queue summary | the helper already `update-branch`es before probing, but another PR merging during the ~5-10 min probe makes this one BEHIND again and policy (sig #36) forbids moving the head afterwards; `gh pr view` ran from a non-repo cwd | not in the repo; recipe in runbook 24(d) | **operator / tool owner** (cwd already patched live) |

## 5. Remaining / unverified

* **Unverified:** why the 19:57 probe ran ~10 % slower (CI build + deploy overlap is
  the only correlate; no CPU/GPU counters were recorded). Why the router call at
  21:39:23 ran >1.5 s (CPU contention is consistent but unproven; the stale-mlock
  fix is the structural mitigation).
* **Unverified:** the `FLUE_ABORT` path (zero client cancels in the window) and the
  new Telegram voice-note path (flags off, no traffic).
* **zoe-data drift (listed by the tool, not in scope here):** the live unit does not
  load the repo-root `.env` that the template lists as its first `EnvironmentFile`
  (only `services/zoe-data/.env` and `~/.hermes/.env`). Operator should confirm that
  is intentional before anyone "fixes" it; the file contents were not read.
* MTP draft acceptance (0.53 overall) is below the 0.6 `--spec-draft-p-min` the unit
  sets; whether a different `n-max`/`p-min` helps needs a replay-gated A/B in a brain
  window — not attempted on a live box.
* `unit_drift_check.py` is an operator/CI-on-Jetson tool: it reads `~/.config/systemd/user`,
  which does not exist on a hosted runner, so only its comparator is in `ci_safe`.
