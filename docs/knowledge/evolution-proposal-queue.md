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
2. Decide the fate of the 716 legacy `pending` rows (now `proposed`, class code): reject them in bulk, or let Phase 2 delete the
   writers they came from (design section 9, Phase 2).
3. Round-1 checklist item 2: stop and delete generation 0 (`zoe-training.timer`, the cron self-review, `~/training/improvement-proposals`).
