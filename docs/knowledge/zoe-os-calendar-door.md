---
type: Reference
title: Zoe OS calendar door - adding, changing and deleting an event asks the kernel first
description: ZOE_OS_CALENDAR=kernel (default off). The calendar is the fifth area on the one Zoe OS door. Where calendar data lives, every writer, the outside-calendar picture (inbound only), what is asked and what is not, the permission table and how it maps onto the stored event, what crosses the socket, and the switch-on steps.
tags: [zoe-os, kernel, calendar, door, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS calendar door

**Status 2026-11-20: built, flag-dark (`ZOE_OS_CALENDAR` unset = `off`). Nothing on this page is live.** Lists, reminders,
alarms and timers came first ([zoe-os-alarms-door.md](zoe-os-alarms-door.md)); the calendar uses the same mechanism. The design
is the cut-over plan's ([zoe-os-cutover-plan.md](../architecture/zoe-os-cutover-plan.md), "Changed 2026-11-18"): the kernel
decides and records; the data stays where it is.

## 1. Where the calendar lives, and its one door [code]

| Table | Holds |
|---|---|
| `events` | one row per event. `user_id` = whose calendar it is on; `visibility` = `family` (the whole house sees it), `personal`, or `busy` |
| `event_attendees`, `event_alarms` | an event's guests and its stored alerts (nothing fires those) |
| `calendars` | a subscribed feed and its sync state |

Everything a person does to an event goes through `calendar_service.py`:

| Entry point | Door function |
|---|---|
| the screen: create, edit, delete (`routers/calendar.py`) | `create_event_record`, `update_event_record`, `delete_event_record` |
| the brain's tools (`mcp_server.py`) | `create_event_record`, `tool_update_event_record`, `tool_delete_event_record` |
| spoken: "add ...", "move ...", "delete ..." (`skybridge_service.py`) | `spoken_create_event_record`, `spoken_move_event_record`, `spoken_delete_event_record` |
| the spoken quick-add (`intent_router.py`) | `create_event_record` |
| an accepted suggestion (`pending_suggestions.py`) | `suggested_create_event_record` |
| the pendant day review, on approval (`omi_review_apply.py`) | `create_event_record` |
| an `.ics` file import (`calendar_ical.store_parsed`; nothing in production calls it yet) | `create_event_record` |

Each entry point keeps the statement it had (the placeholders, WHERE clauses and columns differ; unifying them is a separate
decision). `tests/test_calendar_one_door.py` pins the one door over every module; `tests/test_calendar_same_as_main.py` pins
what every entry point does against a record captured from main. The anticipation loop reads events and writes none.

**Not a person, so outside the ask** (named by path in the scan):

* `calendar_feed.py`: reconciling a subscribed feed. It is the system's own work, like the alarm ring engine.
* `calendar_forget.py`, `memory_forget_cascade.py`: the erasers.

## 2. Outside calendars: inbound only [code]

Zoe does not push events anywhere. The only outside calendar is a **read-only `.ics` subscription that is fetched**
(`calendar_feed.py`, `ZOE_CALENDAR_FEED`, default off; nothing schedules a sync yet). There is no CalDAV, Google, Microsoft or
Home Assistant write, and no write-back: `calendar_read.is_writable` refuses any change to a fed or provider event ("change it
there"). So no calendar change sends data off the box, with the flag on or off.

**When write-back is built**, the rule is already set: the push to the outside calendar must come AFTER the door's ask, inside
the same door function, so a kernel refusal stops the outside write too. It must not be added in a caller.

## 3. What is asked, and what is not [code]

| Change | Kernel |
|---|---|
| add, change, delete an event (every entry point in section 1) | **asked first**; a refusal is final (`EventRefused`, nothing written); down or slow = proceeds `not_checked` |
| an event's guests and alerts | written under the event's own ask (the screen always changes the event row with them) |
| reading the calendar | not asked, recorded nowhere |
| the feed's reconcile, the erasers | not asked: nobody is acting |

How a refusal reaches the person: the screen gets 403 with a plain sentence (the one app handler); the brain's tools return an
error result with `status: refused`; a spoken turn says the sentence; an accepted suggestion and the day review fail and keep
the item pending, as any other failure there does.

A spoken move or delete names the speaker in its statement, so on someone else's event it changes nothing: the kernel then
hears `failed`, never `done`. An accepted suggestion writes inside its own transaction: the kernel hears `done` only once that
has committed.

**Flag off is main exactly:** a flag read; no kernel call, socket, thread or extra statement. All 59 entry-point scenarios give
the record captured from main (`tests/test_calendar_kernel_door.py`). With the flag on and the kernel allowing, the statements
are still main's: every caller hands the door the event it already read, so the door reads nothing of its own.

## 4. Who may do what [code; the table is the kernel's, `labs/zoe-os/zoe_os/calendar_door.py`]

| Event | Who | Add | Change, delete |
|---|---|---|---|
| any | the person whose calendar it is on | allow | allow |
| private | an adult, when it is a child's | allow | allow |
| private | anyone else; the shared panel | refuse `not_owner` | refuse `not_owner` |
| household | an adult | allow | allow |
| household | a child or a guest (not theirs) | allow | refuse `adults_only` |
| household, on a person's calendar | the shared panel, nobody identified | allow | refuse `adults_only` |
| household, the house's own | the shared panel | allow | allow |
| any | someone not on the roster | refuse `unknown_person` | refuse `unknown_person` |

**How this maps onto the stored event.**

* *Whose calendar* is `events.user_id`. There is no separate "who made it" column: the person whose calendar an event is on
  is the one who made it, so "its creator" in the table is that person.
* *Household* is `visibility = family`. `personal` and `busy` are both private (`busy` shows others only that the time is taken).
* *The house's own.* An event made at the shared panel is stored under the household account (`family-admin`), which is not a
  person. The ask names no owner for it, and the panel is its maker: the panel may change and delete those, and no others.
* A caller with nobody identified (the panel writes as the household viewer; a guest id) asks as the panel, never as a person.

**What changes for the household when the flag goes on** (zoe-data's own rules still run first, and the table is a ceiling on top):

* Today any signed-in member can change or delete any household event from the screen. With the door on, a child or a guest
  can do that only to their own; an adult still can to anyone's.
* Today the panel can change or delete any household event. With the door on, only the ones made at the panel.
* An adult changing a child's private event is allowed by the table, but zoe-data still does not show one member another's
  private event (404), so nothing opens up.

Decisions taken here: "an adult for a child" is any adult, by role (the kernel also knows guardians; narrowing is one line).
A guest on the roster may add a household event and keep their own.

## 5. What crosses the socket and reaches the log [code]

One ask per change: `door_ask` with `area=calendar`, the verb (`event.create` / `event.edit` / `event.delete`), the acting
member's id (or `via=panel`, never a device id), the event's id, the id of the member whose calendar it is on (none for the
house's own), `household` or `private`, and an erase stamp per member named. Never a title, a day, a time, a place, a note or
a guest. One log line: `ZOE_OS_ACT area=calendar verb=... decision=... reason=... ask_ms=...`, every field from a closed set.

## 6. Ordered against a forget [code; lab against the real kernel table]

The mechanism is the lists' ([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 4a). Specific here: a change to a stored
event names its owner, who is read from the row by the caller. So every caller takes its stamps
(`calendar_service.stamps_now()`) as the first thing it does, before it reads the event, and hands them to the door; a
structural test holds every caller in every module to that, and to saying who is acting.

## 7. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it**: an older kernel has no calendar area (every ask ends
   `not_checked reason=protocol`: safe, nothing decided).
2. The roster current, with roles (it is while lists are on). Children must be marked as children, or the household rows of
   the table treat them as adults.
3. `ZOE_OS_CALENDAR=kernel` in zoe-data's environment, then a zoe-data restart.
4. Live check with a demo member: add, move and delete an event by voice and on the screen (`decision=allow` lines); add one
   at the panel and delete it there (allowed); try to delete a member's household event at the panel (403, one
   `decision=refuse reason=adults_only` line, the event still there).

Turn off: remove the flag (or set `off`), restart zoe-data. Nothing else to undo.

## 8. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken calendar paths are touched).
* The kernel's record of a calendar change has no undo and no "explain" wording yet.
* An `.ics` file import has no route; when one is built it must pass the importing member as `actor`.
* Write-back to an outside calendar does not exist (section 2).
