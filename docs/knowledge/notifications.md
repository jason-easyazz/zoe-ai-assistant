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

Added 2026-11-24 (owner: "press the orb and see the notifications, without having to say anything, and be able to clear them and
them actually stay cleared, or click on them and them actually do something"):

- I press the orb when its dot is lit and the list is on the screen. I did not have to say anything, and the mic did not open.
- I press the orb again (or say "Hey Zoe") and she listens, as always.
- I tap Clear on one and it is gone. It is still gone after the panel reloads, after a minute, and tomorrow.
- I tap Clear all and every one of them is gone, the ones that did not fit on the screen too. The dot goes out straight away.
- "Reminder created", "Reminder snoozed" and "Reminder done" are not notifications. They never fill my list.
- I tap the words of one and something happens: the matching screen opens, or the row opens out to its full words.
- Anything private is one row that says so. A voice ("what's up?") opens it; a tap by whoever is standing there does not.

## Design

One flag, `ZOE_NOTIFICATIONS` = `off` | `on`, default `on`, read per call. Off = the inbox response, the pull reply, every voice
reply and the brain stream are what they were before this feature (the stream is not even wrapped; the card routes are absent).

| piece | where | what it does |
|---|---|---|
| store | `services/zoe-data/notifications_store.py` | `create` (deterministic id, `ON CONFLICT DO NOTHING`), `unread`, `unread_count`, `mark_read`; sensitivity rides in the row's `data` JSON (`{"sensitive": true}`), so no migration |
| dot | `routers/proactive.py` `/api/proactive/inbox` | a member session gets `notify` (its OWN unread count); any other caller (the kiosk guest panel, a device token, a degraded session) gets only `notify_any`, a bare boolean; `dist/touch/home.html` paints the existing dot when the proactive count, `notify` or `notify_any` says so |
| card | `notifications_store.card` / `push_card` | a private `voice_card` of kind `notifications`, minted with `panel_review_cards.mint`, drawn by the existing `js/touch-review-cards.js`; taps go to `POST /api/panel/notifications/act` (`routers/notifications.py`, `X-Panel-Card` header, `panel_review_cards.authorize`) |
| orb press (no voice; `notify_touch` in the inbox poll) | `POST /api/panel/notifications/open` (`routers/notifications.py`) -> `notifications_store.card` / `panel_card` | the press of a dot lit by notifications answers the card to THAT request (nothing pushed, nothing spoken, no mic): a member's own session gets their own card; the panel itself (the kiosk guest that `panel_principal.is_panel_guest` accepts, flag `ZOE_NOTIFICATIONS_PANEL_TOUCH`, default on) gets the panel's own card; anyone else 403. `home.html` `orbList` draws it through the same `zoe:voice-card` event. With the list open, or with no dot, the press listens |
| spoken pull | `fast_tiers._pull_tier` -> `notifications_store.pull_addendum` | "what's up?" (and the press of a dot lit by a selector item) runs today's spoken pull; when the turn's member has unread notifications the card goes to that panel and one sentence follows ("And three notifications on the screen."). With `ZOE_PROACTIVE_SELECTOR` off (its default) the pull answers nothing, so a VERIFIED member's voice turn gets the notification sentence alone (`fast_tiers._notifications_only_pull`); never a guest, an unverified voice or a dry replay |
| tail | `services/zoe-data/notification_tail.py` | `note_voice_turn` (voice lane), `append_stream` (brain stream, `zoe_flue_client.py`), `append_to_reply` (deterministic tiers, `routers/voice_tts.py`), `tier` ("read them" / "later") |
| first creator | `notifications_store.notify_wearer_proposals` | the pendant's wearer print has open proposals and no confirmed print: one `wearer_confirm` notification per batch of proposals ("Please confirm this voice") |

**The panel's own card** (`panel_card`, card member `PANEL_MEMBER`, `private: false`, `touch: true`). Nobody has been identified, so
each kind is judged by one question - is it addressed to one member, or is it a notice about the household's kit?

| kind | decided | on the panel's card |
|---|---|---|
| `omi_health`, `omi_sync`, `panel_speaker` (`PANEL_DEVICE_TYPES`) | a device notice: fixed text about a pendant's battery / storage, a finished sync, the panel's speaker ID; nothing of a member's in it | words, and anyone at the panel may **Clear** it (and Clear all takes them) |
| `ops_alerting_broken` (`PANEL_WORDS_ONLY`) | fixed text about Zoe's alert bot, but it is the admins' second channel for an alert that could not reach them | words, **no Clear** from the panel |
| `wearer_confirm`, `transcript_check` (`PANEL_TITLE_ONLY`) | an ask addressed to ONE member (their voice, their transcripts) | the **title alone** - never the stored message, never what the pendant heard - no Clear (dismissing it is that member's **Not now** / own answer); a tap asks for a voice |
| every other kind (a fired reminder's words, a note Zoe wrote, `distress_handoff`, any new type) | a member's own | ONE row, "Some are private", no count; a tap opens the mic |

Never whose; sensitive rows are absent, as they are from the kiosk dot. Only a device notice is in the card's token at all, so a
forged tap on any other row is 404. Its taps are honoured only for the panel itself (`_require_card` applies the panel rule;
`panel_review_cards.authorize` refuses a panel token everywhere else). A new type is locked until it is placed in one of the
three sets: fail closed.

**The flag.** `ZOE_NOTIFICATIONS_PANEL_TOUCH` = `on` (default) | `off`, read per call. Off = the press is exactly the old one:
`/api/panel/notifications/open` answers 404 for every caller (a member's own session included), the inbox poll carries no
`notify_touch`, and the page - which learns the flag only from that poll it already makes - never asks `/open`, so the mic opens
with no round trip first.

**Clear stays cleared.** Clear = `delivered = 1`; every read filters on it. What used to bring rows back, and the rule now:

- RECEIPTS (`RECEIPT_TYPES`: `reminder_created` / `reminder_snoozed` / `reminder_acknowledged`, one new row from
  `reminder_service` for every reminder made, snoozed or ticked off) are not notifications: `unread`, `unread_count`, the kiosk
  boolean, the card and the spoken tail all leave them out. They piled up without end (the live tail logged its 50-row read cap),
  so a cleared card of eight was followed by the next eight and the dot never went out.
- **Clear all** (`act` `clear_all`, `notifications_store.clear_all`) is ONE update over every row the card's audience may clear -
  no read window, so the rows past the eight and past any query cap go too: a member's card all of their own (old receipts too,
  never a "confirm this voice" ask, which has its own **Not now**), the panel's card the household's device notices only.
- `COLLAPSE_TYPES` (`omi_sync`) are folded BY KIND in SQL (`_folded`): the newest row stands for every unread row of that kind
  of that member ("and 129 earlier", counted over the table), and its Clear is one update over the kind (`clear_kind`), not over
  the rows a card happened to read. (The live backlog was ~1,600 unread sync notices: one Clear now takes them all.)
- The wearer ask: a cleared or answered ask is not asked again when the batch only SHRANK (a proposal expired or was decided -
  a new batch hash, nothing new to ask). A proposal never asked about is a new ask.
- The page re-reads the dot the moment a row is cleared (`zoe:card-acted`), not at the next minute's poll.
- A member's card opened by a press is honoured with their own session, so its taps no longer die with a voice session.

**A tap on a row's words** (`open` / `detail` on the item, `zoe:card-open` in the page): `data.open` when the creator named an
estate screen (`SCREENS`), else by kind (`OPEN_BY_TYPE`): `reminder` -> Reminders, `transcript_check` -> the checks screen,
`omi_health` / `omi_sync` / `panel_speaker` -> Settings. EVERY row does one of the two: a kind with no screen
(`ops_alerting_broken`, `distress_handoff`, a plain note however short) carries `detail` and opens out to its full words. On the panel's own card a member-private screen is never the target: the tap
opens the mic instead. No creator writes calendar, list, message or timer notifications today; when one does it names its
screen in `data.open`.

Card rows: a plain notification shows its title and a one-line message with **Clear**; a `wearer_confirm` shows one row per open
proposal (at most 3) with up to 3 short lines of what was said there, and **That's me** / **Not me** / **Not now**, the first two of which call
`omi_repass.confirm_wearer` / `reject_wearer`. When no proposal of the batch is left open the notification is marked read.

## Privacy rules

- The dot's count (`notify`) is for the request's own member session only. Another member's session reads that member's own rows.
  A guest, a device token or a degraded session never gets a count.
- **The kiosk dot - the privacy trade.** The household uses the panel by voice on the kiosk guest session; a PIN sign-in is rare.
  So the unauthenticated panel poll gets one boolean, `notify_any`: true when ANY household member (never a guest) has at least
  one unread notification that is not sensitive. It is never a count, never whose, never a title. The trade, stated plainly: anyone
  standing at the panel learns only that "someone here has something waiting" - not who, not how many, not what. Sensitive
  notifications never light the dot for that poll. A dot lit ONLY by `notify_any` proves no one, so pressing it never runs the
  voiceless `/pull` (the server would refuse the guest). Since 2026-11-24 it opens the panel's own card (above): the wall-safe rows in
  words, the rest locked. This widens the trade on purpose (owner request): anyone standing at the panel can now read and clear the
  household's kit notices, still never whose and never a member's own words. `ZOE_NOTIFICATIONS_PANEL_TOUCH=off` restores the old
  press (the mic, "Say what's up?"). A verified voice asking runs the pull as that member, so only their own card is shown. A dot lit by the member's own `notify` or proactive count pulls as before.
- The card is pushed only to the panel the member is at (`voice_session.send_to_panel`, never `all`), only for the turn's
  identified member (`panel_review_cards._member`: the voice session or a fresh PIN), and a tap is honoured only while that
  member's voice session is open there or with their own session.
- The tail and "read them" need a VERIFIED voice turn (speaker verdict True). Someone else answering "read them" gets the private line.
- A sensitive notification is counted aloud but never read: "One is private, it is on the panel."
- Proposal lines (what the pendant heard) are read only to build that member's card: never stored in a notification row, never
  logged, never in any other push frame. Logs carry counts and types only.
- `create` refuses, by `omi_devices.assign`'s member rule: a member being erased right now or erased since the creator was scheduled
  (`user_erase_gate`), and a member whose household account no longer exists or is a guest (read under that gate); also words
  naming a forgotten entity (tombstone / forget ledger). A member whose memory was wiped but whose account remains is a live member
  again: new notifications after the erase are allowed, older scheduled ones are not.
- Forgotten means forever: `MemoryService.delete_user` calls `notifications_store.forget_user` (fail closed), which deletes EVERY
  notification row of the member, whoever wrote it, plus their tail-caps row, under the member's erase gate. Both deletes fail
  closed: only "no such table / no database pool in this process" counts as nothing to erase. `forget_reach` lists
  the table as reached. The entity forget ("forget Dana") does not scan notification titles.

## Do not overwhelm

- The tail rides only a reply to a turn the member started, after the reply finished with real text that does not end in a
  question; never on an emotional turn, in quiet hours, under `replay_isolation`, or on the pull's own reply.
- One appended line per reply across all tails: the transcript check goes first and wins.
- At most once every 4 hours and 3 times a household day per member, and only when an unread notification has not been announced
  yet (the orb press counts as announcing). The last-tail time and the day's count persist in one `app_settings` row per member
  (key `notification_tail:<member>`, no migration), so a restart does not reset them; the "already announced" set is in memory,
  so after a restart the same set may be announced once more, still inside the caps. The caps fail CLOSED: if the row cannot be
  read there is no tail that turn, and the new caps are saved BEFORE the line is spoken - a line whose save failed is not said.
  The check and the save are ONE step per member (a per-member lock): two overlapping turns on two panels get one line. The save
  runs under the member's erase gate with the generation taken before the tail's reads: a member erased in between gets no line
  and no recreated caps row.
- Never on a replay or dry turn: the brain stream is not wrapped under `replay_isolation`, and the deterministic-tier call passes
  `replay=` (the `replay-` instrument panels) / `dry=`, which return the reply untouched and record nothing.
- "Read them" reads at most 3 titles, then "And N more on the panel." "Later" or silence leaves them on the panel.
- The card shows at most 8 rows, counted AFTER a "confirm this voice" notification expands into its proposals; the rest are
  "And N more." on the card (a row that is not shown cannot be acted on).

## Adding a creator

Call `await notifications_store.create(user_id, "<type>", title, message, data, dedupe_id=<stable key>, sensitive=<bool>)`.
Pass `scheduled_at=user_erase_gate.generation(user_id)` from where a background job was scheduled - BEFORE any awaited read it
does (`notify_wearer_proposals` takes it before `_wearer_status`), so an erase that completes during the read drops the create. Keep the title short (it is
read aloud), keep owner-only content OUT of the row (build it into the card at render time, as the wearer creator does), and give
the type a card action in `notifications_store.card` / `act` only if it needs more than **Done**. A creator that reads another store
runs from `notifications_store.refresh` (throttled to once per 10 minutes per member).

## Not in this version

- The other hand-rolled writers of the table (`transcript_check._notify` and older ones) are not migrated to `create`.
- The entity forget does not reach notification titles (the member erase does).
