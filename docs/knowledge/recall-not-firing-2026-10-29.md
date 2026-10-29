---
type: record
title: Why recall is not reaching the brain on real panel turns (2026-10-29)
description: Read-only investigation of the "83% of replies used zero memory sources, continuity empty on 97% of turns" finding in the 2026-10-29 synthesis. Counts for the last 7 days of real panel turns, every gate on the Flue-lane turn path (flag, code default, live state), and the ranked causes. Identity is NOT the cause; the injectors are trigger-gated by design and real traffic rarely has the trigger.
tags: [recall, memory, continuity, flue, voice-path, measurement, samantha]
timestamp: 2026-10-29T00:00:00Z
---

# Why recall is not reaching the brain on real panel turns

Scope: read-only. No code, flag or live state was changed. Counts and code references only; no turn text and no memory content appear here.
Window: 2026-10-22 to 2026-10-29 (the current `zoe-data.app.log`, which starts 2026-10-18); live zoe-data process started 2026-10-29 20:39 local.

## Verdict

The 83% / 97% figures are directionally real, and **recall is not broken**. Four things were ruled out; one thing explains almost all of it.

* **Not identity.** 63 of 64 panel voice commands in the window carried a verified member identity (`has_scope=True`); 1 was a guest.
* **Not a flag.** The recall floor flag is ON live and continuity is ON by code default. Nothing is dark that should be on.
* **Not a drop.** Every recall packet that was fetched was non-empty and injected (4 of 4, 12 bullets each); no packet-fetch failure or timeout was logged.
* **Not the lane.** The live lane is Flue (`ZOE_BRAIN_BACKEND=flue`, `ZOE_FLUE_WIRE=2`) and both blocks are assembled in `zoe_flue_client.py`, so the live lane does call them.
* **The cause: both injectors are trigger-gated, and real household traffic rarely matches the triggers.** There is no ambient every-turn injection of retrieved facts. The only always-on memory is the user-model card in the system prompt; the only other path is the brain choosing to call its own `recall_memory` tool, which nothing measures today.

## The numbers (7 days, panel voice, real household traffic)

| measure | count |
|---|---|
| panel voice commands logged | 64 |
| identified member / guest | 63 / 1 |
| turns that reached the brain (`FLUE_CONTEXT_BUDGET`) | 35 |
| turns answered by fast tiers before the brain | 29 |
| user messages stored (voice-panel sessions) | 63 |
| messages the recall floor claims by shape | 4 (6%): 2 `personal`, 2 `own_fact` |
| messages with no recall shape | 59 (94%) |
| of those, with a first-person pronoun | 9 (6 of them questions) |
| of those, command-shaped (lists, home, timers, weather, time) | at least 14 |
| brain turns by router domain | chat 12, tasks 11, unrouted 11, memory 1 |
| median words per message | 4 |
| `SEAM_RECALL` logged on brain turns | 2 of 35 (all non-empty) |
| `SEAM_RECALL` in the whole current log | 4, all `own_fact`, all non-empty |
| `SEAM_CONTINUITY` evaluated / matched, brain turns | 32 / 0 |
| `SEAM_CONTINUITY` evaluated / matched, whole current log | 49 / 0 |
| messages matching the continuity regex | 0 of 63 |
| traffic class of stored panel replies | household 63 of 63 |

On brain turns the floor fired on about 6% and continuity on 0%: the same shape as the synthesis's 83% (zero sources) and 97% (continuity empty). The exact lane that produced those two percentages was not found in the repo, so this record reproduces the direction on first-hand data rather than the exact figure. The older log (10-10 to 10-18) is dominated by the Samantha-bar harness (86 `event_time`, 24 `evidence`, 19 `named_person` recalls) and must not be read as household behaviour.

## Gate table for the Flue-lane turn path

| gate | flag | code default | live state | on the Flue lane |
|---|---|---|---|---|
| Recall floor | `ZOE_SEAM_RECALL_INJECT` | OFF | set, true | yes, `_recall_context_block` |
| Present-state / event-time shapes | `ZOE_RECALL_PRESENT_STATE_SHAPES` | OFF | set, 1 (differs from default) | yes, extends `_recall_question_shape` |
| Own-fact shapes | `ZOE_OWN_FACT_PRECEDENCE` | OFF | set, 1 (differs from default) | yes |
| Named-person floor | `ZOE_PERSON_RECALL_FLOOR` | enforce | unset (= default) | yes, only when no shape claims the turn |
| Recall evidence (dates, quotes) | `ZOE_RECALL_EVIDENCE` | OFF | set, 1 | yes |
| Continuity injection | `ZOE_SEAM_CONTINUITY_INJECT` | ON | unset (= default) | yes, `_continuity_context_block` |
| Relevance gate | `ZOE_RECALL_GATE` | shadow | unset (= default) | logs only, never changes the packet |
| Personalisation hop | `ZOE_PERSONALISATION_HOP` | ON | unset (= default) | yes, generic-advice requests only |
| Pending-offer block | `ZOE_SEAM_OFFER_INJECT` | see code | set, 1 | yes |
| User-model card (system suffix) | `ZOE_USER_MODEL_BLOCK` | off | set, 1 | yes, every member turn; not counted as a recall "source" |
| Manner block | `ZOE_MANNER_BLOCK` | off | set, on | yes, 34 of 35 brain turns |
| Brain `recall_memory` tool | doctrine in `labs/flue-zoe-brain-2x/src/agents/zoe.ts` | n/a | n/a | the brain decides; no live telemetry |
| Per-turn outcome table | `turn_outcomes` | dark | table empty | cannot answer "which tools ran" |

