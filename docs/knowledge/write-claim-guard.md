---
type: Reference
title: The write-claim guard - Zoe never says "I've added it" unless a row landed
description: Why a spoken "done" is checked against receipts the writers record; where the guard sits (brain_dispatch, every brain turn), how a claim is recognised (per-language lexicon DATA), what counts as a write, the fast-path parser fixes that came with it, and what is still open. Flag ZOE_WRITE_CLAIM_GUARD = enforce | shadow | off, default enforce.
tags: [trust, voice-path, lists, calendar, skybridge, zoe-data]
timestamp: 2026-10-12T00:00:00Z
---

# The write-claim guard (`write_claim_guard.py`, `write_receipts.py`)

## The failure (live, 2026-10-12, AWST, the owner's own words to the panel)

| time | said | what Zoe did |
|---|---|---|
| 18:34:57 | a complaint ("I was asking you to set meetings and add things to the shopping list") | skybridge VETOED it (`router_disagrees`), then the legacy `intent_router` list path took the whole sentence as an item and answered "I've added i don't know where you got that from… Check the panel and tap Done" - over a form the (deaf) panel never showed. Nothing was saved. |
| 18:35:18 | "Can you add noodles? Noodles. To the shopping list." | saved, as `noodles? Noodles` |
| 18:35:47 | "Work." (the answer to "…a meeting tomorrow at 9 a.m. for work") | the brain said "I've added a work meeting to your calendar for tomorrow at 9 a.m." - **no row, one model round (`rounds=1`), no tool call**. |
| 18:36:03 | "Can you add fix zoe to my to-do list." | skybridge parsed a list SHOW ("Lists has 2 items."); nothing added. |
| 18:42:38 | "Can we add a doctor's appointment on Friday at twelve o'clock?" | two failed NLU calls (6.8 s), then the brain asked for a title the words had given; the next turn lost the context; no event. |
| 18:43:04 | mis-heard "Keynes pollutants in my calendar are at 12 o'clock" | `SKYBRIDGE_GATE … calendar show … router_unsure`, router confidence 0.39, ALLOWED -> "You have 0 events today." |

Root causes, in order of weight:

1. **A claim was never checked against a write.** Nothing between the 4B brain's text and the speaker asked "did a row
   land?". The Flue sidecar's prompt already says "NEVER claim … anything a tool didn't confirm" (`agents/zoe.ts`); a 4B
   model does not obey it reliably. The tools exist (`add_calendar_event`, `add_to_list`, `shopping_list_add`,
   `add_reminder`), `ZOE_BRAIN_ALLOW_WRITES=true` live, and they were *offered* on that turn (`tools=872` tokens, the
   calendar group disclosed and session-sticky) - the model simply answered in prose.
2. **The legacy list panel path claimed a write it had not made** (`routers/voice_tts.py`, quick `list_add`): the reply
   said "I've added X" while the item waited behind a Done button, and the path ran with no INTENT_GATE so a turn the
   router had sent to memory was still claimed.
3. **A loose pattern stored a sentence as an item** (`intent_router`'s "STT-garbled leading add" matcher:
   `^(?:i'd|and|at|it|hey|a)?\s*(?:go to )?(.+?) to my <list> list`), plus no item hygiene (stutter, punctuation, filler).
4. **Parser gaps**: `to-do` / `to do` were not list names in the Skybridge add regex; "put X on my list" was not an add;
   `_calendar_create_from_text` accepted digit times only, ignored weekdays (`_calendar_date_from_text` had none) and kept
   "on Friday" inside the title; a loose list/calendar SHOW fallback claimed any unparsed add.
5. **The router_unsure allowance had no floor** for personal-data writes.

## The guard

`brain_dispatch.brain_streaming` / `brain_oneshot` is the one place every brain turn ends (voice stream and non-stream,
chat, Telegram, LiveKit). `_claim_guarded` wraps the lane stream (`write_claim_guard.guard_stream`); the one-shot form is
`guard_text`. The replay harness (`replay_isolation`) is never guarded: its writes are deliberately not committed.

