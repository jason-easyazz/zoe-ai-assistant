---
type: Reference
title: Zoe OS alarms and timers door - alarm changes ask the kernel first; silencing and timers are told afterwards
description: ZOE_OS_ALARMS=kernel (default off). Alarms and timers are the third and fourth areas on the one Zoe OS door. Where each is stored, what asks first and what is told afterwards (a ringing alarm or timer is never held up by the kernel), the permission table, what crosses the socket, and the switch-on steps.
tags: [zoe-os, kernel, alarms, timers, door, safety, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS alarms and timers door

**Status 2026-11-20: built, flag-dark (`ZOE_OS_ALARMS` unset = `off`). Nothing on this page is live.** Lists were the first
area, reminders the second ([zoe-os-reminders-door.md](zoe-os-reminders-door.md)); alarms and timers share one flag and the same
mechanism. The design is the cut-over plan's ([zoe-os-cutover-plan.md](../architecture/zoe-os-cutover-plan.md), "Changed
2026-11-18"): the kernel decides and records; the data stays where it is.

## 1. Where they live, and their one door [code]

| | Stored | The one writer | Callers |
|---|---|---|---|
| Alarms | Postgres: `alarms`, `alarm_events`, `alarm_overrides` | `alarms.py` | `routers/alarms.py` (the screen), `alarm_voice.py` (spoken, and the brain's `alarm_set` tool), `anticipation.py`, the ring engine |
| Timers | one in-process registry, snapshotted to `~/.zoe/timers.json` | `skybridge_service._TimerStore` | the spoken path and `create_timer_direct`, the screen's cancel route (`cancel_timer_by_id`, `cancel_any_timer`) |

Both already had one writer. `tests/test_alarms_timers_one_door.py` pins it over every module, and
`tests/test_alarms_timers_same_as_main.py` pins what every entry point does against a record captured from main.

## 2. What asks first, and what is told afterwards [code]

| Change | Kernel | Why |
|---|---|---|
| Alarm: set, edit (one that neither silences nor turns it off), a day's override and its undo, delete | **asked first**; refusal is final (`AlarmRefused`), down or slow = proceeds `not_checked` | an ordinary change: the kernel can decide it |
| Alarm: snooze, dismiss | **made first, told afterwards** | a ringing alarm must always be silenceable by whoever is standing there |
| Alarm: an edit made while it rings or is snoozed (every edit lands it idle), and any edit that turns it off | **made first, told afterwards** (`alarm.stop` when it was sounding, else `alarm.edit`) | turning an alarm off on the screen is how many people silence it: the kernel can neither delay nor refuse it |
| Timer: every change (set, add or take time, cancel, the panel's dismiss of a ringing one) | **made first, told afterwards** | the timer path is synchronous end to end, so there is nowhere to wait for an answer; and a ringing timer must be silenceable |
| The ring engine (ring, auto-stop, reschedule) | nothing | it acts for nobody |

"Told afterwards" (`os_shadow.record_door`) never blocks, never raises and never refuses. The write is finished before the
kernel hears of it; the door's own thread then asks and, when the table allows it, closes the ask `done` at once. The log line
says `decision=recorded`, `decision=objected` (the table says no; **the change stands**) or `decision=not_checked`.

**What this means for timers:** the kernel records and judges timer changes but cannot stop one. Making a timer set or cancel
refusable needs the timer path made asynchronous first; that is a separate change, not part of the move.

**Flag off is main exactly:** a flag read; no kernel call, socket, thread or extra statement. All 44 entry-point scenarios give
the record captured from main (`tests/test_alarms_kernel_door.py`). With the flag on and the kernel allowing, the statements are
still main's: every caller hands the door the alarm it already holds, so the door reads nothing of its own.

## 3. Who may do what [code; the table is the kernel's, `labs/zoe-os/zoe_os/alarm_door.py`]

An alarm or timer belongs to the person it is for. One that belongs to the house has no owner: production keeps a household alarm
under the marker `__household__` and a guest's timer under a guest principal, and neither is a person, so the ask says
`household` and names no owner.

| Alarm or timer | Who | Set, edit, delete / cancel | Snooze, stop |
|---|---|---|---|
| any | the person it is for | allow | allow |
| private | an adult, when it is for a child | allow | allow |
| private | anyone else on the roster | refuse `not_owner` | allow |
| private | the shared panel, nobody identified | refuse `not_owner` | allow |
| household | anyone on the roster; the shared panel | allow | allow |
| any | someone not on the roster | refuse `unknown_person` | refuse `unknown_person` (recorded as `objected`; the silence still happened) |

Decisions taken here:

* A household alarm or timer can be set, changed and cancelled by anyone in the house and by the shared panel. That is today's
  rule (`alarms.can_edit`), and the wall panel is where the house's alarms are set: an "adults only" row would switch that off.
* "An adult for a child" is any adult, by role (the kernel also knows guardians; narrowing is one line).
* The table is a ceiling. zoe-data's own rules run first and are unchanged: another member's private alarm is not found (404),
  and the panel may silence any ringing alarm but not edit a private one.

## 4. What crosses the socket and reaches the log [code]

One ask per change: `door_ask` with `area=alarms` or `timers`, the verb, the acting member's id (or `via=panel`, never a
device id), the alarm's or timer's id, the id of the member it is for (none for the house's), `household` or `private`, and
an erase stamp per member named. Never a label, a time, a repeat rule or a sound. One log line:
`ZOE_OS_ACT area=alarms|timers verb=... decision=... reason=... ask_ms=...`, every field from a closed set.

## 5. Ordered against a forget [code; lab against the real kernel table]

The mechanism is the lists' ([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 4a). Specific here: every alarm writer is
handed the alarm row by its caller, so the stamps must be older than that read. The actor (`alarms.Viewer`) is made first thing
in a request and carries the erase stamps of that moment; a structural test holds every caller to passing its actor and making
it before anything it awaits. A timer change is stamped as it is told (nothing is awaited between reading the timer and stamping).

## 6. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it**: an older kernel has no alarms or timers area (every ask ends
   `not_checked reason=protocol`: safe, nothing decided).
2. The roster current, with roles (it is while lists are on).
3. `ZOE_OS_ALARMS=kernel` in zoe-data's environment, then a zoe-data restart.
4. Live check with a demo member: set, edit and delete an alarm (three `decision=allow` lines); let one ring and dismiss it at the
   panel (it stops at once; one `verb=alarm.stop decision=recorded` line); set and cancel a timer (two `recorded` lines).

Turn off: remove the flag (or set `off`), restart zoe-data. Nothing else to undo.

## 7. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken alarm and timer paths are touched).
* Timers are recorded, not enforced (section 2).
* The kernel's record of an alarm or timer change has no undo and no "explain" wording yet.
* While the door is on, the watch-only `alarm_create` shadow comparison stops (the door is then the one record).
