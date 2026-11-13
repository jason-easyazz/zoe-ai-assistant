---
type: Reference
title: Reminders on the phone (ZOE_REMINDER_PHONE_DELIVERY, flag-dark)
description: How a due reminder reaches its owner's phone through the channels that already exist (web push, the Telegram bot's loopback notify route), chosen per member, with owner-only, adults-only, quiet-hours, neutral-text and at-most-once rules; the preference keys, the route, the two UI pieces a later lane needs, and the owner's switch-on steps.
tags: [reminders, notifications, telegram, web-push, preferences, privacy]
timestamp: 2026-11-13T00:00:00Z
---

# Reminders on the phone

Gap audit 2026-11-13, lane C finding 3: the web-push server works but has no subscriptions, and Telegram was not a
reminder channel, so reminders never left the panel.

## How a due reminder is delivered (unchanged when the flag is off)

1. `proactive/triggers/reminder_scan.py` `schedule_due_reminder` schedules a `proactive_scheduled` row and an APScheduler
   job (`proactive/triggers/reminders.py` `schedule_reminder`).
2. `reminders.py` `_fire_reminder` claims the row (once), checks the reminder is still live, and calls
   `proactive/engine.py` `fire_notification(trigger_type="reminder")`.
3. `fire_notification` defers inside the household quiet hours, writes `proactive_pending`, runs the spoken panel lanes
   (`_maybe_speak_notification`, its own flag), sends web push to ALL of the member's `push_subscriptions`
   (`routers/push.py` `send_push_to_user`), and when no push went out writes an in-app `notifications` row and rings the bell.

## What the flag adds

`ZOE_REMINDER_PHONE_DELIVERY` = `off` (default) | `shadow` | `on`, read per call. The seam is `services/zoe-data/reminder_delivery.py`
`deliver(user_id, kind, title, body, *, dedupe_id, sensitive)`; `fire_notification` calls it for reminders only.

- `off`: the seam is never called. Byte-identical to before.
- `shadow`: the engine's web push runs as before; the seam also runs every rule and logs one `REMINDER_PHONE` line per
  channel (`would_send` or `skipped:<class>`). Nothing is sent, nothing stored.
- `on`: reminder web push goes through the seam (instead of the engine's direct push) and Telegram is added. The panel side
  (spoken lanes, in-app notification, bell) is unchanged. A member who has NOT opted in gets no reminder web push under `on`
  (before, any subscribed browser got the full text).

Rules (all tested in `services/zoe-data/tests/test_reminder_phone_delivery.py`):

- Owner only: the member's own `push_subscriptions` and own `user_prefs.telegram_id`; the bot also refuses an unlinked chat.
- Adults only: `enrollment_state.audience_of` must be `adult`; a child, guest, unknown role or failed lookup gets nothing.
- Quiet hours: the member's `communication.quiet_hours` (comm_prefs), else the household `ZOE_QUIET_START_HOUR` /
  `ZOE_QUIET_END_HOUR`. Phone channels are skipped (not deferred) inside the window; the panel still has the reminder.
- Sensitive: category `health` / `medical` / `private`, visibility `private`, health words in the title, or an unknown
  reminder = the phone shows "You have a reminder." unless the member set `full_text`.
- At most once per (reminder fire, channel): a claim row in `app_settings`, key `reminder_phone:<member>:<hash>:<channel>`,
  value = outcome class, written before the send inside `user_erase_gate.writing`. A definite failure releases the claim:
  a `retry` outcome, no web push reached, or an error raised before any transport call (`PRE_SEND_ERRORS`: a wrong
  argument, a missing module or key). A timeout or a transport error mid-send keeps it (never a double send). `MemoryService.delete_user` calls
  `reminder_delivery.forget_user` (inventory: `reminder_phone_ledger` in `forget_reach.py`). Rows are ids only; they grow
  by at most two per fired reminder of an opted-in member and are not pruned yet.
- Never raises; each channel is bounded (20 s); logs carry channel, outcome class and an 8-char hash, never text or ids.

## Preference and route

`user_prefs` key `reminder_delivery` = `{"channels": [], "full_text": false}`; channels from `telegram`, `web_push`.
Absent = none. No migration.

- `GET /api/user/profile/reminder-delivery` -> `{channels, full_text, telegram_linked, mode}` (own profile).
- `PUT /api/user/profile/reminder-delivery` with `{channels, full_text}` -> stored document. Own profile only; 403 for
  anyone who is not an adult member.

## What the UI lane needs (not built here)

1. Desktop Settings, Telegram section (`services/zoe-ui/dist/settings.html` `sec-telegram`): a "Send my reminders to my
   phone" toggle (PUT `channels: ["telegram"]`), shown only when `telegram_linked`, plus a "show the full text of private
   reminders" checkbox (`full_text`). Hide both for non-adults (the GET still works; the PUT returns 403).
2. A web-push subscribe prompt on the phone: a button that calls `window.zoePushNotifications.subscribe()` (it must run in a
   user gesture on iOS) and then adds `web_push` to `channels`. Unverified: iOS only allows web push for a site added to
   the Home Screen over trusted HTTPS, which may be why there are no subscriptions today.

## Owner switch-on steps

1. Link Telegram (desktop Settings, Telegram, scan the QR) and opt in with the PUT above (or the toggle once built).
2. Set `ZOE_REMINDER_PHONE_DELIVERY=shadow` in `.env`, restart zoe-data, set a test reminder, and read the
   `REMINDER_PHONE` lines in `~/.zoe-logs/`.
3. Set it to `on`, restart, and confirm one test reminder arrives once on the phone.

Owner decisions open: which channel to suggest by default (Telegram works today with no phone setup); whether
quiet-hours phone skips should be deferred to the end of the window instead; whether alarms and other proactive lines
should use the same seam (only reminders do now).
