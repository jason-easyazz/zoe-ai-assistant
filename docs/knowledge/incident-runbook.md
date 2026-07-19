---
type: Reference
title: Production Incident Runbook
description: Verified failure signatures on the live box and their fixes — the zoe-data accept-queue hang (health 000 while systemd says active) and root-owned lab-container files silently blocking every deploy at the git pull step. Diagnose-fast patterns plus the prevention rules.
tags: [incident, runbook, deploy, zoe-data, systemd, docker, permissions]
timestamp: 2026-07-03T00:00:00Z
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
  copy silently changes the bind address alongside the memory fix.
- **Never add `Nice=-N` / `OOMScoreAdjust=-N` to a `--user` unit.** systemd
  accepts it, the service starts, status is success — and the value is *silently
  dropped* (`ulimit -e` is 0). It documents a guarantee that does not exist.

**Amplifier.** Per-session Serena spawns (~1 GB each, one per MCP client at
*connect* time) can push the box back under pressure; the shared
`serena-mcp.service` is the fix. See `scripts/setup/systemd/README.md`.

**Caution — one failed `/health` poll is not an outage.** Under swap thrash a 6s
timeout returns `000` while the service is fine. Poll three times before
declaring anything down; `systemctl is-active` is not evidence either way.
