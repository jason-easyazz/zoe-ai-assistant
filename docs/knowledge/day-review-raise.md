---
type: Reference
title: The day review as a conversation - raise, pull, orb and the crowded-room phone send
description: Memories-space P6 (flag-dark ZOE_DAY_REVIEW_RAISE + ZOE_OMI_DAY_REVIEW=on). Zoe mentions a waiting pendant day review in ONE counts-only line on the member's first greeting turn of the morning (never unprompted speech, never a question), at most once a day, snoozed after two ignored raises or one decline. "What did you find in my day?" gets a counts-only summary and the private card; "what's up?" adds one sentence; the orb shows a quiet ring. Only an identified member alone hears it; a crowded room sends the count to their phone. Raise and pull never write.
tags: [memories, omi, day-review, voice-path, proactive, privacy, flag-dark, touch-ui]
timestamp: 2026-10-19T00:00:00+08:00
---

# The day review as a conversation (`day_review_raise.py`)

Design: [memories-space.md](../architecture/memories-space.md) section 5.1 and phase P6. Owner rules that bind it: W2 (no
unprompted speech, `ZOE_PROACTIVE_SPOKEN=0`; the replacement is a natural mention on the first turn he starts), "Zoe never
asks the household anything", D11 (sensitive classes never spoken), D13 (transcripts never on the wall), approve-before-write.

## What the member hears and sees

| when | Zoe says | the panel shows |
|---|---|---|
| his first greeting / open turn of the morning ("good morning", "hey Zoe, how's it going?"), a review waiting, alone, identified | the brain weaves ONE line it is handed, composed in code: "I went through your day - there are four things for you to look at whenever you like (two events, one thing for your lists and one reminder)." No names, no quotes, no question | nothing new |
| a command turn ("turn on the lights") | nothing about the review | nothing |
| "what did you find in my day?" / "anything from today?" / "anything from my pendant?" / "what's in my day review?" | "I went through your day and found two events, one thing for your lists and one reminder. They're on the screen." | the private **Your day** card (`panel_review_cards`, needs `ZOE_PANEL_REVIEW_CARDS`) on THIS panel; without the card flag: "...on the Pendant page on your phone." |
| "what's up?" with the review waiting | the pull's usual reply + "Also, I went through your day and found ..." | - |
| the same ask with someone else in the room | "That's private and someone else is here, so I've sent it to your phone." | the locked card (the list leaves the wall) |
| "send my day (review) to my phone" | "I've sent it to your phone." | - |
| a guest / an unverified voice asks | "That's private. Say it again so I know it's you, or unlock with your PIN." - never a count | the locked card |
| any time a review is waiting (PIN-signed-in member) | - | a thin, still ring round the orb (`#orb.review`): no count, no sound, no motion |

The phone message (Telegram, off the box) carries the COUNT only: "I went through your day: four things are waiting for you to
look at." plus a "Review your day" button to `<ZOE_PUBLIC_URL>/pendant.html` (the member's own Pendant page, which needs their
sign-in). No kinds, no words from the day.

## Frequency and snooze (no nagging)

- **At most once a household day** (`user_prefs` key `day_review_raise.day`, so a restart cannot repeat it), and only on a turn
  that also takes the day's first-turn brief claim. Outside the brief window (05:00-12:00) nothing is raised.
- **Taking it up** = a pull ("what did you find..."), "review my day" (the card), or approving / dismissing ANY item anywhere
  (desktop, phone, card) after the raise. That resets the count.
- **Two raises in a row not taken up**, or one **decline** within 10 minutes of a raise ("not now", "leave it", "maybe later",
  "no thanks", "don't mention it again", "stop telling me about my day"; the STORED raise time decides, so a restart in between
  does not lose it) = SNOOZED. A decline is only recorded; the turn still
  goes on to the pull's own "not now" handling and the brain.
