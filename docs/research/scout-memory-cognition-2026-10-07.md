---
type: Research / scouting
title: "Scout: memory engines and cognitive architectures for a local companion (2026-10-07)"
status: Research only. Nothing installed, nothing run, no live store touched. Time-boxed scouting pass.
date: 2026-10-07
slice: memory engines + cognitive architectures (reflection, user model, initiative, persona continuity)
evidence_labels: "[gh] GitHub REST API read 2026-10-07 (stars, SPDX licence, commits in last 90 days, distinct commit authors; 'commits90d 100' is the API page cap, read it as 100 or more) | [readme] README / docs read first-hand today | [web] URL fetched today | [unread] named in a search result, not opened | [unverified] not measured"
---

# Scout: memory engines and cognitive architectures (2026-10-07)

## 0. Answer first

1. **Nothing maintained closes the companion gaps.** The big maintained memory projects (MemOS, EverOS, OpenViking, ReMe, memU, agentmemory) are built for coding and tool-using agents: they remember tasks, skills and preferences. None models a household of several people, none decides to speak first, none measures whether the persona stays itself. The companion-shaped work (user profile, dreaming, affect, initiative) lives in young, one-to-three-person projects.
2. **Hindsight stays the lead candidate** (not the live engine: the run-2 verdict is `KEEP_Z0` - no arm passes every floor, Zoe's own memory stays and the engines are not adopted, see `docs/research/memory-bakeoff-decision-2026-10-08.md`; live memory is still Chroma / MemPalace). Nothing found beats it on the local gates already measured (small extraction prompt, Gemma-4 default, pgvector). This scout adds one second arm, three idea donors, and three evaluation instruments.
3. **The most useful find is not an engine: it is the evidence that nobody has solved your problem.** A July 2026 paper (ANCHOR, "Best Friends, Not Forever") ran 2,008 companion conversations over 27 personas: trajectory accuracy averaged 44.4 percent, user-state recall stayed near chance, and no tested memory setup fixed persona collapse. So "stays herself over months" is a measurable open problem, and a scored scenario for it is a differentiator for your bake-off, not a thing to buy.
4. **Authority is still unbought.** The only project whose design matches your authority doctrine is `tigerless-labs/agent-memory` (unattended pass may add and update; deletion only as a proposal the user confirms; supersede keeps the chain). EverOS and kiwi-mem both resolve conflicts newest-wins, so they must sit under `memory_authority.py`.

## 1. Top 5, ranked

Ranking rule: gap closed for Zoe first, then maintenance (ties go to the maintained project), then fit with the thin layer.

| # | Candidate | Verdict | Why it is ranked here |
|---|---|---|---|
| 1 | **EverOS** (`EverMind-AI/EverMemOS`) | **ADOPT-TRIAL** (second bake-off arm, user-profile + reflection only) | Only maintained engine with a first-class per-user profile track AND offline reflection, markdown source of truth, soft-archive with provenance, local SQLite + LanceDB (no Postgres). 81 commits / 8 authors in 90 days. Needs authority wrapped around it. |
| 2 | **kiwi-mem** (`LucieEveille/kiwi-mem`) | **BORROW** (ideas) | The richest bundle of "stays herself over months" mechanics: emotion-weighted heat and decay, nightly blur, day-week-month-quarter-year calendar summaries, a three-layer dream, user locks that never decay, machine locks that expire. Single-user, a proxy gateway, AGPL: do not run it, take the ideas. |
| 3 | **agent-memory** (`tigerless-labs/agent-memory`) | **BORROW** (design), watch for a trial | The only design with your authority rule: sleep-time Manage with authority tiers, delete-as-proposal, supersede chain, `recall --as-of`. No LLM client inside the library. Five weeks old, coding-agent hosts today. |
| 4 | **Miru** (`kiyotakali/Miru`) | **BORROW** (initiative + write-purity pattern) | The one open project with an "AttentionEngine" that decides when to speak, a commitments list, a `soul.md`, a SleepAgent, and the rule "the chat agent only reads memory; every write comes from something that happened". Apache-2.0, but ships as prebuilt releases; read the pattern, not the code. |
| 5 | **Eval instruments**: PersonaMem-v2, ATRBench, ANCHOR | **ADOPT-TRIAL** (as scoring arms in ZMB, not as engines) | Gives numbers for the three gaps nobody sells a fix for: implicit user model (PersonaMem-v2, code public, a 4B model was RL-trained on it), asking to remember (ATRBench), persona collapse (ANCHOR, code not yet released). |

Just outside the top 5: **LightMem** (`zjunlp/LightMem`, MIT, ICLR 2026, Ollama/vLLM native, offline "sleep-time" update, compression to cut LLM calls): the cheapest-in-calls engine arm if you want one more; not ranked higher because it is a research framework with no user-model or persona surface.

## 2. Already evaluated by you (listed as known, not re-evaluated)

Hindsight (adopt candidate), MemPalace (two ideas taken), mem0, Zep/Graphiti, Letta/MemGPT (incl. sleep-time agents; `letta-ai/letta` 7 commits in 90 days, `letta-code` is the active product), Honcho, Cognee, LangMem, Memobase, Supermemory, A-MEM, MemoryBank.

## 3. Candidate sheets

Format: what it is | maintained? | licence | local on 16 GB Jetson with a 4B model | what it gives Zoe that we lack | adoption cost | verdict. "Calls per turn" is only given where read; otherwise [unverified].

### 3.1 EverOS (EverMind-AI/EverMemOS) - ADOPT-TRIAL, rank 1
- URL: https://github.com/EverMind-AI/EverMemOS (README + `docs/reflection.md` + `default.toml` read 2026-10-07).
- What: Python library and local-first runtime. Conversations become Episodes; a clustering step groups them; user profile and agent skills are separate tracks. Markdown is the source of truth; SQLite + LanceDB are rebuildable indexes. [readme]
- Maintained: 13,352 stars, pushed 2026-10-06, 81 commits / 8 authors in 90 days, created 2025-10-28. [gh]
- Licence: Apache-2.0. [gh]
- Local: LLM and embedding are OpenAI-compatible blocks (`[llm] base_url`, `[embedding] base_url`), so a local llama.cpp endpoint is configurable. Defaults point at OpenRouter / DeepInfra and Qwen3-Embedding-4B (too heavy for your RAM; you would swap in a small embedder). Reflection and skill extraction need the embedding provider. No RAM or per-turn call count measured. The episode-extraction prompt slot is 954 bytes, a good sign against the 8,192-token slot, but the code-level prompts were not measured. [readme][unverified]
- Gives Zoe: (a) a per-user profile surface refined between sessions, which is the closest thing to a maintained "model each household member"; (b) reflection: weekly, at most 10 clusters per run, merges an episode cluster into one narrative, soft-archives the originals with `deprecated_by`, rebuildable from Markdown. Their own doc warns repeated re-consolidation is lossy, hence weekly. [readme]
- Cost / risk: Reflection "resolves contradictions by keeping the latest state" = newest-wins, which breaks the authority rule; it must only ever operate on the derived (inference) tier, never on user-stated rows. Sits beside Hindsight as a sidecar for profile + reflection, or replaces Hindsight's consolidation; does not replace the thin authority layer. Cloud defaults and a 'demo' path need checking for phone-home (not checked).
- Trial design: run only the profile and reflection strategies on a synthetic household for four simulated weeks against the same ZMB probes; pass bar = profile facts correct, no user-stated row altered, RAM inside the 2 GB headroom.

### 3.2 kiwi-mem (LucieEveille/kiwi-mem) - BORROW, rank 2
- URL: https://github.com/LucieEveille/kiwi-mem (README, README_EN read 2026-10-07).
- What: FastAPI proxy gateway between chat client and LLM; PostgreSQL + pgvector; built "to remember a person".
- Maintained: 329 stars, pushed 2026-10-05, 49 commits / 2 authors in 90 days, created 2026-04-18. [gh]
- Licence: README says AGPL-3.0-or-later; API reports NOASSERTION. Treat as AGPL: ideas only. [gh][readme]
- Local: needs an OpenAI/Anthropic-style API; runs on Postgres (you have the pgvector image). Local-4B viability [unverified]; the nightly dream and daily profile are several LLM calls.
- Ideas worth taking (each is specific and testable):
  1. Heat = time decay + recall reinforcement + query diversity + emotional weight; high-emotion memories decay slower; "blur" old memories nightly (keep the gist, drop detail) with a 21-day cooldown, and a blurred memory that is recalled earns 30 more days.
  2. Calendar hierarchy: chat compressed to day, week, month, quarter, year summaries; recent days injected in full, older only as gist. This is the cheapest answer to months-scale continuity inside an 8k slot.
  3. Dream in three layers: clean stale/duplicate, merge fragments into scenes (with vector index), infer unstated-but-useful things. Merge keeps a floor of 20 items so it cannot over-compress.
  4. Locks: user-locked memories never decay or retire (your authority rule, in another dress); machine locks demote after 90 days without recall, reversible, never deleted.
  5. Prompt order: static (persona, profile, locked, calendar) first so the prefix caches; dynamic after. Relevant to llama.cpp prompt cache on the brain.
- Gives Zoe: salience weighting, forgetting-by-value, hierarchical time summaries, profile refresh. Does not give: multi-person households (single-user), initiative.
- Cost: none to take ideas; running it would add a second proxy in front of Flue, which is the opposite of a thin layer.

### 3.3 agent-memory (tigerless-labs/agent-memory) - BORROW, rank 3
- URL: https://github.com/tigerless-labs/agent-memory (README read 2026-10-07).
- What: Markdown files as the single source of truth, SQLite FTS5 index as a deletable cache, recall returns paths and levels (L0 list, outline, abstract, full), writes fire at conversation boundaries, and a separate sleep-time "Manage" layer consolidates and forgets by value. No LLM client inside the library: judgement is borrowed from the host agent. [readme]
- Maintained: 2,377 stars in five weeks (created 2026-09-01), 100+ commits / 9 authors in 90 days, MIT. Very young and hype-adjacent; version 0.1.0. [gh][readme]
- Local: no API key, no model inside, so RAM is trivial; the judgement calls would be made by Zoe's own brain. [readme]
- Gives Zoe: the one worked example of authority tiers on a sleep-time pass ("an unattended pass may add and update, deletion only ever arrives as a proposal you confirm"), supersede that leaves the chain intact, `recall --as-of <date>`, and a test enforcing "delete the index, rebuild, lose nothing". These match your forget-ledger and temporal needs.
- Cost: hosts today are Claude Code / Codex CLI; a companion would need an adapter. Take the design now; re-check in 60 days for a trial.

### 3.4 Miru (kiyotakali/Miru) - BORROW, rank 4
- URL: https://github.com/kiyotakali/Miru (README read 2026-10-07; awesome-ai-companion list notes "ships as prebuilt releases; no client source").
- What: Local-first desktop companion. Four long-term memory kinds (projects, people, self, topics), a daily journal, a commitment list, `soul.md` personality, continuous emotion, a SleepAgent and Curator, and an AttentionEngine that judges "the moment" to speak.
- Maintained: 173 stars, pushed 2026-09-23, 49 commits / 3 authors in 90 days, created 2026-07-03. Apache-2.0. [gh]
- Local: the chat, memory and vision tiers call any OpenAI-compatible API; screen-sensor design is not relevant to Zoe. Local-4B [unverified].
- Gives Zoe: (a) initiative design (stay quiet when deep in work, come closer when stuck or winding down; decision made by a model, not a timer), (b) write-purity: the chat agent only reads memory; every write is derived from something that happened (your exact doctrine), (c) the commitment list as a first-class object.
- Cost: pattern only; no code adoption.

### 3.5 Evaluation instruments - ADOPT-TRIAL, rank 5
- **PersonaMem-v2** (https://arxiv.org/abs/2512.06688; code https://github.com/bowen-upenn/PersonaMem-v2, 44 stars, no licence file, 1 commit in 90 days [gh]): 1,000 personas, 20,000+ implicit preferences. Frontier models get 37-48 percent; the paper RL-trained Qwen3-4B to 53 percent and an agentic memory to 55 percent using 16x fewer tokens [web, via search summary, abstract not opened]. A 4B result is directly on-class for your brain. Use as the "implicit user model" ZMB axis. MemOS also reports PersonaMem v2 = 40.58 [readme].
- **ATRBench / Ask-to-Remember** (https://arxiv.org/abs/2605.28108, EMNLP 2026 main): agents fall at least 62 points behind an oracle because they do not ask for a reusable preference; acquisition, not storage, is the bottleneck [web]. This is a scored version of "initiative" and "builds a user model deliberately".
- **ANCHOR** (https://arxiv.org/abs/2607.28818, 2026-07-30): persona collapse and behavioral drift in companions; 27 personas, nine schedules, three memory settings, four models; trajectory accuracy 44.4 percent; no code or dataset link found [web]. Re-check for release; until then, copy its two probe shapes (sealed identity questionnaire; counterfactual trajectory questions) into a Zoe-scripted scenario.
- Cost: low; ZMB already separates scenario data from the driver.

## 4. Everything else read, with verdicts

| Candidate | Facts [gh] | Read | Verdict and reason |
|---|---|---|---|
| **MemOS** (MemTensor/MemOS) | 11,740 stars; Apache-2.0; 100+ commits, 9 authors; pushed 2026-09-29 | README, local-plugin README | **SKIP** as engine. Self-host needs Neo4j + Qdrant (RAM); the SQLite "local plugin" is a TypeScript plugin for Hermes / OpenClaw / DeepSeek Harness coding agents (L1 traces, L2 policies, L3 world models, skills). **BORROW** nothing new beyond "feedback and correction in natural language". Self-reported LoCoMo 88.83, LongMemEval 89.20 (vendor numbers). |
| **MIRIX** (Mirix-AI/MIRIX) | 3,449 stars; Apache-2.0; 3 commits in 90 days | README | **SKIP**. Six memory agents behind a meta-agent plus Postgres and Docker = many LLM calls per add; maintenance is thin. Has an "auto-dream" cleanup endpoint with `dry_run` (a nice idea: show what consolidation would change before applying). |
| **memU** (NevaMind-AI/memU) | 14,510 stars; 100+ commits, 17 authors; licence NOASSERTION by API, README badge Apache-2.0 (unresolved) | README | **SKIP**. Has turned into a sidecar wiki/skills tool for desktop coding agents; core is about 500 lines. Licence needs a human to read the LICENSE file before any use. |
| **ReMe** (agentscope-ai/ReMe) | 3,555 stars; Apache-2.0; 100+ commits, 10 authors | README | **SKIP** for Zoe (agent-knowledge-base shape: Markdown + wikilinks + BM25, daily notes to long-term). **BORROW** the "tag index is rebuildable from files" idea only if file-native memory is ever wanted. |
| **OpenViking** (volcengine/OpenViking) | 39,330 stars; AGPL-3.0; 100+ commits, 19 authors | README | **SKIP**. Virtual filesystem for agent context with L0/L1/L2 summaries and per-user and per-peer directories (`user/{id}/peers/...`). Interesting multi-user layout, but AGPL and a heavy embedding/VLM dependency; L0/L1/L2 tiering is the same idea as agent-memory's levels. |
| **LongMemory** (ex-OpenMemory; CaviraOSS/OpenMemory) | 4,521 stars; Apache-2.0; 37 commits, 7 authors | README, tree | **SKIP**. TypeScript engine with "immutable content, provenance, temporal truth, strict/historical/associative recall"; at most one model call for answer grounding. Closest in spirit to exact-words plus provenance, but project-memory/coding oriented. Worth one look if a TS memory library under Flue is ever wanted; not now. |
| **LightMem** (zjunlp/LightMem) | 1,185 stars; MIT; 12 commits, 5 authors; pushed 2026-09-05 | README | **ADOPT-TRIAL (lite)**, just outside top 5: Ollama / vLLM / Transformers back ends; pre-compression (llmlingua-2) and topic segmentation to cut calls; offline "sleep-time" update (`offline_update_all_entries`); same repo hosts StructMem (event-bound hierarchical memory, ACL 2026) and FluxMem. Benchmarks reported on gpt-4o-mini and Qwen3-30B-A3B, not a 4B. Companion benchmark harness `zjunlp/MemBase` (MIT) can run mem0, A-MEM, EverMemOS, LangMem on LoCoMo / LongMemEval, which is a ready-made comparison rig. |
| **agentmemory** (rohitg00/agentmemory) | 29,189 stars; Apache-2.0; 78 commits, 12 authors | search snippet only | **SKIP**: coding-agent memory. |
| **Serein** (Yinglianchun/Serein) | 149 stars; MIT; 100+ commits, 2 authors; created 2026-07-18 | README, English summary | **BORROW**. Verbatim quotes stored apart from memory text and from the index, with a record of which quotes support which memory (evidence binding); Scene (written by the chat model) vs Event (summariser, first person, faithful to the quote, no invented feelings) - and an honest warning that summaries can over-state a guess; rerank-gate between "found" and "injected"; Arc narratives. OpenAI-compatible endpoints, local OK. |
| **DuduLove Memory** (VITASID57/dudulove-memory) | 25 stars; MIT; created 2026-10-05 (two days old) | README | **BORROW** (ideas), too new to trust. Identity resolved server-side from the connection credential (the model cannot choose to be someone else), rejected memories leave a rejection record so background consolidation cannot rewrite them, a recall ledger stops the same memory resurfacing within a session, "admission" gate on every write. Directly parallels your forget ledger and identity-from-account. |
| **Kin Mind** (mycyg/kin-mind) | 24 stars; MIT; 100+ commits, 1 author | README | **BORROW** (ideas): worries kept with source, relief, resolution and re-emergence records; emotional state with a source event and a hypothesised identity; a heartbeat in which the companion checks mood, open worries and wants. One-person hobby project, mostly Chinese docs. |
| **Headlong** (laude-institute/headlong) | 1,213 stars; Apache-2.0; 100+ commits, 7 authors; created 2026-04-07; alpha | README | **SKIP** as a stack, **BORROW** the idea: a human message lands in an always-running thought stream as one more observation and the agent decides whether to answer; local OpenAI-compatible model supported. Bash recursive-model harness; too different from Flue; an always-on loop does not fit a 4B brain with a 2 GB headroom. |
| **connectome-host** (anima-research) | 108 stars; no licence file; 100+ commits, 12 authors | listing only | **SKIP**: no licence. Idea only: "self-voiced autobiographical memory" and branchable history. |
| **Ombre-Brain** (P0luz) | 1,421 stars; MIT shown by API but listing says non-commercial from v2.4.0 | listing only | **SKIP** (licence drift). Idea: valence/arousal tag per memory; forgetting curve. |
| **WrenWen** (ssxl0126/WrenWen) | 56 stars; no licence; docs only | listing only | **BORROW to read**: "production docs of a 24/7 companion: 9D drive-based desires, 2-tier memory scoring, prompt-caching forensics, anti-drift debugging". The only open field report on keeping a companion in character for months; read it before writing the persona-stability scenario. [unread] |
| **Khoj** (khoj-ai/khoj) | 37,584 stars; AGPL-3.0; **2 commits in 90 days**, last push 2026-08-02 | gh only | **SKIP**: effectively unmaintained, AGPL, document-search shape not a companion. |
| **Generative Agents** (joonspk-research/generative_agents) | 22,191 stars; Apache-2.0; last push 2024-08-05; 0 commits | gh only | **SKIP** as code (dormant). The recency x importance x relevance retrieval and periodic reflection are now standard; CrewAI ships the formula (below). |
| **AI Town** (a16z-infra/ai-town) | 10,594 stars; MIT; 4 commits | gh only | **SKIP**: game engine on Convex. |
| **Project Sid** (altera-al/project-sid) | 1,385 stars; no licence; last push 2024-11 | gh only | **SKIP**: Minecraft simulation, no code to reuse. |
| **Open Souls** (opensouls/opensouls) | 322 stars; MIT; last push 2026-02-08; 0 commits in 90 days | gh only | **SKIP** (dormant). The "cognitive steps + working memory" pattern is what Flue already does. |
| **Voyager / Reflexion** | Voyager last push 2024-04; Reflexion 2025-01 | gh only | **SKIP**: dormant; procedural-memory idea has been absorbed into ReMe, memU, MemOS skills. |
| **Concordia** (google-deepmind/concordia) | 1,760 stars; Apache-2.0; 63 commits, 15 authors | gh only | **SKIP**: social-simulation framework (game-master pattern), not a companion memory. |
| **CrewAI Memory** | 59,403 stars; MIT; active | docs read | **BORROW** one formula: recall score = 0.5 x similarity + 0.3 x 0.5^(age/half-life) + 0.2 x importance, importance assigned at encode time; consolidation threshold 0.85 where an LLM decides keep/update/delete/insert. That is the importance-weighted recall you asked about, in 3 lines; but note the LLM-decides-delete step would violate your authority rule. |
| **LlamaIndex Memory** | 52,426 stars; MIT; 99 commits, 58 authors | docs read | **BORROW** little: static block (user identity), fact-extraction block (LLM), vector block, token-budget flush with priorities (priority 0 never truncated). A framework shape, Zoe already has it. |
| **LangGraph store, OpenHands memory** | stats only (OpenHands 90,136 stars) | not read | Not evaluated beyond stats; both are generic agent-framework stores / coding-agent condensers. Marked "not looked at", not "rejected". |
| **Basic Memory, Second Me, MemoryOS (BAI-LAB), Memary, memvid, Memori** | Basic Memory 4,108 stars AGPL (active); Second Me last push 2025-09 (0 commits); MemoryOS 0 commits in 90 days; Memary last push 2024-10; memvid 2 commits; Memori licence NOASSERTION, 1 author | gh only | **SKIP**: AGPL note tool / dormant / ambiguous licence. MemoryOS's "heat" idea is already superseded by kiwi-mem's. |
| **Omi** (BasedHardware/omi) | 13,646 stars; MIT; 100+ commits | README | **SKIP**: wearable plus cloud pipeline (Firebase, Deepgram, Redis); memory extraction is conversation summaries; not a local companion memory. |
| **AIRI** (moeru-ai/airi) | 50,107 stars; MIT; 100+ commits, 32 authors | README | **SKIP** for memory: its own checklist lists "Memory Alaya" as WIP and in-browser DuckDB/pglite as the only ticked item. Useful only as a reference for companion UI and voice. |
| **Open-LLM-VTuber, Neuro (kimjammer)** | Open-LLM-VTuber 0 commits in 90 days; Neuro stalled since early 2025 | gh / listing | **SKIP**. |
| **SillyTavern** | 34,160 stars; AGPL-3.0; 51 commits, 33 authors | docs read | **BORROW** (mechanics, no code): world-info entries with `sticky` (stay active N messages), `cooldown`, `delay`, `constant` entries, `inclusion groups`, token budget (constant entries inserted first, then by order), insertion depth (entries near the end of the prompt influence the next reply more). This is the field's mature answer to "keep the character itself in every turn without blowing the budget" and costs zero LLM calls. Community memory add-ons (Qvink MessageSummarize, 167 stars, 0 commits in 90 days; MemoryBooks; VectFox 71 stars) are single-maintainer and AGPL. |
| **Tiny sleep-time projects** (nram-ai/nram, spqian/dreamweave, huodebing-alt/anima, Zenetusken/consolidate-memory) | 7 to 10 stars each, one author | search snippets | **SKIP**: too small to depend on; the idea (consolidate on a separate clock) is in agent-memory and kiwi-mem already. |
| **Commercial companions (Kindroid, Nomi, Replika)** | not repos | third-party comparison pages only | No first-hand engineering write-ups found. Third-party summaries say Kindroid publishes three tiers (persistent, cascaded, retrievable) with stated context budgets, and Nomi uses a graph "mind map" with short / mid / long tiers. These are marketing-grade; not cited as evidence. |

## 5. Papers and benchmarks (last six months), what was actually opened

| Paper | Date | Opened? | Why it matters |
|---|---|---|---|
| ANCHOR, "Best Friends, Not Forever" https://arxiv.org/abs/2607.28818 | 2026-07-30 | abstract | Companion persona collapse; trajectory 44.4 percent; no memory setup fixes it; no code. |
| ATRBench, "Ask Now, Use Later" https://arxiv.org/abs/2605.28108 | 2026-05-27 | abstract | Proactive preference acquisition gap of 62+ points. |
| PASK https://arxiv.org/abs/2604.08000 | 2026-04-09 | abstract | Streaming demand detection + memory for proactive agents; code release not mentioned. |
| PGMem https://arxiv.org/abs/2608.01708 | 2026-08-03 | abstract | Persona and event nodes joined by typed provenance edges; small-LM backbones; code https://github.com/wonjunchoi23/pgmem (4 stars, no licence, 1 author). Idea only. |
| PersonaMem-v2 https://arxiv.org/abs/2512.06688 | 2025-12 | search summary | See 3.5. |
| CreaMem 2609.08550, AdaMem 2603.16496, GRAVITY 2605.01688, "Learning User-Aware Recall" 2607.00017, "From Recall to Forgetting" 2604.20006, "User as Code" 2606.16707, eMEM 2606.03374 | 2026 | titles only [unread] | Leads for a follow-up: user-aware recall and forgetting benchmarks are on-topic for ZMB. |

## 6. What each gap maps to

| Zoe gap | Best find | Status |
|---|---|---|
| Exact-words recall | Serein evidence binding; Hindsight verbatim mode (known) | Already covered by Hindsight; Serein adds quote-to-memory provenance. |
| Reflection | EverOS reflection (trial); kiwi-mem dream (ideas) | Trial one arm; weekly cadence, soft archive, dry-run. |
| User model per person | EverOS profile track; PersonaMem-v2 as the measure; Honcho (known) | No maintained system models a household; test with ZMB multi-person scenarios. |
| Initiative | Miru AttentionEngine pattern; ATRBench as the measure | Pattern only; Headlong too heavy. |
| Salience / forgetting by value | kiwi-mem heat; CrewAI composite score | Borrow; locks map to your authority tier. |
| Months-scale continuity | kiwi-mem calendar hierarchy; SillyTavern sticky / constant entries | Borrow; zero or low LLM cost. |
| Persona stability | ANCHOR (measure only); WrenWen field notes (unread) | Open problem field-wide; write a scenario. |
| Authority | agent-memory design; DuduLove rejection records | Keep `memory_authority.py`; borrow the proposal-not-delete pattern. |

## 7. Caveats and what was not done

- Nothing was installed or run: no RAM, latency or calls-per-turn figures exist for any candidate. All "local viability" statements are read from docs and configs, marked [unverified].
- GitHub numbers are from today's API; `commits90d` is capped at 100 per page. Star counts on young repos are volatile (agent-memory gained 2,377 in five weeks).
- Licences: API said NOASSERTION for memU, kiwi-mem (README says AGPL-3.0-or-later), Memori, Open-LLM-VTuber. Do not rely on any of them until the LICENSE file is read.
- Vendor benchmark numbers (MemOS, EverOS) are self-reported.
- Several claims come from README summaries produced by the fetch tool (WebFetch uses a small model): ATRBench, ANCHOR, PASK, PGMem, SillyTavern, CrewAI and LlamaIndex facts were not checked against the full pages.
- Many companion projects are Chinese-language and one-person; their READMEs were read through the tool's translation.
- The awesome list `DasterProkio/awesome-ai-companion` (887 stars, CC0, active, created 2026-07-03) is the best discovery source for this slice and was used for the companion cluster; re-run it monthly.

## 8. Sources (all fetched 2026-10-07)

- https://github.com/EverMind-AI/EverMemOS (and `docs/reflection.md`, `src/everos/config/default.toml`)
- https://github.com/MemTensor/MemOS ; https://github.com/MemTensor/MemOS/tree/main/apps/memos-local-plugin
- https://github.com/LucieEveille/kiwi-mem
- https://github.com/tigerless-labs/agent-memory
- https://github.com/kiyotakali/Miru
- https://github.com/zjunlp/LightMem ; https://github.com/zjunlp/MemBase
- https://github.com/Yinglianchun/Serein ; https://github.com/VITASID57/dudulove-memory ; https://github.com/mycyg/kin-mind
- https://github.com/laude-institute/headlong ; https://github.com/volcengine/OpenViking ; https://github.com/CaviraOSS/OpenMemory ; https://github.com/Mirix-AI/MIRIX ; https://github.com/NevaMind-AI/memU ; https://github.com/agentscope-ai/ReMe
- https://github.com/DasterProkio/awesome-ai-companion ; https://github.com/TeleAI-UAGI/Awesome-Agent-Memory
- https://github.com/BasedHardware/omi ; https://github.com/moeru-ai/airi
- https://docs.crewai.com/en/concepts/memory ; https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/ ; https://docs.sillytavern.app/usage/core-concepts/worldinfo/
- https://arxiv.org/abs/2607.28818 ; https://arxiv.org/abs/2605.28108 ; https://arxiv.org/abs/2604.08000 ; https://arxiv.org/abs/2608.01708 ; https://arxiv.org/abs/2512.06688
- Stats for all other repos: `gh api repos/<owner>/<repo>` and `.../commits?since=2026-07-09`.
