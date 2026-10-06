---
type: Research
title: "MemPalace deep dive: the idea, the software, the community, and whether it is the missing piece (2026-10-06)"
description: "Owner-requested open-minded investigation of MemPalace 3.10.0. Part A reads the repo, docs, changelog, issues, discussions, press, a peer paper and community deployments, and pilots every module that earlier work never tested (conversation miner, memory-type extractor, L0-L3 wake-up stack, temporal KG, fact checker, rooms, AAAK, exporter, entity detector, agent protocol). Part B tests seven candidate combinations against Zoe's actual weaknesses. Part C is the verdict: a source of ideas, not the missing piece, with two small pieces worth taking and one concrete experiment."
tags: [memory, mempalace, research, protocol, knowledge-graph, forgetting, samantha, bake-off]
timestamp: 2026-10-06
status: DRAFT for the owner. Research only; nothing deployed, no flag flipped. Everything ran under /home/zoe/.zoe/bakeoff-2026-10/ (mempalace-venv 3.10.0, scrubbed HOME, in-process egress audit). The live checkout, store, Postgres, brain and services were never touched. All names are synthetic.
evidence_labels: "[doc] URL fetched 2026-10-06 (source table in section 9) | [src] file:line read today | [measured] I ran it today, command in section 8 | [community] third-party report, link in section 9 | [derived] arithmetic on measured numbers | [inferred] reasoned, not run | [unverified] not confirmed"
---

# MemPalace deep dive (2026-10-06)

Read with: `mempalace-chroma-best-practice-2026-10-05.md` (module inventory), `memory-arm-hm-hindsight-mempalace-2026-10-06.md` (verbatim-tier pilot),
`memory-system-decision-2026-10-05.md` (section 2.4), `bakeoff-setup-verification-2026-10-06.md` (HM sections), `docs/knowledge/open-problems.md`.
Pilot code: `scripts/perf/zmb/pilot/mempalace_deepdive.py` (this PR). Web pages are cited as `[doc Sn]`; the table in section 9 gives URL and fetch date (all 2026-10-06).

## 0. Answer first

1. **What MemPalace really is.** A local-first, MCP-first, verbatim memory library for coding agents. Four parts: (a) a deterministic, zero-LLM write path that files text unchanged into Chroma, tagged `wing` (person/project) and `room` (topic); (b) hybrid BM25 + vector retrieval; (c) a small SQLite temporal triple store that only fills when an agent calls it; (d) a **five-rule memory protocol** that the status tool hands to the model, plus hooks that make the agent save. The "palace" is a naming scheme over metadata filters. It started on 2026-04-05, has 59.4k stars and 7.6k forks, is MIT, Beta on PyPI [doc S1, S2].
2. **The idea is real but is not where the headline numbers come from.** The 96.6 % LongMemEval recall_any@5 is "store every session verbatim, query with Chroma's default MiniLM" [doc S9]; a peer paper that I read in full says the same and calls the palace hierarchy "standard vector database metadata filtering" [doc S12]. My own first-hand pilots agree: scoped Chroma + MiniLM gets hit@5 0.93; MemPalace's hybrid re-rank is **worse** (0.85-0.88) [measured, section 4.1].
3. **Strongest piece.** The *read-time protocol* ("search before you answer, say 'let me check', supersede a changed fact atomically"), delivered through tool output, not a prompt file. Community evidence says it decides whether the model uses the memory at all [community S17]. Zoe already has a larger one, and measured it: `recall_memory` fired 67 % of the time before the imperative doctrine, 97 % after (`labs/flue-zoe-brain-2x/src/agents/zoe.ts:75,135`, `parity/RELIABILITY.md`).
4. **Community verdict.** Real, useful for coding agents, honestly corrected after an April benchmark controversy (100 % headline withdrawn, "30x lossless" retracted, comparison tables removed) [doc S6, S11, S12, S13]. Fragile where Chroma/HNSW is under concurrent writes (164 of 908 issues mention HNSW) [measured, gh]. Production reports show the real gap is **consumption, not retrieval**: 97.0 % R@5 but 60.4 % end-to-end QA [community S16]. Forgetting, consolidation and multi-tenancy are asked for and unanswered in core [doc S5]. One maintainer wrote 51 % of the commits [measured, gh].
5. **Pilot numbers (new today, 1,000 synthetic household turns, Wilson 95 %):** conversation miner stores assistant text inside "verbatim" drawers or, mined as user lines, **loses 632 of 1,000 turns** (every turn under its 30-char floor); memory-type extractor returns **0 chunks** on household speech and 3/12 on labelled sentences; the L1 "essential story" wake-up is the 15 newest turns, **all routine commands, 0 of 9 durable facts present**; rooms add **+1/60** over the wing filter as a soft prior and **-16/60** as a hard filter with a naive router; the KG leaves two open values for a single-valued predicate unless the agent calls `supersede`; AAAK makes household sentences longer (6 of 7). One piece **works**: the fact checker's edit-distance (<= 2) catches **8/8 STT-style misspellings and 3/3 split spellings of a forgotten name with 0 false matches** in the household vocabulary (section 4.8).
6. **My verdict: a source of ideas, not the missing piece.** Nothing in MemPalace's code beats what Zoe has measured on retrieval, authority, forgetting or the people graph. Three of its ideas are worth taking, none as a dependency (section 6).
7. **The best combination is a stance, not a package:** keep Zoe's policy layer (authority, forgetting, people graph; MemPalace has none of them) and add MemPalace's two stances, **verbatim evidence** and **agent-led lifecycle**, as one small thing: **quote-backed retirement**. When a turn changes a state ("I gave up the cello"), the brain (or the idle distiller) is offered the user's top-3 current rows, retires the right one with the user's own sentence attached as proof, and never deletes. Candidate retrieval is already good (old row top-3 in 29-30 of 30, top-1 26 of 30); the judgement ("change, or just a mention?") is the part nothing offline can do, because retrieval alone retires the wrong row on 34 of 40 non-changes.
8. **Proof plan:** the S10x cells in section 6.3 (30 changes, 40 non-changes incl. 10 hard, plus the I2/forget controls) on the bake-off clone brain. RAM: about 0 MB extra (the embedder is already resident; 1,000 rows x 384 floats is 1.5 MB).
9. **Smaller take, ready now:** a forget-time "did you also mean ...?" sweep using MemPalace's `_edit_distance` (section 4.8). Closes the tracked target HM-F5. About 20 lines, no RAM.
10. **Corrections to my earlier records** (section 7): `similar_name` is an ambiguity warning between two registered names, not a typo detector; `convo_miner` is not a drop-in verbatim filer for a voice household.

