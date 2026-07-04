# ADR: Relationship / Graph Memory — relational property graph on Postgres, not a graph DB

## Status

Accepted. Supersedes the "graph backend TBD" placeholder left by
[`ADR-graphiti-bakeoff.md`](ADR-graphiti-bakeoff.md) (Graphiti not adopted) and
records the design that is **already live**, plus the roadmap to Samantha-grade.

## Context

Relationships — who relates to whom, how, and how that changes over time — are the
difference between memory that *recalls facts* and memory that *understands a life*.
This was the original motivation for evaluating **Graphiti** (temporal relationship
truth: entities, typed edges, validity windows, provenance, multi-hop retrieval).

The Hindsight/Graphiti bake-off **concluded and was retired-by-removal on 2026-07-03**
(only stale `.pyc` remain; the measured record lives in `ADR-graphiti-bakeoff.md` and
`docs/architecture/zoe-graphiti-fixtures.md`). Verdict: a temporary FalkorDB sidecar was
reachable, but Zoe's runtime lacks the Graphiti backend packages, Graphiti's library
extractor **failed the local structured-output smoke test**, and — the binding
constraint — a **16 GB Orin NX with ~1.8 GB free after Gemma (~4.9 GB)** cannot host
Neo4j/FalkorDB without breaking the north-star principle (local / private / fast). The
Samantha build plan (`docs/architecture/zoe-memory-samantha-buildplan.md`) therefore
mandates **no heavy graph DB**; derive relationship value from the Postgres + Chroma
already running.

## Decision

Zoe's relationship memory **is a relational property graph in Postgres**, mirrored into
Chroma for semantic recall. No graph database, no new backend service.

**Live today (verified):**
- **Nodes** — `people` (migration `0007_person_relationships.py` adds `is_partial`,
  `how_we_met`, `first_met_date`, `introduced_by_person_id` self-FK).
- **Edges** — `person_relationships`: `person_a_id`/`person_b_id` (FK→`people`, cascade),
  `rel_type`, **`rel_a_to_b` / `rel_b_to_a`** (inverse-role labels), `rel_group`,
  `created_at`/`updated_at`, `UNIQUE(user_id, person_a_id, person_b_id)`.
- **Satellite facts** — `person_important_dates`, `person_activities`,
  `person_gift_ideas`, `person_bucket_list`.
- **Write / extraction** — `person_extractor.process_text` (regex) + `person_extractor_llm`
  (Gemma) run on **every chat and voice turn** (`routers/chat.py:1215`,
  `routers/voice_tts.py:2211`). `_write_relationship` (`person_extractor.py:319`) upserts a
  typed edge, **auto-creates partial `people` stubs** for unknown names, resolves inverse
  labels from `routers.people.RELATIONSHIP_TYPES`, dedups via `ON CONFLICT`, and mirrors a
  fact into MemPalace/Chroma.
- **Read** — `zoe_memory_compose.compose_relational_block` (`zoe_memory_compose.py:184`)
  folds a person's **1-hop** edges + dates + portrait into the `/api/memories/for-prompt`
  packet, each line cited (`[relationship]`/`[date]`/`[portrait]`), router-gated by a
  zero-LLM `needs_relational` classifier. **Enabled in prod 2026-07-03**
  (`ZOE_MEMORY_COMPOSE_ENABLED=1`).

**Explicitly NOT doing:** adding Graphiti/Neo4j/FalkorDB, or a second relationship store.
The relational graph gets typed edges, inverse roles, dated facts, and 1-hop recall at
**zero extra footprint**.

### Corollary: the `people_relate` intent is redundant, not a missing backend

An earlier tech-debt note called `people_relate` a "no storage backend / needs schema
design" gap. That was wrong — it inspected the dead *intent* path (an mcporter command
with no fulfillment) and missed the live extraction path beside it. The backend (edge
table + `_write_relationship` + live extraction) already exists. **Fix = remove
`people_relate` from the advertised brain surface, or make it a thin alias to
`_write_relationship`. No new schema.**

## Consequences / roadmap to Samantha-grade

The design is correct for the constraints; making it *understand the evolving thread*
is four bounded increments on top of what's live (none require a graph DB):

1. **Retire/alias `people_relate`** — remove the redundant broken intent (small).
2. **Temporal edges** — add `valid_from`/`valid_to`/`superseded_by` to
   `person_relationships` so relationships change over time without losing history
   ("was married, now divorced"). This is the piece Graphiti promised, done relationally —
   highest Samantha value.
3. **Bounded multi-hop traversal** — a **Postgres recursive CTE** over the user's edges,
   exposed as a `relationship_query` capability ("who are Tom's siblings", "everyone in my
   work circle"). Real graph queries, still no graph DB; keep it explicit-query/async, off
   the chat hot path (per the bake-off guidance).
4. **Precision + identity** — route relationship writes through the existing admission gate
   (`zoe_memory_admission.py`) with a confidence threshold so low-confidence edges become
   *pending/confirmable* rather than silent facts (4B LLM extraction is noisy); and add a
   **person-merge / entity-resolution** path for `is_partial` stubs (name collisions, stub
   → real-contact promotion).

## Acceptance criteria (for future increments)

- Multi-hop relationship answers (siblings, circles, "how is X connected to Y") without a
  graph DB, within the relational latency budget; off the chat hot path.
- Superseded relationships return the *current* truth while retaining history.
- Relationship writes are admission-gated + confidence-scored; no silent wrong edges.
- Partial stubs merge cleanly into real contacts without duplicate nodes/edges.
- Every increment flag-gated, byte-for-byte no-op when off, lab-proven before prod (per the
  Samantha build plan guardrails).
