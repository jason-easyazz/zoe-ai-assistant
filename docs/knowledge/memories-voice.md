---
type: Reference
title: Memories by voice - "what do you know about me?", the voice-session view, forget and correct (memories-space phase 3)
description: Flag ZOE_MEMORIES_VOICE (default off). A member in a verified voice session or a fresh PIN sign-in asks "what do you know about me?" and hears a short summary (told and confirmed first, inferred marked, private classes never spoken) while the panel shows a private card grouped People / Preferences / Plans / About you with where each item came from and That's wrong / Forget / Where's this from. "forget that I ..." and a tapped row's spoken correction go through the existing audited forget and correction paths. The phone's Memories page gets the same list with Fix and Forget. Owner answers D6, D7/D9, D10, D11, D13 applied; security model, operator steps, rollback, what is unproven.
tags: [memory, voice-path, panel, touch-ui, privacy, provenance, forget, correction, flag-dark]
timestamp: 2026-10-19T00:00:00+08:00
---

# Memories by voice (`memories_voice.py`, `routers/memories_card.py`, `js/memories-about-me.js`)

Design: [memories-space.md](../architecture/memories-space.md) 2.2 and 11. Panel card pattern: [panel-review-cards.md](panel-review-cards.md).

## What the member says and sees

| say (verified voice session or fresh PIN on this panel) | Zoe says | the panel shows |
|---|---|---|
| "Hey Zoe, what do you know about me?" / "what do you remember about me?" | "Here's the short version. I know 4 people in your life, most recently Robin, your sister. You've told me things like you walk the dog every morning and you like dark roast coffee. I've also worked out 2 things on my own; they're marked on the screen. A few private things stay off this screen. It's on the screen. Tap one to fix it or forget it." | **What I know about you**: People, Preferences, Plans, About you; each row with "You told me on 3 Oct" / "You confirmed this" / "From your day review, approved by you" / "I worked this out" (dimmed); That's wrong / Forget / Where's this from |
| "what have you learned about me this week?" (today / lately / this month) | "This week I picked up 3 things. Like ..." | only those rows |
| "what do you remember about Robin?" (Robin is in MY people) | "Here's what I know about Robin, your sister: Robin lives in Eastvale; ..." | Robin's entry, with Forget (fresh PIN only) |
| "what do you know about my health?" | "That's private, so I won't say it out loud here. Ask me on your phone or in chat." | nothing |
| "forget that I like jazz" | "Done - I've forgotten that you like jazz." (a private row: "Done - I've forgotten that.") | (the row is gone next time) |
| tap **That's wrong** -> **I'll say it**, then "it's on Saturday" / "my birthday is on 15 March" / "forget it" / "never mind" | "Fixed - I now have: ..." / "Done - I forgot ..." / "Okay, I'll leave it as it is." | |
| a guest, an unrecognised voice, or another member's voice | "That's private. Say it again so I know it's you, or unlock with your PIN." | the existing locked card: no row, no count, no name |

**Fix on my phone** shows a QR to `memories.html#about-me` (no secret in it). There the signed-in member sees the same list
(`GET /api/memories/mine`) with **Fix this** (typed) and **Forget** (asks first). No keyboard is ever shown on the panel.

## What is reused (no parallel path)

- **Read:** `provenance_answers.gather` (own approved rows; pasted, instruction-shaped, unverified and unanchored rows left out;
  private classes counted) and `memory_space` (grouping, the new `provenance_kind` / `provenance_label`). `provenance_answers.Item`
  now carries `origin` and `cls` so the label is decided by the writer and the authority class, never a model.
- **Forget a row:** `memory_space.forget_row` - the voice "forget it" steps: `MemoryService.review(reject)` of the row and its
  twins (each an audit row), physical erase when on, the owner's verbatim words out of the exact-words index and the transcript
  turn flagged off-record so nothing re-mines it.
- **Correct a row:** `provenance_answers.fix` (voice) / `memory_space.correct_row` (phone) -> `correction_apply.apply_row_correction`
  -> `MemoryService.review(edit)`: the new row is written under the user-class writer with the correcting words as evidence; the
  old row becomes `superseded` with `superseded_by_id` (history kept); its twins are retired. Needs `ZOE_CORRECTION_APPLY` (on live).
- **Forget a person:** the `memory_forget_entity` intent (`intent_router.execute_intent`): the permanent forget ledger
  (`memory_forgotten`, the forget shield), the verbatim words, the cascade. Fresh PIN only.
- **The card:** `panel_review_cards.mint` / `authorize` (one live card per panel, card-local ids, `X-Panel-Card` header,
  `voice_cards.push` to the panel's own channel), drawn by `js/touch-review-cards.js` inside `js/touch-voice-session.js`'s card.

A static test pins that `memories_voice.py` and `routers/memories_card.py` never call `.review(`, `erase_rows`, `DELETE FROM`,
`hard_delete`, `delete_user(` or `forget_last(` themselves.

## Privacy walls

- **The member is the TURN's identity** (`extra_ctx['scope_user']`: the open voice session, else a fresh PIN sign-in on THIS
  panel). A voice lane with no panel identity (`scope_user` absent) is not touched; chat keeps `provenance_answers`' longer list.
  `speaker_verified is False` is no member. There is no member parameter anywhere.
