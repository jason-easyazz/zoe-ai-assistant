---
type: Research
title: People graph and temporal memory - Zoe vs Graphiti/Zep, mem0, Letta, and the Postgres/asyncpg/alembic docs
description: Extends docs/research/memory-fidelity-audit-2026-10-05.md (section 5 and P2.1) with a schema-level audit of the people graph (people + person_relationships) and memory-row validity, a comparison against Graphiti's bi-temporal edge model, mem0 graph memory and Letta blocks, Postgres/asyncpg/alembic upstream guidance checked against the live database, and a ranked target model (bi-temporal everywhere, as_of reads, invalidate-never-delete, edge provenance) with a test and negative control per item.
tags: [memory, people-graph, temporal, bi-temporal, graphiti, postgres, asyncpg, alembic, provenance, research]
timestamp: 2026-10-05
---

# People graph and temporal memory: where Zoe stands, and what "best in the field" needs

Scope. The 2026-10-05 fidelity audit (`docs/research/memory-fidelity-audit-2026-10-05.md`, section 3.6, section 5.4,
P2.1) already says: memory rows get `valid_from`/`invalid_at` only behind the implicit-supersede flag, reads hide
superseded rows so there is no "as of" query, edges have `valid_from`/`valid_to`/`superseded_by`, and provenance is
missing on people and edges. **This record does not repeat that.** It adds (a) the Graphiti/Zep data model read at
the primary source, (b) a column-by-column audit of the edge and person writers, including five defects the audit
did not list, (c) Postgres/asyncpg/alembic best practice checked against the live database, and (d) a concrete
target schema, an `as_of` API shape, a migration path and a test plan.

Evidence labels: **[src]** file:line in this worktree (base `25948580`, `main`) unless a branch is named;
**[branch]** read via `git show origin/<branch>:<path>` (not merged); **[live]** read-only `psql` against
`zoe-database` (selects and plain `EXPLAIN`, no `ANALYZE`, nothing written; counts only, no names);
**[doc]** URL fetched 2026-10-05; **[unverified]** not confirmed at source. All example names below are synthetic.

---

## 1. What Zoe does today

### 1.1 Schema as it actually is

**[live]** PostgreSQL 17.10 (aarch64), `max_connections=100`, `statement_timeout=0`,
`idle_in_transaction_session_timeout=0`, only the `plpgsql` extension installed, alembic head `0036`.

`person_relationships` columns **[live]**: `id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a,
rel_group, notes, created_at, updated_at, valid_from, valid_to, superseded_by` - **all timestamps are `text`**.
Indexes **[live]**: `person_relationships_pkey (id)` and the partial unique
`person_relationships_pair_active (user_id, person_a_id, person_b_id) WHERE valid_to IS NULL`. Nothing else.
`people` has only its primary key **[live]** (migration 0001 declares no secondary index, **[src]**
`alembic/versions/0001_initial_schema.py:87-102`), and `pg_stat_user_tables` shows `seq_scan=2096, idx_scan=0` on
`people` **[live]** - every lookup is a sequential scan; harmless at 32 rows, a design debt at any real size.

Row counts **[live]**: `people` 32 (14 soft-deleted, 0 partial, 6 distinct owners); `person_relationships` 3, all
current, 0 historical, 0 with `superseded_by`, **3 of 3 with `valid_from IS NULL`**, one owner; `person_activities` 3.
The 3 live edges carry no `valid_from` although the 0015 backfill and `_write_relationship` both set it; the only
other insert path, `POST /people/{id}/relationships`, omits the column **[src]** `routers/people.py:1005-1011`
(cause of the 3 rows is **[unverified]**, but that path is the only one in the tree that produces the shape).

### 1.2 Does every edge/fact carry both timelines? No.

| Object | Event timeline (when true) | Transaction timeline (when Zoe learned / stopped believing) | Type |
|---|---|---|---|
| `person_relationships` | `valid_from`, `valid_to`; `valid_from` = capture time, never event time **[src]** `person_extractor.py:814` (`now = datetime.utcnow()...`) | `created_at`, `updated_at` only; there is no "expired_at": when a belief is retired the time Zoe learned it is overwritten into `valid_to` and `updated_at` | `text` ISO `...Z` from Python, but `text` `NOW()` format (`YYYY-MM-DD HH:MM:SS+00`) from `routers/people.py:1062` and 3 `DEFAULT NOW()::TEXT` columns (0006) |
| `people` row (name, relationship text, birthday, notes, circle) | none | `created_at`/`updated_at` | updated in place **[src]** `routers/people.py:520`, `correction_apply.py:388`, `pending_suggestions.py:664`, `person_merge.py:353` - no history of any attribute change |
| `person_activities` / dates / gift ideas / bucket list | none | `created_at`; `person_activities` has `source`, `session_id`, `mem_id` **[src]** migration 0006 | provenance exists here, and **not** on edges |
| Memory row (Chroma metadata) | `valid_from` (epoch float = `added_ts`, capture time) only when `ZOE_MEMORY_IMPLICIT_SUPERSEDE` is on **[src]** `memory_service.py:1986-1990` | `added_ts`, `last_accessed`; `invalid_at` epoch float written on supersede, again only under the flag **[src]** `:1763-1764`, `:1831` | epoch floats, a different representation from the edges' ISO text |

Two different time representations (epoch float in Chroma metadata, ISO text in Postgres), two different
"closed" encodings (`status=superseded` + `invalid_at` vs `valid_to`), and no shared vocabulary. **[src]** Flags
live today **[live .env]**: `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1`, `ZOE_RELATIONSHIP_GRAPH_ENABLED=1`,
`ZOE_PERSON_MERGE_ENABLED=1`, `ZOE_MEMORY_IMPLICIT_SUPERSEDE=1`, `ZOE_MEMORY_COMPOSE_ENABLED=1`.

