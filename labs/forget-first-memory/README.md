# forget-first-memory (lab, 2026-11-11)

A ~240-line sqlite3 store that tests one idea: **if the write API refuses any derived artifact that does not carry lineage
pointers to its sources, forget becomes one generic operation and its completeness can be proven by a property test.**
Scratch data only (invented words + random hex markers), temp-dir sqlite, nothing imported from `services/`, never wired
anywhere. Labels: [Proven] = a test or the bench printed it on this box; [Experimental] = measured on synthetic data only;
[Speculative] = reasoning.

## What it is

`lineage_store.py`

- `ingest(sid, payload)`: a source row + an FTS5 row. Refused when the HMAC-salted hash of the NFKC/casefold/whitespace-normalised
  payload is in `forget_ledger` (re-ingest block) or the id is tombstoned.
- `write_derived(aid, layer, payload, parents)`: **the choke-point.** No parents -> `LineageRequired`. A parent that is
  tombstoned, quarantined or missing -> `Forgotten` (checked inside the same `BEGIN IMMEDIATE` as the edge insert, so a racing
  forget always sees the edges). A payload whose normalised hash is ledgered -> `Forgotten` (same transaction; one two-connection test holds the
  write lock, commits a forget while a writer waits, and checks the writer is refused - that single interleave is all that is tested).
- `forget_phase1(sid)` (atomic): ledger the hash (and the hash of every derived payload in the closure, so the derived text
  cannot be re-admitted either; exact-text match after normalisation only, a paraphrase is not caught), tombstone the id, delete the source row and its FTS row (by rowid), compute the
  transitive closure of derived artifacts with one recursive CTE, mark them `quarantined`.
- `forget_phase2()`: queue a rebuild of each quarantined artifact from its surviving parents (ids only), drop the artifacts and their
  edges, then `optimize` the FTS index.
- `rebuild(derive)`: recompute queued artifacts shallowest-first from survivors, through `write_derived`.
- `serve(layer)`: `state = 'live'` AND a re-walk of each artifact's ancestry against `tombstones` (fail closed), so a missed
  quarantine still cannot serve forgotten-derived text.
- `residue(needle)`: scans every column of every table, FTS5 shadow tables included, plus an FTS `MATCH`. `file_residue()` scans
  the raw db/journal/wal bytes for the needle's tail (FTS5 prefix-compresses terms, so the head of a term may not be contiguous).
- `write_legacy()`: a deliberately lineage-less write path, used only as the negative control.

`test_lineage_store.py` (107 cases, seeded `random`, no `hypothesis` installed): random DAGs of 8-40 sources, depth 1-4,
5-30 artifacts per layer, fan-in 1-3, derivation = verbatim concatenation of parents (worst case: every derived row copies its
sources). `bench.py`: forget latency + fan-out grid and an on-disk residue comparison.

## Results (this box, Jetson Orin, sqlite 3.37.2, 2026-11-11 22:38 AWST)

**Property tests [Proven, on synthetic DAGs]:** `107 passed` (rc 0), under `flock -x /tmp/zoe-test-lane.lock`, no xdist.
Over 60 random DAGs, after forgetting 1-3 sources: zero residue in any table/column/FTS shadow table or FTS `MATCH`, nothing
served (mid-forget, after purge and after rebuild), re-ingest refused (exact and case/whitespace variant), late derived writes onto a
quarantined parent refused, no collateral (every artifact outside the closures still served), no over-deletion (every surviving
source still present). Over 20 DAGs the serve wall alone withholds a closure whose quarantine was reverted.

**Negative control [Proven]:** one lineage-less legacy write that copies the forgotten payload makes the SAME property check fail
in 20/20 seeds (residue in `artifacts.payload`). The instrument is not blind.

**Mutation check [Proven]** (scratch copies, each mutation alone; every one turns the suite red):

| mutation | failing cases |
|---|---|
| skip the FTS row delete | 61 |
| skip the ledger insert | 60 |
| skip the source row delete | 61 |
| skip the quarantine | 57 |
| skip the parent-liveness check in the choke-point | 56 |
| skip the FTS `optimize` | 49 |
| serve wall off (`continue` -> `pass`) | 17 |
| lineage requirement off | 1 |

