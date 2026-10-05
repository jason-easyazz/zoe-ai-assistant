---
type: Research / ADR-draft
title: "Memory system decision: adopt, fork or keep? Eleven candidates, one pre-registered bake-off (2026-10-05)"
status: DRAFT for the owner. Research only. Nothing installed, nothing run against the live store, backups or /home/zoe/assistant. All names below are synthetic.
date: 2026-10-05
description: Owner mandate is "stop building our own; adopt a maintained system, keep only the thin layer nobody provides." Eleven candidates (mem0 OSS v3, Graphiti, Letta, MemPalace, Honcho, Cognee, LangMem, A-MEM/MemoryBank, Memobase, Supermemory, Hindsight) plus Zoe's current MemoryService were read at source or docs and judged by the same criteria. Top three get an adoption design (where it sits, what Zoe keeps, what is deleted, migration, RAM, latency, the week's four failure modes mapped to source lines). Ends with a bake-off runnable on the lab box this week, a decision rule fixed in advance, and a plain recommendation.
evidence_labels: "[src] file:line read today | [doc] URL or docs file read today | [measured] command given, run today | [derived] arithmetic on measured numbers | [unverified] not confirmed"
---

# Memory system decision (2026-10-05)

## 0. Answer first

1. **No candidate gives Zoe the property the owner asked for.** None of the eleven has an authority
   rule ("an inference may never supersede, archive or contradict what the user said"). Every one that
   puts an LLM in the write path either resolves conflicts newest-wins (Graphiti, Hindsight
   consolidation, mem0 ranking) or by the agent's own judgement (Letta, LangMem). The incident class
   of this week is therefore *not* bought by adoption; it stays in the thin layer under every option.
   The thin layer is already written and merged: `services/zoe-data/memory_authority.py` (877 lines, PR #1868).
2. **mem0 OSS v3 cannot run on our brain at all as shipped.** Its extraction system prompt is
   **8,131 Gemma tokens** [measured, section 2.1] against the live slot `--ctx-size 8192 --parallel 1`
   [measured, `ps`]. It also extracts from assistant turns (opposite of Zoe's purity contract), has no graph in OSS
   v3, and phones home by default. **Dropped.**
3. **Letta's server is retired** (the repo README says the V1 API server lives on an `archive` branch;
   the maintained product is `letta-code`, a TypeScript coding-agent harness) [src, section 2.3]. Putting it
   under Flue would be a second pi look-alike, exactly the thing the owner named. **Dropped.**
4. **Hindsight is the only maintained system that clears the hard local gates on paper:** its concise
   extraction prompt is **2,318 tokens** [measured], `llamacpp`'s documented default model is a Gemma 4
   (`gemma-4-e2b-it`, `config.py:1108`) [src], it runs on Postgres + pgvector (Zoe's `zoe-database` image is
   `pgvector/pgvector:pg17`, extension `vector 0.8.2` available, not enabled) [measured], it has
   `verbatim` and `chunks` extraction modes that store the user's own words instead of a model paraphrase,
   per-bank config switches for every retrieval arm and the reranker, document-delete cascade, and it was
   already run on this box against local Gemma (4/4 recall, about 0.94-0.98 GiB RSS, recall p95 about 650 ms)
   [src `docs/architecture/zoe-hindsight-bakeoff.md:170-246`]. It is also pre-1.0 (0.10.2), had 760 issues
   opened in 90 days [measured], and its entity resolver has a documented name-conflation failure class
   (section 3.1).
5. **Graphiti is the right model for the temporal axis and the wrong machine for this box:** 4-8 LLM
   calls per episode [src], a default `max_tokens` of 16,384 against an 8,192 slot [src
   `llm_client/config.py:19`], open small-model structured-output bugs [doc issue #1909], newest-wins
   contradiction code [src `edge_operations.py:538-573`]. Keep its *ideas* (the people-graph record already
   specifies them); do not run it as the memory system. One cheap arm (`add_triplet`, no Graphiti LLM) stays in
   the bake-off so the claim is measured, not assumed.
6. **RAM is the unmoved constraint.** `MemAvailable` is **2,338 MB** now with swap in use [measured, `free -m`];
   the voice gate wants 2 GB quiet headroom. A sidecar of 0.5-1.0 GB therefore breaks the gate unless
   something is freed first (W3 reclamation, or the net deletion of Chroma/ONNX from zoe-data). This is a
   hard gate in the decision rule, not a footnote.
7. **Recommendation (plain):** *Adopt Hindsight as the single derived-memory engine, conditional on the
   pre-registered bake-off in section 6 passing, and keep a roughly 900-line Zoe layer on top (authority,
   identity-from-account, affect/consent policy, extraction-coverage invariant, durable forget ledger,
   Samantha bar).* Delete about 7-9k of the 17.9k memory lines. **If it fails the rule, do not adopt
   anything else: keep the current MemoryService with the audit's P1-P3 plan, port two ideas, and reopen the
   question only when the slot or RAM changes.** Section 7.

## 1. What "adopt" can and cannot mean here

Zoe's memory is five layers: **capture/extraction** (regex extractors, turn digest, person LLM extractor,
nightly digest, idle consolidation), **store** (Chroma rows + audit collection; Postgres `people`,
`person_relationships`), **read** (search, `/api/memories/for-prompt` packet, ranking), **lifecycle**
(supersede, decay, consolidation, contradiction judges, tombstones) and **policy** (authority, identity,
affect consent, guest/kid rules, the Samantha bar). The field sells layers 1-4. Layer 5 is the product,
and the owner's failures this week (name overwritten, children's names dropped, correction not applied,
forgotten facts resurfacing) are layer-1/4/5 failures. The fair framing: adopt layers 2-4 if a candidate
beats ours on the same cells at acceptable RAM; layer 5 is ours under every option.

Constraints that bind every candidate **[measured today unless marked]**:

