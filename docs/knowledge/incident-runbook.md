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
