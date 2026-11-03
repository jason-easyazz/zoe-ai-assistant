---
type: research
title: "Samantha Initiative lane C: memory, continual learning, autobiographical memory and personal growth (Phase 2.2 + Phase 6)"
date: 2026-11-03
status: lane report
---

# Lane C: memory and continual learning, autobiographical memory and personal growth

Scope: Phase 2.2 (memory and continual learning) and Phase 6 (autobiographical memory, personal growth). Research only; no
production change, no live-store read (architecture and counts already published in the repo docs only), no tests run.

Evidence labels used throughout: **[Proven]** = measured on Zoe by a repo record, or a result reproduced by several independent
parties; **[Experimental]** = a paper or project claim not reproduced here, or measured elsewhere on different hardware or
benchmarks; **[Speculative]** = reasoning, no evidence yet. **[derived]** = arithmetic on a cited number.

**What this lane did and did not inspect.** No third-party source tree was read in this lane. Project facts (licence, stars, last
push) come from the GitHub REST API on 2026-11-03 (`gh api`); paper claims come from the arXiv abstract pages and search
summaries, so every paper number below is the authors' own and unverified by me. Zoe-side facts come from the repo docs named in
section 2 and were not re-measured. Where a prior Zoe record already measured an engine (the 2026-10-08 bake-off), that record is the
evidence and is cited, not repeated.

**A correction to the brief.** The brief says Hindsight is "conditionally adopted behind an authority layer". That was the 10-05
position. The pre-registered bake-off run 2 (2026-10-08, `docs/research/memory-bakeoff-decision-2026-10-08.md`) returned
**`KEEP_Z0`**: no engine arm (MemPalace alone, Hindsight+MemPalace, Zoe+MemPalace verbatim tier) passed every floor, Zoe's own
memory leads every shared axis, and no engine is adopted. Live memory is Zoe's own stack (MemoryService, Chroma/MemPalace drawers,
Postgres people graph). I treat KEEP_Z0 as the current state throughout. Also stale: the docstring of
`services/zoe-data/zoe_memory_layers.py` still names Hindsight and Graphiti as layer owners (see section 2).

---

## 0. What Zoe should take from this (ten bullets)

1. **The binding problem is delivery, not storage or engine choice.** Zoe's memory leads every axis it shares with the three
   candidate engines [Proven, bake-off 10-08], yet 83% of real replies used zero memory sources and continuity was empty on 97% of
   turns [Proven, `synthesis-deep-dive-2026-10-29.md`]. No paper in this survey fixes that, because none targets a 4B brain that
   skips memory tools: in the bake-off the 4B searched memory first in only 36 of 100 turns, with schema-valid calls 172/172.
   **Push beats pull on this brain.** Memory has to arrive in the prompt without the model asking.
