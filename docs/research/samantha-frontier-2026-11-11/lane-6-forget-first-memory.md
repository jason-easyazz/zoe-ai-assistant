---
type: research-lane
title: Lane 6 - forget-first memory (lineage-enforced forget)
date: 2026-11-11
---

# Lane 6 - forget-first memory

## Hypothesis

If the write API forces every derived artifact to carry lineage pointers to its sources, forget becomes one generic operation
(tombstone sources, invalidate the transitive closure, rebuild or drop), a new derived layer is covered automatically, and
completeness is proven by a property test instead of audited by inventory. [Speculative going in]

## What was built

Branch `labs/forget-first-memory`, commit `687cd5c81` (not pushed, no PR), worktree `~/.worktrees/forget-first-memory`.
`labs/forget-first-memory/`: `lineage_store.py` (240 lines), `test_lineage_store.py` (99), `bench.py` (139), `README.md`; one
Child DOX Index line in `labs/AGENTS.md`. Stdlib + sqlite3 3.37.2 + pytest; `hypothesis` not installed, seeded `random` used.

- Choke-point `write_derived`: no parents -> refused; tombstoned/quarantined parent -> refused (same txn as the edge insert);
  payload equal to forgotten text -> refused.
- Forget phase 1 (atomic): salted-HMAC ledger, tombstone, delete source + FTS row, recursive-CTE closure -> quarantined.
  Phase 2: rebuild queue from survivors, drop closure, FTS optimize. Serve: live-only AND ancestry re-walk vs tombstones.

## Results

| check | result | label |
|---|---|---|
| property tests, 60 random DAGs (depth 1-4, verbatim-copy derivation) + 20 serve-wall + 20 negative-control + 2 file cases | 103 passed, 2.71 s, rc 0 | Proven (synthetic) |
| negative control: one lineage-less legacy write | caught 20/20 seeds | Proven |
| 8 single mutations (no FTS delete, no ledger, no source delete, no quarantine, no parent check, no FTS optimize, no serve wall, no lineage check) | all red (61/60/61/57/56/49/17/1 failing cases) | Proven |
| FTS5 delete without optimize | forgotten term stays in `sources_fts_data` (8/10 victims) | Proven |
| file bytes after forget: plain / secure_delete only / optimize only / secure_delete+optimize / optimize+VACUUM | 10/10, 10/10, 10/10, 0/10, 0/10 residue; 6.2 / 6.3 / 11.7 / 13.2 / 15.9 ms per forget | Experimental |
| choke-point cost | 287.8 vs 162.5 us per write (+125 us) | Experimental |
| phase 1 forget latency 1e3 -> 1e5 sources, depth 1-4 | 2.1 - 3.3 ms median (p95 <= 10.7) | Experimental |
| phase 2 purge | 1.9 ms (1e3 d1) -> 24.1 ms (1e5 d4) | Experimental |
| FTS optimize | 2.4 ms (1e3) -> ~61 ms (1e5), O(index) | Experimental |
| closure fan-out (uniform parents) | median 1-6, max 28 (1e5 d4) | Experimental |
| serve ancestry re-walk | ~50 us per artifact (5000 artifacts = ~248 ms) | Experimental |

Own-design residue found and fixed during the lane: FTS deleted by an UNINDEXED column = full scan (40 ms phase 1 at 1e5);
FTS delete leaves terms until optimize. Both reported in the README.

## Writer lineage in the real code (services/zoe-data, origin/main 1ddb70951, read only)

| derived store | writer file:line | lineage? |
|---|---|---|
| night_observations | night_store.py:143 | yes (turn_id) |
| night_threads | night_store.py:143, night_mind.py:1613 | partial (via observations) |
| now_cards | now_card.py:290 | yes (refs in card_json, serve re-check :423) |
| user_model_cards | user_model_card.py:309 | yes (mem ids, re-check :362) |
| user_portraits | user_portrait.py:359 | no (count only) |
| member_register (speaking style) | member_register.py:218 | no |
| conversation_notes | conversation_notes.py:404 | yes (turn_ids) |
| proactive_candidates | proactive/selector.py:263, :687; commitments.py:533 | yes (source_ref), 3 writers |
| open_loops (digest) | memory_digest.py:3298 | no |
| open_loops (day-review apply) | omi_review_apply.py:170 | yes (context = ref) |
| person_relationships | people_graph.py:439 | partial (optional turn_id) |
| pending_suggestions | pending_suggestions.py:303; anticipation.py:419; list_sharing.py:146 | no, 3 writers |
| omi_day_candidates | omi_day_review.py:1635 | yes (evidence, recording_ids) |
| palace digest fact | memory_digest.py:1252 | partial (sha1-of-text turn id :1016 + verbatim excerpt) |
| palace digest fact | memory_digest.py:1548 | yes (user_turn_id) |
| palace emotional moment | memory_digest.py:1705 | no |
| palace synthesis | memory_digest.py:2954/:2971 | partial (related_ids shared with similarity links :2721) |
| router shadow log | semantic_router.py:265 | no (content hash) |
| night_extract files | night_extract.py:552 | yes (turn_id) |
| self_review files | self_review.py:969 | yes (turn_id) |

24 writer sites: 12 yes, 4 partial, 8 no. [Experimental: grep + reading, not exhaustive; "yes" = pointer recorded, not used by forget]

## Verdict

ONE RULE to retrofit, not a new engine. Z0 keeps the authority model (237/237), the KEEP_Z0 bake-off, the ledger + serve walls,
the inventory CI (127 stores, 51 reached, 44 gaps; docs/knowledge/forget-reach.md:113) and the pointer-only memory views
(docs/knowledge/memory-views.md:21). The missing piece is a choke-point: "no derived write without lineage" via one shared
helper + a CI test that every INSERT into a registered derived table passes non-empty sources. Caveat: Zoe's main forget is
"forget <name>" (redact in place across sources); lineage only helps once the source set is found, it does not replace the name match.

## Next experiment (pass/fail)

In the lab, port the 3 "no" writers that the completeness probe already drives (portrait, member_register, digest open_loops)
behind a `derived_write(table, row, sources)` shim over the probe's in-process fakes, and replace their per-layer erasers with
the generic closure forget. PASS: the probe's forgotten AND erase_missed arms withhold the name for all three with zero
layer-specific erase code, and an AST check finds 0 INSERTs into those tables outside the shim. FAIL: any layer still needs a
bespoke eraser or name match after the source set is known, or the shim cannot express its inputs (a writer reads more than it can declare).

## Could not verify

- Behaviour under LLM paraphrase derivation (lab derivation is concatenation).
- Chroma/HNSW, Postgres TOAST/WAL, JSONL logs, sidecar/KV caches, backups: not modelled.
- Freed filesystem blocks of the deleted rollback journal: not examined.
- Which Zoe stores use FTS5 (and so share the optimize finding).
- Hub-source fan-out (uniform random parents only); cause of phase-2 growth not profiled.
- The writer table is a single manual pass; other derived writers may exist.
