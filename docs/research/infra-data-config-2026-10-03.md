# Infrastructure + data-layer configuration audit (2026-10-03)

Scope: on-box, read-only audit of the Jetson host, systemd units, Docker, zoe-data's serving stack,
Postgres, Chroma/MemPalace, nginx/TLS/mDNS and backups, against current published practice.
Measured on the live box 2026-10-03 21:20–22:40 AWST (uptime 53 d; zoe-data up since 09-30 11:14).
Env values were not read; only names. No setting was changed. Per-process RAM attribution is out of
scope here (a separate memory profile covers it; see also
[memory-pressure-profile.md](../knowledge/memory-pressure-profile.md)). RAM is the gate
([samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md) W3). Every action below
has a measurement and a rollback.

**Correction to the brief:** zram is **8 × 244 MB** (1.94 GB of uncompressed capacity), not one
244 MB device. That is the B0.1 shrink (`/ 8 /` in `/etc/systemd/nvzramconfig.sh`, 2026-09-27); the
07-06 profile also had 8 devices, which were larger then. All 8 are **full**: 1,943 MB stored in
501 MB of RAM (4.1:1, lzo-rle), so further swap lands on the NVMe swapfile (0.2 GB at 21:20, 0.6 GB
by 22:40 while agent sessions were running).

## 1. What we run

