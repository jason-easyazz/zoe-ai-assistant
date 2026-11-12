---
type: spec
title: Household notifications on the wall panel - a dot on the orb, a card on press, one line at the end of a turn
description: ZOE_NOTIFICATIONS=off|on (default on) - the existing notifications table drives the orb dot, a private card on the panel when the orb is pressed, and at most one "you also have N notifications" line on a verified member's own turn; Zoe creates them herself (first creator - confirm the pendant wearer's voice).
tags: [notifications, panel, orb, voice, privacy, spec]
timestamp: 2026-11-12T00:00:00Z
---

# Household notifications on the wall panel

## Acceptance - true when done (owner's words)

- There is a dot on Zoe's orb when I have notifications.
- I press the orb and she lists the new notifications, for example "please confirm this voice".
- When I use Zoe, at the end of what I am doing she can say "you also have 3 notifications, you can check them on the panel or I can read them to you now".
- Zoe can create a notification herself (first use: she keeps hearing a regular voice on the pendant and needs me to confirm whose it is).
- Do not overwhelm me.

## Design

One flag, `ZOE_NOTIFICATIONS` = `off` | `on`, default `on`, read per call. Off = the inbox response, the pull reply, every voice
reply and the brain stream are what they were before this feature (the stream is not even wrapped; the card routes are absent).

| piece | where | what it does |
|---|---|---|
| store | `services/zoe-data/notifications_store.py` | `create` (deterministic id, `ON CONFLICT DO NOTHING`), `unread`, `unread_count`, `mark_read`; sensitivity rides in the row's `data` JSON (`{"sensitive": true}`), so no migration |
| dot | `routers/proactive.py` `/api/proactive/inbox` | a member session gets `notify` (its OWN unread count); any other caller (the kiosk guest panel, a device token, a degraded session) gets only `notify_any`, a bare boolean; `dist/touch/home.html` paints the existing dot when the proactive count, `notify` or `notify_any` says so |
| card | `notifications_store.card` / `push_card` | a private `voice_card` of kind `notifications`, minted with `panel_review_cards.mint`, drawn by the existing `js/touch-review-cards.js`; taps go to `POST /api/panel/notifications/act` (`routers/notifications.py`, `X-Panel-Card` header, `panel_review_cards.authorize`) |
| orb press | `fast_tiers._pull_tier` -> `notifications_store.pull_addendum` | the press runs today's spoken "what's up" pull; when the turn's member has unread notifications the card goes to that panel and one sentence follows ("And three notifications on the screen.") |
| tail | `services/zoe-data/notification_tail.py` | `note_voice_turn` (voice lane), `append_stream` (brain stream, `zoe_flue_client.py`), `append_to_reply` (deterministic tiers, `routers/voice_tts.py`), `tier` ("read them" / "later") |
| first creator | `notifications_store.notify_wearer_proposals` | the pendant's wearer print has open proposals and no confirmed print: one `wearer_confirm` notification per batch of proposals ("Please confirm this voice") |

Card rows: a plain notification shows its title and a one-line message with **Done**; a `wearer_confirm` shows one row per open
proposal (at most 3) with up to 3 short lines of what was said there, and **That's me** / **Not me**, which call
`omi_repass.confirm_wearer` / `reject_wearer`. When no proposal of the batch is left open the notification is marked read.

## Privacy rules

- The dot's count (`notify`) is for the request's own member session only. Another member's session reads that member's own rows.
  A guest, a device token or a degraded session never gets a count.
- **The kiosk dot - the privacy trade.** The household uses the panel by voice on the kiosk guest session; a PIN sign-in is rare.
  So the unauthenticated panel poll gets one boolean, `notify_any`: true when ANY household member (never a guest) has at least
  one unread notification that is not sensitive. It is never a count, never whose, never a title. The trade, stated plainly: anyone
  standing at the panel learns only that "someone here has something waiting" - not who, not how many, not what. Sensitive
  notifications never light the dot for that poll. Pressing the orb then runs the spoken pull, which is voice-verified, and only
  that verified member's own card is shown; for anyone else the press behaves exactly as before.
- The card is pushed only to the panel the member is at (`voice_session.send_to_panel`, never `all`), only for the turn's
  identified member (`panel_review_cards._member`: the voice session or a fresh PIN), and a tap is honoured only while that
  member's voice session is open there or with their own session.
- The tail and "read them" need a VERIFIED voice turn (speaker verdict True). Someone else answering "read them" gets the private line.
- A sensitive notification is counted aloud but never read: "One is private, it is on the panel."
- Proposal lines (what the pendant heard) are read only to build that member's card: never stored in a notification row, never
  logged, never in any other push frame. Logs carry counts and types only.
- A member erased since the creator was scheduled (`user_erase_gate`), or words naming a forgotten entity (tombstone / forget
  ledger), get nothing created.
- Forgotten means forever: `MemoryService.delete_user` calls `notifications_store.forget_user` (fail closed), which deletes EVERY
  notification row of the member, whoever wrote it, plus their tail-caps row, under the member's erase gate. `forget_reach` lists
  the table as reached. The entity forget ("forget Dana") does not scan notification titles.

## Do not overwhelm

- The tail rides only a reply to a turn the member started, after the reply finished with real text that does not end in a
  question; never on an emotional turn, in quiet hours, under `replay_isolation`, or on the pull's own reply.
- One appended line per reply across all tails: the transcript check goes first and wins.
- At most once every 4 hours and 3 times a household day per member, and only when an unread notification has not been announced
  yet (the orb press counts as announcing). The last-tail time and the day's count persist in one `app_settings` row per member
  (key `notification_tail:<member>`, no migration), so a restart does not reset them; the "already announced" set is in memory,
  so after a restart the same set may be announced once more, still inside the caps.
- "Read them" reads at most 3 titles, then "And N more on the panel." "Later" or silence leaves them on the panel.
- The card shows at most 8 rows.

## Adding a creator

Call `await notifications_store.create(user_id, "<type>", title, message, data, dedupe_id=<stable key>, sensitive=<bool>)`.
Pass `scheduled_at=user_erase_gate.generation(user_id)` from where a background job was scheduled. Keep the title short (it is
read aloud), keep owner-only content OUT of the row (build it into the card at render time, as the wearer creator does), and give
the type a card action in `notifications_store.card` / `act` only if it needs more than **Done**. A creator that reads another store
runs from `notifications_store.refresh` (throttled to once per 10 minutes per member).

## Not in this version

- The other hand-rolled writers of the table (`transcript_check._notify` and older ones) are not migrated to `create`.
- The entity forget does not reach notification titles (the member erase does).
