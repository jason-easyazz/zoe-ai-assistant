---
type: Reference
title: Zoe OS routines door - a routine's changes ask the kernel, its ticks are told afterwards
description: ZOE_OS_ROUTINES=kernel (default off; needs ZOE_ROUTINES=on to matter). Where routines live and their one writer, what asks first (create, add a step, a one-off), what is made first and told afterwards (tick, skip, snooze, undo), the table with its two switches (a child does not change a routine set up for them, as the owner's acceptance words say), what crosses the socket, the card API, and the switch-on steps.
tags: [zoe-os, kernel, routines, door, children, flag-dark]
timestamp: 2026-11-22T00:00:00Z
---

# Zoe OS routines door

**Status 2026-11-22: built, flag-dark.** Routines themselves are flag-dark too (`ZOE_ROUTINES`, `ZOE_ROUTINE_VOICE`, both
off: [routines.md](routines.md)). Nothing on this page is live.

## 1. Where routines live, and their one writer [code]

Four tables (`routines`, `routine_steps`, `routine_days`, `routine_day_steps`), written only by `routines_store.py`
(`tests/test_routines_same_as_main.py` scans every module). A routine belongs to the member it is FOR (`user_id`, the
subject); `created_by` is who set it up; `visibility` is `personal` or `family`. Its entry points and the conversation tier
are pinned against main by 36 scenarios (`tests/fixtures/routines_as_on_main.json`).

## 2. What the door does [code; `routine_door.py`, called from `routines_store.py`]

| A member | Store entry point | Kernel |
|---|---|---|
| creates a routine | `create_routine` | asked first (`routine.create`) |
| adds a template step, adds a one-off line | `add_step`, `add_one_off` | asked first (`routine.edit`, or `routine.edit_given` when someone else set the routine up for its subject) |
| ticks, skips, snoozes, unticks a step (said or tapped) | `_apply` (behind `act_said`, `undo_said`, `act_tapped`) | **made first, told afterwards** (`routine.tick` / `skip` / `snooze` / `undo`) |

* Asked first, meaning before anything is written and AFTER every check of the store's own (who may edit, `MAX_STEPS`, a
  closed day, the write gate's erase and forgotten-words checks): a change the store itself refuses is never asked and comes
  back with the store's own refusal. A "no" is final (`RoutineRefused`: nothing is written; the card API answers 403 with a plain sentence, the
  spoken one-off says it). Kernel down, old or slow: the change proceeds, `not_checked`. The kernel then hears `done` or
  `failed`.
* Told afterwards: never asked, never delayed. The store's own rules run first and are not the kernel's: a medicine line
  needs its verified subject, a closed day is settled, a said tick acts only on the step Zoe just named, a personal line is
  its subject's alone. A change the store refused is never told. A kernel "no" only makes the record say objected.
* There is no delete or rename of a routine in the store yet; `routine.delete` and `routine.delete_given` are in the table
  for when there is.
* The erasers (`forget_user`, `erase_entity`) are automatic: not asked, not told.

**Flag off is main exactly:** each entry point reads one flag; all 36 scenarios give main's record. Door on and the kernel
allowing: the same record.

## 3. The table [code; `labs/zoe-os/zoe_os/routines_door.py`]

| Routine | Who | Create / edit / delete | Tick / skip / snooze / undo |
|---|---|---|---|
| any | its subject | allow, except a CHILD changing a routine someone else set up for them: refuse | recorded |
| private | an adult, for a child's routine | allow | objected (an adult does not tick a child's step for them) |
| private | anyone else | refuse | objected |
| household | anyone on the roster | allow | recorded |
| household | the shared panel | refuse | recorded (the fridge rule) |
| any | not on the roster | refuse | objected |

**A child and a routine an adult set up for them.** The kernel rule is ON as shipped: the child ticks, skips and snoozes it
and does not edit or delete it. This follows the owner's acceptance words ([routines.md](routines.md), acceptance line 10:
"Riley's routine can be ticked by Riley. Only a parent (admin) can change who is told or what the how-to says"), NOT the
store's looser code, which lets the subject edit their own routine whoever made it. One line restores the looser rule
(`CHILD_MAY_CHANGE_GIVEN = True`). A second switch, off, is the stricter household rule (`HOUSEHOLD_CHANGE_STRICT`: only a
household routine's subject or an adult edits or deletes it).

## 4. What crosses the socket and reaches the log [code]

The verb, the acting member's id (or `via=panel`), the routine's id, its subject's id, `household` or `private`, the erase
stamps. Never a routine's name, a step's words or a how-to. One log line: `ZOE_OS_ACT area=routines verb=... decision=...`.

## 5. The card API [code; `routers/routines.py`]

Behind `ZOE_ROUTINES=on` (404 otherwise): `GET /api/routines/today` (the caller's routines for today with their lines; a
signed-out panel sees household ones) and `POST /api/routines/steps/act` (`done` / `skip` / `snooze` / `undo` on one line).
A tap is a tick: it goes through `routines_store.act_tapped`, so every store rule and the door apply. A medicine line is
ticked from the card only in its subject's own signed-in session. No panel UI is built.

Who is asking: a member's own session, or the panel with nobody identified, PROVED by the request (`_at_the_panel`: the strict
`registered` test of `panel_principal.is_panel_guest`; a device token never over the Cloudflare tunnel). A device token bound
to a member is judged as the device, never as that member, and being a member's does not make it the panel. An auth outage is
nobody's evidence: authentication's fail-closed 503 is the route's answer and a degraded principal is 403; only "no valid
session" (401) reads as nobody identified. Pinned on the real path by
`test_a_device_token_bound_to_a_member_is_never_the_member_and_is_the_panel_only_where_a_device_token_is` and
`test_an_auth_outage_is_nobodys_evidence_even_at_a_registered_panel_address`.

## 6. Switch-on (owner steps)

1. This change deployed and the kernel unit restarted on it.
2. The roster current, with children stated as children.
3. `ZOE_ROUTINES=on` (and `ZOE_ROUTINE_VOICE` as wanted), then `ZOE_OS_ROUTINES=kernel`; restart zoe-data.
4. Live check with a demo adult and child: the adult creates a routine for the child (allowed); the child ticks a step
   (done, recorded) and tries to add a step (refused, "needs a grown-up"); with the kernel stopped a tick still happens at once.

## 7. Not done, not proven

* Not run live; the voice replay has not been run.
* With the door OFF the store still lets a child subject add steps to a routine an adult set up, and still hides a child's
  personal routine from the adult who created it (so the adult cannot edit it): both are main's behaviour, listed as faults.
* The routines feature programme beyond this (carer links and consent, the missed-step alert, readings, the how-to check,
  the scorecard) is not part of this change.