### 1.3 Is history queryable ("where did I live before?")? Not through any shipped read.

* The composed prompt packet reads current edges only: `WHERE ... pr.valid_to IS NULL` **[src]**
  `zoe_memory_compose.py:204-211`. The recursive CTE traversal filters `valid_to IS NULL` on both legs **[src]**
  `relationship_graph.py:101-118` (by design: ADR says "current-edge-only").
* `GET /people/{id}/relationships` has **no** temporal filter at all **[src]** `routers/people.py:894` - once a
  superseded edge exists it is returned beside the current one as if both were live (latent: 0 historical rows today
  **[live]**, so no one has seen it).
* No read takes a timestamp. The history is stored (when the flag is on) and unreadable. For memory rows,
  `_BLOCKED_READ_STATUSES` hides `superseded` outright **[src]** `memory_service.py:842`.
* Sentinels: the person graph has no concept of an *address* or *attribute over time* at all - "where did I live"
  is a memory-row question, so it depends on the memory-row validity above, not on edges.

### 1.4 Invalidate vs delete: not consistent. Eight paths, three behaviours.

| Path | Behaviour | Evidence |
|---|---|---|
| Extractor edge change (flag on) | close old (`valid_to`, `superseded_by`) then insert new | **[src]** `person_extractor.py:749-790`, `:874-892` |
| Extractor edge change (flag off) | `ON CONFLICT ... DO NOTHING`: the new belief is **silently dropped** | `:901` |
| `PUT /people/{id}/relationships/{rel}` | **in-place overwrite** of `rel_type`, labels, group; no history | `routers/people.py:1049-1066` |
| `DELETE /people/{id}/relationships/{rel}` | **hard delete**, no audit row | `:1085` |
| Merge: self-edge | hard `DELETE` | `person_merge.py:283` |
| Merge: duplicate current edge | hard `DELETE` of the source-derived edge (its `valid_from`/`notes` are lost) | `person_merge.py:305` |
| Pet correction | in-place rewrite of `rel_type`, labels **and endpoint order** on the current edge; fallback closes with `valid_to` but **no `superseded_by`** | `correction_apply.py:397-409` |
| Person delete | soft (`deleted=1`); edges stay "current" and are hidden only by `JOIN ... deleted=0` | `routers/people.py:555`, `zoe_memory_compose.py:207-208` |

So "invalidate, never delete" is the rule in one writer and violated by five others. Of the memory side, the audit
already lists the hard-delete gap (`_delete_ids` writes no audit).

### 1.5 Provenance on edges

On `main`: **none**. Columns are exactly those in 1.1; the named-relations writer (PR #1874, branch
`fix/named-relations-kept`) calls the same `_write_relationship(user_id, *edge, db)` with no provenance argument
**[branch]** `named_relations.py` (`_apply_one`), and it stamps neither `user_confirmed` nor `review_ui` on edges
(grep of the branch finds `review_ui` only as a pre-existing memory source string at `memory_service.py:1658` and
`memory_tombstones.py:40`). The `source`/`session_id`/`excerpt` it does pass go to the memory fact
(`_ingest_to_mempalace`), not the edge.

