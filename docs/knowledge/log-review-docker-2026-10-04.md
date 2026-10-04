---
type: Reference
title: Docker container log review (2026-10-04 evening)
description: Read-only review of every Docker container's log for the window 2026-10-04 17:00-22:00 AWST (09:00-14:00Z) - per-container error/warning classes with counts, first/last time and the config or code cause, the docker stats and docker system df snapshot, which classes were fixed in the repo (nginx poll-log noise, compose log caps, HA bridge blind spot, APScheduler duplicate-key ERRORs, panel pin index on a bridge error body) and which are operator steps (YouTube Music cookies, Tuya cloud, stale ESPHome entry, memory caps, recreate-to-bound-logs).
tags: [docker, logs, nginx, postgres, apscheduler, home-assistant, music-assistant, cloudflared, memory, review, evidence]
timestamp: 2026-10-04T22:30:00+08:00
---

# Docker container log review - 2026-10-04

Window: `docker logs --since 2026-10-04T09:00:00Z` (17:00 AWST) to about 22:00 AWST (14:00Z). Every
number below was read with `--since` plus grep/uniq; no log was dumped whole. Nothing was
restarted, stopped, exec-modified or reconfigured; the only `docker exec` was one read-only
`psql` count that failed on a wrong column name. No utterance, transcript, person, device-id or
token text appears here: samples are redacted to their shape.

## Context that changes how the logs read

- **dockerd restarted at 14:35 AWST (06:35Z).** `ps` shows `dockerd` started 14:35:33 and
  `/etc/docker/daemon.json` (edited 10:32 AWST) now carries
  `log-driver=json-file, log-opts={max-size:10m,max-file:3}`. `live-restore=false`, so every container
  restarted together ("Up 7 hours" for ten of them). Home Assistant's own log says it was not shut
  down cleanly, which is the same event. That is the A8 step 1 of
  [docker-log-and-memory-limits.md](docker-log-and-memory-limits.md) already done.
- **Only containers created after 14:35 AWST are bounded.** `docker inspect` shows
  `max-size:10m,max-file:3` on `zoe-auth` and `zoe-ui` (recreated by the deploy at 21:25 and 21:35
  AWST) and `map[]` (unbounded) on `zoe-database`, `zoe-music-assistant`, `zoe-multica-backend`,
  `homeassistant` and the rest. A restart does not re-read the daemon default; only a re-create does.
- `zoe-ui` and `zoe-auth` logs only start at their re-create (13:25Z and 13:35Z), so their window
  is shorter than the others.
- Home Assistant logs in **local time** (AWST) while Docker stamps UTC. Its container log shows
  nothing after the 14:35 start, so the window is quiet for it; the lines below are its start-up.

## 1. Inventory by container

