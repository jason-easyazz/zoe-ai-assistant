---
type: review-record
title: zoe-data log review — 2026-10-04 evening
description: Inventory of every WARNING/ERROR/Traceback class and the noise classes in the zoe-data logs for 17:00–21:46 AWST on 2026-10-04 (seven restarts), why the under-rotated stderr/stdout logs grow, what was fixed in code, and what remains for the operator. No household data.
tags: [logging, zoe-data, stderr, rotation, noise, review, incident]
timestamp: 2026-10-04T22:00:00+08:00
---

# zoe-data log review — 2026-10-04 (17:00 → 21:46 AWST)

Scope: `~/.zoe-logs/zoe-data.{app,stderr,stdout}.log`, `zoe-data-8012.log`, `zoe-stop.log`,
read-only. The logs hold household conversation; this record contains **no** utterances,
names, addresses or tokens — user turns are summarised, identifiers redacted.

**Timezones.** The app log stamps `+0800` (AWST) explicitly. The stderr JSON lines carry a naive
local (AWST) timestamp. The stdout log (uvicorn access) has **no** timestamps at all.
apscheduler's "next run at" fields mix `AWST` and `UTC`. UTC = AWST − 8 h.

## Verdict in one paragraph

Tonight's window is **clean of faults**: 0 tracebacks, 0 ERROR/CRITICAL, 7 restarts that all
came up healthy (database ready in 2–3 s, Moonshine warm 1.5–2.7 s, Gemma KV cache warm ~12–14 s
after start, no `/health` non-200 in 460 probes). The only WARNINGs were an expected CSWSH
rejection stream from the UI-verification nginx on `:8443` and a few expired-session 401s. The
real problem is **volume**: 96% of the stderr bytes (and ~94% of stdout) were healthy `200 OK`
poll traffic written three times, into files nothing rotates, and the 20-char session-id prefix
that the auth path logged on every invalid session.

## 1. Inventory (window 17:00 → 21:46 AWST)

Counts are lines in the **stderr** JSON stream unless noted (the app log mirrors WARNING+ 1:1).