| Constraint | Value | Source |
|---|---|---|
| Brain | Gemma 4 E4B QAT + MTP draft, `--ctx-size 8192 --parallel 1`, port 11434, RSS 6.35 GB | `ps -o args= -p <llama-server pid>`; `ps --sort=-rss` |
| Decode / prefill speed | about 33 tok/s decode, about 650 tok/s cold prefill | [src] `docs/research/inference-speech-stack-2026-10-03.md:125-126` |
| Box RAM | 15,655 MB total, **2,338 MB available**, 630 MB free, swap 1.6 GB used | `free -m` |
| Other residents | zoe-data (py3.12 venv) 1.47 GB, router llama 0.65 GB, Kokoro 0.42 GB | `ps --sort=-rss` |
| Postgres | `pgvector/pgvector:pg17`; installed extensions: `plpgsql` only; `vector 0.8.2`, `pg_trgm 1.6` available | `docker inspect`, `pg_available_extensions` (catalog read only) |
| Python | zoe-data runs a **py3.12** venv; system python is 3.10.12 | `ps` (venv path), `python3 --version` |
| Live store size | 284 drawer rows (103 owner, 82 approved), 32 `people` rows, 3 current edges | [src] fidelity audit section 4; people-graph record 1.1 |
| Doctrine | local-only, memory stores never leave the box; rocks fixed; lab-prove before prod | `docs/VISION.md`, samantha-evolution-plan section 11 |

Scale note: 284 rows is nowhere near LongMemEval-S (about 115k tokens) or BEAM. Retrieval at this scale is
not where any candidate wins; MemPalace's own committed results show raw verbatim text plus default Chroma
embeddings reaches recall_any@5 = 0.966 (n=500) and 0.9844 on its 450 held-out [measured: I re-aggregated
`benchmarks/results_mempal_raw_session_20260414_1629.jsonl` and `..._hybrid_v4_held_out_...jsonl`, per-question
`metrics.session.recall_any@5`, command in section 2.4; this is session-level *retrieval* recall, not QA accuracy,
and it says nothing about updates, authority or forgetting]. The decision therefore turns on **write policy,
footprint and failure behaviour**, not retrieval scores.

## 2. Candidate screen (same criteria for all)

Activity = commits since 2026-07-07 (GitHub API, capped at 100 per call) and issues opened since then
[measured, `gh api repos/<r>/commits?since=2026-07-07...`, `search/issues`].

| Candidate | Licence | 90-day activity | Py3.12 / local LLM fit | Verdict |
|---|---|---|---|---|
| **mem0 OSS** (`mem0ai` 2.2.1 source = "v3" algorithm) | Apache-2.0 | 100+ commits, 400 issues | py>=3.10 ok. **Extraction prompt 8,131 tokens > 8,192 slot** | **Drop** (2.1) |
| **Graphiti** (`graphiti-core` 0.30.2) | Apache-2.0 | 100+ commits, 111 issues | py>=3.10; FalkorDBLite needs 3.12; LLM per episode, 16k max_tokens default | **Top 3, scoped** (3.2) |
| **Letta** server | Apache-2.0 | server repo: last push 2026-09-10 (docs/policy only) | n/a | **Drop** (2.3) |
| **MemPalace** 3.10.0 | MIT | 100+ commits, 230 issues, v3.10.0 2026-09-16 | no LLM needed; already installed | **Reference, not dependency** (2.4) |
| **Honcho** 3.2.2 | **AGPL-3.0** | 100+ commits, 115 issues | **requires-python >=3.13** (`pyproject.toml:9`); pgvector + redis; tool-calling agents | **Drop** (2.5) |
| **Cognee** 1.6.2 | Apache-2.0 | 100+ commits | py3.10-3.14; instructor + lancedb + ladybug graph; document pipeline | **Drop** (2.6) |
| **LangMem** | MIT | 18 commits, 67 issues open | depends on LangGraph store, trustcall | **Drop** (2.6) |
| **A-MEM / MemoryBank** | MIT | A-mem last push 2025-12 / 2026-03; MemoryBank 2023-05 | research code | **Drop** (2.6) |
| **Memobase** | Apache-2.0 | **0 commits since 2026-07-07**, last push 2026-01-11 | server stack | **Drop** (stale) |
| **Supermemory local** | repo MIT, **server source proprietary** | 100+ commits | local needs a 20B-class model, single user, no multi-member | **Drop** (2.6) |
| **Hindsight** (`hindsight-api-slim` 0.10.2) | MIT | 100+ commits, **760 issues**, v0.10.2 2026-09-29 | py>=3.11; Postgres+pgvector; Gemma-4 default for `llamacpp` | **Top 3, lead** (3.1) |
| **Zoe current** (MemoryService + authority) | ours | PR #1868 merged | runs today | **Top 3, control** (3.3) |

### 2.1 mem0 OSS v3 (dropped)

* ADD-only, one LLM call per `add()`: "Extraction: Single-pass ADD-only (one LLM call, no UPDATE/DELETE)"; "Graph memory
  moved to Platform: the external graph store integration is removed from OSS" [doc/src
  `docs/migration/oss-v2-to-v3.mdx` Overview; `add()` pipeline `mem0/memory/main.py:881-1000`].
* **Prompt size** [measured]: `ADDITIVE_EXTRACTION_PROMPT` (`mem0/configs/prompts.py:468`, 33,662 chars) tokenised with the
  live brain's own `POST /tokenize`: **8,131 tokens**. The slot is 8,192. The system prompt alone leaves 61 tokens for the
  existing-memory list, the new messages and the JSON answer. There is no config to swap the prompt (only
  `custom_instructions` is appended). Stock mem0 extraction **cannot run on this brain**; a fork to a 2k prompt would
  be a rewrite of the thing we would be adopting.