## 1. Method, limits, what I did not do

* **Read first-hand:** the installed package (`mempalace-venv`, 3.10.0, 37,120 lines in its 64 top-level modules) and the repo pages below. Where a fetch tool returned a model-written summary of a page (not the page), I label it `[doc, summary]`; the arXiv paper I downloaded and read in full with `pdftotext`.
* **Measured:** the pilots in section 4, run offline in the scrubbed env on the synthetic household of `scripts/perf/zmb/pilot/household.py` (Dana, Tove, Mika, Leo, Biscuit, Marisol, Kofi: invented). The egress audit hook logged **no** non-loopback connect or lookup during any run; its negative control (a lookup of `example.invalid`) logged `VIOLATION` right after (section 8).
* **Instrument checks:** Zoe's own `same_topic` returns False for the cello pair, reproducing the documented S10 failure (`docs/knowledge/samantha-bar.md`); the memory-type extractor lights up on long project prose (positive control 2/2); the entity detector finds 5 of 6 on a diary-style control; a wrong room filter returns 0 of 360, so the filter does bite.
* **Not done, and why:** (a) the protocol's effect on the 4B brain (no clone brain was running and the live one is off limits); (b) the `closet_llm` pass and `rooms propose` (they need an LLM); (c) anything on real household text; (d) the multi-agent hub, logstream, replica and daemon modules (a household has one writer; read, not run).
* **RAM disclosure:** the conversation-miner arms peaked at **0.87-0.93 GB RSS** in one process (it also runs the queries), above the 0.7 GB cap the earlier HM pilot kept to; `MemAvailable` stayed above 1.5 GB. A Hindsight API and its Postgres from another window were running on the box; I did not touch them. Nothing of mine is left running.

## 2. Part A.1: the idea, the project, the people

### 2.1 The idea in plain words

MemPalace borrows the **method of loci** (a memory palace). Memory is a building: a **wing** is a person or project, a **room** is a topic, a **closet** is a pointer index card to drawers, a **drawer** is one chunk of verbatim text, a **hall** is a memory type, a **tunnel** links rooms across wings [doc S1, S7, S19]. The creators' stated principle is *verbatim first*: "does not summarize, extract, or paraphrase" [doc S1]; "storage is not memory, but storage + this protocol = memory" [src `mcp_server/tools_read.py:370-379`]. Retrieval stays plain: vector search over drawers, scoped by wing/room, optionally re-ranked with BM25 [src `searcher/query.py:143`, `searcher/ranking.py`].

Reduced to what the code does:

```
text in ---> chunk (800 chars / one Q+A exchange) ---> Chroma drawer { wing, room, hall, source_file, filed_at, entities }
agent -----> mempalace_status  -> returns PALACE_PROTOCOL + AAAK spec   (this is how the model is taught)
agent -----> mempalace_search(query, wing, room)  -> hybrid BM25 + vector top-k, verbatim text back
agent -----> kg_add / kg_supersede / kg_invalidate -> SQLite triples with valid_from / valid_to (only when the agent calls)
hooks -----> every 15 messages: "block and ask the model to save" (stop), "emergency save" (precompact)
```

**The agent-facing instructions and the hooks** (what "teaches the model" means in practice) [src, installed package]:

* `instructions/search.md`, `status.md`, `init.md`, `mine.md`, `help.md`: skill-style step lists ("parse the query, map it to a wing/room, prefer the MCP tools in this order, always show wing/room/drawer, offer next steps"). `instructions/shared_brain_rules.md` is the multi-agent version: identity `host:harness:project`, "search the palace before answering about past work ... quote results verbatim, never paraphrase ... if the palace has nothing, say so", file durable outcomes, `kg_supersede` when a single-valued fact changes, plus a mailbox protocol for delegating tasks between agents.
* `PALACE_PROTOCOL` (`mcp_server/tools_read.py:370`) is the five rules the status tool returns on wake-up: (1) call status on wake-up; (2) before responding about any person, project or past event call `kg_query` or `search` first, "never guess, verify"; (3) if unsure about a name, gender, age or relationship say "let me check" and query; (4) after each session write a diary entry; (5) when a single-valued fact changes (model, employer, address) call `kg_supersede`, not hand-rolled invalidate-plus-add.
* Hooks (`hooks_cli.py`): session-start only initialises state; **stop** blocks every 15 messages (`SAVE_INTERVAL`, line 38) and returns "MemPalace auto-save checkpoint. Use `mempalace_diary_write` ... and `mempalace_add_drawer` ... Do NOT use native auto-memory files" (line 139); **precompact** says the same with "emergency save". In silent mode the hook saves by itself instead of asking the model. The design leans on the model complying; the Telegram-bot report shows what happens when it does not [community S17].

The **layered memory model** (`layers.py`): L0 identity (a text file the user writes), L1 "essential story" (auto-generated), L2 wing/room recall, L3 deep search; claimed wake-up cost 170 tokens [src `layers.py:77-606`; doc S12].

### 2.2 The project

