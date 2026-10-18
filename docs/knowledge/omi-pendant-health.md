---
type: Runbook
title: Omi pendant health - the heartbeat, the five warnings, the away back-off (ZOE_OMI_HEALTH)
description: What the pendant exposes, what the panel Pi reports to zoe-data, the stored row and the owner-scoped read, the warning thresholds, how a warning reaches the owner without speech, the retry back-off, and the exact operator deploy steps for the Pi.
tags: [omi, pendant, health, battery, ring, overwrite, runbook, p0-2]
timestamp: 2026-10-18T00:00:00Z
---

# Omi pendant health

**Status: built.** Gap audit P0-2 "loss is silent" ([omi-gap-audit-2026-10.md](../research/omi-gap-audit-2026-10.md)), P2-1 (retry
storm), P2-2 (spool never shrinks) and P2-5 (battery). The pendant's ring holds about 480 MiB, about 34 h of audio at the codec's
ceiling, and the firmware overwrites the oldest unread audio when it is full. Until now the overwrite count reached only the Pi's
journal, and last-sync and battery were tracked nowhere.

## What the pendant exposes (verified in the code, not guessed)

| Fact | Where it comes from | Used |
|---|---|---|
| Battery level, percent | the standard BLE **Battery Service** (`0x180F` / `0x2A19`), a plain GATT read. The plan records 93-94 % on the live unit and `check.py` already reads it | yes, every visit |
| Firmware version | Device Information `2A26` | yes |
| Ring fill | the ring `INFO` the engine already reads: read and write sequence, **capacity in records**, record size (444 B) | yes: `unread / capacity` |
| Stored bytes | derived: unread records x record size (the separate 16-byte status read is NOT needed) | yes |
| Overwritten records | `INFO.dropped_packets`, the firmware's cumulative count of records it overwrote before they were read | yes, as the absolute counter |
| Charging state / temperature | not exposed on this firmware path | no |

The legacy 3.0.18 file path has no `INFO`, so a legacy session reports battery and firmware only. The owner's unit is on 3.0.21 (ring).

## What the Pi reports

`omi-sync` posts a small JSON record to `POST /api/omi/device-status` (device token, the same one the uploads use) **after a visit, and
at least every 15 minutes while the pendant is away**. Numbers only: `reachable`, `consecutive_misses`, `last_sync_age_s` (an age, so
the Pi's clock never matters), `battery_pct`, `firmware`, `ring_fill_pct` (the END-of-session estimate, after the drain freed what Zoe
confirmed, so a successful catch-up is not reported as "80 % full"), `ring_fill_start_pct`, `ring_stored_bytes`, `ring_capacity_bytes`,
`dropped_total`. The endpoint is `extra="forbid"`: a field it does not name (audio, transcript, address, a note) is a 422.

"Overwritten since the last report" is **derived in zoe-data from the absolute counter**, not sent as a delta: a repeated report (a lost
200) adds nothing and a lost report loses nothing. The first report is a baseline (overwrites before Zoe watched are not news); a counter
that goes down means the pendant restarted its count.

## What zoe-data keeps

`omi_device_health` (migration **0056**): ONE row per pendant, overwritten by each report; no history. `GET /api/omi/health` returns the
caller's pendants with live-evaluated warnings, owner-scoped exactly like the recordings (a device token never reads; no admin override;
the pendant's CURRENT owner is re-checked at read time, so a reassignment hides the row from the previous owner at once). The erase
cascade deletes a member's rows.

## The warnings (they fire and clear from the data; nothing is acknowledged by hand)

| Kind | Fires when | Clears when |
|---|---|---|
| `overwrite` | the overwrite counter rose in the last **72 h** | 72 h pass |
| `no_sync` | the last clean sync is older than **36 h** while the Pi is reporting (away, or in range but not syncing) | a clean sync |
| `ring_fill` | ring fill after the last drain is above **75 %** | a later reading at or below 75 % |
| `battery_low` | battery below **20 %** | a later reading at or above 20 % |
| `bridge_silent` | the Pi has said nothing for **3 h** (sync service or panel down; "away" cannot be known, so this mutes `no_sync`) | the Pi reports |

The 36 h line sits just past the ring's ~34 h ceiling: by then audio is probably already being overwritten. Wake-on-sound stretches the
ring in wall-clock time, so in practice 36 h is "may be losing audio", not "is". The constants are at the top of `omi_health.py`.

## How a warning reaches the owner (never speech)

