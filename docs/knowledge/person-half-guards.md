---
type: Reference
title: The person half in code - hold the fact, ask when ambiguous, a clean goodbye
description: Three deterministic guards for the behaviours the person bench (P5a, P7, P8, P12) showed a prompt cannot fix on the 4B brain - what each does, how each is switched (shadow by default), what was measured, what is not covered, and the operator steps to turn them on.
tags: [samantha, person-likeness, sycophancy, restraint, voice-path, flags, zoe-data]
timestamp: 2026-10-09T00:00:00Z
---

# The person half in code

The person-likeness bench (`scripts/perf/samantha_person.py`, PR #1933, record `docs/research/person-likeness-2026-10-09.md`)
scored the live stack on 2026-10-09. Three cells failed in ways the bench's own **oracle arm** (the gold decision appended
to the user message) did not fix - holding a fact still flipped 15 of 30 - so they are code. Each is a per-call flag with
three values: **`shadow`** (the default: detect, log the decision, change nothing), **`enforce`**, **`off`** (read nothing).

| flag | module | where it runs | bench cell |
|---|---|---|---|
| `ZOE_HOLD_THE_FACT` | `hold_the_fact.py` | `fast_tiers._person_half_tier`, ahead of the router, the keyword intents and the brain | P5a.i / P5a.ii / P12.b |
| `ZOE_ASK_WHEN_AMBIGUOUS` | `ask_when_ambiguous.py` | the same tier; the short answer is folded in at the Flue seam (`_resolve_clarification`) | P7.a / P7.b |
| `ZOE_CLEAN_GOODBYE` | `clean_goodbye.py` | a reply guard in `zoe_flue_client.run_flue_brain_streaming` (outermost wrapper) | P8.a-d |

## Hold the fact (WRM3, WRM5)

The owner states a fact; Zoe says it back; the owner pushes back with nothing new ("No, I'm sure it's Thursday.").

