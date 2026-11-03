---
type: research
title: Lane D - cognitive architectures, persistent cognition and emergent intelligence through integration
date: 2026-11-03
status: lane report
description: Samantha Initiative phases 2.3, 5 and 8, the program's central question. What global-workspace, classic and LLM-era cognitive architectures, sleep-time compute, metacognition, salience, uncertainty, event runtimes and the persistent-context products actually offer a 16 GB single-slot household box; a persistent-cognition design that costs near zero while idle; event-driven vs scheduled vs hybrid; the perception-to-memory loop traced through one household example with the benefit it must show; experiments; and where this would be complexity without intelligence.
evidence: docs/research/synthesis-deep-dive-2026-10-29.md; docs/research/completeness-and-issues-2026-11-02.md section 1; docs/knowledge/night-window.md sections 4, 11, 17; docs/knowledge/night-mind.md; docs/architecture/struggle-detector.md; docs/architecture/samantha-brain-blueprint-2026-10-09.md sections 2.0-2.2, 5; docs/research/what-gets-us-closer-2026-10-07.md; services/zoe-data/proactive/ (engine.py, selector.py, pull.py, presence.py, arrival.py); live read 2026-11-03 (scripts/maintenance/zoe_ground_truth.sh, free -m, named flags only).
---

# Lane D: cognitive architecture, persistent cognition, emergent intelligence (2026-11-03)

Labels: **[Proven]** = shipped and reproduced elsewhere, or measured on Zoe's own box. **[Experimental]** = shown in a paper or a community build, not in Zoe's estate. **[Speculative]** = plausible, unmeasured. Source tags: *read* = I read the page, repo doc or Zoe source in this session; *search* = only a search summary of the abstract or docs (treat details as unverified); *known* = a well-known paper whose link is given but which I did not re-open today. No external code was cloned for this lane: every external verdict below is a design judgement on published descriptions, not on executed source.

## 0. What Zoe should take from this (ten bullets)

