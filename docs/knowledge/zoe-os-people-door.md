---
type: Reference
title: Zoe OS contacts door - a change to a contact asks the kernel first
description: ZOE_OS_PEOPLE=kernel (default off). Contacts (people and person_relationships) are another area on the one Zoe OS door. Where contacts live, every writer and which are a member acting, what is asked and what is not, the permission table (today's rule, with the one line the owner can switch), what crosses the socket, what is not yet behind the door, and the switch-on steps.
tags: [zoe-os, kernel, people, contacts, door, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS contacts door

**Status 2026-11-20: built, flag-dark (`ZOE_OS_PEOPLE` unset = `off`). Nothing on this page is live.** Same mechanism as the
other areas ([zoe-os-calendar-door.md](zoe-os-calendar-door.md)); the design is the cut-over plan's
([zoe-os-cutover-plan.md](../architecture/zoe-os-cutover-plan.md), "Changed 2026-11-18"): the kernel decides and records; the
data stays where it is. Memory facts about a person are NOT part of this area.

## 1. Where contacts live, and their door [code]

| Table | Holds | Its one writer for a person's action |
|---|---|---|
| `people` | one row per contact. `user_id` = whose address book it is in; `visibility` = `family` (the house shares it; the screen's default) or `personal` | `people_service.py` |
| `person_relationships` | the edges between contacts | `people_graph.py` |

A member's action reaches `people` from: the screen (`routers/people.py`: create, update, delete, add / change / remove a
relationship, merge), the brain's `people_create` / `people_update` / `people_delete`, the spoken add and same-person rename
(`intent_router.py`), Skybridge's "remember that <name> ..." (`skybridge_service.py`), the panel's introduce
(`routers/voice_tts.py`), an accepted "add X as a contact" offer (`pending_suggestions.py`), a spoken correction
(`correction_apply.py`), the pendant day review's approval (`omi_review_apply.py`), a settled contact review
(`contacts_store.resolve_review`), and a member's voice tag or guided guest enrolment (`omi_voices._resolve_person`).

**Not a member acting, so not asked** (named by path in the scans):

| Module | Why |
|---|---|
| `person_extractor.py`, `named_relations.py` | extractors: they mint stubs and fill roles from what was said |
| `person_health.py` | a score |
| `contacts_store.import_card` | a card import |
| `named_guest_learning.py` | voice learning |
| `memory_forget_cascade.py`, and the undo in `omi_review_apply.unwrite` | the erasers: an erase is never refused |

`contacts_store.py` and `person_merge.py` are shared mechanics: automatic work and a member's action both run through them. A
member's action asks before it reaches them (the caller names the acting member); automatic callers name nobody.

## 2. What is asked [code]

| Change | Kernel |
|---|---|
| add, change, delete a contact (every entry point in section 1) | **asked first**; a refusal is final (`ContactRefused`, nothing written); down or slow = proceeds `not_checked` |
| add, change, remove a relationship on the screen | asked first, as a `contact.edit` of the contact whose page it is |
| merge | asked first about BOTH: `contact.edit` of the contact kept, `contact.delete` of the one folded in. If either is refused nothing is merged; with `ZOE_PERSON_MERGE_ENABLED` off it is the usual 404 and nothing is asked |
| Skybridge's "remember that <name> ..." | a new contact is a `contact.create` AND a `contact.edit`; both are asked before the contact is made, the fact saved to memory or the card changed. A refusal of either leaves nothing behind |
| a spoken correction (a name, "that's a pet") | asked once, as a `contact.edit`, before the row and its edges change |
| reading contacts | not asked, recorded nowhere |
| extractors, the health score, card import, voice learning, the erasers | not asked: nobody is acting |

How a refusal reaches the person: the screen gets 403 with a plain sentence; the brain's tools return an error result with
`status: refused`; a spoken turn says the sentence; the panel's introduce files no contact; an accepted offer and the day review
fail and keep the item pending.

**Flag off is main exactly:** a flag read; no kernel call, socket, thread or extra statement. All 38 entry-point scenarios give
the record captured from main (`tests/test_people_kernel_door.py`). With the flag on, the statements are still main's, with ONE
addition: on the screen's routes that hold only a contact's id (update, delete, the relationship routes, merge) the door reads
whose the stored contact is (`people_service.ask_about`), in the same scope the screen uses. A contact the caller cannot see is
not asked about (the route answers "not found" as before). A fault in that read raises: the change is not made unasked.

## 3. Who may do what [code; the table is the kernel's, `labs/zoe-os/zoe_os/people_door.py`]

This is **today's rule**, kept as it is.

| Contact | Who | Add | Change | Delete |
|---|---|---|---|---|
| any | the member whose book it is in | allow | allow | allow |
| private | anyone else, an adult included; the shared panel | refuse `not_owner` | refuse `not_owner` | refuse `not_owner` |
| household | anyone on the roster | allow | allow | allow |
| household | the shared panel, nobody identified | allow | refuse `not_owner` | refuse `not_owner` |
| any | someone not on the roster | refuse `unknown_person` | refuse `unknown_person` | refuse `unknown_person` |

**One line to switch, a household choice for the owner:** `people_door.HOUSEHOLD_CHANGE_ADULTS_ONLY` (off). Set, a household
contact can still be added by anyone, but is changed or deleted only by its owner or an adult.

**How this maps onto the stored rows.** The owner is `people.user_id`. A household contact is `visibility = family`. Statements
that name the member in their WHERE (the tools, the spoken paths, an accepted offer, the day review) act on the member's own
contact: the ask says owner = the member. Where such a caller reads only the id, the ask says `private` whatever the contact's
visibility; the decision is the same (the owner).

**What changes for the household when the flag goes on:** nothing a member can do today, except that the shared panel can no
longer change or delete a household contact (it can still add one), and a session with nobody identified is refused by the
kernel as well as by the role matrix.

## 4. Not yet behind the door [code: counted by `tests/test_people_one_door.py`]

* The side tables: contact fields, sources, links, merge reviews, synced cards, important dates, activities, gift ideas,
  bucket lists and custom fields: **55 statements in 6 modules**, written where they always were.
* The screen's two counters on `people` (mark-read, log-activity), allowed in the scan by their exact text.
* `named_guest_learning` can name a guest on a member's say-so; it is listed as voice learning and not asked.

## 5. What crosses the socket and reaches the log [code]

One ask per change: `door_ask` with `area=people`, the verb (`contact.create` / `contact.edit` / `contact.delete`), the acting
member's id (or `via=panel`), the contact's id, the id of the member whose book it is in, `household` or `private`, and an
erase stamp per member named. Never a name, a number, an address, a birthday, a note or a relationship. One log line:
`ZOE_OS_ACT area=people verb=... decision=... reason=... ask_ms=...`, every field from a closed set.

## 6. Ordered against a forget [code]

As the other areas: every caller takes `people_service.stamps_now()` as the first thing it does, before it reads the contact,
and hands it to the door (scanned over every module). The stamps are copied only when THIS area's door is on.

## 7. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it** (an older kernel has no contacts area: every ask ends
   `not_checked reason=protocol`).
2. The roster current, with roles.
3. `ZOE_OS_PEOPLE=kernel` in zoe-data's environment, then a zoe-data restart.
4. Live check with a demo member: add, change and delete a contact by voice and on the screen (`decision=allow` lines); try to
   delete a household contact at the panel (403, one `decision=refuse reason=not_owner` line, the contact still there).

Turn off: remove the flag (or set `off`), restart zoe-data. Nothing else to undo.

## 8. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken contact paths are touched).
* The kernel's record of a contact change has no undo and no "explain" wording yet.
* Section 4.