**Finding [Proven]: an FTS5 delete is not an erase.** With the FTS row deleted but no `optimize`, the forgotten term stays in
`sources_fts_data` segment blobs (in-table residue 8/10 victims; the other 2 had been merged away by automerge). sqlite 3.37.2 has no
FTS5 `secure-delete` option (added in 3.42), so the lab compacts the whole index per forget. Any Zoe FTS index over member words has
the same property. [Speculative for Zoe: not checked which Zoe stores use FTS5.]

**On-disk residue, 1e4 sources depth 2, 10 forgets [Experimental]:**

| strategy | ms / forget | in-table residue | file bytes before | file bytes after |
|---|---|---|---|---|
| plain delete | 6.2 | 8/10 | 10/10 | 10/10 |
| `secure_delete=ON` only | 6.3 | 8/10 | 10/10 | 10/10 |
| FTS `optimize` only | 11.7 | 0/10 | 10/10 | 10/10 |
| `secure_delete=ON` + FTS `optimize` | 13.2 | 0/10 | 10/10 | **0/10** |
| plain + FTS `optimize` + `VACUUM` | 15.9 | 0/10 | 10/10 | **0/10** |

Only secure_delete+optimize or a VACUUM leaves no recoverable bytes in the db file. Freed filesystem blocks of the deleted
rollback journal were NOT examined.

**Choke-point cost [Experimental]:** `write_derived` 287.8 us/write vs a bare one-row transaction 162.5 us (3 parents, 1e3
sources, `synchronous=OFF`): about +125 us per derived write.

**Forget latency and fan-out [Experimental]** (30 random victims per row; artifacts = sources/2 per layer, fan-in 1-3 uniform; FTS
optimize timed separately; serve = all L1 artifacts with the ancestry re-walk):

| sources | depth | artifacts | fan-out median / p95 / max | phase 1 ms median (p95) | phase 2 ms median | FTS optimize ms | serve L1 ms | db MB |
|---|---|---|---|---|---|---|---|---|
| 1e3 | 1 | 500 | 1 / 2 / 2 | 2.11 (2.56) | 1.94 | 2.4 | 23.4 | 0.3 |
| 1e3 | 4 | 2000 | 4.5 / 19 / 20 | 2.36 (2.72) | 2.75 | 2.3 | 23.1 | 0.6 |
| 1e4 | 1 | 5000 | 1 / 2 / 3 | 2.32 (2.72) | 2.29 | 6.9 | 247.8 | 2.5 |
| 1e4 | 4 | 20000 | 6 / 14 / 16 | 2.58 (10.66) | 4.67 | 6.9 | 253.3 | 5.0 |
| 1e5 | 1 | 50000 | 1 / 2 / 4 | 2.65 (3.94) | 7.19 | 60.1 | - | 25.0 |
| 1e5 | 2 | 100000 | 1 / 5 / 7 | 2.71 (3.40) | 12.66 | 60.8 | - | 34.2 |
| 1e5 | 3 | 150000 | 4.5 / 9 / 9 | 3.27 (4.01) | 18.77 | 61.7 | - | 43.5 |
| 1e5 | 4 | 200000 | 5.5 / 22 / 28 | 3.27 (4.93) | 24.10 | 61.0 | - | 52.9 |

All 12 rows are printed as JSON on stdout (nothing is written outside the temp dir); whole bench wall 37.3 s. Phase 1 (the part that makes forget take effect) stays ~2-3 ms to
1e5 sources. Phase 2 grows roughly with table size (cause not profiled). The FTS optimize is the dominant cost at 1e5 (~61 ms) and
is O(index), not O(closure). The serve ancestry re-walk costs ~50 us per artifact: fine for a per-turn packet, expensive for bulk.
A first version deleted the FTS row by an UNINDEXED `id` column: a full scan, 40 ms phase 1 at 1e5; fixed by deleting by rowid.

## What this does NOT prove