| # | Class | Level | Count | First → last | Redacted sample | Emitted at |
|---|---|---|---|---|---|---|
| 1 | Cross-origin WebSocket rejected | WARNING | 67 (+77 uvicorn `… 403` and 77 `connection rejected (403 Forbidden)` raw lines) | 19:49:49 → 21:45 (bursts 19:49–19:51, 21:40–21:45) | `Rejected cross-origin WebSocket handshake: origin='https://<box-ip>:8443' path=/ws/push` | `services/zoe-data/main.py` `_enforce_ws_origin` (≈L2149) |
| 2 | Invalid session (zoe-auth returned 401) | WARNING | 6 in app log (3 sessions × 2 concurrent requests) | 18:41:53, 19:51:15, 20:13:53 | `Invalid session: <20 chars of the token>...` (one literal `null`) | `services/zoe-data/auth.py` `get_current_user` (≈L229) |
| 3 | zoe-auth `GET /api/auth/user` 401 | INFO (httpx) | 6 | same 3 instants | `HTTP Request: GET …/api/auth/user "401 Unauthorized"` | httpx (library) |
| 4 | onnxruntime "GPU device discovery failed … /sys/class/drm/card1/device/vendor" | native WARNING (raw stderr) | 14 (2 per start × 7) | 19:53:53 → 21:42 | `[W:onnxruntime:Default, device_discovery.cc:164 …]`, same for `MoonshineStreamingModel` | libonnxruntime C++ (Tegra has no DRM card1); not our code |
| 5 | uvicorn lifecycle | INFO (raw) | 7 × start/ready/shutdown | 19:53, 20:05, 20:41, 20:50, 21:25, 21:35, 21:41 | `Started server process [N]` … `Finished server process [N]` | uvicorn |
| 6 | HTTP 4xx from unauthenticated probes | INFO (`Request completed`) | 14× 403 `/api/push/vapid-public-key`, 7× 403 `/api/proactive/schedule`, 7× 403 + 1× 401 `/api/people/`, 5× 403 `/api/notifications/pending`, 2× 401 each `/api/skybridge/timers` and `/api/ha/entities`, 2× 404 `/api/journeys`, 1× 404 `/api/lists`, 1× 405 `HEAD /api/weather/current`, misc 403/401 | 18:41 → 21:42 | all `authenticated:false`; user agents = the headless verification browser and `curl`/`python-requests` probes | `middleware/logging.py` (access) + guest guards (`require_feature_access`) |
| 7 | Maintenance-gate probe (#1827) | INFO | 1× `POST /api/memories/maintenance/compact-index` → **404** | 21:42:21 | curl probe | `routers/memories.py` `memory_compact_index_endpoint` (flag `ZOE_MEMORY_INDEX_COMPACT` dark ⇒ 404 by design) |
| 8 | Voice replay-gate traffic | INFO | 200× `voice/transcribe panel=replay-harness`, 20× "Moonshine STT returned empty transcript" | 19:47 → 21:39 | `audio=0.00s STT=0.39s chars=N` | `routers/voice_tts.py` (replay corpus, not household traffic) |
| 9 | Retired-runtime probe | INFO | 60× `Runtime health probe: local_llm=True hermes=False openclaw=False` (every minute) | 17:00 → 21:41 | — | `main.py` |
| 10 | Paused board poll / skipped autopilot | INFO | 53× `multica_poll: runtime dispatch pause active (paused)`, 20× `board review skipped` | 17:00 → 21:45 | — | `main.py`, `multica_autopilot_sync.py` |

**Searched for, not present in the window:** `Traceback`, `ERROR`, `CRITICAL`, "took too long",
watchdog trips (only `Background task watchdog started` ×7 and one routine purge per start),
chroma/HNSW messages (none — the weekly compaction trigger runs Sunday 02:30, already past),
Music Assistant bridge errors (418 `POST :8095/api` all 200), HA bridge errors (1,739
`GET :8007/entities` all 200), flag-inventory or import errors, Telegram media router
(`_telegram_media.register(app)` logs nothing on success, so "loaded" cannot be proven from the
log — only that nothing failed). `zoe-stop.log` has no entry since 2026-06-19.

**`zoe-data-8012.log` / `zoe-data-8011.log`.** Last written **2026-06-18 22:01** — not tonight.
A hand-launched pair of `uvicorn` processes on :8011/:8012 (python3.10 user-site, started 21:47,
stopped ~22:01) from an ad-hoc experiment. No unit, script or doc in the repo starts them and
nothing listens there now. Safe to delete (runbook §23 step 3).

### 1b. Same files, earlier today (outside the window, same classes would recur)

| Class | Level | Count | When | Cause |
|---|---|---|---|---|
| `panel config: HA bridge unreachable; pins unresolved` (+full traceback each) | WARNING | 121 | 10-04 10:55 → 14:04 | HA bridge `ReadTimeout` on every kiosk poll |
| `ha/entities error` (+full traceback) | ERROR | 20 | 10:57 → 13:55 | same `ReadTimeout` through `/api/ha/entities` |
| `Exception in ASGI application` / `CannotConnectNowError: … in recovery mode` | ERROR | 25 (23 DB recovery) | 11:13–11:14 | Postgres restarted/recovering; `db_pool exhausted` ×6 and `panel binding lookup FAILED` ×2 in the same minute |
| `zoe-auth timeout during session validation` | WARNING | 9 | 12:54 → 13:42 | auth container slow |
| `EXPERT_ACTIVE …` (success trace at WARNING, printed the first 80 chars of the reply) | WARNING | ~25 | 10-03 22:40 → 10-04 10:38 | diagnostic promoted to WARNING in the no-handler era |
| `MEMORY_FORGET_SYNTHETIC`, `reconcile_for_ingest: no search hits` | WARNING | dozens | 10-03 22:34 → 10:36 | Samantha-bar harness cleanup of `demo_bar_*` users on the live service (expected, audit trail) |
| `Platform Health Check failed (exit 1)` | WARNING | 4 (one per day 10-01 → 10-04 06:00) | daily | `scripts/maintenance/platform_health_check.sh` exits 1 — not investigated |
| `Multica sync_evolution_proposal failed: 400` | WARNING | 3 | 02:00–07:13 | Multica rejects the issue payload — not investigated |
| `music discovery batch rc=2:` (empty stderr) | ERROR | 1 | 11:00:00 | not investigated |

## 2. Why the under-rotated logs grow

`zoe-data.service` is started with the host drop-in `20-capture-output.conf`
(`StandardOutput=append:…/zoe-data.stdout.log`, `StandardError=append:…/zoe-data.stderr.log`; it
was untracked, now mirrored in `scripts/setup/systemd/zoe-data.service.d/`). systemd `append:`
**never rotates**; the host has no logrotate; journald is volatile. The one rotator is a
host-local, untracked pair (`~/bin/zoe-logs-rotate.sh` + `zoe-logs-rotate.{service,timer}`, daily
02:50) that only acts above 150 MB, keeps a 50 MB tail via a non-atomic `tail -c … | cat >` rewrite
and prunes only the stdout archives (to 3) — so the files oscillate between ~50 MB and 150 MB
instead of staying small. The new tracked rotator replaces it (runbook §23 step 1 retires the
old pair first; the new segment names, `zoe-data.stderr.1.log.gz`, cannot match the old script's
`zoe-data.stdout.log.*.gz` prune). Three writers:

1. **stderr = the JSON root-logger stream.** `main.py` calls `middleware.logging.setup_json_logging()`
   at import, which attaches an INFO `StreamHandler` (stderr) to the root logger. Every
   application `logger.info` therefore lands on stderr as JSON — in addition to the app log that
   `logging_setup.configure_logging()` (lifespan, PR #1468) writes. Measured on the whole file
   (119 MB, 321 k lines): `Request completed` for healthy polls **78%**, httpx per-call lines +
   apscheduler job lines **17%**, everything else (incl. all real WARNINGs) **5%**.
2. **stdout = uvicorn's access log** (no `--no-access-log`; uvicorn configures its own loggers) plus
   the `ExecStartPre` `sync_zoe_self.sh` banner printed on each of ~168 starts. 1.12 M lines, 94% of
   them the same eight kiosk poll paths.
3. **The app log** is the *intended* sink and does rotate (6 × 10 MB) — but at the old volume that
   was only about a day of history. Its `Request completed` lines carry no path/status (the formatter
   drops `extra=`), so they were pure bulk.

The seven polls (≈ one per 2–5 s per open kiosk tab) are `GET /api/ui/actions/pending`,
`POST /api/ui/state/sync`, `GET /api/voice/announcements`, `GET /api/system/display/preferences`,
`GET /api/skybridge/timers`, `GET /api/ha/entities`, `GET /api/panels/<id>/config` (+ `/health`,
`/api/music/now-playing` on stdout).

## 3. What was fixed (one PR)

| Class | Fix | Test (goes red when the fix is reverted) |
|---|---|---|
| Healthy polls flood stderr + app log | `middleware/logging.py`: `is_quiet_poll()` — fast `<400` polls log at DEBUG; `>=400` or `>=1 s` stays INFO. Escape: `ZOE_LOG_QUIET_POLL_PATHS=off` | `test_log_noise_2026_10_04.py::test_middleware_logs_healthy_poll_at_debug_and_trouble_at_info`, `…healthy_poll_is_quiet`, `…trouble_on_a_poll_path_is_never_quiet`, `…non_poll_paths_are_never_quiet` |
| Same polls flood stdout (uvicorn access) | `QuietPollAccessFilter` on `uvicorn.access`, installed by `configure_logging()` (fails open) | `…access_filter_drops_healthy_polls_only`, `…configure_logging_quiets_chatty_libraries_and_is_idempotent` |
| httpx / apscheduler-executor per-call INFO (and full URLs incl. household coordinates) | `logging_setup.quiet_chatty_loggers()` caps `httpx`, `httpcore`, `apscheduler.executors.default` at WARNING. Escape: `ZOE_LOG_CHATTY_LIBS_LEVEL=INFO` | `…configure_logging_quiets_chatty_libraries_and_is_idempotent`, `…chatty_level_env_restores_the_lines` |
| Poorly-rotated systemd `append:` logs (150 MB threshold, lossy tail rewrite) | `scripts/maintenance/rotate_service_logs.py` (stdlib copytruncate, ≥ 50 MB, keep 4 segments, archive built + verified before anything is replaced, flock, never touches the app log) + `zoe-log-rotate.{service,timer}` templates (operator install) + tracked `20-capture-output.conf` | `tests/unit/test_rotate_service_logs.py` (content-preserving, shift/keep bound, failed gzip leaves archive + live file untouched, O_APPEND writer survives and file not sparse, late-appended bytes carried over, symlink/app-log skipped, dry-run inert, templates exist) |
| Expected upstream outage logged as a ~5 KB traceback per poll (121 + 20 in one outage) | `log_throttle.log_upstream_failure()` — transport/status errors: ONE throttled line, no traceback; genuine bugs keep ERROR + traceback and are never throttled. Applied in `routers/panel_config.py` and `routers/ha_control.py` | `…upstream_timeout_logs_one_line_without_traceback`, `…genuine_bug_keeps_its_traceback…`, `…ha_entities_timeout_is_502…`, `…panel_config_ha_outage_returns_none…` |
| Reconnect-looping client floods the WARNING stream | `_enforce_ws_origin` logs through `log_throttled` (per origin+path, 60 s, carries "+N similar suppressed"); the 403 itself is unchanged | `…log_throttled_first_emits_repeats_fold_into_a_count`, `…ws_origin_rejection_goes_through_the_throttle`, key-space bound test |
| Routine success trace at WARNING that printed reply text | `expert_dispatch.py`: `EXPERT_ACTIVE`/`EXPERT_SHADOW` → INFO; `reply=<80 chars>` → `reply_chars=<n>` | `…expert_active_trace_is_info_and_carries_no_reply_text` |
| Credential material in the log | `auth.py`: `Invalid session: id_digest=<sha256[:8]> len=<n>` replaces the 20-char token prefix | `…invalid_session_log_never_contains_the_token` |
| App log world-readable (0664) while the other logs are 0640 | `_PrivateRotatingFileHandler` chmods every segment 0640 (also after rollover) | `…app_log_is_not_world_readable` |
| Crash-loop watcher: 41,700 identical undated "healthy, +0" lines (2.3 MB) | `zoe_crash_loop_watch.py`: silent in steady state, speaks on any change and hourly, every line timestamped | `tests/unit/test_zoe_crash_loop_watch.py::test_steady_state_healthy_is_logged_hourly_not_every_tick`, `…heartbeat_returns_after_the_interval` |

Expected effect (computed by replaying the new predicate over the window's stderr, 10.1 MB):
healthy-poll lines 82.4% + chatty-library lines 13.5% disappear ⇒ **≈ 4% of the previous volume
(~3 MB/day instead of ~70 MB/day)**, and app-log history stretches from ~1 day to weeks. Failures,
slow requests, 4xx/5xx and all other loggers are untouched.

Flag posture: the poll/httpx quieting changes **log volume only** (no product behaviour), so it
ships ON with an `off` escape hatch instead of default-dark; four new knobs are registered in
`flag-inventory.*`: `ZOE_LOG_QUIET_POLL_PATHS`, `ZOE_LOG_QUIET_POLL_SLOW_MS`,
`ZOE_LOG_CHATTY_LIBS_LEVEL`, `ZOE_LOG_REPEAT_WINDOW_S`. Voice-path files (`routers/voice_tts.py`,
`routers/memories.py`, …) were deliberately **not** touched, so this PR needs no replay-gate run.

## 4. What remains

**Operator (exact commands in `incident-runbook.md` §23):** retire the old `zoe-logs-rotate` pair, then install + start `zoe-log-rotate.timer`
and run it once (rotates the 119 MB / 86 MB files without a restart); restart zoe-data once so the
volume fixes load; delete the two stale `zoe-data-801{1,2}.log`; `chmod 640` the existing app-log
segments.

**Not fixed — deliberately or not reachable from code:**

* *Raw content in logs elsewhere.* `routers/voice_tts.py` still logs `transcript=%r` (80 chars),
  `ROUTER_SHADOW text=%r`, `SKYBRIDGE TIMING … reply=%r`; `expert_dispatch`/`zoe_agent`/
  `memory_extractor` log memory text on `MEMORY_QUALITY_REJECT`/`MEMORY_STORE_DROPPED`. These are
  voice-path files (replay-gated) — a follow-up PR should redact them behind a
  `ZOE_LOG_REDACT_CONTENT` flag and run the gate. Until then treat every log file as private.
* *onnxruntime GPU-discovery warning* (#4): emitted by native code at session creation; no supported
  runtime knob for the Moonshine-owned session. Two lines per restart; harmless.
* *Untracked-config drift:* `sync_zoe_self.sh` (ExecStartPre) rewrites `CAPABILITIES.md` in the live
  checkout on **every** start ("capabilities_md: updated"), which is why the live tree shows
  `M CAPABILITIES.md` and has to be handled before every `merge --ff-only`.
* *Client bugs seen only as 4xx:* a UI client sends `X-Session-ID: null` (literal), and the browser
  hits `/api/journeys`, `/api/lists` (404) and `HEAD /api/weather/current` (405) — verification
  harness probes against routes that do not exist; not a server defect.
* *Earlier-today faults (§1b):* the 11:13 Postgres recovery window, the 06:00 `platform_health_check`
  exit-1, Multica 400s and the 11:00 `music discovery batch rc=2` were not investigated here.
* *Unverified:* why 77 uvicorn 403s correspond to 67 app-level WARNINGs (10 early rejections around
  restarts were not matched line-by-line); the Telegram media router's load cannot be confirmed from
  the log because it logs nothing on success.
