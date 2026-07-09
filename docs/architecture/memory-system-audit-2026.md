---
type: architecture-audit
status: reference
date: 2026-07-09
scope: Zoe memory subsystem vs the 2026 agent-memory field + best-practice gap analysis
---

# Zoe Memory System — Audit & Field Comparison (2026)

**Verdict:** Zoe's memory system is *mature and closely aligned with 2026 best
practices* — it already implements the field's headline technique (multi-signal
retrieval) and Mem0's ADD/UPDATE/SKIP reconciliation, and it is **local/private
and governance-gated**, which the hosted leaders (Mem0, Zep) are not. It even
has the two things a first read mistook for gaps — **proactive contradiction
detection** and **active consolidation** — already live in the nightly digest
(see §4 correction). The one genuine gap is **measurement** (no standardized
benchmark run yet); the rest is operational (keep the nightly cycle healthy;
close the W0 identity issue).

Grounded in a 2026-07-09 read of the code (26 memory modules) + web research
(sources at the end). Every Zoe claim carries a `file`/`file:line`.

---

## 1. Zoe's memory architecture (what exists)

Zoe follows the layered design in [ADR-zoe-memory-layer](../adr/ADR-zoe-memory-layer.md):
working context (disposable) → **PostgreSQL** (canonical truth) → **MemPalace**
(episodic semantic recall via `MemoryService`) → **relational graph** → optional
**reflective** memory (Hindsight) → **governance** (admission gates / Multica).

| Layer | Modules | What it does |
|---|---|---|
| **Read/write surface** | `memory_service.py` (1801L) — sole gatekeeper over Chroma; `zoe_memory_compose.py`, `user_portrait.py` (recall packet / dossier) | vector store + the 7-signal `_blend` ranker + idempotency + expiry |
| **Extraction** | `memory_extractor.py`, `person_extractor.py`, `person_extractor_llm.py` | conversational → structured facts; regex + LLM fan-out |
| **Write quality** | `memory_quality.py` (425L), `zoe_memory_admission.py`, `memory_gate.py` | mem0-style storable-fact gate + **ADD/UPDATE/SKIP** reconciliation |
| **Relational graph** | `relationship_graph.py`, `person_merge.py`, `person_health.py` | recursive-CTE neighbour traversal, entity resolution, health scoring |
| **Temporal** | `person_relationships` (`valid_from/valid_to/superseded_by`, migration 0015) | current-edge-only traversal + supersession on relationship change |
| **Reflective / dream cycle** | `memory_digest.py` (1628L), `memory_idle_consolidation.py`, `hindsight_memory.py` | idle LLM digest, live→idle→store consolidation, reflective bake-off |
| **Hygiene / scoring** | `memory_lint.py` (report-only), `memory_importance.py`, `memory_metrics.py` | stale/dup/contradiction lint, importance + salience, Prometheus |
| **Governance** | `zoe_memory_router*.py`, `zoe_memory_layers.py`, `zoe_memory_contract.py`, `zoe_multica_memory_admission.py` | portable contract, routing policy, review-gated self-evolution writes |

The retrieval ranker (`memory_service._semantic_search._blend`) fuses **7
additive signals**, live: semantic distance · confidence · time-decay (70-day
half-life) · salience (`access_count`) · keyword/BM25 overlap · temporal-recency
· preference/importance · **graph-adjacency** (the GBrain increment, `#1199`).

---

## 2. The 2026 field

| System | Core idea | Benchmark (leading) | Hosted? |
|---|---|---|---|
| **Mem0** (48k★, $24M A) | extraction + ADD/UPDATE/DELETE/NOOP reconciliation; multi-signal retrieval (semantic + BM25 + entity); Mem0g graph variant | LoCoMo ~92.5 / LongMemEval ~94.4 (own report); LOCOMO 67% (older) | hosted default |
| **Zep / Graphiti** | temporal knowledge graph; bi-temporal edge invalidation | LongMemEval 71.2 (GPT-4o) | OSS engine + hosted |
| **Letta / MemGPT** | LLM-as-OS: main-context / recall / archival; model pages memory via tools; editable "memory blocks" | (paper-origin; long-horizon) | OSS |
| **LangMem** | LangChain-native memory SDK | — | SDK |
| **OMEGA / Mastra** | observational memory | LongMemEval 94–95 | — |

