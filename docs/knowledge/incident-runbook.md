---
type: Reference
title: Production Incident Runbook
description: Verified failure signatures on the live box and their fixes — the zoe-data accept-queue hang (health 000 while systemd says active), root-owned lab-container files silently blocking every deploy at the git pull step, the memory-reconcile fail-open duplicate factory, the voice stack swapped out, the brain's CUDA-OOM crash-loop under unified-memory pressure, MemoryMax-without-MemorySwapMax being no cap at all, and a VAD model swap that loads cleanly but detects no speech, the B0.8 client pins deployed ahead of the memory store (deploy gate accepted replay evidence bound to another commit), and the Skybridge fast path answering a statement as a contacts query over the router's chat verdict. Diagnose-fast patterns plus the prevention rules.
tags: [incident, runbook, deploy, zoe-data, systemd, docker, permissions, memory, cuda, swap, vad, voice, chromadb, b0.8, voice-gate, skybridge, router]
timestamp: 2026-09-28T00:00:00Z
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

## 10. Fast path over-claim — a statement answered as a contacts query (2026-09-28)

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