Provenance on edges exists only in PR #1868 (`feat/memory-authority`): migration `0037_relationship_edge_authority.py`
adds two nullable TEXT columns `authority` and `origin`, no backfill, no index; `_stamp_edge` writes them in a
**second** statement after the insert (`person_extractor.py` on that branch, `_stamp_edge`/`_edge_may_change`) and
`create_edge_from_candidate` stamps `user_confirmed` **[branch]**. Gaps even after #1868: no pointer to the
**turn** (`chat_messages` id) or the quote that produced the edge (the audit's "Graphiti episode" idea), no
`asserted_by` speaker identity, no `closed_by`/`close_reason` on supersession, and the 0037 downgrade silently does
nothing on SQLite (only the `postgresql` branch drops columns) **[branch]**.

### 1.6 LIKE-substring person resolution

`_resolve_person_uuid` **[src]** `person_extractor.py:259-276`: `WHERE user_id=$1 AND deleted=0 AND lower(name) LIKE
lower('%{name}%')` then `cursor.fetchone()` with **no ORDER BY**. Consequences, all from the SQL: "Ann" resolves to
"Joanna"; "Mika" to "Mikaela"; with two matches the winner is whichever heap row Postgres returns first
(nondeterministic, can change after a VACUUM or update); the wildcard characters `%`/`_` in a name are not
escaped; the query is a seq scan **[live EXPLAIN]** (`Seq Scan on people ... Filter: (lower(name) ~~ '%ann%')`).
It is the single resolver for: edge endpoints (`:817`, `:842`), fact attachment (`:1031`, `:1184`),
`contact_backfill.py:510`, and PR #1874's `_name_clash`/`_kept_pet` (which *detects* the clash after the fact by
comparing the resolved row's name, `named_relations.py`, instead of fixing the resolver). Both comments in
`memory_extractor.py:575` and `memory_service.py:698-736` already work around it by hand. The ADR's "entity
resolution" increment shipped merge (`person_merge.py`), not resolution.

### 1.7 Migrations vs alembic practice

* **Runtime vs migration drivers differ:** runtime is asyncpg (`db_pool.py:174`), migrations run through
  `postgresql+psycopg2` **[src]** `alembic/env.py:13-14`, with `target_metadata = None` (no autogenerate, no
  `naming_convention`).
* 0007 mixes schema and data: four `UPDATE people SET circle...` remaps plus a lossy downgrade comment ("best-effort")
  **[src]** `0007_person_relationships.py:44-55`. 0006 does the same (`UPDATE people SET circle = CASE ... LIKE`).
  0015 backfills `valid_from` with a one-line `UPDATE`. 0037 [branch] is clean (columns only).
* Idempotent: yes on Postgres (`IF NOT EXISTS` throughout), dialect-branched for SQLite.
* Reversible: 0015's downgrade can fail by design when history exists (documented) and always drops the data in
  the dropped columns; 0007's downgrade re-adds a column it cannot repopulate.
* Indexes are created with plain `CREATE INDEX` (blocking), fine at this size.
* Explicit index/constraint names are used everywhere (so the naming-convention footgun does not bite), but
  nothing is declared through `op.create_index`/`op.create_table`, so there is no metadata to diff or autogenerate.

### 1.8 Postgres/asyncpg usage vs live DB

* Pool: `min_size=2, max_size=10, command_timeout=30` **[src]** `db_pool.py:174-179`; bounded `acquire` with
  `PoolExhaustedError` (10 s) and gauges **[src]** `:23-60`; 3 live backends vs `max_connections=100` **[live]**.
  Sizing is sound.
* **Transactions: the compat layer makes `commit()` a no-op** (`AsyncpgCompat.commit` is `pass` - "asyncpg
  auto-commits outside explicit transactions", **[src]** `db_pool.py:339-340`) so the dozens of `await db.commit()`
  calls in writers mean nothing. Only `person_merge.merge_person` opens a real `transaction()` **[src]**
  `person_merge.py:197-210`. The edge supersede in `_write_relationship` is **UPDATE (autocommit) then INSERT
  (autocommit)** with a compensating `_reopen_edge` if the insert raises **[src]** `person_extractor.py:874-925`;
  the code comment states PostgreSQL auto-commits the supersede.
* **Locks:** the only advisory lock in the service is `multica_board_runner.py:105`
  (`pg_advisory_xact_lock`). No `FOR UPDATE` and no advisory lock on edge supersede or on merge.
* **Statement timeout:** none server-side (`statement_timeout=0` **[live]**); only the client-side `command_timeout=30`.
* **Upserts:** `ON CONFLICT (user_id, person_a_id, person_b_id) WHERE valid_to IS NULL DO NOTHING` correctly infers
  the partial index **[src]** `:901`; the REST insert instead catches the exception text (`"unique" in str(exc)`)
  **[src]** `routers/people.py:1015-1018`.
* **Indexes missing for the queries Zoe actually runs:** the recursive CTE's second leg filters
  `person_b_id = <frontier>` and the pair index leads with `(user_id, person_a_id)`, so that leg cannot use it
  (`EXPLAIN` shows a seq scan on the edge table **[live]**, trivial at 3 rows); `people (user_id, deleted)` and a
  `lower(name)` trigram or exact-match index are absent; no `(person_id, valid_to)`-style history index exists
  because no history read exists.

---

## 2. What the field and the upstream docs say

### 2.1 Graphiti / Zep (primary sources fetched today)

* **Three tiers** - raw *episodes*, resolved *entities* with typed *entity edges*, and *communities* (label
  propagation, extended incrementally by plurality of neighbours, refreshed periodically because incremental
  updates "gradually diverge") **[doc]** https://arxiv.org/html/2501.13956 (authors, 12 pages, 2025-01-20:
  https://arxiv.org/abs/2501.13956).
* **Bi-temporal edge, four timestamps.** Transaction timeline `t'_created`, `t'_expired`; event timeline
  `t_valid`, `t_invalid` **[doc]** arXiv HTML. Zep's product docs name them `created_at` (when Zep learned it),
  `valid_at` (when it became true), `invalid_at` (when it stopped), `expired_at` (when Zep learned it stopped);
  worked example: marriage then divorce **[doc]** https://help.getzep.com/facts. The code's `EntityEdge` holds
  `valid_at`, `invalid_at`, `expired_at`, `created_at`, `reference_time` ("reference timestamp from the episode
  that produced this edge"), `episodes` ("list of episode ids that reference these entity edges") and a free
  `attributes` dict **[doc]** https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/edges.py.
* **Invalidation rule** **[doc]** `graphiti_core/utils/maintenance/edge_operations.py`: candidates come from a
  hybrid semantic search of the fact text over the whole `group_id`; on contradiction the old edge gets
  `invalid_at = new.valid_at` and `expired_at = now (UTC)`; the loop `continue`s (no invalidation) when the old
  edge ended before the new began or vice versa (`old.invalid_at <= new.valid_at or new.invalid_at <= old.valid_at`),
  so only genuinely *overlapping* contradictions retire anything. "Graphiti consistently prioritizes new
  information" **[doc]** arXiv. (The whole-graph candidate scan is the source of the audit's cited over-invalidation
  report; Zoe's same-pair key does not have that failure - see 3.G7.)
* **Provenance** is structural: "episodes and their derived semantic edges maintain bidirectional indices"; "Every
  derived fact traces back here" **[doc]** arXiv; https://github.com/getzep/graphiti. Search can filter by
  `episode_uuids` (max 256) and re-rank by `episode_mentions`.
* **Temporal query surface** **[doc]** https://help.getzep.com/searching-the-graph: edge-scope datetime filters
  on all four timestamps with `= <> > < >= <= IS NULL IS NOT NULL`, ISO-8601 with timezone, outer arrays OR /
  inner arrays AND; plus `edge_types`, `node_labels`, `connected_node_uuids`, BFS origin (up to 5), rerankers
  `rrf | mmr | cross_encoder | episode_mentions | node_distance`. Graphiti's own wording: "Query what's true now,
  or what was true at any point in time"; old facts are "invalidated - not deleted" **[doc]** README. The point-in-time
  query is therefore `valid_at <= T AND (invalid_at IS NULL OR invalid_at > T)`; "what we believed at K" adds the
  same predicate on `created_at`/`expired_at`.
* **Stated limits:** works best with LLMs that support structured output; smaller models risk extraction failure;
  backends Neo4j / FalkorDB / Neptune **[doc]** README. This is the same finding as the in-repo bake-off (a 270M-4B
  local model cannot be the extractor of record), which is why the right piece to borrow is the *data model*, not
  the pipeline.
* An attribute-over-time gap exists on the Zep side too: facts live on edges only, and there is no authority axis
  (already in the audit, section 5.1).

### 2.2 mem0 graph memory

**[doc]** https://docs.mem0.ai/open-source/features/graph-memory (fetched today): entities are nodes, links are
"inferred from co-occurrence rather than declared", schema-free and zero-configuration; the external graph stores
(Neo4j, Memgraph, Kuzu, AGE, Neptune) are **deprecated**, `enable_graph` and `graph_store` are no longer used and
the old `relations` field is "always an empty list"; entity matches give a ranking *boost* combined with vector and
BM25 into one `score`. The page says nothing about conflict resolution, update, deletion or temporal validity.
Net: mem0 has moved **away** from typed, dated relationship edges; for a relationship-truth benchmark it is the
weakest of the three on exactly the property Zoe is building. Combine with the audit's note that v3 is ADD-only and
ranking resolves conflicts.

### 2.3 Letta memory blocks

**[doc]** https://docs.letta.com/guides/agents/memory-blocks: a block is `label`, `value`, `description`, `limit`
plus `read_only`; blocks can be shared ("update once, visible everywhere"); agents edit through memory tools;
"last write wins" under concurrent modification. The page documents **no** versioning, history or temporal
feature. The design lesson that transfers is the **description-governed always-in-context block with an
agent-immutable flag** (maps onto Zoe's user-model card / persona layer), not a temporal model.

### 2.4 Chroma (Zoe's memory-row store)

**[doc]** https://docs.trychroma.com/docs/querying-collections/metadata-filtering: operators `$eq $ne $gt $gte $lt
$lte $in $nin $and $or`; comparisons work on strings, ints, floats, booleans; **no null/missing-field check is
documented** ("`$nin` returns results where the attribute's key is not present", nothing explicit for null). This
matters for as_of reads: "`invalid_at` is absent" (the open interval) cannot be expressed in the store query; it
must be a Python-side filter (as `_semantic_search` already does for status/expiry, audit 3.2) or a sentinel value.

### 2.5 Postgres, asyncpg, alembic

* **Temporal integrity in the database.** Range types plus an exclusion constraint prevent overlapping validity:
  `EXCLUDE USING GIST (room WITH =, during WITH &&)` with the `btree_gist` extension to mix scalars and ranges
  **[doc]** https://www.postgresql.org/docs/current/rangetypes.html. The PostgreSQL 18 manual additionally
  documents `PRIMARY KEY/UNIQUE (..., range WITHOUT OVERLAPS)` and `FOREIGN KEY (..., PERIOD range)` ("temporal
  key") **[doc]** https://www.postgresql.org/docs/18/sql-createtable.html. Zoe runs 17.10 **[live]** and has no
  `btree_gist` installed, so `WITHOUT OVERLAPS` is a post-upgrade option; the introduction version is
  **[unverified]** (the page fetched states only that it is the PG18 manual).
* **Partial unique indexes** are the documented way to allow one "active" row among many history rows
  (`CREATE UNIQUE INDEX ... WHERE success`), with the caveat that the planner uses a partial index only when it can
  prove the query predicate implies the index predicate, and **parameterised predicates do not** prove it
  **[doc]** https://www.postgresql.org/docs/current/indexes-partial.html. Zoe's `valid_to IS NULL` is a literal, so
  it is safe.
* **ON CONFLICT:** `DO UPDATE` needs an explicit `conflict_target`; the partial-index `WHERE` predicate lets it infer
  the index; the statement is atomic under concurrency; a row cannot be affected twice in one statement
  **[doc]** https://www.postgresql.org/docs/current/sql-insert.html. Zoe's use matches the doc.
* **Advisory locks:** prefer transaction-level (`pg_advisory_xact_lock`) for short critical sections - released
  automatically at commit/rollback; session-level locks survive rollback; beware `LIMIT` with a locking function in
  the same `SELECT` **[doc]** https://www.postgresql.org/docs/current/explicit-locking.html. Note asyncpg's pool
  `reset` runs `SELECT pg_advisory_unlock_all()` **[doc]** https://magicstack.github.io/asyncpg/current/api/index.html
  but only transaction-level locks are safe regardless.
* **Timeouts:** `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout` all default to 0
  (disabled); the manual warns against setting the first two in `postgresql.conf` because it affects every session,
  and says the idle-in-transaction timeout stops idle sessions holding locks and blocking vacuum **[doc]**
  https://www.postgresql.org/docs/current/runtime-config-client.html. The per-connection route is asyncpg's
  `server_settings` on the pool (**[unverified]** this session: parameter name from the asyncpg API, not re-fetched).
* **asyncpg transactions:** a statement outside `connection.transaction()` is auto-committed; nested `transaction()`
  blocks become savepoints **[doc]** https://magicstack.github.io/asyncpg/current/api/index.html#transactions.
  Pool defaults: `min_size=max_size=10`, `max_queries=50000`, `max_inactive_connection_lifetime=300`; do not use
  prepared statements through pgbouncer in transaction mode **[doc]** same page.
* **Alembic:** "Alembic migrations are designed for schema migrations ... it's not in fact advisable in the general
  case to write data migrations that integrate with Alembic's schema versioning model"; downgrades "might require
  deletion of data"; use a separate data-migration script or `-x data=true` conditional steps; make steps idempotent
  with `IF [NOT] EXISTS`; name constraints and indexes so `drop_constraint` is portable
  **[doc]** https://alembic.sqlalchemy.org/en/latest/cookbook.html and
  https://alembic.sqlalchemy.org/en/latest/naming.html.

---

## 3. Gaps ranked

Impact (I) and effort (E) are 1-5 (5 = highest impact / largest effort). Rank = I descending, then E ascending.

| # | Gap | Evidence | I | E |
|---|---|---|---|---|
| G1 | **No as_of read, and the default `GET .../relationships` shows history as current.** The history the flag stores cannot be asked for; the one list endpoint that does not filter will present a closed edge as live the first time one exists. | 1.3; `routers/people.py:894` | 5 | 2 |
| G2 | **Supersede is not atomic** (autocommit UPDATE then INSERT + compensating reopen). A crash between the statements leaves the pair with no current edge; the `ON CONFLICT DO NOTHING` branch (concurrent writer already inserted the current edge) leaves the closed row's `superseded_by` pointing at an id that was never inserted, because the reopen only runs on exception. | `person_extractor.py:874-925`; `db_pool.py` `commit()` no-op; no lock anywhere | 4 | 2 |
| G3 | **Invalidate-never-delete is violated by five writers** (PUT in place, DELETE, two merge DELETEs, pet in-place rewrite incl. endpoint swap) and the flag-off path drops the new belief silently. | 1.4 | 4 | 2 |
| G4 | **No edge provenance on `main`; even #1868's `authority`/`origin` omits the turn, the quote and the speaker.** Cannot answer "who said this, in which turn, and what did Zoe believe before". | 1.5; **[branch]** 0037 | 4 | 3 |
| G5 | **One timeline only, capture time as event time, text timestamps in two formats.** `valid_from` = when Zoe heard it, so "since 2019" / "until March" cannot be represented; `TEXT` compares lexicographically and `'2026-10-05 12:00'` sorts before `'2026-10-05T01:00'` (space < `T`), so any range predicate over mixed rows is wrong within a day. | 1.2; `routers/people.py:1062` writes `NOW()`; **[live]** 0 of 3 edges currently in the bad format | 4 | 3 |
| G6 | **Name resolution is substring + first-row** (wrong person, nondeterministic, un-indexable, unescaped). Every edge and fact write goes through it, so it is the highest-leverage correctness fix for the graph's *content* even though it is not temporal. | 1.6 | 4 | 2 |
| G7 | **Edge cardinality is "one current edge per ordered pair", not "one current edge per pair per relation kind"**: (i) a second true relation (colleague and friend) is read as a *change* and closes the first (flag on) or is dropped (flag off); (ii) symmetric types are stored directionally, so `(A,B,"sibling")` and `(B,A,"sibling")` can both be current; (iii) "ex" exists as a `rel_type` *and* closing an edge exists, two encodings of "it ended". Zoe's same-pair scope is, however, **safer** than Graphiti's whole-graph candidate scan (cannot retire an unrelated fact). | 1.4; `routers/people.py:54-84`; `person_extractor.py:712`, `:901` | 3 | 3 |
| G8 | **Memory-row validity is an optional flag, epoch floats in Chroma, no null check available** - the audit's P2.1 plus the Chroma limitation (2.4), which changes the shape of the fix (Python-side interval filter or a Postgres validity ledger, not a `where`). | `memory_service.py:1986`; 2.4 | 4 | 3 |
| G9 | **No DB-level temporal integrity.** Overlap-freedom is enforced only by a partial unique index on `valid_to IS NULL`, so two *historical* rows for a pair can overlap or leave gaps and nothing notices; merge and REST can write them. | 1.1 | 3 | 3 |
| G10 | **Server-side limits absent**: `statement_timeout=0`, `idle_in_transaction_session_timeout=0` **[live]** with the single-process writer holding autocommit connections; the 2026-07-12 pool-leak outage was found only by the client acquire timeout. | `db_pool.py:23-30`, **[live]** | 3 | 1 |
| G11 | **Indexes missing for real queries** (`people(user_id, deleted)`, edge `(user_id, person_b_id)`, history `(user_id, person_a_id, valid_from)`). Not a today problem at 32/3 rows; a growth cliff for the CTE. | 1.8 | 2 | 1 |
| G12 | **Migration hygiene**: 0006/0007 edit data inside schema revisions; lossy downgrades; 0037's SQLite downgrade is a silent no-op; no `target_metadata`. | 1.7 | 2 | 2 |
| G13 | **No communities / no multi-hop over history**; bounded CTE exists for current edges only. Graphiti's community layer is an optional summarisation, not a correctness property - rank last. | 2.1 | 2 | 4 |

---

## 4. Recommendation: the target model

Design rules, in force order: (1) a belief is **closed**, never overwritten or deleted, except through the one
audited hard-delete path the audit's P2.2 already specifies (forget-for-good); (2) every belief row carries the
**two timelines** and a **provenance triple**; (3) reads take an explicit `as_of`; (4) the database, not the writer,
enforces non-overlap; (5) every change is flag-dark first, with a migration that is schema-only.

### 4.1 Target edge schema (Postgres, additive, then flipped)

```
person_relationships  (existing columns kept; added, all nullable until backfilled)
  valid_during   tstzrange          -- event timeline [valid_from, valid_to), '-infinity'/'infinity' allowed
  recorded_at    timestamptz        -- transaction timeline: when Zoe learned it   (= created_at, parsed)
  expired_at     timestamptz        -- transaction timeline: when Zoe stopped believing it (NULL = still believed)
  authority      text               -- #1868 (already planned): user_stated | user_confirmed | inferred | operator
  origin         text               -- #1868: writer id
  asserted_by    text               -- the speaker's account id (identity from the account, per #1866)
  closed_by_id   text               -- edge id or person id/actor that closed it
  close_reason   text               -- superseded | user_removed | merged | forgotten | corrected | pet_reclass
  reference_time timestamptz        -- Graphiti's reference_time: the turn time used to resolve "last year"
person_edge_evidence (edge_id FK, chat_message_id, quote_sha256, role)   -- Graphiti "episodes", as a join table
```

Why a join table not a JSONB array: it gives a foreign key, an index in both directions ("which edges came from
this turn" is the question the forget cascade needs), and Postgres can enforce it; JSONB wins only when the
cardinality is unbounded and the content is never joined, neither of which holds. Why `tstzrange` plus separate
`valid_from/valid_to`-compatible views: the range gives the exclusion constraint (below) and `@>` point queries;
keep `valid_from`/`valid_to` as generated or dual-written columns during the transition so the existing readers
(`relationship_graph.py`, `zoe_memory_compose.py`, `person_merge.py`, tests) do not change in the same PR.

Integrity (replaces trust in the writer):

```
CREATE EXTENSION IF NOT EXISTS btree_gist;          -- trusted extension; verify the DB role may create it  [unverified]
ALTER TABLE person_relationships ADD CONSTRAINT person_rel_no_overlap
  EXCLUDE USING gist (user_id WITH =, person_a_id WITH =, person_b_id WITH =, rel_type WITH =,
                      valid_during WITH &&) WHERE (expired_at IS NULL);
```

This is the documented pattern (2.5) and also fixes G7(i): cardinality becomes "one current edge per pair **per
type**", with an `exclusive` flag per `rel_type` in `RELATIONSHIP_TYPES` (spouse/partner/ex/boss are exclusive per
pair; friend/colleague are not). Canonicalise symmetric types at write time (store `min(id),max(id)`) and let the
constraint see one row. On PG18, replace the `EXCLUDE` with `WITHOUT OVERLAPS` after the upgrade.

### 4.2 Memory rows

Option A (recommended first, effort M): make `valid_from` unconditional and add the missing half - exactly the
audit's P2.1 - **plus** `learned_at` (= `added_ts`, already stored), `valid_until` (event-time end, optional),
`expired_at` (transaction-time end; equals `invalid_at` today) and `reference_time`. `as_of(T)` for memory rows =
owner-filtered store query with `valid_from $lte T` (numeric, supported), then the open-interval test in Python
(`invalid_at` absent or `> T`), because Chroma has no null check (2.4). Keep every superseded row; the read API
returns a `history` view instead of hiding it.

Option B (larger, only if A's Python-side filter proves too slow past ~10k rows): a Postgres `memory_fact_validity
(mem_id, user_id, valid_during, recorded_at, expired_at, authority, supersedes_id)` ledger with Chroma holding only
text and embeddings. It gives the same exclusion-constraint and index story as the edges and is the natural home of
the audit's "forgotten" ledger. Do not start here.

### 4.3 The `as_of` read API

```
GET /api/people/{id}/relationships?as_of=<ISO-8601-with-tz>&known_at=<ISO-8601-with-tz>&include=history
GET /api/memories/search?q=...&as_of=...&known_at=...&include=history
```

Semantics (same names as Zep's four fields so a future graph backend or an eval harness maps 1:1):
`as_of` = world-time instant; default now, equivalent to today's `valid_to IS NULL`;
`known_at` = transaction-time instant, default now ("what did Zoe believe on 1 March?" - the dispute/forget
audit tool); `include=history` returns the closed rows with `valid_from, valid_to, close_reason, authority,
evidence[]`. SQL (edges):
`valid_during @> $as_of::timestamptz AND recorded_at <= $known_at AND (expired_at IS NULL OR expired_at > $known_at)`.
Brain-facing: a recall bullet for a changed fact reads "(until March 2026; before that)" so the model is told it
**changed**, the audit's 3.6 complaint.

### 4.4 Writers

* One `close_and_insert(conn, user_id, a, b, rel_type, ...)` helper: `async with conn.transaction():`
  `pg_advisory_xact_lock(hashtextextended($user||$a||$b, 0))`, then a single statement
  `WITH closed AS (UPDATE ... SET valid_during = ..., expired_at = now(), closed_by_id = $new, close_reason = $r
  WHERE <current row> RETURNING id) INSERT ... ON CONFLICT DO NOTHING RETURNING id`, raising (rolling back) when the
  insert yields no row after a close. Replaces `_supersede_edge`/`_reopen_edge`/the autocommit pair (G2) and gives
  `person_merge` the same lock.
* `PUT`/`DELETE` on edges and the pet correction become `close_and_insert(..., close_reason=...)`; REST `DELETE`
  becomes `close_reason='user_removed'` (reversible, auditable). The person merge replaces its two `DELETE`s with
  `close_reason='merged'` and keeps the colliding row's `valid_from` (the earliest wins).
* Hard delete exists only in the P2.2 forget path, which writes a text-free deletion record.
* `_resolve_person_uuid` becomes: exact match on a normalised name, then alias exact match, then (only if exactly
  one row) a word-boundary prefix match; ambiguity returns `None` and the caller makes a stub or asks. Add
  `people(user_id, lower(name)) WHERE deleted = 0`.
* Timestamps: new columns are `timestamptz`; the writer passes timezone-aware values; the `routers/people.py`
  `NOW()`-into-`TEXT` writes are replaced.
* Server-side limits: pass `server_settings={'statement_timeout': '15000', 'idle_in_transaction_session_timeout':
  '30000'}` on the pool (not in `postgresql.conf`, per the manual); set `lock_timeout` inside the supersede
  transaction only.
* Event time: the extractor receives the turn's `reference_time` and the two cheap deterministic cases
  ("since <year>", "until <month>", "last <season>") populate `valid_from`/`valid_to`; everything else stays
  "capture time" with `valid_from_precision = 'learned'` so a reader can tell inferred-instant from stated-instant.
  (The 270M-4B local models are not the extractor of record, per section 2.1 - this stays regex-first.)

### 4.5 Migration path from today's data

Counts **[live]**: 3 edges (all current, all `valid_from IS NULL`), 32 people, 3 activities, 103 palace rows per the
audit - **the data volume is trivial, so the risk is process, not scale.**

1. Alembic **schema-only** revision `0038` (after #1868's `0037`): add the new nullable columns, `btree_gist`, the
   new indexes. No `UPDATE`, no constraint yet. Idempotent `IF NOT EXISTS`; downgrade drops the added columns only
   and states what it loses.
2. Separate **data script** (not an alembic revision, per the cookbook), dry-run by default like
   the dry-run-by-default operator-tool pattern of #1870 (owner-memory restore): parse `created_at`/`valid_from` text into `timestamptz`
   (treating both ISO-Z and `NOW()` formats), set `valid_from = created_at` where NULL (the 3 live edges),
   `recorded_at = created_at`, `valid_during = tstzrange(valid_from, valid_to)`; stamp pre-existing edges
   `authority = 'user_stated'` per #1868's own "unstamped = the user's" rule. Run it, then verify with a count query.
3. Dual-write for one release: writers fill old and new columns; readers still use `valid_to IS NULL`.
4. Add the exclusion constraint **after** the backfill proves zero overlaps (a `SELECT` that would violate it is the
   preflight; with 3 rows it is a no-op, with any more it is the guard).
5. Flip readers to `as_of` behind `ZOE_AS_OF_READS` (dark), then default; then drop reliance on the TEXT columns.
6. Memory rows (Option A): the stamp is a pure addition (audit P2.1); backfill `valid_from = added_ts` for the rows
   that lack it (the audit measured 12 of 82 approved rows with the field) with a dry-run operator script, never in a
   migration.

### 4.6 Ranked work items, with the test that proves each and its negative control

| # | Item | I x E | Test that proves it | Negative control (must go red) |
|---|---|---|---|---|
| R1 | `as_of` / `known_at` / `include=history` on edges and memory rows; default list endpoint filters to current | 5 x 2 | Seed a pair with spouse (2019-2024) then ex (2024-); `as_of=2022` returns spouse, `as_of=now` returns ex, `known_at` before the correction returns spouse as current | Remove the temporal predicate: `as_of=2022` returns ex, test fails. Also assert `GET .../relationships` with a closed edge returns one row, not two (fails on today's `routers/people.py:894`) |
| R2 | Atomic `close_and_insert` + advisory lock + roll back on no-insert | 4 x 2 | Inject a failure after the close statement: pair still has exactly one current edge, no dangling `superseded_by`; concurrent two-writer test ends with one current edge and a consistent chain | Revert to autocommit pair: the injected-failure test leaves 0 current edges |
| R3 | No hard delete / no in-place overwrite for edges: PUT, DELETE, merge, pet correction close with `close_reason` | 4 x 2 | After each route, the old row exists with `valid_to` set and `close_reason`; row count never decreases; `grep` test that no `DELETE FROM person_relationships` remains outside the forget module | Reintroduce one `DELETE`: the count/grep test fails |
| R4 | Provenance on edges: `authority`, `origin`, `asserted_by`, `reference_time`, `person_edge_evidence(chat_message_id, quote_sha256)` | 4 x 3 | Write an edge from a voice turn: row carries speaker account, turn id, and a quote hash that matches the transcript; a model-class write cannot close a `user_stated` edge (extends #1868's `_edge_may_change`) | Drop the evidence insert: the "which edges came from this turn" query returns nothing and the forget-cascade test fails |
| R5 | Resolver rewrite (exact, alias, unique-prefix; ambiguity returns None) + `people(user_id, lower(name))` index | 4 x 2 | Seed "Ann Reyes" and "Joanna Reyes": resolving "Ann" returns Ann Reyes only; seed "Mika" and "Mikaela": "Mika" resolves to Mika; ambiguous "Ann" with two Anns returns None and creates a stub, deterministically across 50 runs | Restore `LIKE '%name%'`: "Ann" resolves to Joanna and the determinism loop shows two answers |
| R6 | Timeline split + `timestamptz` + exclusion constraint (`EXCLUDE ... valid_during WITH &&`), symmetric canonicalisation, per-type `exclusive` | 4 x 3 | Inserting an overlapping current or historical row for the same (pair, type) fails with the exclusion error; `(A,B,sibling)` then `(B,A,sibling)` yields one edge; colleague + friend for one pair both stay current; spouse then ex closes spouse | Remove the constraint: the overlap insert succeeds and the test fails; flip `exclusive` off for spouse: spouse+ex both current and the test fails |
| R7 | Memory rows: unconditional `valid_from`, `learned_at`/`valid_until`/`expired_at`, `history` view (audit P2.1) with the Python-side open-interval filter | 4 x 3 | Store "I live in Rosevale" (t1), then "I moved to Pinecrest" (t2): `as_of=between` returns Rosevale, `as_of=now` Pinecrest, `include=history` returns both ordered | Make `invalid_at` unwritten: `as_of=now` returns both and fails |
| R8 | Server-side `statement_timeout` / `idle_in_transaction_session_timeout` via pool `server_settings`; `lock_timeout` in the supersede transaction | 3 x 1 | `SHOW statement_timeout` on a pooled connection returns the configured value; a `pg_sleep` longer than the limit is cancelled | Remove the setting: `SHOW` returns 0 and the sleep completes |
| R9 | Indexes: edge `(user_id, person_b_id) WHERE valid_to IS NULL`, history `(user_id, person_a_id, valid_from DESC)`, `people(user_id, deleted)` | 2 x 1 | `EXPLAIN` over a 10k-edge fixture uses the new indexes (assert on plan node, not on timing) | Drop the index: the plan reverts to `Seq Scan` and the assertion fails |
| R10 | Migration hygiene: new revisions schema-only with an `-x data=true`-style or separate dry-run script; downgrade parity test on Postgres and SQLite | 2 x 2 | CI test: every new revision's `upgrade()` contains no `UPDATE`/`DELETE`; upgrade-downgrade-upgrade leaves the same columns on both dialects | Add an `UPDATE` to a revision: the lint test fails; make 0037's SQLite downgrade a no-op (as it is today) and the parity test fails |
| R11 | Event-time extraction for the two deterministic shapes ("since 2019", "until March") with `valid_from_precision` | 3 x 3 | "Dana and I were married from 2015 until 2021" yields `valid_during=[2015,2021)`; a bare "we got divorced" yields capture-time with `precision='learned'` | Strip the extractor: the 2015-2021 case degrades to capture time and fails |
| R12 | Communities / multi-hop over history | 2 x 4 | defer; revisit when the CTE traversal is measured slow at real size | n/a |

Sequence: R6's schema and R3 first would be heavy; the order that minimises risk and unlocks the most is
**R8, R9 (hours) -> R5 (the resolver, content correctness) -> R2 and R3 (one PR, the writers) -> R4 (rebases on
#1868) -> R1 (the as_of read, now meaningful) -> R6 (constraint + canonicalisation, once the data is clean) -> R7
(rows) -> R10 throughout -> R11**. R1 can ship before R6 in a thin form (predicate over today's text columns,
parsing with a documented fallback) but only after the mixed-format check in G5 returns zero offenders **[live]**
(it does today: 3 of 3 edges ISO-Z).

### 4.7 What would make Zoe's temporal memory best in the field

The published systems each have half of it. Graphiti has both timelines, structural provenance and invalidate-not-
delete, but newest wins with no authority and a whole-graph candidate scan. mem0 v3 dropped dated edges. Letta has
the `read_only` flag and no history. Zoe's edge candidate scope is already tighter than Graphiti's (same pair, not
semantic neighbourhood), #1868 adds the authority axis nobody publishes, and the data volume is small enough to
do the migration properly. What remains to be *best*, in order: **both timelines on every belief with
database-enforced non-overlap; a provenance triple (speaker, turn, quote) on every edge and fact; an `as_of` and
`known_at` read that is actually exposed to the brain so Zoe can say that something changed and when she learned
it; and a closed-not-deleted rule with one audited exception.** None of those four is in Graphiti's open-source
core as a guarantee, and together with the authority gate they are the measurable differentiator against the
LongMemEval knowledge-update and temporal categories the audit already proposes as scenario shapes.

---

## 5. Caveats and open questions

* **[unverified]** `btree_gist` can be created by the `zoe` role (it is marked trusted from PG13 on; confirm with
  `SELECT * FROM pg_available_extensions WHERE name='btree_gist'` and a dry run on a throwaway DB, not live).
* **[unverified]** the exact PostgreSQL version that introduced `WITHOUT OVERLAPS`; the fetched page is the PG18
  manual and Zoe is on 17.10 **[live]**.
* **[unverified]** asyncpg's `server_settings` pool argument was not re-fetched this session.
* Graphiti LoCoMo/LongMemEval numbers are deliberately not repeated here; the audit's section 5 already flags that
  they are disputed and self-reported.
* `psycopg` (v3) is not used by Zoe: alembic runs on `psycopg2`, runtime on `asyncpg` **[src]**
  (`alembic/env.py:13`, `db_pool.py:174`). The transaction guidance above is therefore asyncpg-only; psycopg2 sees
  only DDL.
* `pg_stat_user_tables.n_live_tup` reads 2 for `people` while `count(*)` is 32 **[live]** - stats are stale
  (autovacuum has not analysed it); do not read the planner stats on this table as authoritative.
* The 0037 SQLite-downgrade point and the `_stamp_edge` second statement are read from the unmerged branch
  `feat/memory-authority`; both could change before it merges.

## 6. Sources fetched 2026-10-05

https://arxiv.org/abs/2501.13956 - https://arxiv.org/html/2501.13956 - https://github.com/getzep/graphiti -
https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/edges.py -
https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/utils/maintenance/edge_operations.py -
https://help.getzep.com/facts - https://help.getzep.com/searching-the-graph (the `.../core-concepts/temporal-awareness`
URL returned a 404 page) - https://docs.mem0.ai/open-source/features/graph-memory -
https://docs.letta.com/guides/agents/memory-blocks -
https://docs.trychroma.com/docs/querying-collections/metadata-filtering -
https://www.postgresql.org/docs/current/rangetypes.html - https://www.postgresql.org/docs/18/sql-createtable.html -
https://www.postgresql.org/docs/current/ddl-constraints.html (no temporal section in the excerpt) -
https://www.postgresql.org/docs/current/explicit-locking.html - https://www.postgresql.org/docs/current/sql-insert.html -
https://www.postgresql.org/docs/current/indexes-partial.html - https://www.postgresql.org/docs/current/runtime-config-client.html -
https://magicstack.github.io/asyncpg/current/api/index.html (+ #transactions) -
https://alembic.sqlalchemy.org/en/latest/cookbook.html - https://alembic.sqlalchemy.org/en/latest/naming.html
