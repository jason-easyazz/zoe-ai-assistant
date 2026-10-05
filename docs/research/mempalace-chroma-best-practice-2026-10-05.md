---
type: Research
title: "MemPalace and Chroma: is Zoe using them the way their docs and source say, and what would beat mem0 / Zep / Letta"
description: "Module-by-module inventory of mempalace 3.10.0 vs what Zoe calls or re-implements, a Chroma 1.5.9 settings/query audit with file:line, an embedder and hybrid-retrieval pilot (96 synthetic facts, 276 queries), and ranked gaps each with the measurement that proves it."
tags: [memory, mempalace, chroma, hnsw, embeddings, retrieval, hybrid-search, bm25, authority, research]
timestamp: 2026-10-05
status: read-only research; nothing committed, no PR; live palace untouched (sqlite mode=ro, aggregates and shapes only, no row text)
---

# MemPalace and Chroma best practice vs Zoe (2026-10-05)

Evidence labels: **[src]** repo or installed-package file:line read today; **[doc]** URL fetched today; **[measured]** I ran it today on this box; **[unverified]** not confirmed. "Pilot" numbers are synthetic and small; their caveats are in section 3.4. Names in the pilot are invented. Palace facts are counts and shapes only.

## 0. Summary

1. **Zoe does not use MemPalace as software.** `mempalace` is pinned (`requirements-py312.txt:65` `mempalace==3.10.0`; `requirements.txt:106` still says `3.3.1`) and installed, but no production module imports it. **[src]** `grep -rn "^\s*(import|from) mempalace"` over the repo returns only a test stub and an unrelated local file named `mempalace_baseline.py`; `memory_service.py:181-183` says it "replaces `mempalace.palace.get_collection`" and `requirements-py312.txt:61-63` says "zoe-data opens the store with raw chromadb, NOT mempalace's wrapper". What Zoe shares with MemPalace is a name (`mempalace_drawers`), a metadata vocabulary (`wing` = user id, `room`), and a Chroma layout. Retrieval, ranking, dedup, KG and sanitising are all Zoe's own.
2. **The live versions are not what `requirements.txt` says.** **[measured]** the live venv has `chromadb 1.5.9`, `mempalace 3.10.0`, `onnxruntime 1.23.2`, `fastembed 0.8.1`. The old pins (`chromadb==0.6.3`, `mempalace==3.3.1`) are still in `services/zoe-data/requirements.txt:106-107`.
3. **Largest cheap safety gap: HNSW threading.** MemPalace pins `hnsw:num_threads=1` on every collection open because of a parallel-insert race that segfaults on chromadb 1.5.x and earlier (**[doc]** MemPalace issue #974; **[src]** `mempalace/backends/chroma.py:1886-1913`, which also notes the setting does not persist across reopens on 1.5.x). Zoe never sets it (`get_drawers_collection`, `memory_service.py:174-206`), and has a history of HNSW crashes and a 2026-10-04 wedge.
4. **Largest ranking gap: authority is inverted in the blend.** `_blend` multiplies relevance by a per-writer constant `confidence` (`memory_service.py:2384`). **[measured]** among the owner's approved rows, LLM-only writers carry 0.8-0.9 (`digest` 0.9, `idle_consolidation` 0.8) while the user's own words carry 0.7 (`conversation`, `voice`). An LLM paraphrase outranks the user's sentence at equal distance.
5. **Latent time bomb: age decay applies to durable facts.** `semantic = (1/(1+dist)) * conf * exp(-ln2/70 * age_days)` (`memory_service.py:2337,2384`). Factor 0.41 at 91 days (today's oldest row), 0.027 at one year, so a name or allergy fades below any fresh distractor.
6. **At 284 rows HNSW is the wrong tool; exact search is faster and removes a failure class.** **[measured]** k=200 over 300 vectors: HNSW 4.0 ms vs exact numpy 0.03 ms; at 20,000 vectors: 8.2 ms vs 8.0 ms (parity). MemPalace itself ships exact backends (`sqlite_exact`, `rust_exact`) as "a correctness backend" (**[src]** `backends/sqlite_exact.py:1-8`). Zoe carries about 600 lines of tombstone, compaction and maintenance-gate machinery for the HNSW path (`memory_service.py:208-612`, `memory_index_health.py`). I could **not** reproduce the 2026-10-04 "filtered query comes back wrong" failure on a fresh chroma 1.5.9 in three scenarios (section 2.5), so that incident's mechanism is unproven.
7. **The embedder is not the bottleneck.** **[measured pilot]** vector-only hit@1: MiniLM 0.949, bge-small q8 0.964, snowflake-arctic-s 0.946, nomic-v1.5-Q 0.906, EmbeddingGemma (MemPalace's recommended one, 384-d) 0.975, with n=276 queries so the standard error is about 1.3 points. EmbeddingGemma costs 1,447 MB RSS and 204 ms/query here: not viable on this box. bge-small q8 is already loaded by the router, is 6x faster (7.5 vs 45 ms/query) and equal on recall. Switching buys latency and one fewer ONNX session, not recall.
8. **Hybrid, as shipped, may be net-negative.** **[measured pilot]** MemPalace's default convex blend (0.6 vector + 0.4 BM25) dropped MiniLM hit@1 from 0.949 to 0.884; RRF dropped it to 0.725; Zoe's live blend (my reconstruction) scored 0.928 (paraphrase 0.896 to 0.792, reverse lookups 0.952 to 1.000). A 0.9/0.1 blend was +1.1 points (noise level). Hybrid must be tuned on a household-shaped set; the repo has no such set (the existing baseline is 7 system-trivia cases scored by term overlap, `mempalace_baseline.py:31-120`).
9. **What would beat mem0 / Zep / Letta is not a better vector index.** All three put an LLM in the write path, default to cloud embeddings, and none has an authority axis. Zoe can win on: authority-ranked retrieval, verbatim evidence beside every distilled fact, invalidate-never-delete with `as_of` reads, abstention below a calibrated relevance floor, and a local exact search at single-digit milliseconds. Section 5.
10. **Not done:** I did not extract the 2026-10-04 pre-compaction tar to replay the incident (the auto-mode classifier denied it as household data). That replay is the right test for item 6 and needs the operator's go-ahead (section 4, G6).

## 1. What Zoe does today

### 1.1 Storage

- One `chromadb.PersistentClient` per palace dir, shared by drawers and audit (`memory_service.py:100-118`). **[src]**
- Drawers: collection `mempalace_drawers`, opened with a cached `ONNXMiniLM_L6_V2` subclass named `"default"` (`memory_service.py:151-170`). Created, only if absent, with `metadata={"hnsw:space": "cosine"}` (`:198-199`); the live collection is `l2` with no other HNSW setting. **[measured]** `collection_metadata` holds only `hnsw:space=l2`; `segment_metadata` is empty; `collections.config_json_str` is `'{}'` for both collections. Negative control: a throwaway 1.5.9 collection created WITH a named EF also stores `'{}'`, so that is normal, not a misuse.
- Live shape **[measured, aggregates only]**: drawers 284 rows, dim 384, document length median 50 chars, p90 69, p99 148, max 183 (none near MiniLM's 256 word-piece limit); 42 rows contain digits, 2 non-ASCII; all rows stamped `embedding_model_version=minilm-v1`. Audit collection 19,492 rows, i.e. 98.6% of every vector in the file is an audit placeholder. DB 63 MB, drawers HNSW 0.8 MB, audit HNSW 39 MB. Two orphan HNSW directories (594ef7d2…, e0998ef8…, 3.3 MB) are not referenced by `segments`.
- Audit rows are written with a constant unit-basis vector (`_AUDIT_NULL_EMBEDDING`, `memory_service.py:86`) via `get_or_create_collection(_AUDIT_COLLECTION)` (`:2148`), `before`/`after` JSON cut at 4,000 chars (`:2700-2701`).
- Compaction: delete and recreate the collection from stored embeddings, behind a maintenance gate and in-flight-op lease (`memory_service.py:208-612`); health from SQLite + pickle (`memory_index_health.py`). The rebuild recreates with `metadata={"hnsw:space": space}` only (`:452-453`).

### 1.2 Read path

- `load_for_prompt` (`:1337-1357`): `_metadata_read` does `col.get(where=scope)` with no limit (all of a user's rows, documents and metadata), then sorts in Python by `conf*exp(-λ·age) + 0.1*log1p(access_count)` (`:2160-2205`).
- `search` (`:1382-1425`) calls `_semantic_search` (`:2240-2423`): unfiltered `col.query(n_results=max(limit*20,200))`, Python visibility/status/expiry filtering, a filtered supplement (`n_results=max(limit*3,limit)`, `:2324`) only if fewer than `limit` survive, then re-rank.
- Re-rank (`:2384-2420`): `(1/(1+dist))*conf*decay + 0.05*log1p(access)` and, because `ZOE_HYBRID_RETRIEVAL_ENABLED=1` in the live `.env`, `+0.50*keyword_overlap + 0.05*recency + 0.05*type_pref`; `ZOE_GRAPH_RECALL_BOOST=1` adds a graph-adjacency term. **[src]** `_hybrid_keyword_overlap` (`:777-793`) is the fraction of query content tokens found in the text (substring or token); no IDF, no length normalisation, stopword list `_HYBRID_STOPWORDS` (`:655-662`).
- Gate: the for-prompt endpoint only runs semantic search when `message_needs_memory` says the turn looks like recall (`routers/memories.py:852-858`, `memory_gate.py:393-421`); the reason in its docstring is "would waste the embed". `search(message, limit=6)` passes the raw user message as the query. Otherwise the packet is the query-independent ranked prefix.
- Packet builder (`routers/memories.py:425-600`): drops superseded/archived/rejected/pending, token-level near-dup collapse, `[mem:id]` cites, newest-first presentation of conflicts, optional dates and the user's quoted words (`recall_evidence.py`). No relevance floor.
- `tick_access` on every read: `col.get(ids)` then `col.update(ids, metadatas=<full dict>)` (`:2523-2557`, `:2586-2601`), under a per-user asyncio lock.
- `zoe_memory_router.py` is not a retrieval router. It maps a query to a backend policy among MemPalace/Hindsight/Graphiti/Graphify/Multica by keyword (`route_memory_query`, `:127-212`) for the evolution harness; the default branch just returns MemPalace with `latency_budget_ms=300`. **[src]**

### 1.3 Zoe's own versions of MemPalace's modules

| MemPalace module (3.10.0) | What it does per source/docs | Zoe calls it? | Zoe's own equivalent | Verdict |
|---|---|---|---|---|
| `searcher/` (`ranking._hybrid_rank`, `_bm25_scores`, `sqlite_bm25`, `candidates`, `query`) | Okapi BM25 (k1 1.5, b 0.75) with candidate-set IDF, min-max normalised, convex blend 0.6/0.4 (env-tunable), optional "union" candidates from Chroma's FTS5 trigram table; metric-aware distance-to-similarity; date windows (`since`/`before`) **[src]** `ranking.py:119-363`, `sqlite_bm25.py:76-100`, `candidates.py:1-60`; **[doc]** CHANGELOG 3.4.0, 3.10.0 | No | `_semantic_search` blend + `_hybrid_keyword_overlap` | **Re-implemented, weaker** (no IDF, fixed 0.5 weight, assumes `l2` squared distance). Port BM25 + `_distance_to_similarity`; do not import the package (schema differs). |
| `query_sanitizer.py` | Trims system-prompt-contaminated queries; **[src]** docstring cites 89.8% to 1.0% R@10 without it (`query_sanitizer.py:1-27`); 200/250-char thresholds | No | none | **Missing; low impact today** (voice/chat queries are short) but a 40-line port is free insurance. Log the query-length histogram first. |
| `dedup.py` | Near-duplicate drawers by cosine distance (default 0.15, scoped per `source_file`), keeps longest/richest, dry-run default (`dedup.py:7,39,98-131`) | No | **Four** independent versions: write gate `memory_quality._NEAR_DUP_RATIO=0.92` text ratio (`:331-440`), `memory_lint` Jaccard (`:394`), packet `_is_near_duplicate` (`routers/memories.py:263-320`), idempotency key (`memory_service.py:1294`) | **Stop duplicating.** One `near_duplicate()` using the stored vector (free) plus token containment, used by all four. |
| `fact_checker.py` | Offline check of text against registry + KG: `similar_name` (1-2 edits from another registered name), `relationship_mismatch`, `stale_fact` (`fact_checker.py:1-30`) | No | `memory_supersede.py` cue table, `person_merge.py`, `recall_evidence`, verify-on-challenge | **Partly covered; `similar_name` is missing** (typo/mix-up between two household members). Port the algorithm against the Postgres `people` table. |
| `entity_registry.py` | Names to types with priority onboarding > learned > researched; ambiguous-word guard ("Riley" vs "ever") | No | Postgres `people` + `person_extractor._resolve_person_uuid` | **Equivalent store exists.** The source-priority order is the same idea as the authority matrix in the 2026-10-05 fidelity audit. Idea only. |
| `entity_detector.py` | Two-pass regex scoring of person vs project candidates | No | `person_extractor.py` (1,274 lines, regex + LLM) | **Don't adopt.** |
| `knowledge_graph.py` | SQLite temporal triples, `valid_from`/`valid_to` half-open, atomic `supersede()` (3.6.0), `query_entity(as_of=…)` (`knowledge_graph.py:375-400,496-510`) | No | Postgres `person_relationships` with `valid_from/valid_to/superseded_by` (flag on) | **Don't adopt the store; adopt two ideas:** half-open intervals with one shared boundary, and an `as_of` read on drawer rows (today reads hide superseded rows outright, `_BLOCKED_READ_STATUSES`). |
| `layers.py` | L0 identity, L1 essential, L2 on-demand, L3 deep search; ~600-900 token wake-up | No | `zoe_memory_layers.py` (124 lines) + the for-prompt packet | **Same concept, own code.** Nothing to adopt. |
| `convo_miner.py` | Chunks chats by exchange pair and files **verbatim** | No | `memory_digest.py` (2,379 lines), `memory_extractor.py`; distilled facts only | **Philosophy gap**, see G8: MemPalace stores verbatim; Zoe stores distilled rows and only 5 of 284 carry `source_excerpt`. |
| `closet_llm.py`, `palace/closets.py` | Optional LLM-built topic index layer over drawers, bring-your-own-LLM | No | none | **Not needed at 284 rows.** |
| `general_extractor.py` | 5-type heuristic extractor (decision, preference, milestone, problem, emotional) | No | `memory_extractor.py` (990 lines) | **Don't adopt.** |
| `dialect.py` (AAAK) | Compressed memory notation | No | none | **Not needed**; packets are capped at 12 bullets / 1,600 chars. |
| `exporter.py` | Palace to markdown folder | No | JSON export + tar in the compaction (`memory_service.py:566-573`), `export_user` | **Own version is fine.** |
| `hooks_cli.py` | Claude Code / Codex session-start/stop/precompact hooks | No | n/a | **Not applicable** to a household assistant. |
| `backends/chroma.py` (`_pin_hnsw_threads`, `_HNSW_WRITE_DEFAULTS`) | `num_threads=1` pinned on every open; batch 100 / sync 1000 | No | none | **Adopt** (G1). |
| `backends/sqlite_exact.py`, `rust_exact.py` | Exact cosine over float32 blobs, numpy-vectorised, matrix cached on the handle | No | none | **Adopt the idea** (G6). |

## 2. What the docs and source say, and where Zoe departs

### 2.1 Chroma (installed 1.5.9) settings

**[doc]** docs.trychroma.com/docs/collections/configure: `space` default `l2`, `ef_construction` 100, `ef_search` 100, `max_neighbors` 16, `num_threads` = CPU count, `batch_size` 100, `sync_threshold` 1000, `resize_factor` 1.2; mutable after creation: `ef_search, num_threads, batch_size, sync_threshold, resize_factor`; the embedding function and its parameters are persisted in the collection configuration. **[measured]** a new 1.5.9 collection reports `{'hnsw': {'space':'l2','ef_construction':100,'ef_search':100,'max_neighbors':16,'resize_factor':1.2,'sync_threshold':1000}}`. **[src]** `chromadb/api/collection_configuration.py:19-27,271-278` maps the legacy `hnsw:*` keys to these fields.

| # | Departure | Evidence | Verdict |
|---|---|---|---|
| C1 | `hnsw:num_threads` never set; default is CPU count (8 here) | `memory_service.py:174-206` vs MemPalace `backends/chroma.py:1886-1913`; **[doc]** MemPalace issue #974: "default multi-threaded `ParallelFor` triggers a race condition" in `repairConnectionsForUpdate`/`addPoint`, on chromadb <1.0 **and 1.5.x** | **Real gap.** On a 284-row index parallelism buys nothing. Pin 1 on every open (not persisted across reopens on 1.5.x per MemPalace). |
| C2 | Scores assume `l2` squared distance: `1/(1+dist)` (`memory_service.py:2384`); the opener creates `cosine` for a new palace (`:199`), the live one is `l2` | `docs/knowledge/chroma-1-5-migration.md:70-75` already warns "changing the space would silently rescale every score". MemPalace's `_distance_to_similarity(distance, metric)` is metric-aware (`ranking.py:201-250`) | **Fragile coupling, not a bug today** (MiniLM output is unit length, so `l2` ranks identically to cosine). Make the blend ask `_collection_space()` (`:408-418`) or store cosine similarity. |
| C3 | Unfiltered `n_results=max(limit*20,200)` (`:2315-2318`) | At 284 rows k=200 is most of the collection, so today it is effectively a full scan; at larger N the top-200 is dominated by whichever user has the most rows (prior audit §3.2) | **Stopgap, correct at this size.** G6 removes the need. |
| C4 | `col.get(where=scope)` with no `limit`, every turn, documents + metadatas (`:2160-2170`, `:2188-2205`) | **[doc]** `get` supports `limit`/`offset`; O(rows) per turn | Fine at ~100-1,000 rows; will not be fine at 10k+. Cache or page (folds into G6). |
| C5 | `where_document` never used anywhere in `services/zoe-data` **[src]** (grep) although Chroma maintains an FTS5 trigram index for every document (**[measured]** `embedding_fulltext_search` holds 19,776 rows) and the installed client accepts `$contains`, `$not_contains`, `$regex`, `$not_regex` (`chromadb/api/types.py:1298-1304`; the docs page lists only `$contains`/`$not_contains`) | MemPalace's union mode uses that index (`sqlite_bm25.py:82-90`) | **Free lexical channel left unused**; Zoe's lexical signal only sees the 200 vector candidates. |
| C6 | `col.update(ids, metadatas=<full dict>)` for access counters (`:2557,2601`) | **[measured]** on 1.5.9 `update` **merges** keys and `None` deletes a key (throwaway test), so only the changed keys need writing | Writing the whole stale snapshot back can revert a concurrent `status` change by a writer that does not take the per-user lock (direct drawer writers use `guard_collection`, `:204`) **[unverified that any such interleaving occurs]**. Write two keys, or move counters out of Chroma. |
| C7 | Audit log stored as a vector collection with a constant vector (`:86,2148,2700`) | **[measured]** 19,492 of 19,776 vectors are placeholders; inserting 4,000 identical vectors was not pathological (3.8 s vs 2.6 s for random) | **Footprint and complexity, not a bug.** An append-only SQL table is the right shape; it also removes the `[:4000]` truncation. |
| C8 | Rebuild recreates with `metadata={"hnsw:space": space}` only (`:452-453`); the 2026-09-25 recovery re-applied `resize_factor=2.0` (`docs/knowledge/state-of-zoe-review-2026-09-25.md:73`) | **[measured]** live `segment_metadata` and `collection_metadata` carry no `resize_factor` | **[unverified whether it matters on the 1.5.9 Rust path.]** The rebuild's own verify step exercises the historical crash path (`:479`), which is the right proof; keep it. |
| C9 | `Search` API (hybrid with RRF, sparse BM25) | **[doc]** docs.trychroma.com/cloud/search-api/overview: "Search API is available in Chroma Cloud only. Future support on single-node Chroma is planned." The installed 1.5.9 exposes `Collection.search` (`api/models/Collection.py:350`) | **Not available to Zoe's local store.** Build the lexical channel yourself. |
| C10 | Embedding function identity | **[doc]** persisted in collection configuration; Zoe passes the EF explicitly on every open and names it `"default"` to avoid the conflict error (`:151-170`) | **Correct workaround.** Negative control above shows the config column is `'{}'` regardless. |

### 2.2 MemPalace's stated practice (what to compare against)

- **[doc]** README (github.com/MemPalace/mempalace): verbatim storage in "drawers", scoped by wing/room; default model All-MiniLM-L6-v2 (English-only), EmbeddingGemma-300M "recommended" for multilingual; hybrid BM25 + vector; LongMemEval raw 96.6% R@5 with no LLM. Note the benchmark is over verbatim conversation chunks with the **default MiniLM**: MemPalace's own number does not depend on a better embedder.
- **[doc]** CHANGELOG: 3.3.6 EmbeddingGemma ONNX (384-d via Matryoshka), 3.7.0 HNSW write defaults synced to Chroma (100/1000), 3.8.0 `sqlite_exact` vectorised numpy, 3.10.0 hybrid weights configurable (0.6/0.4) and read-only searches stop contending with writer leases.
- **[src]** `mempalace/embedding.py:1-45` lists `minilm` (default), `embeddinggemma`, `openai-compat`; "Switching models on an existing palace requires `mempalace repair rebuild-index` (different vector space)". `docs/knowledge/chroma-1-5-migration.md:77-79` already pins `config.json` to `"embedding_model": "minilm"` so 3.10's onboarding default (`embeddinggemma`) cannot re-point the palace.
- **[src]** `backends/chroma.py:214-262`: MemPalace measured write amplification for tiny `sync_threshold`/`batch_size` (1.67x to 2.91x) and reverted to Chroma's 100/1000; Zoe uses Chroma defaults, so it already matches.

### 2.3 Embeddings

- **MiniLM-L6-v2** **[doc]** huggingface.co/sentence-transformers/all-MiniLM-L6-v2: 22.7M params, 384-d, "input text longer than 256 word pieces is truncated", English-only training data. Good: short English sentences, topical similarity. Weak: exact tokens (names, numbers, dates, IDs), negation, non-English. MTEB: overall ~56.3 **[doc: search summary]**; retrieval average 41.95 **[unverified: recalled]**.
- **bge-small-en-v1.5** **[doc]** huggingface.co/BAAI/bge-small-en-v1.5: 33.4M params, 384-d, 512 tokens, MTEB average 62.17, retrieval 51.68; the query instruction `"Represent this sentence for searching relevant passages:"` is optional in v1.5; "similarity scores typically fall between 0.6 and 1.0 ... select thresholds empirically". Same dimension as MiniLM.
- **EmbeddingGemma-300M** **[doc]** huggingface.co/google/embeddinggemma-300m: 300M params, 768-d with MRL truncation to 512/256/128, MTEB English v2 69.67 (768-d), 68.37 at 256-d, Q8 69.49; query/document prompt prefixes; Gemma terms of use.
- 2026 field (web search summary, **[secondary]**): nomic-embed v1.5 and EmbeddingGemma are the usual CPU/edge picks, Qwen3-Embedding-0.6B (int8 ONNX exists) when more compute is available; fastembed 0.8.1 in the live venv lists bge-small/base, snowflake-arctic-embed xs/s/m, nomic v1.5 (and `-Q`), gte-base, jina v2, `Qwen3-Embedding-0.6B-Q`, plus cross-encoder rerankers (`Xenova/ms-marco-MiniLM-L-6-v2` 0.08 GB, `jinaai/jina-reranker-v1-tiny-en` 0.13 GB) and sparse BM25 (`Qdrant/bm25`). **[measured]** list from `TextEmbedding.list_supported_models()` etc.
- Already on the box: `BAAI/bge-small-en-v1.5` q8 is loaded in-process by the semantic router (`semantic_router.py:118,300-302`, `ZOE_ROUTER_MODEL`) and `persona_drift.py:92-96`; the HF/fastembed caches hold it. Palace re-embedding and the router could share one session.

### 2.4 Re-embedding feasibility **[measured]**

284 drawer rows (owner ~100 approved; the rest legacy/probe/guest ids), documents median 50 chars. Encoding all of them takes seconds with bge-small and about a minute with EmbeddingGemma. The audit collection needs no re-embed (placeholder vectors). Dimension is 384 for MiniLM, bge-small, snowflake-s and MemPalace's EmbeddingGemma, so **a swap would not fail loudly: mixed old and new vectors in one index are silent garbage.** The re-embed must be one atomic pass through the existing `_rebuild_drawers` path with a collection-level model stamp checked at query time (today only each row carries `embedding_model_version`, `memory_service.py:1975`).

### 2.5 What I tried to reproduce and could not

The 2026-10-04 symptom (owner-filtered query returns nothing while the rows exist; 1,591 elements for 258 live rows). On chroma 1.5.9 in a throwaway directory I tried: 3,000 deletes then a filtered query; 3,000 rows upserted three times then deleted; the same, then reopen in a fresh process, with demo rows tightly clustered around the query and owner rows scattered. **[measured]** In all three the filtered query returned the full result and matched exact ranking 10/10. So the mechanism of the incident is **[unverified]**; the compaction is a mitigation of an unexplained state, not a fix with a proven cause. The saved pre-compaction tar is the one artifact that can settle it (G6 test).

## 3. Measured today

### 3.1 Chroma behaviours (throwaway dirs under the session scratchpad; live palace never opened by chromadb)

| Check | Result |
|---|---|
| `update(metadatas=[{k: v}])` | merges; other keys kept; `None` removes a key |
| New collection default config | `l2`, ef_construction 100, ef_search 100, M 16, resize 1.2, sync 1000 |
| 4,000 identical vectors vs random, insert | 3.8 s vs 2.6 s; query on the identical-vector collection 3.8 ms |
| k=200 query, 300 vectors | HNSW 4.02 ms; exact numpy matmul + top-k 0.03 ms; one-off `get(include=embeddings)` 40 ms |
| k=200 query, 20,000 vectors | HNSW 8.23 ms; exact 7.99 ms; one-off `get(include=embeddings)` 986 ms; matrix 30.7 MB |
| EF identity in `config_json_str` | `'{}'` with or without a named EF |

### 3.2 Embedder cost (fresh process, includes Python imports, `OMP_NUM_THREADS=2`)

| Embedder | RSS after load + 1 call | Query latency p50 (warm, box under load) |
|---|---|---|
| MiniLM (chroma ONNX, live) | 226 MB | 41-46 ms |
| bge-small-v1.5 q8 (fastembed, the router's) | 310 MB | 7.5 ms (10.3 ms with query instruction) |
| snowflake-arctic-embed-s | n/m | 9.7 ms |
| nomic-embed-v1.5-Q | n/m | 14.4 ms |
| EmbeddingGemma-300M q8 via `mempalace.embedding` (384-d MRL) | **1,447 MB** | **204 ms** |

Available RAM at the time was about 2.3 GB; the brain, Kokoro and zoe-data are resident. EmbeddingGemma is out on this box regardless of recall.

### 3.3 Retrieval pilot (96 facts, 276 queries)

Corpus: 12 invented people x 8 attributes (job, dog's name, birthday, favourite meal, allergy, dentist day, gym locker number, car), one sentence each, so every query faces 11 same-attribute distractors and 7 same-person distractors. Queries: direct (96), paraphrase (96), reverse value-to-person (84). Gold = the one fact. Metrics: hit@1, hit@3, MRR.

| Embedder | vector only (h@1 / h@3 / MRR) | Zoe live blend* | MemPalace 0.6/0.4 | 0.9/0.1 | RRF |
|---|---|---|---|---|---|
| BM25 only | 0.609 / 0.739 / 0.706 | | | | |
| MiniLM (live) | 0.949 / 0.989 / 0.969 | 0.928 | 0.884 | 0.960 | 0.725 |
| bge-small q8, no prefix | 0.964 / 1.000 / 0.982 | 0.920 | 0.826 | 0.960 | 0.732 |
| bge-small q8, +query instruction | 0.953 / 1.000 / 0.976 | 0.913 | 0.793 | 0.909 | 0.736 |
| snowflake-arctic-embed-s | 0.946 / 1.000 / 0.972 | 0.920 | 0.790 | 0.960 | 0.736 |
| nomic-embed-v1.5-Q | 0.906 / 0.967 / 0.942 | 0.913 | 0.822 | 0.960 | 0.736 |
| EmbeddingGemma 384-d | 0.975 / 1.000 / 0.987 | 0.938 | 0.801 | 0.967 | 0.732 |

*Zoe live blend = `0.7 * 1/(1+d_l2sq) + 0.5 * keyword_overlap` using a copy of `_hybrid_keyword_overlap` and Zoe's stopword list, `conf` fixed at 0.7, no decay (all rows same age), no hotness/graph/type terms. It is a reconstruction, not the service.

By query kind for MiniLM: vector only direct 1.000 / paraphrase 0.896 / reverse 0.952; Zoe blend 1.000 / 0.792 / 1.000; MemPalace 0.6/0.4 0.938 / 0.729 / 1.000.

### 3.4 How far to trust the pilot

- n=276, hit@1 near 0.95: standard error about 1.3 points. Embedder differences of 1-3 points are **not separable**; only the large effects (RRF and 0.6/0.4 hurting; latency and RAM differences) are real at this size.
- The corpus is easy, uniform, templated and English; real rows are messier (names inside longer sentences, relationships, dates, updates). It is a harness proof, not a verdict. The decisive set does not exist yet (G5).
- BM25 candidate pool here is the whole 96-row corpus; production re-ranks the top-200 vector hits. At 284 rows these are nearly the same pool, at scale they differ.
- The box was loaded; latencies are relative, not absolute.
- Why lexical hurt: the person's name is the highest-IDF token and appears in all 8 of that person's facts, so BM25 lifts same-person wrong-attribute facts; paraphrase queries share no attribute words with the answer. Reverse queries (value tokens) are where lexical helps (MiniLM 0.952 to 1.000). Lexical weight should therefore depend on the query shape, not be a constant.

## 4. Gaps ranked (impact x effort)

Scale: impact H/M/L on recall fidelity or crash risk; effort XS (a few lines), S (under a day), M (days), L (a week plus). The "Proof" column is the test or measurement that decides it.

| Rank | Gap | Impact | Effort | Proof |
|---|---|---|---|---|
| G1 | **Pin `hnsw:num_threads=1` on every open** (C1) | H (crash class; Zoe has HNSW crash history and a 10-04 wedge) | XS | Soak on a scratch copy: N threads of upsert + metadata `update` + filtered query for 30 min, count segfaults and wedges, with and without the pin. I could not make default threads crash on a fresh store, so the control may be silent; MemPalace #974 reports the crash in `repairConnectionsForUpdate`, the path `update` and `upsert` exercise. Also assert after reopen: `col.configuration_json["hnsw"]["num_threads"] == 1`. |
| G2 | **Authority in the ranker**: replace the per-writer `confidence` multiplier with an authority-class weight; keep `conf` only inside a class (`memory_service.py:2384`) | H | S | Unit: at equal distance a user-stated row beats an LLM-paraphrase row, and a superseding LLM row never outranks the user's row. Offline: add authority scenarios to the eval (G5). Live evidence: `digest` 0.9 and `idle_consolidation` 0.8 vs user `conversation`/`voice` 0.7 **[measured, aggregates]**. |
| G3 | **No age decay on durable attributes**; decay only event/episodic types (`:1543, :2196, :2337`) | H (latent: oldest row is 91 days, factor 0.41; at one year 0.027) | S | Synthetic: a 400-day-old name/allergy row vs a fresh lower-similarity distractor; today the distractor wins (compute from the formula; confirm in the harness). |
| G4 | **Build the household-shaped retrieval eval** before any retrieval change | H (every other decision depends on it) | M | Design: at least 300 queries (SE under 1.3 points), categories direct / paraphrase / reverse / names / numbers / dates / negation / update-supersede / same-name distractors / long-tail / non-English / **no-answer** queries; report hit@1/3/5, MRR, abstention precision, p50/p95 latency, RSS; real phrasing sampled from query-length and shape logs, with invented entities. The pilot harness in section 3.3 (dataset generator + scorer) is the starting skeleton; scripts are in the session scratchpad `…/scratchpad/pilot/` and are throwaway, so re-create them in `scripts/perf/` if adopted. Replace the 7-case term-overlap baseline (`mempalace_baseline.py:31-120`). |
| G5 | **Relevance floor / abstention** (`memory_service.py:2315-2326`; `routers/memories.py:852-858`): `search` returns top-`limit` whatever the distance, and the packet header says the memories are "authoritative and current" | H (irrelevant rows injected as fact) | S after G4 | No-answer queries in the eval: false-injection rate vs threshold, per model (bge scores sit in 0.6-1.0 so thresholds are model-specific, **[doc]**). Threshold chosen at the knee, stored with `embedding_model_version`. |
| G6 | **Exact search over the user's rows instead of HNSW** (C3, C4, section 2.5): `get(include=["embeddings"])` once into a numpy matrix keyed by a write counter, `where` as an exact pre-filter, cosine in numpy; Chroma stays the store of record | H (removes tombstones, `count()` wedge, "full but wrong" filtered queries, the maintenance gate and compaction code) | M-L | (a) Operator-approved replay of the 2026-10-04 pre-compaction tar on a scratch copy: filtered vs unfiltered vs exact ranking on a fixed query list, counts and id overlap only (I was denied extracting it; this needs a go-ahead). (b) Shadow mode: top-k ids from HNSW vs exact on live queries for a week, log disagreements. (c) Latency: exact 0.03 ms at 300 rows, 8 ms at 20k (**[measured]**). (d) kill -9 mid-write then query. Do not touch the embedder in this change. |
| G7 | **Don't rewrite the whole metadata dict on every read** (C6); move access counters out of Chroma | M | XS-S | Race test: snapshot, concurrent `supersede`/`archive`, then `tick_access` write-back; assert status survives. `update` merge semantics **[measured]**. Hotness is already almost flat (prior audit: `access_count>0` for 81 of 82 rows), so the counters buy little. |
| G8 | **Verbatim evidence channel** (see the `convo_miner` row in section 1.3): embed the user's raw turn (or keep `source_excerpt` on every per-turn writer) so details dropped by distillation stay reachable | H | M-L | Eval slice "detail-drop": answer present in the raw turn but not in the distilled row; hit@k with and without the channel. Zoe's own note credits "+15.9 LoCoMo" for verbatim beside distilled (`recall_evidence.py:4-5`); today 5 of 284 rows have `source_excerpt` **[measured]**. Must cascade on forget (prior audit §3.7). |
| G9 | **Hybrid retuned, query-shape aware**: replace overlap-fraction by IDF-weighted rare-token (name/number/value) evidence with a small weight; raise it only when the query carries value tokens | M | S after G4 | Pilot signal: 0.9/0.1 was +1.1 points; Zoe's 0.5 keyword weight cost paraphrase 10 points and gained reverse 5 (**[measured]**, synthetic). Accept only if it wins on G4 overall AND per slice. Optional free lexical candidates via `where_document={"$contains": …}` (C5) so exact-value rows outside the vector top-200 can enter. |
| G10 | **Move the audit log to an append-only SQL table** (C7) | M | M | Row-count and hash parity on a copy; `delete_user` and compaction time before/after; vector file drops from ~39 MB to ~1 MB. |
| G11 | **Embedder: consolidate on the router's bge-small q8, only if G4 says so** | L-M (latency 45 to 7.5 ms, one fewer ONNX session; recall unchanged on the pilot) | M | Decision rule: switch only if hit@1 on the G4 set is not worse and p50 or RSS improves, or hit@1 improves 3+ points. Procedure: re-embed all rows in one pass through `_rebuild_drawers`, collection-level model stamp, refuse mixed-version queries, keep the old tar. EmbeddingGemma rejected on RAM (1.4 GB) and latency (204 ms). |
| G12 | **Drop the dead dependency and the stale pins** | L | XS | `grep` shows no production import; remove from `requirements-py312.txt:65`, `requirements.txt:106-107` and the smoke list in `scripts/setup/build_py312_venv.sh:83`; CI green in a worktree. Removes the pin churn B0.8 documented and the onboarding-default hazard. |
| G13 | **One near-duplicate function** (four today) using stored vectors | M | M | Run all four on the same labelled pair set; agree on one threshold; assert zero new false merges on the eval's update/supersede slice. |
| G14 | `query_sanitizer` port and a query-length histogram | L now | XS | Histogram of `len(query)` at `search`; if p99 under 200 chars, skip. |
| G15 | Orphan HNSW dirs (3.3 MB), `as_of` reads, similar-name check | L | S each | Orphan dirs are not in `segments`; as_of and similar-name are covered by the prior audit's plan. |

Dependency order: G1 and G12 any time; G4 first for everything that changes ranking (G5, G9, G11); G2 and G3 are small and can ship with their unit tests; G6 after the replay.

## 5. Recommendation

**Decision on MemPalace.** Treat it as a reference, not a dependency. Zoe already owns its storage code; the package adds pin risk and a wrong-model onboarding default for no runtime use. Port four small things (BM25 + metric-aware similarity, `num_threads=1` pin, `query_sanitizer`, half-open interval semantics for `as_of`) and remove the package.

**Decision on Chroma.** Keep Chroma 1.5.9 as the durable store (SQLite, FTS5, metadata filters work). Pin threads (G1). Stop relying on HNSW for a collection this small: add an exact-search read path in shadow mode (G6) once the replay settles the unexplained 10-04 failure. Keep the compaction code until exact search has run clean for a period, then retire it.

**Decision on embeddings.** No swap now. Build the eval (G4); on the pilot, MiniLM, bge-small, snowflake-s and EmbeddingGemma are not separable and EmbeddingGemma does not fit the RAM budget. If the eval agrees, moving to the router's bge-small is a latency and RAM saving, not a recall one.

**What makes the layer better than mem0 / Zep / Letta.** From the 2026-10-05 fidelity audit's field table and today's reading:

| System | Retrieval and storage today | What Zoe can do that it does not |
|---|---|---|
| mem0 (v3) | **[doc]** mem0.ai blog: three parallel scoring passes (semantic, keyword, entity matching) fused by rank scoring; ADD-only store; under 7,000 tokens per retrieval; default embedder and store not stated | An authority axis; local exact search; abstention; verbatim beside facts |
| Zep / Graphiti | **[doc]** github.com/getzep/graphiti: hybrid semantic + BM25 + graph traversal, rerankers (RRF, MMR, cross-encoder, node distance), Neo4j/FalkorDB/Neptune, default OpenAI embeddings, an LLM at ingest | No graph database or cloud call on the hot path; newest-wins is not the rule (user-stated wins); bi-temporal reads |
| Letta | **[doc]** docs.letta.com archival memory: semantic search with tags and paging; store, embedder, hybrid and top_k are not documented | Everything above; also a measured retrieval eval instead of a self-reported score |

The shape of the win: (1) retrieval ranks by **who said it** before how similar it is (G2, G3); (2) it can say **nothing relevant** (G5); (3) every distilled fact has **verbatim evidence** reachable (G8) and a validity interval with `as_of` reads; (4) it is **deterministic** (exact search, no ANN state to corrupt) and fast on-device; (5) it is **measured** on a household-shaped set that includes an authority axis no public benchmark has (LoCoMo and LongMemEval do not test it; the prior audit's section 5.2). Vendor numbers (LoCoMo, LongMemEval) are self-reported and disputed; do not quote them as settled.

## 6. Caveats

- Pilot is synthetic and small (section 3.4). The "Zoe live blend" row is a reconstruction.
- I could not reproduce the 10-04 incident or any HNSW crash; claims about it are labelled unverified.
- MiniLM's MTEB retrieval figure (41.95) is recalled, not fetched. The MemPalace README and CHANGELOG were read through a summarising fetch tool; the installed 3.10.0 source was read directly and is the stronger evidence.
- Whether any direct drawer writer interleaves with `tick_access` (C6) is unverified.
- The pre-compaction tar was not opened (denied); no household row text was read at any point. Throwaway Chroma stores and embedder caches live under the session scratchpad and a scratch `HF_HOME`; the live palace and the shared model caches were not modified (one fastembed read of the existing `/tmp/fastembed_cache`).
