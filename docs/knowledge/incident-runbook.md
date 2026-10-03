---
type: Reference
title: Production Incident Runbook
description: Verified failure signatures on the live box and their fixes — the zoe-data accept-queue hang (health 000 while systemd says active), root-owned lab-container files silently blocking every deploy at the git pull step, the memory-reconcile fail-open duplicate factory, the voice stack swapped out, the brain's CUDA-OOM crash-loop under unified-memory pressure, MemoryMax-without-MemorySwapMax being no cap at all, and a VAD model swap that loads cleanly but detects no speech, and the B0.8 client pins deployed ahead of the memory store (deploy gate accepted replay evidence bound to another commit), a stale profile cookie short-circuiting the YouTube Music QR sign-in, and the Skybridge fast path answering a statement as a contacts query over the router's chat verdict, and the panel barge-in monitor cutting off Zoe's own replies (pre-playback speech and her own onset), a landing script edited while running that left Kokoro stopped, auto-merge firing before a voice PR's head-bound probe, the overnight landing-chain hazards (no deploy run created, the replay-artifact slot overwritten, reset --hard in a shared worktree, deploy-run lag), a Samantha compare taken while the box was mid-deploy/restart, a stacked PR conflicting after its parent squash-merged, the panel device-token rotation runbook, the morning check-in failing on a datetime in its context (latent until open loops existed), parallel PRs clashing on append-only docs and Alembic numbers, a voice probe hung in getaddrinfo on an mDNS name, and detaching agent-launched harnesses. Diagnose-fast patterns plus the prevention rules.
tags: [incident, runbook, deploy, zoe-data, systemd, docker, permissions, memory, cuda, swap, vad, voice, chromadb, b0.8, voice-gate, ytmusic, sign-in, skybridge, router, barge-in, panel, kokoro, auto-merge, landing, samantha-bar, stacked-pr, device-token, proactive, json, mdns, zoe-base-url, alembic, harness]
timestamp: 2026-09-30T11:00:00+08:00
---

# Production Incident Runbook

Verified failure signatures from the live Jetson box, written so the *next* agent
pattern-matches in seconds instead of re-deriving the diagnosis. Each entry:
signature → diagnosis → fix → prevention. Append new incidents in the same shape.

Related: [Merge & deploy discipline](merge-and-deploy.md) (merged ≠ live),
[Runtime topology](runtime-topology.md) (what runs where).

## 1. zoe-data hung — health 000 while systemd says "active" (2026-07-03)

**Signature.** `curl http://localhost:8000/health` returns `000` (connection
times out), but `systemctl --user status zoe-data.service` shows
`active (running)` with `NRestarts=0`. The decisive check:

```
ss -ltnp | grep ':8000'
# LISTEN 2049 2048  0.0.0.0:8000 ...
```

Recv-Q **2049** against a backlog of **2048** = the **accept queue is full**:
the process still holds the listening socket, but its event loop stopped
accepting connections. systemd sees a live process; every client times out.
(The observed instance had been up 4 days before hanging.)

**Fix.**

```
systemctl --user restart zoe-data.service   # healthy (/health = 200) in ~10s
```

**If it recurs:** capture `py-spy dump --pid <zoe-data pid>` *before*
restarting so the blocking frame is preserved — the root cause of the loop
stall was not identified the first time, and the evidence dies with the restart.

**Do not confuse with a slow cold start** — zoe-data takes >6s to warm up after
a restart; a `000` in the first seconds after deploy/restart is normal (the
deploy health check retries over a ~120s window for exactly this reason).

## 2. Every deploy red in ~14s — root-owned files block `git reset` (2026-07-03)

**Signature.** A consecutive string of failed Deploy runs, each dying in
~14–15s (real deploys take ~30s+). The failed step is **"Pull latest main"**:

```
error: unable to unlink old 'labs/flue-zoe-brain/test/…': Permission denied
fatal: Could not reset index file to revision 'FETCH_HEAD'.
```

**Diagnosis.** A lab/spike container ran as **root** and wrote root-owned
dirs/files inside the live checkout (`/home/zoe/assistant`). The self-hosted
runner runs as `zoe` and cannot unlink them, so `git reset --hard FETCH_HEAD`
fails — and **every merge to `main` silently stops reaching the box** while PRs
happily show MERGED. Confirm with `ls -ld` on the path from the error message
(owner `root:root`).

**Fix.**

```
docker run --rm -v /home/zoe/assistant/<offending-dir>:/fix alpine chown -R 1000:1000 /fix
gh run rerun <latest-failed-deploy-run-id>
```

One rerun suffices: the deploy pulls latest `main`, so it also delivers every
merge stranded by the earlier failures.

**Prevention (the actual rule).**
- Lab/spike containers that bind-mount any path under the repo checkout must run
  with `user: "1000:1000"` (compose) / `--user 1000:1000` (docker run) — or
  write only outside the checkout entirely.
- When something is "merged but not live", check
  `gh run list --workflow=deploy.yml` **first**: a string of red runs means
  `main` is not deploying at all, regardless of PR states. What is actually
  live: `git -C /home/zoe/assistant log --oneline -1`.

## 3. Memory reconciliation fail-open — the silent duplicate factory

**Signature.** Recall starts surfacing near-duplicate facts (the same fact
stored several times, corrections stacking instead of superseding), and
`journalctl --user -u zoe-data | grep reconcile_for_ingest` shows a steady
stream of `storing as ADD without supersession check` lines — escalating to
`ERROR … [SUSTAINED fail-open rate …]` under load.

