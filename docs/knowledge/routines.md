---
type: spec
title: Routines, stage 1 - a daily checklist per person, ticked by voice or on the panel
description: Acceptance spec + build record for routines stage 1 (ROADMAP Next 6, IDEAS "Routines"). A routine is a named, ordered checklist for one person tied to a moment of the day that starts fresh each day; one-off lines join one day ("take the drill to work tomorrow"); a tick is never a guess (wrong ticks must be 0); step text is stored and returned verbatim. Flag ZOE_ROUTINES=off|on, default off.
tags: [routines, checklist, voice, panel, forget, spec]
timestamp: 2026-11-14T00:00:00Z
---

# Routines, stage 1

Synthetic people only: Alex (adult), Sam (adult with a medicine step), Morgan (Sam's carer), Riley (child).

## Acceptance - true when done

In the owner's words. Each line is checkable in the lab with synthetic members.

1. Alex says "remind me to take the drill to work tomorrow" and Zoe says "Added to tomorrow's morning routine: take the drill."
   Tomorrow's morning card shows it as "today only"; the day after, it is gone. "Remind me at 7 to take the drill" still sets an
   ordinary 7:00 reminder.
2. In the morning Alex says "what's next?", hears one step at a time, and "done", "skip" and "later" do exactly that to the step
   she just named. "Yep" said when she has not just named a step ticks nothing.
3. Nothing on any routine is ever ticked that the person did not tick. Over the lab week, wrong ticks = 0.
4. Zoe never speaks first about a routine. The card and the phone (if Alex turned it on) are the only prompts.
5. Sam's tablets step is missed. The card shows it, then a second time, and then Morgan gets one message saying a step on Sam's
   morning routine hasn't been done. Morgan's lock screen never shows the word "tablets" unless Morgan turned on full text.
6. Morgan is told nothing until Sam has said yes and Morgan has said yes. "Stop telling Morgan" stops it at once.
7. A how-to Morgan wrote is shown and read back word for word. If Zoe would say it differently, Morgan is shown that when saving.
8. Sam's reading outside Morgan's range gets Morgan's own instruction, word for word, and Morgan is told. Zoe adds no advice and
   no number of her own.
9. "I took them twice by accident" gets the medical safety answer, not a tick.
10. Riley's routine can be ticked by Riley. Only a parent (admin) can change who is told or what the how-to says.
11. After "delete my data" for Sam, no routine, step, day, reading, carer link or alert for Sam remains, and the forget inventory
    says so.
12. The scorecard prints steps prompted, confirmed and missed per day, wrong ticks, and time to carer, or says "unmeasured".

## What is built (by PR)

| PR | what | acceptance it moves |
|---|---|---|
| 1a | the tables (alembic `0082_routines.py`), routines + steps in `services/zoe-data/routines_store.py`, both erasers, forget reach | 11 (for the four tables that exist) |
| 1b | the day: lazy day copies, one-off lines, the walk (`note_named`) and the said / tapped ticks | 3 (store-level tick rule) |

### PR 1 (1a + 1b) - the store

- **Tables** (alembic `0082`): `routines` (the subject in `user_id`, an anchor, an optional window and days rule, `personal` by
  default), `routine_steps` (template lines), `routine_days` (one routine's copy for one household date), `routine_day_steps` (that
  day's lines with their state, who ticked, whether the voice was verified, how often Zoe named it). Readings, carer links and the
  carer outbox are later PRs with their own migration.
- **Lazy day copies.** No night job. A day copy is made on first need; template steps are copied in once the day has come, so a
  one-off for tomorrow creates tomorrow's row early and the template edit made tonight still applies. Opening a new day settles
  the routine's earlier open days: every line still pending there is logged `missed`.
- **The tick rule is in the store.** A SAID tick (`act_said`) cannot name a step: it acts on the one step Zoe named last
  (`note_named`) for the same member in the same session within 120 s, and nothing else. A TAP (`act_tapped`) names its row.
  A `medicine` line is ticked only by its subject with a verified voice or the subject's own member session; a family routine may
  not hold one; a household tap at the panel can tick, skip or snooze a family routine's line (the lists "fridge rule") and never
  a personal one. Two racing changes never double-act (`WHERE state = <the state read>`).
- **Visibility** matches lists and reminders: `visibility = 'family' OR user_id = <viewer>`; a guest or an unidentified panel sees
  family routines only; a personal routine is invisible (not 403) to anyone else.
- **Verbatim.** Step text, one-off text and routine names are stored and returned exactly as entered: no strip, no re-case, no
  number touched (a test round-trips dashes, a fraction, `> ~ | * _`, a degree sign, an ISO date and edge spaces). CAVEAT for the
  spoken lane: `tts_waterfall._clean_for_speech` rewrites dashes to ", ", strips `* _ ~ | >`, speaks ISO dates as words and ° as
  "degrees", so "1–2 tablets" would be SPOKEN "1, 2 tablets". The save-time "Zoe will say it like this" check is PR 8; until then a
  step read aloud is not guaranteed word for word.
- **Forget.** All four tables are `reached` in `forget_reach.py`: `routines_store.forget_user` (called by
  `MemoryService.delete_user`, fail closed, under the member's erase gate) deletes every routine the member is the subject of, with
  its steps, days and lines, and blanks the member as actor or creator on anyone else's; `routines_store.erase_entity` (the
  `routines` step of `memory_forget_cascade`) deletes a routine named for the entity and any line naming it.
- **Logs**: `ROUTINE event=<created|step_added|one_off|prompted|ticked|skipped|snoozed|undo|missed|refused> kind= via= verified=
  member=<hash8> routine=<hash8>`; codes and counts only, never a step's words or a name (a test asserts it).

## The numbers

| number | read from |
|---|---|
| steps prompted | `ROUTINE event=prompted` count (each time Zoe names a step); `routine_day_steps.prompts` |
| steps confirmed | `ROUTINE event=ticked`, split by `via=said|tapped` |
| steps missed | `ROUTINE event=missed ... n=<count>` (logged when the next day is opened) |
| wrong ticks (must be 0) | `ROUTINE event=undo` + `event=undo_window quick=1` (an undo within 10 min of a said change); the DB check that no `medicine` line is `done` with `verified = 0` |
| refused ticks | `ROUTINE event=refused reason=<unverified|not_the_subject>` |

Nothing is live: the flag is off and nothing reads these yet. The scorecard section is PR 9.

## Owner decisions (stage 1)

1. Ship stage 1 with no unprompted speech at all (card, phone, in-turn only)? Recommended: yes.
2. Carers limited to adult household members with an account? Recommended: yes.
3. A carer alert waits for the end of the carer's quiet hours unless the carer turned on "even in quiet hours" for that step?
   Recommended: yes.
4. An unverified voice "done" on a medicine step: refuse and point to the card? Recommended: yes (other steps: accept it from the
   person whose walk is open).
5. A blood-sugar reading on a carer-set reading step: say the carer's instruction instead of the floor's general advice, still
   adding the floor's "ring the emergency number if ..." sentence for very low or very high readings? Recommended: yes.
6. Every routine store erased by "delete my data", unlike reminders and lists today? Recommended: yes (built that way in PR 1).
