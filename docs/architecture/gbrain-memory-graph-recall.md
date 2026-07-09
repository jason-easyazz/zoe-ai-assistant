---
type: architecture-design
status: 💡 design (not started — RAM/replay-gated)
source: https://github.com/garrytan/gbrain
date: 2026-07-09
owner: memory / Samantha-evolution W-memory
---

# Wiring the relationship graph into recall (the GBrain lesson)

**One line:** Zoe already implements most of GBrain's memory playbook and mostly
live; the single genuine gap is using the `people_relate` graph as a recall
signal — and a code audit shows the real blocker under it is an **inconsistent
fact→person linkage**, not the boost itself.

This is a design, not a change. Nothing here ships until the RAM /
replay-gate that guards `ZOE_RELATIONSHIP_GRAPH_ENABLED` clears (see
[relationship-memory-flag-enable](../knowledge/relationship-memory-flag-enable.md)).
Grounded in a 2026-07-09 read of the live code; every claim carries a `file:line`.

---

## 1. Audit verdict: Zoe is peer-or-ahead of GBrain on memory

GBrain (Garry Tan) is a **knowledge/memory layer**, not a voice system — its
voice integration is a thin Twilio + OpenAI Realtime recipe, so it has nothing
to teach Zoe's local voice stack. Its value is its retrieval design. Auditing
each GBrain idea against Zoe's actual code:

| GBrain technique | Zoe's reality (file:line) | Verdict |
|---|---|---|
| Hybrid search (vector + BM25 + fusion) | `memory_service._semantic_search._blend` fuses **6 additive signals** — semantic distance, keyword overlap, salience, confidence, decay, recency — flag `ZOE_HYBRID_RETRIEVAL_ENABLED=1` (`memory_service.py:1223-1265`) | ✅ live |
| Salience scoring | `hotness = 0.05·log1p(access_count)`; access ticked on every recall (`memory_service.py:1241`, `:1402 tick_access`) | ✅ live |
| Time-decay / freshness | 70-day half-life `exp(-λ·age)` (`memory_service.py:1214,1240`) | ✅ live |
| "Dream cycle" idle enrichment | `MEMORY_DIGEST_ENABLED` + `memory_digest.py` | ✅ live (partial — see §6) |
| Recall injection into the prompt | `ZOE_SEAM_RECALL_INJECT=true` (`zoe_flue_client._recall_context_block`) | ✅ live |
| Local-first / private | Whole stack local; GBrain **defaults to hosted embeddings (ZeroEntropy)** | ✅ Zoe ahead (a rock) |
| **Knowledge-graph adjacency in retrieval** | Graph exists (`relationship_graph.neighbors`, `GET /people/{id}/graph`, [`ADR-relationship-memory`](../adr/ADR-relationship-memory.md)) but **`_semantic_search` never calls it** | ❌ **the gap** |

GBrain's headline benchmark is **+31.4 P@5 from its graph over vector-only RAG**
— which is precisely the one signal Zoe has built and does not use. That number
is the justification for prioritizing this increment.

---

## 2. The deeper finding: the fact→person linkage is inconsistent

Graph-adjacency can only boost a fact if we know *which person the fact is
about*, keyed by the graph's node identity (`people.id`). Facts carry
`entity_type` + `entity_id` metadata (`memory_service.py:318-320`), but the
**producers disagree on what `entity_id` holds**:

- `routers/people.py:147` — `entity_type="person"`, `entity_id=people.id` ✅ graph-usable
- `person_extractor.py:300-301` — resolved → `entity_type="person"`, `entity_id=<people.id>`; unresolved → `entity_type="person_pending"`, `entity_id="slug:<name>"` ✅ **honest marker**
- `memory_extractor.py:373-374,451-452` — `entity_type="person"`, `entity_id="<name-slug>"` ❌ **mislabelled**: claims "person" but the id is a slug, not a `people.id`

Both extractors run in the live memory pass (`routers/voice_tts.py:2282,2284`).
So the store holds a mix of graph-linked (`people.id`), honestly-pending
(`slug:` + `person_pending`), and **silently-broken** (`person` + bare slug)
facts. A graph boost that trusts `entity_type=="person"` would match on
un-linkable slugs and quietly do nothing for a chunk of the corpus.

**Two primitives already exist that make the fix small:**
- `person_extractor._resolve_person_uuid(name, user_id)` — name → `people.id`
  resolver (query-side and backfill-side).
- `contact_backfill.py` — turns known-from-memory people into `people` rows, but
  is **API-triggered only** (`routers/memories.py:660`), creates *proposals*, and
  does **not** rewrite memory `entity_id`s. It is not an idle loop.

**Live-ratio measurement (do first, one-off):** count store facts by
`entity_type` and whether `entity_id` is a UUID vs `slug:` vs bare slug. (The
direct Chroma read is owner-locked to the `zoe-data` process — run it in-process
via a one-shot admin route or a `get_memory_service()` script, not an external
client.) This sizes the backfill and sets the eval's expected ceiling.

---

## 3. Design — three parts

### Part A — Linkage hygiene (prerequisite; RAM-free; landable NOW)
1. **Stop the mislabelling.** `memory_extractor.py:373-374,451-452` must mark
   unresolved person-facts `entity_type="person_pending"`, `entity_id="slug:…"`
   — identical to `person_extractor`. Attempt `_resolve_person_uuid` first; only
   fall back to the slug. Pure producer consistency, no graph, no model.