**Diagnosis.** The shared `memory_quality.reconcile_for_ingest` chokepoint
(every conversational writer routes through it) **fails open to ADD** when its
patient search (15 s budget) times out, returns empty, or errors — a deliberate
tradeoff (**duplicates over lost facts**; Jason's rule). But when search/the
embedder is unhealthy this fires on *every* write and the store silently fills
with duplicates while `/health` stays 200. The cause is labelled:
`search_timeout` (burned ~the whole 15 s budget — embedder busy), `empty_results`
(fast empty — cold / genuinely-empty store), or `search_error` (search raised).

**Watch it.**
- Metric (on `/metrics`): `zoe_memory_reconcile_failopen_count{cause=…}` — a
  Counter incremented on every fail-open ADD.
- Human-queryable: admin `GET /api/system/memory-reconcile/failopen-status`
  → `{count, threshold, window_seconds, sustained}` over a 300 s sliding
  window. `sustained: true` (≥20 fail-opens in 5 min, default) is the alert
  condition; it also escalates the reconcile log WARNING → ERROR.
- **PromQL alert rule:**
  ```promql
  # Reconciliation has become a duplicate factory: >20 fail-open ADDs in 5m.
  - alert: MemoryReconcileFailOpenSustained
    expr: sum(increase(zoe_memory_reconcile_failopen_count[5m])) > 20
    for: 5m
    labels: { severity: warning }
    annotations:
      summary: "reconcile_for_ingest failing open to ADD — memory duplicating"
      description: "Search/embedder likely unhealthy; supersession is being skipped on every write. Check {{ $labels.cause }} split; inspect zoe_memory_search_latency_ms and embedder/db-pool health."
  ```
  Split by cause with `sum by (cause) (increase(zoe_memory_reconcile_failopen_count[5m]))`.

**Fix.** This is a symptom of an unhealthy search/embedder, not of reconcile
itself — do NOT "fix" it by making reconcile drop facts (that loses real data).
Restore search health: a `search_timeout`-dominated burst means the embedder is
starved (check `zoe_memory_search_latency_ms`, memory pressure, a `zoe-data`
accept-queue hang per §1); `search_error` means the store backend is erroring.
Once search recovers the fail-open rate drops to baseline on its own.

**Prevention (the actual rule).** The fail-open-to-ADD decision is intentional
and must stay (duplicates over lost facts). Never regress the observability: any
new fail-open branch in `reconcile_for_ingest` must call
`memory_metrics.record_reconcile_failopen(cause)` so it stays counted and
alertable — a mandatory gate that fails silently is the pattern this entry
exists to prevent.

## 4. Voice slow / "in pieces" — the voice stack got swapped out (2026-07-19)

**Signature.** Replies arrive chopped, the first turn after idle is slow, and a
`/health` curl can exceed a 6s timeout — while every service reports healthy and
nothing in the logs looks wrong. It reads as a product bug; it is a resource one.

**Confirm in one command** (non-zero on either process = the guards are missing
or a unit was restarted without them):

```bash
for p in $(pgrep -f 'llama-server --model'; pgrep -f kokoro_sidecar); do
  awk -v p=$p '/^VmSwap:/{printf "  pid %s swap %.0f MB\n", p, $2/1024}' /proc/$p/status
done
```

Measured before the fix: llama-server **1,457 MB** and kokoro-tts **1,489 MB**
paged out — 3 GB of the voice path on disk. `kokoro-tts` had *no* memory
directives at all, so its cgroup `memory.low` was `0`: zero reclaim protection,
so the kernel evicted it first.

**Fix.** cgroup guards on both units — values, rationale and the apply procedure
in [`scripts/setup/systemd/README.md`](../../scripts/setup/systemd/README.md)
("Memory protection"). Three things that cost time:

- **`--mlock` is not sufficient on Tegra.** `VmLck` held only 1.95 GB of a 5.6 GB
  RSS. `MemorySwapMax=0` is what actually keeps the brain resident.
- **Apply via a drop-in, never `cp` the template** over a live unit — the tracked
  template binds `--host 127.0.0.1` while the live brain binds `0.0.0.0`, so a
  copy silently changes the bind address alongside the memory fix. (By 2026-09-27
  the installed unit's ExecStart matched the template, `127.0.0.1` included. Diff
  before any copy anyway; the B0.4 apply recipe in `voice-pipeline.md` does.)
- **Never add `Nice=-N` / `OOMScoreAdjust=-N` to a `--user` unit.** systemd
  accepts it, the service starts, status is success — and the value is *silently
  dropped* (`ulimit -e` is 0). It documents a guarantee that does not exist.

**Amplifier.** Per-session Serena spawns (~1 GB each, one per MCP client at
*connect* time) can push the box back under pressure; the shared
`serena-mcp.service` is the fix. See `scripts/setup/systemd/README.md`.

**Caution — one failed `/health` poll is not an outage.** Under swap thrash a 6s
timeout returns `000` while the service is fine. Poll three times before
declaring anything down; `systemctl is-active` is not evidence either way.

## 5. Brain CUDA-OOM crash-loop under unified-memory pressure (2026-07-19)

**Signature.** The voice replay gate collapses (e.g. **3/20**) with ERROR
verdicts and `brain_ms` medians around **~100 ms** — that is an *instant
connection-refused*, not slow inference; a genuinely slow brain shows seconds.
Meanwhile tier0 paths (weather/time/calendar) keep passing — **fast-tier OK +
brain fast-fail is the fingerprint**. Confirm:

```bash
curl -m 3 http://127.0.0.1:11434/health          # fails
systemctl --user is-active llama-server          # "activating" — restart loop
```

A manual run of the llama-server command line shows the smoking gun:

```
NvMapMemAllocInternalTagged: error 12
cudaMalloc failed: out of memory
```

**Diagnosis.** Tegra is **unified memory**: CUDA allocations come from the same
physical RAM as everything else. Burst RAM from other workloads — CI validate +
playwright runs, the replay harness's own in-process Moonshine (~1.5 GB
transient), deploy warmups — starves the *running* brain's next CUDA
allocation. The cgroup guards (`MemoryLow`, `MemorySwapMax=0`) protect **CPU
pages only** — they do nothing for NvMap/CUDA allocations, so a "protected"
brain still OOMs on the GPU side. Once it dies, `Restart=` loops it in
`activating` until enough RAM drains to reload (**~6.3 GB availMB needed**);
it self-heals when the pressure source finishes.