- Real derivation is an LLM, not concatenation. Lineage covers paraphrase only if the writer declares every input it read; a
  writer that reads more than it declares (a whole day's turns, a cached card) leaks exactly like the legacy path. [Speculative]
- Zoe's commonest forget is **"forget Dana"**: a concept across many sources, redacted in place, not one source row deleted.
  Lineage needs a source SET first (still a name match), and dropping whole sources is over-deletion compared with today's
  in-place redaction. This lab only models "forget this item". [Speculative]
- Nothing outside one sqlite file: Chroma/HNSW, Postgres TOAST/WAL, JSONL logs, the Flue sidecar cache, llama KV cache, backups.
- Fan-out is for uniform random parents; a hub source (a person every digest mentions) fans out far wider.
- The salt sits in the same db (lab shortcut); a ledger hash is only as private as the salt.

## Comparison with the existing Z0 system (honest)

Z0 already does better on what matters most: the authority model (237/237 authority cells,
`docs/research/memory-bakeoff-decision-2026-10-08.md:26`), a bake-off verdict `KEEP_Z0` (2026-10-08), a forget ledger + serve
walls, and an inventory that fails CI on drift (`docs/knowledge/forget-reach.md:113`: 127 stores, 51 reached, 32 out of scope,
44 known gaps). Zoe also already has the pointer idea: memory views store nothing but ids, so "forget is complete by
construction" (`docs/knowledge/memory-views.md:21`; `services/zoe-data/memory_episodes.py:119` `resolve()` drops dead pointers),
and the forget-completeness probe (`services/zoe-data/tests/test_forget_completeness_probe.py:75`) drives every derived layer
through forgotten / erase_missed / control arms. Several layers already carry pointers and re-check them at serve time
(`now_card.py:423` `_live`, `user_model_card.py:362` `_live_ids`).

What is missing is not an engine but **a rule with a choke-point**: today a new derived layer is covered only when someone adds a
`Store` and a `Layer` row by hand, and half the writers record no pointer (table below). So the right outcome is **ONE RULE to
retrofit**: "no derived write without lineage", enforced by one shared write helper (the analogue of `write_derived`) plus a CI
property/AST test that every INSERT into a registered derived table goes through it with non-empty sources. Not a new engine:
house rule ADOPT-don't-rebuild, Z0 won the bake-off, and the lab's generic forget only covers single-item forget.

## Writer lineage in the real code (read, not run; services/zoe-data, origin/main 1ddb70951)

| derived store | writer (file:line) | lineage? |
|---|---|---|
| night_observations | night_store.py:143 (from night_mind) | yes: `turn_id` |
| night_threads | night_store.py:143, night_mind.py:1613 | partial: only via observations.thread_id |
| now_cards | now_card.py:290 | yes: per-line ref in card_json, re-checked at serve (:423) |
| user_model_cards | user_model_card.py:309 | yes: mem id per item, re-checked (:362) |
| user_portraits | user_portrait.py:359 | **no**: `generated_from_memory_count` only |
| member_register (speaking style) | member_register.py:218 | **no**: provenance rank only |
| conversation_notes | conversation_notes.py:404 | yes: `turn_ids` |
| proactive_candidates | proactive/selector.py:263, :687; commitments.py:533 | yes: `source_ref` NOT NULL (3 writers) |
| open_loops (digest) | memory_digest.py:3298 | **no** |
| open_loops (day review apply) | omi_review_apply.py:170 | yes: `context` = candidate ref |
| person_relationships | people_graph.py:439 | partial: optional `turn_id` evidence column |
| pending_suggestions | pending_suggestions.py:303; anticipation.py:419; list_sharing.py:146 | **no**: session id only (3 writers) |
| omi_day_candidates | omi_day_review.py:1635 | yes: `evidence`, `recording_ids` |
| palace fact (digest) | memory_digest.py:1252 | partial: turn id is a sha1 of the text (:1016) + verbatim `source_excerpt` |
| palace fact (digest) | memory_digest.py:1548 | yes: `user_turn_id` (+ verbatim excerpt) |
| palace emotional moment | memory_digest.py:1705 | **no** |
| palace synthesis | memory_digest.py:2954 / :2971 | partial: `related_ids`, the same field as similarity links (:2721) |
| router shadow log | semantic_router.py:265 | **no**: 12-hex content hash, recoverable only by rehash join |
| night_extract facts (files) | night_extract.py:552 | yes: `turn_id` |
| self_review records (files) | self_review.py:969 | yes: `turn_id` |
| memory views (episodes etc.) | memory_episodes.py | n/a: store nothing |

**Count (24 writer sites): 12 yes, 4 partial, 8 no.** [Experimental: one pass of grep + reading, not exhaustive; "yes" means a
pointer is recorded, NOT that forget uses it: entity forget still matches names.]

## Run

```
flock -x -w 3600 /tmp/zoe-test-lane.lock python3 -m pytest -q -p no:cacheprovider labs/forget-first-memory/test_lineage_store.py
flock -x -w 3600 /tmp/zoe-test-lane.lock python3 labs/forget-first-memory/bench.py   # ~40 s, prints the results as JSON
```