## What gates the two injectors (the code)

* **Recall floor** (`_recall_context_block`, `services/zoe-data/zoe_flue_client.py`): returns "" unless `_recall_floor_shape(message)` is non-empty (`challenge`, `personal`, `present`, `event_time`, `event`, `evidence`, `own_fact`) or `_named_person_floor` finds a named, known person. `_PERSONAL_QUESTION_RE` is deliberately conservative. A greeting, a list command or a world question is not a recall question, so no packet is fetched at all.
* **Continuity** (`is_continuity_turn`, `_CONTINUITY_RE`): fires only on a first-person emotional or state statement. It exists for the S4 check-in behaviour, not for general conversational context. 0 of 63 real messages matched, so "97% empty" is the expected steady state, not a malfunction.
* **Conversation history** is separate. `FLUE_CONTEXT_BUDGET` shows `history=0` on 9 of 35 brain turns and `tail>0` on all 35 (the sidecar's own session tail). "Continuity" in the synthesis means the cross-session memory block, which is the trigger-gated one above.

## Ranked causes

1. **Trigger-gated injection meets short, task-shaped traffic (about 94% of turns).** 59 of 63 messages have no recall shape and none matches the continuity regex; median 4 words, mostly commands, chit-chat and world questions where memory is correctly not needed. This is working as written (the shape gate protects latency and the 4B from irrelevant facts). Whether it is the right design is the product question: "she knows me" implies some ambient retrieval, which this path does not do.
   * Smallest fix, if more recall is wanted: widen shapes, not inject every turn. The 9 unshaped first-person messages (6 questions) are the audit set: check by hand whether recall would have helped. Code change in `zoe_flue_client.py` / `memory_gate.py`; **voice path, replay-gated** against `~/.zoe-voice-samples`, speed must not regress.
   * Alternative: move `ZOE_RECALL_GATE` shadow to enforce, only after its offline replay (see [recall-gate.md](recall-gate.md)). That changes which rows enter a packet, not when a packet is fetched. **Needs an owner decision.**
2. **The brain's own `recall_memory` tool is the only other path and it is invisible.** zoe-data logs no tool calls, the sidecar journal emits only `FLUE_EARLY_TEXT`, and `turn_outcomes` is empty (flag-dark). So "zero memory sources" cannot tell "no recall needed" from "the 4B should have called the tool and did not". The doctrine's 97% firing was measured on its own probe, not on live traffic.
   * Smallest fix: instrumentation only. One log line per turn from the Flue terminal event with the tool names called and whether `recall_memory` returned rows. Touches `zoe_flue_client.py` (voice-path file, replay-gate it; no behaviour change) or the sidecar. No owner decision.
3. **Small sample and an under-specified metric.** 35 brain turns in 7 days (about 5 a day), so any percentage moves several points per turn. The ambient sources that do ride every member turn (user-model card, manner block) are not counted as "memory sources", which inflates the zero. Fix: define the metric as three counts (floor packet, continuity packet, tool recall) plus an "ambient card present" flag, per traffic class.

Identity and enrolment are not on this list: 98% of panel commands were identified. `GUEST_MEMORY_DISCARD` (46 in the current log) is a write-side guard and unrelated to this read gap.

## Voice-path and decision notes

* Cause 1 (any shape widening) and cause 2 (logging in `zoe_flue_client.py`) touch the voice path: replay-gate under `flock /tmp/zoe-voice-harness.lock`; the harness stops before TTS, so its numbers are relative.
* Cause 1's direction (ambient retrieval versus reactive floors) needs an owner decision; no flag flip changes it, because the floor flag is already on.

## Method and limits

Sources: `zoe-data.app.log` (turn lines from `voice/command` through `FLUE_CONTEXT_BUDGET`), flag state of the live zoe-data process (names; values only for plain boolean or mode words), `chat_messages` (read-only; the real predicate `_recall_floor_shape` was run over the stored voice-panel user messages locally, counts only), the sidecar journal. Not found: the lane that produced the original 83% / 97%; no `memory_sources` column or counter exists in the repo or DB. The log window is the 11 days since the last rotation; "7 days" is a filter on it.