**Fix.** Usually none needed — stop/finish the competing workload and the
restart loop succeeds on its own. Verify recovery with
`curl -m 3 http://127.0.0.1:11434/health` then a replay-gate re-run.

**Prevention (the actual rule).**
- **Never run the replay gate — or any ~1.5 GB-transient job — with
  < 2 GB availMB, or concurrently with CI runs or deploys.**
- The probe's 1500 MB free-RAM guard protects **the probe**, not the brain: the
  probe can pass its own guard and still be the allocation that kills the
  brain's next `cudaMalloc`.
- When triaging a "brain down" replay collapse, check `brain_ms` first: ~100 ms
  medians mean connection-refused (this incident), not a model problem.

## 6. systemd `MemoryMax` without `MemorySwapMax=0` is not a cap (2026-07-20)

**Signature.** A service with `MemoryHigh=1G` / `MemoryMax=2G` (the shared
`serena-mcp.service`) sitting at **1.0 G RSS + 2.1 G swap ≈ 3.1 G real
footprint**. The "cap" held RSS exactly at MemoryHigh — by pushing everything
else to swap.

**Diagnosis.** `MemoryHigh` pressure causes reclaim, and reclaim's outlet is
swap, which is **unbounded** unless `MemorySwapMax` is set. So
`MemoryHigh`/`MemoryMax` alone converts a RAM hog into a swap hog of arbitrary
size — worse on this box, where swap thrash is the voice-latency killer (§4).

