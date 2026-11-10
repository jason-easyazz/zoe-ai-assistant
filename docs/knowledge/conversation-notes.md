---
type: spec
title: Conversation notes - Zoe remembers what you talked about last time and can pick it back up (ZOE_CONVERSATION_NOTES, flag-dark)
description: Acceptance spec + design record. After a conversation goes quiet Zoe keeps a short note (who, topics, how they seemed, what is open) in that person's own layer, and offers it as soft context at the start of their next conversation. Members, named guests (guest scope only), the pendant (owner-approved) and an owner backfill. The map of the existing memory pieces and why continuity was near zero.
tags: [memory, continuity, guests, pendant, forget, flue, voice-path, flags, spec]
timestamp: 2026-11-09T00:00:00Z
---

# Conversation notes

## Acceptance — true when done

Synthetic names: Quinn is a named guest, Avery is the owner. Owner request 2026-11-09: "i still think zoe should make notes about the
previous conversations to continue these topics or have knowledge about the conversation".

1. Next time someone talks to Zoe she remembers what they talked about last time and can pick it back up: "last time you were
   worried about the race entry - did you hear back?" - gently, once, only when it fits, never reciting a list and never saying
   "my notes say".
2. A guest's chats stay theirs. Quinn's notes are in Quinn's own space, served only when the server matched the voice to Quinn.
   Avery (who enrolled her), another guest, an unknown voice and every other member never see them.
3. Forgotten stays forgotten. A name Avery asked Zoe to forget is never in a note, never served from one, and "forget that", "forget what I
   told you about X", "forget Quinn" and the member erase each remove the notes that rest on it.
4. What a person said or confirmed always beats a note. A note is Zoe's impression; it is never filed as a memory, and the brain is told
   so every time it is served.
5. A child's note has no mood (an age lookup that fails counts as a child) and nothing frightening in it; health, money, grief and family-conflict subjects are never raised by
   Zoe unless the person raises them first.
6. The pendant stays approve-before-write: a conversation Avery had while wearing it is only ever PROPOSED as a "conversation note" in
   the day review, and nothing is stored until Avery approves it.
7. With the flag off Zoe behaves exactly as before. In shadow she logs what she would write and say, and says nothing different.

## Why continuity was near zero (the map, 2026-11-09)

Question: are notes not written, or written and not retrieved? Both, for different reasons, and for guests the turns were never kept.

