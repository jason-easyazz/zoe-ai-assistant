---
type: research
title: Recall gate shadow-log replay (2026-11-05) - Samantha Initiative experiment 1 (lane C E2)
date: 2026-11-05
status: experiment run; verdict DO NOT ENFORCE YET (the precision criterion could not be scored from logs, and the real sample is below the bar). Nothing flipped; ZOE_RECALL_GATE stays shadow.
description: Every RECALL_GATE shadow line in the live zoe-data logs (2026-10-10 to 2026-11-05) was collected and split into real household turns and synthetic harness turns. The would_add precision test the initiative record asks for cannot be run from the logs (by design they carry 8-character row ids and counts, never text), and the real would_add sample is 28 decisions over 7 distinct rows. What the logs do show - coverage, packet shrink, empty packets, budget use, latency - is reported here, with the fix that unblocks the precision test.
evidence: ~/.zoe-logs/zoe-data.app.log{,.1,.2,.4} RECALL_GATE / RECALL_EVIDENCE / RESTRAINT lines (counts and ids only; raw lines kept in a private chmod-700 scratch dir, not committed). A hermetic CPU benchmark of recall_gate.evaluate_packet on synthetic rows (no live store). No database was read.
builds_on: docs/research/samantha-initiative-2026-11-03.md (section 8 row 1), docs/knowledge/recall-gate.md ("What enforce needs" item 1)
---

# Recall gate shadow-log replay (2026-11-05)

Experiment 1 of the [Samantha Initiative](samantha-initiative-2026-11-03.md) (section 8, lane C E2): would `ZOE_RECALL_GATE=enforce`
have added relevant rows on real turns without crowding the budget? Success was would_add precision >= 0.8 on a hand-scored sample
(>= 50 adds, >= 30 drops); failure < 0.6. Gate design: [recall-gate](../knowledge/recall-gate.md).

## Answer first

1. **Precision could not be scored. n scored = 0.** The `RECALL_GATE` line carries 8-character row ids and counts, never text
   (deliberately), and no other zoe-data log line names those ids. Judging "would this row have helped this reply" needs the row's
   text and the turn, which live only in the store; this run was logs-only by instruction. The precision criterion is **unmet, not failed**.
2. **The real sample is too small anyway.** 28 would_add decisions on real household turns, over **7 distinct rows** (two rows make 17
   of the 28). The bar asked for >= 50 adds. Adding the synthetic harness turns gives 45 adds over 16 distinct rows - still short, and
   harness rows are fixtures, not household evidence.
3. **The gate barely sees real turns.** 54 real packet decisions in 26 days; 41 of them on two days (2026-10-10 and 2026-10-12), then
   13 over the following 24 days (about one every two days). This is the open circuit the initiative record names: the brain rarely
   reaches `memory_for_prompt`, so enforce would change very few household replies until that is fixed.
4. **Enforce is a large cut, not an addition.** On real turns the floor presents a median of 20.5 rows (packet ~2,200 characters);
   the gate would keep a median of 2. Every one of the 54 real decisions drops rows (902 drops in all), and **11 of 54 (20%) become an
   empty packet** (abstain) where the floor had rows. The risk to measure is on the drop side, not crowding.
5. **No budget crowding.** The gate's selection never came near its cap: real turns median 24 tokens, max 155 of 400; harness max 83.
   Enforce cannot push the packet past its budget or evict a higher-value row on budget grounds; what it evicts, it evicts by the
   `delay` / `cooldown` / threshold rules (real turns: 574 rows held back by `delay`, 58 by `cooldown`, 9 held in by `sticky`).
6. **Latency: likely a net saving.** The decision costs ~5-21 ms of CPU for a ~150-row pool (19-70 ms at 500 rows; lower bound with
   stamped triggers, upper with re-derivation) plus one durable-pool read (capped at 0.8 s) and one restraint mutes read, now awaited
   inline instead of in the background. Against that, the packet shrinks from ~550 to ~25-50 tokens on the turns it touches; at the
   brain's measured ~650 tok/s prefill that is roughly 0.7 s less prefill. The pool and mutes reads were not timed live (no store access).

**Verdict: do not enforce yet.** Not because the gate was shown wrong, but because the deciding measurement (precision of adds, and
whether drops removed a needed row) has not been taken, and the evidence that exists says enforce is mostly a 90% packet cut plus
a 20% empty-packet rate on a path that real turns rarely reach.

## Data

All `RECALL_GATE` lines in the retained logs: **439**, 2026-10-10 07:09 to 2026-11-05 01:14, all `mode=shadow`. The rotated app
logs and the stderr stream carry the same lines (counted once).

| slice | lines | packet decided | hop decided | skipped | would_add (turns) | distinct added rows | would_drop (turns) |
|---|---|---|---|---|---|---|---|
| real household (1 account) | 56 | 54 | 2 | 0 | 28 (25) | 7 | 902 (54) |
| synthetic `demo_bar_*` harness (15 users) | 383 | 296 | 31 | 56 (`ambiguous_session`) | 17 (11) | 9 | 1,813 (262) |

Per real packet decision: floor rows median 20.5 (max 24) -> gate rows median 2 (mean 1.9); turn index within the session mostly 1
(23 of 54), so `delay` (filler not eligible in the first two turns) drives most drops. Hop surface: 2 real decisions, nothing added
or dropped. Every `ambiguous_session` skip is a harness turn (several concurrent sessions of one synthetic user).

The second-resolution timestamps put the background decision within 1 s of the floor's packet on all 45 real turns that have both lines.

## What unblocks the precision test (the fix)

1. **A private scoring pass with text.** Either a one-off owner-approved read of the added and dropped rows' text into a
   chmod-700 scratch dir (never committed), or a code change: an opt-in shadow trace that writes `(turn, row id, row text, owner words)` to
   a 0600 file under `~/.zoe-logs/` beside the existing line. Either lets the hand-score run as specified.
2. **More real decisions.** At the current rate (~0.5 real decisions a day) 50 real adds is months away. The coverage fix the initiative
   record already ranks first (the now card / continuity injection that puts memory into every household turn) is what produces the
   sample; or score on the day-sim and bar worlds, which are synthetic but exercise the same selection.
3. **Score the drops first.** With a 90% cut and 20% empty packets, a `would_drop` that held the row a good reply used is the likelier
   regression. The policy knobs already exist: `abstain=False` keeps the floor's packet when nothing triggers, and `filler_max` caps filler.
   A softer first enforce (abstain off) removes the empty-packet risk while keeping the additions.

Re-decide when items 1 and 2 give >= 50 scored adds and >= 30 scored drops: precision >= 0.8 -> enforce; < 0.6 -> do not; between ->
tune `SIM_NEAR` / threshold on the misses.

## Method and privacy

Collected with `grep RECALL_GATE` over the retained `~/.zoe-logs/zoe-data.*` files; split by the synthetic `demo_bar_` user prefix.
Raw lines and any per-row notes stayed in a private chmod-700 scratch dir outside the repo. This record carries aggregate counts only:
no user ids, no row ids, no utterances, no memory text. The CPU benchmark ran `recall_gate.evaluate_packet` (code identical to `main`
at 2cd750bb3) over synthetic rows with the pool and restraint stubbed, 60 turns per configuration, under the test-lane lock.