- A snooze lifts only when a NEWER review is waiting AND `ZOE_DAY_REVIEW_RAISE_SNOOZE_DAYS` (3) have passed since the last
  raise. The same old items are never raised a third time; they wait on the card, the orb and the pull.

## Who may hear it

Only the member whose candidates they are, on a turn that identified them:
- **voice**: `voice_tts` notes each turn's `scope_user` (an accepted voice claim / the open voice session / a FRESH PIN) per
  session id (`note_voice_turn`); the brief reads it back by session id. A ContextVar would not work: the streaming brain runs in
  the request task, not in `voice_command`'s task. A voice session id with no note is refused, never assumed.
- **chat**: the signed-in caller of their own session.

A guest, an unverified voice, a stale panel binding and another member get nothing - not even the count. Member B's own pull
counts only B's candidates.

## Crowded room (identity-presence-engine section 2.1)

"Another voice heard on this panel in the last 10 minutes": an unverified voice (the gate judged and did not confirm) or another
member's confirmed voice. A crowded raise is simply not made (a raise is unasked; sending a Telegram unasked would be a push). A
crowded pull goes to the phone and the wall gets the locked card. At most one phone send per member per 10 minutes.

A spoken turn with NO speaker verdict (for example a fresh-PIN turn the gate did not judge) is an unknown voice and counts too.
Honest limits: a silent person is invisible (voice-only presence); the owner's own unverified or unjudged turn counts as
"someone" (fail-safe); for the first 10 minutes after a zoe-data restart presence is unknown and the room
counts as crowded.

## Read-only

Raise, pull, orb and phone never write a candidate, list, reminder, event or memory (`omi_review_apply` stays the only writer;
pinned statically and by a write-path tripwire in the tests). The module's own state is two `user_prefs` keys: dates and a
counter, no text. No item text is logged (`DAY_REVIEW_RAISE user= raised= reason=`, `DAY_REVIEW_PULL`, `DAY_REVIEW_PHONE`).

## Measured cost (2026-10-19, live Postgres, read-only probe id)

- Flag off: `prepare` returns after one env read, p50 1.4 us.
- Flag on, the raise's full I/O path on a greeting turn (state read + two indexed counts): p50 1.52 ms, p95 1.66 ms (n=300). It
  runs inside the brief's existing 2 s time box, only on greeting turns before the day's brief is settled.
- The pull / orb count: p50 0.61 ms.

## Flags and operator steps

| flag | default | meaning |
|---|---|---|
| `ZOE_DAY_REVIEW_RAISE` | off | the raise, the pull, the "what's up?" sentence, the orb ring, the phone send |
| `ZOE_OMI_DAY_REVIEW` | off | must be `on` (`shadow` = analysis only, no mention) |
| `ZOE_DAY_REVIEW_RAISE_SNOOZE_DAYS` | 3 | the snooze length (1-30) |
| `ZOE_BRIEF_ON_FIRST_TURN` | off (live: on) | the raise rides it; off = no raise (the pull and orb still work) |
| `ZOE_PANEL_REVIEW_CARDS` | off | the private card on a pull; off = the pull points to the phone page |
| `ZOE_PUBLIC_URL` | unset | the phone message's button; unset = the message goes without a link |

Turn on: set `ZOE_OMI_DAY_REVIEW=on` and `ZOE_DAY_REVIEW_RAISE=1` in `services/zoe-data/.env`, restart zoe-data, poll `/health`.
Rollback: `ZOE_DAY_REVIEW_RAISE=0` (instant, per call) and restart; `ZOE_OMI_DAY_REVIEW=shadow` also silences it.

## Not built here (P6 remainder)

- The voice dialogue tier (`omi_review_dialog`: "A meeting: Thursday at 10. Add it?" / yes / skip / "add the rest" with a
  read-back). Going through items is by touch (the card), phone or desktop.
- The `others_present: ask` member setting (identity design): a crowded room never reads it out.
- The orb ring shows only for a PIN-signed-in member (the inbox poll needs a session); a voice-session member hears the raise.
