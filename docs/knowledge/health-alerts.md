---
type: Runbook
title: Health-check owner messages and planned windows
description: What each zoe_health.py check means in household terms, the exact shape of the Telegram messages it sends, the dropping-out rule, the planned-window marker (maintenance_window.sh open/close) and which probes a window skips, and the crash-loop watch's matching words.
tags: [health, alerts, telegram, owner, maintenance-window, crash-loop, ops]
timestamp: 2026-11-16T00:00:00Z
---

# Health-check owner messages and planned windows

`scripts/maintenance/zoe_health.py` (`zoe-health.timer`, every 5 minutes) sends its messages to the owner over Telegram
through `zoe_crash_loop_watch.deliver`. Until 2026-11-16 they read like log lines; the owner asked for them to be "a bit
more human, not like reading a log".

## Message shape

- Plain sentences: what is wrong in household terms, what it means, since when, what to do and what Zoe is doing. A
  short technical code goes in brackets at the END (service name + `no-answer`, `error-reply`, `not-ready`, ...);
  never a port, an exception class, `DOWN:` or a path under home.
- Usually ONE message per run (several new problems become one list); if a run's news would not fit the delivery
  limit (1000 characters) it is split into complete messages, at most 4, and past that the last one ends "And N more to
  tell you next time: <plain names>" and those stay pending. A transition counts as told only when its message was
  DELIVERED. A message the outbox merely QUEUES is not delivered: sending stops, and while it stays queued later runs
  send nothing new (the outbox keeps one entry per code, so a new message would replace it); once it leaves the outbox
  its checks count as told and the rest follow. ONE recovery, only for something alerted, saying how long
  it lasted. Two consecutive failures still needed before any alert.
- Dropping out: the 3rd alerted drop of a check within 6 h is said once as a summary; then only a drop longer than an
  hour is told, plus one closing line after 6 h with no new drop (state: `incidents`, `flap`, `flap_n`).
- Deterministic English, no model call. Delivery goes through the same outbox (`send_run`, `undelivered`); an undelivered
  summary is retried like an undelivered outage alert. Exact texts: `tests/unit/test_zoe_health_alerts.py`.

## What each check means

| check | plain name | what it means | down starts | recovered |
|---|---|---|---|---|
| zoe-data | the main part of me | nothing (panel, app, chat) can get an answer | "I'm not reachable at all." | "I'm reachable again." |
| llama-server | my thinking | can hear, cannot answer | "I can't think right now." | "I can think again." |
| flue-zoe-brain-2x | my conversations | no reply, or an apology | "I can't hold a conversation right now." | "I can hold a conversation again." |
| kokoro-tts | my voice | hears and answers on screen, cannot speak | "I've lost my voice." | "My voice is back." |
| functiongemma-router | my action picker | slower, less reliable at choosing the action | "I'm slow at working out what you want." | "I'm quick at working out what you want again." |
| postgres | my memory | cannot remember or look anything up | "I can't reach my memory." | "I can reach my memory again." |
| containers | my background helpers | most things work, one helper is unhealthy | "One of my background helpers is unwell." | "My background helpers are all healthy again." |
| failed units | my scheduled jobs | a job (for example a backup) did not finish | "One of my scheduled jobs did not finish." / "My latest backup did not finish." | "My scheduled jobs are all finishing again." |
| night-window timer | my overnight routine | no overnight thinking and tidying tonight | "My overnight routine is switched off." | "My overnight routine is switched back on." |
| restore drill | my practice restore | backups unproven until a practice restore passes | "My practice restore from backup has stopped running." | "My practice restore is running again." |
| disk / | my storage | cannot save anything new if it fills | "I'm running out of storage." | "I have enough storage again." |
| memory | my working memory | may be slow, may miss a request | "I'm running short of working memory." | "My working memory is back to normal." |
| forget ledger | permanent forgetting | a forget hides, but is not yet permanent | "Forgetting isn't permanent right now." | "Forgetting is permanent again." |
| owner alerts | my messages to you | Telegram refuses them; they are held and retried | "My messages to you aren't getting through." | "My messages are getting through to you again." |

The spine services' words are in `zoe_crash_loop_watch.PLAIN_WORDS`, which the crash-loop watch uses as well. The
other checks' words are in `zoe_health.CHECK_WORDS`. The exact full texts are pinned in
`tests/unit/test_zoe_health_alerts.py`.

## Planned windows

The landing probe window stops `kokoro-tts` for up to ~15 minutes (`scripts/landing/landing_lib.sh`
`landing_probe_window`); the night window already had its own marker. Daytime windows had none, so each became an
outage plus a recovery.

- **Marker:** `~/.zoe/maintenance-window/OPEN` (dir: `MAINTENANCE_WINDOW_DIR`, like `NIGHT_DIR`), mode 0600, JSON
  `{"until": <epoch>, "kind": "test" | "replay" | "deploy" | "other"}` and nothing else.
- **Skipped while valid:** `WINDOW_TAKES = ("kokoro-tts",)`, the only service a planned window stops. Brain, zoe-data,
  database, disk, memory and the rest keep alerting.
- **Ignored:** unreadable, any other shape, unknown kind, non-number `until`, expired, or `until` more than 2 h ahead.
- **Overrun:** `until` passed under 2 h ago and the voice is still off: the first alert names the window. A failure
  counted just before the window starts afresh after it.
- **Writer:** `scripts/maintenance/maintenance_window.sh open <kind> <minutes>` (1-120, atomic, refuses more) before
  the stop, `scripts/maintenance/maintenance_window.sh close` after the start. Until the window tooling calls these,
  the noise continues.

## The crash-loop watch

`zoe_crash_loop_watch.py` sends four messages: zoe-data or another unit looping, and each one recovered. They use the
same plain words, with the unit name, restart count and state in brackets at the end. They keep the red or green mark
at the start, because `_hand_off` and the stored `pending` alert key on it.