* **Receipts** (`write_receipts.record(user_id, kind, row_id, source)`) are recorded by the writers themselves, after the
  row exists: `calendar_service.create_event_record` (calendar), `list_service.add_item_to_list` (list, including a replayed
  add that deduped), `reminder_service.create_reminder_record` (reminder), the Skybridge inline event / list inserts, the
  Skybridge timer store (timer), the note executor (note), and the mcporter fallback tail of `execute_intent` for
  `list_add` / `calendar_create` / `reminder_create` / `note_create` (that subprocess writes in ANOTHER process). A Flue
  brain tool call reaches those writers through `/api/system/intent-dispatch`, so a tool that succeeded leaves a receipt and
  one that failed ("I need a title…") does not. Per user, in process, 64 newest, 1 h.
* **Claims** are data (`lexicons_data/write_claims.json`, languages `en` and `es`): claim verbs (`added`, `scheduled`,
  `booked`; `put/set/saved/created/made` only with a target noun), target nouns per kind (list / calendar / reminder / timer /
  note), openers, `other_targets` (contacts, memory, lights, playlist… - not this guard's kinds), and blockers (negation,
  irrealis, future, "want me to"). Questions, offers, refusals and promises are never claims; a 2nd-person subject ("you
  added") is not; "is scheduled for Friday" describes the calendar and is not (a passive needs `been`).
* **Verdict**: a claim naming kinds needs a receipt of every kind since the turn began (calendar and reminder back each
  other; a recap question - "did you add the milk?" - may stand on a write from the last 30 min). A claim naming no target
  ("I've added it") is backed by any receipt or by any non-read tool having run this turn.
* **Action**: the claiming sentence is replaced - once per reply - by the language's honest line ("I didn't manage to add
  that; want me to try again?"), and `WRITE_CLAIM_BLOCKED action=replaced lane= user= lang= kinds= reason=no_receipt` is
  logged (kinds and counts only: the sentence is household text). `shadow` logs `action=shadow` and changes nothing.
* **Streaming**: a sentence is held only while its words so far could still open a claim (claim-opening words, or a claim
  verb has arrived); every other sentence is released at once, in its original deltas, so first audio on an ordinary reply is
  untouched. Tool sentinels pass through and tell the guard which tools ran.

## Parser and gate fixes that came with it

* `list_service.clean_item_text` / `is_plausible_item` - ONE cleaner (stutter, fillers, edge punctuation) and ONE refusal
  (a sentence, a vague noun, a pronoun/wh-word opener, > 12 words) used by both the Skybridge and the `intent_router` parsers.
* Skybridge `_list_add_from_text`: `add|put … to|on|in (the|my|our) <list>`, list names incl. `to-do`, `to do`, `todo`,
  `bucket`; the verb must open a COMMAND (`_command_shaped`: only lead-in words before it).
* Skybridge calendar create: `add|schedule|book|put|set up|create|make X (on <day>) at <time>` and "what/how about a
  <event noun> … at <time>"; spoken times ("twelve o'clock", "nine thirty am", "noon"); weekdays (next occurrence);
  default title from the noun ("Doctor's appointment"); a bare "at 9" is still not guessed. The read-back names the day and
  the time ("Added Doctor's appointment on Friday at 12 PM.").
* `intent_router._extract_simple_calendar_slots`: the legacy `calendar_create` fills title/day/time from the same parsers
  before the NLU filler (which had returned no tool call twice on the live sentence).
* `skybridge_router_gate`: a READ that contradicts a command-shaped add/put/create in the words is vetoed
  (`verb_contradiction`); an `router_unsure` verdict for a WRITE-like turn on a personal-data domain needs router confidence
  >= `ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE` (default 0.45; 0 = off) else `router_unsure_low_conf`. Plain reads keep the
  garbled-STT recovery.
* `voice_tts` quick `list_add` / `calendar_create`: behind the INTENT_GATE; when `push.PushBroadcaster.panel_live(panel_id)`
  is false the write is done NOW and the executor's true read-back is spoken, never "check the panel"; when the panel IS live
  the reply no longer claims the write ("Tap Done on the panel to save X to your list.").
* `push.PushBroadcaster.panel_live(panel_id, max_ack_age_s=None)` / `note_panel_ack(panel_id)`: a subscriber on the panel's
  own channel, and a fresh ack when acks are tracked - the hook the panel-liveness work feeds.

## Open (see `open-problems.md`, "False write claims")

The brain still sometimes skips an offered write tool - the guard makes that visible and honest, it does not make the model
call the tool. The change touches the voice path (`voice_tts.py`, `brain_dispatch.py`): it must be replay-gated before deploy.
