---
type: Reference
title: The single evolution proposal queue (self-evolution Phase 0)
description: evolution_proposals as THE queue every proposal writer feeds - change class, risk tier, component, the seven statuses and their legal moves (enforced in evolution_queue.py, the only writer, with a history row per move), the decision column, JSON gate evidence, lesson id; which writers were folded in (the struggle detector, evolution_notice, the MCP tool) and which do not exist; the one read API for the Zoe score page; migration 0062 and its remap.
tags: [self-evolution, proposals, queue, struggle, migration, score]
timestamp: 2026-10-19T20:00:00+08:00
---

# The single evolution proposal queue

Design: `docs/architecture/self-evolution-nightly-loop.md` section 2 ("one proposal queue") and section 0.7 ("this replaces, it does
not add"). Code: `services/zoe-data/evolution_queue.py` (the ONLY writer), migration `0062_evolution_proposal_queue.py`, the read API in
`routers/system.py`. Tests: `services/zoe-data/tests/test_evolution_queue.py` (negative controls in its header),
`tests/test_struggle_proposals.py` (the struggle writer in the queue), `tests/test_evolution_proposal_writer_closure.py`.

## The row

| column | values | meaning |
|---|---|---|
| `change_class` | `harness` / `release` / `code` | overlay text (prompts, doctrines, manner, tool guidance, recall shape, a router example) / a program update / the maintainer PR process only |
| `risk_tier` | 0-3 | 0 harness text off the voice path, 1 harness on the voice path (replay-gated), 2 a release, 3 code (never applied by the loop) |
| `component` | short tag | `router`, `intent_router`, `brain`, `stt`, `chat`, `manner`, ... |
| `status` | `proposed` `probed` `applied` `canary` `kept` `reverted` `rejected` | the lifecycle (below) |
| `decision` | empty / `approved` | a person verdict on an OPEN row, apart from the status: approving applies nothing |
| `evidence_json` | JSON | the gate evidence: `signal` (counts), `before` / `after` (scores), `controls`, `diff`, `probes`, `gate`, `canary`, `ref` - nothing else is accepted, `diff` at most 64 KiB |
| `lesson_id` | text | the lesson this attempt produced (the lesson library is a later phase) |
| `source` | text | the writer: `struggle`, `evolution_notice:<kind>`, `mcp:create_evolution_proposal`, `legacy` (rows from before 0062) |
| `decided_by`, `decision_note`, `updated_at` | | who decided, a reason code or note, the last change |

The 0004 / 0027 columns stay (type, title, description, the legacy free-text `evidence`, target patterns, Multica link, the autonomy
contract the 0027 trigger stamps). `evolution_proposal_history` holds one row per status move or decision (`from_status`, `to_status`,
`decision`, `actor`, `note`, `at`): the audit "what changed" and the score page read.

## Legal moves (`evolution_queue.TRANSITIONS`)

```
proposed -> probed | applied | rejected        probed  -> applied | rejected
applied  -> canary | kept | reverted           canary  -> kept | reverted
kept     -> reverted  ("undo that")            reverted / rejected -> proposed  (a REOPEN: the problem recurred; the old verdict is cleared)
```

`transition()` refuses anything else (`IllegalTransition`) and is compare-and-swap on the from-status (`Conflict` when the row moved; each write and its history row are ONE transaction on asyncpg);
`require_decision=` makes a lane that needs an approval (the board runner) refuse an undecided row. `decide()` works only on an open
row, optionally guarded (the approve route guards on the autonomy contract its gate read). On PostgreSQL CHECK constraints back the
value sets; `tests/test_evolution_queue.py::test_one_writer` fails the build when any other module writes a status, a decision, an
insert, a delete or a history row.

## Writers, folded

| writer | before | now |
|---|---|---|
| struggle detector night proposals (`struggle_proposals.py`, 0057) | own table with its own status | every proposal is ALSO a queue row under the SAME id (source `struggle`): router_example / answer_quality / stt_term = harness tier 1 (`router` / `brain` / `stt`), intent_gap = code tier 3 (`intent_router`). The status and decision columns LEFT `struggle_proposals` (it keeps the struggle-domain detail); the module reads them back through a join (open = proposed, open + approved = accepted, applied / canary / kept = applied, rejected / reverted = rejected), so its API, routes, CLI and the Phase 3 seam are unchanged. Queue evidence is counts only; a withdrawn or forgotten proposal leaves both tables (and its history). |
| `evolution_notice` intent-miss clusters, agent-health spikes, frustration signals, user issue reports | raw INSERT, status `pending` | `evolution_queue.propose`, class code, tier 3 (`intent_router` / `brain` / `chat`), counts-only evidence; dedupe reads the new names; auto-approve is a decision; the 48 h measure moves `applied -> kept / reverted` with before / after evidence |
| MCP `create_evolution_proposal` | raw INSERT | `evolution_queue.propose`, class code, tier 3 |
| approve / reject / defer / deploy (`/api/agent/evolution/proposals/<id>/action`), the Multica board webhook, the background runner | raw status UPDATEs | queue moves and decisions: approve = decision (409 unless open), reject = `rejected` (409 once applied: revert instead), deploy = `applied`, defer = decision cleared + next review; board in_progress = approved, done = applied then kept, cancelled = rejected or reverted; the runner applies only an approved open row. A struggle row is refused here (decided at `/api/system/struggles/proposals`). |

Not folded because they do not exist as writers: the **night mind** writes observations and threads (memory, `night_store.py`), not
change proposals - there is no night-mind proposal writer in the repo. **Generation 0** (`~/bin/zoe-self-review.py` writing
`~/training/improvement-proposals/`) is untracked and outside the repo: deleting it is the round-1 checklist item 2 (owner OK).

## Readers

- `GET /api/system/evolution/queue?status=&class=&component=&source=&limit=` (human admin): the queue rows (queue fields only, never
  the legacy free-text evidence) plus the summary. `GET /api/system/evolution/queue/summary?days=30`: counts by class and status,
  open, kept / reverted in the window, the oldest open age. `GET /api/system/evolution/queue/<id>`: one row with its history. This is
  the read API the Zoe score page (design section 12) builds on.
- `GET /api/agent/evolution/proposals?status=pending` (legacy, used by settings.html and the review intent) takes the old names
  (`LEGACY_STATUS`: pending = open and undecided, approved = open and approved, deployed = applied, validated = kept, failed =
  reverted) or the new ones, answers the old name in `status` and the new one in `queue_status`, and leaves struggle rows out.
- The weekly digest counts "pending" = open and undecided, "validated" = kept, struggle rows excluded.

## The owner's decide page (2026-11-13)

`evolution.html` (desktop / phone, linked from Settings > Evolution Proposals; never from a touch page) over `evolution_inbox.py` +
`routers/evolution_inbox.py`, household admin persons only (a panel token and the `ZOE_UNAUTHENTICATED_ROLE` promotion are refused):

- `GET /api/system/evolution/inbox?stale_days=30`: at most 20 cards. A card groups the UNDECIDED open proposals (not approved, not
  put off past their review date) with the same component, class, kind and normalised title (or lesson id), ranked by (count + summed
  signal counts) halved every 14 days of EVIDENCE age (`proposed_at`; a decision never refreshes a card). A card carries its count and
  an opaque group `key` (a hash of those grouping fields), never its ids. A group with no new evidence for `stale_days` is stale. Struggle proposals
  (live evidence only) and the owner's own failed-turn clusters (counts only, no buttons) are cards too. Open rows with no activity for
  `stale_days` are only a count; rows put off with "later" are a count. A card carries kind, counts, dates, the proposed change and its
  risk tier, and what a yes does next; NEVER a title, description or legacy evidence (they can hold a member's words). `decided` lists
  the last approved, person-rejected (who, when) and measured rows with their measurement (better / no change / worse / not
  measurable), plus ONE summary line for every "dismiss old" rejection (count and date); a row put off with "later" is in neither list.
- `POST /api/system/evolution/decide {key | ids (max 500), action: approve | reject | later, reason: code}`: by key the server recomputes
  the groups and applies the verdict to that group's CURRENT undecided ids (any number, in chunks of 200); approve = the `decision` column, reject = the
  `rejected` status, later = the legacy defer (`next_review_at` + 7 days, decision withdrawn); a struggle card goes through
  `struggle_proposals.decide`. Idempotent; the queue history is the audit (actor, time, reason code). Approving applies nothing.
- `POST /api/system/evolution/dismiss-stale {stale_days}`: rejects the stale bucket (history note `dismissed: stale`).

## MEASURE (`evolution_notice.run_measure_phase`, the night digest)

Only an `applied` row older than 48 h is measured. Today nothing reaches `applied` (the board is paused, the legacy page has no deploy
button, the struggle retrain is off), which is why every night logged `evaluated: 0`; the result now carries `applied_due`. The
verdict (`measure_verdict`) compares a RATE: household intent misses similar to the proposal's target patterns per household turn the
router logged (`local_text_logs.ROUTER_SHADOW`, one record per routed turn), 48 h each side. It first needs PROOF OF COVERAGE: at least
`MEASURE_MIN_TURNS` (30) household turns in EACH window and at least one miss-log record of any kind inside the after window (the miss
writer was alive), else `unmeasured` / `no_before_data` or `no_after_coverage`: a log that stopped is never an improvement. Then at
least `MEASURE_MIN_BEFORE` (5) target misses before (else `sample_too_small`); `validated` at a rate of 0.8x or less, `regressed` at
1.2x or more (moved to `reverted`; nothing is rolled back automatically), otherwise `no_change`; no target patterns is
`no_target_signal`. Every outcome is written to `validation_result`
(`schema: 2`, counts only); a verdict without `schema: 2` is shown as not measurable.

The Multica board mirror of new proposals (and of measured status) is opt-in: `ZOE_EVOLUTION_BOARD_MIRROR` (default off). The NOTICE
pass runs once a night, in the night digest; the board's "Evolution Nightly Notice" autopilot is a no-op.

## Migration 0062

Adds the columns and `evolution_proposal_history`; REMAPS every existing row (pending -> proposed, approved -> proposed + decision
approved, deployed -> applied, validated -> kept, failed -> reverted, rejected stays; any other value becomes rejected with the old value
in `decision_note`), records one history row per row (`legacy status: <old>`), backfills class code / tier 3 / a component from the
type; folds every `struggle_proposals` row in and drops its status / decision columns; on PostgreSQL adds the CHECK constraints and
the `proposed` default. Rerun-safe; the downgrade gives every old status back (an odd value or a NULL, noted in `decision_note`, included). Proven on SQLite (the tests) and on a throwaway pg17
from the live image (the remap, the fold, the 0027 trigger interplay, a round trip). Chains onto `0059`; the coordinator re-points
`down_revision` if another migration lands first (`test_one_head`).

## Owner steps

1. Nothing to flip: the queue is live with the deploy that runs 0062 (the struggle proposals stay `shadow`; the legacy writers keep
   writing, now into the queue).
2. Decide the fate of the legacy `pending` rows (now `proposed`, class code): the decide page's "Dismiss old proposals" rejects the
   stale ones in one go.
3. Round-1 checklist item 2: stop and delete generation 0 (`zoe-training.timer`, the cron self-review, `~/training/improvement-proposals`).