**Fix.** Add a `MemorySwapMax=0` drop-in (drop-in, never template-copy — §4).
A real breach then OOM-kills the service and `Restart=always` brings it back —
acceptable for rebuildable-cache services like Serena. The voice-stack units
already carry `MemorySwapMax=0` (#1409); this extends the same rule to every
capped unit.

**Amplifier — stale per-worktree stdio Serena configs.** ~91 pre-#1400
worktrees carried stale stdio `serena` entries in their `.mcp.json`, each
spawning a **~1 GB per-session Serena at MCP connect time** (bypassing the
shared server entirely). Swept 2026-07-20 — but **recheck `.mcp.json` when
resurrecting an old worktree**; a stale config silently re-creates the fleet.

**Prevention (the actual rule).** A memory cap on this box is
`MemoryHigh` + `MemoryMax` + **`MemorySwapMax=0`** — all three, via drop-in.
Two out of three is not a cap.

## 7. Scheduled job runs on time and does NOTHING — the zero-effect blind spot (2026-07-22)

**Signature.** Everything is green. The loop logs `nightly run complete` at
03:00 on the dot, `/health` is 200, `stale: false` on the memory-loops status
endpoint — and no memory has been written for weeks. This has now happened
**twice** to the same job: a timezone cast that made the lookback window empty
(#1217) and ten consecutive nights processing zero users (#1480).

**Diagnosis.** The observability added after the first outage (#1226) records
*that* the loop ran and raises `stale` when a run is MISSED. A run that happens
punctually and produces nothing is, to that check, indistinguishable from a
healthy one — the empty successes were faithfully recorded and never alerted
on. **A heartbeat that carries only liveness is not a heartbeat.**

**Watch it.**
- Every recorded run now carries `effect_count` — the summed effects the run
  actually produced (digest: `extracted + new + superseded + skipped_duplicates`;
  consolidation: `merged + resolved_contradictions + archived`). Zero means the
  run did no work at all.
- Consecutive zero-effect runs accumulate into `zero_effect_streak`; reaching
  `zero_effect_alert_after` (default 5, `ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS`;
  `< 1` disables) sets `zero_effect_alert: true` and escalates the run-complete
  line in `~/.zoe/zoe-data-memory-loops.log` to a `ZERO-EFFECT ALERT` WARNING.
- Human-queryable: admin `GET /api/system/memory-loops/status` →
  `{loops, healthy, alerts}`. `alerts` is prose, e.g. `digest: ran 10 times in
  a row and did nothing each time (threshold 5)`.
- **PromQL alert rule:**
  ```promql
  # A memory-maintenance loop is running on schedule but doing nothing.
  - alert: MemoryLoopZeroEffect
    expr: zoe_memory_loop_zero_effect_alert == 1
    for: 10m
    labels: { severity: warning }
    annotations:
      summary: "{{ $labels.loop }} loop runs but produces no effects"
      description: "zoe_memory_loop_zero_effect_streak{loop=\"{{ $labels.loop }}\"} consecutive runs with effect_count 0. Staleness will NOT catch this — the runs are on time. Check the loop's query window (timezone/lookback) and its active-user selection."
  ```

**Fix.** Zero effect is a *symptom*: read the loop's own selection query first
(both digest outages were in the "which users / which window" step, not in the
extraction). Confirm with a manual run and inspect `users` alongside
`effect_count` — `users: 0` means the selection is empty, `users: N` with
`effect_count: 0` means the window or the extractor is.

**Prevention (the actual rule).** Any scheduled job's heartbeat must record
**what the run did, not just that it ran**, and N consecutive no-op runs must
raise a visible unhealthy state. A job that is *legitimately* idle sometimes
declares it (`memory_metrics._IDLE_TOLERANT_LOOPS`) rather than having its
alert threshold quietly raised for everyone — the declaration is reviewable,
a tuned-away threshold is not. Pinned by
`services/zoe-data/tests/test_memory_loop_observability.py`.

## 8. Barge-in / idle listening silently off — a VAD model that loads but hears nothing (2026-09-26)

**Signature.** Barge-in and idle (no-wake) listening on the panel stop triggering; nothing errors,
`/health` is green, the replay gate is green. `voice_vad` logged `Silero VAD loaded from …` (no
RMS-fallback warning). Scoring real corpus clips gives peak speech probabilities of ~0.001–0.03.

**Diagnosis.** `/home/zoe/models/silero_vad.onnx` had been replaced with the Silero v6.2.1 export.
It has the same I/O names and shapes, so `onnxruntime` loads it and `voice_vad.SileroVAD` runs it —
but on the service's 512-sample streaming path it scores 0/12 corpus clips ≥ 0.5 (v6.0: 12/12).
`create_vad()` only falls back to RMS when the model FAILS to load, so a model that loads and says
"no speech" is a silent failure. **Root cause (found 2026-09-27): the loader, not the file** —
`voice_vad.py` fed bare 512-sample hops, while upstream's `OnnxWrapper` prepends the previous 64
samples (576-sample input) and v6.2 cannot work without that context (v6.0 degrades). Fixed in
`voice_vad.py` + pinned by `tests/test_voice_vad_context.py`; with it v6.2.1 detects speech
(20/24 in the VAD stage). The same signature can still come from a genuinely bad file or a loader
regression, so the checks below stand. Check the file first:

```bash
md5sum /home/zoe/models/silero_vad.onnx      # compatible v6.0 = 00bdd41445da13fe3d52a5a074013aa1
jq .vad ~/.cache/zoe/voice_regression_last.json   # the probe's VAD stage (see Prevention)
```

**Fix.** Restore the v6.0 file (`/home/zoe/models/silero_vad.onnx.v6.0.bak-20260926`), then confirm
with the real-model test (small, ~100 MB — no flock needed):
`cd services/zoe-data && nice -n 15 python3 -m pytest -q tests/test_voice_barge_in.py -k silero_real_model`.
zoe-data loads the session lazily and keeps it, so a restart is needed for the running service to
pick the restored file up. Restored 2026-09-27; the v6.2.1 file is kept as
`silero_vad.onnx.v6.2.1-INCOMPATIBLE-20260927` (historical name) as the A/B candidate — v6.0 stays
live until the false-trigger A/B in voice-pipeline.md → *The VAD stage*.

**Prevention.** `voice_regression_probe.py` now has a **VAD stage** (default on): it runs the
service's real `voice_vad` over the newest 24 usable corpus clips and FAILS the run below 60 %
speech detection — also on the memory-skip path — recording `vad: {clips, speech_detected,
min_max_prob, model: {path, md5}, status}` in the artifact; `voice_gate_check.py` blocks on a failed
or missing block. `voice_vad.py`, `voice_turn.py` and `*silero*` are in `VOICE_PATH_PATTERNS`. The
model file lives outside git, so the **nightly** probe run is what catches a hand swap — before
swapping, test the candidate with `ZOE_SILERO_VAD_MODEL=<candidate>`. Detail:
[voice-pipeline.md](voice-pipeline.md) → *The VAD stage*.

## 9. B0.8 pins deployed ahead of the store (2026-09-28)

**Signature.** Right after a merge that changes memory-client pins, `/readyz` reports
`memory_capture: degraded` and the zoe-data log shows the palace **format guard** refusing to open
the store (`memory_service._check_palace_format`: a 1.x client against a 0.6 sysdb). Voice and
chat keep working; memory capture/recall is down. The Deploy run for the merge is **green**, and
its voice-gate line reads `OK — voice replay-gate PASS (2.6h old …)` although nobody ran a
replay for that commit.

**Timeline (AWST).**
- 08:13:45 — cutover PR #1745 merged as `d346aa90`. The runbook
  ([chroma-1-5-migration.md](chroma-1-5-migration.md) §5) assumed its deploy would be REFUSED by
  the voice gate (`requirements-py312.txt` is voice-path) and leave the live tree untouched.
- 08:13:56 — Deploy run `36361425584`: `voice-gate: OK — voice replay-gate PASS (2.6h old, n=20,
  VAD 19/24)`. The artifact was a fresh, passing probe run for a different commit.
- 08:14 — the deploy moved the venv to `chromadb==1.5.9` + `mempalace==3.10.0`, reset the live
  code to `d346aa90` and restarted zoe-data, while `~/.mempalace` was still the 0.6 store. The
  format guard refused to open it → `memory_capture: degraded`.
- 08:18–08:21 — the operator session completed the swap by hand (recovery below).
- 08:21 — `/readyz` ready, `self-recall ok`. Memory was degraded ~7 min.

**Root cause.** The deploy gate (`voice_gate_check.py`, called from `deploy.yml`) accepted any
replay artifact that was **fresh and passing**. Nothing bound the evidence to the commit being
deployed, so a probe run for another PR earlier that morning cleared the cutover merge.
(`voice-gate.yml`'s PR-time check already passes `--expect-revision`; the deploy path did not.)

**Why no stored data was lost.** The read-only format guard reads the sysdb format from SQLite
(`mode=ro`) before chromadb touches the file, so the 1.x client never opened, and never
migrated in place, the 0.6 store. The old store was unmodified from 07:30 (its last write) until
the copy at 08:18.

**New memories are a separate question.** While the guard refused, a turn's memory write would
have failed with no durable retry, so captures in that window can be lost even though the store
is intact. On 2026-09-28 the app log (`~/.zoe-logs/zoe-data.app.log`, 08:14–08:21) shows 11
guard refusals, all from the startup and retry self-recall probes (08:14:05, 08:14:50, 08:19:50,
08:20:18), and no chat or voice turn lines; the probe passed at 08:20:40. So nothing needs
reprocessing this time. After any recurrence, grep that window for turns and replay their
captures by hand.

**Recovery (what was run).**
1. Stop the timers and zoe-data; confirm nothing holds `~/.mempalace/chroma.sqlite3`.
2. `chroma_migrate_rehearsal.py run --date cutover-2026-09-28-081803 --old-python /usr/bin/python3`.
   The live venv no longer carried 0.6.x, so the system 3.10 interpreter (which keeps
   chromadb 0.6.3) served as the old client. It opens only the copy and its scratch copies.
   Result: PROOF TABLE **10/10**, peak RSS 379 MB, 102 s.
3. Check the old store was not modified since the copy: `~/.mempalace/chroma.sqlite3` mtime
   07:30 < copy 08:18.
4. Swap: `~/.mempalace` → `~/.mempalace.pre-b08-20260928-082034` (the rollback), the migrated
   `dst/` → `~/.mempalace`. Restart zoe-data, poll `/readyz` for `self-recall ok`, re-arm the
   timers.
5. Post-cutover replay PASS 13/13 (brain 1414 ms, VAD 0.792). The tombstone report had one
   collection UNKNOWN (expected on a fresh 1.x index; non-fatal). zoe-data RSS 1.33 GB.

**Lessons (the rules).**
- **Merge a pins PR only inside the window that applies it.** A merge is a deploy unless the gate
  provably refuses it, and "the gate will refuse" is an assumption that failed here. Stop the
  services first, then merge.
- **The deploy gate must bind evidence to the deployed tree**, not only freshness + `pass`. The
  fix is in flight in a separate PR, "voice gate binds the replay artifact to the deployed tree".
  Until it lands, a fresh passing artifact from any other commit clears any voice-path deploy.
- **The rehearsal manifest's `proofs` is a LIST** of proof rows, not a dict. Read the PROOF TABLE
  the run prints (or iterate the list); do not index it by proof name.
- **`src/chroma.sqlite3` is an SQLite online-backup snapshot and is never byte-identical** to the
  live file. To show the live store was untouched since the copy, compare **mtimes** (live store
  < copy), not checksums.
- **`run` needs `--old-python` once the live venv is on 1.x.** Its default old interpreter is the
  live venv, and it refuses (`--old-python must carry chromadb 0.6.x`) after the deploy has
  converged the venv. `/usr/bin/python3` (3.10, chromadb 0.6.3) is the fallback.

**Live recall parity: PASS.** The first post-cutover `compare-recall` printed
`no complete parity baseline` because it was given the wrong path: `--baseline <run dir>`
instead of the run's `recall-parity` pointer. It was not a retention bug. The baseline was
retained and complete: `cutover-2026-09-28-081803/recall-parity` →
`recall-parity.20260928T001943894457-1679199`, with manifest
`run.recall_parity_baseline_retained: true`. Re-run against that pointer on a copy of the live
store (demo user `demo_b08_b9233229`), it passed: identical order 20/20, top-1 equal 20,
min Jaccard 1.0. **Lesson:** `--baseline` takes `$R/recall-parity`, not `$R`; block B in
[chroma-1-5-migration.md](chroma-1-5-migration.md) §5 already passes the right path. A
`--demo-user` default read from the manifest's `run.recall_demo_user` would be a nice-to-have,
not a fix.

## 10. Stale cookie short-circuits the YouTube Music QR sign-in (2026-09-28)

**Signature.** On the panel → phone YouTube Music sign-in, the phone's view link "didn't work":
13–17 s after `ytmusic sign-in: starting Xvfb`, zoe-data logged `ytmusic sign-in: harvested
cookie <… chars> — saving provider (user=…)` and tore the rig down — before the person had
opened the noVNC view. Three attempts 18:53–18:55 (box local time), same result each time. Music Assistant
then listed no `ytmusic` provider at all (15 providers, none of them YouTube Music).

**Diagnosis.** `ytmusic_signin._run_watcher` treated "the browser holds `__Secure-3PAPISID`" as
"the person signed in". The sign-in browser runs on the PERSISTENT profile
(`$ZOE_YTMUSIC_SECRET_DIR/profile`), which still held the cookie that had rotated/expired in the
2026-09-25 outage ([music-ytdlp-js-runtime.md](music-ytdlp-js-runtime.md)), so the very first
poll after the page loaded "found" a login, and the dead cookie was saved to MA again. The class:
**cookie present is not login happened.**

**Fix.** The watcher snapshots a digest of the profile's `__Secure-3PAPISID`+`SID` at session
start and validates it against YouTube (`_validate_cookie`: the youtubei `account_menu` POST with
MA's SAPISIDHASH header, read through YouTube's own `logged_in`/`yt_li` flags). Signed out →
the Google/YouTube cookies are cleared (only those), the view reloads to a real sign-in form,
and the state is `stale_cookie_cleared` (the phone and the panel's handoff card say "sign in
again"). From then on only a cookie that CHANGED during the session and validates True is
saved; a failing one is not saved and does not tear the view down. `refresh_now()` refuses to
push a signed-out cookie too. CloakBrowser's per-launch pypi/github update check (seen at
18:53:38) is off. Pinned by `services/zoe-data/tests/test_ytmusic_signin.py`, including a
negative control: removing the snapshot comparison turns the unchanged-cookie test red.

**Diagnose fast.** In `~/.zoe-logs/` (zoe-data logs there, not journald): a `harvested cookie`
line within seconds of `starting Xvfb` means the flow never waited for a human. After the fix,
expect `profile cookie is stale (validation failed) — clearing…` on the first attempt after a
rotation, then `harvested cookie` only after the person signs in.

**Prevention.** Any flow that reads credentials from a persistent browser profile must prove the
credential is NEW (changed since the flow started) and LIVE (the provider says so) before it
saves it. A presence check alone will pass on whatever the profile left behind.

## 11. Fast path over-claim — a statement answered as a contacts query (2026-09-28)

**Signature.** On the panel Zoe answers a personal statement with a canned card reply
("I found 0 contacts.", an empty calendar, a weather card). The app log shows
`SKYBRIDGE TIMING … reply='I found 0 contacts.'` for the turn, while the router line just before it
says `chat` — here `router_two_stage {"actual_routed": "chat", "head_conf": 0.9185,
"gated": false, "shortlist": ["people","memory","journal"]}` at 18:26:27.

**Diagnosis.** The Skybridge fast path runs before the brain and did not look at the router. Its
people branch claimed any utterance containing `" family"`/`" friends"`/`" person"`/`" contact"`
(`" person"` even matched "personal"), so "I go down and I spend the weekends with him and just get
updates from the family." became a directory query. Same class as #1150 (the fast path claiming
"add a journal entry"). Running the 517 distinct panel transcripts in the app logs through the old
classifier found 9 more people claims, none of them a directory ask — among them "Hey Zoe. Can you
remove all memories of a person named Sarah?", "But it's the person who's authenticated on the
panel." and "Hey zoe Show me my dashboard". Test phrasings fared the same: "can you find me a good
pizza recipe" and "show me the news" were contact searches.

**Fix.** (1) The people matchers need a command/question shape — imperative/interrogative at the
start, noun as the verb's object. (2) The router veto: when the active two-stage router says
`chat` (or an incompatible domain), Skybridge declines before any auth challenge or side effect
and the turn goes on to the brain. Flag `ZOE_SKYBRIDGE_ROUTER_VETO` (default on).

**Check it.** `grep SKYBRIDGE_GATE ~/.zoe-logs/zoe-data.app.log | tail` — every gated voice turn
Skybridge classified logs `decision=allow|veto reason=…`. A run of `reason=router_unavailable` means
the router is off or not in `active` head mode, so the veto is not protecting anything.

**Prevention.** A new fast-path phrasing goes into a shape regex, never another
`any(term in text …)`. The replay gate cannot see this class (it never calls Skybridge — see
[voice-pipeline.md](voice-pipeline.md) → *The Skybridge fast path defers to the router*), so the
guard is `services/zoe-data/tests/test_skybridge_router_veto.py` with its negative controls.

## 12. Zoe interrupts herself — barge-in on her own onset (2026-09-28)

**Signature.** Replies are cut off within the first second, and the room is quiet. The Pi
`voice.log` shows `Barge-in detected during playback (monitor, prob=0.9x)` with no user speech.
The following follow-up window then finds nothing (`max_prob=0.008`). On 2026-09-28 at 20:44–20:45
this happened on three turns in a row. Using `t0 + TTFA` (the `turn_stream TTFA=` line is logged
after the drain, see [voice-pipeline.md](voice-pipeline.md) → *Panel barge-in*), the fires came
0.43 s, 0.50 s and 0.81 s after the first audio write. An 18:24 turn fired at **t+11 ms**, while
the user was still talking at the end of an 8 s capped recording.

**Diagnosis.** `_BargeMonitor` opens at turn start and keeps a rolling 2-of-5 window through
the STT/brain wait. It had two faults. (a) The window was never tied to playback, so speech
from just before the first write counted as an interruption. That was the t+11 ms fire.
(b) Nothing ignored Zoe's own onset. Playback and capture are both on the Jabra (the Pulse
default sink and source), so the hardware echo canceller does apply. The 20:44 fires landed
0.3–0.7 s after the first sound, in a quiet room, at prob 0.90–0.99, and two chunks were
enough to fire. That is the signature of Zoe's own voice reaching the mic before the echo
canceller settles. It is inferred from the timing: the monitor keeps no audio, so it was not
recorded. The
first guess was a stale **queue** backlog, and it was wrong for turns: the monitor reads its
own stream, and `_BARGE_QUEUE` receives nothing during a turn because the wake stream is
closed. The queue path, which serves announcements, had the same class with a single-chunk
trigger.

**Fix.** A single `_BargeDetector` now serves both paths. It is anchored to playback start,
drops stale audio, ignores a `BARGE_GRACE_MS` (800 ms) onset grace, and needs 3-of-6
sustained speech or 2 consecutive chunks at ≥ 0.95. The fire line now says
`t+<ms>` and `window=…`.

**Check it.** `grep -a "Barge-in detected" ~/.zoe-voice/voice.log | tail` on zoe-pi. A fire
below t+800ms should not happen. A run of fires at t+800–1100ms with a quiet room means the
echo residual outlasts the grace, so raise `BARGE_GRACE_MS` in `.env.voice` and restart
`zoe-voice`. A deliberate interruption should show `t+` about 1.1–1.3 s after you start talking.

**Prevention.** `tests/unit/test_voice_daemon_barge_in.py` (stale backlog, the user still talking
at playback start, onset inside the grace, a real interruption 1 s in, env overrides) has negative
controls for each guard.

## 13. Editing a running landing script left Kokoro down for 5 minutes (2026-09-28)

**Signature.** Kokoro is stopped and nothing restarts it. `journalctl --user -u kokoro-tts`
shows `Stopping Kokoro TTS Sidecar` at **19:37:20** and the next `Started` only at
**19:42:46** — a 5.4 min gap, where every other pause that day was 50–110 s (a probe window).
Voice replies in the gap come from the fallback provider, not the CUDA sidecar; `/readyz`
`dependencies.tts` no longer names `kokoro-sidecar`. Nothing alarms: `zoe-data` stays healthy
and `tts.ok` stays true on the fallback.

**Diagnosis.** The landing helper (`land_voice_pr.sh`, an operator scratch script) pauses
Kokoro around the head-bound replay probe: `systemctl --user stop kokoro-tts` → probe →
`systemctl --user start kokoro-tts`. The file was **edited while an instance of it was still
running**. bash reads a script incrementally by byte offset, so an edit under a running
instance makes it resume at a shifted offset: the running copy never reached its own `start`
line and exited. The stop had happened; the start never did. Kokoro was started again at
19:42:46.

**Fix.** The helper installs the restore before the stop —
`trap 'systemctl --user start kokoro-tts.service' EXIT` (the `kokoro-restore-trap` line) — so
a crash, a `set -u` abort or a truncated script body still starts Kokoro. Its last step polls
`:10201/health` for `pipeline_loaded: true` and prints it, so a run whose final line is not
`kokoro back: … "device":"cuda"` is a run to inspect.

**Prevention.**
- Never edit a script that is running. Copy it to a new name and start the next run from the
  copy, or wrap the body in a function called on the last line (`main "$@"`), which makes bash
  parse the whole file before executing any of it.
- Any window that stops Kokoro (or the brain) sets the restore `trap` **before** the stop, not
  after — the same rule as the brain window in [voice-pipeline.md](voice-pipeline.md) →
  *Stopping the brain does NOT guarantee it restarts*.
- Check: `journalctl --user -u kokoro-tts --since today | grep -E 'Stopping|Started'` — a
  stop without a start within ~90 s is this class.

## 14. Auto-merge armed before the probe — a voice PR merged on an unprobed head (2026-09-28)

**Signature.** A voice-path PR merges while its head-bound replay is still running. On #1757
the review-fix push `e4a827c1` landed at 12:35:20Z, the PR **merged at 12:42:07Z**, and the
probe for that head finished at **12:42:55Z** (`~/.cache/zoe/voice_regression_trend.jsonl`,
commit `e4a827c1`) — 48 s after the merge. The deploy at 12:47 then found the artifact
(`voice-gate: OK — … tree-identical to fe83ea3e (tree b7f31e22; artifact from e4a827c1)`), so
the deploy gate was satisfied after the fact. Nothing was refused and the merged code was
probed — but only by ordering luck; a failing probe would have found the code already on
`main`.

**Diagnosis.** `gh pr merge --squash --auto` had been armed on an earlier head. The fix push
re-ran `validate` and `secret-scan`; auto-merge fires the moment the **required** set is
green, and `voice-gate` is informational by design (root `AGENTS.md` → *voice-gate —
INFORMATIONAL*), so it held nothing. Same property as #1587 in
[merge-and-deploy.md](merge-and-deploy.md): a context that has not reported does not hold
auto-merge. The PR-time gate cannot close this; the deploy gate is the real block, and it only
refuses when no matching artifact exists at deploy time.

**Fix.** The landing helper **disarms first** (`gh pr merge <n> --disable-auto` at the top of
every run) and re-arms `--squash --auto` only after the head-bound probe has passed and the
`voice-gate` run for that exact head has concluded `success`. A push to a voice PR therefore
always means: disarm → probe the new head → gate green → arm.

**Prevention.**
- Never leave auto-merge armed across a push on a voice-path PR. Arming is the LAST step of a
  landing, never the first.
- Before arming, confirm the artifact is bound to the current head:
  `python3 -c "import json;print(json.load(open('/home/zoe/.cache/zoe/voice_regression_last.json'))['revision']['commit'][:8])"`
  must equal `gh pr view <n> --json headRefOid --jq '.headRefOid[:8]'`.
- If a voice PR did merge unprobed, probe a checkout of the MERGED sha before its deploy (or
  `gh run rerun` after) — merge-and-deploy.md → *Landing a voice-path PR*.

## 15. Landing-chain hazards seen overnight (2026-09-28 → 09-29)

Four smaller ones from the same night, each with its guard now in the landing helpers. None
lost data on the box; one lost an agent's uncommitted edits.

- **(a) GitHub created NO workflow run for a merge to `main`.** #1762 merged as `d46be79a`
  at 22:30 and `gh run list --workflow deploy.yml` never gained a row for it (the deploy list
  jumps from `53a6c209` 21:21 to `a1570765` 23:16); at the time the commit had 0 check-runs.
  The deploy gate's own printed recipe (`deploy_with_probe.sh`: probe a detached worktree at
  the merged sha with the live `.env` copied in, then `deploy_live.sh`) was run by hand; its
  first three probes recorded `status=error` (`measure_voice failed before aggregation
  (rc=1)` — the worktree's probe script under the venv interpreter; the live tree's script
  with `/usr/bin/python3` and `--service-dir` is the form that works). `#1762`'s code reached
  the box with the **#1763** deploy at 23:16 (reflog). A 2-minute watchdog
  (`deploy_watchdog.sh`) now runs a local deploy when `origin/main` is ahead of the live
  checkout with no deploy run after 6 min, outside the brain window and the 04:18–04:52
  nightly-probe window. Consequence for evidence: the "post-#1762" Samantha compare at 22:38
  ran against `53a6c209`, i.e. **before** #1762 was live — it says nothing about #1762.
- **(b) The single replay-artifact slot was overwritten (22:34).** A stuck landing's probe
  for another PR wrote `voice_regression_last.json` after #1762's head-bound probe (14:29Z,
  `ac9fbd82`) and before its deploy; the deploy gate then had no artifact for that tree and
  refused until a re-probe. Rule (unchanged, now enforced by the helpers' brain-window
  `flock`): voice landings are strictly SERIAL, and the slot belongs to the PR being deployed
  until its deploy has run.
