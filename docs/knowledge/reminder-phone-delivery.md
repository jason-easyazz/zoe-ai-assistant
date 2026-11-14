---
type: Reference
title: Reminders on the phone (ZOE_REMINDER_PHONE_DELIVERY, flag-dark)
description: How a due reminder reaches its owner's phone through the channels that already exist (web push, the Telegram bot's loopback notify route), chosen per member, with owner-only, adults-only, quiet-hours, neutral-text and at-most-once rules; the preference keys, the route, the desktop Settings toggle and the "Also send to this browser" web-push control (both built), the lab rehearsal, and the owner's switch-on steps.
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
- Neutral text by default: the phone shows "You have a reminder." unless the member set `full_text`, for every reminder
  that is not positively household-visible (visibility `family`, the one value the reminders read filter shares; the
  schema default `personal`, empty and unknown values are all neutral), and for a household one with category
  `health` / `medical` / `private` or health words in the title. A lock screen shows previews to anyone nearby.
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

- `GET /api/user/profile/reminder-delivery` -> `{channels, full_text, telegram_linked, mode, eligible}` (own profile).
  `eligible` comes from the same check the PUT uses for its 403 (`routers/user_profile.py` `_phone_delivery_eligible`:
  `audience_of` says `adult`); a failed or unknown lookup is `false`.
- `PUT /api/user/profile/reminder-delivery` with `{channels, full_text}` -> stored document. Own profile only; 403 for
  anyone who is not an adult member.

## The UI

1. BUILT: desktop Settings, Telegram section (`services/zoe-ui/dist/settings.html` `sec-telegram`, component
   `dist/js/reminder-phone-settings.js`, global `ZoeReminderPhone`; desktop only, no touch page loads it). A "Send my reminders
   to my phone" toggle and a "Show the full text of private reminders on my phone" checkbox (one line says a private reminder
   otherwise arrives as "You have a reminder."), shown only when the GET says `telegram_linked` AND `eligible === true` (a
   child, an unknown member or an older server without the field sees nothing); re-read whenever the page draws the Telegram
   link status. The toggle adds or removes `telegram` only and keeps any `web_push` already chosen (the browser control below
   owns that channel). A 403 on the PUT stays as the backstop: it reverts the control and replaces the section with one
   sentence ("for adult members").
   Nothing says "Saved." before the server answered; the controls are disabled while saving; a failed PUT reverts to the last
   stored state, says so, and re-reads the GET. When `mode` is not `on` a line says the choice is kept for when phone
   reminders are switched on. Only fixed strings are rendered. Pinned by `dist/test_reminder_phone_settings.js`
   (CI: `services/zoe-data/tests/test_reminder_phone_settings_ui_harness.py`); the GET/PUT agreement by
   `services/zoe-data/tests/test_reminder_phone_settings_api.py`.
2. BUILT: "Also send to this browser", in the same component and card. Shown only when this browser supports web push
   (service worker + PushManager + Notification, secure context) AND the GET says `eligible === true`; Telegram need not be
   linked. Checked = `web_push` is in `channels` AND this browser holds a push subscription (read with
   `getRegistration('/')` + `getSubscription()`, which never prompts and never registers). The page never asks for
   notification permission on load: `Notification.requestPermission()` is called only from the member's click on this
   control and is the FIRST thing the click does (iOS drops a prompt that comes after an await). On: permission -> `GET
   /api/push/vapid-public-key` -> register `/sw.js` if this page has no registration (settings.html does not register it;
   other pages do) -> replace any old subscription -> `POST /api/push/subscribe {subscription: {endpoint, keys}}` -> the PUT
   with `web_push` added (other channels and `full_text` kept). A denied or dismissed prompt (or a browser already blocking
   Zoe) shows one plain line and changes nothing; a failed subscribe or store undoes the browser subscription; a failed PUT
   (or the 403 backstop) also deletes the stored row again. Off: `DELETE /api/push/subscribe {endpoint}` and the browser
   unsubscribe first, then the PUT without `web_push`; a failed DELETE changes nothing. It does NOT use
   `js/push-notifications.js` (that file subscribes and prompts on page load and is loaded by no page; Settings must not
   load it). `sw.js` was not changed: its existing `push` handler shows the notification.
   Known, not changed: while `ZOE_REMINDER_PHONE_DELIVERY` is not `on`, `fire_notification` pushes every proactive
   notification to ALL of a member's subscriptions, so subscribing a browser here means it gets those too until the flag is
   `on`. Unverified on a device: iOS allows web push only for a site added to the Home Screen over trusted HTTPS.

## Lab rehearsal (before the shadow step)

`services/zoe-data/tests/test_reminder_phone_rehearsal.py` runs the REAL `fire_notification` -> REAL `reminder_delivery` against
a scratch SQLite store with fake senders for four invented members (opted-in adult, adult not opted in, child, opted-in adult
in quiet hours) in each mode: off never calls the seam; shadow logs `would_send` / `skipped:not_opted_in` /
`skipped:not_adult` / `skipped:quiet_hours` per channel, sends nothing, writes no claim row, and leaves the engine's own effects
identical to off; on sends once per channel to the opted-in adult only, neutral text for a private or health reminder unless
`full_text`, and a second fire of the same id sends nothing. Every `REMINDER_PHONE` line is checked to carry classes and a hash
only.

## Owner switch-on steps

1. Link Telegram (desktop Settings, Telegram, scan the QR) and turn on "Send my reminders to my phone" just below it.
2. Set `ZOE_REMINDER_PHONE_DELIVERY=shadow` in `.env`, restart zoe-data, set a test reminder, and read the
   `REMINDER_PHONE` lines in `~/.zoe-logs/`.
3. Set it to `on`, restart, and confirm one test reminder arrives once on the phone.

Owner decisions open: which channel to suggest by default (Telegram works today with no phone setup); whether
quiet-hours phone skips should be deferred to the end of the window instead; whether alarms and other proactive lines
should use the same seam (only reminders do now).