* It extracts from **both user and assistant messages** (prompt line 474: "You extract from BOTH user and assistant
  messages"), the inverse of Zoe's extractor-purity contract (`memory_extractor.py:678`) and the recall-echo loop
  the audit cites (mem0 issue #4573). One can pass only user turns, but the prompt still tells the model assistant text is
  extractable.
* Conflicts: none resolved at write ("the new fact is stored alongside the old one"); ranking decides. Open issue #7535
  (2026-10): "Search ranks an outdated memory above its update: `score_and_rank` has no recency term" [doc, `gh api`
  search]. `update()` is a bare overwrite (`main.py:1829-1881`); `history()` is a per-memory edit log (`:1960`), no
  authority field; `actor_id` and `role` are the only provenance.
* Telemetry: `MEM0_TELEMETRY` defaults to `True`, PostHog at `us.i.posthog.com` (`mem0/memory/telemetry.py:14-16`) [src].
  Needs `MEM0_TELEMETRY=False` to be local-only.
* What survives as an idea: hybrid retrieval with an entity-match signal, already in Zoe's `_blend`.

### 2.2 Graphiti (kept scoped, section 3.2) and 2.3 Letta (dropped)

2.3 Letta. `letta-ai/letta` README: "The current source code lives in `letta-ai/letta-code`"; "The `archive` branch contains the
retired Letta V1 API server ... active projects should use the current source" [src, `README.md`]. `letta-code` is described
as "a coding-agent harness, not a memory backend for other runtimes", Node/Bun, defaults to Letta Cloud sign-in
[doc, github.com/letta-ai/letta-code]. Memory blocks, MemFS (git-tracked files) and `/sleeptime` "dreaming" are
good ideas Zoe already approximates (`docs/knowledge`, weekly portrait), but adopting the server means adopting another
agent loop beside Flue/pi. **Dropped.** Idea kept: `read_only` blocks = a locked, user-authored row (already P1.1).

### 2.4 MemPalace 3.10.0 (reference, not dependency)

* README claims (develop branch): LongMemEval R@5 96.6% raw, 98.4% on a held-out 450, "zero API calls", LoCoMo R@10 60.3% raw
  / 88.9% hybrid [src `README.md:25,107-135`]. It "deliberately" does not compare with mem0/Zep/Hindsight.
* First-hand check [measured]: per-question metrics in the committed result files re-aggregate to the README figures.
  Command: a 12-line python script summing `retrieval_results.metrics.session["recall_any@5"]` over
  `benchmarks/results_mempal_raw_session_20260414_1629.jsonl` (n=500 → **0.966**; by type: single-session-user 64/70,
  multi-session 132/133, preference 29/30, temporal 126/133, knowledge-update 78/78, assistant 54/56) and
  `..._hybrid_v4_held_out_session_20260414_1634.jsonl` (n=450 → **0.9844**). I did not re-run retrieval (no install).
* What the claim is and is not: retrieval of the right *session* in the top 5, with raw verbatim text and Chroma's default
  embedder. Its own `BENCHMARKS.md` says "no extraction ... stores the actual words". It does not test updates (the knowledge-update row of 78/78 only
  means the session was retrieved, not that the current value was chosen), authority, forgetting, or any write. It is the strongest published argument
  for **verbatim-first storage**, which is the point we take.
* As software: Zoe imports none of it (`mempalace-chroma-best-practice` record section 0.1). No authority, no people model, no
  affect. Decision stands as in that record: port BM25 and half-open `as_of` intervals; do not depend on the package.

### 2.5 Honcho (dropped)

"Reasoning-first memory" with a deriver, a `dialectic` agent that "uses tools to gather context", and `dreamer` specialists
(`src/dialectic/core.py:4`, `src/dreamer/`), configured by default to `gpt-5.4-mini` (`config.toml.example`), `requires-python >=3.13`
(`pyproject.toml:9`, our venv is 3.12), Postgres+pgvector+Redis, **AGPL-3.0** [src]. Multi-step tool-calling agents are
beyond a 4B on one slot. Local override exists (`OPENAI_BASE_URL`, `src/config.py:788`) but that does not change the agent depth.
Idea kept: peer "conclusions" that cite their premises.

### 2.6 Cognee, LangMem, A-MEM/MemoryBank, Memobase, Supermemory (dropped, one line each)

* **Cognee 1.6.2**: a document `add -> cognify -> memify` knowledge-graph pipeline with `instructor` structured output, lancedb,
  ladybug graph; v1.6.0 added "keyless" local-model workflows [src `pyproject.toml`, `README.md:304`]. Built for ingesting corpora,
  not for a user whose last sentence corrects the previous one; no per-fact authority.
* **LangMem**: SDK over a LangGraph `BaseStore`; `enable_inserts/updates/deletes` flags, no deletes by default
  (`knowledge/extraction.py:224-226`) [src]; the model chooses. 18 commits in 90 days.
* **A-MEM / MemoryBank**: research code, stale (dates above). MemoryBank's Ebbinghaus decay is what Zoe's 70-day half-life already is
  (and the audit says that decay is wrong for durable facts).
* **Memobase**: no commits in the window (last push 2026-01-11): rule 1 of the screen.
* **Supermemory local**: "The server source code is proprietary"; local is "single auto-generated API key", no multi-user;
  recommended local model `gpt-oss:20b` [doc, supermemory.ai/docs/self-hosting/local-vs-enterprise; `README.md:314-341`].
  Fails licence, multi-user and RAM at once.

### 2.7 Criteria not discriminating (so not in the table)

Every kept candidate runs offline once pointed at a local OpenAI-compatible endpoint, and every one needs its telemetry
turned off (`MEM0_TELEMETRY`, `GRAPHITI_TELEMETRY_ENABLED` at `graphiti_core/telemetry/telemetry.py:22`; I found no
PostHog/analytics hook in Hindsight's `config.py` or `pyproject.toml`, which is a grep, not a network audit [unverified]).
None has a household/guest/children policy; none models affect with consent. These are Zoe's layer under every option.

## 3. Top three, in depth

### 3.1 Hindsight (lead candidate)

**What it is** [doc, `hindsight-docs/docs/developer/*`]: banks (isolation) with tags (visibility); `retain` chunks content and
extracts structured facts (`what/when/where/who/why`, `fact_type`, `entities: list[str]`, occurred_start/end plus mention time),
builds entity/time/semantic/causal links, then background **observations** (consolidated beliefs with source facts, proof counts
and exact-quote evidence); `recall` fuses four arms (semantic, BM25, graph, temporal) and reranks with a cross-encoder; `reflect`
is the agentic read, which we would not use per turn.

**Why it passes the gates that killed the others**

* Prompt size [measured, `fact_extraction.py:1053-1218` tokenised on the live brain]: base + concise guidelines + examples =
  **2,318 tokens** (1,316 without examples). Default chunk is 3,000 chars (`HINDSIGHT_API_RETAIN_CHUNK_SIZE`). Fits an 8k slot with room.
* Small-model posture: `llamacpp` default model is `gemma-4-e2b-it` (`config.py:1108`) [src]; fact schema forces all five dimensions
  required (`ExtractedFact`, `fact_extraction.py:255-285`); it imports `strict_json_schema`, i.e. the "every property required"
  shape whose absence is Graphiti issue #1909 [src, doc]. On this box the 2026-06-11 run used local Gemma + cached BGE:
  4/4 retains, 0 extraction errors, recall 8/8 at score 1.0, p50 514 ms, p95 658 ms, container 0.94-0.98 GiB
  [src `docs/architecture/zoe-hindsight-bakeoff.md:170-246`]. n=4 events: a feasibility proof, not a quality result.
* **Verbatim-first is a mode, not a fork.** `retain_extraction_mode`: `concise` (default), `verbose`, `custom`, **`verbatim`** ("original
  chunk text preserved, with LLM-extracted metadata such as entities and dates"), **`chunks`** ("stored as-is with no LLM call")
  [doc `retain.md:288-302`; `configuration.mdx:1645-1690`]. In `verbatim` the stored text is the user's own words (a span), which is the
  audit's P1.2 anchor rule satisfied by construction and removes the paraphrase-laundering path.
* **Footprint is configurable per bank**: `ENABLE_TEXT_SEARCH`, `ENABLE_TEMPORAL_RETRIEVAL`, `ENABLE_GRAPH_RETRIEVAL`, `ENABLE_RERANKING`
  are hierarchical per-bank switches; `enable_observations: false` skips consolidation, "the other background LLM workload"
  [doc `configuration.mdx:1660-1690`]. Slim image: 512 MB minimum, 1 GB recommended, no local models, needs external embedding and
  reranker providers; full image idle 0.8-1.0 GB, 1.2-1.5 GB loaded [doc `installation.md:55-62`].
* **Scoping and cascade are documented**: tags with `any/all/any_strict/all_strict/exact` (`recall.mdx:193-217`); consolidation uses
  `all_strict` so "a memory consolidated under [student:alice] will never bleed into an observation tagged [student:alice,
  teacher:bob]" (`api/retain.mdx:188`); re-retaining a `document_id` deletes the old document **and all its memories**
  (`api/retain.mdx:120`) and consolidation re-runs after retain/delete/update (`observations.mdx`).

**Where it fails the owner's requirements** (all need Zoe's layer, none are native)

* *Consolidation is newest-evidence-wins, by an LLM.* `consolidation/prompts.py:39-47`: "PREFER UPDATE OVER CREATE ... STATE CHANGES -
  UPDATE CONCISELY"; `deletes` "only when an observation is directly superseded or contradicted by new facts" (`:169`). A model-derived
  fact "User's name is Dev" would UPDATE/DELETE the observation built from the owner's statement. That is incident S1 again, one layer
  up. **Fence (design, untested):** consolidate per authority scope, using tag scopes that `all_strict` isolates
  (`class:user_stated` observations can never be updated by facts tagged `class:inferred`); Zoe refuses to retain model-derived text into
  the `user_stated` scope. Whether the fence holds is bake-off cell A1/A2 on arm H2.
* *Entity resolution can absorb a new person into an unrelated entity.* The docs say so: "a short new name that resembles an existing
  entity ... can be absorbed into it instead of becoming its own entity" (`retain.md`, Entity Resolution). Issue #3751 (closed
  2026-08-24): a correctly extracted new person `Tigran` was stored on an existing `Iran` entity because name similarity (0.80
  SequenceMatcher) plus co-occurrence cleared the 0.6 threshold [doc]. A household with short, similar names (Mika/Mikaela, Leo/Lea) is the
  worst case. Whether 0.10.2 contains the fix is [unverified]. Bake-off cell B-names.
* *No coverage invariant.* `entities` is a model-written list; nothing checks that every personal name in the user's text appears. The
  "dropped children's names" class (brain-extraction record G3/G4) is the same under Hindsight. Zoe's deterministic name-coverage check
  (a regex/GLiNER-style pass over the user turn, one bounded retry) stays in our layer.
* *No durable forget across re-ingest.* Deleting a document cascades, but a nightly retain over a transcript that still contains the
  forgotten turn recreates the facts. The forget ledger (source turn ids excluded from any retain) must be Zoe's.
* *Churn.* 0.10.2, pre-1.0; 760 issues opened since 2026-07-07 [measured]; 239 open. Pin the version, vendor nothing, treat the HTTP API as
  the seam.

**Where it sits.**

* **Read:** `/api/memories/for-prompt` keeps its contract (`routers/memories.py`); its implementation becomes
  `HindsightBackend.recall(bank, query, tags, budget=low, max_tokens=~1,200)` for the user's bank plus the `household` bank, then Zoe's
  packet builder (authority labels, `[mem:id]` cites, "you told me" vs "I inferred" marker, 12 bullets / 1,600 chars). Flue is unchanged:
  `ZOE_SEAM_RECALL_INJECT` still injects that packet on recall-shaped turns (`zoe_flue_client.py:551-570`) and the brain's `recall_memory`
  tool still calls the same endpoint. **No Flue/sidecar change.**
* **Per-turn writers:** keep the deterministic extractor (`memory_extractor.py`, rank-4 `user_stated`) as the only per-turn writer; it calls
  `authority.resolve_write()` and then `retain(chunks|verbatim)`. Replace the turn digest, the person LLM extractor, idle consolidation and
  the weekly passes with **one** async `retain(document_id=<user>/<day>, content=<user-only turns>, context="<name> is speaking")` at idle,
  never in the voice path. This also removes one of the two serialised LLM passes per turn (brain-extraction record G8).
* **Nightly digest:** becomes that retain plus (arm H2 only) a scoped consolidation. The 03:00 pass no longer reads `chat_messages` and
  re-asserts.
* **Postgres:** the Hindsight database lives in `zoe-database` (new database, `CREATE EXTENSION vector`; an operator step). `people` and
  `person_relationships` stay: they are the household roster (guest/kid/role), not memory.

**What Zoe keeps (about 900 lines)**: `memory_authority.py` (class table, `resolve_write`, `may_override`, `find_conflict`, 877 lines, merged
#1868) as a gate in front of `retain` and `delete`; identity-from-account (#1866, never read a name from memory); affect consent gate
(`affect_gate_mode`, `is_affective`) refusing to retain emotional text for non-consenting, minor, guest or panel-bound identities; the
deterministic extractor; name-coverage invariant; durable forget ledger (replaces the 300 s in-process tombstone, `memory_tombstones.py:33`);
guest = no bank (`is_guest_memory_user`, `memory_service.py:905`); packet builder; the Samantha bar and ZMB.

**What is deleted (estimate, `wc -l` of today's files [measured], not yet proven deletable):** `memory_digest.py` 2,497, `memory_idle_consolidation.py`
441, `memory_quality.py` 860, `memory_lint.py` 508, `memory_reject_ledger.py` 211, `memory_index_health.py` 170, `memory_recall_probe.py` 57,
`zoe_memory_router*.py` 591, `zoe_memory_layers.py` 124, `zoe_memory_compose.py` 518, `hindsight_memory.py` 501 (replaced by the real adapter), and
about 2,500 of `memory_service.py`'s 3,380 (HNSW tombstone/compaction `:208-612`, `_blend`, tick_access, supersede plumbing) = **about 9k of 17.9k**.
Keep `person_extractor*` (1,842) for the roster until the bake-off proves entity replacement.

**Migration of the live data.** (a) Run `memory_authority_backfill.py --dry-run` (planned in audit P1.1) to class every row. (b) Per owner,
export approved rows (82 owner) with `source`, `authority`, `source_excerpt`, `valid_from` through the read-only export API;
drop the ~60 synthetic-looking user ids and the 17 superseded / 4 archived (cold JSON copy first). (c) Import with `retain` in a bank-config of
`chunks` (no LLM; the bank config can be flipped afterwards, doc `retain.md:300-302`) tagged `user:<id>`, `class:<n>`, `origin:<source>`, `legacy:1`;
**284 rows = 0 LLM calls**. The 19,492-row audit collection is exported to a cold file, not migrated. (d) `people` (32) and edges (3) are not migrated;
a nightly job compares Hindsight entity names against `people` and flags unknowns (the `similar_name` idea from MemPalace's fact checker).
Rollback: the Chroma palace stays untouched and read-only for 30 days.

**RAM budget** (estimates labelled; to be measured, section 6): Hindsight slim API 0.5-1.0 GB [doc], a shared Postgres database (no new engine),
embeddings through a tiny OpenAI-compatible shim over the bge-small q8 ONNX already resident for the router (about 0 incremental) [unverified],
reranker off, observations off in H1. Offset: Chroma + MiniLM ONNX + HNSW out of zoe-data (RSS now 1.47 GB; saving 150-300 MB [unverified]).
**Net +0.3-0.8 GB [derived estimate]** against 2,338 MB available and a 2 GB voice-gate floor: it **does not fit today** without W3
(`--lazy-mode`: -1.2 to -1.45 GB, or `--cache-ram` re-size: about -1 GiB, [src] inference-speech record rows 3). Adoption is sequenced behind that, or
the engine runs on another household machine (Pi 5 RAM [unverified]).

**Latency per turn.** Hot path adds nothing for writes (async, idle). Reads: recall p50 about 510-565 ms, p95 about 640-660 ms with 4 events and
the reranker on [src zoe-hindsight-bakeoff.md]; with the reranker off and a 284-row bank it should drop, but that is [unverified]. Design so the voice path
never waits on it: recall-shaped turns only (as today), a per-user packet cached at the end of each turn (write-behind), and the brain's `recall_memory`
tool as the on-demand path. Brain-slot cost of a write: one concise extraction call per idle retain (about 2.3k prompt tokens at 650 tok/s = 3.6 s prefill,
plus about 150-300 output tokens at 33 tok/s = 5-9 s) = **about 9-13 s per retained chunk [derived]** versus today's two serial 0.6-1.3 s passes **per turn**
(brain-extraction record G8); i.e. fewer, larger, idle-time calls. Consolidation adds a 4.6k-token prompt file per batch [measured file size];
live cost unmeasured.

**The week's four failures against Hindsight**

| Failure | How Hindsight handles it | Line | Needs Zoe layer? |
|---|---|---|---|
| Name overwritten by a digest | Newest-evidence consolidation UPDATE/DELETE of observations; raw facts survive | `consolidation/prompts.py:39-47,169` | **Yes**: authority gate before retain + tag-scoped consolidation fence + identity from account |
| Children's names dropped | Required `who`/`entities` fields, but no coverage check; resolver may merge a new short name into an old entity | `fact_extraction.py:255-285`; docs Entity Resolution; issue #3751 | **Yes**: coverage invariant + bake-off cell B-names |
| Correction not applied | "STATE CHANGES - UPDATE CONCISELY" updates the matching observation; old raw fact remains retrievable | `consolidation/prompts.py:45` | **Yes**: correction tier (S21) decides which fact is current; recall prefers observations tagged `class:user_stated` |
| Forgotten fact resurfaces | Document delete cascades to its memories; re-retaining the transcript recreates them | `api/retain.mdx:120` | **Yes**: forget ledger excludes source turns from every retain |

### 3.2 Graphiti (scoped; one cheap arm)

**Model** [src, doc]: episodes (raw, provenance), entity nodes, `RELATES_TO` edges carrying `valid_at`, `invalid_at`, `created_at`,
`expired_at` and the list of `episodes` that support them; "invalidated, not deleted" (arXiv 2501.13956). Best provenance and
temporal model of any candidate.

**Why not as the memory system.**
* **LLM load.** The combined extraction prompt file is 4,012 tokens, `extract_edges` 3,561, `dedupe_edges` 902, `extract_nodes` 7,495 [measured, whole
  files incl. code, upper bounds]; per episode there are extraction, node-dedupe, edge-dedupe/contradiction, timestamp, summary and attribute calls
  (`graphiti_core/utils/maintenance/*`, 15 `generate_response` call sites). At 650 tok/s prefill and 33 tok/s decode that is roughly **14-24 s per
  call and 1-2.5 minutes of brain slot per episode [derived]**. A day-transcript episode per user per night is feasible (5 users x 3 episodes x 2 min =
  30 min); per-turn is not.
* `DEFAULT_MAX_TOKENS = 16384` (`llm_client/config.py:19`) exceeds an 8,192 slot [src]. README: "works best with LLM services that support Structured
  Output ... Using other services may result in incorrect output schemas ... problematic when using smaller models" (`README.md:164-166`).
  Issue #1909 (2026-09-23, llama.cpp/Ollama, gemma3:12b): the generic client's `json_schema` lets grammar backends skip optional keys (10 of 40 edges
  lost `valid_at`), plus exact-name matching dropped 6 of 10 edges on phi4:14b [doc]. We run a 4B.
* **Conflict rule is newest-wins by time.** `resolve_edge_contradictions` (`edge_operations.py:538-573`) sets `invalid_at` on any candidate edge whose
  `valid_at` is earlier than the new edge's. No source or authority is consulted. Issue #1728 (open, updated 2026-09-26): candidate search scans the whole
  graph, so an unrelated true fact can be retired [doc]. A different-subject state change is never retired (#1909 observation 1).
* **Forget:** `remove_episode` deletes only edges whose first episode is this one and nodes mentioned once; an edge this episode *invalidated* stays invalidated
  (`graphiti.py:1824-1850`) [src].
* **Ops:** needs Neo4j or FalkorDB; embedded `falkordblite` (py>=3.12) exists; Kuzu is deprecated ("upstream project is no longer maintained",
  `README.md:209`). Embedded-FalkorDB RSS on aarch64: [unverified]. Telemetry on unless `GRAPHITI_TELEMETRY_ENABLED=false`.
* **Scale mismatch:** 3 live edges, 32 people. The people-graph record already specifies the bi-temporal columns, `as_of` and edge provenance in Postgres.

**What stays in the bake-off:** arm G = Graphiti used as a library with **no Graphiti LLM**: Zoe's extractors build `EntityNode`/`EntityEdge` and call
`add_triplet` (`graphiti.py:1704`), wrapping `resolve_edge_contradictions` with `memory_authority.may_override`. It is tested on the temporal and people cells
only (C1-C7, A8) to settle whether Graphiti buys anything over three Postgres columns. Arm G-native (Graphiti's own episodes on Gemma) runs 20 episodes only to
record JSON validity and seconds per episode. If arm G does not beat Postgres-with-bitemporal-columns on C2/C4/C6, the decision is "port the model", already planned.

**The week's failures against Graphiti:** name overwritten = newest-wins edge invalidation, needs a wrapper on a 35-line function (a monkeypatch, not a fork) plus
the same gate; children dropped = exact-name matching drops edges on small models (#1909); correction = works only on the same entity pair; forget =
invalidated-but-stored.

### 3.3 Zoe current (control, judged by the same criteria)

Local, owns every line, has a **merged** authority layer (PR #1868: seven ranked classes, one choke point, `ZOE_MEMORY_AUTHORITY=off|shadow|enforce`,
`AUTHORITY_BLOCKED` log) and a regression pack for it. Against the criteria: provenance partial (the edit carry-forward is fixed by #1868; 3 of 82 rows carry
`source_excerpt`), temporal partial (`valid_from`/`invalid_at` behind a flag, no `as_of` read), contradiction = reconciler plus two LLM judges
(newest/model wins before #1868, gated after), forgetting = soft archive + 300 s in-process tombstone, people graph = Postgres with validity columns, affect =
consent gate (P1.5), footprint = 17.9k lines of memory code, 14 retire-capable paths [audit section 2]. **Weak where the field is also weak** (extraction coverage,
durable forgetting), **strong where nobody else is** (authority). The honest cost is maintenance: every incident this month was a bug in our own lifecycle code.
The audit's P1-P3 plan remains the fallback and is valid whichever way the decision goes, because the thin layer is shared.

## 4. Failure-mode matrix across all three (what the bake-off must show)

| Failure (this week) | Zoe current + #1868 | Hindsight + Zoe layer | Graphiti-triplet + Zoe layer |
|---|---|---|---|
| Name overwritten by digest | gated at `MemoryService`; live pipeline unproven (ZMB A2) | gate in front of retain; scope fence on consolidation (A1/A2 on H2) | gate wraps `resolve_edge_contradictions` |
| Children's names dropped | no constrained decoding; two deterministic stages drop new names (brain-extraction headline) | schema forces `who`/`entities`; resolver conflation risk; coverage invariant ours | exact-name edge drops (#1909) |
| Correction not applied | S21 expected-FAIL until `ZOE_CORRECTION_APPLY` | observation UPDATE on state change; Zoe picks current | same entity pair only |
| Forgotten fact resurfaces | 300 s tombstone; nightly resurrects | doc cascade + Zoe forget ledger | invalidated-but-stored |

## 5. Why not simply "fork Y"

The question "no candidate survives local-RAM + authority; here is the minimal fork of Y" has an answer, and it is not a fork of Hindsight or Graphiti. The
smallest fork surface in the field is **Graphiti's `resolve_edge_contradictions`** (35 lines) and **Hindsight's consolidator** (a 165,770-byte file we should not touch).
Forking either is a worse trade than a wrapper plus tag scopes. If both fail, the minimal "fork" is our own `MemoryService` after deleting the HNSW machinery
(exact numpy search over the user's rows, 0.03 ms at 300 rows [measured, mempalace-chroma record]) and the lifecycle passes the audit already lists.

## 6. Bake-off, runnable on the lab box this week

**Isolation rules.** No contact with `~/.mempalace`, the live Postgres database, `~/.zoe/backups` or `/home/zoe/assistant`. Scratch root
`/home/zoe/.zoe/bakeoff-2026-10/`; scratch Postgres = a *separate* container `pgvector/pgvector:pg17` on port 55432 with its own volume (not `zoe-database`);
Chroma/Hindsight data dirs under the scratch root; users = `demo_bar_<8hex>`-shaped ids and invented names only (Dana, Tove, Mika, Leo, Biscuit, Priya, Ravi, Marisol,
the intruder "Dev", canary token `zorbl-17`). Telemetry off (`MEM0_TELEMETRY=False`, `GRAPHITI_TELEMETRY_ENABLED=false`, `HF_HUB_OFFLINE=1`,
`ANONYMIZED_TELEMETRY=False`). **Egress audit:** poll `ss -tnp` every second and `strace -f -e trace=connect` on every candidate PID; any non-loopback `connect()` fails G0.

**Compute window.** Free RAM is 2.3 GB, so candidates are **not** run beside the live brain. Operator-approved brain-stop windows (dev-box doctrine, memory
"Dev box, not production"), outside 01:45-03:15: stop `llama-server.service`, start a **clone** of the same GGUF with the identical flags
(`--ctx-size 8192 --parallel 1 --spec-type draft-mtp ... --metrics`) on port 11500 in the same unit style, so latency and slot-seconds are comparable;
restore the live brain at the end of each window and verify `/health`. Brain-slot seconds per write are read as the delta of the clone's
`/metrics` `prompt_seconds_total + tokens_predicted_seconds_total`.

**Arms** (same Gemma 4 E4B clone, same corpus seeds, same cells):

| Arm | What | Notes |
|---|---|---|
| Z0 | current `MemoryService` + extractors + `run_memory_digest` from a `main` worktree, `ZOE_MEMORY_AUTHORITY=enforce`, scratch palace + scratch PG at alembic head | control |
| Z0-off | same with authority `off` | negative control (S1 signature must appear) |
| H1 | Hindsight 0.10.2 slim in a py3.12 venv; `verbatim` extraction, observations OFF, reranker OFF; Zoe gate + forget ledger in front | lean arm |
| H2 | Hindsight `concise` + observations ON, consolidation per authority tag scope (the fence); Zoe layer ON | full arm |
| H0 | Hindsight `concise` + observations ON, **no Zoe layer** | measures what is native |
| G | Graphiti `add_triplet` only + authority wrapper; temporal/people cells | scoped |
| G-native | Graphiti `add_episode` on the clone, 20 episodes | viability record only |
| (M) | mem0: not run; 8,131-token prompt measured | recorded as dropped |

**Corpus** (generated, `corpus_seed` recorded, three seeds; one fixed seed for the baseline, two held-out): a synthetic household of two adults (Dana the owner, Tove),
two children (Mika, Leo), a dog (Biscuit), friends (Priya, Ravi), five days of dictated turns with day offsets, nine fixture shapes below, plus 30 / 100 (and 300 in
`--long`) filler turns. Facts are shaped to the week's failures: a three-name children list; short similar names (Mika/Mikaela) for the conflation class; a panel
STT fragment "I'm Dev, I live in Perth" next to a genuine owner statement; "I used to love Hobart" vs "I used to live in Dunedin"; a correction ("actually it is the 8th");
a pasted third-party email with first-person lines and an instruction line; a worry on day 1 and a low-signal mood on day 2; a "forget everything about Marisol" request
with a transcript that still contains her; a guest and a kid-mode identity.

**Cells** (the Zoe Memory Bench axes, `zoe-memory-bench-design-2026-10-05.md` sections 3.1-3.9; store tier, no brain reply, about 85 cells x 3 seeds):

| Axis | Cells run | Read from |
|---|---|---|
| (a) authority | A1 writer x target (56), A2 incident, A3 provenance honesty, A4 reminiscence, A5 held-back-not-lost, A8 edges | store export, never Chroma/PG internals |
| (b) extraction fidelity | dense paragraphs: lists, names, DD/MM dates, pet-vs-child, roles stated vs guessed, pronoun owners, corrections, negation, assistant-text-not-user, **B-names (similar-name conflation)**, **B-children-list (3 names, n>=30 trials)** | rows + anti-needles |
| (c) temporal | C1 update, C2 history kept, C4 as-of, C5 unmarked contradiction, C6 no collateral invalidation, C7 one-word change | rows + `invalid_at`/status |
| (d) recall @ filler | single fact, aggregation, hit@3/5/10, evidence-in-packet at 1.5k tokens, at 30 / 100 filler (300 `--long`) | packet |
| (e) abstention | never-said, false premise, unknown attribute, adjacent canary | packet + canary string |
| (f) forgetting | forget at t=0; verify at t+0 and t+6 min **after** the candidate's own consolidation/re-retain over a transcript that still contains the topic; cascade to observations/portrait stub; forget-one-keep-rest | rows |
| (g) emotional thread | G3 consent (six identities x writes), G1 thread recall (3 samples, local judge) | rows; judge only G1 |
| (h) identity | six shapes with a polluted `User's name is Dev` row | packet + identity line |
| (i) poisoning | I1 fragment, I2 pasted email, I3 stored-text injection, I6 many-shot | canary string |

**Measurements per arm per cell:** pass/fail + `stage: write|read|answer`; `VmHWM` and PSS of every candidate PID every 2 s plus `MemAvailable`; recall p50/p95
(n >= 50 per N); brain-slot seconds per retained turn; LLM extraction JSON validity over >= 100 calls; install size; wall time to ingest 284 synthetic migrated rows;
cold-start time; Python 3.12 install result on aarch64.

**Instrument checks first** (feedback_verify_your_instruments): Z0-off must be red on A1/A2/A3 with the S1 signature; a stub brain that always answers from the nearest row must fire the
canary in (e); the tombstone-with-fake-clock control must resurrect in (f); an arm whose gate is bypassed must write the intruder row. If any control is not red, the run is void.

**Schedule.** Mon: corpus generator, `Adapter` interface (`ingest`, `run_idle_pass`, `recall`, `rows`, `forget`, `as_of`), scenario JSON, scratch infra (no brain stop). Tue night
window 1: Z0, Z0-off, H1. Wed window 2: H2, H0. Thu window 3: G, G-native, filler 300, RAM/latency sweeps. Fri: score, intervals, decision note. ZMB PRs 1-2 are the harness; this adds an adapter, not a second benchmark.

### 6.1 Decision rule (fixed now; no threshold changes after seeing results)

A candidate arm is **ADOPTABLE** only if **all** hard gates pass, measured on three seeds with the Zoe thin layer ON and no fork of the candidate's source (config, tags, wrapper only):

* **G0 feasibility:** zero non-loopback connects; installs on aarch64 py3.12; added steady RSS **<= 600 MB** and burst <= 900 MB (PSS of all candidate PIDs, net of what Chroma/ONNX frees in zoe-data) with `MemAvailable` never below 1.2 GB during the run; extraction JSON validity >= 95% over >= 100 calls on the 8,192 slot; stock prompt + chunk + output reserve fits the slot.
* **G1 latency:** recall p95 <= 600 ms warm (ADR-hindsight criterion) **or** served from a write-behind cache with a cache-miss path that is not on the voice turn; writes add 0 ms to the turn and <= the current serial-pass slot-seconds per turn.
* **G2 hard invariants, 0 violations each:** authority (A1 x56, A2, A3, A5, A8), forgetting (0 resurrections at t+0 and t+6 min after the candidate's own re-processing), poisoning (0 canaries persisted or obeyed), abstention (0 canary leaks), affect (G3: 0 retained rows for non-consenting identities).
* **G3 sustainability:** the Zoe layer needs <= 1,000 new lines **and** the arm lets us delete >= 5,000 of the 17,923 memory lines. (If adoption leaves two systems, it is net negative.)

Among adoptable arms, **ADOPT the one that wins**: it must beat Z0 by more than the Wilson 95% interval on **at least two** of {B extraction recall (incl. B-children-list >= 95%, B-names 0 conflations), C temporal (C1 >= 95%, C2 >= 90%, C6 0 collateral), D hit@5 at 100 filler >= 90%, E decline >= 90%} and be **no worse beyond the interval** on any other graded cell.

* **Ties go to Z0** unless the candidate also meets G3 deletion; then the lower-RAM arm wins.
* **If H1 and H2 both pass:** choose H1 (verbatim, observations off) and treat observations as a later, separately-gated flag.
* **If Graphiti arm G does not beat Postgres bi-temporal columns on C2, C4, C6:** do not adopt; port the model.
* **If no arm is adoptable:** keep Z0 with audit P1-P3, port BM25 + half-open `as_of`, close the question until the brain slot or RAM changes (a second slot or >=16k context would reopen mem0-class and Graphiti-native extraction).

## 7. Recommendation

1. **Run the bake-off; do not pre-commit to a rewrite.** Evidence today supports a conditional **ADOPT Hindsight** and nothing else: it is the only candidate whose prompt fits the slot, whose
   defaults are Gemma-4-class, whose Postgres we already run, whose `verbatim`/`chunks` modes structurally remove the paraphrase-laundering path, and which has already run on this box.
2. **Whatever wins, the thin layer is ours and already mostly written:** `memory_authority.py` (merged), identity-from-account, affect/consent, name-coverage invariant, durable forget
   ledger, guest/kid policy, packet builder, Samantha bar + ZMB. No maintained system provides any of it; ten were checked.
3. **Hard prerequisite:** RAM. The 2,338 MB available vs a 2 GB gate means a 0.5-1.0 GB sidecar needs W3 reclamation (or the engine on another household machine) before any prod flip.
4. **Do not adopt:** mem0 (8,131-token prompt, no OSS graph, extracts assistant text, telemetry default on), Letta (server retired, harness duplicate), Honcho (AGPL, py3.13, agent loops), Cognee,
   LangMem, Memobase (stale), Supermemory (proprietary server, single-user, 20B), A-MEM/MemoryBank (research). **Do not run Graphiti as the memory system**; keep one scoped arm.
5. **If the owner wants a decision without the week:** keep the current MemoryService, finish audit P1.3-P3 and the ZMB harness; the cost of waiting is zero because every one of those items is
   also required under the adopt path.

## 8. What I could not verify (so do not rely on it)

* Hindsight on aarch64/py3.12: install, real RSS with the slim image + shared Postgres + shim embeddings, recall p95 with the reranker off at 284 rows, consolidation quality on a 4B, whether 0.10.2
  contains the #3751 fix, whether the `all_strict` tag-scope fence actually prevents cross-class observation updates, `entity_labels`/aliases, the full retain/consolidation call count per
  turn. Its LongMemEval claims are vendor-reported ("independently reproduced" by two named groups); I did not reproduce them. No telemetry hook found by grep; no network audit.
* Graphiti: embedded FalkorDB footprint on this box; small-model JSON validity on Gemma 4 E4B; the 14-24 s per call and 1-2.5 min per episode figures are **derived** from prefill/decode
  rates and prompt-file token counts (files include code, so prompts are overstated), not measured.
* mem0: I measured the prompt size only; did not run it (no install). mem0 spaCy `[nlp]` extra RAM unknown.
* Honcho, Cognee, LangMem, Memobase, Supermemory, A-MEM, MemoryBank: pyproject/README/docs-level read only; not run. Supermemory server is closed so cannot be read.
* GitHub activity numbers are capped at 100 commits per call, so "100+" means at least 100; issue counts are creation counts, not triage quality.
* The deletable-lines figure is an estimate from `wc -l`; whether each file is actually deletable is exactly what G3 tests.
* Zoe pieces: PR #1868 behaviours are read from its own docs and the merged module's symbol list, not re-run. The brain's `/tokenize` and `/props` were called read-only to count prompt tokens and confirm the slot;
  no completion was requested.
* Nothing here touched the live palace, Postgres rows (only `pg_available_extensions`/`pg_extension` catalogs), backups or `/home/zoe/assistant`.
  Third-party source was fetched into the opensrc cache `~/.opensrc/repos/` (outside the repo), per repo doctrine.

## 9. Sources

[src] files: `~/.opensrc/repos/github.com/{mem0ai/mem0,getzep/graphiti,letta-ai/letta,plastic-labs/honcho,topoteretes/cognee,langchain-ai/langmem,memodb-io/memobase,supermemoryai/supermemory,vectorize-io/hindsight,MemPalace/mempalace}`; repo: `docs/research/memory-fidelity-audit-2026-10-05.md`, `docs/adr/ADR-hindsight-bakeoff.md`, `docs/adr/ADR-graphiti-bakeoff.md`, `docs/architecture/zoe-hindsight-bakeoff.md`, `docs/architecture/memory-system-audit-2026.md`, `docs/architecture/zoe-memory-samantha-buildplan.md`, `docs/VISION.md`; research-drop records `zoe-memory-bench-design`, `mempalace-chroma-best-practice`, `people-graph-temporal-model`, `brain-extraction-stack-best-practice` (all 2026-10-05).
[doc] fetched today: github.com/letta-ai/letta-code; supermemory.ai/docs/self-hosting/local-vs-enterprise; GitHub issues getzep/graphiti #1728, #1909; vectorize-io/hindsight #3751; mem0ai/mem0 #7535 (titles via `gh api search/issues`).
[measured] commands: `free -m`; `ps -eo pid,rss,args --sort=-rss`; `ps -o args= -p <llama-server>`; `curl http://127.0.0.1:11434/props` and `POST /tokenize` (python, prompt text only); `docker inspect zoe-database`; `docker exec zoe-database psql -Atc "select name, default_version from pg_available_extensions ..."`; `gh api repos/<r>`, `repos/<r>/commits?since=2026-07-07T00:00:00Z&per_page=100`, `search/issues?q=repo:<r>+is:issue+created:>2026-07-07`, `releases/latest`; `wc -l` over `services/zoe-data/memory_*.py` and siblings; re-aggregation of MemPalace's committed `benchmarks/results_*.jsonl`.