**Benchmarks:** **LOCOMO** (1,540 Qs: single/multi-hop, open-domain, temporal)
and **LongMemEval** (500 Qs, ICLR-2025: user/assistant recall, preferences,
knowledge-updates, temporal, multi-session). Both measure *retrieval quality*
and *token efficiency* (leaders answer in ~6.8–7k tokens vs ~26k full-context).

**Field consensus:** *retrieval strategy matters more than storage;
multi-strategy retrieval (semantic + keyword + graph + temporal) is the robust
default.* **Named failure modes** (Mem0 2026 report): memory **staleness**
(confident-but-wrong stale facts), **cross-session evolution** (replacing vs
modelling change), **identity resolution** (unstable `user_id`), and — called out
as unsolved industry-wide — **"no robust mechanism for detecting/resolving
conflicting high-confidence memories."**

---

## 3. Best-practice scorecard — Zoe vs the field

| Best practice | Zoe | Evidence |
|---|---|---|
| **Multi-signal retrieval** (semantic + keyword + graph + temporal) | ✅ **ahead of most** — 7 signals incl. graph adjacency | `memory_service.py:_blend` |
| Write-time reconciliation (ADD/UPDATE/SKIP/dedup) | ✅ implemented ("mem0 ADD vs UPDATE idea") | `memory_quality.py:215,350` |
| Same-attribute contradiction → supersede stale row | ✅ (partial contradiction handling) | `memory_quality.py:399` |
| Idempotent writes (no double-store) | ✅ `(user_id, user_turn_id)` keys | `memory_service.py:22` |
| Temporal edges + supersession | ✅ live (migration 0015, current-edge-only) | `relationship_graph.py`, `#1198`+ |
| Salience / importance scoring | ✅ `access_count` hotness + importance | `memory_service.py:1241`, `memory_importance.py` |
| Time-decay / freshness | ✅ 70-day half-life in ranking | `memory_service.py:1214` |
| Identity/scope isolation (`user_id`, visibility) | ✅ owner-scoped everywhere (fuzz-verified in tests) | `_semantic_search where=`, graph BFS |
| Async writes (off the response path) | ✅ memory passes spawned in background | voice_tts `_spawn_bg` |
| Idle enrichment ("dream cycle") | ✅ LLM digest + consolidation | `memory_digest.py`, `memory_idle_consolidation.py` |
| Reflective/lessons memory | ✅ evaluated (Hindsight bake-off) | `ADR-hindsight-bakeoff.md` |
| Evidence-gating + governance | ✅ **ahead** — admission gates, Multica review | `zoe_memory_admission.py` |
| **Local / private** | ✅ **differentiator** (Mem0/Zep default hosted) | rock; `ADR-hindsight-bakeoff` rejects cloud LLM |
| **Standardized benchmark (LOCOMO / LongMemEval)** | ❌ **gap** — only a bespoke 40/40 eval | — |
| **Proactive contradiction detection** (idle LLM-judge + supersede) | ✅ **live** — the nightly `memory_digest` LLM-judges each new fact vs existing person-facts and supersedes conflicts (`review(decision="edit")`) | `memory_digest.py:219,443`, `zoe-dreaming.timer`, `MEMORY_DIGEST_ENABLED=true` |
| **Active forgetting / consolidation** (prune stale/low-score) | ✅ **live** — `sweep_soft_archive` (score = conf·decay + log access; age ≥ 30d, score < 0.02) runs inside the digest; reversible soft-archive | `memory_service.py:792`, `memory_digest.py:850`, `ZOE_IDLE_CONSOLIDATION_ENABLED=1` |
| Post-retrieval reranker (cross-encoder) | ❌ deliberately omitted (RAM budget) | see §4 |

---

## 4. Gaps & prioritized recommendations

1. **Run a standardized benchmark (highest value).** Zoe cannot say where it
   sits vs Mem0 (67–92%) / Zep (71%) without LOCOMO or LongMemEval. Build a
   **local LongMemEval-style harness** (demo users, the Flue brain as judge/actor,
   the real `MemoryService` read path) reporting P@k/R@k by category
   (single-hop, multi-hop, temporal, preference, multi-session). This turns
   "we think recall is good" into a number and exposes the weak categories.
   *(A first slice ships with this audit — see §5.)* RAM-safe: read-path + a
   small Q set.
   > **Correction (2026-07-09, second pass):** the original audit listed
   > contradiction detection and active consolidation as gaps. A closer read
   > found **both are already implemented AND run nightly** — the `memory_digest`
   > LLM-contradiction-judge + supersede (`memory_digest.py:219,443`) and
   > `sweep_soft_archive` consolidation (`memory_service.py:792`, wired at
   > `memory_digest.py:850`), fired by `zoe-dreaming.timer` (last run confirmed
   > ~19h before this note) under `MEMORY_DIGEST_ENABLED` + `ZOE_IDLE_CONSOLIDATION_ENABLED`.
   > So the two mid-tier "gaps" are actually **live features**; the scorecard is
   > corrected above. This strengthens the verdict: on the feature axis Zoe is
   > effectively complete — the remaining work is *measurement* and *operations*,
   > not new memory machinery.