- **(c) `reset --hard` in a PR worktree wiped an agent's uncommitted edits (01:4x).** The
  helper used to reset the agent's own worktree to `origin/<branch>` before probing. Landings
  now use a private detached checkout per PR (`~/.worktrees/land-<pr>`) and push with an
  explicit refspec; the agent's worktree is never touched.
- **(d) The deploy run for a merge can be created ~4 min after the merge.** A probe started
  in that gap (02:01) was killed by the deploy's zoe-data restart and recorded a spurious
  `fail` (OK rate 0.5, 10 CANT_DO/ERROR — commit `1cdf275d`, land-1769). The helper now waits
  for a **completed** deploy of `origin/main` HEAD (or 8 min of quiet) before it probes.
  Corollary for readers of the trend file: a `fail` row during a deploy restart is not a
  regression; the re-probe 20 min later passed 20/20.

## 16. A Samantha compare run while the box was changing under it (2026-09-29)

**Signature.** A `samantha_bar.py --compare-baseline` row shows `ERROR` (or a one-off `FAIL`)
right after a merge. On 09-29 the post-#1781 compare (10:21–10:25 AWST) recorded S8 `ERROR`
while zoe-data restarted mid-run (journal: 10:24:25 and 10:25:13).

**Diagnosis.** `git rev-parse HEAD == <merge sha>` on the live checkout is not proof the code
is live: `deploy.yml` resets the tree FIRST and restarts services after, which can be minutes
later (memory waits, sidecar rebuilds). A compare in that gap, or across any restart, tests a
half-deployed box.