| piece (code) | what it keeps | when it reaches the brain | why it is not "last time we talked about X" |
|---|---|---|---|
| `memory_digest` nightly facts | durable FACTS ("has a dog called ...") and `emotional_moment` rows from the day's own words | via the recall floor / `recall_every_turn` | facts, not conversations: nothing says "Sunday you were worried about the race entry". `recall_every_turn` serves only approved owner-derived-or-above rows (an inferred note could never be one) and is flag-dark |
| `_extract_open_loops` -> `open_loops` | unresolved threads ("waiting to hear about X"), 48 h of turns | the old `zoe_agent` lane (`[OPEN LOOPS]`) and `proactive`; NOT the live Flue lane's first turn | a thread without the conversation around it; aged out at the TTL; never injected on the Flue lane |
| `night_mind` | observations / unresolved worries over a longer window (12B, night) | `memory_for_prompt` only for an open check-in ("how has my week been") | trigger-gated, reader flags off |
| outcome edges (`proactive/outcome_edges.py`) | what Zoe offered and what came of it | proactive selection | measures offers, not conversations |
| `exact_words` | the owner's verbatim sentences (index) | on an exact-quote ask | a sentence store, not a conversation summary |
| recall floor / `SEAM_CONTINUITY` (`zoe_flue_client`) | nothing new - it reads the above | the recall floor on a recall-shaped question (~6% of turns); continuity on a mood statement (`_CONTINUITY_RE`: 0 of 91 real turns) | both are TRIGGER-gated, 94% of household brain turns match neither ([recall-not-firing-2026-10-29.md](recall-not-firing-2026-10-29.md)) |
| `turn_outcomes` | per-turn outcome table | nothing | dark, empty |
| `memory_episodes` | pointers: "these 12 turns were one conversation" (no text) | nothing | the segmentation exists and is unused - this feature reuses it |
| `voice_session_bridge` | the last 3 exchanges, in memory, 2 h | first 3 brain turns after a 5-minute session roll | gone after 2 h or a restart; flag-dark |
| Flue sidecar session store (`labs/flue-zoe-brain-2x`, `zoe-brain.db`) | the full event log per session id | replays the session's own history | one session id per 5-minute voice window; no cross-session summary exists |
| named guests (`named_guest_turns`, #2342) | the guest's last exchanges, raw | `named_guests.brain_block`, last 6 only | no summary, so a chat older than 6 turns is gone; before enrolment nothing was kept (`GUEST_MEMORY_DISCARD`) |
| pendant (`omi_day_review`) | candidates: events, lists, facts, people | owner approval only | never proposed the *conversation* itself |

So: **nothing wrote a per-conversation note**, and **nothing that is written is pushed in at the start of the next conversation** (every
injector is trigger-gated). The fix is one small store plus one start-of-conversation injector, reusing the existing segmentation
(`memory_episodes`), loader (`memory_digest.load_day_messages`: owner rule, off-the-record, distress, forgotten-name and rejected-speaker
filters already applied), model call pattern, forget ledger, voice-bridge placement and the day review's approval path. It does not touch
`MemoryService`, so it adds no new memory authority question.

## What was built

* **Store** - `conversation_notes` (migration 0074): one row per conversation: `user_id` (the member layer), `people_id` (`''` = the
  member's own; the guest's person id for a named guest), `episode_key` (idempotence), `source` (`chat` | `panel_guest` | `pendant` |
  `backfill`), `status` (`active` | `shadow`), summary, topics, mood, open threads, one follow-up, `sensitive`, `served_count`, and `turn_ids` (pointers to the
  source turns - chat message ids, or `named_guest_turns` ids - never text), so a forget that names something only a source turn said still reaches a note that paraphrased it.
* **Write** (`conversation_notes.py`) - the nightly dreaming pass (`memory_digest`, right after the open loops) reads the member's last 48 h
  of own-words turns, cuts them into conversations (45-minute gaps), leaves the one still going, skips non-conversations (< 3 turns or
  < 30 words) and makes ONE local-brain call per conversation. The model only summarises: code parses the JSON, checks every field is
  grounded in the transcript (an invented open thread is dropped, an invented summary drops the note), strips a child's mood, and marks
  private subjects. The same pass runs over each named guest's own turns (`named_guest_turns`) for the member who enrolled them.
* **Serve** (flag `on`) - members: `block_for`, beside the voice bridge in `zoe_flue_client`: confirmed speaker only, the first 2 brain
  turns of a session, the newest 2 notes younger than 21 days, each offered at most 3 times with a 6-hour cool-down, strict forget-ledger
  and mute filters, `[MEMORY CONTEXT ...]` delimiters so the sidecar elides it from older turns. **Precedence in code**: a stated / confirmed durable
  fact NEWER than the note that shares two content words with it means the note is not served at all ("waiting to hear about the race
  entry" is never offered beside "I got in"; the newer fact reaches the brain through memory); an unreadable fact store drops the open threads of every note. Guests: `guest_lines` inside
  `named_guests.brain_block`, theirs only, on the first 2 matched turns of a VISIT (30 minutes of silence ends one), each note at most 3 visits. The block tells the brain: sparingly, naturally, never recite, never mention notes, what the person
  said or confirmed wins, drop it if they want to move on.
* **Pendant** - `omi_day_review` proposes a `conversation_note` candidate (asked for only while the flag is on; needs a real conversation,
  2+ cited lines, a grounded summary, no mood field - Zoe cannot tell whether the other person is a child - and none of the health / money / grief / family-conflict / distress classes). `omi_review_apply.write_conversation_note`
  is the ONLY writer (a test pins that nothing else imports it) and stores it in the owner's layer. Forgetting the recording or the name un-writes it.
* **Backfill** - `scripts/maintenance/conversation_notes_backfill.py` (below).
* **Forget** - `forget_user` (member erase, fail closed), `erase_entity` (a cascade step, `conversation_notes`: matches the note's words OR the words of
  its source turns), `erase_topic` ("forget what I told you about ...", same reach), `erase_for_text` ("forget that": the note lists the turn as a
  source by word, its conversation's time span covers the turn, or they share two content words - it over-forgets on purpose), `erase_people` (a guest's erase), the strict serve-time
  ledger filter, and the `forget_reach.py` inventory row.

## Flags

| flag | default | meaning |
|---|---|---|
| `ZOE_CONVERSATION_NOTES` | `off` | `off` nothing runs. `shadow`: the nightly pass writes notes as `shadow` rows and `block_for` logs `CONVERSATION_NOTES_INJECT mode=shadow` with the note ids it WOULD serve, serving nothing. `on`: notes are `active` and served; the first `on` night promotes the shadow rows still inside the 21-day TTL |
| guests | needs `ZOE_NAMED_GUESTS=on` and `ZOE_NAMED_GUEST_MEMORY=1` as well | a guest's notes are written and served only when the guest layer itself is on |
| pendant | needs `ZOE_OMI_DAY_REVIEW` on/shadow as well | the proposal exists only while `ZOE_CONVERSATION_NOTES` is not `off`, and an approval after the flag went off is refused (`notes_off`); in shadow an approved note is stored as a `shadow` row |
| backfill | refuses to do anything while the flag is `off` (a dry run included) | in `shadow` it stores `shadow` rows (a dry run stores nothing), in `on` `active` ones |

Logs carry counts and 8-character ids, never text. To read the shadow notes: `conversation_notes_backfill.py --show` (guest) or
`SELECT ... FROM conversation_notes` as the DB owner.

## Rollout

1. Merge dark. Apply migration 0074 with the normal deploy.
2. `ZOE_CONVERSATION_NOTES=shadow` for a few nights. Check `grep CONVERSATION_NOTES ~/.zoe-logs/zoe-data.app.log`: `stored` per night, `no_note`
   share, `CONVERSATION_NOTES_INJECT mode=shadow` count (how often a note WOULD have been served), and read a handful of shadow notes yourself.
3. `ZOE_CONVERSATION_NOTES=on` (and the guest flags if the guest layer is on). Voice-path file `zoe_flue_client.py` is touched: the change
   is replay-gated at landing like every voice-path change.

## Backfill a guest's earlier chat (owner steps)

The guest's Sunday chat was discarded by zoe-data (a guest's turns are never saved), but the Flue brain kept its own event log. Once she is
ENROLLED as a named guest (`named-guests.md`: "learn Quinn's voice" at the panel, or `named_guest_backfill.py` from saved clips):

```
cd ~/assistant
# 0. (once) set ZOE_CONVERSATION_NOTES=shadow in zoe-data's .env: stored-but-never-served, zero risk to Zoe; the tool refuses while it is off
# 1. find the window: household-local times of the chat (a few minutes either side); make the notes, store nothing
python3 scripts/maintenance/conversation_notes_backfill.py --owner <your user id> --name Quinn \
    --since "2026-11-08 02:40" --until "2026-11-08 02:50" --dry-run
# 2. read the printed note(s) and the counts: guest_exchanges should be the chat (about 12), skipped.member should be 0 or your own turns
# 3. store them
python3 scripts/maintenance/conversation_notes_backfill.py --owner <your user id> --name Quinn \
    --since "2026-11-08 02:40" --until "2026-11-08 02:50"
# 4. check what is stored for her
python3 scripts/maintenance/conversation_notes_backfill.py --owner <your user id> --name Quinn --show
```

Set `ZOE_CONVERSATION_NOTES` to `shadow` (stored, not served until the first `on` night) or `on` in zoe-data's environment first - the CLI refuses to store while it is `off`.
Notes: it refuses between 01:50 and 04:55 (the night window owns the brain); it reads the Flue file read-only; a member's turns in the window
are counted and left out; a turn naming a forgotten name is left out; it is idempotent (a rerun stores nothing). The notes are served only
once `ZOE_CONVERSATION_NOTES=on` and `ZOE_NAMED_GUESTS=on` and `ZOE_NAMED_GUEST_MEMORY=1`. The pendant side of the same Sunday conversation
arrives through the day review as a proposal you approve (flag on); it is stored in YOUR layer, not hers.

## Not built / honest limits

* Notes are made nightly, not the minute a conversation ends: a chat at 3 pm has no note until that night (the in-memory voice bridge covers
  the first two hours when its flag is on).
* Precedence is recency: a stated or confirmed fact NEWER than a note and sharing two content words with it removes the whole note. An OLDER fact is
  not checked against a later note - word overlap cannot tell a contradiction from agreement, and "training for a half marathon" overlaps nearly every
  note about a marathon - so there the block's instruction ("what they said or confirmed wins") is the only guard. Reviewed and declined as a code rule.
* A note is an LLM summary of the owner's own words; its quality depends on the digest model. Every field is grounded against the transcript
  by word overlap, which stops invention but not a bad paraphrase. The shadow period is for reading real notes.
* "Forget that" binds to the previous turn, which cannot yet be in a nightly note; the time-span rule covers the rare overlap and a paraphrase. A guest cannot forget a note (guests have no forget; the owner's "forget Quinn" erases them all).
* Source-turn matching needs the source rows to still hold the name when the forget runs (the entity forget runs at once; the nightly transcript redaction comes later). A backfilled note's `turn_ids` are Flue submission ids: a forget reads those exchanges back (read-only) from the Flue event log (`conversation_notes_backfill.source_text`); if they cannot all be found (log rotated or moved, `ZOE_FLUE_BRAIN_DB` overrides the path) the note is removed by ANY forget, since it cannot be proven clean.
* The legacy `zoe_agent` lane does not serve notes; the live lane is Flue.
* The forget-completeness probe covers `conversation_notes` as a layer; the Flue sidecar's own transcript store (the source the backfill reads) is
  the existing `flue_conversation_store` gap in `open-problems.md`.

## Files

`services/zoe-data/conversation_notes.py`, `conversation_notes_backfill.py`, `alembic/versions/0074_conversation_notes.py`,
`scripts/maintenance/conversation_notes_backfill.py`; hooks in `memory_digest.py`, `zoe_flue_client.py`, `named_guests.py`,
`named_guest_memory.py`, `omi_day_review.py`, `omi_review_apply.py`, `memory_service.py`, `memory_forget_cascade.py`, `forget_topic.py`,
`intent_router.py`, `forget_reach.py`. Tests: `services/zoe-data/tests/test_conversation_notes.py`.
