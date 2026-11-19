---
type: Reference
title: Zoe OS reminders door - every reminder change asks the kernel first
description: ZOE_OS_REMINDERS=kernel (default off). The second area on the one Zoe OS door. reminder_service is the one writer of the reminders table; with the flag on each change asks the kernel (ids only), the kernel decides by role and records, zoe-data writes and reports. The permission table, what crosses the socket, how it is ordered against a forget, what a refusal looks like at each caller, and the switch-on steps.
tags: [zoe-os, kernel, reminders, door, forget, privacy, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS reminders door

**Status 2026-11-20: built, flag-dark (`ZOE_OS_REMINDERS` unset = `off`). Nothing on this page is live.** Lists were the first
area ([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 4a, live); reminders are the second, on the same mechanism. The
design is the cut-over plan's ([zoe-os-cutover-plan.md](../architecture/zoe-os-cutover-plan.md), "Changed 2026-11-18"): the kernel
decides and records; the reminder rows stay in Postgres.

## 1. One door [code]

`services/zoe-data/reminder_service.py` holds every write of the `reminders` table: `create_reminder_record`,
`update_reminder_record`, `delete_reminder_record`, `snooze_reminder_record`, `acknowledge_reminder_record`. The screen's REST
routes, the brain's MCP tools, the spoken path, an accepted suggestion and the background makers all call them
(`tests/test_reminders_table_one_writer.py` fails for any other module that writes the table). The brain's MCP tools write different rows from
the screen's and always have (`tool_update_reminder_record`, `tool_delete_reminder_record`, `tool_snooze_reminder_record`); those
rows are kept exactly and sit behind the same door (edit, delete, snooze). Moving the writes changed no behaviour at any entry
point: `tests/test_reminders_same_as_main.py` compares each with a record captured from main. Nothing in the scheduler writes the
table: jobs live in `proactive_scheduled`.

## 2. What the door does with the flag on [code]

Each of the five functions, in order: takes the erase stamps (first statement, before anything it awaits); runs zoe-data's own
checks (role, ownership, a past time), which are unchanged; asks the kernel (`_gate`); cancels or reschedules the job and writes;
reports `done`, or `failed` if a write raised. Inside a caller's own transaction (an accepted suggestion) the report waits until
that transaction has committed, and is `failed` if it rolled back.

| The kernel says | What happens |
|---|---|
| allow | the change is made; the outcome is reported after the write |
| refuse | `ReminderRefused`: FINAL. Nothing is written and no scheduled job is touched |
| nothing in 150 ms, down, an older kernel, `erased` | the change is made as before; one `not_checked` log line; nothing is reported |

A refusal at each caller: REST 403 with a plain sentence (the app's one handler for every area); an MCP tool error result
(`status: refused`); a spoken sentence on the fast path. The sentences name nobody and quote nothing.

**Flag off is main exactly:** a flag read, no kernel call, no socket, no thread, and the same statements as before the door
existed (`tests/test_reminders_kernel_door.py` compares them with a fixture captured from the pre-door code). The screen's
doors read nothing of their own from the database even when on: the row every edit already loads names who the reminder is for.
The one exception is the snooze tool, whose statement names no owner: with the flag on its door reads the owner first (a
database fault there raises; it never skips the kernel).

## 3. Who may do what [code; the table is the kernel's, `labs/zoe-os/zoe_os/remind_door.py`]

A reminder belongs to the person it is for (`reminders.user_id`). `visibility = 'family'` means it is addressed to the household.
The table holds no separate "who made it" column, so the person it is for is the only member a reminder names.

| Reminder | Who | Create, edit, delete | Done, snooze |
|---|---|---|---|
| any | the person it is for | allow | allow |
| private | an adult, when it is for a child | allow | allow |
| private | anyone else; the shared panel | refuse `not_owner` | refuse `not_owner` |
| household | an adult | allow | allow |
| household | a child or a guest (not theirs) | refuse `adults_only` | allow |
| household | the shared panel, nobody identified | refuse `adults_only` | allow |
| any | someone not on the roster, as actor or as the person it is for | refuse `unknown_person` | refuse `unknown_person` |

Decisions taken here, and why:

* "An adult for a child" is any adult of the house, by role. The kernel also knows each child's guardians; narrowing this row to
  guardians is a one-line change if the owner wants it.
* Done and snooze count as answering an alert, so the panel and a child may do them to a household reminder. Create, edit and
  delete change what the house is told.
* The kernel's table is a ceiling. zoe-data's own rule runs first and is narrower today: another member's private reminder is
  not found (404), and done and snooze are the owner's only (403). So two rows the kernel allows cannot be reached yet: an
  adult acting on a child's private reminder, and the panel or a child completing someone else's household reminder. Opening
  them is a change to `load_for_actor`, not to the kernel.

## 4. What crosses the socket and reaches the log [code]

One ask: `door_ask` with `area=reminders`, the verb, the acting member's id (or `via=panel`, never a device id), the
reminder's id, the id of the person it is for, `household` or `private`, and an erase stamp per member named. One report:
the ask's number, `done` or `failed`, the same stamps. Never a title, a day, a time or a name. One log line per change:
`ZOE_OS_ACT area=reminders verb=... decision=... reason=... ask_ms=...`, every field from a closed set.

## 5. Ordered against a forget [code; lab against the real kernel]

The mechanism is the lists' ([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 4a), unchanged: every ask and report carries
the erase stamp of every member it names, the kernel records nothing for a stamp that is not strictly newer than that member's
erase mark, and an ask is never queued while a member erase is running. What is specific to reminders:

* the stamps are taken as the first statement of each door function, before the row that names the owner is read;
* `create_reminder_record` is told who the reminder is for, so every caller takes `stamps_now()` as its own first statement
  and passes it (`stamps_at`); a structural test scans every module for a caller that does not;
* a whole-member forget now carries its stamp while ANY door is on, not only the lists';
* a member the roster does not name yet triggers the roster rewrite, for the actor and for the person the reminder is for.

## 6. Switch-on (owner steps; nothing here is done by the PR)

Needs, in order:

1. This change deployed, and **the kernel unit restarted on it** (`zoe-os-shadow-kernel.service`). A kernel from before this
   change does not know `door_ask`: every reminders ask would end `not_checked reason=protocol` (safe, but nothing is decided).
   The live lists door keeps working across the restart order either way: its wire ops did not change.
2. The roster current (`people.json`), with roles. It already is while lists are on; `os_shadow.py --write-roster-from-accounts`
   rewrites it by hand.
3. `ZOE_OS_REMINDERS=kernel` in zoe-data's environment, then a zoe-data restart.
4. Live check with a demo member: make, snooze, complete and delete a reminder and read four
   `ZOE_OS_ACT area=reminders ... decision=allow` lines in `~/.zoe-logs/zoe-data.app.log`; then one refusal (a demo child
   deleting a household reminder of a demo adult's: 403, the row unchanged).

Turn off: remove the flag (or set `off`), restart zoe-data. Nothing else to undo: the rows never left Postgres.

## 7. Not done, not proven

* Not run live. Proven against the real kernel process in `tests/test_os_shadow_contract.py`, and against a fake one elsewhere.
* The voice replay gate has not been run for this change (the spoken reminder path is touched: see the PR).
* The kernel's record of a reminder change has no undo and no "explain" wording yet.
* The ask budget is the door default (150 ms); reminders have no flag of their own for it.
* With the door on, the watch-only `reminder_create` shadow comparison stops (the door is then the one record).