**Rule.** Start a compare only after the merge's own deploy RUN has `completed`
(`gh run list --workflow deploy.yml --json headSha,status,conclusion`) and `/health` answers;
hold while any deploy run is `in_progress` or a restart is pending. A row taken across a
restart is not evidence — re-run it.

## 17. Stacked PR conflicts on every parent file after the parent squash-merges (2026-09-29)

**Signature.** A PR opened on another PR's branch (#1784 on #1783) goes `CONFLICTING` on every
file the parent touched as soon as the parent squash-merges.

**Diagnosis.** The squash commit on `main` has a different sha from the parent branch's commits,
so git sees both sides editing the same lines. Force-push is blocked by policy, so the branch
cannot be rewritten in place.

**Fix.** In a private landing checkout: `git rebase --onto origin/main <parent-branch-tip>
<child-branch>`, push it as a NEW branch, open a replacement PR (#1785) and close the old one
with a "superseded by" comment. Prevention: do not stack; branch from `main` after the parent lands.
Seen again 2026-09-30: #1788 → #1791 (same recipe).

Two neighbours from the same day:
- **Parallel PRs conflict on append-only docs** (`docs/PLANS.md` log lines, `AGENTS.md` bullets)
  because each adds a line at the same spot. The operator's local landing helper (not in the
  repo) now union-merges `docs/PLANS.md` (keeps both sides, drops the markers); other files
  still abort the landing for a manual merge.