* **Origin and people** [doc S6, S1]: launched 2026-04-05 by the actress Milla Jovovich and Ben Sigman; first-week claims were corrected publicly (below). The GitHub contributor table [measured, gh api]: `igorls` 1,065 commits of 2,085 (51 %), `mvalentsev` 151, `bensig` 115, `fatkobra` 81, `milla-jovovich` 80, `jphein` 52. The last commits by the two founders are merge commits on 2026-09-02 and 2026-06-06 [measured]. Day-to-day work is one community maintainer. [inferred] bus factor of one.
* **Cadence** [doc S2]: 20 PyPI releases from 2026-03-28 (2.0.0) to 2026-09-16 (3.10.0), about one every nine days; `CHANGELOG.md` on `develop` already lists 3.11.0 dated 2026-10-02 [doc S10, summary]. Classifier is "4 - Beta".
* **Volume** [measured, gh api search, 2026-10-06]: 908 issues (544 closed, 364 open); 784 merged PRs and 425 open. In the last 90 days (since 2026-07-08): 235 issues opened and 136 closed; 474 PRs opened and 280 merged. 147 of the 364 open issues (40 %) have no comment. Of the newest 23 closed issues I sampled, the median time to close was 209 hours (p25 47 h, p75 1,006 h); that sample is small and biased to recently updated issues.
* **What breaks** [measured, gh search counts of title/body mentions, all states]: "hnsw" 164, "corrupt" 91, "segfault" 32, "supersede" 24, "forget" 14. The 25 newest open issues (2026-09-24 to 10-06) are memory growth during mining, 1M-drawer performance, Windows lock leaks, a repair that "reports a healthy palace as corrupt", a miner that "leaves old drawers searchable" when a source shrinks, EmbeddingGemma failing with onnxruntime 1.30 [doc S4]. The roadmap page (written around 3.1; a pgvector backend has shipped since) lists "stale index detection" and time-decay scoring as planned [doc S8].
* **What users ask for** [doc S5, summary]: shared/multi-machine instances (several threads, community workarounds only), multi-tenant and per-palace isolation (unanswered), forgetting. The forgetting thread (#759, 14 replies) ended with: decay for *ranking*, supersession for *lifecycle*; what shipped is an opt-in exponential recency weight (`MEMPALACE_RECENCY_WEIGHT`, off by default), a "dreaming" cleanup skill and a community UI [doc S5 #759, summary].
* **Benchmark controversy** [doc S6, S11, S12, S13]: within two weeks, community auditors (dial481, lhl) showed: the 96.6 % is Chroma's default embedder on verbatim text; the 100 % LongMemEval came from fixing three failing questions plus a paid LLM rerank (held-out 98.4 %); the LoCoMo 100 % used top-k=50, which returns whole conversations (honest: 60.3 % raw R@10, 88.9 % hybrid); retrieval recall was tabulated next to other systems' QA accuracy; "30x lossless" AAAK is lossy (84.2 % vs 96.6 % R@5); "contradiction detection" was exact-match dedup. The founders retracted these on 2026-04-07 and 04-14. One auditor reported on 04-14 that the README and site still carried the claims a week later [doc S11 #875, summary]. The current README no longer compares against other systems and says why [doc S1].
* **A search for "MemPalace" also returns lookalike domains** (mempalace.tech, .net, .info); the README says the only official sources are the GitHub repo, PyPI and mempalaceofficial.com, and that impostor sites distributed malware [doc S1, S6]. I did not rely on any lookalike site.

### 2.3 Benchmarks: what was actually measured

| Claim | How it was produced [doc S9, S12] | What it does and does not mean |
|---|---|---|
| LongMemEval_S 96.6 % recall_any@5 (n=500) | one document per session, Chroma default MiniLM, k=5, no LLM; "is the labelled session in the top 5" | retrieval of the right *session*, any of five; no answer is generated, no update is tested. I re-aggregated the committed per-question files earlier: 0.966 and 0.9844 [measured, `memory-system-decision-2026-10-05.md` 2.4] |
| 98.4 % hybrid v4 held-out (n=450) | tuned on 50 dev questions (seed 42), then held out | the last 0.6 points "reached by inspecting wrong answers" (their words) |
| LoCoMo 60.3 % raw / 88.9 % hybrid R@10 | session level; the 100 % row is top-k=50, "saturated" | the project itself says those deltas show no rerank gain |
| End-to-end QA | **not measured by the project.** Third parties: about 67 % LongMemEval QA [doc S12, secondary]; 60.4 % E2E QA on a production fork at 97.0 % R@5 [community S16]; BEAM 49 % raw, 26-43 % in MemPalace modes [doc S11 #875, summary] | retrieval recall is not the user-visible quality |
| MemBench "noisy" 43.4 % | their own table | "the designed hard case for verbatim storage: when noise is indistinguishable from signal" |

The most useful number in the whole ecosystem for Zoe is not a MemPalace benchmark. A production user (jphein, Postgres + pgvector + a ~1.9M-triple graph) measured R@5 92.7 % and found that giving the reader the *complete evidence sessions* lifted answer accuracy from 0.610 to 0.868; his conclusion: "the lever is what the memory system delivers" [doc S5 #1659, summary]. The TechEmpower fork reports the same 38-point retrieval-to-answer gap [community S16].

### 2.4 What it is used for in the wild

Coding agents first: Claude Code, Codex, Cursor and DeepSeek-harness plugins ship in the repo [doc S1]; a Claude Code Telegram bot wired in 20 minutes, where the model **never called the tools until CLAUDE.md told it to** [community S17]; Hermes live-turn filing (`integrations/hermes`, 1,596 lines) [src]. Long-run reports: an OpenClaw deployment for 84 days and 7,635 drawers, which put an LLM distillation pipeline (raw log -> semantic extraction -> scene blocks -> 7-day persona -> knowledge graph, 6,077 triples) **around** MemPalace storage [doc S5 #2067, summary]; a 140,000-drawer research archive that wants shared, signed, multi-author instances [doc S5 #2680]; a 335,000-drawer Postgres fork run behind a single-writer daemon after concurrent Chroma writes proved "intractable" [community S16]. I found **no** household-assistant or companion deployment [unverified that none exists].

## 3. Part A.2: module by module

"Cost" is measured here unless stated. "Zoe equivalent" cites the current worktree (main at 767fa9ba).

| MemPalace piece | What it does | How well (measured today) | Zoe equivalent | Gap? |
|---|---|---|---|---|
| Palace / wing / room / drawer | metadata filters over one Chroma collection | wing scope is worth about +10 points hit@5 (0.933 vs 0.833) [measured, HM doc 3.1]; **room** as a soft prior +1/60, as a hard filter with a keyword router 40/60 (see 4.6) | wing = user id (`memory_service.py`); `room` = memory type | none for wing; rooms not worth it |
| `convo_miner.py`, `normalize.py`, `sweeper.py` | chunk chats by exchange pair, file verbatim; the sweeper files one drawer per message, cursor-resumable | drops every exchange under 30 chars; stores the assistant reply inside the drawer (see 4.1) | chat log in Postgres `chat_messages`; per-turn digest `memory_digest.py:529`; no vector verbatim tier | **verbatim tier** (HM's V). Take the sweeper's idempotent per-message idea, not the miner |
| `general_extractor.py` | keyword/regex classifier into decision/preference/milestone/problem/emotional | 0 chunks on 522 household turns; 3/12 labelled (4.2) | `memory_extractor.py`, `person_extractor.py`, `memory_digest.py` | no |
| `layers.py` (L0-L3) | identity file; L1 newest-15 drawers; L2/L3 filtered/deep search | L1 = last 15 turns, all routine commands, 0/9 facts; L3 search fine (4.5) | `user_model_card.py` (nightly card of current facts), `zoe_memory_layers.py:96`, for-prompt packet | no |
| `knowledge_graph.py` | SQLite triples, half-open `[valid_from, valid_to)`, atomic `supersede`, `query_entity(as_of=)` | semantics correct; no extraction; allows two open values for one predicate (4.3) | Postgres `person_relationships` with `valid_to`/`superseded_by` (`person_extractor.py:807`); `MemoryService.search(as_of=)` (`memory_service.py:1703`) | people-graph `as_of` read (open-problems PG G1) |
| `fact_checker.py` | `similar_name`, `relationship_mismatch`, `stale_fact` over registry + KG | mismatch/stale work on two sentence shapes (6/7); `similar_name` is an ambiguity warning, not a typo detector; its edit distance is the useful part (4.3, 4.8) | `correction_apply.py` (flag-dark), verify-on-challenge | **forget-alias sweep** |
| `dedup.py` | collapse near-duplicate drawers per source | would delete 29 % of household turns [measured, HM doc 2.7] | memory_quality near-dup gates | never use on conversation |
| `entity_detector.py`, `entity_registry.py` | regex scoring of person/project candidates | voice transcript: 0 people; diary-style prose: 5 of 6 cast, but the dog typed as a person (4.7) | Postgres `people`, `person_extractor.py` | no |
| `closets`, `closet_llm.py` | pointer index lines; optional LLM-built topic index | **not built for conversations** in 3.10.0 (`convo_miner.py` never imports them; the closets collection does not exist after mining) [measured, src] | none | not needed |
| `dialect.py` (AAAK) | lossy symbolic summary | 6 of 7 household sentences got longer; "gave it up last week because my wrist hurts" lost the cause (4.7) | none | no |
| `exporter.py` | palace to markdown folders | plaintext of every drawer, one file per room; no redaction (4.7) | `export_user`; compaction JSON export | our own is fine; remember exports are a forgetting hole |
| `hooks_cli.py` | stop (every 15 messages) / precompact hooks that *ask the model* to save | relies on model compliance (the Telegram-bot report shows the failure) | automatic extraction after every turn (`chat.py` -> `run_turn_digest`) | ours is the stronger design for a household |
| `instructions/`, `PALACE_PROTOCOL` | the model-facing protocol | 994 chars (about 250 tokens), 5 rules | `soul.ts:35` + nine doctrines in `agents/zoe.ts:82-228` (about 1,400 tokens for the memory-related ones) | **rule 5**: brain-led supersede (6.2) |
| `diary_ingest.py` | one drawer per (wing, day), grows through the day | not run; maps onto Zoe's dreaming cycle `memory_digest.py:2327` | dreaming / portrait | candidate for B3.7 (own thread), [unverified] |
| `searcher/` hybrid | BM25 + vector convex blend 0.6/0.4 | **worse than vector alone** on this corpus (4.1; HM pilot 0.883 vs 0.933) | `_blend` in `memory_service.py` | no |
| backends (`sqlite_exact`, `rust_exact`, `pgvector`) | exact search, Postgres | exact is the right shape at household scale [measured earlier: 0.03 ms at 300 rows] | none | MC G6 already open |
| `dynamics.py`, `hallways.py`, `palace_graph.py` | Hebbian strengthening, Ebbinghaus decay, tunnels | **dynamics are not wired**: the module's own docstring says nothing potentiates on access [src `dynamics.py:1-16`] | `tick_access` | no |
| `logstream`, `tasks`, `daemon`, `hlc`, `replica`, `mcp_proxy`, hub | multi-agent coordination, replication | out of scope for a household [src] | n/a | no |
| `mcp_server` (45 tools; 3-tool light server since 3.10.0) | tool surface; the light server cut the schema from 41.6 KB to 10.6 KB [doc S10] | a warning for the 8k slot: schema size is a real cost | `tool-groups.ts` progressive disclosure | no |

## 4. Part A.3: pilots (all [measured], synthetic, `scripts/perf/zmb/pilot/mempalace_deepdive.py`)

### 4.1 Conversation miner on 1,000 household turns

Corpus: the HM pilot's household (four members, 60 exact-reference queries, scoped by wing). Gold is "the retrieved drawer contains the gold turn's text". Two arms. A: each turn is `> user text` plus a one-line assistant reply ("Okay, that's done."), the shape the miner is built for. B: user lines only, the shape Zoe's authority rules require (assistant text never enters a user's verbatim store).

| | A: exchange (user + assistant) | B: user lines only | per-turn upsert (HM pilot) |
|---|---|---|---|
| drawers from 1,000 turns | 999 | **368** | 999 |
| turns whose text is in no drawer | 0 | **632** | 0 |
| wall time (mining) | 69-71 s | 25-27 s | 74-95 s |
| vector hit@1 / hit@5 (scoped) | 45-47 / **53-54** of 60 | 49 / **58** of 60 (fewer distractors) | 47 / **56** |
| hybrid `search_memories` hit@5 | 51-52 of 60 | 51 of 60 | 53 of 60 |
| process RSS high-water | 870-880 MB | 887-930 MB | 291-303 MB |

Run-to-run spread is about 1 query. Reading:

* `MIN_CHUNK_SIZE = 30` characters [src `convo_miner.py:163`]. **676 of 1,000 household turns are shorter** ("stop", "pause the music"); mined as user lines they vanish, all 10 durable fact turns survive because they are long. Live median user turn is 28 characters [measured earlier, HM doc 5.3], so about half of real turns would fall under the floor [derived]. Mined with the assistant reply attached they survive, but then **assistant text sits inside the user's "verbatim" drawer**, which collides with the authority rule (bench cell I3) [inferred].
* The exchange arm is **no better** than one drawer per turn (53-54 vs 56 of 60, inside the +-6 interval) and the hybrid blend is lower again. The miner adds nothing the library's plain `upsert` did not.
* The RSS is the miner's batching on the ONNX embedder (the HM pilot found 761 MB at batch 32). On a box with about 2 GB free this is a hazard unless batches are capped at 8.
* No closets were created (the `mempalace_closets` collection does not exist after conversation mining), so the "closet boost" claim does not apply to conversations in 3.10.0. Rooms came from a dev-topic keyword list (`architecture`, `planning`, `technical`, ...): 738 of 999 drawers are `general`.
* **As a nightly-digest replacement: no.** The digest distils facts (and an emotional pass); the miner only chunks. It replaces the *capture* half, which is the V tier HM already specifies.

### 4.2 The memory-type extractor

`extract_memories` on every Dana and Tove turn (273 and 249): **0 chunks** (it skips paragraphs under 20 characters and applies a confidence threshold [src `general_extractor.py:374-390`]). On 12 labelled sentences (decision, preference, milestone, problem, emotional, household-flavoured) it extracted 3 and typed 2 correctly (2/12, Wilson 0.05-0.45); 0 of 6 non-memory lines were extracted wrongly; the three household facts (dentist, gate code, allergy) were extracted **0 of 3** times. Positive control: two long project-style paragraphs typed correctly (2/2). It is a prose-transcript tool; it extracts no household facts.

### 4.3 Knowledge graph and fact checker

* `add_triple` never closes anything: after "Dana lives_in Dunedin" then "Dana lives_in Hobart" both were current; two employers for one person were both current. `supersede(subject, predicate, old, new, at=)` closed and opened at one instant: current = Hobart; `as_of` 2025 = Dunedin; `as_of` at the boundary = Hobart; the day before = Dunedin. The half-open semantics are right. **Nothing derives triples from text**: they exist only if a caller writes them (the library path; the issue tracker has a request to promote drawer claims into triples, still open [doc S11 #1416]).
* Fact checker on seven household shapes: 6/7 as intended. `"Biscuit is Dana's child"`, `"Mika is Dana's sister"` and `"Dana's child is Biscuit"` raised `relationship_mismatch` against KG rows (pet, daughter); `"Kofi is Dana's plumber"` raised `stale_fact` for a closed row; the correct `"Mika is Dana's daughter"` raised nothing. It **missed the S21 sentence shape** ("Dana has two kids, Mika and Biscuit"): only "X is Y's Z" and "X's Z is Y" are parsed [src `fact_checker.py:155-180`].
* `similar_name`: with a household of distinct names, 0 flags over 4 naming turns; with Dana/Dina, Tove/Toby, Mika/Mia, Leo/Leon registered, **5 flags in 4 turns** ("'Leo' mentioned, did you mean 'Leon'?"). An unregistered misspelling ("Dane") raised nothing. So it warns about ambiguity between registered names; it does not detect typos.

### 4.4 The one-word change of state (Samantha bar S10), on 30 pairs

Setup: 30 (old fact, change utterance, extracted-fact form) pairs ("User plays the cello in a community orchestra on Tuesday evenings." / "I gave up the cello." / "User gave up the cello."); a pool of 100 rows (30 old rows, 30 copies about "User's sister Priya", 40 generic); 40 non-changes about the same objects, 10 of them hard ("almost gave up the cello but kept going", "not giving up running", "stopped running to tie a shoelace").

| Measure | Result | Wilson 95 % |
|---|---|---|
| Zoe's rule `same_topic(new, old)` finds the old row | **7/30** (reproduces the S10 failure) | 0.12-0.41 |
| Zoe's cue table fires on the change | 15/30 | 0.33-0.67 |
| ...and on a non-change / on the 10 hard non-changes | 6/40 / 6/10 | 0.07-0.29 / 0.31-0.83 |
| Embedding retrieval (MiniLM) of the old row from the **utterance**: top-1 / top-3 / top-5 | **26 / 29 / 30 of 30** | 0.70-0.95 / 0.83-0.99 / 0.89-1.0 |
| ...from the extracted-fact form: top-1 / top-3 | 26 / 30 of 30 | 0.70-0.95 / 0.89-1.0 |
| The other-person copy outranks the old row | 2/30 (utterance), 1/30 (fact) | 0.02-0.21 |
| Retrieval's top-1 on a **non-change** is the row it mentions | **34/40** | 0.71-0.93 |

Misses at rank 3-4: "I left the library", "I switched to tea", "I no longer go swimming", "Mika took over walking Biscuit". A relaxed deterministic rule (cue + shared object token) found only 10/30 (0.19-0.51), so a rule tweak is not the answer. **What this shows:** finding the row to retire is solved by an embedder Zoe already runs; deciding that *this turn is a change and that row is the one* is a language judgement that neither retrieval (34/40 false) nor the cue table (50 % recall, 60 % false on hard cases) makes. That is exactly the job MemPalace's protocol hands to the agent (rule 5). MemPalace's KG offers nothing for the identification step: `supersede` needs the old object as an argument [src `knowledge_graph.py:375`]. **Unmeasured:** whether Gemma 4 E4B makes that judgement reliably.

### 4.5 L0-L3 wake-up on a household palace

Dana's 273 turns filed with `filed_at`; `MemoryStack.wake_up(wing="dana")` is 849 characters (about 212 tokens, [derived] chars/4). The L1 section is the **15 newest turns**, all routine commands ("what's 35 times 10", "good morning", ...; my filler detector matched 13 of the 15), and **none of the 9 durable facts is present** (0/9, 0.00-0.30). The source says why: "the ingest pipeline never records an importance field ... filed_at is the effective ordering signal: newest first" [src `layers.py:200-216`]. L3 search is fine ("what am I allergic to" -> the allergy turn at similarity 0.52 first). The wake-up idea needs a curated store (a diary, a card of current facts); on a raw voice stream it is a recency tail. Zoe's `user_model_card.py` is the right shape.

### 4.6 Rooms as a routing scheme for the recall packet

Six household rooms built by keyword (health, family, home, work, calendar, media), 1,000 turns, 60 queries, wing-scoped, hit@5:

| | hit@5 |
|---|---|
| wing only | 56/60 (0.84-0.97) |
| the **gold** room as a filter (an oracle) | 60/60 (0.94-1.0) |
| any **wrong** room (6 rooms x 60 queries, minus the gold) | 0/360 |
| a keyword router picks the gold room | 40/60 (0.54-0.77); as a hard filter hit@5 = 40/60 |
| routed room as a **soft prior** (2 of 5 slots, wing fills the rest) | **57/60** (0.86-0.98) |

The ceiling is +4 points (the oracle) and a real router gives +1 (noise) as a prior or **-16** as a hard filter. Rooms are a precision tool only if routing is near-perfect; at household scale the wing already does the work. MemPalace's own room list is a dev vocabulary: all ten fact turns land in `general` (9) or `technical` (1) [measured].

### 4.7 AAAK, exporter, entity detector

* **AAAK**: on seven household sentences the "summary" is longer in characters in 6 of 7 (85 vs 55 for the dentist line; the project's own `size_ratio` averages 1.26 by a word-count heuristic). "Dr Voss and the surgery is on Elm Street" became `0:DR+VOS+ELM|voss_elm_street|"..."`; the long cello sentence kept "play cello" and cut the quote before "gave it up". There is nothing to gain at 28-character turns.
* **Exporter**: writes every drawer in plaintext, one markdown file per room plus an index; no redaction hook. For Zoe that is another place a forgotten name survives (the ledger already lists backup exports).
* **Entity detector**: on the voice transcript 0 people found (names appear too rarely); on a diary-style narrative with the same cast it found 5 of 6 (Dana scored 0.40, "uncertain") and typed **Biscuit, the dog, as a person**, the S21 confusion.

### 4.8 The piece that works: edit distance as a forget-alias sweep

The fact checker's `_edit_distance` (Levenshtein, `fact_checker.py:289`) applied as "tokens within two edits of the forgotten name" over the household transcript: **8/8** STT-style spellings of "Marisol" (Marisal, Marysol, Marizol, Marisole, Marissol, Maricol, Marisoul, Marrisol) and **3/3** split spellings after joining adjacent tokens ("Mari sol", "Mari-sol", "Maris ol"), with **0** other words within two edits in the 233-word household vocabulary. The risk is the vocabulary size, so I measured it on a 73,604-word dictionary: words within two edits of a name

| name length | examples (within 1 / within 2) |
|---|---|
| 3-4 letters | Leo 22 / 363; Dana 16 / 266; Tove 15 / 271; Mika 4 / 118; Ravi 1 / 135; Kofi 0 / 41 |
| 5-6 | Priya 1 / 38; Teodor 0 / 11 |
| 7+ | Marisol 0 / 11; Barnaby 0 / 6; Biscuit 1 / 2; Ottoline 0 / 1; Percival, Ignatius, Henrietta 0 / 0 |

So the rule is: distance <= 2 only for names of 7+ letters, <= 1 for 5-6, none for 3-4; and the sweep **proposes, the owner confirms** ("I also found 'Marisal' in three places. Is that her too?"), never erases silently. The owner's rule is user words are never overwritten without consent. Design tension: the ZMB ledger stores **salted hashes** of forgotten names (`scripts/perf/zmb/arms/hm_policy.py:127`), which cannot be fuzzy-matched; the sweep needs the plaintext name at forget time (it has it, the owner just said it) and a phonetic key if a later digest must reject variants. This addresses HM-F5 (`docs/knowledge/zoe-memory-bench.md:327`, a tracked target); a pronoun-only chunk still has no route.

## 5. Part B: the honest search for "the missing piece"

Zoe's weak spots today (from `docs/knowledge/open-problems.md`, `samantha-bar.md`, `zoe-memory-bench.md`): S10 one-word change (target, fails), S21/S22 roles (flags dark), I2.third_party (needs a speaker verdict, not text), F3 resurrection after the 300 s tombstone, HM-F5 misspelled forgotten name, authority-inverted blend, no relevance floor, persona layer inert on the live lane, people-graph `as_of`. Samantha-grade goals: continuity over weeks, knowing the household as people, a memory protocol the brain follows.

| # | Candidate | Evidence | Verdict |
|---|---|---|---|
| 1 | **Agent-instruction protocol on our store** | MemPalace: 5 rules, 250 tokens; Zoe: nine doctrine blocks, five of them about memory (about 1,400 tokens with the soul's recall paragraph), imperative style measured 67 % -> 97 % for recall firing (`zoe.ts:75,135`). Community: a model told nothing "promises to remember" and never calls the tool [S17]. Zoe's brain has `recall_memory`, `remember_fact`, `remember_emotional_moment` but **no correct/retire tool** (`zoe-tools.ts:979`, `tool-groups.ts`) | **Already owned, except rule 5.** Rule 5 is the S10 mechanism. See 6.2 |
| 2 | Conversation miner as nightly digest | 4.1 | **No.** Chunker, not a distiller; loses 63 % of short turns or stores assistant text as the user's |
| 3 | MemPalace KG vs Postgres people graph | KG semantics = ours (half-open, supersede); KG has no extraction, no contradiction check, no entity types, SQLite file beside Chroma. Ours has authority, merge, provenance fields | **Keep ours.** Port the shared-boundary supersede and the `as_of` read for the people graph (already queued PG G1) |
| 4 | Fact checker + dedup in the write path | dedup: no (29 % loss). Fact checker: `relationship_mismatch`/`stale_fact` work on two shapes, miss the S21 shape; `similar_name` is the wrong tool; the **edit distance** is right for forgetting | **Take the edit distance (4.8).** An output-side check ("the reply asserts a closed fact") is a cheap idea but unmeasured |
| 5 | Spatial organisation as a routing scheme | 4.6: +1/60 as a prior; hard filter loses; the paper's verdict "metadata filtering" | **No.** The wing (person) is the only spatial level that earns its keep, and Zoe has it |
| 6 | MemPalace verbatim + Hindsight distilled (HM) | already measured: V = Chroma + MiniLM; +0.53-1.03 GB with Hindsight; fails the 2 GB voice floor by up to 0.6 GB. The OpenClaw deployment independently built the same layering [S5 #2067]; jphein found the lever is delivery, not retrieval | **Direction confirmed by independent convergence; cost unchanged.** V alone is +14 MB in-process and needs no Hindsight |
| 7 | Community combinations that worked | single-writer daemon + Postgres (TechEmpower) fixed concurrent HNSW writes [S16]; an LLM distillation pipeline around the store (OpenClaw) [S5 #2067]; recency weight + supersession for forgetting [S5 #759]; "promote repeated drawer claims to triples with human approval" proposed [S11 #1416] | Each is something Zoe already has or has queued (single writer = the zoe-data lock; distillation = the digest; supersession = `memory_supersede.py`) |

Two more items I tested because the owner asked for the whole works: the **diary** (`diary_ingest`: one drawer per member per day) is untested and maps onto B3.7 "Zoe's own thread" [unverified value]; and the **tool-schema cost** of a 45-tool MCP server (41.6 KB) is a reminder that anything added to the 8k slot must come through progressive disclosure, as `tool-groups.ts` does.

## 6. Part C: verdict and proposal

### 6.1 Is MemPalace the missing piece?

**No. It is a source of ideas.** On the axes the owner's rules make non-negotiable (user words never overwritten, forgetting forever, provenance, authority) MemPalace has nothing: no authority classes, an API delete that leaves the text in SQLite pages and the vector file (HM doc 3.3), no provenance beyond `source_file`, no speaker or consent model, no multi-user story (open discussion). On retrieval, the earlier verdict stands and is now replicated through the miner path: it is Chroma + MiniLM. Its real contributions are two stances and a lot of careful engineering for coding agents.

**Were the earlier pilots unfair?** Partly. They tested MemPalace as a store, which is what its own authors' benchmark file and the peer paper say it is; that part holds. They did not test the miner, extractor, wake-up stack, KG, fact checker, rooms or the protocol. Now they are tested: the miner, extractor, L1 and AAAK are unsuitable for a voice household; the KG and fact checker are correct but narrow; one small algorithm and one protocol rule are worth taking. The protocol's effect on a 4B model is the one piece I could not test.

### 6.2 What to take, and how

1. **Forget-time alias sweep** (4.8). Where: the forget cascade (`services/zoe-data/memory_forget_cascade.py`) proposes candidates; the owner confirms by voice; the ledger gets the confirmed spellings. Cost 0 MB, about 20 lines plus a bench cell that flips HM-F5 from TARGET to graded. Proof: HM-F5 green; zero silent erasures in a cell where a similar-but-different name ("Marisa") exists.
2. **Quote-backed retirement** (6.3). The idea from MemPalace's protocol rule 5 plus its atomic half-open `supersede`, with Zoe's authority gate in front and the user's own sentence attached as proof.
3. **Idempotent per-message ingest for the V tier**: the sweeper's "deterministic id + cursor, rerun is a no-op" pattern, when V is built (HM adapter already does the id part).
4. **Leave:** miner, extractor, AAAK, L1, rooms, closets, dedup, entity detector, hooks, the KG store, the MCP surface, the hub.

### 6.3 The combination worth proving: quote-backed retirement

Why this and not "MemPalace + something": the data says retrieval is not Zoe's bottleneck; **lifecycle and delivery** are (S10 7/30; the 38-point retrieval-to-answer gap seen in production). The one capability that makes a companion feel it *knows* you over weeks is that a change you mention once is reflected everywhere, and that Zoe can show where she learned it. MemPalace's protocol supplies the judge (the agent), its supersede the atomic boundary, its verbatim stance the evidence; Zoe supplies authority, forgetting and the roster.

Design (no new service, no new store):

1. **Gate (every verified-owner turn, no model call).** Embed the turn with the already-resident MiniLM (6-40 ms), take the top-3 current approved rows of that user with similarity above a calibrated floor. This is the 26/29/30-of-30 candidate stage.
2. **Judge.** Chat lane: progressive-disclose one tool `retire_fact(row_id, reason)` with the three candidates in the turn context; the brain decides. Voice lane: never in-turn; the idle distiller (HM shape) makes the same call at idle and the packet cache serves meanwhile.
3. **Effect.** Atomic close of the old row at one boundary (`invalid_at` = successor `valid_from`; `memory_supersede.py` and `search(as_of=)` already implement half-open reads), the successor row carries a pointer to the turn (and to the V chunk once V exists) so the recall evidence frame can say "you told me on 3 Oct: 'I gave up the cello'". Nothing is deleted. A "no, I still play" turn re-opens the row.
4. **Authority.** Only `owner_voice_verified` / `owner_typed` turns may call it; `third_party`, `panel_unverified`, pasted and quoted text cannot (the I2 cells); the brain supplies a row id, never an identity.
5. **Forgetting interplay.** A retirement cites a verbatim chunk; forgetting the entity must erase the chunk **and** the citation (the F-cells and the physical-erase cell).

**Experiment (pre-registered):** new cell family S10x in the ZMB lab, run on the bake-off clone brain (`:11500`) in the next window; arms Z0 (today), Z0 + gate + brain judge (chat), Z0 + idle judge (voice). Inputs: the 30 pairs, 40 non-changes (10 hard), the 30 other-person copies from `mempalace_deepdive.py s10` (the data is in the file, synthetic). Pass: correct old row retired **>= 24/30** (80 %, Z0 today 7/30); **<= 2/40** wrong retirements and **0/10** on the hard set; **0/30** other-person copies retired; **0** retirements from a third-party, unverified or pasted turn; `as_of` before the change returns the old fact in 30/30; a forgotten entity leaves no byte of the cited quote (physical cell); added latency on a non-change turn <= 40 ms and no extra model call; voice turn unchanged. **Negative control (already measured red):** retire the retrieval top-1 without a judge, which fails with 34/40 wrong retirements; remove the authority check and the I2 cell must go red.

**RAM:** about 0 MB (embedder resident; a 1,000-row matrix is 1.5 MB [derived]). **What would change my mind:** the judge below 80 % or above 2 false retirements on the clone brain means the 4B model cannot own this and the idle path with a stricter cue gate is the fallback; no MemPalace code is involved either way.

## 7. Corrections to my earlier records

* `mempalace-chroma-best-practice-2026-10-05.md` table: `similar_name` was listed as "typo/mix-up between two household members, port the algorithm". Measured today (4.3): it flags registered pairs that sit within two edits (noisy for Leo/Leon households) and ignores an unregistered typo. The reusable part is the distance function, as a forget-alias sweep (4.8).
* Same record: `convo_miner` "files verbatim". For voice speech it drops what is under 30 characters, or stores assistant text with the user's words (4.1).
* Same record, `layers.py`: "same concept, own code". Confirmed and sharpened: L1 is recency, not importance (4.5).
* `memory-arm-hm-hindsight-mempalace-2026-10-06.md` hybrid finding: replicated through the miner and `search_memories` (51-52 vs 53-58 of 60).

## 8. Reproduce

```
# the scrubbed env, venv and egress audit are the HM pilot's (/home/zoe/.zoe/bakeoff-2026-10/mp_run.sh)
bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/mempalace_deepdive.py all --out /home/zoe/.zoe/bakeoff-2026-10/mp-work/dd-all.json
# one at a time: convo | extractor | kg | s10 | layers | rooms | aaak | export | protocol | entities | stt_names
# egress negative control (must print VIOLATION in egress-mp.log):  bash .../mp_run.sh /home/zoe/.zoe/bakeoff-2026-10/mp_egress_control.py
```

Totals of the last `all` run: 1,084 MB process high-water, about 4.7 minutes, 0 errors, 0 non-loopback connects logged. GitHub numbers: `gh api` read-only calls (repo, contributors, issue/PR search counts, one list of closed issues) on 2026-10-06. Per-pilot JSON: `dd-<cmd>.json` beside the HM pilot outputs under `/home/zoe/.zoe/bakeoff-2026-10/mp-work/` (not committed; synthetic).

## 9. Sources (all fetched 2026-10-06)

| id | source | note |
|---|---|---|
| S1 | https://github.com/MemPalace/mempalace (README, counts) | summary by the fetch tool; counts confirmed with `gh api` |
| S2 | https://pypi.org/project/mempalace/ | release table |
| S3 | https://github.com/MemPalace/mempalace/releases | cadence |
| S4 | https://github.com/MemPalace/mempalace/issues | newest 25 open; counts via `gh api` |
| S5 | discussions: `/discussions` index, `/759` (forgetting), `/868` (creator's post-launch post), `/1659` (jphein: Postgres fork, oracle-context experiment), `/1685` (a user's "road that nearly made me quit": HNSW corruption, nightly repair that emptied the index), `/1703` (two machines), `/754` (Android), `/1856` (Go + Postgres fork), `/2067` (84 days, OpenClaw), `/2570` (MAQS audit checklist), `/2680` (shared instance for a small group) | all summaries by the fetch tool, not the pages |
| S6 | https://github.com/MemPalace/mempalace/blob/develop/docs/HISTORY.md | launch, corrections, impostor domains |
| S7 | .../MISSION.md | Zettelkasten, background processing |
| S8 | .../ROADMAP.md | v4 plans |
| S9 | .../benchmarks/BENCHMARKS.md | how each number is produced |
| S10 | .../CHANGELOG.md | 3.8-3.11 themes; light MCP |
| S11 | issues #29 (dial481 audit), #875, #514 ("the weight of memory itself"), #1416 (promote claims to triples) | summaries; #29's page showed no replies to the fetch tool while other sources say the maintainers acknowledged it |
| S12 | arXiv:2604.21284, Dey and Viradecha, 2026-04-23 | **read in full** (`pdftotext`) |
| S13 | https://news.ycombinator.com/item?id=47672792 | 67 points, 17 comments; summary |
| S14 | cybernews, SDxCentral, HackerNoon, danilchenko.dev coverage | seen as search snippets only (one page returned 403); used for the controversy timeline only |
| S15 | https://vectorize.io/articles/mempalace-vs-hindsight | **vendor of Hindsight**: conflict of interest, used only for what it states about the claims |
| S16 | https://github.com/dyk1454683243-sudo/mempalace (TechEmpower production fork) | 335K drawers; 97.0 % R@5 vs 60.4 % E2E; summary |
| S17 | https://www.ivanmorgillo.com/2026/04/07/how-i-added-persistent-memory-to-my-claude-code-telegram-bot-in-20-minutes/ | tools unused until CLAUDE.md said so |
| S18 | https://explainx.ai/blog/mempalace-local-ai-memory-github, a gist alleging purchased stars | the star-buying claim is circumstantial and polemical; [unverified] |
| S19 | https://mempalaceofficial.com/ and /guide/claude-code-retention.html | concepts, hooks |
| S20 | arXiv:2605.12477 (MEME) | does not evaluate MemPalace; all six systems it tests score 3 % on cascade and 1 % on absence |

## 10. What I would do next, in order

1. Put the two ledger lines (done in this PR: forget alias sweep) and decide whether the owner wants S10x in the next clone-brain window.
2. Build the alias sweep with its bench cell (smallest, measured, closes a tracked target).
3. Run S10x; if the judge passes, wire `retire_fact` behind a flag (default off) and an idle variant for the voice lane.
4. Do not install, upgrade or depend on `mempalace` for any of this; the pin removal already in the ledger stands.