1. A **bell notification** (`notifications` row, type `omi_health`, the same path as the "N recordings synced" notice) the moment a
   warning becomes active; once per arrival, with a 6 h re-notify cooldown so a battery hovering at 19/21 % does not ring every cycle.
   A 10-minute sweep raises the time-based ones (a silent Pi, 36 h with no report at all).
   The bell row and the saved warning state are one decision: the row is written first with an id derived from (pendant, kind, episode), so a crash between the two can neither lose nor double the bell, and a heartbeat and the sweep are serialised per member (the same erase-gate lock that stops a racing heartbeat from recreating a forgotten member's row).
2. `GET /api/omi/health` for the Memories/Pendant UI (each warning carries `since`).
3. At most **one line** in the member's first-turn-of-the-day brief (`brief_first_turn.day_items`, fed by
   `morning_checkin._build_morning_context` -> `omi_health.morning_items`): "About their pendant ...", which Zoe mentions naturally if
   she speaks to them at all. No unprompted speech (the owner turned the spoken brief off 2026-09-29; `ZOE_PROACTIVE_SPOKEN` stays off and this adds no spoken path).

The struggle detector reads conversation turns only (`struggle_signals.SIGNALS`) and has no device-health path, so it is NOT used; the
bell + first-turn seams above are the codebase's existing health-to-owner route.

`ZOE_OMI_HEALTH=0` (default on) stops the warnings (no bell, no brief line, no sweep); reports are still stored and readable.

## The away back-off (P2-1) and the spool (P2-2)

A pendant that does not answer is retried at 30, 60, 120, 240, 480 s, then every **600 s** (`OMI_SYNC_AWAY_BACKOFF_MAX_S`), instead of
a flat 30 s (about 85 failed `hcitool lecc` attempts an hour, 256 journal lines). One answer resets it. A busy radio (Settings >
Bluetooth holding it) is not "away" and does not grow it. Cost: after the owner comes home the first sync can start up to 10 minutes
late. **Not built:** an immediate retry on a BLE advertisement. A passive scan competes for the single radio with `lecc` and the
Bluetooth agent, and adverts arrive once per 20-40 s at this range (audit P2-1), so it is not cheap; revisit if the 10-minute
latency matters.

The spool (`spool.sqlite3`) is compacted with `VACUUM` at service start and after a visit that uploaded, only when the free pages are at
least 16 MiB and at least half the file. No row changes; a crash mid-VACUUM leaves the original intact. The 252 MB / 99 % free file
shrinks to a couple of MB on the first start after the deploy.

## Operator deploy (NOT done by the PR; the Pi is yours to restart)

Order: zoe-data first, then the Pi. (The other order is safe too: an older zoe-data answers 404 and the Pi ignores it.)

1. **Jetson:** from a clean checkout of merged `main`, apply migration 0056 (`scripts/deploy/migrate.sh`, `alembic heads` must show one
   head), then restart zoe-data and poll `/health` (not `systemctl is-active`). `ZOE_OMI_SYNC_ENABLED` is already on live; the new routes
   appear with it. No env change is needed (`ZOE_OMI_HEALTH` defaults on).
2. **Pi (`zoe-pi`), from a git worktree of the merged commit, never the live checkout** (there is no `deploy_omi_sync.sh` in the repo;
   this is the `scripts/setup/deploy-pi-bluetooth.sh` pattern: copy with a timestamped backup, then restart):

   ```bash
   cd <worktree of merged main>
   ssh zoe-pi 'cp -a ~/.zoe-omi-sync/app/omi_sync ~/.zoe-omi-sync/app/omi_sync.bak-$(date +%Y%m%d-%H%M%S)'
   rsync -a --delete --exclude __pycache__ services/omi-sync/omi_sync/ zoe-pi:~/.zoe-omi-sync/app/omi_sync/
   ssh zoe-pi 'systemctl --user restart omi-sync.service; sleep 5; journalctl --user -u omi-sync -n 20 --no-pager'
   ```

   No new Python dependency (stdlib only). Restarting mid-drain is safe: the spool is the custody.
3. **Verify:** the Pi journal shows `omi-sync running` and (after a visit) no `health report not accepted`; as the pendant's owner,
   `GET /api/omi/health` returns the pendant with `reported_at` within 15 minutes. The first start also logs `spool compacted at start`.
4. **Rollback:** `ssh zoe-pi 'rm -rf ~/.zoe-omi-sync/app/omi_sync && mv ~/.zoe-omi-sync/app/omi_sync.bak-<ts> ~/.zoe-omi-sync/app/omi_sync && systemctl --user restart omi-sync.service'`.
   zoe-data needs no rollback (alembic `downgrade` drops the table; the feature degrades to "no health").

Pi-side knobs (`~/.zoe-omi-sync/.env.omi`, all optional): `OMI_SYNC_HEALTH` (default 1; 0 stops the reports),
`OMI_SYNC_AWAY_BACKOFF_MAX_S` (600), `OMI_SYNC_HEALTH_HEARTBEAT_S` (900, floor 60).

## Tests

`tests/unit/test_omi_sync_health.py` (Pi side: back-off cap and reset, busy radio, report contents, end-of-session fill, throttle,
spool compaction) and `services/zoe-data/tests/test_omi_health.py` (auth, owner scoping, every threshold on both sides of the line,
fire-and-clear, once-per-arrival bells, kill switch, erase cascade, migration). Source mutations to re-run are listed in each
file's docstring. Both are `ci_safe`.