2. **Add a tiny, prefix-stable "now card" per member to every household turn** (about 120 to 180 tokens: who is speaking, the one
   or two open threads, the next date inside 7 days, one portrait line), built nightly and read by key, not by search. This is
   the Letta/MemGPT "core memory block" idea ([MemGPT](https://arxiv.org/abs/2310.08560)) without the agent loop, and the
   sleep-time-compute idea ([Lin et al. 2025](https://arxiv.org/abs/2504.13171)) applied to a predictable household. Their paper's
   own caveat fits: gains are largest when the queries are predictable from context, and a household's mornings are.
3. **Move `ZOE_RECALL_GATE` from shadow to enforce on evidence Zoe already logs.** The gate (sticky, cooldown, delay, 400-token
   budget, abstain, authority ordering) is built and runs in shadow, writing `would_add` and `would_drop` per turn. The data to
   decide is a replay away. This is the cheapest available lift and carries the voice replay gate.
4. **Do not put facts in weights.** Parametric memory (LoRA, SEAL, Doc-to-LoRA, Titans-style modules) cannot carry provenance,
   cannot be audited, and cannot honour "forgotten means forever" or user-stated > confirmed > inferred; removing a fact from
   weights is unsolved. [LoRA Learns Less and Forgets Less](https://arxiv.org/abs/2405.09673) shows LoRA also learns less than full
   fine-tuning. Keep continual learning non-parametric; allow weights only for style (section 6, labelled speculative).
5. **Derived memory must be a view over pointers, never a second store.** Zoe's night mind already stores
   pointer plus verbatim span plus enums, with no model prose. Autobiographical "chapters", portraits and associations should be
   assembled the same way, so a forget cascade can rebuild them and a probe can prove the erase. This single rule is what makes
   Phase 6 safe; it is also why most "agentic memory that rewrites old notes" designs (A-MEM's note evolution, mem0 DELETE ops)
   cannot be taken whole.
6. **Temporal reasoning is mostly a write-time and aggregation problem, and Zoe's design is the right shape.** 2026 evidence says
   stores retrieve the updated fact and the model still fails to apply it ([STALE](https://arxiv.org/abs/2605.06527): best model
   55.2%; the temporal-KG engine scoring 7% on fact consolidation in the cited
   [2606.01435](https://arxiv.org/abs/2606.01435) summary, secondary). Zoe's deterministic supersession chain plus bi-temporal
   validity is that shape. The gap is that **nothing ages** (2 of 146 approved rows carry a validity end) and there is no
   person-keyed single-value contradiction check.
7. **"Connections across months" is a dormant-thread problem plus an opportunity matcher, not a graph-database problem.**
   Keep threads past the 9-day quiet line as `dormant` with their trigger keys, and match new signals (lists, calendar, Omi day
   review, chat) against those keys at night, writing a candidate to the existing pull queue. No graph engine is needed; the
   recall gate's `derive` already produces language-independent keys. Measure false alarms, as
   [TriggerBench](https://arxiv.org/abs/2606.23459) says models drift to "always remind".
8. **Public benchmarks are sanity checks, not the Samantha bar.** LoCoMo has an audited 6.4% answer-key error rate and a judge that
   accepted up to 63% of deliberately vague wrong answers ([audit](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer),
   third-party), and a full-context baseline matches or beats memory systems on it. LongMemEval-S fits in a modern context
   window. Zoe's own ZMB (authority, forgetting, poisoning, temporal, emotional, exact words) is the harder and more relevant
   instrument. Add three external cell families it lacks: implicit-conflict (STALE), prospective/opportunity (TriggerBench,
   PM-Bench), and governance under several principals (GateMem).
9. **Zoe's store is small, which changes the engineering.** The 11-02 audit counted 146 approved memory rows across the whole
   household. [derived] At roughly 25 tokens a row that is under 4,000 tokens in total, and per member far less. At this scale the
   right design is a selective always-on core plus the gate, not retrieval sophistication (the ConvoMem preprint
   [2511.10523](https://arxiv.org/abs/2511.10523) makes the same point for the first ~150 conversations). Graph and RL-trained
   memory machinery only pays at a scale Zoe will not see for years.
10. **Measure continuity, not retrieval.** Retrieval hit-rate is already near ceiling on Zoe's bench (recall 12/12, multi-hop items
    39/40). The unmeasured quantities are thread carry over days and months, opportunity recognition without over-firing, the
    re-telling rate (the household repeating what it already said), staleness of served facts, and forget-completeness across
    derived artefacts. Section 5 defines them.

---

## 1. Projects and papers: purpose, evidence and verdict

Hardware frame used for every row: **waking hours** = one live 4B Gemma E4B-QAT+MTP brain (8,192-token slot, decode ~60 tok/s
measured 10-09, ~500 MB free RAM, no room for a second model or a database server); **night window** (~02:50 to ~03:10) = the 12B
(measured 10-09: prefill 136 tok/s, decode 3.62 tok/s), so a call with an 8k prompt and 600 output tokens costs about 60 s + 165 s
[derived]. "Source" says what I actually read: **none** means abstract and metadata only.

### 1.1 Agent memory systems and architectures

| Project / paper | What it is and how it works | Source / maturity / licence | Fit on Zoe's hardware | Verdict |
|---|---|---|---|---|
| **MemGPT / Letta** [paper](https://arxiv.org/abs/2310.08560), [repo](https://github.com/letta-ai/letta) | OS-style tiers: always-in-context "core" blocks, recall (history) and archival stores, the model pages memory in and out by tool calls. | Source: none. 25.3k stars, pushed 2026-10-18, Apache-2.0. Server product plus agent loop. | A server plus Postgres does not fit beside the brain; the loop depends on the model choosing memory tools, which the 4B does 36/100 [Proven, bake-off]. | **Adapt the idea, reject the runtime.** Take core-block-in-prompt (bullet 2). Already prior-evaluated. |
| **Sleep-time compute** [paper](https://arxiv.org/abs/2504.13171), [code](https://github.com/letta-ai/sleep-time-compute) | Do inference between interactions to pre-digest context; reports ~5x less test-time compute for equal accuracy and +13% / +18% accuracy on its two stateful reasoning sets, best when queries are predictable. | Source: none. Research code, MIT, 141 stars, last push 2025-04. [Experimental] | Fits the night window by construction; Zoe already runs it (dreaming, night mind). | **Adapt** (precompute the now card, section 3.1). The idea is already native; do not add code from the repo. |
| **mem0** [paper](https://arxiv.org/abs/2504.19413), [repo](https://github.com/mem0ai/mem0) | Extract salient facts per turn, then ADD / UPDATE / DELETE / NOOP against existing memories with an LLM; optional graph variant (Mem0g). | Source: none. 68.3k stars, Apache-2.0, pushed 2026-10-30. Headline LoCoMo gains are self-reported; vendor-vs-vendor dispute on its baselines. | Per-turn LLM write path competes with the live slot; the DELETE op lets a model retire facts. | **Reject as engine** (already dropped). The op-vocabulary is already in Zoe's dedup gate (ADD/UPDATE/SKIP, never drop a correction). |
| **Zep / Graphiti** [paper](https://arxiv.org/abs/2501.13956), [repo](https://github.com/getzep/graphiti) | Bi-temporal knowledge graph: episode subgraph, entity subgraph, community subgraph; edges carry event time and ingestion time; hybrid retrieval. | Source: none. Graphiti 32.5k stars, Apache-2.0, pushed today. Product claims on LoCoMo disputed by Mem0. | A graph backend and LLM entity resolution per episode do not fit waking RAM; the build plan (DO-NOT-REPEAT list) already rules out a heavy graph DB. | **Adapt the model, reject the engine.** Bi-temporal edges are already designed into the Postgres people graph (`people-graph-temporal-model-2026-10-05.md`). |
| **Hindsight** [paper](https://arxiv.org/abs/2512.12818), [repo](https://github.com/vectorize-io/hindsight) | Four memory networks (world facts, experiences, entity summaries, evolving beliefs) with retain / recall / reflect; reports 83.6% on LongMemEval with a 20B open model vs a 39.0% full-context baseline (authors, [Experimental]). | Source: **ran in the bake-off** [Proven]. MIT, 58.3k stars, pushed today. | Measured on this box: its consolidation prompt peaks at 8,313 tokens against the 8,192 slot; as arm HMA it failed authority (31/77) and had 58 hard violations; glue 1,361 lines vs a 1,000 ceiling. | **Reject for waking use; experiment at night only** (E6), because a 16k night slot removes the prompt-size blocker. Not adopted under KEEP_Z0. |
| **A-MEM** [paper](https://arxiv.org/abs/2502.12110), [repo](https://github.com/agiresearch/A-mem) | Zettelkasten notes: on write the model generates keywords, tags and links, and updates older linked notes ("memory evolution"). | Source: none. 1.2k stars, MIT, last push 2025-12. | Link generation at write is an LLM call; fine at night. | **Adapt the linking, reject the evolution.** Propose links at night as candidates; never let the model rewrite an older row (authority rule). |
| **MemoryBank** [paper](https://arxiv.org/abs/2305.10250) | Forgetting-curve decay (R = e^(-t/S)) with strengthening on recall; user portrait; the SiliconFriend companion. | Source: none. 2023 research; no maintained code assessed. | Pure arithmetic; free. | **Adapt narrowly.** Use for *ageing of inferred / mood-class rows* (`review_after`), never for user-stated durable facts. |
| **Generative Agents** [paper](https://arxiv.org/abs/2304.03442), [repo](https://github.com/joonspk-research/generative_agents) | Memory stream scored by recency x importance x relevance; periodic reflection that writes higher-level notes with pointers to what they cite. | Source: none. Repo dormant (last push 2024-08), Apache-2.0. Pattern is now standard. | Free. | **Adopt, already adopted** (hotness re-rank, importance 3b, night-mind pointers). |
| **HippoRAG 2** [paper](https://arxiv.org/abs/2502.14802), [repo](https://github.com/OSU-NLP-Group/HippoRAG) | Entity/passage graph plus Personalized PageRank from query entities; "non-parametric continual learning"; +7% on associative tasks vs the strongest embedding baseline (authors). | Source: none. ICML 2025. 4.1k stars, MIT, pushed 2026-10-06. | PPR over a few thousand nodes is CPU-cheap; the costly part is LLM triple extraction, which Zoe already does. | **Experiment, low priority (E5).** Zoe's multi-hop cell is already 39/40 on Z0; headroom appears only at larger stores. |
| **LightRAG / GraphRAG** [LightRAG](https://arxiv.org/abs/2410.05779), [repo](https://github.com/HKUDS/LightRAG), [GraphRAG](https://github.com/microsoft/graphrag) | Graph-indexed document RAG; dual-level (entity vs theme) retrieval; GraphRAG adds community summaries; LightRAG claims incremental update. | Source: none. LightRAG 40.5k stars MIT; GraphRAG 36.5k stars MIT. Corpus-QA shape. | Index-time LLM cost is high; designed for documents, not a household's 146 rows. | **Reject** (wrong shape). Community summaries would be unattributed prose, which the own-words wall forbids. |
| **Cognee** [repo](https://github.com/topoteretes/cognee) | Pipeline that builds a knowledge graph plus vectors from ingested data for agent memory. | Source: none. 33.0k stars, Apache-2.0, pushed today. Footprint not measured. | Graph + vector stack; not verified to fit; no authority model. | **Reject** (previously dropped; nothing new found). |
| **MemOS, EverOS, LightMem** [MemOS](https://github.com/MemTensor/MemOS), [EverOS](https://github.com/EverMind-AI/EverOS), [LightMem](https://github.com/zjunlp/LightMem) | MemOS: memory OS for coding agents (heavy graph/vector backends). EverOS: per-user profile plus offline reflection, markdown source of truth. LightMem: pre-compression and sleep-time update to cut LLM calls. | Source: **none this lane; 10-07 scout read the READMEs**. MemOS 12.2k Apache-2.0; EverOS 13.8k Apache-2.0; LightMem 1.2k MIT. | LightMem runs against local Ollama/vLLM; EverOS needs authority wrapped round it. | **MemOS reject; EverOS and LightMem: experiment, deferred behind E1 to E4** (scout verdicts stand; none was run in bake-off run 2). |
| **Memp** [paper](https://arxiv.org/abs/2508.06433) | Procedural memory: distil agent trajectories into step-by-step and script-level abstractions; update on failure; transfers from a strong to a weak model. | Source: none. v3 2026-01. [Experimental] on TravelPlanner and ALFWorld. | Cheap text store. | **Adapt** for the procedural layer (E10): "how to serve this household" rows from struggle-detector fumbles. |
| **Mem-alpha, MemAgent** [Mem-alpha](https://arxiv.org/abs/2509.25911), [MemAgent](https://arxiv.org/abs/2507.02259) | RL-trained models that learn what to write / compress into a fixed memory. | Source: none. Both need RL training of the model. | Training a policy model is out; the brain is a rock. | **Reject as method; adapt the signal.** Use downstream-QA reward only to *select prompts/extractor settings* offline with ZMB, never to train weights. |
| **Mem-infinity ("Mem∞")** | Named in the brief. **I could not find a paper or repo by this name**; nearest matches are MemGPT and MemAgent above. | Not located; treat as unverified. | n/a | **No verdict.** If the owner has a link, send it. |
| **Survey: Memory in the Age of AI Agents** [2512.13564](https://arxiv.org/abs/2512.13564) | Taxonomy: forms (token, parametric, latent), functions (factual, experiential, working), dynamics (formation, evolution, retrieval). | Source: none (abstract level). | n/a | **Adopt as vocabulary** for section 2; confirms the field now splits "experiential/procedural" from "factual". |

### 1.2 Continual learning without retraining, and its limits

| Project / paper | What it is | Source / maturity / licence | Fit | Verdict |
|---|---|---|---|---|
| **LoRA adapters** [Biderman et al.](https://arxiv.org/abs/2405.09673) | LoRA "learns less and forgets less" than full fine-tuning; acts as a regulariser; underperforms on knowledge-heavy targets. | Source: none. [Proven] by the authors' controlled runs; reproduced widely. | Training on the Orin during the night window is conceivable on a small adapter, but the live model is a quantised QAT+MTP build and adapter compatibility with MTP is unknown here. | **Reject for facts. [Speculative] for style only** (section 6). |
| **SEAL** [paper](https://arxiv.org/abs/2506.10943) | Model writes its own fine-tuning "self-edits" and RL trains that behaviour; reports 72.5% vs 20% on a few-shot abstract-reasoning setup (authors). | Source: none. [Experimental]; needs RL loops; the paper's own figures show forgetting under sequential edits (per a secondary summary). | No. | **Reject.** |
| **Doc-to-LoRA / Text-to-LoRA** [Sakana](https://sakana.ai/doc-to-lora/), [Text-to-LoRA](https://arxiv.org/abs/2506.06105), [Doc-to-LoRA](https://arxiv.org/abs/2602.15902) | A hypernetwork emits a LoRA from a document or task text in one forward pass. | Source: none. [Experimental]; hypernetwork must be trained per base model. | Not for the Gemma build without training a hypernetwork; no provenance in the output. | **Reject for memory; [Speculative] for task skills.** |
| **Titans, Nested Learning (Hope)** [Titans](https://arxiv.org/abs/2501.00663), [Nested Learning](https://arxiv.org/abs/2512.24695) | New architectures with a learned long-term memory module updated at test time; multi-timescale "continuum memory". | Source: none. [Experimental]; architecture-level, needs pretraining. | Cannot be added to a fixed Gemma checkpoint. | **Reject** now; watch only. Conceptually they restate Zoe's fast / slow split. |
| **Memory Decoder** [paper](https://arxiv.org/abs/2508.09874) | A small decoder trained to imitate a retriever, interpolated with the base model's output; reduces domain perplexity. | Source: none. [Experimental]. | Perplexity-type gains, no per-fact provenance, tokenizer-bound. | **Reject.** |
| **RAG as continual learning** [HippoRAG 2](https://arxiv.org/abs/2502.14802) | Frames external memory as the way to learn without catastrophic forgetting. | See 1.1. | Matches Zoe's architecture. | **Adopt the framing:** Zoe's continual learning is its store, its night passes and its prompt; weights stay a rock. |

### 1.3 Benchmarks, evaluations and the phase-6 literature

| Item | What it measures | Notes and caveats | Verdict |
|---|---|---|---|
| **LongMemEval** [paper](https://arxiv.org/abs/2410.10813), [repo](https://github.com/xiaowu0162/LongMemEval) | 500 questions; extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention; S ~115k tokens, M ~1.5M. MIT, pushed 2026-05. | Authors found 30% drop for commercial assistants. S now fits in modern context windows (critics). | **Adopt as an external sanity cell** for the temporal / update / abstention axes ZMB already has. |
| **LoCoMo** [paper](https://arxiv.org/abs/2402.17753), [repo](https://github.com/snap-research/locomo) | ~300-turn, up to 35-session conversations; QA, summarisation. Licence `NOASSERTION`. | Audit: 6.4% key errors, lenient judge ([third party](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer)). | **Use only for relative comparison; do not quote as a bar.** |
| **BEAM** [paper](https://arxiv.org/abs/2510.27246) | 100 conversations to 10M tokens, 2,000 questions; ICLR 2026. | Scale Zoe will not reach soon. | **Skip** until the store is 100x larger. |
| **STALE** [paper](https://arxiv.org/abs/2605.06527) | 400 implicit-conflict scenarios: detect a stale belief, reject presupposing queries, apply the update downstream; best model 55.2%. | May 2026; new, [Experimental]. | **Adopt the three-part probe** as new ZMB temporal cells. |
| **TriggerBench, PM-Bench** [TriggerBench](https://arxiv.org/abs/2606.23459), [PM-Bench](https://arxiv.org/abs/2607.12385) | Prospective memory: recall a latent constraint or intention at the right cue; false-alarm rate; decays with context length. | June / July 2026; [Experimental]. | **Adopt for the opportunity metric** (section 5). |
| **GateMem** [paper](https://arxiv.org/abs/2606.18829), **Collaborative Memory** [paper](https://arxiv.org/abs/2505.18279) | Memory governance with several principals: utility, access control and deletion scored together (GateMem); user / agent / resource access graphs with private and shared tiers (CM). | GateMem: no method wins all three; long-context best on governance, retrieval systems leaked (secondary summary). New. | **Adopt GateMem's joint scoring** for the multi-member cell; Zoe's walls are stricter than CM's. |
| **KnowMe-Bench, LifeMem, Sophia, StorySage** [KnowMe](https://arxiv.org/abs/2601.04745), [LifeMem](https://arxiv.org/abs/2608.19621), [Sophia](https://arxiv.org/abs/2512.18202), [StorySage](https://arxiv.org/abs/2506.14159) | Person understanding from autobiographical narratives; structured life events plus per-agent LoRA with replay; a persistent agent with a goals/experiences graph; interview-driven life-story chapters. | All abstract-level reading, all [Experimental]. KnowMe reports retrieval helps facts but interpretive and temporal questions stay weak. | **Mine ideas, adopt nothing.** KnowMe for eval cells; StorySage for the idea of one topic per session. LifeMem's LoRA half conflicts with bullet 4. |
| **ANCHOR, ATRBench, PersonaMem-v2** | Companion persona collapse (trajectory accuracy 44.4%); proactive preference acquisition gap; implicit user model. | Cited from the 10-07 scout (`scout-memory-cognition-2026-10-07.md`), not re-read here (IDs and titles re-checked 2026-11-03): [ANCHOR](https://arxiv.org/abs/2607.28818), [ATRBench](https://arxiv.org/abs/2605.28108), [PersonaMem-v2](https://arxiv.org/abs/2512.06688). | **Adopt as scoring arms** (scout verdict stands). |
| **ConvoMem** [preprint](https://arxiv.org/abs/2511.10523) | Argues the first ~150 conversations do not need RAG; filesystem ops matched sophisticated systems on LoCoMo (74%). | Preprint; supports bullet 9. [Experimental] | **Use as a design caution.** |

---

## 2. The seven layers of Phase 6 mapped onto what Zoe already has

Sources for the "exists today" column: `zoe-memory-samantha-buildplan.md`, `person-centred-memory.md` (D1-D12 accepted
2026-11-03), `memory-authority.md`, `recall-gate.md`, `night-mind.md`, `memory-supersession.md`, `zoe_memory_layers.py`, module
names in `services/zoe-data/`. Counts are those the 11-02 audit published. I did not read any household memory content.

| Layer | Exists today as | Gap | Smallest step (rename vs build) |
|---|---|---|---|
| **Working** | The Flue-seam packet (`/api/memories/for-prompt`) plus voice mirror; `session_continuity` (id-less asks reuse a 20-minute `ask_` session); `recall_gate` per-session sticky / cooldown state; the 8,192-token slot. | Packet fires only on triggers; continuity empty on 97% of turns; no always-on identity or thread card. | **Build** the now card (E1). Flip the gate to enforce after replay (E2). No rename. |
| **Episodic** | `chat_messages` verbatim; drawers with `exact_turns` and captured_at; turn / voice digests; MemPalace verbatim tier (bake-off: exact-words items 40/40 on Z0). | No **episode objects**: nothing says "this stretch of days was the Perth trip" with a start, an end and member set; recall is turn-shaped. | **Build** a thin `episodes` pointer table at night (time-gap + shared keys + named entities), pointers only. |
| **Semantic** | Authority rows (fact / person / event / habit / health / profile), the people graph with temporal edges, `person_important_dates`, `user_portraits`. | 34% of approved rows link to a person; 27% carry a provenance label; nothing ages; no person-keyed single-value contradiction check. | **Build** what person-centred-memory D1-D12 already specifies (subject resolution at the `ingest` choke point, `review_after`, single-value check). No new design needed. |
| **Procedural** | Deterministic `fast_tiers` (how Zoe does timers, lists, music); habit rows; `recall_gate` routine flag (habit cue plus time of day); the struggle detector (phase 1). `skills/` is documentation only, not wired. | No store of *how to serve this person* (their phrasing, corrections that stuck, tool preferences). | **Build small** (E10): `facet=manner` rows fed by struggle-detector fumbles, owner-approved, served as 1 to 2 lines. Memp is the pattern. |
| **Autobiographical** | Part of it: `user_portrait` (weekly narrative per person), night-mind threads with quotes, important dates, open loops. | No **timeline** and no "what we have been through together". Two kinds are conflated: each member's life story, and Zoe's shared history with that member. Nothing is assembled across months. | **Build** pointer-assembled **chapters** (E9): per member per month, a deterministic template over resolved / advanced threads, dates and episodes; pull-tier only; every line cites a row id. |
| **Associative** | Entity triggers and keys in `recall_gate`; `concept_tags`; 1-hop relational block (`person_recall_floor`, gbrain design); the people graph (3 edges only). | Cross-month links between a thread and a later event are not made; the graph is nearly empty (data starvation, not a design gap). | **Build** the dormant-thread matcher (E4) before any graph index; HippoRAG-style PPR (E5) only if a bench cell shows headroom. |
| **Reflective** | Weekly portrait synthesis; digest; `insight` rows (8 approved); **night mind** (moments, threads, raise/leave policy; flag `off` default, `serve` mode live-capable); EmotionalFollowUp trigger (off). | Night mind's ship bar (K1 Wilson lower bound >= 0.85 over >= 40 observations) is unmet; the 12B window is the intended writer. K6, K9 to K12 failed on the 4B. | **Keep.** Run it in the 12B window (the flag's `serve` split already exists), and gate promotion on K1 at n >= 40. |

**Rename item.** `zoe_memory_layers.py` defines seven layers (working_context, canonical_state, episodic_memory,
reflective_memory, relational_temporal_memory, governance, observation_evaluation) and its docstring names Hindsight and
Graphiti as owners. That is a different seven from Phase 6's and contradicts KEEP_Z0. **Rename** the enum to the Phase 6 seven
plus keep `governance` and `observation_evaluation` as cross-cutting concerns, and rewrite the docstring to the real owners
(MemoryService, MemPalace drawers, Postgres graph, night mind). I did not check whether the enum is consumed at runtime; if it is
not, delete it (retire by removing).

---

## 3. Analysis by theme

### 3.1 Making recall reach the brain on every household turn, inside a token budget

**Diagnosis [Proven in repo records, interpretation mine].** Every injector is trigger-gated: the hybrid floor needs a keyword
or similarity hit, the hop needs its word lists, the night block needs a story-shaped message. A household turn like "turn the
lights off" carries no trigger, so memory is correctly absent, and that is most turns. The 83% therefore mixes two populations:
turns where nothing relevant exists (correct), and turns where something relevant existed but nothing fired (the defect). **The
first thing to build is the instrument that separates them** (section 5, coverage ratio), otherwise any fix is unfalsifiable.

**Design: three tiers by cost, all zero-LLM on the hot path.**

| Tier | Content | Budget | Source | When it changes |
|---|---|---|---|---|
| T0 identity line | speaker name, relationship to the household, one stable line (language, a standing preference) | 20 to 30 tokens | speaker id, portrait | rarely; prefix-cache stable |
| T1 now card | the raise thread (night mind), up to 2 open threads, next date within 7 days, one "to check" item, one `manner` line | 100 to 150 tokens | night mind, dates, open loops, `manner` rows | once nightly |
| T2 triggered recall | the existing gate: names, keys, clock times, similarity, sticky / cooldown | 400 tokens, 12 rows (existing) | `recall_gate` | per turn |

T0 + T1 are written by the night pass into one row per member and read by key, so they cost no search and no model call. Because
they change at most nightly, the prompt prefix stays byte-stable and the panel's resident prompt cache keeps working (the
2026-10-29 synthesis ranks keeping that cache resident as a top-two latency item). Total added is ~170 to 180 tokens against an
8,192 slot [derived], inside the 400-token recall envelope the blueprint already allocates.

**Restraint is the real risk.** An always-present card invites the 4B to mention things. Mitigations already in the repo:
`restraint.decide` classes, the leave deny-list (health, grief, money, quarrels), at-most-one `raise`, the back-off that doubles
after each ignored raise, `ZOE_PROACTIVE_SPOKEN=0`. The card should carry *pointers and permission* ("may raise: X, once"), not
instructions to speak, and the brain doctrine (as with `PERSONAL_RECALL_DOCTRINE`) decides use. Guests and unverified speakers get
no card; an off-record turn arms nothing (the gate's existing rules).

**Evidence that this is the right lever.** The bake-off measured the 4B using memory tools correctly when it called them
(172/172 valid) but calling them first only 36% of the time; the live-brain eval rose from 88% to 100% only after a prompt
doctrine told the brain to call recall on oblique personal questions [Proven, build plan 07-05]. Both say the same thing: model-side
initiative is weak, deterministic injection is strong.

### 3.2 Consolidation

Zoe has four consolidators: idle consolidation (live since 06-24), the digest, weekly portrait synthesis, and night mind (moments
-> threads -> decide). The field's consolidation ideas map as follows.
- **Reflection with citations** (Generative Agents; Hindsight reflect; sleep-time compute): Zoe's pointer-only observations are
  stricter than any of them, which is why the 4B reached quote discipline of 0 bad quotes over ~40 [Proven, 10-09 smoke].
- **Update-over-create thread discipline** (Hindsight's create/update): already copied into THREADS, with code-side enforcement.
- **Write-time linking** (A-MEM): add as night candidates only.
- **Compression before extraction** (LightMem): worth one trial because the 3,000-character cut was the old failure; night mind's
  PACK stage already replaced it for that pass.
- **What is missing is consolidation across *weeks and months***: threads currently have one clock (quiet after max(9 days,
  2 x usual gap)). Add a `dormant` state with trigger keys retained and a monthly roll-up (episodes and chapters, section 3.7).

### 3.3 Temporal reasoning

- **Have:** `valid_from` / `invalid_at` / `expired_at` (two timelines on every row), implicit supersede (flag-dark), `as_of` design,
  supersession chain with `supersedes_id` on 157 rows, deterministic read-time hiding of superseded rows.
- **Field evidence:** retrieval of the new fact is not the failure; applying it is (STALE; the fact-consolidation result in
  [2606.01435](https://arxiv.org/abs/2606.01435) argues for deterministic aggregation over versioned facts). Zoe's authority layer
  already does aggregation in code. [Experimental] for the field claim; Zoe's own temporal axis is 39/39 on Z0 [Proven].
- **Gaps to close:** (a) ageing: `review_after` defaults (90 days life-now, 14 days mood), expiring into a "still true?" item in
  the confirm queue, never a silent drop; (b) person-keyed single-value check (employer, city, partner, diet) raising one
  question; (c) **relative time resolution at write**: "next Friday" must be stored as an absolute date with the turn's own date
  (the extractor's job, testable with a clock-injected cell); (d) a STALE-style implicit-conflict cell, where a later
  observation invalidates an earlier one without negation ("we finally moved" vs a stored address).

### 3.4 Confidence and provenance

Zoe's ladder (user_stated > confirmed > inferred; inference never supersedes) is stricter than any engine surveyed; the bake-off
shows engines fail it (authority 31/77 and 38/77 vs Z0 237/237). Recommendations:
- **Do not add model-reported confidence.** Self-reported probabilities from a 4B are uncalibrated [Speculative but standard
  caution]. Derive confidence from structure: authority class, number of independent user statements, recency since last
  confirmation, quote verified against the turn.
- **Backfill provenance** (73% of approved rows have no label): night pass labels only by evidence (a user-turn pointer exists =
  `user_stated_derived`); everything else stays unlabelled and is never served as confirmed.
- **Make provenance speakable:** "how do you know that?" should return the turn date and the member's own words from `exact_turns`
  (a deterministic tier like `provenance_answers`). It is the cheapest high-trust behaviour on the list.

### 3.5 Forgetting, correction and deletion

Zoe: forget shield is permanent (sentinel), cascade over `memory_forget_*`, tombstones, `night_mind.erase_entity` failing closed,
served rows re-checking the ledger on read. Field state: GateMem reports no method simultaneously gets utility, access control and
reliable forgetting (secondary summary, [Experimental]); Zoe's own forgetting axis is 18/21 on Z0 with three misses still to
explain [Proven]. Three additions:
1. **Derived-artefact completeness.** Every new derived layer (episodes, chapters, now card, associative index) must register an
   erase hook, and the forget probe must query *all* of them. A derived store without a hook fails the build (the writer-map test
   pattern: a new writer module fails CI until it has a role line).
2. **Rebuild-on-forget for views.** Because derived layers are pointer views, forget = delete the source plus rebuild the affected
   view next night plus immediate suppression by the ledger. No model has to "unlearn" anything. This is the structural reason
   parametric memory is excluded.
3. **Correction ergonomics.** "That's wrong, it's X" exists as the one-sentence fix. Extend it so a correction closes the open
   loops and dormant threads that depended on the old fact (the open-loop lifecycle already does this for supersede).

### 3.6 Multi-member separation and relationships

- **Separation:** wings per member, owner-scoped reads, guest refused, other-member accounts refused, minors held to
  guardian-stated facts only, voice-verified speaker required (voice is a password), household-visible class under the fridge rule.
  This exceeds the access-control model in [Collaborative Memory](https://arxiv.org/abs/2505.18279).
- **Gap: shared episodes.** A conversation between two members, or one member talking about another, produces facts whose subject
  and speaker differ. Rule to add: a fact stated by A about B is stored as **A's claim about B**, attributed, served only to A,
  and *never* upgraded to B's fact; B's own statement outranks it and, if different, raises a dispute to B (privately). It is
  already implicit in `subject_basis`; make it a first-class `claimant` field.
- **Relationship modelling:** the CRM-shaped design (profile read-model, dates, gifts, cadence, open threads per person) is the
  accepted design. The research addition is the *temporal edge discipline* already specified (close, never delete; role changes keep
  history). Nothing in the field beats it for a household; adopt no engine.
- **Test:** GateMem-style joint cell: legitimate-use utility, cross-member leakage (target 0), and post-forget leakage, scored
  together so one cannot be bought at another's expense.

### 3.7 Autobiographical memory and personal growth (Phase 6 proper)

Three distinct things are being asked for; keep them apart.
1. **A person's own story** (their projects, moves, losses, milestones). Source: threads, resolved events, important dates,
   episodes. Output: *chapters*, one per member per month, assembled by template from cited rows ("In October: the Perth trip
   (ended 12 Oct); the dentist worry (resolved); a new thread about X"), never model-written narrative. Served on request ("what
   has my year been?"), in the pull tier, and as the source of "this time last year" candidates.
2. **Zoe's shared history with that member** (how long, what has been tried, what she learned about how to help). Source:
   `manner` rows, resolved raises (accepted vs ignored), the cadence of check-ins. Output: a 1 to 2 line "us" fact in T0.
3. **Personal growth of the person** ("you used to dread X; you've been calm about it for a while"). This is the highest-value and
   highest-risk item: it asserts a change in a person's inner state. Constraints: only for states the member themselves named
   (user-stated mood or goal rows), only as **observation with dates and their own quotes** ("in March you said X; in October you
   said Y"), never an inferred trait, off by default for minors, offered not volunteered, and the leave deny-list (health, grief,
   money) always wins. The ANCHOR result (trajectory accuracy 44.4%, [scout cite](https://arxiv.org/abs/2607.28818)) is a warning
   that models over-claim change.

**Why pointers beat summaries here.** A model-written life story is unauditable and will drift (Generative Agents' own reflections
were stored as free notes, and the field's [KnowMe-Bench](https://arxiv.org/abs/2601.04745) reports interpretive and temporal
questions are exactly where retrieval-based systems stay weak). A pointer chapter is boring and verifiable, and the 4B can phrase it
on the way out using only cited lines.

### 3.8 Connections across months (the "project, then a later chance to help" case)

Mechanism, end to end, using only parts that exist:
1. **Capture:** a member mentions a project ("I'm planning to repaint the shed"); night mind makes a thread with `kind: plan` and
   trigger keys from `recall_gate.derive` (stem keys, names, times).
2. **Hold:** the thread passes the quiet line and becomes `dormant` (new state) instead of fading; keys and the owning member are
   retained; it is subject to the same forget cascade.
3. **Watch:** at night, a deterministic matcher intersects dormant-thread keys with the day's *new signals*: shopping-list items,
   calendar entries, reviewed Omi day items, the member's own new statements. A match above the gate's threshold (rare key 1.5,
   shared name 2.5) creates an **opportunity candidate** with both pointers.
4. **Deliver:** into the existing selector / pull queue only, one per day at most, with the back-off. Phrased as an offer ("you
   mentioned painting the shed in August; the hardware list has primer on it - want the earlier notes?"). Never an action and never
   spoken unprompted (`ZOE_PROACTIVE_SPOKEN=0` stands).
5. **Learn:** accepted / ignored feeds the raise back-off; two ignores = leave.

The field gives no engine for this. The closest evaluations are prospective-memory benchmarks
([TriggerBench](https://arxiv.org/abs/2606.23459), [PM-Bench](https://arxiv.org/abs/2607.12385)), whose key warning is that the
easy policy ("always remind") scores recall at the price of false alarms. Zoe's measure must therefore be precision-weighted.

### 3.9 Catastrophic forgetting

For Zoe it is a non-issue by construction, and should stay so: the model is a fixed rock, so no gradient step can overwrite earlier
knowledge. The relevant "forgetting" classes are (a) *user-directed* (forget = permanent, a feature), (b) *ageing* (review_after
checks, a feature), (c) *accidental loss* (a consolidation pass archiving a row it should not have; guarded by "an unsupported
model write retires nothing", dedup "never drop a correction", and archive-not-delete). Measure (c) with a **consolidation
regression cell**: take a synthetic store, run every night pass, assert no approved user-stated row changed status. The bake-off's
3 misses of 21 on each of forgetting and poisoning (Z0) are the open items in (a).

---

## 4. Experiments

All run on **synthetic households** (the bench's own, never real members), scratch stores, under the existing guards:
`flock -x /tmp/zoe-test-lane.lock`, voice harness lock for anything touching the voice path, nothing in the 01:55 to 04:55 window
except the night job itself, and the replay gate against `~/.zoe-voice-samples` for any voice-path change.

### E1. Now card (T0 + T1), shadow first
- **Hypothesis:** a nightly-built, key-read card of <= 180 tokens raises the share of turns with an eligible memory item that
  actually carry one from today's baseline to >= 50%, with irrelevant-mention rate <= 5% and no added TTFT.
- **Implementation:** a night step writes one row per member (T0 + T1) from existing stores (night mind `raise` and open threads,
  `person_important_dates`, open loops, portrait first line); `memory_for_prompt` appends it behind `ZOE_MEMORY_NOW_CARD =
  off|shadow|serve` (shadow logs the would-be card and token count only; the existing three-state flag convention).
- **Resources:** no model on the hot path; the build is one SQL read set at night. Voice path, so replay gate and Greptile
  (load-bearing) apply.
- **Evaluation:** coverage ratio and irrelevance rate (section 5, M1 and M3) on day-sim runs and on a 100-turn sample of real
  turns labelled by the owner or the 12B at night; TTFT and prefix-cache hit from `measure_voice.py`.
- **Success / failure:** success = coverage >= 50% with M3 <= 5% and TTFT drift inside the baseline interval. Failure = the 4B
  mentions card items on unrelated turns (M3 > 5%): then demote T1 to "available on ask" and keep only T0.
- **Effort:** M (about 4 to 6 days including replay).

### E2. Recall gate shadow to enforce
- **Hypothesis:** the gate's `would_add` rows are relevant >= 80% of the time and `would_drop` rows irrelevant >= 80%.
- **Implementation:** none new; collect `RECALL_GATE` lines, run `scripts/perf/recall_gate_replay.py`, label a sample, flip
  `ZOE_RECALL_GATE=enforce` for one member for a week.
- **Resources:** labelling effort only. **Evaluation:** precision of adds and drops; abstain rate; S9b-type day-sim asks.
- **Success / failure:** success = both >= 80% and no authority or privacy regression (negative controls stay red when neutered).
  Failure = abstain starves a turn that needed memory (the floor stands: `abstain=False`).
- **Effort:** S (1 to 2 days plus a week of observation).

### E3. Continuity cells for ZMB (months, not turns)
- **Hypothesis:** thread carry and opportunity recognition are currently unmeasured and are weak.
- **Implementation:** extend the day-sim with an injectable clock; plant 12 threads (4 resolved, 4 live, 4 dormant) over 90
  simulated days; add cells C1 thread-carry, C2 opportunity, C3 stale-serve, C4 re-telling, C5 forget-completeness (section 5).
  Add STALE-style and GateMem-style cells from section 1.3.
- **Resources:** deterministic harness; the 4B for the reading half, the 12B night window for a nightly pass if needed.
  **Evaluation:** Wilson intervals as in ZMB, with a negative control for each cell (feature off must go red).
- **Success / failure:** success = cells discriminate (control red, Z0 reports a number). Failure = a cell cannot be made red by
  its control; then it is not an instrument and is dropped.
- **Effort:** M to L (1.5 to 2 weeks).

### E4. Dormant-thread opportunity matcher
- **Hypothesis:** deterministic key matching finds >= 70% of planted later-opportunities with <= 1 false candidate per 30 member-days.
- **Implementation:** a `dormant` thread state; a night step intersecting its keys with new lists / calendar / reviewed Omi /
  chat keys using the gate's `derive` signals; writes a pull-queue candidate.
- **Resources:** CPU only, no model. **Evaluation:** C2 in E3, plus owner thumbs on a 4-week trial in shadow.
- **Success / failure:** success as stated and accept-rate >= 40%. Failure = false-candidate rate above bound; tighten the
  threshold or demote to "owner asks 'anything I should remember?'" only.
- **Effort:** M (about 1 week after E3's C2).

### E5. Associative index (HippoRAG-lite) as a bench-gated trial
- **Hypothesis:** Personalized PageRank over a co-occurrence graph of Zoe's entities beats the current 1-hop block only when the
  store is large.
- **Implementation:** offline, scratch store, networkx on CPU over the extractor's existing entity output; compare against Z0e on
  multi-hop and recall_distance with the store inflated by synthetic filler.
- **Resources:** minutes of CPU, no GPU. **Evaluation:** ZMB axes D and multi-hop.
- **Success / failure:** success = a gain beyond the Wilson interval on a store >= 5,000 rows; failure (expected today) = no gain at
  146 rows, which confirms deferral. **Effort:** S to M.

### E6. 12B night comparator (Hindsight reflect and LightMem), read-only
- **Hypothesis:** at a 16k night context Hindsight's reflect passes the observation-precision veto that failed on the 4B, and costs
  an acceptable night budget.
- **Implementation:** synthetic household export only; run in a scratch container during the night window with the existing
  guards; compare its observations to night mind's on K1 / K2 and quote-verifiability. [derived] cost: ~8.3k-token prompt = ~61 s
  prefill + ~165 s for 600 output tokens at 3.62 tok/s, about 3.8 minutes per call.
- **Resources:** the 12B window, ~1 night; RAM guard as for the bench. **Evaluation:** observation precision, authority
  violations, glue size vs the 1,000-line ceiling.
- **Success / failure:** success = precision >= night mind's K1 with zero hard authority violations *without* a rewrite layer;
  failure (likely given 58 violations before) = confirms KEEP_Z0. **Effort:** M. Optional; do after E1 to E4.

### E7. Person-keyed single-value contradiction check and ageing
- **Hypothesis:** a per-person single-value slot check plus `review_after` expiry finds >= 80% of planted changes with <= 1 question
  per member per week.
- **Implementation:** as specified in person-centred-memory (3.3); reuse `memory_lint` and `memory_disputes`.
- **Resources:** deterministic plus the 4B yes/no judge off-path (280 ms p50, structural-floors record). **Evaluation:** STALE-style
  three-part probe (detect, reject presupposing queries, apply downstream) and the question-budget cap (homework limit).
- **Success / failure:** success as stated; failure = question volume over cap, then batch weekly. **Effort:** M.

### E8. Speakable provenance ("how do you know that?")
- **Hypothesis:** answering from `exact_turns` plus captured_at is correct on >= 95% of labelled rows and refused on every
  unlabelled row.
- **Implementation:** deterministic tier beside `provenance_answers`; replay-gated (fast_tiers). **Resources:** none beyond reads.
- **Evaluation:** ZMB authority and identity cells plus 40 spoken probes. **Success / failure:** any fabricated provenance is a
  hard fail (the answer must be refused when no pointer exists). **Effort:** S.

### E9. Pointer-assembled chapters
- **Hypothesis:** a templated monthly chapter is judged accurate (every line traceable) and wanted by members.
- **Implementation:** a night / monthly job over resolved and advanced threads, episodes, dates; output = list of
  `{text-from-template, row_id}`; erase hook registered.
- **Resources:** CPU only. **Evaluation:** 100% line traceability (hard bar), KnowMe-style factual and temporal questions
  answerable from the chapter, owner rating over a month; leakage cell. **Success / failure:** failure = any untraceable line, or
  a chapter that reaches another member.
- **Effort:** M.

### E10. Procedural `manner` rows
- **Hypothesis:** storing owner-approved "how to serve me" rows cuts repeated corrections by >= 30% over four weeks.
- **Implementation:** struggle-detector fumbles and accepted corrections become `facet=manner` proposals (approve-before-write);
  served as 1 to 2 lines in T1. **Resources:** none beyond existing detector. **Evaluation:** M5 (repeat-correction rate).
- **Success / failure:** failure = rows pile up unapproved (homework) or cause stilted replies. **Effort:** M.

### E11. Forget-completeness across derived layers
- **Hypothesis:** after forget, zero served or retrievable trace remains in every derived layer within one night.
- **Implementation:** forget probe enumerating every registered layer; fail the build if a layer lacks an erase hook.
- **Resources:** CPU. **Evaluation:** M7; plant, forget, probe at t+0, t+1 night. **Success / failure:** any trace = hard fail.
- **Effort:** S to M (grows with each new layer; do it first).

---

## 5. Metrics for continuity and accuracy

Retrieval accuracy is near ceiling on Zoe's bench; these are the quantities that distinguish "stores things" from "remembers me".
Targets are proposals, to be fixed before any run (pre-registration, as the bake-off did). Intervals: Wilson 95%, as ZMB.

| ID | Metric | Definition | Proposed bar |
|---|---|---|---|
| M1 | **Coverage ratio** | Of turns where the oracle says a stored row was relevant, the share where that row (or an equivalent) was in the prompt. Oracle = owner labels on a 100-turn sample, or a night 12B labeller checked against owner labels on a subsample. | >= 0.5 on eligible turns; report eligible-turn share separately (the "83%" mixes both) |
| M2 | **Uptake** | Of served rows, the share the reply actually used or correctly ignored. | report only |
| M3 | **Irrelevant-mention rate** | Share of replies mentioning a memory item the owner marks unwanted or off-topic. | <= 5% |
| M4 | **Thread carry @ k** | For a thread opened on day d, share correctly acknowledged when the matching topic recurs at d+k, k in {1, 7, 30, 90}. | >= 0.8 at 7, >= 0.6 at 90 |
| M5 | **Re-telling and re-correction rate** | Share of turns where the member re-supplies a fact already stored, or repeats a correction already made. Proxy from transcripts (fact-match of new statements against stored rows). | falling month on month; target -30% in 4 weeks |
| M6 | **Stale-serve rate** | Share of served facts that were superseded or past `review_after` at serve time. Plus STALE three-part score (detect, reject presupposition, apply). | 0 superseded served; STALE-style >= Z0 baseline + interval |
| M7 | **Forget-completeness** | After a forget, hits across every registered derived layer at t+0 and after the next night pass. | 0 at both times |
| M8 | **Cross-member leakage** | Facts of member A appearing in B's prompt or reply, scored jointly with utility and deletion (GateMem shape). | 0; utility not below Z0 |
| M9 | **Opportunity precision / recall** | Planted later-opportunities surfaced / all planted; false candidates per 30 member-days. | recall >= 0.7, <= 1 false per 30 days; accept-rate >= 40% in trial |
| M10 | **Provenance fidelity** | Spoken "how do you know?" answers whose pointer and date are correct; refusal rate on unlabelled rows. | 100% on served, 100% refusal when no pointer |
| M11 | **Attribution rate** | Share of approved rows with subject resolved and provenance label. | 34% -> >= 80% subject; 27% -> >= 90% label (person-centred targets) |
| M12 | **Consolidation safety** | After a full night pass on a synthetic store, user-stated rows whose status changed without a user action. | 0 |
| M13 | **Persona stability** | An ANCHOR-style scenario: months of simulated interaction; does her stance on named household facts and manner stay coherent. | baseline first; no bar until measured |
| M14 | **Cost** | Added prompt tokens p50 / p95, added TTFT, night minutes per member, RAM during the pass. | <= 180 tokens; TTFT inside baseline interval; RAM guard as the bench |

Run the existing ZMB axes (authority, extraction, temporal, multi-session, abstention, forgetting, emotional, identity, poisoning)
unchanged beside these as the no-regression floor, and keep one external family (a LongMemEval-style subset) purely as a
cross-check against the field.

---

## 6. Speculative items (labelled; none recommended for build now)

1. **[Speculative] Style adapter, not memory adapter.** A small LoRA trained on approved Zoe replies in a member's preferred register
   (manner, brevity, warmth) in the night window, applied only to style. It never stores facts, so it does not collide with
   forget or authority. Unknowns: whether a LoRA applies cleanly to the QAT+MTP Gemma build and whether it alters MTP acceptance
   rate; the "rocks" rule (swapping a rock fails CI) needs an owner ruling on whether an adapter counts. [LoRA's low-rank
   regularisation](https://arxiv.org/abs/2405.09673) suggests it would disturb the base less than full tuning.
2. **[Speculative] Per-member nightly adapters with replay** (the LifeMem pattern, [arXiv 2608.19621](https://arxiv.org/abs/2608.19621)).
   Rejected on forget grounds for facts; revisit only if a verifiable unlearning method for adapters appears.
3. **[Speculative] A learned memory-write policy** (the Mem-alpha idea) used offline to tune extractor prompts against ZMB, never to
   update weights.
4. **[Speculative] Spaced "do you still..." check-ins** using a forgetting-curve schedule (MemoryBank) for life-now facts, delivered
   through the pull tier so staleness shrinks without nagging.
5. **[Speculative] "Dream replay" for the associative layer:** at night, replay the day's moments against dormant threads and
   propose links (A-MEM linking restricted to proposals). Pair with E4 once its false-alarm rate is known.
6. **[Speculative] Anniversary and "this time last year" generator** from chapters (E9), which is where autobiographical memory
   becomes felt. Gated by the same leave deny-list.
7. **[Speculative] A second box for the night job** (the DGX Spark question in the 10-29 synthesis). This lane's evidence: nothing
   here needs it; E6 is the only item that wants the 12B for minutes per call, and it is optional. Decide on Phase 3's measurement,
   not on this report.
8. **Unresolved question for the owner:** "Mem∞" (could not be located). Also whether the autobiographical layer (3.7, item 3,
   growth observations) is wanted at all for any member; it is the one item here with real emotional risk, and the answer is an
   owner decision, not a research result.

---

## Appendix: ordering that follows from the evidence

1. E11 (forget-completeness hook rule) and the consolidation-safety cell: cheap, protects everything below.
2. E2 then E1: delivery, the biggest measured gap; both voice-path, replay-gated.
3. E3 cells (so progress is measurable), then E7 and E8 (ageing, contradiction, speakable provenance).
4. E4 (months-scale opportunity) and E10 (manner), then E9 (chapters) if the owner wants Phase 6's growth layer.
5. E5 and E6 only if a bench cell shows headroom or the night window has idle time.

Sources are linked inline. No file other than this one was written.