- **Alembic revision numbers clash between parallel PRs.** #1791 and #1792 both claimed 0033;
  renumber the later PR after the earlier one merges (#1792 → 0034) and re-check `down_revision`.

## 18. Runbook — rotating the panel device token (2026-09-29)

1. Log in to zoe-auth as an admin (the username is case-sensitive) and keep the session id.
2. Mint: `POST /api/panels/{panel}/token` with header `X-Session-ID: <admin session>`; note the
   new token and its id. Never write a placeholder into any env file.
3. Install it in BOTH places: on the Pi, `/home/pi/.zoe-voice/.env.voice` `DEVICE_TOKEN` (the
   daemon); on the Zoe box, `/home/zoe/.hermes/.env` `ZOE_DEVICE_TOKEN` (the replay/latency probe).
4. `systemctl --user restart zoe-voice`; the journal must show `Speaker profiles synced` and no
   `API auth failure … (401)` line.
5. Only THEN revoke the old id: `DELETE /api/panels/{panel}/token/{old_token_id}` (admin).
6. Recovery: the daemon's `.env.voice.bak-*` backup restores the last working env.
Never print a token into a transcript or log; check by length or by the sync line.

## 19. Morning check-in notification failed — latent until the data existed (2026-09-30)

`fire_notification failed … (type=morning_checkin): Object of type datetime is not JSON serializable`: `open_loops.follow_up_after` (TIMESTAMP) rode into the trigger context as a `datetime` (#1781) and `create_pending` stored it with a bare `json.dumps`; green for days only because `open_loops` stayed empty until #1782. Fixed at both ends — the gatherer emits a UTC ISO string, and `session_utils.dumps_context` serialises datetime/Decimal/UUID. Prevention: a test of a DB-fed path must feed a row of every column type, not an empty table.

## 20. Voice probe hangs for minutes in `getaddrinfo` — `ZOE_BASE_URL=http://zoe.local` (2026-09-30)

**Signature.** `voice_regression_probe.py` (or `measure_voice.py`) makes no progress for minutes;
a stack dump shows the replay stuck in `socket.getaddrinfo`.

**Diagnosis.** The replay harness (`tests/replay_samples.py` `_load_env`, setdefault) merges
`services/zoe-data/.env` into its environment, so it inherits `ZOE_BASE_URL=http://zoe.local`.
On this LAN another device answers as `zoe.local` and the box is `zoe-2.local` on mDNS
(operator diagnosis), so the IPv4 lookup through NSS `mdns4_minimal` times out
(`getent ahostsv4 zoe.local` gave no answer within 5 s on a re-check). Find it with `python3 -X faulthandler` under
`timeout -s ABRT <n>`, which prints every thread's stack when it fires.

**Fixed in #1798** (after the nightly unit timed out at 05:00 on 10-02 and 10-03 — the
probe's own skip diagnosis also called `getaddrinfo('zoe.local')`). The replay never reads
`ZOE_BASE_URL`: `--stt remote` targets `--base-url`, else `ZOE_REPLAY_BASE_URL`, else
`http://127.0.0.1:8000`; a hostname base is resolved under a 5 s bound and fails with a
one-line reason. The probe passes the base explicitly, and its diagnosis port-probes numeric
hosts only and reports `ZOE_BASE_URL` without resolving it. Pinned by
`tests/unit/test_replay_base_url.py`. 🧑 Operator, still: set `ZOE_BASE_URL=https://192.168.1.218`
in the live `.env` (the panel pairing QR `pair_url` is built from it). Cost on 09-30: the stuck
landing left Kokoro stopped 07:40–08:03.

## 21. A harness launched from an agent tool must be detached from the tool (2026-09-30)

Run long harnesses (probe, bar, A/B) from an agent's shell with stdin closed, unbuffered
Python, and stdout to a file: `python3 -u … </dev/null >~/.cache/zoe/<name>.log 2>&1`. Without
`</dev/null` a child can block on the tool's stdin; without `-u` and a file, progress stays in a
pipe buffer and a hang looks like silence. Read the log file to follow it.