2. **Verify the nightly cycle keeps firing + widen its coverage.** Since
   contradiction-resolution and consolidation live in the nightly digest, their
   value depends entirely on `zoe-dreaming.timer` staying scheduled and healthy
   (it is, as of this audit). Watch it; consider surfacing digest outcomes in
   `memory_metrics`. Optionally extend the contradiction judge beyond person-facts
   to high-salience *attribute* facts (employer/city).
3. **Staleness guards on high-confidence facts.** The #1 field failure mode:
   confident-but-outdated facts (job/location changes). Zoe's temporal edges
   cover *relationships*; extend supersession-on-change to high-salience
   *attribute* facts (employer, city) so a new value demotes the old at read.
4. **Identity resolution hardening.** The field flags unstable `user_id`; this is
   Zoe's known W0 issue (voice turns resolving as `voice-guest`, FK-failing).
   Close W0 — memory keyed to the wrong/guest identity is the worst pollution.
5. **Reranker — keep omitted.** A resident cross-encoder (Cohere/Qwen3-Reranker)
   would help but **breaks the RAM budget** (the 2026-07-08 swap incident). The
   7-signal additive `_blend` is the RAM-free substitute; revisit only after the
   W0 RAM-reclamation workstream.

**Not gaps (Zoe is right to differ):** hosted embeddings, cloud memory LLMs, and
BEAM-scale (1M–10M token) tuning are irrelevant to a local, single-household
deployment.

---

## 5. Testing expansion (shipped with this audit)

The audit's #1 gap is measurement, so this lands the first standardized slice
plus adversarial coverage of the named failure modes, all **ci_safe / demo-user**:

- `tests/test_memory_benchmark_recall.py` — a LongMemEval/LOCOMO-*style* local
  harness over the real `_semantic_search` ranker: **P@k / R@k by category**
  (single-hop, multi-hop-via-graph, temporal-recency, preference), with a
  no-regression floor. The scaffold to grow toward the full benchmark.
- Adversarial memory tests for the field's failure modes: **staleness /
  supersession**, **same-attribute contradiction**, **temporal ordering**, and
  **cross-user scope isolation** — the exploratory battery from 2026-07-09
  (which found the `_ensure_db` + resolver bugs, `#1202`) made durable.

Full LongMemEval integration (500 Qs, brain-as-judge) is a follow-up build; this
establishes the harness + categories.

---

## Sources (2026 web research)

- Mem0, *State of AI Agent Memory 2026* — https://mem0.ai/blog/state-of-ai-agent-memory-2026 (LOCOMO/LongMemEval scores, failure modes, multi-signal retrieval, ADD/UPDATE/DELETE)
- AgentMarketCap, *Agent Memory at Scale 2026: Letta, Zep, Mem0, LangMem* — https://agentmarketcap.ai/blog/2026/04/10/agent-memory-vendor-landscape-2026-letta-zep-mem0-langmem
- Vectorize, *Best AI Agent Memory Systems in 2026* — https://vectorize.io/articles/best-ai-agent-memory-systems
- Atlan, *Best AI Agent Memory Frameworks 2026* — https://atlan.com/know/best-ai-agent-memory-frameworks-2026/
- TeleAI, *Awesome-Agent-Memory* (systems/benchmarks/papers) — https://github.com/TeleAI-UAGI/Awesome-Agent-Memory
- LongMemEval (ICLR 2025) and LOCOMO — long-term-memory agent benchmarks

Internal: [ADR-zoe-memory-layer](../adr/ADR-zoe-memory-layer.md),
[ADR-hindsight-bakeoff](../adr/ADR-hindsight-bakeoff.md),
[ADR-graphiti-bakeoff](../adr/ADR-graphiti-bakeoff.md),
[ADR-relationship-memory](../adr/ADR-relationship-memory.md). (The GBrain
graph-into-recall design that this audit builds on lands with PR #1197.)
