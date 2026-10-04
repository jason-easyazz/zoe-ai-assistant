---
type: Runbook
title: Music Assistant idle reap — stop when idle, start on demand
description: Flag-dark (ZOE_MA_IDLE_REAP, default OFF) lifecycle for the zoe-music-assistant container — a 10-min user timer stops it after ZOE_MA_IDLE_MIN with no playback, and the first music request docker-starts it again. Verified facts from the live box (start-to-HTTP ~3.5 s, restart policy, poll cadences, HA/MA coupling), the wake-command set, the idle decision and its guards, the YouTube Music re-auth risk, and the operator apply/rollback lines.
tags: [music, music-assistant, docker, memory, operations, flag-dark]
timestamp: 2026-10-04T00:00:00Z
---

# Music Assistant idle reap

Owner decision Q2 (2026-10-04): *"Stop Music Assistant when idle, start it on
demand."* Reclaim row 2 of the
[2026-10-03 memory-pressure profile](memory-pressure-profile-2026-10-03.md):
`zoe-music-assistant` holds ~128 MB anon + ~234 MB swap (≈0.5 GB resident at
its worst) and the last MA-driven playback before this was written was
2026-09-30. Same shape as the LiveKit on-demand reap in
`routers/voice_livekit.py`, split into two processes so the stop side keeps
working while zoe-data is down.

**Ships dark.** `ZOE_MA_IDLE_REAP` unset/`0` = byte-identical behaviour and an
inert timer.

## Verified facts (read-only, 2026-10-04)

| Fact | Evidence |
|---|---|
| MA serves HTTP **~3.5 s** after `docker start` | container log: started 11:53:13, "Webserver available" 11:53:16.7 (2026-09-27) |
| Docker **healthcheck is not the start signal** | `wget /info`, `interval 30s`, `start_period 60s` — first tick can land 30 s after HTTP is up; `ensure_running` polls `/info` directly, bounded by `ZOE_MA_START_TIMEOUT_S` (25 s) |
| `restart: unless-stopped` | a `docker stop` sticks across daemon restarts and reboots until `docker start` — after a reap MA stays down at boot until the first music request (or `docker compose up`) |
| `zoe` can drive docker | uid 1000 is in group `docker` (`docker ps` already works unprivileged) |
| Player/queue state over HTTP | `POST /api {"command":"players/all"}` → `state`/`playback_state` (`idle`/`playing`/`paused`); `player_queues/all` → `items`, `elapsed_time_last_updated` (epoch). Same commands the client already uses |
| **Reads are continuous** | the panel (`touch/home.html`) polls `/api/music/now-playing` every 5 s; `music_history.observe_once` polls `recently_played_items` every 300 s. Waking on reads would restart MA within seconds of every reap |
| Voice `music_play` does **not** touch MA | `intent_router._execute_music_intent` posts to the HA bridge (`/devices/control` → HA `media_player`). The MA paths are the panel/Skybridge (`music_service.resolve_music`) and `routers/music.py` |
| HA ↔ MA coupling | **none on this box**: HA has 0 `music_assistant` config entries and MA lists no `hass` provider. Nothing on HA's side breaks while MA is down; if the integration is ever added, HA's MA integration reconnects on its own (its retry loop) and would also become a wake candidate |
| "Zoe Panel (AirPlay)" is an MA player | `up88a29e0a953f`, provider `universal_player` (shairport on zoe-pi); it is a *target*, not a wake source — a reaped MA simply does not advertise it until started |

## How it works

**Start (`services/zoe-data/ma_ondemand.py`, called from `music_service`).**
`_ma_response` wakes on `_MA_WAKE_COMMANDS` only — `music/search`,
`player_queues/play_media`, playlists library/tracks/create, favourite add,
provider setup/reconfigure/save. Every mutating service entry point
(transport, seek, transfer, group/ungroup, play, queue edits, playlist add,
favourite add/remove, don't-stop, `resolve_music` for every action but `status`,
and `_ma_api_for_write()` — the credential-write funnel) calls `ensure_running()`
first. The wake: touch `activity` + `inflight` → under the shared `flock` (the one
the reaper holds across stamp+stop): honour a cached "MA answered" only if it is
newer than the reaper's `stopped` stamp and < 30 s old → else `GET /info` → else
`docker start` (via `async_subprocess.run_to_completion`, never a loop-thread
fork) + poll `/info` → log `MA_REAP start latency_ms=…`. Failure returns False and the existing "music
isn't available" degrade path applies — never a broken turn.