- **Own rows only**: every read is `gather(member)`; the voice forget only matches the member's own rows (B's words find nothing).
- **Never on the wall (owner D11, 2026-10-18/19):** `provenance_answers.sensitive_class` (health, money, private, feelings) AND the
  restraint classes the panel keeps off the wall (`panel_review_cards.OFF_THE_WALL`: health, money, grief, family conflict,
  affect) - fail-closed. They are counted ("2 private things are not shown here"), never spoken or carded; a private topic asked
  by name on the panel is refused aloud. People's facts are filtered the same way.
- **Told first:** the spoken examples are only rows the member told or confirmed; inferred rows are counted ("I've also worked
  out 2 things") and dimmed on the card.
- **Taps:** honoured only while that member's voice session on that panel is open, or with their own session. "That's wrong"
  arms ONE row for the NEXT spoken turn on that panel, by the same member, within 60 s; any other turn there clears it; a
  command ("turn on the lights") or a cancel ("never mind") is never taken as the correction.
- **Exact words behind a FRESH PIN (owner D13):** "Where's this from?" always says how and when ("You told me on Tuesday."); the
  member's verbatim words only when the request carries their own session AND they hold the panel's fresh PIN session
  (`voice_tts._resolve_fresh_panel_member`, the idle-logout window). A voice session alone is not enough. Private words never.
- **Forgetting a person needs a fresh PIN** (the voice path's step-up rule, kept for touch): 403 `pin_needed` otherwise.
- **Children (owner D7 / D9):** a curated row (`origin="curator"`, memories-space 2.3) is `protected` from a child's forget or
  correction (`memory_space.curated_locked`, fail-closed on an unreadable role); an adult's own word still wins. No curated rows
  exist yet (P7 builds the curator grant); the guard is in place for when they do.
- **Owner D6:** forgetting a person keeps the member's own calendar events and list items (the cascade only unlinks an attendee);
  pinned statically.

## Voice path

`fast_tiers._provenance_tier` calls `memories_voice.tier` right after the turn is numbered (`memory_provenance.note_user_turn`),
before `provenance_answers.handle`. So this is a voice-path change: replay-gated. Flag off = one env read per turn. The shapes are
whole-utterance anchored; `tests/test_memories_voice.py::test_other_intents_are_never_stolen` pins reminders, lists, calendar,
recall questions ("when is Robin's birthday?"), "forget that" / "forget what I said" / "forget about Robin", "don't forget ...",
"what's on my plate?" and commands as untouched, on voice and chat. "what do you remember about X" is claimed only when X is
exactly one of the member's own people with something wall-safe to say; any other name goes on to recall as before.

## Operator steps

1. Land through the voice landing loop (replay probe against the merged commit).
2. Set `ZOE_MEMORIES_VOICE=1` in zoe-data's env and restart zoe-data. Deploy the UI: `touch/home.html` loads
   `touch-review-cards.js?v=rc-2`, `memories.html` loads `memories-about-me.js?v=am-2`; nothing is precached, no `SW_VERSION` bump.
3. Needs `ZOE_VOICE_SESSION=1` (live) for the voice-session view and `ZOE_CORRECTION_APPLY=1` (live) for corrections.
   `ZOE_MEMORY_PROVENANCE_ANSWERS` must stay on (default): the tier runs inside it.
4. Verify at the panel with a verified voice (badge "<name> · voice"): "what do you know about me?" -> the card; tap That's wrong ->
   I'll say it, say the fix; say "lock" and the card goes; ask again as a guest and hear "That's private". On the phone, open
   `memories.html#about-me`, Fix and Forget one synthetic note.

**Rollback:** unset `ZOE_MEMORIES_VOICE` and restart: the tier returns None, `/api/panel/memories/*` and
`/api/memories/mine/actions` / `/correct` answer 404, the phone section stays hidden. Forgets and corrections already made stay
made (they are the existing writers' rows and audit).

## Unproven / gaps

- Not run on the live panel or with real voices (flag off; the kiosk was not reloaded). The node harnesses drive the real scripts
  against DOM shims; the backend tests use a fake Chroma.
- **A fact forget is not hash-shielded the way a person forget is.** "forget that I like jazz" rejects the row and its twins,
  erases them (physical erase on), removes the owner's words and marks the transcript turn off-record, so the same words are not
  re-mined; but a NEW conversation that restates the fact is treated as a re-teach. The permanent ledger holds entity names only;
  extending it to whole facts would hold out every later sentence sharing a short span (and trigger near-spelling "did you also
  mean" offers), so it was not done here.
- The spoken summary is long-ish for a wall (about four sentences); measure on the panel before turning it on.
- No presence signal exists for other people in the room, so the crowded-room rule (memories-space 2.2) is not enforced beyond
  the voice session closing on another voice (same open problem as the voice cards).
- Same residual risk as the panel review cards: a LAN client subscribed to `panel_<id>` sees the card frame while the member's
  session is open.