| Component | Version | Key settings (live) |
|---|---|---|
| Host | L4T R36.4.3 (JetPack 6.2, Seeed reComputer image), kernel 5.15.148-tegra, systemd 249 | `nvpmodel` MAXN_SUPER; `jetson_clocks` on (CPU 8 × 1984 MHz fixed, GPU 1173 MHz fixed, EMC 3199 MHz override); governor schedutil (pinned min=max); fan nvfancontrol; 44 °C tj at idle, ~9 W VDD_IN |
| Memory/VM | cgroup v2 with `memory_recursiveprot`; `CONFIG_PSI` not set | swappiness 10, vfs_cache_pressure 50, min_free_kbytes 262144, watermark_scale_factor 100, page-cluster 3, overcommit 0; THP `always`, defrag `madvise` (AnonHugePages 2.76 GB, 2.0 GB of it in the brain's mlocked region); swap = 8 × 244 MB zram (prio 5) + 50 GB `/swapfile` (prio -2) |
| Storage | NVMe 1.8 TB, ext4 `relatime`, scheduler `none`, read_ahead 128 KB | 23% used. Docker: 66 GB images (16 GB reclaimable), 12.5 GB build cache, 946 MB container JSON logs |
| User units (voice/hot path) | — | llama-server (brain): `MemorySwapMax=0`, `MemoryLow=6G`, `CPUWeight/IOWeight=400`, `--load-mode mmap+mlock` (VmLck 1.95 GB); zoe-data: `MemorySwapMax=0`, `MemoryLow=2G`, `CPUWeight/IOWeight=300`, `MALLOC_ARENA_MAX=2`, `MALLOC_TRIM_THRESHOLD_=131072`; kokoro-tts: `MemorySwapMax=0`, `MemoryLow=3G`, `MemoryMax=4G`, same MALLOC_*; functiongemma-router: `MemorySwapMax=0`, `MemoryLow=768M`, `MemoryMax=1G`, `--threads 4`, default mmap, no mlock; flue brain/telegram: `MemorySwapMax=0` + caps; serena-mcp: `Nice=10`, `OOMScoreAdjust=500`, `MemoryHigh=1G`, `MemoryMax=2G` |
| Delegation | `user@.service`: `Delegate=pids memory` | `user.slice` → `user-1000.slice` → `user@1000.service` → `app.slice` all `memory.low=0`; their `cgroup.subtree_control` = `memory pids` (no `cpu`, no `io`) |
| Docker | 29.1.3, overlay2, cgroup v2/systemd | `daemon.json`: only `dns`, `runtimes`; **no `log-opts`** (json-file, unbounded). **No container has a memory or CPU limit.** 12 containers |
| zoe-data | CPython 3.12.13 (python-build-standalone: PGO + ThinLTO, shared), uvicorn 0.53.0, uvloop 0.22.1, httptools 0.8.0, FastAPI 0.141.1, Starlette 1.7.0, Pydantic 2.13.5, asyncpg 0.31.0, chromadb 1.5.9, numpy 1.26.4, onnxruntime 1.23.2 | `python -m uvicorn main:app --host 0.0.0.0 --port 8000` — 1 worker; loop/http `auto` → uvloop + httptools (both mapped in the process); backlog 2048 (somaxconn 4096); keep-alive 5 s; no `--limit-concurrency`; open-files soft limit **1024** (41 in use); 91 threads; torch is installed in the venv but **not loaded**; two ONNX Runtime copies loaded (pip `onnxruntime` + Moonshine's bundled one); startup to "ready" ≈ 2 s, stop ≈ 0.3 s |
| zoe-data → Postgres | `db_pool.py` | asyncpg `min_size=2, max_size=10, command_timeout=30`; every acquire bounded by `asyncio.wait_for` (raises `PoolExhaustedError`); 11 idle client backends observed |
| Postgres | 17.10 (`pgvector/pgvector:pg17` container, compose pins `0.8.6-pg17@sha256` = 17.11 — #1727 not applied) | shared_buffers 128 MB, work_mem 4 MB, effective_cache_size 4 GB (default), max_connections 100, autovacuum defaults, `jit=on`, `log_min_duration_statement=-1`, no `pg_stat_statements`, `idle_in_transaction_session_timeout=0`, data_checksums off. DBs: zoe 40 MB, multica 20 MB. Hit ratio 99.97%. **Published on 0.0.0.0:5432** |
| Chroma / MemPalace | chromadb 1.5.9, embedded `PersistentClient` in zoe-data, `~/.mempalace` (61 MB sqlite + 41 MB HNSW) | `mempalace_drawers` 257 items, `mempalace_audit` 19,300 items; both `space=l2`, `max_neighbors=16`, `ef_construction=100`, `ef_search=100`, `num_threads=8`, `sync_threshold=1000`, `resize_factor=2.0`; default embedding (MiniLM-L6 ONNX); sqlite `journal_mode=delete`, 11% freelist; queue auto-purge on |
| nginx (zoe-ui) | nginx 1.31.6 (`nginx:alpine`, unpinned) | TLS 1.2/1.3, http2; proxies to `host.docker.internal:8000` with `proxy_http_version 1.1`, no upstream `keepalive` (52 TIME_WAIT sockets, no 502s in 72 h); cert self-signed RSA-2048, **CN=zoe.local, no SAN, expires 2026-11-24** |
| Naming | avahi 0.8 | box publishes **`zoe-2.local`** (name conflict at boot 08-11); the daemon does not answer on D-Bus (`GetHostNameFqdn` times out; `avahi-resolve` "Daemon not running"); `getent ahostsv4 zoe.local` and `zoe-2.local` both time out on the box. IP 192.168.1.218 is **DHCP** (`ipv4.method auto`). Panel uses `https://192.168.1.218`, `/etc/hosts` pins `zoe.local`, and Chromium runs with `--ignore-certificate-errors` |
| Backups | `zoe-backup.timer` 02:35, `zoe-backup-verify.timer` weekly | pg_dump `-Fc` of zoe + multica, 7 daily + 4 weekly; MemPalace sqlite-backup-API copy + HNSW tar, keep 7, integrity check first. **All on the same NVMe.** Home Assistant `.storage` (not in git) and Music Assistant data (`~/.zoe/music-assistant`, 41 MB) are not in the nightly set |
| Logs | journald `SystemMaxUse=400M` (396 MB used); zoe-data app log `RotatingFileHandler` 10 MiB × 6; stdout/stderr files rotated nightly by `zoe-logs-rotate` (79 MB + 75 MB today) | Docker JSON logs unbounded: multica-backend 621 MB, HA-MCP bridge 122 MB, zoe-ui 93 MB, music-assistant 68 MB |

## 2. Deviations from best practice, with the expected effect

**D1. The router's weights are evicted, and after an idle gap that costs about 130–190 ms.**
The two-stage router (`functiongemma-router`, on the voice and chat hot path) maps its 278 MB GGUF and
does not mlock it. Live `smaps` shows **Rss 0 kB** for that mapping, so all of it has been reclaimed. Its
cgroup has **7.6 M file refaults** (≈ 29 GB re-read) across about 2,400 requests since 09-27, roughly
12 MB per call. The router's own `total time` lines in journald (09-28 → 10-03, n = 2,384) give p50
**293 ms** / p90 **450 ms** when the previous request was under 10 min earlier, and p50 **427 ms** /
p90 **638 ms** / max 818 ms after a gap of more than 10 min (n = 47). Overall p99 is 630 ms and the
max is 1,588 ms, which is past the 1.5 s client timeout. The unit's `MemoryLow=768M` was meant to hold
this working set, but it is inert (D2). Expected effect of pinning the weights: about −130 ms p50 and
−190 ms p90 on the first turn after a quiet period. Cost: up to 278 MB of resident RAM, most of which
is already resident whenever the router is hot.

**D2. The cgroup priorities are partly decorative.** These are two separate gaps.
- *Memory floors.* Every leaf `MemoryLow` (6G / 2G / 3G / 768M / 512M / 256M) computes to an effective
  floor of **0**, because each ancestor has `memory.low=0`. The kernel caps a cgroup's effective low by
  its ancestors'. This was already found in
  [memory-pressure-profile.md §"The natural experiment"](../knowledge/memory-pressure-profile.md);
  `memory.events low 0` on every unit confirms it is still true. `MemorySwapMax=0` holds, and every
  voice unit shows `memory.swap.current 0`, so anon pages are safe. File pages (code, mmapped
  weights) have **no** protection, and D1 is the measured cost of that.
- *CPU and IO weights (not previously recorded).* `user@.service` delegates only `pids memory`, so
  `cpu` and `io` are never enabled below `user.slice`. The leaf cgroups have **no `cpu.weight` or
  `io.weight` file at all**. `CPUWeight=400/300` and `IOWeight=400/300` on llama-server and zoe-data
  are therefore no-ops. The memory-pressure-profile table's claim that "weights keep it *scheduled*
  once resident" does not hold on this box. Agent sessions (`session-*.scope`), CI jobs
  (`github-runner.service`) and the voice stack all compete as plain CFS threads. Only `Nice=`
  de-prioritisation (serena `Nice=10`) actually takes effect. Expected effect of real weights: tail
  latency for the router (CPU-bound, 4 threads) and zoe-data while agents or CI run. It is not
  quantified yet; see A2's measurement.

**D3. A dev tool is reading ~200 MB/s from the NVMe while it is capped.** During this audit, one
`codebase-memory-mcp` (an agent's MCP server, in `run-u11349.scope`) sat at its `MemoryHigh=512M`
for more than 25 minutes, with 1.2 M `high` events, ~1,800–2,000 major faults/s and **200 MB/s of
block reads attributed to `user.slice`** (`system.slice`: 0). Its cgroup had 39 M file refaults
(≈ 150 GB). The cap in `scripts/maintenance/codebase_memory_capped.sh` does its job (the process is
not OOM-killed), but because reclaimable file pages keep it below `MemoryMax=768M`, it **thrashes
indefinitely instead of being killed**. With no `io` delegation (D2), nothing ranks that IO below the
voice stack's refaults or swap-ins. This is fleet behaviour, not house behaviour, but it shares the
disk and the swapfile with the house.

**D4. Docker logs are unbounded.** The json-file defaults are `max-size=-1`. Today they total 946 MB;
multica-backend alone is 621 MB after 7 weeks. There is no RAM effect, and disk is at 23%, so this is
slow growth plus `docker logs` latency. The fix only applies to containers when they are recreated.

**D5. No container has a memory limit.** On the RAM-gated box, a leaking HA or MA (HA upgrade-leak
reports are a recurring community topic) grows until the global OOM killer chooses a victim. Current
footprints, resident + swap: HA 91 + 369 MB, MA 125 + 269 MB, omnigent 30 + 209 MB, Postgres
41 + 103 MB, potoken 29 + 78 MB. CI jobs under `github-runner.service` are also uncapped
(`MemoryMax/MemorySwapMax=infinity`). The memory note on voice-stack protection records CI burst RAM
killing the running brain.

**D6. #1727 (B0.12) is merged but not applied.** Postgres (`0.0.0.0:5432`, 17.10) and the
**unauthenticated** HA-MCP bridge (`0.0.0.0:8007`) are reachable from the LAN. iptables `INPUT`
policy is `ACCEPT` and there is no host firewall. The containers were created 06-15 and 05-10, before
the compose change. This is already tracked in PLANS / the program doc, and is restated here because
it is the only item on this list with a security rather than a latency effect.

**D7. Naming and TLS will fail on a fixed date.**
- The cert expires **2026-11-24** (52 days away). It has no SAN, so modern browsers reject it for any
  name. The panel is unaffected (`--ignore-certificate-errors` plus an `/etc/hosts` pin); phones get
  an interstitial.
- `ZOE_BASE_URL=http://zoe.local` in the live `.env` feeds the pairing QR `pair_url`. On this LAN,
  `zoe.local` resolves to another device or nothing, and the box itself publishes `zoe-2.local`.
- The local avahi does not answer D-Bus, and both names time out locally. The nsswitch line
  `mdns4_minimal [NOTFOUND=return]` turns any `.local` lookup on the box into a multi-second stall
  (runbook §20).
- The IP is DHCP-assigned, so the panel URL, the QR and the cert's would-be IP SAN all depend on a
  lease.

**D8. Backups are on the same disk.** The nightly set is good: dump format, integrity check,
retention and a weekly verify. But there is no off-box copy, so an NVMe failure loses Postgres, the
MemPalace and the backups together. HA `.storage` (device registry and integration auth, 144 KB,
untracked) and MA data are not in the set.

**D9. Postgres has no slow-query visibility, and the hot table has text timestamps.**
- `log_min_duration_statement=-1`, no `pg_stat_statements`, `track_io_timing=off`, so a slow query
  cannot be seen.
- `chat_messages.created_at` is `text`. Six background readers filter on `created_at::timestamptz`.
  That is non-sargable, and no index on it is possible because the cast is not immutable.
  `chat_messages` has had **145,828 sequential scans reading 990 M tuples**. Measured `EXPLAIN ANALYZE`:
  5.7 ms (idle-consolidation candidates) and 14.6 ms (active users, 14 d) at 8,199 rows. That is small
  today, grows linearly, and is off the voice path: the per-turn history reads are `session_id`-keyed
  and indexed.
- `jit=on` has no measured effect today. No plan here reaches `jit_above_cost=100000` (the largest
  measured cost is ~414), so `jit=off` is only a guard against a future misestimate costing hundreds
  of ms.

**D10. zoe-data's open-files soft limit is 1024.** It is harmless today (41 fds at 3.5 days uptime,
so no leak now). It is still a cheap hypothesis for runbook §1's unexplained accept-queue hang:
`accept()` hitting EMFILE leaves the process alive and the listen queue filling to backlog + 1.
That is the same signature as an event-loop stall.

**D11. The VM knobs are tuned against each other.** `swappiness=10` tells reclaim to drop file pages
before anon. With zram the kernel docs suggest values above 100 for in-memory swap. The effect here:
idle container anon (HA/MA/Postgres) stays put while hot-path file pages (D1) get evicted. But zram
is full and the overflow is NVMe, so raising swappiness is not free. This is the W3.4 "zram
rebalance (measure-first)" item. It is listed as an experiment (A12), not a fix.

## 3. Ranked actions

Ranking is expected hot-path and stability gain ÷ risk. "Repo" = PR to a tracked template or
compose file; "Operator" = Jason / root on the box. Every restart named below follows the existing
voice-window rules (≥ 2 GB quiet headroom; replay gate for voice-path units).

| # | Change | Expected gain | Risk | Rollback | How to measure | Who |
|---|---|---|---|---|---|---|
| **A1** | **Pin the router's weights:** add `--mlock` to `functiongemma-router` ExecStart and `LimitMEMLOCK=infinity`. This build still takes `--mlock`; the brain's b11194 build spells it `--load-mode mmap+mlock`. Then raise `MemoryMax` to 1.25G so the locked mapping plus the existing ~300–600 MB working set cannot trip the 1G backstop | −130 ms p50 / −190 ms p90 on the first routed turn after >10 min idle (D1). The tail past the 1.5 s client timeout should go | Low. Costs ≤ 278 MB resident (VmLck); an OOM is only a degradation (Restart=always + fallback). Restart of a non-brain unit | Remove the flag, daemon-reload, restart | Re-run the journald split (hot vs gap > 10 min) after a week; `VmLck` ≈ 278 MB; cgroup `workingset_refault_file` per request ≈ 0 | Repo (template `scripts/setup/systemd/functiongemma-router.service`) + Operator restart |
| **A2** | **Make the priorities real** (root drop-ins): `/etc/systemd/system/user@.service.d/delegate.conf` → `Delegate=pids memory cpu io`; and ancestor floors via `systemctl set-property user.slice MemoryLow=11G`, `user-1000.slice MemoryLow=11G`, `user@1000.service MemoryLow=11G`, with user-level `systemctl --user set-property app.slice MemoryLow=11G`. Size the 11G from the sum of what is actually used (~7.5 GB) plus headroom, not the 12.5 GB sum of the leaf settings | D2: CPUWeight/IOWeight start working (voice stack 300–400 vs default 100 against agents and CI); file pages of hot units become reclaim-protected, which fixes the class D1 belongs to without per-unit mlocks | **Medium.** Delegation takes effect when the user manager restarts, which bounces every user unit, so it needs a maintenance window plus the replay gate. A floor that is too high pushes all reclaim onto `system.slice` (HA, MA, Postgres containers) and agent sessions | Delete the drop-ins / `set-property … MemoryLow=0`, daemon-reload, restart user@1000 | `cat …/llama-server.service/cpu.weight` exists = 400; `memory.events low` > 0 under pressure; router cold-vs-hot split; a stress run (CI job + agent) with router p90 compared before and after | Operator/root |
| **A3** | **Apply #1727 (B0.12), and do the Postgres config changes in the same recreate:** `jit=off`, `log_min_duration_statement=250ms`, `shared_preload_libraries=pg_stat_statements` (+ `CREATE EXTENSION`), `track_io_timing=on`, `idle_in_transaction_session_timeout=5min` (via compose `command: postgres -c …`) | Closes LAN exposure of Postgres and the unauthenticated HA bridge (D6). Gives slow-query visibility for the first time (D9). One Postgres restart instead of two | Low–medium: ~10 s of DB downtime; the zoe-data pool reconnects (bounded acquire). pg_stat_statements costs a few MB of shared memory | `docker compose up -d` the previous image / remove the `-c` flags | `docker port zoe-database` → `127.0.0.1`; `select count(*) from pg_stat_statements`; slow log lines appear in `docker logs` | Repo (compose `command:` for the PG flags) + Operator (recreate) |
| **A4** | **Fix naming and TLS before 11-24:** (1) DHCP reservation for .218 on the router; (2) set `ZOE_BASE_URL=https://192.168.1.218` (runbook §20's recommendation); (3) restart `avahi-daemon` (it is wedged), and set `host-name=zoe` in `avahi-daemon.conf` only once the device that answers `zoe.local` is powered off or renamed; (4) re-issue the cert with SAN `DNS:zoe.local, DNS:zoe-2.local, IP:192.168.1.218`. Better, issue it from a small local CA that phones install once | Pairing QR works from phones; no `.local` multi-second stalls on the box; no expiry outage on 11-24 | Low. Avahi restart drops mDNS for ~1 s. Cert swap = nginx reload | Revert `.env`; keep the old `cert.pem`/`zoe.key` beside the new pair and swap back + `nginx -s reload` | QR scan from a phone resolves; `getent ahostsv4 zoe.local` < 100 ms on the box; `openssl x509 -ext subjectAltName` | Operator (router, .env, avahi, cert) |
| **A5** | **Off-box backup copy + coverage:** after the nightly backup, push `~/.zoe-backups/{postgres,mempalace}` plus a new `ha-storage` tar (`homeassistant/.storage`) and `~/.zoe/music-assistant` to a second device. Candidates: the panel Pi (SD card is weak), a NAS, or encrypted cloud via restic/rclone | Turns a single-NVMe failure from "lose everything" into "restore last night" | Low (append step; secrets: dumps contain household data, so encrypt off-box) | Remove the step | `zoe-backup-verify` extended to check the remote copy's age < 26 h | Repo (script) + Operator (target + credentials) |
| **A6** | **Stop the codebase-memory thrash:** in `codebase_memory_capped.sh` set `MEM_HIGH` = `MEM_MAX` (768M), so a capped spawn is OOM-killed instead of throttled into permanent refault. Or raise MEM_HIGH to 768M and MEM_MAX to 1G if indexing this repo legitimately needs more than 512 MB | Removes the sustained ~200 MB/s NVMe reads and ~2k majfaults/s per long-lived agent session (D3); frees disk bandwidth for swap-in and hot-path refaults | Low. Worst case an agent's code-intel restarts | Revert the defaults | Same per-process majflt/read sampler (`/proc/<pid>/stat` field 12 delta); `memory.events high` rate on `run-u*.scope` | Repo |
| **A7** | **Cap CI on the live box:** drop-in for `github-runner.service`: `MemoryHigh=2G`, `MemoryMax=3G`, `MemorySwapMax=0`, `Nice=10`, `OOMScoreAdjust=500`. Track it as a template | A CI burst becomes a failed job instead of a dead brain (the memory note's "fast-fail ~100 ms" signature) | Low–medium: a heavy test lane might need more. Check `memory.peak`-equivalent (kernel 5.15 has no `memory.peak`; sample `memory.current`) on a full self-hosted run first | Remove the drop-in | Job pass rate unchanged; `memory.events max/oom` on the runner cgroup | Repo (template) + Operator |
| **A8** | **Bound Docker logs:** compose `x-logging: &default-logging {driver: json-file, options: {max-size: "10m", max-file: "3"}}` on every service, plus the same in `daemon.json` `log-opts` for non-compose containers | Caps ~946 MB of growing logs at ~30 MB per container | Low. Takes effect on recreate only (batch with A3 / the next MA re-create) | Remove the anchor | `docker inspect -f '{{.HostConfig.LogConfig}}'`; container log sizes | Repo (compose) + Operator (daemon.json) |
| **A9** | **Leak backstops for containers:** `mem_limit` (with `memswap_limit` = 1.5×) — HA 1.5g, MA 1g, omnigent 768m, multica-backend 512m, multica-web 256m, potoken 256m. Leave Postgres unlimited, or at 1g (its working set is ~150 MB) | A runaway container is OOM-killed inside its own cgroup and restarted (`unless-stopped`) instead of pressuring the brain | Low–medium. Set from footprints at ≥ 3× current resident + swap; HA's first start after an upgrade spikes, so watch it | Remove the keys, recreate | `docker stats`; `memory.events oom_kill` per container scope | Repo (compose) + Operator (recreate) |
| **A10** | **zoe-data `LimitNOFILE=65536`** in the tracked drop-in, plus fd count in `zoe-health-check.sh` | Removes EMFILE as an accept-queue-hang path (D10) and records evidence if fds ever climb | Very low | Remove the line | `/proc/<pid>/limits`; fd trend in the health log | Repo + Operator restart (deploy window) |
| **A11** | **`chat_messages.created_at` → `timestamptz`** (Alembic `USING created_at::timestamptz`), plus `(role, created_at)` and `(created_at)` indexes; same for `chat_sessions` | Turns the six background full scans (5–15 ms each, 145 k so far, linear in history) into index scans | Medium: a migration on the hottest table. Every `::timestamptz` cast and text comparison in the six readers must be checked; the 8 k rows make the rewrite itself instant | Downgrade migration back to text | `EXPLAIN ANALYZE` of the same two queries; `seq_scan` delta on `pg_stat_user_tables` | Repo (migration + reader changes) |
| **A12** | **Experiment, not a fix (W3.4):** A/B `vm.swappiness` 10 → 60 (then 100) for 48 h each, with A1/A2 in place | May keep hot file pages resident by swapping idle container anon instead | Medium: more anon to a full zram, then NVMe | `sysctl vm.swappiness=10` (runtime, instant) | `pswpin`/`pswpout`/`workingset_refault_file` rates, router cold split, HA first-command latency | Operator |

Not recommended (considered and rejected):
- Multiple uvicorn workers. In-process Moonshine and the in-memory LRU and session state would
  duplicate ~1.1 GB per worker and split state.
- `--limit-concurrency`. It returns 503s under load but does nothing for a stalled loop, which is the
  failure mode actually seen.
- Turning `jetson_clocks` off. It saves a few watts at idle and costs DVFS ramp latency on every
  turn; thermals have headroom (44 °C).
- Changing THP. The large THP users are the inference engines, where huge pages cut TLB misses, and
  `defrag=madvise` already avoids the compaction stalls the Postgres literature warns about.
- Chroma HNSW retuning. At 257 and 19.3 k items, M=16 / ef=100 is at or above the recall knee, and
  `resize_factor=2.0` stays.
- `PYTHONMALLOC` changes. pymalloc is right for CPython 3.12; mimalloc is only bundled from 3.13.

## 4. Things already right (keep)

- MAXN_SUPER plus `jetson_clocks` for a latency-first box; NVMe scheduler `none`.
- `MemorySwapMax=0` on every voice-path unit: `memory.swap.current` reads 0 on llama-server,
  zoe-data, kokoro-tts and the router. This is the guard that actually holds.
- The brain mlocks its weights (VmLck 1.95 GB) and has no `MemoryMax` (a ceiling plus no swap would
  turn a spike into a kill).
- `OOMScoreAdjust=500` + `Nice=10` on serena works. Both global OOM kills this boot (09-13, 09-26)
  took serena and nothing on the voice path. Both memcg kills (09-27, 09-28) stayed inside capped
  `run-u*.scope` jobs.
- uvicorn auto-selects uvloop + httptools; a single worker is by design; stop time is 0.3 s and
  startup-to-ready about 2 s.
- asyncpg: every acquire is bounded (`asyncio.wait_for` → `PoolExhaustedError`, the #953 leak
  lesson); pool 2–10 against `max_connections` 100; 99.97% buffer hit ratio; 128 MB shared_buffers
  is more than the whole 40 MB DB.
- `MALLOC_ARENA_MAX=2` + `MALLOC_TRIM_THRESHOLD_=131072` on the two big Python services. These match
  glibc's documented knobs for multi-threaded arena bloat.
- Python 3.12.13 is a PGO + LTO build. torch is not imported by zoe-data.
- The app log rotates (10 MiB × 6); journald is capped at 400 MB.
- The MemPalace backup uses the sqlite backup API after an integrity check; Postgres uses
  `pg_dump -Fc`; 7 daily + 4 weekly; there is a weekly verify timer.
- Chroma HNSW: sensible defaults, `resize_factor=2.0` (the HNSW-segfault guard), queue auto-purge on.
- nginx → zoe-data has no upstream keepalive, so no 5 s uvicorn keep-alive race. **If upstream
  keepalive is ever added** (nginx ≥ 1.29.7 enables it by default inside `upstream {}` blocks), set
  `keepalive_timeout` below uvicorn's 5 s, or raise `--timeout-keep-alive`, or expect intermittent 502s.

## 5. Sources (accessed 2026-10-03)

- Linux kernel, *Control Group v2* — memory.low effective boundary limited by ancestors; `memory_recursiveprot`: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux kernel, *sysctl/vm* — swappiness 0–200, "values beyond 100 can be considered" for zram: https://docs.kernel.org/admin-guide/sysctl/vm.html
- Linux kernel, *zram*: https://docs.kernel.org/admin-guide/blockdev/zram.html
- systemd, *systemd.resource-control(5)* — Delegate=, controllers enabled for user units: https://man7.org/linux/man-pages/man5/systemd.resource-control.5.html
- Worked examples of inert MemoryLow under a 0-floor slice (2026): https://github.com/CannObserv/notifier/issues/85 , https://github.com/gregoryfoster/skills/issues/307
- NVIDIA, *Jetson Linux Developer Guide r36.4.4 — Platform Power and Performance* (MAXN_SUPER, jetson_clocks): https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html
- NVIDIA Jetson AI Lab, *RAM Optimization* (disable zram, NVMe swap for LLMs): https://www.jetson-ai-lab.com/tutorials/ram-optimization/
- uvicorn 0.53.0 source (`opensrc path pypi:uvicorn@0.53.0` → `uvicorn/config.py`: backlog 2048, timeout_keep_alive 5, limit_concurrency None; `uvicorn/loops/auto.py`: uvloop when importable); *Server Behavior*: https://uvicorn.dev/server-behavior/
- asyncpg 0.31.0 source (`opensrc path pypi:asyncpg@0.31.0` → `asyncpg/pool.py` defaults) and API docs: https://magicstack.github.io/asyncpg/current/api/index.html
- PostgreSQL 17, *When to JIT?*: https://www.postgresql.org/docs/17/jit-decision.html ; *pg_stat_statements*: https://www.postgresql.org/docs/17/pgstatstatements.html
- Percona, *Settling the Myth of Transparent HugePages for Databases*: https://www.percona.com/blog/settling-the-myth-of-transparent-hugepages-for-databases/
- Chroma, *Configure collections* (HNSW parameters, defaults, mutable-after-create set): https://docs.trychroma.com/docs/collections/configure
- Docker, *JSON File logging driver* (max-size default -1; config applies to new containers only): https://docs.docker.com/engine/logging/drivers/json-file/
- nginx CHANGES 1.29.7 (upstream keepalive and HTTP/1.1 on by default): https://nginx.org/en/CHANGES ; upstream module: https://nginx.org/en/docs/http/ngx_http_upstream_module.html
- mallopt(3) — `M_ARENA_MAX` / `MALLOC_ARENA_MAX`, `M_TRIM_THRESHOLD`: https://man7.org/linux/man-pages/man3/mallopt.3.html
- Home Assistant community, container RAM growth after upgrade: https://community.home-assistant.io/t/home-assistant-container-fills-up-my-ram-and-swap-in-a-minute-after-upgrade/872679
- Internal: [incident-runbook.md](../knowledge/incident-runbook.md) §1 (accept-queue hang), §20 (`zoe.local` getaddrinfo); [memory-pressure-profile.md](../knowledge/memory-pressure-profile.md); [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md) B0.1, B0.12.