1. **Zoe already has a cognitive architecture; it is distributed and half-closed.** Mapped onto CoALA's slots, Zoe has working memory (the packet builder), episodic memory (`chat_messages`, `exact_turns`), semantic memory (facts, people graph), procedural memory (router, tools, doctrines), a salience function (the selector's importance x recency x relevance, lifted from Generative Agents and **live**: `ZOE_PROACTIVE_SELECTOR=1` on the running process today), reflection (the night mind), metacognition (the struggle detector, shadow), and a proactive manager (pull-not-push, the orb). [Proven, *read*] What is missing is not a module; it is the **edges between them**: the night mind's readers are off, the struggle feed is off, and the selector does not learn from whether a raise landed beyond the delivery ledger's class back-off.
2. **The single llama slot is the governing constraint of every "background cognition" idea.** A daytime 4B call holds the one slot for seconds and evicts the panel's cached prefix: the round-3 traffic lane measured 29 of 100 panel turns re-prefilling because harness traffic shared the slot. Any persistent process that calls the 4B by day pays in household p90. [Proven] Therefore: **by day, cognition is code plus resident small models; the 4B is spent only inside a turn the person started.**
3. **Sleep-time compute is real but its Zoe form is mostly not an LLM.** Letta/Berkeley report ~5x less test-time compute for equal accuracy and up to 13-18% accuracy gains when context is pre-processed before the query, and the gain tracks how predictable the query is ([arXiv 2504.13171](https://arxiv.org/abs/2504.13171), *search*). [Experimental] For Zoe the predictable thing is *who is likely to speak next and what is open for them*; that precompute is SQL + bge-small, not a 4B pass.
4. **The 12B night window is a scarce, unreliable budget, not a free brain.** Measured 12B decode 3.62 tok/s, prefill 136 tok/s; the window is ~20 minutes; 2 of the last 4 nights failed to load or were floor-killed. That is roughly ten useful 12B calls a night on a good night. [Proven, *read* night-window.md sections 11, 17] The 4B at night decodes ~60 tok/s (night-mind smoke) and loads every time. Night depth should be planned on the 4B first, with the 12B as an optional upgrade, not the other way round.
5. **Global workspace / LIDA at household event rates is a priority queue.** LIDA's cycle is a 300-600 ms competition of attention codelets ([Franklin, CCRG tutorial](https://ccrg.cs.memphis.edu/tutorial/mind-according-to-lida.html), *search*); the LLM-era GWT agents (CogniPair, Global Workspace Agents) run cascades of frontier-model calls per broadcast ([2506.03543](https://arxiv.org/abs/2506.03543), [2604.08206](https://arxiv.org/html/2604.08206), *search*). Zoe sees hundreds of events a day, not hundreds a second, and has one slot. Borrow the *idea* (one winner per moment gets into the context; everything else waits) - the selector's `CAP` and one-RAISE-per-conversation already are that. [Experimental -> borrow only]
6. **Small-model self-reflection is the wrong reflection.** Huang et al. found LLMs cannot reliably self-correct reasoning without external feedback ([arXiv 2310.01798](https://arxiv.org/abs/2310.01798), *known*), and the blueprint records 7B self-refine falling 3.6-10.1 points below repeated sampling. Zoe's working reflection is the opposite shape: code extracts external signals (re-asks, corrections, abandoned replies) and the model only rewords counts. Keep that. [Proven for Zoe's struggle signals' precision guards; Experimental for downstream benefit]
7. **Perception is the thinnest layer, and it is thin by policy, not by accident.** Zoe has zero presence sensors, does not subscribe to Home Assistant's event bus (grep over `services/zoe-data` finds no `subscribe_events` / `state_changed` consumer; HA is proxied only for UI), and the household used Zoe on 9 of 30 October days. An attention engine with nothing to attend to is decoration. [Proven, *read*] The cheapest new perception is HA's `state_changed` stream, filtered in code ([HA events](https://contributors.home-assistant.io/core/internals/events/), *read*), and even that should be shadow-logged before anything consumes it.
8. **Durable-workflow engines solve a problem Zoe does not have.** Temporal (MIT) and Restate (BSL 1.1, *read* LICENSE) add a server, a store and a programming model for multi-step workflows that must survive crashes. Zoe's longest "workflow" is a dated open loop that already lives as a Postgres row with APScheduler and a 300 s slow loop beside it. Adopt neither; keep the pattern "state is a row, the scheduler is a wake-up, every step is idempotent". [Proven pattern; adoption = complexity]
9. **The measurable claim for "integration" is follow-through, not intelligence.** The one benefit a closed loop can show over today's reactive design: a dated thing the person said is mentioned at the right time, welcomed, and resolved - with zero added p90 and zero added waking RAM. Section 4 defines it as a number. With today's usage, reaching 40 real loops for a credible result could take months; that power problem is the honest limit of this whole lane.
10. **The simplest architecture that could produce the benefit is "turn on and connect what exists", plus one new edge: outcome.** Night-mind readers to `serve` (owner flag), a due-today re-score at the first sign of a member (code), the raise/pull outcome written back to the thread and to the selector's back-off, and a shadow perception journal. No new daemon, no new model, no new store engine. Anything bigger is section 6.

## 1. Projects and papers

Columns: purpose / architecture; source inspected; maturity; strengths; weaknesses; compute; licence; benefit to Zoe; verdict. Compute is for the published form, not a Zoe port.

| # | Item | Purpose / architecture | Inspected | Maturity | Strengths | Weaknesses | Compute | Licence | Benefit to Zoe | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **CoALA** ([arXiv 2309.02427](https://arxiv.org/abs/2309.02427)) | Vocabulary: working / episodic / semantic / procedural memory; internal (reason, retrieve, learn) vs external actions; a decision cycle | *known* paper | Framework paper, widely cited | Gives Zoe's modules names and exposes the missing "learning action" edges | No code; descriptive, not prescriptive | n/a | paper | A map to audit Zoe's loop against (section 2 uses it) | **BORROW (vocabulary)** |
| 2 | **Generative Agents** ([arXiv 2304.03442](https://arxiv.org/abs/2304.03442), [repo](https://github.com/joonspk-research/generative_agents)) | Memory stream; retrieval = recency x importance x relevance; reflection when summed importance crosses a threshold; plans | *known*; Zoe's selector cites it (*read*) | Research code, 2023, archived-feeling | The salience formula is cheap and already live in Zoe | Reflection writes model prose as new "memories" (blueprint L2 forbids); GPT-3.5 per agent per tick | Many LLM calls per agent-hour | Apache-2.0 (verified 2026-11-03) | Already adopted (selector); the importance-threshold trigger for reflection is the useful unadopted piece | **ADOPTED (salience); BORROW (threshold-triggered reflection, code-only)** |
| 3 | **LIDA** ([CCRG](https://ccrg.cs.memphis.edu/tutorial/mind-according-to-lida.html), Baars & Franklin 2009) | GWT: perceptual codelets fill a workspace, attention codelets form coalitions, the winner is broadcast; 300-600 ms cycle | *search* | Decades of papers; Java framework, academic | The cleanest statement of "attention = competition for one broadcast slot" | Designed for continuous perception; no LLM; framework dormant | Low per cycle, but always ticking | LIDA framework research licence [unverified] | Conceptual: one item into context per turn, coalitions = several cues pointing at one thread | **BORROW (idea only)** |
| 4 | **CogniPair** GNWT agents ([arXiv 2506.03543](https://arxiv.org/abs/2506.03543)) | Emotion / memory / norms / planning / goal sub-agents compete by salience; broadcast updates all | *search* | Paper, v2 Nov 2025 | Concrete LLM-era GWT | A GPT-4o cascade per broadcast; social-simulation target | Several frontier calls per step | unverified | None at one slot; illustrates the cost of literal GWT | **REJECT** |
| 5 | **Global Workspace Agents** ([arXiv 2604.08206](https://arxiv.org/html/2604.08206), code `giansha/Global-Workspace-Agents`) | GWT to break repetitive / sycophantic multi-agent loops | *search* | Paper + code, 2026 | Targets a real failure (loops, sycophancy) | Multi-agent, multi-call | Multi-call | MIT (verified) | The anti-repetition aim is already served by Zoe's raise cooldown and recall ledger | **WATCH** |
| 6 | **Unified Mind Model** ([arXiv 2503.03459](https://arxiv.org/pdf/2503.03459)) | GWT macro-architecture with an LLM core | *search* (title only) | Paper | Explicit module map | Not inspected | LLM-centric | unverified | None identified | **WATCH** |
| 7 | **Soar** ([SoarGroup/Soar](https://github.com/SoarGroup/Soar)) | Production rules, impasse -> subgoal, chunking, episodic + semantic memory | *search* | 40 years, maintained, C++ | Impasse-driven subgoaling; deterministic; tiny | Hand-written rules; no language understanding; integration cost | Very low CPU, small RAM | BSD (LICENSE.md verified; clause count not stated) | "Impasse" maps exactly onto the struggle detector's CANT_DO / FALLTHROUGH: an impasse should create a goal, not just a log row | **BORROW (impasse -> proposal semantics)** |
| 8 | **ACT-R** ([official Lisp mirror](https://github.com/asmaloney/ACT-R); project site act-r.psy.cmu.edu [link unverified]) | Declarative memory with base-level activation (power-law decay of use), production system, buffers | *known* | Decades, research | Base-level activation is a better recency term than one fixed half-life: frequency AND recency | Cognitive-modelling tool, Lisp | Very low | LGPL-2.1 (mirror LICENSE verified) | Replace the selector's single 72 h half-life with activation over mention times (code, cheap) | **BORROW (one formula)** |
| 9 | **Sigma** (Rosenbloom; [JAGI 2016](https://reference-global.com/article/10.1515/jagi-2016-0001)) | Cognitive architecture on factor graphs | *search* | Research | Unifies symbolic + probabilistic | Research code; no ecosystem | Moderate | unclear | None at Zoe's scale | **REJECT** |
| 10 | **OpenCog Hyperon / MeTTa** ([alpha announcement](https://medium.com/singularitynet/announcing-the-release-of-opencog-hyperon-alpha-38941f8f389f)) | Atomspace hypergraph, MeTTa language, distributed atomspace | *search* | Alpha (2024); PyPI says pre-alpha | Ambitious unified substrate | Alpha, conflicting maturity signals, new language | Unknown, likely heavy | not confirmed in sources | None; Zoe's Postgres graph already holds people and relationships | **REJECT** |
| 11 | **MemGPT / Letta** ([arXiv 2310.08560](https://arxiv.org/abs/2310.08560), [letta](https://github.com/letta-ai/letta)) | OS-style paging of context; memory blocks; tool-driven self-edits | *known* | Maintained product | Context as a managed resource | Self-editing memory conflicts with Zoe's authority wall; bake-off already ran engines | Agent loop per turn | Apache-2.0 | Zoe's packet builder already is the paging policy | **REJECT engine (KEEP_Z0 stands)** |
| 12 | **Letta sleep-time agents** ([docs](https://docs.letta.com/guides/agents/sleep-time-agents)) | A background agent shares memory blocks with the primary and rewrites them asynchronously | *search* | Shipped feature | Off the critical path | Background agent edits memory with an LLM; on one slot it competes with the household | One LLM agent per primary, continuous | Apache-2.0 | Confirms the night-window shape; the async rewrite by an LLM is what Zoe forbids by day | **BORROW (shape) / REJECT (daytime LLM)** |
| 13 | **Sleep-time compute** ([arXiv 2504.13171](https://arxiv.org/abs/2504.13171)) | Pre-process a known context before the query; amortise over related queries | *search* | Paper, 2025 | ~5x test-time compute reduction; 2.5x cheaper per query when amortised; gain scales with query predictability | Benchmarks are stateful math, not households; precompute can inject irrelevant context | Offline LLM passes | paper | Strong support for the night window and for precomputing the next-turn packet; predictability is the selector's job | **ADOPT (principle)** |
| 14 | **Reflexion** ([arXiv 2303.11366](https://arxiv.org/abs/2303.11366)) | Verbal self-feedback stored and replayed | *known* | Paper + code | Works with an external success signal | Without one, small models confabulate critique | Extra calls per attempt | MIT (verified) | Only with Zoe's external signals (struggle events); not free-form self-critique | **BORROW (signal-gated only)** |
| 15 | **"Cannot self-correct yet"** ([arXiv 2310.01798](https://arxiv.org/abs/2310.01798)) | Evidence that intrinsic self-correction does not help reasoning | *known* | Paper | Sets the floor for metacognition claims | Frontier-model study | n/a | paper | Justifies "the model rewords counts, code decides" | **ADOPT (as a constraint)** |
| 16 | **The AI Scientist** ([arXiv 2408.06292](https://arxiv.org/abs/2408.06292), Sakana) | Idea -> experiment -> write-up loop with automated review | *known* | Research code | A full propose-test-judge loop | Frontier models, high cost, judge reliability questioned | Hundreds of calls per paper | custom source licence (verified: AI Scientist Source Code License v1.0) | The shape matches Zoe's planned nightly self-evolution loop; the lesson is the judge, which Zoe's synthesis already flags (the judge is the model under test) | **BORROW (gate structure)** |
| 17 | **Voyager** ([arXiv 2305.16291](https://arxiv.org/abs/2305.16291)) | Curiosity via automatic curriculum + skill library + self-verification | *known* | Paper + code | Skills persist and compose | Minecraft, GPT-4, open-ended exploration | Very high | MIT (verified) | A household must not "explore"; the skill-library idea is Zoe's tools + router | **REJECT (curiosity); skill library already exists** |
| 18 | **Intrinsic curiosity (ICM)** ([arXiv 1705.05363](https://arxiv.org/abs/1705.05363)) | Reward = prediction error of a forward model | *known* | RL research | Principled novelty signal | Needs an environment to act in and an RL loop | Training-time | paper | Novelty as a *salience feature* only ("this has never happened at this hour") | **BORROW (feature)** |
| 19 | **Active inference / pymdp** ([infer-actively/pymdp](https://github.com/infer-actively/pymdp)) | Predictive processing: minimise expected free energy over discrete POMDPs | *known* | Maintained library | Formal surprise + information-seeking | Requires a hand-built generative model of the home; no data to fit it (no sensors, sparse use) | Small for small state spaces | MIT (verified) | Speculative until perception exists | **WATCH** |
| 20 | **Semantic entropy** ([Nature 2024](https://oatml.cs.ox.ac.uk/publications/202406_FarquharKossenKuhn_HallucinatingLLMs.html)) | Uncertainty over meaning clusters of several samples | *search* | Peer-reviewed | Task-agnostic confabulation detector | 5-10 samples per answer; misses consistent errors | 5-10x decode | code licence unverified | Night-only: score the 12B/4B's extracted moments; never by day (slot) | **NIGHT TRIAL** |
| 21 | **Proactive Agent / ProactiveBench** ([arXiv 2410.12361](https://arxiv.org/abs/2410.12361), [thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent)) | Infer tasks from observed activity; reward model judges accept / reject | *search* | ICLR 2025 | Real accepted / rejected labels; a 7B fine-tune reached F1 66.5% | Coding / writing / desktop events, not households; the 8B baseline is 55% F1 | Fine-tune + reward model | Apache-2.0 (verified) | The label shape (accepted / rejected suggestion) is exactly Zoe's welcome tap; Zoe should collect it before any model | **BORROW (label schema + eval)** |
| 22 | **HTN / LLM+P** ([arXiv 2304.11477](https://arxiv.org/abs/2304.11477)) | Hierarchical decomposition; LLM translates to PDDL, a classical planner solves | *known* | Mature planning field | Correct plans when the domain is modelled | Domain modelling cost | Planner is cheap | various | Household plans are short (remind, buy, book); the tool chain covers them | **REJECT for now** |
| 23 | **Home Assistant event bus** ([docs](https://contributors.home-assistant.io/core/internals/events/)) | `state_changed`, `call_service`; listeners with filters; WebSocket subscription | *read* | Production, huge install base | The house's own perception stream, already running on the LAN | Noisy; `MATCH_ALL` is expensive by HA's own docs | Near zero for a filtered subscription | Apache-2.0 | The cheapest perception Zoe could add | **SHADOW TRIAL (log-only)** |
| 24 | **Temporal** ([temporalio/temporal](https://github.com/temporalio/temporal)) | Durable workflow execution: event-sourced histories, workers, timers | *search* | Production-grade | Exactly-once timers, retries, visibility | Server + persistence + workers; heavy for a 16 GB box | Hundreds of MB resident [unverified] | MIT | None that APScheduler + Postgres rows do not cover | **REJECT** |
| 25 | **Restate** ([restatedev/restate](https://github.com/restatedev/restate)) | Durable execution via a log-based server and SDK handlers | *read* (LICENSE) | Maintained | Single binary, durable promises | BSL 1.1 (not open source until the change date) | A resident server | BSL 1.1 | None | **REJECT** |
| 26 | **ChatGPT memory** ([OpenAI help](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work)) | Saved memories (explicit) + reference chat history (implicit, changes over time) | *search* | Shipped to hundreds of millions | Two tiers: user-stated is durable, inferred is fluid; matches Zoe's authority order | Deleting a chat does not remove memories drawn from it; implicit recall is reported as unpredictable | Cloud | proprietary | Validates authority tiers; warns that unpredictable implicit recall erodes trust | **BORROW (two-tier UX); note the forget gap Zoe avoids** |
| 27 | **Gemini memory / past chats** (Google help pages [link unverified]) | Memory of past chats, opt-out | *search* (thin) | Shipped | Opt-out control | Official docs not read; details unverified | Cloud | proprietary | None beyond #26 | **NOTE ONLY** |
| 28 | **Rewind / Limitless** ([betanews](https://betanews.com/2025/12/08/meta-has-acquired-limitless-a-startup-that-produces-ai-powered-wearables/), [Sacra](https://sacra.com/research/why-meta-bought-limitless/)) | Total capture (screen, then pendant audio) + search + summaries | *search* | Acquired by Meta Dec 2025; pendant sales halted, Rewind app sunset | Proved capture is easy | Capture without a reason to return; category contracted | Cloud transcription | proprietary | Zoe's Omi day review already sits on this; the lesson: capture is worthless without the approve-then-act loop | **LESSON** |
| 29 | **Humane AI Pin** ([iPhone in Canada, 2025-02-18](https://www.iphoneincanada.ca/2025/02/18/hp-acquires-humane-ai-pin-tech-for-cool-116-million-usd/)) | Cloud assistant in a wearable | *search* | Servers off 2025-02-28; HP bought the Cosmos platform, team and 300+ patents for $116M, not the device | - | Latency, heat, no context continuity worth paying for | Cloud | proprietary | Latency and reliability killed it before intelligence mattered - the synthesis's ranking in one product | **LESSON** |
| 30 | **Rabbit R1 / LAM** | "Large action model" operating apps | none (no primary source read) | Shipped, widely criticised at launch | - | Public claims exceeded demonstrated capability | Cloud | proprietary | Do not claim agency that is not measured | **LESSON [unverified detail]** |

### 1.1 Reading the field: what the thirty rows say together

- **Classic architectures (Soar, ACT-R, LIDA, Sigma, Hyperon) were built for the problem LLMs now absorb** - turning perception into symbols and choosing among productions. What survives is small and formal: Soar's *impasse creates a subgoal*, ACT-R's *activation = frequency and recency of use*, LIDA's *one coalition wins the broadcast*. Each is a few lines of code in an existing Zoe seam; none is a framework to adopt. [Proven as theory; Speculative as a Zoe gain]
- **LLM-era cognitive architectures (Generative Agents, CogniPair, GWA, CoALA-shaped agents) mostly scale by adding model calls.** Their published systems run a frontier model per module per step. On one slot that design inverts: every extra call is household latency. The parts that transfer are the ones that are *formulas* (retrieval scoring, reflection thresholds), not the ones that are *agents*. [Experimental]
- **Background compute works when the query is predictable and the work is amortised** (sleep-time compute). In a household the predictable part is narrow: who is likely to speak, what is due for them, and what they talked about yesterday. That is a cache, not a mind. [Experimental]
- **Metacognition that helps has an external signal.** Reflexion helps where success is checkable; intrinsic self-correction does not reliably help (Huang et al.). Zoe's struggle signals are external (the person re-asked, corrected, abandoned), which is why that design is the right one. [Proven for the principle]
- **Uncertainty is cheap only offline.** Semantic entropy needs several samples; by day Zoe's honest uncertainty signals are structural (router abstain, recall miss, identity unverified). At night sampling is affordable on the 4B. [Experimental]
- **Products that bet on persistent context failed on the basics first.** Humane died of latency and reliability; Limitless proved capture is easy and stickiness hard, and was absorbed; ChatGPT's two-tier memory works at scale but users report implicit recall as unpredictable, and deleting a chat does not delete what it taught. Zoe's synthesis already ranks "she is there when spoken to" above every Samantha feature; the field agrees. [Proven as market history]
- **Event runtimes are a solved, boring problem.** Home Assistant's bus is the house's perception stream; Temporal and Restate are for multi-service business workflows. Zoe needs a filtered subscription and an idempotent job, nothing more. [Proven]

### 1.2 A CoALA audit of Zoe (what exists, what is wired, what is missing)

| CoALA element | Zoe today | Wired into a conversation? | Gap |
|---|---|---|---|
| Working memory | Packet builder: identity, recall, continuity, brief / RAISE, date hint (`zoe_flue_client.py`) | Yes | Recall fires on few real turns (synthesis: 83% of replies used zero sources; #2225 diagnosis) |
| Episodic memory | `chat_messages`, `exact_turns`, Omi day review | Partly (continuity empty on 97% of turns per synthesis) | Injection is trigger-gated |
| Semantic memory | Facts through the authority wall, people graph, user-model card | Yes | Precision first by design |
| Procedural memory | Router (270M), tools, doctrines, fast tiers | Yes | Struggle feed to router retrain is off |
| Retrieval action | `recall_memory` tool, recall block | Yes | - |
| Reasoning action | The 4B inside the turn | Yes | One slot |
| Learning action (write) | Turn digest (live), night mind (writer runs in the 12B window) | **Writes yes, reads no** for night mind | Readers to `serve` |
| Grounding (external) action | HA, Music Assistant, timers, lists | Yes | Timer fail-closed bug (completeness section 1, item 4) |
| Decision cycle | Reactive: one turn in, one reply out; selector nightly; pull on ask | Partly | No presence-time re-score; no outcome edge |

The audit's one-sentence result: **Zoe's learning actions write, but too few of them read back.** That is a wiring gap, not an architecture gap.

## 2. The persistent-cognition design

### 2.1 The envelope it must fit (measured, with dates)

- **RAM by day:** the brief gives ~500 MB MemAvailable in waking hours with the 4B, Kokoro (~2.3 GB) and Moonshine resident, a 700 MB voice floor, and ~17 GB swap. My read at ~11:30 on 2026-11-03: 2,977 MiB available, 2.0 GiB swap used - **but Kokoro was down** (`kokoro-tts inactive`, health 000), so the comparable figure is ~650 MiB. [Proven] Design budget: **0 MB of new resident model RAM by day**; at most ~20 MB of Python state inside zoe-data.
- **The slot:** one llama-server slot, `--parallel 1`, 8,192 context; system prompt ~2,332 tokens; prefill ~650 tok/s on the 4B; panel-lane decode 20.1 tok/s measured 2026-07-26 (blueprint 2.2), ~60 tok/s in the night-mind smoke. A background 4B call of 1,500 prompt + 120 output tokens holds the slot ~2.3 + ~6 s [derived], and if it evicts the household prefix, the next panel turn re-prefills ~2,332 tokens = ~3.6 s [derived].
- **Already resident, already paid for:** FunctionGemma-270M router (`:11436`, decision p50 ~393-424 ms, CPU), bge-small embeddings in zoe-data (recall p95 112-125 ms warm), Postgres, APScheduler, the proactive slow loop (300 s, `engine.py:31`). [Proven, *read*]
- **Night:** 12B window 02:50 to ~03:10; 12B prefill 136 / decode 3.62 tok/s; up to 3 load attempts; night jobs reserve 700 MiB beside it. A 3,000-token prompt + 300-token answer = ~22 + ~83 = ~105 s per 12B call [derived], so about 8-12 calls fit in a night after load and restore. [Proven inputs; derived count]

### 2.2 The components, mapped to what exists

The rule from blueprint L1 and L5 holds: the model points, code decides; nothing new is resident by day. Each component is listed with where it lives today and what it would need.

| Component | Job | Exists today (live state) | Tier that should run it | New work |
|---|---|---|---|---|
| **Perception journal** | One append-only record of "what happened": turns (speaker, panel), panel sessions, pulls, raises and their outcomes, reminders fired, struggle events, Omi syncs, later HA state changes | Scattered: `chat_messages`, `ui_panel_sessions`, delivery ledger (`proactive/lines.py`), `struggle_events`, `proactive_pending` | **Code** (insert, ~1 ms) | A view or a thin table unifying these by `(ts, member, kind, ref)`; HA subscription shadow-only |
| **Attention / salience engine** | Score what is worth surfacing now | `proactive/selector.py` (importance x recency x relevance, CAP 5, MIN 0.1; **live**) runs nightly; `pull.py` delivers on ask | **Code**; bge-small only for cue matching against the current turn | Re-score "due today" items at the first presence of a member (not only nightly); ACT-R-style activation instead of one 72 h half-life |
| **Goal / commitment manager** | Hold open loops, plans, promises Zoe made ("I'll remind you") with due dates and states | `open_loops` + `ZOE_LOOP_LIFECYCLE=1` (live), night-mind threads (writer runs; readers off), reminders | **Code** state machine; 4B only to extract (already in the digest) | One lifecycle: open -> due -> raised -> welcomed / ignored -> resolved / dropped; Soar's impasse rule (a CANT_DO creates a proposal) |
| **Context manager** | Decide what enters the packet for this turn | `zoe_flue_client.py` packet builder (identity, recall, continuity, brief / one RAISE, date hint) | **Code**; bge-small recall | Precompute the packet's recall block when a member is first seen (sleep-time compute, code form) |
| **Memory consolidation** | Turn the day into durable, cited rows | Turn digest (live), nightly digest, dreaming, night mind (12B window) | **4B / 12B at night** | Plan the night on the 4B first (loads reliably, ~60 tok/s), 12B as upgrade |
| **Reflection engine** | Threads, what changed, what is quiet, what to raise / leave | Night mind (pointer-shaped, code-decided) | **Night** (4B or 12B), code DECIDE stage | Readers to `serve` after the replay gate (owner) |
| **Metacognition** | Notice fumbles, propose fixes | Struggle detector P1/P2 (shadow), P3 feed (off) | **Code** signals; night clustering; 12B only rewords | Enforce after reading the shadow week; outcome capture on |
| **Reasoning scheduler** | Decide when an LLM call is justified and where | Implicit: turn = 4B; night window = 12B; everything else = none | **Code** policy | A written policy (section 3.3): by day the 4B is only spent inside a person's turn |
| **Proactive engagement manager** | Turn a selected item into an orb dot, a pull answer or one inline RAISE | `pull.py` (orb count, "what's up?"), selector RAISE, `arrival.py` (flag-dark, spoken off by owner) | **Code**; 4B phrases inside the turn | Record outcome (welcomed / ignored / resolved) on the thread, not only the class |
| **Uncertainty estimator** | Know when Zoe does not know | Verify-on-challenge guard; router abstain | **Code** (router abstain, recall miss) by day; semantic entropy at night only | Night-only sampling over extracted moments (experiment E6) |

The striking result of the mapping: **of ten components, nine exist in some form, and six are live.** The design work is the binding, not the modules.

### 2.3 What runs where, and what it costs

| Work | Runs on | Trigger | Latency | Resident RAM added | Slot time | How to measure |
|---|---|---|---|---|---|---|
| Journal insert per event | zoe-data (code) | each event | ~1 ms [Speculative est.] | 0 | 0 | `pg_stat_statements` mean time on the insert; rows/day |
| Salience re-score for one member | zoe-data (code, SQL) | first presence of the day; slow-loop tick | < 50 ms [Speculative est.] | < 5 MB | 0 | timer around the call, logged; p99 over a week |
| Cue match of the current turn against open items | bge-small (resident) | each household turn, off the reply path | ~10-30 ms per short text [Speculative est.]; recall p95 112-125 ms measured | 0 | 0 | existing recall timing logs; add a span |
| Precomputed recall block for the likely speaker | bge-small + SQL | panel session becomes member-owned / wake word with a voice verdict | removes up to ~110 ms from the turn [derived from recall p95] | < 10 MB cache, TTL minutes | 0 | A/B the packet builder's recall span with and without the cache |
| Classify an event's kind (when a rule cannot) | FunctionGemma-270M (resident) | rare, rule miss | ~400 ms | 0 | 0 (separate server) | router decision timing |
| Phrase a RAISE inside a turn | 4B | the person's own turn | +~15 tokens = ~0.75 s at 20 tok/s [derived] | 0 | inside the paid turn | panel TTFA and total-turn p90 with / without the RAISE |
| Background 4B reasoning by day | 4B | idle | ~8 s per call + ~3.6 s penalty to the next panel turn if the prefix is evicted [derived] | 0 | **contended** | **Forbidden by policy** until E3 proves slot save/restore makes it free |
| Moments + threads per member | 4B at night (or 12B) | night window | ~25 s per member-night on the 4B (smoke) | night only | night | night-mind run rows (`calls`, `capped`, timings) |
| Semantic-entropy check of moments | 4B at night | night window | 5 samples x ~1.5 s = ~8 s per moment batch [derived] | night only | night | K-cell precision with and without the filter |
| Struggle clustering + wording | code + 12B | night window | 1-2 12B calls | night only | night | struggle digest run rows |

**Idle cost of the whole design:** a SQL tick every 300 s (already running) and a few journal inserts per hour. Effectively zero. [Speculative until E2 measures it, but bounded by construction]

### 2.4 Component contracts (inputs, outputs, failure semantics)

Each component below is a capability block in the AGENTS.md sense: explicit parameters, structured returns, fail toward "do nothing", no hidden global state.

**Perception journal.**
- Input: `(ts, member_id | guest | none, kind, ref_table, ref_id, panel_id)`; never raw text (the text stays in its source row so a forget reaches it once).
- Output: rows; a per-member "today" read.
- Failure: an insert that fails is counted and dropped; nothing downstream may require it.
- Privacy: guests and synthetic ids are not journaled (same filter as the struggle detector: `user_filters.is_synthetic_user`); forget and delete-user cascade by `ref`.
- Start as a SQL view over the existing tables; promote to a table only if the view is measured too slow.

**Salience re-score at presence.**
- Input: member id, now, the member's candidates and dated loops.
- Output: an ordered list of at most `CAP` items with `{score, reason, due_window}`; updates the orb count.
- Trigger: the first time a member's panel session becomes owner today, or a verified voice turn after a gap of N hours; at most once per member per hour.
- Failure: timeout 500 ms; on any error the nightly ranking stands.
- Never: speaks, pushes, or calls the 4B.

**Commitment lifecycle.**
- States: `open -> due -> surfaced -> (welcomed | not_now | ignored) -> resolved | dropped | expired`.
- Two touch-points for a dated plan with a feeling (before and after); one for a plain dated plan.
- Transitions are code; the only model-derived transition is `resolved`, which needs the night mind's `finishes` agreement (its existing rule) or the person saying so in a pull reply.
- Back-off: per item, doubling after an ignore (the night mind's 1, 2, 4 days), two ignores = leave.

**Context manager (precompute).**
- Input: member id from a trusted source (session owner or voice verdict), never from the text.
- Output: a cached recall block with a short TTL; the packet builder uses it only if the turn's resolved identity equals the cache key.
- Failure: miss = today's behaviour.

**Reasoning scheduler.**
- A pure function `may_use_model(tier, context) -> (bool, reason)` consulted by any code that wants a model; logs every refusal; its policy is section 3.3.

**Proactive engagement manager.**
- Unchanged surfaces (orb, pull, one RAISE); new: the outcome is written to the item, not only to the class ledger.
- Owner decision 2026-09-29 stands: no unprompted speech. Brief-on-arrival stays flag-dark with spoken off.

### 2.5 Night budget planning

| Night job | Calls (4B) | Calls (12B) | Notes |
|---|---|---|---|
| Night mind, per active member | 2-7 (cap 7) | 2-7 | ~25 s per member-night on the 4B (smoke); ~3-12 min on the 12B [derived from 3.62 tok/s] |
| Struggle clustering wording | 0-2 | 1-2 | Code clusters; the model only rewords counts |
| Semantic-entropy filter (E6) | 5 per moment batch | not planned | 4B only |
| Omi day review | per synced day | per synced day | Was floor-killed on 10-29 and 10-30 beside the 12B |

With one active member the 12B fits; with four active members and Omi days it does not, on the measured decode rate. That is the arithmetic behind bullet 4 of section 0.

## 3. Event-driven vs scheduled vs hybrid

### 3.1 The three options on Zoe's facts

| Property | Event-driven (react to each event) | Scheduled (tick / nightly) | Hybrid (capture by event, decide by schedule or by turn) |
|---|---|---|---|
| Idle cost | Zero when nothing happens; bursts when the house is busy | Constant small tick | Zero capture cost; tick already exists |
| Latency to notice | Immediate | Up to one tick (300 s) or one night | Immediate capture; decision at the next tick, presence or turn |
| Slot safety | Dangerous: an event that escalates to the 4B lands whenever, including mid-conversation | Safe: night jobs run when the 4B is stopped | Safe if escalation is restricted to the person's own turn and the night |
| Failure mode | Storms (HA `state_changed` floods, deploy restarts replaying events) | Staleness (a 07:30 item computed at 03:00) | Two code paths to keep consistent |
| Fit with pull-not-push | Pushes toward interrupting, which the owner forbade | Natural: precompute, then wait to be asked | Natural |
| Zoe precedent | Reminders (APScheduler one-shots), struggle `observe` queue | Night window, music jobs, slow loop | Struggle detector (capture per turn, cluster nightly) - already the pattern |

### 3.2 Verdict

**Hybrid, with one rule: events write, schedules and turns decide, and only the night or a person's own turn may spend a language model.** [Proven pattern inside Zoe: the struggle detector already captures per turn and explains overnight; the selector precomputes nightly and the turn carries one RAISE.] Pure event-driven cognition is wrong for Zoe because the one place a reaction is useful (a timer, a reminder) is already event-driven, and everything else is something the person should pull. Pure scheduling is wrong because presence is the right moment to re-score "due today", not 03:00.

### 3.3 The reasoning-scheduler policy, written down

1. Code may run any time; it must be bounded (timeouts, queue caps) and fail toward "do nothing".
2. bge-small and the 270M router may run any time off the reply path; they are resident and do not use the brain slot.
3. The 4B by day is spent **only inside a turn the person started** (packet content, one RAISE, phrasing a pull). No idle-time 4B calls until E3 shows the slot cost is nil.
4. The night window (4B always; 12B when it loads) does consolidation, reflection, clustering, uncertainty sampling, with a per-night call budget written into the run row.
5. Every escalation records its reason and its cost (tokens, seconds) so the scheduler itself can be audited.

### 3.4 Storms, restarts and idempotency

- **Deploy restarts** (64 zoe-data starts in a week, completeness section 6) must not replay events into raises. Every derived action keys on `(item_id, local_day, touch_point)` and is a no-op when that key exists.
- **HA floods** are bounded at the subscription: an allow-list of entity domains, a per-entity debounce, and a hard per-minute cap; above the cap the journal records one "storm" row and drops the rest.
- **Clock and quiet hours:** every scheduled re-score respects `engine._is_in_quiet_hours`; nothing surfaces 22:00-07:00 (the engine's existing quiet hours).
- **Night window overlap:** zoe-data is stopped during the window, so day code cannot race it; the window's writes are committed whole or not at all (the night mind's "never half a night").
- **Backpressure:** the struggle detector's pattern (bounded queue, `put_nowait`, drop and count) is the template for any event intake.

### 3.5 Each component's trigger, chosen

| Component | Event | Schedule | Turn | Night |
|---|---|---|---|---|
| Perception journal | **yes** (capture) | - | - | - |
| Salience re-score | presence event | slow-loop fallback (300 s) | cue match off the reply path | full ranking |
| Commitment lifecycle | outcome events | touch-point times | pull reply | resolution reading |
| Context precompute | presence event | - | used in the turn | - |
| Consolidation / reflection | - | - | turn digest (live) | **yes** |
| Metacognition | per-turn signals (live, shadow) | - | - | clustering |
| Engagement | - | - | **only here** (pull, RAISE) | - |

## 4. Phase 8: the loop, traced through one household example

### 4.1 The example (synthetic member names, real mechanics)

Tuesday 19:40, at the panel. Member "Robin" (verified voice) says: "I've got the dentist Thursday at ten, I'm honestly dreading it."

| Stage | What happens | Component | Today (live state) | With the integrated loop |
|---|---|---|---|---|
| **Perception** | Moonshine transcript; voice verdict says Robin; turn row written | STT, identity, `chat_messages` | Live | Plus a journal row `(turn, Robin, panel)` |
| **Attention (in turn)** | The router sends it to the brain (no tool); the brain answers kindly | router, packet, 4B | Live | Unchanged: no extra latency in the turn |
| **Memory (after turn)** | Turn digest extracts an open loop "dentist Thu 10:00" with emotional weight; night mind (02:50) makes a moment `{kind: plan, feeling: dread, later: due}` and a thread | digest, `open_loops` (`ZOE_LOOP_LIFECYCLE=1`), night mind | Loop: live. Thread: written by the night window, **not served** (readers off) | Thread served (owner flag) |
| **Reasoning (night)** | DECIDE: the thread is `raise`-eligible Thursday; the "leave" deny-list is checked in code (whether a dental visit counts as a health condition is the policy's call, never the model's); selector ranks it: importance 0.8 x recency x relevance 1.0 (due within 24 h) | night mind DECIDE, selector | Selector live; ranks loops nightly | Same, plus ACT-R activation (mentioned twice = higher) |
| **Planning** | Two touch-points: Thursday morning before 10:00 (a good-luck), Thursday evening (how did it go) | commitment manager | Not modelled: one candidate, one raise | Lifecycle `due -> raised -> follow-up -> resolved` with two scheduled re-scores |
| **Engagement** | Thursday 08:10 Robin's first panel session of the day -> re-score -> orb shows "something"; Robin says "morning, what's up?" -> pull delivers "Dentist at ten today - hope it's quick." If Robin instead just asks the weather, the greeting-turn RAISE may carry it inline, once | presence re-score, `pull.py`, selector RAISE | Orb and pull live; re-score only nightly, so a candidate computed at 03:00 is used | Re-score at first presence; one surface only, never spoken unprompted |
| **Outcome** | Robin: "thanks, yeah" (welcome) or "not now" (back-off), and Thursday evening "it was fine actually" | delivery ledger, welcome tap | Ledger records class-level welcome; **nothing marks the loop resolved from Robin's words** unless the digest happens to | Outcome edge: the night mind's `finishes` reading resolves the thread; the welcome / ignore is stored on the thread and feeds its own back-off |
| **Memory (closing)** | The loop is resolved; the feeling is not carried forward as a "dentist fear" fact (inference never supersedes, and affect policy decides what persists) | authority wall, affect policy | Live floors | Unchanged floors |

What is new in the right-hand column is small: serving the thread (a flag), one re-score at presence (code), a two-touch lifecycle (code), and writing the outcome back to the item (code). **No new model, no new process, no daytime 4B call beyond the one the person's own turn already pays for.**

### 4.2 The measurable benefit it must show over the reactive design

The reactive design answers well when asked and forgets to follow through. The integrated loop must beat it on follow-through without costing the household anything. Pre-registered metrics:

| Metric | Definition | Baseline (to measure first) | Success | Failure |
|---|---|---|---|---|
| **Timely follow-through** | Share of dated open loops (due date, real member) that are surfaced (pull or RAISE) within their due window | Measure 30 days of current state from `open_loops` + ledger | >= 60% and >= 2x baseline | < 1.5x baseline |
| **Welcome rate** | Welcomed / (welcomed + "not now" + ignored) on those surfaces | Current ledger | >= 70% | < 50%, or any rise in "not now" |
| **Resolution** | Share of surfaced loops marked resolved by the person's own later words | ~0 expected (no edge today) | >= 50% | < 20% |
| **Recall reaching speech** | Share of household replies using >= 1 memory source (synthesis: 83% used zero) | Live counter | +15 points | No change |
| **Cost floors (veto)** | Panel turn p90; waking resident RAM; slot occupancy outside turns | Current | dp90 <= +50 ms; RAM <= +20 MB; zero idle 4B calls | any breach = stop |
| **Negative control** | Same pipeline with candidates shuffled across members / days | - | Welcome rate falls clearly (shows the metric detects relevance) | Welcome rate unchanged = metric is measuring politeness, not relevance |

Power: with ~1 active member and bursty use, the household may generate a few dated loops a week. 40 surfaced loops is the minimum for a 60%-vs-30% difference to be distinguishable [derived: rough binomial, not a formal power analysis]. Until usage rises, the synthetic household (the day-sim and `run-synthetic` endpoint) can exercise the plumbing but **cannot** show the benefit; only real loops can.

### 4.3 The simplest architecture that could produce it

```
 events (turns, panel sessions, pulls, raises, outcomes, reminders) --append--> journal (view over existing rows first)
                                                                                    |
 night window (4B always, 12B if it loads):  digest -> night mind -> selector ------+--> candidates + threads (served)
                                                                                    |
 day, code only:  first presence of a member --> re-score "due today" (SQL) --> orb count / pull / one RAISE in the turn
                                                                                    |
 outcome:  welcome / not now / ignored / "it was fine" --> written to the item --> back-off + resolution at night
```

Four changes, each flag-gated and individually revertible: (a) night-mind readers `serve` (owner, after replay gate); (b) a presence-time re-score in `proactive/` (code); (c) a lifecycle with two touch-points on dated loops (code); (d) outcome on the item (code). A journal *view* first, a table only if E2 shows the view is too slow.

### 4.4 A counter-example: where the loop should NOT engage

Thursday 23:10, the back door contact reports open for 20 minutes; nobody is at a panel.

- A cognitive-architecture reading says: perception -> salience (security, high) -> reasoning -> engagement (speak). That is exactly the wrong design for Zoe. Quiet hours forbid speech, nobody asked, and Home Assistant already has the right tool for this: an automation with a notification, owned by the household, deterministic, and testable.
- The correct integrated behaviour is smaller: if E2's journal shows the event, the *next morning's pull* may carry "the back door was open for twenty minutes last night" as a `notify` item, only if a household member set that preference. No model is needed at any step.
- The lesson for the whole lane: **when an action is time-critical and rule-shaped, it belongs to an automation, not to cognition.** Cognition is for the things a rule cannot see: that Robin dreaded Thursday, that a plan quietly stopped being mentioned, that a request failed three times in a week.

### 4.5 What the reactive design already does well, and must keep

- Answers fast when spoken to; on the measured router corpus only ~14.8% of turns fall through to the brain (blueprint 2.2).
- Holds floors structurally: authority order, identity from the account, forgotten-means-forever, affect policy, leave-list never rendered into a prompt.
- Never speaks first. Every change in this report preserves that.
- Precomputes raises nightly with a published formula and caps.

The integrated loop is additive and revertible: turn its flag off and Zoe is today's Zoe.

### 4.6 A second trace: the metacognitive loop on a fumbled request

The same loop, applied to Zoe herself rather than to the household's plans.

1. **Perception.** Monday 18:02 Robin: "play the rain sounds in the kitchen". The router routes to music; Music Assistant's provider is down; Zoe says "I can't do that right now." 18:02:20 Robin repeats it; same reply. Robin gives up.
2. **Attention (code).** The struggle detector records CANT_DO on the first turn and RE_ASK + CANT_DO on the pair (shadow today); harness panels are excluded.
3. **Memory.** `struggle_events` rows with the domain, path and evidence ids.
4. **Reasoning (night).** Code clusters the week: 6 CANT_DO on `music` across 3 days, all while the provider health read was failing. The 12B (or 4B) only rewords the count into a problem statement.
5. **Planning.** A proposal of fix class "capability down, say so" rather than "retrain the router" (Soar's impasse: the right subgoal is restoring the capability, not re-routing).
6. **Engagement.** The owner sees it on the proposal queue; the household, on the next music ask, hears "music isn't working right now - I've flagged it" (the synthesis's "she tells you the truth about herself"), not a bare refusal.
7. **Outcome -> memory.** The provider is reconnected; CANT_DO on `music` drops to zero; the proposal closes with its before/after counts.

Measurable benefit here: **time from first repeated failure to an owner-visible problem statement** (today: until someone notices; music last played in July and nobody was told it was broken, per the synthesis) and **repeat-failure count per incident**. Both are code, both run on existing shadow data, and this loop is probably the higher-value of the two traces while household use is sparse, because Zoe's own failures generate signal even when the household does not.

## 5. Experiments

Each runs flag-dark or in shadow first, under the test-lane flock, outside the night window, and with a negative control that must be seen red before a green counts (the synthesis's universal self-test rule).

### 5.1 Instruments every experiment shares

- **Real-household filter:** only real member ids, never harness panels or synthetic users (the struggle detector's filter), so the 97% harness traffic the synthesis measured cannot dilute the numbers.
- **Cost floors as vetoes:** panel p90 (existing request logs), MemAvailable minimum during waking hours (ground truth), idle-time slot occupancy (llama-server `/slots` polled read-only), night outcome per run row.
- **Self-test before green:** each metric script ships a planted-fault case that must turn it red.
- **Pre-registration:** the success and failure lines of section 4.2 are fixed before the shadow fortnight begins; changing them restarts the clock.

### E1. Baseline the loop that already exists
- **Hypothesis:** dated open loops are rarely surfaced in their window today; follow-through < 30%.
- **Implementation:** read-only SQL over `open_loops`, `proactive_candidates`, the delivery ledger and `chat_messages` for 30 days; classify each dated loop as surfaced-in-window / late / never.
- **Resources:** zero runtime; one query script. **Evaluation:** the follow-through table of 4.2. **Negative control:** plant three synthetic dated loops with known surfaces in a scratch schema; the script must count them exactly.
- **Success / failure:** produces a baseline (any number is a success); fails if the ledger cannot join to the loop (that itself is the missing edge).
- **Effort:** 0.5-1 day.

### E2. Shadow perception journal
- **Hypothesis:** a unified journal costs < 1 ms per event and < 50 MB disk per month, and > 50% of events that later matter (a loop raised, a struggle) are visible in it at the time.
- **Implementation:** a Postgres view over existing tables; optionally a filtered HA WebSocket `subscribe_events` for a short allow-list of entity domains (doors, media players), log-only, never consumed.
- **Resources:** zoe-data CPU; disk. **Evaluation:** insert timing, rows/day, storm behaviour on a deploy restart. **Negative control:** an HA entity outside the allow-list toggled 100 times must produce zero rows.
- **Success:** bounded cost and no storm. **Failure:** event volume > 10k/day after filtering, or any p90 movement. **Effort:** 1-2 days.

### E3. The price of one daytime 4B call (lab, not live)
- **Hypothesis:** an idle-time 4B call costs the next panel turn >= 2 s through prefix eviction; slot save/restore (#2209's study) reduces it to < 200 ms.
- **Implementation:** a lab llama-server on the same build with the live prompt prefix; sequence "panel turn, background call, panel turn" with and without `--slot-save-path` restore; 50 repetitions.
- **Resources:** lab port, runs only when the household is idle and under the test-lane lock; RAM is the risk - run only with Kokoro and the live 4B healthy and MemAvailable above floor, or in the 12B-free part of the night.
- **Evaluation:** next-turn TTFT delta. **Negative control:** a background call with an identical prefix must show ~0 delta.
- **Success:** confirms or refutes policy rule 3. **Failure:** cannot be measured without breaching the voice floor -> rule 3 stays. **Effort:** 1-2 days.

### E4. Precomputed packet at first presence (sleep-time compute, code form)
- **Hypothesis:** computing the recall block when a member's panel session becomes owner (or a verified voice turn starts) removes ~100 ms from the first turn and raises "sources used" on first turns.
- **Implementation:** a short-TTL per-member cache in the packet builder, filled by the presence hook; flag-dark.
- **Resources:** < 10 MB, bge-small (resident). **Evaluation:** first-turn recall span, sources-used counter, replay-gated (voice path). **Negative control:** a cache keyed to the wrong member must be refused by the identity check (cache hit rate 0 for mismatches).
- **Success:** latency down, sources up, no identity leak. **Failure:** cache staleness causes a wrong-person or forgotten item to surface even once (stop). **Effort:** 2-3 days + the replay gate.

### E5. Who should judge salience: code, 270M, or 4B-at-night
- **Hypothesis:** code salience matches a 4B night judge within 5 points on welcome prediction; the 270M adds nothing.
- **Implementation:** offline replay over the ledger's labelled raises (ProactiveBench-style accept / reject labels); score each arm's ranking against outcomes.
- **Resources:** offline; the 4B arm runs in the night window only. **Negative control:** shuffled labels must drop every arm to chance.
- **Success:** keeps the cheapest arm. **Failure:** too few labels (likely today) -> collect for 60 days first. **Effort:** 1 day once labels exist.

### E6. Night budget: 4B-many vs 12B-few
- **Hypothesis:** for the night mind's cells, the 4B with semantic-entropy filtering (5 samples) matches or beats the 12B single pass within the same 20 minutes, and never loses a night to a load failure.
- **Implementation:** the existing `--cells-only` harness, two arms, same corpus seeds.
- **Resources:** the night window or a manual off-hours slot; existing scripts. **Evaluation:** K-cell scores, calls per night, nights lost. **Negative control:** the echo control already in the K-cells.
- **Success:** a decision on what the window should plan around. **Failure:** both arms equal -> keep the 12B as upgrade only (ties go to the reliable path). **Effort:** 2 nights + 1 day.

### E7. The outcome edge (the Phase-8 test proper)
- **Hypothesis:** with E1's baseline, adding presence re-score + two-touch lifecycle + outcome-on-item raises follow-through >= 2x with welcome >= 70% and no cost-floor breach (section 4.2).
- **Implementation:** the four changes of 4.3 behind one flag, shadow first (compute what would surface, log, surface nothing) for two weeks, then enforce.
- **Resources:** code only. **Evaluation:** section 4.2's table, weekly. **Negative control:** the shuffled-candidate arm in shadow.
- **Success / failure:** as pre-registered in 4.2; stop on any floor breach or a single wrong-person surface. **Effort:** 4-6 days build, then calendar time for real loops.

### E8. Time-to-truth for Zoe's own failures (the section 4.6 loop)
- **Hypothesis:** on the existing shadow struggle data, a code-only clustering of CANT_DO / RE_ASK by domain, joined with capability health reads, surfaces a "capability down" problem statement within 24 h of the second repeated failure, where today it takes until a human notices.
- **Implementation:** read-only over `struggle_events` and existing health reads; a nightly report row; no behaviour change. The household-facing "it isn't working right now - I've flagged it" line is a separate, replay-gated change after the report proves precise.
- **Resources:** code, night or off-hours. **Evaluation:** precision of "capability down" statements against the operator's incident record; time-to-statement. **Negative control:** a week with a planted healthy-provider CANT_DO (a policy refusal) must produce no statement.
- **Success:** >= 90% precision, median < 24 h. **Failure:** statements fire on policy refusals or on harness traffic. **Effort:** 1-2 days.

### Order and total cost

Order: E1, E2 and E8 first (cheap, they gate everything), E3 before anyone proposes a daytime LLM, E7 as the headline, E4-E6 alongside as capacity allows.

Total build effort for E1-E8 is roughly 12-20 engineer-days [Speculative est.]; none adds resident RAM by day, and only E3 touches a model server outside the night (in a lab port, under the test-lane lock).

## 6. What is speculative, and where this becomes complexity without intelligence

### 6.1 What is speculative in this report
- All latency figures for the journal, the SQL re-score and bge-small cue matching are estimates; only recall p95, router p50, prefill / decode rates and the 12B night figures are measured, and the derived slot costs depend on them.
- That follow-through is what the household *wants* is an assumption; the owner's earlier decision against unprompted speech shows the line between helpful and intrusive is personal. The welcome rate and its negative control are there to catch a wrong assumption.
- The ACT-R activation and novelty features are untested borrowings; they could add nothing over the existing formula.
- HA events as perception assume the household's devices say something meaningful; with no presence sensors the stream may be mostly media players and lights.
- The number of real dated loops per week is unknown; E7 may be unpowered for months.

### 6.2 Where it would be complexity without intelligence
1. **A literal global workspace.** Coalitions of codelets competing every few hundred ms over a stream of a few hundred events a day is a priority queue with extra vocabulary. The selector's CAP and one-RAISE rule are the workspace.
2. **A resident "cognition daemon".** A separate process with its own loop, state and health check duplicates zoe-data's scheduler, adds a restart surface and a new way to be "merged but not live" (synthesis pattern 1). The slow loop and APScheduler are the daemon.
3. **Daytime background reasoning on the 4B.** Every idle-time thought is paid in the household's next p90 through the one slot. Until E3 proves otherwise it makes Zoe slower and is the harness-traffic problem the synthesis measured, renamed.
4. **Self-reflection prose.** A small model writing insights about itself or the household produces confident text with no external check; the portrait arm already lost 3 vs 4 of 21. Reflection must stay pointer-shaped and signal-gated.
5. **Curiosity and exploration.** In a home, "exploring" means asking questions nobody asked for. ATRBench-style "asking to remember" has value; generic curiosity is nagging.
6. **A world model of the home.** Home Assistant already *is* the state model of the house. A second model, or a predictive-processing layer trained on 9 days of use a month and no presence data, predicts noise.
7. **Durable workflow engines.** Temporal or Restate for loops that live a few days, on a box with ~650 MiB to spare, buys exactly-once semantics for problems a Postgres row and an idempotent job already solve.
8. **Hierarchical planners.** Household goals are two steps long. A planner is justified the day Zoe books things across several services; not before.
9. **More measurement than use.** Seven experiments for one member who talks to Zoe on a third of days risks repeating the synthesis's warning: a self-evolving system with no signal evolves toward its harness. E1 and E7 are the two that matter; the rest are optional.

### 6.3 Open questions for the owner

1. **Night-mind readers to `serve`.** Everything in section 4 depends on it; it is an owner flag after a replay gate.
2. **Follow-through as a goal.** Is "Zoe mentions the dentist on the morning, once, if you ask what's up" wanted, or too much? The welcome rate will say, but the owner's prior sets the default.
3. **HA events as perception, shadow-only.** Allowed to log an allow-listed set of entity changes (no audio, no video, no new sensors)?
4. **Night planning order.** Plan the window around the 4B (reliable) with the 12B as upgrade, pending E6?
5. **Daytime model use.** Confirm the rule "by day the 4B is spent only inside a turn the person started" as policy until E3 says otherwise.

### 6.4 The one-line answer to the central question
Yes, Zoe can run persistent cognition at near-zero idle cost - and mostly already does, as a nightly reflective pass, a live salience selector, a shadow metacognitive detector and a pull-only engagement surface. "Emergent intelligence through integration" for Zoe is not a new architecture; it is closing three edges (serve what the night learned, re-score at presence, write outcomes back) and proving with a pre-registered follow-through number, a negative control and unchanged p90 that the closed loop does something the reactive one cannot.