**Stop (`scripts/maintenance/ma_idle_reap.py`, `zoe-ma-idle-reap.timer` every
10 min).** `decide()` keeps the container unless every guard passes: flag on,
running, MA API readable, no `inflight` stamp younger than
`ZOE_MA_INFLIGHT_GRACE_S` (120 s), no player `playing`, no `paused` player with a
queue, newest of (`activity` stamp, MA per-queue `elapsed_time_last_updated`)
older than `ZOE_MA_IDLE_MIN` (45), local hour outside `ZOE_MA_REAP_QUIET_HOURS`
(`HH-HH`, optional). Then `docker stop` under the same lock, re-checking the
inflight stamp inside it. Log line: `MA_REAP stop idle_min=…`. Dry run against
the live box (2026-10-04, no `--execute`): `would-stop idle_min=5394`.

**Cold-start cost after a reap:** one music request pays ~4 s (start + HTTP)
plus MA's provider load. Subsequent requests are warm.

## ⚠ Product risk — YouTube Music re-auth on restart

[music-ytdlp-js-runtime.md § Re-auth risk](music-ytdlp-js-runtime.md#-re-auth-risk--read-before-restarting-music-assistant):
MA's YouTube Music provider re-runs its Premium check **on every provider
load**, and a failed check leaves it `needs_attention` (search returns nothing)
until the panel Reconnect (QR → phone). Credentials survive a restart
(`auth.db` in the bind mount); the risk is the check itself. A reap turns "a
restart every few weeks" into "a restart on every idle gap", so **enable only
when YouTube Music is either not configured (true on 2026-10-04: MA lists no
`ytmusic` provider) or has been observed to survive a plain `docker restart`.**
Verify before flipping: `docker restart zoe-music-assistant` once in a quiet
window, then `docker logs --since 5m zoe-music-assistant | grep -i "ytmusic\|provider"`
must show no `needs_attention`/`LoginFailed`.

## Apply (operator)

```bash
# 1. flag — ONE home, read by zoe-data AND the timer's EnvironmentFile
echo 'ZOE_MA_IDLE_REAP=1' >> ~/assistant/services/zoe-data/.env
# optional: ZOE_MA_IDLE_MIN=45  ZOE_MA_REAP_QUIET_HOURS=17-23  ZOE_MA_START_TIMEOUT_S=25
# 2. zoe-data must re-read its env (normal deploy restart; poll /health after)
systemctl --user restart zoe-data && until curl -fsS localhost:8000/health >/dev/null; do sleep 2; done
# 3. timer
mkdir -p ~/.config/systemd/user ~/.zoe-logs
cp ~/assistant/scripts/setup/systemd/zoe-ma-idle-reap.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now zoe-ma-idle-reap.timer
# 4. verify: a dry run prints the decision without stopping anything
ZOE_MA_IDLE_REAP=1 /usr/bin/python3 ~/assistant/scripts/maintenance/ma_idle_reap.py
tail -f ~/.zoe-logs/ma-idle-reap.log          # MA_REAP keep … / MA_REAP stop idle_min=…
grep MA_REAP ~/.zoe-logs/zoe-data*.log | tail  # MA_REAP start latency_ms=…
```

Note: the timer runs the script with `/usr/bin/python3` and `EnvironmentFile`
= `services/zoe-data/.env`; without `MUSIC_ASSISTANT_TOKEN` there MA is
"unreadable" and the reaper keeps (visible in the log) — fail-safe, not silent.

## Rollback

```bash
systemctl --user disable --now zoe-ma-idle-reap.timer
sed -i '/^ZOE_MA_IDLE_REAP=/d' ~/assistant/services/zoe-data/.env
systemctl --user restart zoe-data
docker start zoe-music-assistant   # if a reap left it stopped
```

## Tests

`services/zoe-data/tests/test_ma_ondemand.py` (fake docker + HTTP: flag-off
touches nothing, start path, bounded timeout, docker failure, reads never
wake) and `tests/unit/test_ma_idle_reap.py` (pure `decide()`: each guard's
negative control + "all guards pass ⇒ stop"; CLI dry-run never stops).
Both `ci_safe`.
