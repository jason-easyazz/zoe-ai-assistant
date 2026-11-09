---
type: Runbook
title: Outcome edges - test plan and switch-on order (QA round 6a)
description: What ZOE_OUTCOME_EDGES actually does (it re-ranks proactive items by what the person did with them; it does not create the follow-up), which flags must be on for a typed-chat round to see it, the exact values in order, and a synthetic-user QA script with pass/fail checks.
tags: [proactive, outcome-edges, qa, flags, samantha]
timestamp: 2026-11-08T00:00:00Z
---

# Outcome edges: test plan and switch-on order

## What it is (and is not)

`proactive/outcome_edges.py` (`ZOE_OUTCOME_EDGES` = `off` | `on`, default `off`; there is NO shadow mode) writes one row per item Zoe
raises or the member pulls (`proactive_outcome_edges`, migration 0069) and settles it `acted` / `dismissed` / `ignored` / `unknown`. At
a member's first turn of the local day it re-scores today's due items (dismissed x0.5, ignored x0.85 each up to 3, due boost +0.2) so the
raise pick and a pull rank by it. It does NOT create "ring X tomorrow" follow-ups. That chain is:

1. the nightly dreaming cycle extracts open loops (`memory_digest._extract_open_loops`) - a typed or spoken turn leaves nothing to
   raise until that pass has run;
2. `proactive.selector.select_for_user` ranks them into `proactive_candidates` (needs `ZOE_PROACTIVE_SELECTOR`);
3. on the member's next brain turn `selector.prepare` hands the brain one `[RAISE ...]` block. Both brain lanes call it
   (`zoe_flue_client` and `zoe_core_client`), so TYPED chat is covered; a turn a fast tier answers (a command) never raises, by design;
4. `selector._settle` opens the edge; the outcome arrives from a tap, the loop being resolved, the delivery ledger's judgement of the
   next turn, or a 24 h expiry.

Gaps found: there is no tap in the typed chat UI (nothing in `services/zoe-ui` calls `/api/proactive/welcome`), so a typed-chat edge is
settled by `loop_resolved`, `ledger_sweep` (needs `ZOE_PROACTIVE_LEDGER`) or `expiry` only; without the ledger a typed round ends
`unknown`. No code change is needed to start measuring; a chat tap affordance is a UI follow-up.

## Flags, in order (set in the service environment, then restart; never edit a live .env from an agent)

| step | flag | value | why |
|---|---|---|---|
| 0 | `ZOE_PROACTIVE_SELECTOR` | `1` | prerequisite: nothing is raised, so no edge opens, without it |
| 0 | `ZOE_LOOP_LIFECYCLE` | `1` | keeps loops dated 3-14 days out as candidates (nights 1-2 kept nothing) |
| 1 | `ZOE_PROACTIVE_LEDGER` | `1` (only truthy values count, `proactive/ledger.py`) | lets a typed round settle `acted` / `ignored` |
| 2 | `ZOE_OUTCOME_EDGES` | `on` | there is no shadow: `on` only writes its own table and reorders the day's due items |

Check the box's live values first with `scripts/maintenance/zoe_ground_truth.sh`; a flag read from its default is not the live value.

## QA script (synthetic demo user only)

1. Turn 1 (typed): "tomorrow I'm going to ring Dana about the quote". Expect an ordinary reply, no raise.
2. Run the selector now for the demo id (internal token): `POST /api/proactive/selector/run-synthetic/<demo id>`. Expect
   `open_loops` ok and a selected count of at least 1.
3. New session, first turn: "morning". Expect ONE natural follow-up about ringing Dana, once. Edge: one `raise` row with outcome null.
4. Reply "yes I rang her" (resolves the loop) or say nothing. Run the engine slow loop (or wait): the row settles `acted`
   (`loop_resolved`) or, after 24 h, `unknown` / `ignored`.
5. Next local day, first turn: expect a `presence` edge row per due item and the order logged; a dismissed item ranks lower.
6. Negatives: a guest id writes no edge; "forget Dana" leaves no name in the table (rows hold opaque ids only); `off` writes nothing.

Pass: steps 1-3 show exactly one follow-up and one edge; step 4 settles by the strongest source; step 6 holds. Fail on any second
raise in a session, any edge text containing a name, or any write while `off`.
