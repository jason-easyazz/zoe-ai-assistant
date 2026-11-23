---
type: Reference
title: Zoe OS memory door - a member's change to a memory row asks the kernel inside the one writer
description: ZOE_OS_MEMORY=kernel (default off). Where memory lives and its one writer, who is acting at every call (memory_actor), what is asked first (remember, correct, approve), what is made first and told afterwards (every forget), what is never asked (automatic writers), the table and its one strict switch, exactly what crosses the socket (never memory text), the readers the forget shield covers (none moved), and the switch-on steps.
tags: [zoe-os, kernel, memory, door, forget, authority, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS memory door

**Status 2026-11-20: built, flag-dark (`ZOE_OS_MEMORY` unset = `off`). Nothing on this page is live.** The kernel decides and
records; the memory stays where it is. The kernel never holds a memory: no text, name, excerpt, tag or reason reaches it.

## 1. Where memory lives, and its one writer [code]

The memory rows are one palace (Chroma) collection plus an audit collection, behind `memory_service.MemoryService`. A row
carries its owner (`user_id`), `visibility` (`personal`, or `family` = the whole house sees it), status, authority and
provenance. `MemoryService` is the only writer: `tests/test_memory_one_writer.py` scans every module for a write call on
the collection and allows exactly FOUR bypass writes on main by module, function and count (`memory_digest._upsert_rows`: 1;
`memory_digest._update_existing`: 2; `zoe_agent.migrate_mempalace_legacy_records`: 1; all automatic). A fifth fails.

Thirteen methods change rows. `tests/memory_writers.py` lists every call of them: 46 by a member, 48 automatic, the rest
helpers that hand on what they were told.

## 2. Who is acting [code; `memory_actor.py`]

Every one of the thirteen takes `acting=` with NO default: `memory_actor.member(user_id)` (a household member asked: spoken,
typed, tapped, or a tool the brain called for them; built as the first statement of the entry point, before anything is
awaited) or `memory_actor.AUTOMATIC` (nobody asked for THIS write: a per-turn extractor, a night pass, a mirror of a note /
journal / contact change that area's own door already decided, an eraser finishing an earlier forget, a seeder, an operator
script). `acting` is who asked, not whose row it is: a curator or an admin acting on a member's row is the curator or admin.

**The panel.** A voice-verified panel session is one per panel, in process memory, holding the member's id
(`voice_session.py`). While it is open the turn's user IS that member, so they ask as themself. With it closed, or a voice
that is not verified, the turn's user is `guest`: the door asks as the panel (nobody identified), the kernel refuses, and the
store drops a guest's fact exactly as it always did.

## 3. What the door does [code; `memory_door.py`, inside `MemoryService`]

| A member's | Method | Kernel |
|---|---|---|
| remember | `ingest` | asked first (`memory.remember`) |
| correct | `review(edit)`, `supersede_by`, `flag_challenged`, `set_curated_standing` | asked first (`memory.correct`) |
| approve a held row | `review(approve)` | asked first (`memory.approve`) |
| forget | `review(reject / archive)`, `erase_rows`, `delete_user` | **made first, told afterwards** (`memory.forget`) |

* Asked first: a "no" is final (nothing is written; `MemoryRefused`: the screen gets 403 and a plain sentence, the spoken
  remember says it, the brain's memory tools return it). Kernel down, old or slow: the change proceeds, logged `not_checked`.
  The kernel then hears how it ended (`done`, or `failed` when the store dropped or refused the write).
* Forget: never asked, never delayed, and the door puts nothing in front of an erase. A kernel "no" only makes the record
  say `objected`; the erase has happened and stands (someone off the roster forgetting, say). A forget that did not happen (it
  failed, or removed no row: an empty request, or ids none of which are the member's) is not told. **The existing fail-closed forget step is untouched** (`os_shadow.forget_user` / `forget_for_erase` / the
  parked plan: main's own, and it alone decides what a kernel that is down means for an erase).
* zoe-data still decides what it decided before: a child's own forget is refused there as today; a guardian's forget is an
  admin's, for one row; a curator needs the member's opt-in. The kernel records those, it does not decide them.
* **Automatic writers are neither asked nor recorded.** The owner's rule is unchanged and pinned
  (`tests/test_memory_same_as_main.py`): user-stated > confirmed > inferred; an inference never supersedes.

**Flag off is main exactly:** one flag read; 171 scenarios give the record captured from main (every write with its full
metadata). With the door on and the kernel allowing, the same record.

## 4. The table [code; `labs/zoe-os/zoe_os/memory_door.py`]

| Row | Who asks | Remember / correct / approve | Forget (told afterwards) |
|---|---|---|---|
| any | the member whose memory it is | allow | recorded |
| private | another adult | allow (zoe-data still decides which adult: a curator, an admin) | recorded |
| private | anyone else (a child, a guest) | refuse `not_owner` | objected |
| household | anyone on the roster | allow | recorded |
| any | the panel, nobody identified | refuse `not_owner` | objected |
| any | not on the roster | refuse `unknown_person` | objected |

This is TODAY'S behaviour, moved as it is. **One switch, off** (`memory_door.STRICT` in the kernel): private = its owner only;
a household row is remembered by anyone on the roster and corrected or approved only by its author or an adult. It never
touches forget.

## 5. What crosses the socket and reaches the log [code]

`door_ask`: `area=memory`, the verb, the acting member's id (or `via=panel`), the row's id (none for a new fact), the owner's
id, `household` or `private`, the erase stamps of the members named. `door_report`: the ask number, `done` or `failed`, the
same stamps. A told forget is the same ask, made after the erase. One log line: `ZOE_OS_ACT area=memory verb=... decision=...
reason=... ask_ms=...`. **Never:** memory text, a name, an excerpt, a tag, a note or a reason string. The wire has no field
for them (`labs/zoe-os/tests/test_memory_door.py`), and the service-side test searches everything sent for the words.

## 6. No read moved [code]

Recall is never asked and no read path changed. The readers the forget shield covers today (`memory_forgotten`:
`keep_unforgotten`, `matches`, `spans`, `active_hashes`, `ledger_pattern`) are in: `memory_service`, `calendar_feed`,
`commitments`, `contacts_store`, `conversation_notes`, `conversation_notes_backfill`, `correction_marker`, `exact_words`,
`member_register`, `memory_curation`, `memory_digest`, `memory_forget_alias`, `memory_forget_cascade`, `memory_retire`,
`named_guest_memory`, `night_extract`, `night_mind`, `notifications_store`, `now_card`, `own_name`, `person_extractor`,
`person_profile`, `provenance_answers`, `recall_every_turn`, `routines_store`, `self_review`, `struggle_detector`,
`struggle_proposals`, `user_model_card`, `voice_session_bridge`. None of those reads moved; the only change in any of them
is the `acting=` argument on a write call.

## 7. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it** (an older kernel has no memory area: every ask would be
   `not_checked`).
2. The roster current, with roles.
3. `ZOE_OS_MEMORY=kernel` in zoe-data's environment, then a zoe-data restart.
4. Live check with a demo adult and a demo child: each remembers a fact (allowed, `done`); the child corrects the adult's
   private fact (refused, unchanged); the adult forgets a fact (gone at once, `recorded`); stop the kernel unit: a remember
   still works (`not_checked`), and a forget behaves exactly as it does today with the kernel down.

Turn off: remove the flag (or set `off`), restart zoe-data.

## 8. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken remember, correct and forget paths are touched).
* Callers that catch every error (several correction and forget helpers) say a refusal as their ordinary failure line, not
  the kernel's sentence. The change is still not made.
* A whole-member erase (`delete_user`), the member's own or an admin's of another member, is told once the erase span has
  ended, with the erased member as the owner; a count of 0 rows removed is not told. The kernel has just forgotten that member,
  so its erase-stamp order records nothing that names anyone for it (a bare `erased` refusal event): an admin's erase of
  another member is therefore not attributed to the admin in the kernel either (zoe-data's own content-free `delete_user`
  audit rows still name the actor).
* The four bypass writes of section 1 still write without passing through `MemoryService`.