2. **Idle resolver (GBrain "entity resolution" + "dedup people").** A
   `memory_digest` pass that scans `person_pending` facts, runs
   `_resolve_person_uuid` (+ person-merge #1036), and **rewrites `entity_id` to
   the `people.id`** once the person exists, flipping `person_pending`→`person`.
   This is what completes the graph linkage over time. Idle-time ⇒ zero turn
   latency. Reuses the existing digest scheduler.

Part A needs **no** graph traversal and touches **no** RAM budget, so it can
land and bake while the graph flag stays gated.

### Part B — Graph-adjacency boost (the feature; gated)
In `_semantic_search` (`memory_service.py:1176`), when the turn resolves to a
start person:
1. Resolve query → `start_pid` (via `_resolve_person_uuid` on the turn's
   extracted name, or the already-resolved turn entity — no new NLU).
2. `neighbors(db, user_id, start_pid, max_depth=2, limit=32)`
   (`relationship_graph.py:145`) → `[{person_id, depth, …}]`. One bounded
   recursive-CTE BFS, current-edges-only.
3. Build `depth_by_pid = {start_pid:0, **neighbors}`.
4. Add a **7th blend term** (`memory_service.py:1265`):

   ```
   graph = W_graph · 1/(1 + depth)        if md["entity_id"] ∈ depth_by_pid  (else 0)
   score = base + keyword + recency + preference + graph
   ```

   depth 0 = the person themself, 1 = a direct relation, 2 = friend-of. This is
   what surfaces *"my sister's husband's job"* when the fact is stored under the
   husband (depth-2) and vector search alone misses it — GBrain's multi-hop win.

Gated behind the existing `ZOE_RELATIONSHIP_GRAPH_ENABLED` **and** a new
`ZOE_GRAPH_RECALL_BOOST` sub-flag (default OFF), so the graph endpoint and the
recall boost flip independently. OFF must be byte-identical to today (the term
is simply not added), matching the module's existing flag idiom
(`memory_service.py:1220,132`).

### Part C — The benchmark (the thing that earns the flag flip)
GBrain's +31 P@5 is on **multi-hop** queries; Zoe's existing 40/40 memory eval
is single-hop, so it cannot show this win. Build a **multi-hop recall eval**:
- Fixture: a demo user with a seeded people-graph (sister→husband→employer) and
  facts stored under the *connected* entities.
- Queries whose answer lives 1–2 hops from the named person.
- Metric: **P@5 / R@5**, graph-boost ON vs OFF, plus a no-regression check on
  the single-hop set (the boost must not demote direct hits).
- Reuse the existing eval harness / saved-voice replay rig; demo users only.

---

## 4. RAM & latency budget (why this is the *right* increment post-crisis)

- **New models: none.** No embeddings change, **no reranker** (see §8).
- **Per-turn cost:** one bounded BFS SQL query on person-question turns only
  (`max_depth=2`, `limit=32`) + O(hits) dict lookups in `_blend`. ~2-5 ms.
- **Steady-state RAM: ~zero** — recursive CTE runs in Postgres; the neighbour
  set is a small dict.

Contrast the 2026-07-08 incident: the box thrashed to 25 GB swap under
concurrent agents. A resident reranker model (GBrain's Qwen3-Reranker option)
would compete with the 5.3 GB brain for RAM and re-create exactly that failure.
This design deliberately buys GBrain's biggest win (the graph) at no RAM cost.

---

## 5. Sequencing & gates

1. **Part A now** — linkage hygiene + idle resolver. No graph, no RAM, no gate.
   Ships flag-safe; bakes the corpus toward complete `people.id` linkage.
2. **Part C next** — the multi-hop eval harness (demo users). No prod effect.
3. **Part B last** — the boost, lab-proven on the eval, `ZOE_GRAPH_RECALL_BOOST`
   enabled **only after** the memory-pressure / RAM-reclamation workstream (W0)
   clears the replay gate on `ZOE_RELATIONSHIP_GRAPH_ENABLED`. Never before.

Each part is its own small PR through the normal Greptile loop, flag-OFF,
lab-proven before prod (the rock).

---

## 6. Increment 2 — proactive contradiction detection (also GBrain)

Zoe orders conflicting recall facts newest-first (#1124) but never *detects*
contradictions. GBrain runs `eval suspected-contradictions` (LLM judge +
persistent cache) inside its dream cycle. Zoe's home is `memory_digest`: sample
same-subject/same-predicate fact pairs, judge with the existing
`MEMORY_DIGEST_MODEL` (gpt-4o-mini) or the idle local brain, and flag/supersede
conflicts. Idle-time ⇒ no turn latency. Separate, later increment.

---

## 7. Explicitly rejected from GBrain

- **Hosted embeddings** (ZeroEntropy default) — breaks the local/private rock.
  Use only GBrain's local paths as prior art.
- **Resident local reranker** (Qwen3-Reranker) — breaks the RAM budget; the
  2026-07-08 swap incident is the standing evidence. Take RRF's *idea* (already
  present as additive fusion in `_blend`), not another resident model.
- **Markdown-as-source-of-truth, DB-as-index** — philosophically aligned with
  Zoe's DOX/OKF, but a large migration (memory is Postgres-truth today). Note as
  a long-horizon consideration, not this increment.

---

## 8. NEXT ACTION

Land **Part A** (linkage hygiene: fix `memory_extractor` mislabelling + add the
idle `person_pending` resolver to `memory_digest`) as the first, RAM-free step —
it is independently valuable (cleaner entity linkage improves *today's*
recall) and is the prerequisite the graph boost stands on. Everything graph-side
waits on the W0 RAM gate.
