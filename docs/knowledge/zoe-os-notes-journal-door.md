---
type: Reference
title: Zoe OS notes and journal doors - a note or journal change asks the kernel first
description: ZOE_OS_NOTES=kernel and ZOE_OS_JOURNAL=kernel (both default off). Notes and the journal are two more areas on the one Zoe OS door, a flag each. Where each is stored, every writer, what is asked, the two permission tables and how they map onto the stored rows, what crosses the socket, and the switch-on steps.
tags: [zoe-os, kernel, notes, journal, door, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS notes and journal doors

**Status 2026-11-20: built, flag-dark (`ZOE_OS_NOTES` and `ZOE_OS_JOURNAL` unset = `off`). Nothing on this page is live.** Same
mechanism as the calendar ([zoe-os-calendar-door.md](zoe-os-calendar-door.md)); the design is the cut-over plan's
([zoe-os-cutover-plan.md](../architecture/zoe-os-cutover-plan.md), "Changed 2026-11-18"): the kernel decides and records; the
data stays where it is.

## 1. Where they live, and their one door [code]

| | Stored | The one writer | Entry points |
|---|---|---|---|
| Notes | `notes`: `user_id` = the author; `visibility` = `personal`, or `family` (every member sees it) | `note_service.py` | the screen (`routers/notes.py`: create, edit, patch, delete), the brain's `note_create` / `note_update` / `note_delete`, the spoken note (`intent_router.py`), an accepted note suggestion (`pending_suggestions.py`) |
| Journal | `journal_entries`: `user_id` = the writer; always `personal` | `journal_service.py` | the screen (`routers/journal.py`: create, edit, delete), the brain's `journal_create_entry`, the spoken entry (`intent_router.py`) |

Nothing makes a note or a journal entry automatically (the pendant day review writes neither), and nothing erases one row at a
time, so neither scan has a named exception. `tests/test_notes_journal_one_door.py` pins both doors over every module;
`tests/test_notes_journal_same_as_main.py` pins what every entry point does against a record captured from main.

## 2. What is asked [code]

Adding, changing and deleting a note or an entry asks first; a refusal is final (`NoteRefused` / `JournalRefused`, nothing
written); kernel down or slow = the change proceeds, `not_checked`. Reading is never asked. The memory mirror and the people
extraction a note or an entry triggers happen after the write, as before, and are not asked.

How a refusal reaches the person: the screen gets 403 with a plain sentence; the brain's tools return an error result with
`status: refused`; a spoken turn says the sentence; an accepted suggestion fails and stays pending.

**Flag off is main exactly:** a flag read; no kernel call, socket, thread or extra statement. All 30 entry-point scenarios give
the record captured from main (`tests/test_notes_journal_kernel_door.py`), and still do with both doors on and allowing: every
caller hands the door the row it already read.

## 3. Who may do what [code; the tables are the kernel's, `labs/zoe-os/zoe_os/notes_door.py`]

| Note | Who | Add | Change, delete |
|---|---|---|---|
| any | its author | allow | allow |
| private | anyone else, an adult included; the shared panel | refuse `not_owner` | refuse `not_owner` |
| household | an adult who is not its author | refuse `not_owner` (nobody makes a note in another's name) | allow |
| household | a child or a guest who is not its author | refuse `not_owner` | refuse `adults_only` |
| household | the shared panel | refuse `not_owner` | refuse `not_owner` |
| any | someone not on the roster | refuse `unknown_person` | refuse `unknown_person` |

| Journal entry | Who | Add, change, delete |
|---|---|---|
| any | its writer | allow |
| any | anyone else, an adult included; the shared panel | refuse `not_owner` |
| any | someone not on the roster | refuse `unknown_person` |

**How this maps onto the stored rows.** The owner is `user_id` on the row (author / writer; there is no other owner).
A household note is `visibility = family`. A journal entry is always told `private`.

* **An adult may not change or delete a child's private note.** zoe-data does not allow it today (only the author may change
  any note), so the table does not either.
* **An adult may change a household note in the table, but not in zoe-data.** The table is a ceiling; zoe-data's rule (author
  only, 403) runs first and is unchanged. So turning the flag on changes nothing a member can do with notes today.
* **A journal is private to its writer**, from adults too.
* The brain's `note_update` / `note_delete` read only the note's id, so they tell the kernel `private` whatever the note's
  visibility. The decision is the same (the author); the door adds no read to find out.
* A session with nobody identified (a guest id) asks as the shared panel and is refused. Today the guest role matrix decides
  that; with a flag on the kernel refuses it as well.

## 4. What crosses the socket and reaches the log [code]

One ask per change: `door_ask` with `area=notes` or `journal`, the verb (`note.create` / `note.edit` / `note.delete`;
`journal.create` / `journal.edit` / `journal.delete`), the acting member's id, the note's or entry's id, its owner's id,
`household` or `private`, and an erase stamp per member named. Never a title, a text, a mood, a tag or a photo. One log line:
`ZOE_OS_ACT area=notes|journal verb=... decision=... reason=... ask_ms=...`, every field from a closed set.

## 5. Ordered against a forget [code]

As the calendar: every caller takes its stamps (`stamps_now()`) as the first thing it does, before it reads the row, and hands
them to the door; a structural test holds every caller in every module to that.

## 6. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it** (an older kernel has neither area: every ask ends
   `not_checked reason=protocol`).
2. The roster current, with roles.
3. `ZOE_OS_NOTES=kernel` and / or `ZOE_OS_JOURNAL=kernel` in zoe-data's environment, then a zoe-data restart. Each flag stands
   on its own.
4. Live check with a demo member: save, change and delete a note by voice and on the screen, and write a journal entry (one
   `decision=allow` line each).

Turn off: remove the flag (or set `off`), restart zoe-data. Nothing else to undo.

## 7. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken note and journal paths are touched).
* The kernel's record of a note or journal change has no undo and no "explain" wording yet.