| Container | Class | Count | First - last (UTC) | Redacted sample | Cause | Disposition |
|---|---|---|---|---|---|---|
| zoe-ui | `502` from the API/WS proxy, `connect() failed (111)` to the zoe-data upstream | 33 | 13:25:55 - 13:42:00 | `GET /api/ui/actions/pending ... 502`, `GET /ws/push ... 502` from the kiosk | host-native zoe-data was down for three short restarts (13:25-13:26, 13:35, 13:41Z) and the panel polls every second | expected; not an nginx fault. Remaining: nothing to fix in nginx |
| zoe-ui | `403` on `/ws/push` and `/api/people/` | 12 (and counting) | 13:27:41 - 13:53:53 | `GET /ws/push?panel_id=<test-panel> 403` | a headless test browser on the Pi with a panel id that is not a paired kiosk | expected test traffic from the UI review |
| zoe-ui | `404` | 2 | 13:26:52 | `GET /nonexistent.html 404`, a retired `/hermes/...` path | the wave-2 merge verification probing the real-404 and retired-proxy contracts | expected (proves #1834 works) |
| zoe-ui | config warnings | 0 | - | one `info:` line, "can not modify default.conf (read-only file system?)" | the stock image entrypoint cannot rewrite a `:ro` mount; harmless | none |
| zoe-ui | double start | 1 | 13:25:50 | first master gets `SIGQUIT` 1 ms after start, second master starts | `deploy.yml` runs `compose up -d` then `docker restart zoe-ui` on purpose (inode-pinned conf mount) | by design |
| zoe-ui | **log volume** | 2,040 lines in 20 min, 98% `200` | - | `GET /api/ui/actions/pending 200` x1010, `POST /api/ui/state/sync 200` x319, `GET /api/voice/announcements 200` x274 ... | the kiosk and tabs poll 8 endpoints at roughly 1 Hz and every success is logged (93 MB in the earlier audit) | **fixed** (section 3, F1) |
| zoe-ui-test | n/a | - | started 13:39:25 | - | see section 2 | report |
| zoe-auth | warnings/errors | 0 | - | start-up INFO only (OIDC key active, three clients seeded, DB ready) | PyJWT 2.15.0 rebuild is clean; no auth failure, no 401/403 in the window, no trace of the disabled test account | none |
| zoe-auth | **bootstrap setup token written to the log** | 1 per start | 13:35:23 | `WARNING ... One-time bootstrap setup token for first-run password setup: <redacted 32 chars>` | `core/account_setup.py` generates a random token at every start and logs it so a local operator can read it | **remaining**, section 4, R5 |
| zoe-database | `duplicate key value violates unique constraint "apscheduler_jobs_pkey"` | 21 (+21 `STATEMENT:` lines with the pickled job) | 11:53:56 - 13:42:00 | `Key (id)=(<job>) already exists` for three job ids, 7 times each | 7 zoe-data starts x 3 standing jobs; APScheduler's `replace_existing` is INSERT, catch conflict, UPDATE, and Postgres logs the failed INSERT | **fixed** (F4) |
| zoe-database | `unexpected EOF on client connection with an open transaction` | 1 | 12:41:40 | - | a client killed mid-transaction, coincident with a zoe-data restart | benign |
| zoe-database | checkpoints | 58 | every 5 min | `checkpoint complete: wrote 25-58 buffers` | normal | none |
| zoe-database | slow queries | n/a | - | - | `log_min_duration_statement` is unset, so absence of lines proves nothing | **remaining**, R7 |
| homeassistant | start-up warnings | 8 | 06:35:46 - 06:35:52 | custom integration "not tested by Home Assistant" x4; recorder "could not validate ... shutdown cleanly"; "Ended unfinished session (id=69 from 2026-08-10)" | four custom components; the 14:35 dockerd restart killed HA uncleanly | cosmetic; the unclean-shutdown line is the dockerd restart |
| homeassistant | ESPHome `Can't connect to ESPHome API ... [Errno 111]` | 1 (backs off silently after) | 06:35:51 | `Can't connect to ESPHome API for <voice-satellite device> @ <Pi>:6053` | an ESPHome config entry pointing at a voice-satellite device on the Pi, where nothing is listening on port 6053 (cause of the missing listener not investigated) | **operator** R4 |
| homeassistant | localtuya cloud `Error 28841002: IoT Core service subscription has expired` | 1 | 06:35:52 | - | the Tuya IoT Core cloud trial expired; localtuya's cloud fallback cannot refresh | **operator** R4 |
| homeassistant-mcp-bridge | errors | 0 visible | - | 2,322 lines, all access lines | see "blind spot" below | **fixed** (F3) |
| homeassistant-mcp-bridge | log volume | 1,750 `GET /entities` + 573 `GET /` (health) | - | `GET /entities HTTP/1.1 200` | zoe-data entity poll about every 10 s plus the 30 s Docker healthcheck | **fixed** (F3) |
| zoe-music-assistant | `Error loading provider(instance) ytmusic--...: User does not have Youtube Music Premium (will be retried later)` | 143 | 09:00:00 - 13:56:20 | same | the YouTube Music login cookie is invalid; MA retries every ~2 min | **operator** R1 |
| zoe-music-assistant | yt-dlp `The provided YouTube account cookies are no longer valid` | 548 | 09:02:01 - 13:56:18 | same | same root cause; the retry loop calls yt-dlp ~4 times per attempt | **operator** R1 |
| zoe-music-assistant | genre scan, AirPlay | 6 INFO, 0 AirPlay lines | - | `Genre mapping scan completed: 0 items mapped` | normal; no panel/AirPlay errors in the window | none |
| zoe-ytmusic-potoken | errors | 0 | - | 2 lines: a probe "Generating POT for <video id>" and the token it printed | a health probe; the PO token itself is written to stdout (short-lived, not copied here) | none |
| zoe-omnigent | session failures / OAuth | 0 | - | 4 lines: one runner started 11:54Z and stopped; the runner token id is printed in the log (not copied) | one agent session ran clean; the container claude-sdk OAuth renewal noted for 2026-08-22 produced no error in this window | none |
| zoe-multica-backend | errors | 0 | - | 1,581 lines: `db pool stats` x1,185, `GET /api/issues 200` x408, `GET /api/autopilots 200` x28 | board polling every ~40 s plus a pool-stats line every 15 s | log volume only (R3) |
| zoe-multica-backend | `WRN db pool pressure` | 3 | 09:36:12, 10:36:12, 12:36:57 | `empty_acquire_delta=1-2`, `acquired_conns=0-1` of 25 | the hourly burst of ~48 acquires hits an empty-acquire counter of 1-2; the pool is 5 of 25 and idle | false positive, third-party image |
| zoe-multica-web | - | 0 lines | - | - | - | none |
| zoe-smb-drop | - | 0 lines | - | - | - | none |
| zoe-cloudflared | QUIC `timeout: no recent network activity` on all four edge connections | 1 episode: 4 `WRN` + 1 `ERR` | 11:46:10 - 11:46:26 | `Serve tunnel error ... datagram manager error: timeout` | one uplink hiccup; all four tunnels re-registered (two Perth, two Melbourne edges) within 16 s | transient; none |

### The bridge blind spot (why "0 errors" for the HA bridge is not good news)

`homeassistant-mcp-bridge` catches the `HTTPException` its HA client raises (expired token 401, HA restart
503, timeout 408) and returns **HTTP 200** with `{"entities": [], "error": ..., "status": N}`. So uvicorn
logged a success and the bridge logged nothing. The Docker healthcheck (`GET /`) behaves the same way:
it returns 200 with `status: "unhealthy"`. Earlier today the host log of zoe-data shows 70
"HA bridge unreachable; pins unresolved" warnings between 12:00 and 14:04 AWST, none of which has a
matching line in this container's log. That is the class fixed in F3.

## 2. `zoe-ui-test` - who started it, should it still run

- It was a **throwaway `docker run`**, not a compose service: image `nginx` (same family as `zoe-ui`),
  no compose labels, restart policy `no`, started 21:39:25 AWST (13:39:25Z), not 21:58 as noted.
- Its mounts are the **`zoe-ui-deep-review-9d9d4a` worktree's** `services/zoe-ui/dist`,
  `nginx.conf` and `nginx.d` (read-only), plus the shared `ssl/`. It published host ports 8081 and 8443.
  It is the UI deep-review session's own preview of an unmerged `dist`, used by the headless
  browser on the Pi (its requests carry that session's panel id and a `journal.html` call to a
  `/api/journeys` route that zoe-data answers 404).
- At the time of the later check (about 21:50 AWST) it was **gone from `docker ps -a`**, so it removed
  itself or its owner removed it. Because the restart policy is `no` it would not have survived a
  reboot anyway. Nothing was stopped by this review.
- Rule of thumb for the next one: a `zoe-*-test` container with a worktree bind mount belongs to the
  agent session whose worktree it mounts; it is safe to leave while that session is live and safe to
  remove (`docker rm -f`) once it is not. It is not an estate service.

## 3. Fixes landed in this PR (the class, not the instance)

| # | Class | Change | Pinned by |
|---|---|---|---|
| F1 | A closed list of polling endpoints makes up ~98% of the nginx access log; the one 502 that matters is lost in it | `map "$request_method:$status:$uri" $zoe_log_access` in `services/zoe-ui/nginx.conf`; both servers log `main if=$zoe_log_access`. Only successful `GET` (200/204/304) on seven named poll paths plus the one `POST /api/ui/state/sync` heartbeat are skipped; every other method, status (502, 403, 404) and path is still logged, so a successful `PUT` to a polled URI stays in the audit trail | `tests/unit/test_nginx_delivery_layer.py` (regex semantics + negative control: adding `502` to the skip list turns it red); `nginx -t` run with the deploy's pinned image digest |
| F2 | No service has a log cap in its compose definition, so a re-create without the daemon default is unbounded (621 MB in 7 weeks for `zoe-multica-backend`) | `x-logging` anchor (`json-file`, 10m x 3) and `logging: *default-logging` on all 13 services in `docker-compose.yml`, `docker-compose.modules.yml`, `modules/omnigent/docker-compose.module.yml` | `tests/unit/test_compose_loopback_binds.py::test_every_compose_service_has_bounded_json_file_logs` (turned red by deleting one line) |
| F3 | The HA bridge swallows HA failures into HTTP 200 and logs nothing, while logging 2,300 success lines | `services/homeassistant-mcp-bridge/main.py`: `QuietPollAccessFilter` on `uvicorn.access` (drops only 2xx `GET /` and `GET /entities`); `_log_ha_failure` WARNs once per (method, endpoint incl. domain/service, status) per 60 s at the single choke point `_make_request` (401/5xx/408/503/500) | `tests/test_ha_bridge.py` (filter matrix, every failure class, rate limit; red when the timeout log is removed) |
| F4 | Every `add_job(..., replace_existing=True)` provokes a Postgres `ERROR: duplicate key` per standing job per start | `services/zoe-data/proactive/scheduler.py`: `_ReplaceExistingMixin._real_add_job` looks the job up first and, when it exists, goes straight to the store's single UPDATE (the stock replace path) without the failing INSERT. It is deliberately not remove-then-add, which drops the standing job if the add fails (review of #1850). Covers every call site, including Multica autopilot sync and jobs registered before `start()` | `services/zoe-data/tests/test_scheduler_replace_existing.py` counts the jobstore's own `ConflictingIdError`s (the statements Postgres logs): 1 for stock APScheduler (control), 0 with the mixin; red when the mixin is disabled; a failed (unpicklable) replacement leaves the old job in place, red for remove-then-add |
| F5 | The panel pin index reads a bridge error body as "an empty house" and marks every pin stale | `services/zoe-data/routers/panel_config.py::_entity_index` returns `None` (unknown) when the bridge answers with an `error` key | `services/zoe-data/tests/test_panel_config.py` (3 tests; red with the guard disabled) |

Effect is **not live until deployed**: F1 needs the `zoe-ui` restart `deploy.yml` already does; F3 needs
`docker restart homeassistant-mcp-bridge` (the code is volume-mounted, no rebuild); F4/F5 need a
zoe-data restart; F2 reaches a container only when it is re-created.

## 4. Remaining items (not fixable in the repo)

- **R1 YouTube Music is down again** (cookie invalid, 143 provider failures and 548 yt-dlp warnings
  tonight, so music search "finds nothing"). Fix is the known operator flow: panel Music -> Browse ->
  Sources -> Reconnect -> QR -> phone sign-in (reference: runbook section 10 and
  [music-ytdlp-js-runtime.md](music-ytdlp-js-runtime.md)). `ZOE_YTMUSIC_REFRESH_ENABLED` (headless cookie
  re-harvest every 12 h) exists in `services/zoe-data/main.py` and is OFF by default; turning it on is the
  anti-recurrence option and is Jason's call.
- **R2 Ten containers are still unbounded.** Only `zoe-ui` and `zoe-auth` were re-created after the
  daemon default was armed. See the incident-runbook entry for the recreate order.
- **R3 `zoe-multica-backend` log volume** is the third-party image's own 15-second pool-stats line and
  its ~40-second board poll. Bounded by R2 once re-created; no knob exists that this repo owns.
- **R4 Home Assistant config entries** (state lives in `/config/.storage`, not the repo): remove or repair
  the ESPHome entry that points at the Pi (nothing listens on 6053), and either renew the Tuya
  IoT Core subscription or switch localtuya to local-only. Both are UI actions in HA.
- **R5 zoe-auth prints a usable first-run setup token at every start** (WARNING, a fresh random value
  per start, kept in a log that was unbounded until today). It is only needed while an account is still
  `SETUP_REQUIRED`. The right fix is to log it only when such an account exists, or to write it to a
  0600 file. That touches the account-setup security contract and its tests, so it is recorded, not changed.
- **R6 `/api/ha/entities` returns an empty 200 when HA fails** (`routers/ha_control.py::list_entities`
  has the same error-body read as F5). Changing it changes what the kiosk shows during an HA outage, so
  it needs a UI decision first.
- **R7 No slow-query evidence** for Postgres: `log_min_duration_statement` is unset and
  `pg_stat_statements` is not loaded (audit item A3). Set `log_min_duration_statement=500` in the
  database service when A3 lands.
- **R8 Housekeeping candidates** (operator, with eyes on it): `livekit` has been `Exited (137)` for 6 days
  (stopped by hand, `restart: unless-stopped` does not revive it), and four dead containers
  (`awesome_chatterjee`, `wyoming-piper`, `zoe-orbit`, `zoe-jag-board`) are 3-4 months stale.

## 5. Resource snapshot (13:44Z, read-only)

`docker stats --no-stream`; swap and OOM from each container's cgroup (`memory.swap.current`,
`memory.events`). Every `memory.max` is `max`, i.e. **no container has a memory cap**; all `oom_kill`
counters are 0.

