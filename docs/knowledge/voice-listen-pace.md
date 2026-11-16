---
type: Reference
title: Listen pace - Zoe tunes her own end-of-turn wait
description: The owner's "could she auto adjust this setting?". Per panel (and, inside a conversation window, per verified member) Zoe moves one integer level L-3..L+4 in 80 ms steps from counts of talk after the close - lengthen fast, shorten only after 40 clean turns, inside a floor and a ceiling, with a spoken override. The rule, the numbers, the gate (a week of instrument data first), the acceptance list in the owner's words, and what is NOT built yet. Flag ZOE_LISTEN_PACE (default off).
tags: [voice-path, panel, endpointing, flag-dark]
timestamp: 2026-11-14T00:00:00+08:00
---

# Listen pace

## What it is

When the person stops talking, the panel waits a moment before it decides they are done (the end-of-turn tail). Wait too
little and she cuts people off mid-thought; wait too long and every answer starts late. Today the wait is one fixed setting
for everyone (deep tail 640 ms, quiet tail 800 ms, clean-tail gate off). Listen pace lets Zoe tune it herself, from one
signal: **did the person keep talking after she closed the recording?**

Status: **built, dark, unwired.** The decision core (`services/zoe-data/listen_pace.py`) and its tests exist. Nothing on the
panel or in the voice path calls it yet. Flag `ZOE_LISTEN_PACE` = `off` (default) | `shadow` | `on`.

## The rule

- **One level per subject**, `L-3 .. L+4`, in 80 ms steps (one VAD chunk; the daemon ceil-rounds tails, so a finer step is
  fiction). L0 is today's household default.

  | level | clean-tail gate | deep tail | quiet tail |
  |---|---|---|---|
  | L-3 | 560 | 640 | 800 |
  | L-2 | 640 | 640 | 800 |
  | L-1 | 720 | 640 | 800 |
  | **L0** | off | 640 | 800 |
  | L+1 | off | 720 | 880 |
  | L+2 | off | 800 | 960 |
  | L+3 | off | 880 | 1040 |
  | L+4 (ceiling) | off | 960 | 1200 |

  Shorter levels touch ONLY the clean-tail gate (it fires only on a clean fall after >= 480 ms of speech); the deep tail never
  goes under 640 (480 measured 8.3 % false cuts against 3.6 % at 640, upper bounds). The quiet tail never exceeds 1200 ms.
- **Who.** A subject is `(panel, verified member)` or `(panel, household)`. An unknown voice or a guest always gets the panel's
  household level. A verified member with no row yet starts at the household level. A child (by the member record's role,
  never inferred from the voice) starts at L+1 and never goes below L0: the CURRENT role is applied on every read, so a row
  stored before a role correction cannot bypass it.
- **The signal.** A *cut event* is `still_talking=1` on the daemon's `FIRST_SOUND` line (>= 3 speech chunks inside the fixed
  1.5 s window after the close), weight 1; the server's trailing-"and" hold merging a continuation, weight 0.5. A turn whose
  `still_talking` disagrees with its `talk_chunks` (that definition) is malformed and changes nothing.
- **Lengthen fast.** Two cut events in the last 20 eligible turns: one level longer, at once. One cut right after a reflex
  sound: one level longer, at once. Never rate-limited.
- **Shorten slow.** One level shorter after **40 consecutive clean eligible turns**; 80 after any lengthen; 120 to return to a
  level that caused a lengthen in the last 30 days; doubled wherever the reflex is on; at most one automatic shortening a
  day. Any cut resets the run to zero.
- **Unknown never shortens.** A turn is evidence only when every field is known: closed on silence (deep / quiet / clean /
  hesitation), not a barge seed, not frozen (media, night, noise, first wake, speculation, cap), muted ms known and zero, a
  monitor that listened, and (for a clean verdict) at least 1200 ms of the window observed. Anything malformed or unknown
  leaves the state byte-identical.
- **Which level a turn speaks for.** Every tail at a lower level is <= the same tail at a higher one, so a clean turn observed
  at level U proves every level <= U would have been clean too (the quiet run was unbroken and nobody spoke after U's later
  close). A clean turn therefore counts when it was observed at the subject's level or a longer one; in shadow every turn is
  an L0 observation, which is valid evidence for every shorter level. A clean turn observed at a SHORTER level is skipped. A
  cut always counts (the safe direction).
- **Drift.** A level SHORTER than the default drifts back up one step a week after 21 days unheard. A longer level never
  drifts down on its own (that would be shortening on no evidence).
- **Spoken override** (the API; the phrases are a later Skybridge PR):
  - "give me more time" / "you keep cutting me off": two levels longer, counted as two cut events, pinned for 90 days against
    automatic shortening;
  - "you can be quicker with me": one level shorter, never below the floor; a cut within the next 20 turns undoes it
    silently;
  - "go back to normal listening": the default level;
  - "give me a second to think": two levels longer for that one turn, never stored (guests get only this).
- **Silence.** She never narrates a tuning. She confirms only an explicit command, and always answers when asked.

## The numbers

| | value | source |
|---|---|---|
| speech end to close today | 720-880 ms | voice-pipeline.md, panel per-turn dead time [measured] |
| clean 560 on the 246-turn corpus | 124 turns closed a median 160 ms earlier, 5 cuts (2.0 %) | voice-pipeline.md [measured] |
| live cut-offs per 100 turns at L0 | **unmeasured** until the `FIRST_SOUND` instrument (PR 2495) runs on the panel | - |
| target | <= 3 cut events per 100 eligible turns per person | the reflex ship bar |
| turns to reach L-3 at a 1.5-3 % cut rate | about 55-80 eligible turns per step, about 200 in all | design note arithmetic [estimate] |

## The gate

1. **A week of instrument data first.** The `FIRST_SOUND` line (PR 2495) must run on the panel for a week before any level is
   applied, so the L0 cut rate is a measured number, not a guess.
2. Shadow: the server computes and logs the level it WOULD use beside the one used (`ZOE_LISTEN_PACE=shadow`), n >= 100
   eligible turns per subject before acting.
3. The owner's ear, then `on`. Rollback: the flag off.

## Acceptance, in the owner's words

True when done:

- "She stops cutting me off." Cut events per 100 eligible turns stay at or under 3 per person, measured, before and after.
- "She doesn't keep me waiting." The mean wait after speech end is lower than today's fixed setting for people who rarely pause.
- "She never gets faster on a guess." A broken, missing or odd measurement never shortens the wait.
- "If she cuts me off, she fixes it fast." Two cut-offs in twenty turns, or one right after her "mm", and she waits longer at once.
- "She doesn't flip-flop." At most one shortening a day; 40 clean turns before any shortening.
- "I can just tell her." "Give me more time", "you can be quicker", "go back to normal", by voice, no keyboard.
- "Kids and guests are safe." A child never goes below the household default; a guest only ever gets the household setting.
- "She doesn't talk about it." No unprompted remarks about tuning; a plain answer when asked.
- "Forget me means forget this too." The member's row goes with the member forget.

## Not built yet

- The panel side: `_Endpointer` keyword arguments for the three tails, the recorders passing the profile in.
- The wire: the done-frame `listen` key from the server and the `turn_outcome` POST up.
- The spoken intents (Skybridge, lexicons).
- The settings-page view and reset.
