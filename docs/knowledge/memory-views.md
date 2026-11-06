---
type: Reference
title: Memory views over pointers - episodes, slot contradictions + ageing, speakable provenance, dormant threads (flag-dark)
description: Samantha initiative item 15 (lane C E4, E7, E8 and the episode layer), first slice - four derived memory layers that store nothing and copy no text, each flag-dark with a shadow instrument, the authority rule each one follows, why forget stays complete, the owner's shadow report, and what is deliberately deferred.
tags: [memory, samantha, views, provenance, contradiction, ageing, episodes, dormant-threads, flags]
timestamp: 2026-11-05T00:00:00Z
---

# Memory views over pointers

Record: [lane C](../research/samantha-initiative-2026-11-03/lane-C-memory.md) sections 2, 3.3-3.5, 3.8 and experiments E4, E7,
E8; roadmap item 15 of [the initiative](../research/samantha-initiative-2026-11-03.md). Design rules it builds on:
[memory authority](memory-authority.md) (who said it decides who may change it) and
[person-centred memory](../architecture/person-centred-memory.md) section 3.3 (accepted D1-D12, 2026-11-03).

## The one rule every view follows

A view is a **function of rows that already exist**. It returns row ids, turn ids, thread ids, slot names and counts; it never stores
a derived row, a summary, a key list or a value. So:

- **Forget is complete by construction.** A forgotten row / turn / thread observation is gone from its own store (the existing
  cascade); the next read has nothing to point at. There is no derived store to erase and no erase hook to forget to register.
  Each view's tests plant, forget and re-read to prove it.
- **The words are read inside one call and dropped.** Logs and the report carry ids and counts only (pinned by a leak test that
  plants distinctive words in every input).

## The four views

| View | Module | Flag (default) | What it gives |
|---|---|---|---|
| Episodes | `memory_episodes.py` | `ZOE_MEMORY_EPISODES` (off), `ZOE_MEMORY_EPISODE_GAP_S` (2700) | One member's consecutive own turns with no gap over 45 min = one episode `{id, user_id, turn_ids, session_ids, started_at, ended_at}`. Read selects `id, session_id, created_at` from `chat_messages` (never `content`), owner by `message_owner_expr`, off-the-record excluded. `resolve()` drops pointers whose turn is gone. Flag gates the DB read; nothing serves episodes yet (chapters, E9, are the consumer). |
| Slot contradictions + ageing | `memory_slot_check.py` | `ZOE_MEMORY_SLOT_CHECK` off / shadow / on (off) | Four single-value slots per person (employer, home city, partner, diet), keyed to the owner ("User ...", "My ...") or the row's resolved `entity_id`; no fuzzy subject, no reported claim ("User said Ana works at ..."), no non-values ("works at home", "lives in fear"). Pairs of standing rows that disagree are decided by `memory_authority`: **hold_newer** (the newer row may not override the older - an inference against the person's word: not served, one question), **hide_older** (the person's word over an older inference: the inference is not served), **ask** (both the person's word: nothing hidden (the brain reads the dates and its newest-wins rule); one question retires the older only on their answer). Ageing: life-now slots and `facet=life_now` are due a "still true?" check 90 days after the person last stated or confirmed them, mood rows after 14, an explicit `review_after` wins, everything else never ages; due is a question, never a drop. |
| Speakable provenance | `provenance_speech.py` | `ZOE_SPEAKABLE_PROVENANCE` (off) | The recall packet's suffix becomes who-and-when: "(you told me on Tuesday)", "(you confirmed it on 3 October)", "(you mentioned yesterday)", "(I picked up yesterday - not confirmed)". Fails closed: an unstamped legacy row, an unverified voice, an operator row says nothing (the plain date, if any, stays). Night-batch writers get no day. Also phrases the slot-check question ("On 20 September you told me that you work at X, but I picked up that you work at Y - is that right now?"); a side with no speakable provenance gets no phrased question (the conflict stays in the report as ids). |
| Dormant-thread matcher | `dormant_threads.py` | `ZOE_DORMANT_THREADS` off / shadow (off) | A night-mind thread that is `quiet` (or open with no mention for `QUIET_AFTER_DAYS`) and a NEWER signal sharing its anchors (2.5 per shared name, 1.5 per shared distinctive word, threshold 3.0; somebody else's name scores 0) = one candidate `{thread_id, signal_ref, score, counts}`, at most one per scan. Never a `leave` thread (deny-list topic, `raise_policy=leave`, two ignored raises) and never from a health / grief / money / conflict signal. |

Where they are wired: `routers/memories.memory_for_prompt` calls `memory_slot_check.apply_to_packet` (after restraint, before the
packet is built, relevance and continuity both) and passes `speak=` to `_build_memory_prompt_packet` (relevance mode only). Both are
no-ops while their flag is off, so the packet is byte-identical (pinned).

## The shadow report (read this before switching anything on)

```
python3 scripts/maintenance/memory_views_report.py --user <member-id> [--user ...] [--days 7] [--pretty]
```

One JSON object per member: `slots` (rows, keyed rows, every conflict with its action, the held / hidden ids, the still-true-due ids,
at most one question), `episodes` (count, turns, ids over the window), `dormant` (threads, dormant threads, today's signals, the
candidate). Ids and counts only; writes nothing. Memory rows come from the palace sqlite opened read-only (safe while zoe-data runs),
turns and threads from plain Postgres SELECTs. Exit 2 = refused (no `--user`, palace unreadable, no DB pool).

Live per-turn shadow: `ZOE_MEMORY_SLOT_CHECK=shadow` logs one `SLOT_CHECK user=... conflicts=N held=N hidden=<short ids>` line
whenever a packet would have carried a contradiction.

## Deliberately NOT in this slice (next PRs)

- **Asking the question.** The report names the one question; queueing it (a `pending_suggestions` action whose "yes" retires the
  older row through `MemoryService.review` by the person, capped at one per member per week) is the next slot-check PR.
- **Delivering a dormant candidate** into the proactive pull queue (at most one a day, the raise back-off, accepted / ignored feeding
  it). Lane C E4 asks for an owner-thumbed shadow trial first; the report is that trial's instrument.
- **Other signal sources** for the matcher (shopping-list items, calendar entries, reviewed Omi items): the matcher takes any
  `Signal`; only the member's own turns are loaded today.
- **Chapters** (E9), **manner rows** (E10), and the forget-completeness probe across derived layers (E11, separate branch).

## Verification

`services/zoe-data/tests/test_memory_episodes.py`, `test_memory_slot_check.py`, `test_provenance_speech.py`,
`test_dormant_threads.py`, `test_memory_views.py` (all `ci_safe`). Each rule has a negative control, and the controls were
mutation-checked: breaking the hold rule, the `on` filter, the fail-closed provenance, the no-`content` read, the leave exclusion,
the other-name veto, either packet wiring, the not-current guard or the confirmation rule turns the suite red.