| Container | RAM used | Swap | Note |
|---|---|---|---|
| zoe-music-assistant | 431 MiB | 695 MB | most swapped container; 833 MB VmHWM per the audit |
| zoe-omnigent | 380 MiB | 258 MB | idle; sizes with a real session (audit: measure first) |
| homeassistant | 147 MiB | 294 MB | |
| zoe-database | 63 MiB | 26 MB | exempt from caps by the audit |
| zoe-auth | 60 MiB | 33 MB | |
| homeassistant-mcp-bridge | 45 MiB | 18 MB | |
| zoe-ytmusic-potoken | 37 MiB | 95 MB | |
| zoe-cloudflared | 23 MiB | 11 MB | |
| zoe-ui | 11 MiB | 5 MB | |
| zoe-multica-backend | 11 MiB | 4 MB | |
| zoe-smb-drop | 10 MiB | 2 MB | |
| zoe-multica-web | 5 MiB | 35 MB | |
| **Total** | **~1.2 GiB** | **~1.5 GB** | of a 15.3 GiB host |

`docker system df`: images 66.8 GB (16.0 GB reclaimable), containers 305 MB (75 MB reclaimable),
volumes 17.5 GB (297 MB reclaimable, 22 dangling), build cache 12.9 GB (6.7 GB reclaimable). The root
filesystem is 23% used (1.4 TB free), so this is hygiene, not pressure.

Memory caps were **not** added to compose. They are leak backstops sized from measured peaks, they
only help if applied with `docker update` or on re-create, and the audit
([docker-log-and-memory-limits.md](docker-log-and-memory-limits.md)) records the sizing rule, the table
and the Postgres/omnigent exemptions. That recipe is unchanged.

## 6. Unverified in this review

- Nothing in F1-F5 has been deployed or observed live; each is covered by a repo test and a negative
  control, and F1 by `nginx -t` in a throwaway container with the deploy's pinned image.
- Whether the HA bridge reported real HA failures tonight is unknowable: it logged none (the F3 gap).
  The 70 host-side "bridge unreachable" warnings predate the container restart at 14:35 AWST.
- Whether the first-run setup token matters today: the pending-account count was not read (the one
  read-only query used a wrong column name and returned an error).
- The Tuya subscription and the ESPHome entry were read from log text only, not from HA's UI.