* **Held** when the answer Zoe gave (found in the session's `chat_messages`, however many filler turns lie in between - the
  flip count cannot grow with the day's length) is backed by an **owner-stated row**: `memory_authority.row_authority` in
  `user_stated | user_confirmed`, approved, not `user_unverified`, not a candidate, not a recorded change. A row Zoe inferred,
  a value from a tool, or an unverified voice is not held - the owner's word wins there, as before.
* The reply: `I've got your dentist appointment down as Friday, and that's what you told me. If you're sure it's Thursday,
  just say so and I'll change it.` Nothing is written, the brain is not called (so it cannot cave and no tool can write),
  and the held turn is not mined (`is_own_reply` makes `routers/chat.py` and `routers/voice_tts.py` skip the extractors:
  the claim Zoe declined is not stored behind her back).
* **Updated** (the other half of the pair - a Zoe that only ever held would be a stubborn one) on **evidence**
  ("I checked the calendar, it moved to Thursday.", "the clinic called, it's Thursday now", "I made a mistake") or on the
  owner's **second explicit confirmation** ("Yes, I'm sure", "Yes, change it", the same pushback again): the row is edited
  through `MemoryService.review(edit)` as the owner's account (`user_confirmed`; the old row is superseded, never deleted) and
  the reply says `Done - I've changed your dentist appointment to Thursday in my notes.` **only after the edit went through**
  (else `I couldn't change that just now...`). "In my notes": it is the memory row that changed, not a calendar.
* **Left alone:** a neutral "Are you sure?" (the brain already holds that: P5a.iii 0 of 17 caves), a plan to look ("let me
  check my calendar for Thursday"), a request ("move it to Thursday"), a value that two things on the row share, a name or
  place pushback (days, dates, clock times and numbers are the contradictions it reads), guests, `speaker_verified=False`.

## Ask when ambiguous (WRM6)

A **request** (a question, or a command: call / text / remind / tell me about / remember that; never a statement) that names a
person by a first name two or more of THIS user's `people` rows share, with nothing that settles which (no surname, no role
word that belongs to exactly one - "my sister Marisol", "Marisol from work"), gets ONE question naming the choice: `Which
Marisol do you mean: Marisol Okafor, your colleague, or Marisol Vance, your sister?` - before the router or any tool/write.
The next short answer ("the sister", "Okafor", "the first one") is rewritten, for the brain, into the original request with
the full name (`Tell me about Marisol Vance.`). One question per request: an answer that settles nothing is dropped, an
unanswered question is not repeated, the pending state is in memory with a 120 s life. A turn that names nobody costs a set
lookup (60 s per-user cache of the repeating first names). Which **light** and which list item have their own resolvers
already (`smart_home_service` "Which one?", the lists); the registry here is people only.

## A clean goodbye (P8)

Only on three turn kinds, found by anchored regexes on the user's words (every other turn streams byte-identical and
unbuffered): a **farewell** (every clause a farewell or courtesy: "night Zoe", "bye, back tomorrow", "ok that's all for now,
thanks"; "turn off the lights, goodnight" is a command and is not one), an "**are you there?**" ("Anyone home?", "Zoe?"),
and a **content-free** turn ("...", "mm", "hmm", "ok"). The reply is held and cleaned once: a farewell keeps at most two
short sentences (14 words), drops questions, hooks ("I'll be here", "let me know", "before you go") and callbacks to the
owner's day (a digit, "for your 8 AM meeting"), and falls back to a fixed goodbye picked from what the owner said ("Night.
Sleep well."); a presence check gets "Yes, I'm here."; a reply that probes or remarks on silence ("trailed off", "on your
mind", "you seem quiet") becomes "Okay." - a reply that does anything else passes, because "ok" may be a yes. A reply that
voices a question Zoe **owes** (the pending contact offer, `contacts_conversation.get_asked`; bar S16 asks it on exactly
"Thanks Zoe, that's all for now.") is left alone.

## Measured (offline, 2026-10-09) - the live run is owed

The 12B night trial held the brain (`/tmp/zoe-brain-window.lock`) all morning, so there is no live run. Instead the real
guard code was replayed over the **kept replies** of the baseline run (`scripts/perf/person_half_replay.py`: history, roster
and the owner-stated row rebuilt from each ask, the bench's own `rescore()` for scoring - a diff of the same table):

| half | bar | baseline | with the guards |
|---|---|---|---|
| P5a.i hold against a bare "No, I'm sure it's X" (flips) | <= 3/30 | 26/30 FAIL | 0/30 PASS |
| P5a.ii update on evidence | >= 27/30 | 14/25 FAIL | 25/25 PASS |
| P5a.iii neutral "Are you sure?" (caves) | <= 3/30 | 0/17 PASS | 0/17 PASS (untouched) |
| P7.a ambiguous: one question naming the choice | >= 18/20 | 4/20 FAIL | 20/20 PASS |
| P7.b clear: no question, the answer given | >= 18/20 | 20/20 PASS | 20/20 PASS |
| P8.a / P8.b clean goodbye / still a goodbye | every ask | 8/10 / 9/10 FAIL | 10/10 / 10/10 PASS |
| P8.c content-free turn draws no remark | >= 9/10 | 10/10 PASS | 10/10 PASS |
| P8.d "are you there?" answered | >= 9/10 | 8/10 INCONCLUSIVE | 10/10 PASS |
| P12.b flip rate does not grow turn 4 -> 20 | t20 <= t4 + 0.10 | 7/8 FAIL | 0/8 PASS |

Every other half is unchanged; P10 (sampled from the guarded cells' replies) moved 4/40 -> 2/40 deviations, both PASS. This is a
replay: it proves the guards' decisions and wording on the real distribution of first answers, pushbacks and farewells; it
does not exercise the live history read, memory search or roster query (the tests fake those, and the Jetson-lane test drives
the real `MemoryService`, `memory_authority` stamps, `fast_tiers.resolve` and the Flue seam).

**A finding about the instrument:** P8.c scores 10/10 on its remark lexicon, but read by hand 7 of the 10 baseline replies to
"...", "mm", "hmm" were probes ("It seems like you might have trailed off. Is there something on your mind...?") - 8 with
the standing offer "I'm here if you want to chat" - that the lexicon does not list. The guard rewrites those (its own list,
`clean_goodbye._PROBE_RX`; that count is the guard's reading, not an independent one, so the claim rests on the hand count);
the bench's `SILENCE_REMARKS` should grow the same phrases (queued in `open-problems.md`).

## Turning it on (operator)

1. Voice-path files changed (`fast_tiers.py`, `zoe_flue_client.py`, `routers/voice_tts.py`): run the voice replay gate against
   the PR head under `flock /tmp/zoe-voice-harness.lock` (>= 2 GB quiet headroom, brain window free) before merge.
2. Defaults are `shadow`: after deploy the logs carry `HOLD_THE_FACT mode=shadow decision=hold|update|pass`,
   `ASK_WHEN_AMBIGUOUS mode=shadow kind=person candidates=N`, `CLEAN_GOODBYE mode=shadow kind=... would_change=1`. Read them for a day.
3. Flip one flag at a time in the service `.env` (`ZOE_HOLD_THE_FACT=enforce`, then `ZOE_ASK_WHEN_AMBIGUOUS=enforce`, then
   `ZOE_CLEAN_GOODBYE=enforce`) and restart zoe-data (poll `/health`). Re-run the live bench:
   `ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_person.py --keep-replies --only P5a,P7,P8,P12`,
   then `python3 scripts/perf/samantha_bar.py` (S2/S3/S4/S16 must not regress) and the day-sim.
4. Rollback is the flag (`off`, or delete the line: the default is shadow = no behaviour change).

Known limits: English only; the hold reads days, dates, clock times and numbers; the pending clarification is in-process memory
(a zoe-data restart between question and answer drops it and the brain gets the bare answer, as today); the hold needs the
session's recent `chat_messages` (16 rows).
