---
type: research
title: Samantha Initiative lane B - autonomous agents, self-improving systems, autonomous R&D (frontier vs Zoe)
date: 2026-11-03
status: lane report
---

# Lane B: autonomous agents, self-improving systems, autonomous R&D

Scope: Phase 2.1 (autonomous agents), Phase 2.7 (self-improving systems), Phase 7 (autonomous R&D). Read-only research; no code, flag, service or box state changed.

**Evidence labels.** Maturity tags: **[Proven]** shipped and measured by someone other than its authors, or in daily production; **[Experimental]** research code or early product, results self-reported; **[Speculative]** idea or unreplicated claim. Source tags: **(repo)** = I opened the project's GitHub page this session; **(search)** = I only saw search-result summaries, not the paper itself, so numbers are the authors' claims relayed by a secondary source; **(zoe)** = read in this worktree. Where I could not open something I say so. Nothing here quotes household data.

**Method note.** I fetched the repo pages for Harness2, DGM, GEPA, OpenEvolve, SICA and ACE. Every paper figure below is authors' self-reported unless stated, and for most papers I saw only abstracts/summaries. I did not clone or run any source (no opensrc reads) - the verdicts rest on READMEs, abstracts and Zoe's own constraints, not on code review.

---

## 0. What Zoe should take from this (ten bullets)

1. **Zoe's nightly-loop design is already the field's best-evidenced shape.** Harness2 (propose-probe-compose over a harness, weights fixed, no ground-truth verifier) is the same loop the design adopted on 2026-10-19; GEPA, ACE and DGM independently converge on "edit text/code around a frozen model, accept only what an external evaluator confirms". Do not redesign; build Phase 0/1. [Experimental, converging]
2. **The binding constraint is the evaluator, not the improver.** Every serious system (DGM, GEPA, OpenEvolve, AppWorld) needs a scored, automatable objective. DGM's own paper documents objective hacking against its metric. Zoe's design (held-out cells, control arms, zero-for-unmeasured) is correct and ahead of most; the money should go into making the instruments honest (Phase 0) before any improver runs.
3. **Run the cheapest improver first: GEPA-style reflective prompt evolution on the overlay, not DGM-style code self-modification.** GEPA is MIT, `pip install`, works on API models, claims big sample-efficiency over RL with as few as 3 examples; it edits exactly the kind of artefact Zoe's overlay holds (doctrines, manner, tool guidance). Code self-modification (DGM/SICA) is the wrong risk class for a household box.
4. **ACE's "playbook with incremental delta updates" is the right shape for Zoe's lesson library.** Append/merge small structured bullets with counters instead of rewriting a whole prompt (avoids "context collapse"). Adopt the data structure, not the framework.
5. **Sleep-time compute is the academic name for what the night window already is.** Letta/Berkeley report ~5x less test-time compute for equal accuracy when a model pre-digests context offline (search-summary level). Zoe's 12B night job is the right place; the cheapest next step is precomputing answers to predictable questions (the day's likely asks) into the recall cache.
6. **Tool CREATION should be tiny and mediated, not generative.** LATM (strong model writes a tested Python tool, weak model uses it) and Voyager (embedding-indexed executable skill library) work in closed domains. For a household, "a missing capability" is almost always an integration (a feed, an API, a schedule), so the right unit is a *declarative recipe* (fetch X, extract Y, show card Z) validated by tests - not arbitrary code - built by the cloud tier (Omnigent) only when the household opted in.
7. **Plain tool-calling with a good router and progressive disclosure beats most "agent frameworks" for a household.** Retrieval over tools (RAG-MCP, search-summary: selection accuracy 43% vs 14% on its benchmark, prompt tokens halved) validates Zoe's FunctionGemma two-stage router and `tool-groups.ts` progressive disclosure. Multi-agent orchestration helps breadth-first research (Anthropic: +90% on its internal eval, ~15x tokens) and is the wrong default for a 6-8 s voice turn.
8. **The things that demonstrably beat plain tool-calling for a household are not planners.** They are (a) durable memory/lesson injection every turn (ACE/AWM/Reflexion family), (b) reliability measurement with pass^k rather than pass@1 (tau-bench), (c) offline pre-computation, and (d) a write-claim/receipt guard. Zoe has (d), is building (a)-(c).
9. **Zoe is behind in exactly three places:** no running improver of any kind (Phase 1 not started); no pass^k / repeated-trial reliability metric on her instruments; no tool-gap detector that turns "I can't do that" into a ranked capability backlog (CANT_DO events exist in the struggle detector but nothing clusters them into build requests).
10. **Smallest useful experiment (section 3, E1):** one night, one overlay file, K=2 GEPA-style candidates probed on an existing bar subset against a sampling control, shadow-apply only, with the cloud reflection model on a capped key. It is a ~2-week lab build that answers the only question that matters: does the improver beat repeated sampling at equal cost?

---

## 1. Project / paper table

Columns: purpose; architecture; what I inspected; maturity; hardware (can it share an ~8 GB free envelope with the live brain?); licence; verdict. "Cloud" = needs a strong API model for the improver step; the probes can still be local.

| # | Project / paper | Purpose and architecture | Inspected | Maturity | Hardware / cost | Licence | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | **Harness2** (Google Research) [repo](https://github.com/google-research/harness2), paper arXiv 2610.14224 ([summary](https://hyper.ai/en/papers/2610.14224)) | Optimise the harness (prompt, guardrails, memory, tool guidance, skills, scripts) with weights fixed. Task-level Propose-Probe-Compose (contrastive, no ground-truth verifier) + harness-level persistent "improvement harness" of lessons. Executors OpenCode/Codex; parallel and sequential modes | repo README (repo); paper arXiv ID verified 2026-11-03 | Experimental; "not an officially supported Google product"; claims up to +11.5 pts WorkBuddy-Bench, +4.5 JobBench, +11.8 pp LAB (self-reported); ~22k stars (repo page) | Python 3.12 + uv; real runs need Vertex/Node/Docker and API spend; local demo has no credentials. Probes could be local, improver cloud | Apache-2.0 | **Adapt** (already adapted in Zoe's design; lift the pattern, not the code) |
| 2 | **Darwin Godel Machine** [Sakana](https://sakana.ai/dgm/), [arXiv 2505.22954](https://arxiv.org/abs/2505.22954), [repo](https://github.com/jennyzzt/dgm) | Coding agent rewrites its own code; archive of agents (open-ended evolution); empirical validation on SWE-bench/Polyglot instead of proofs. 20->50% SWE-bench, 14.2->30.7% Polyglot (self-reported) | repo README (repo); paper via summary (search) | Experimental. README warns it executes untrusted model-generated code. One secondary source claims ~$22k / two weeks per run (unverified) | Docker + OpenAI and Anthropic keys + SWE-bench setup. Cloud. Does not fit beside a live brain | Apache-2.0 | **Reject for Zoe's runtime; study for its safety case** (sandbox, traceability, objective-hacking case study) |
| 3 | **SICA - A Self-Improving Coding Agent** [arXiv 2504.15228](https://arxiv.org/abs/2504.15228), [repo](https://github.com/MaximeRobeyns/self_improving_coding_agent) | One agent edits its own Python codebase; best-of-archive is next meta-agent. 17->53% on a random SWE-bench Verified subset (self-reported; small subset) | repo README (repo); paper via summary (search) | Experimental. README insists on Docker | Docker + provider API key; cloud | MIT | **Reject for runtime; reference for archive+utility function (benchmark score, cost, time)** |
| 4 | **ADAS / Meta Agent Search** [arXiv 2408.08435](https://arxiv.org/abs/2408.08435) | Meta-agent writes new agents as code, tests, archives them | search summary only | Experimental (ICLR); gains on ARC/DROP/math, not household tasks | Cloud for the meta-agent | Not checked | **Reject** (code-space search is more freedom than a household needs; Harness2/GEPA search a safer space) |
| 5 | **GEPA** (Genetic-Pareto prompt evolution) [arXiv 2507.19457](https://arxiv.org/abs/2507.19457), [repo](https://github.com/gepa-ai/gepa), `dspy.GEPA` | Reflect on full execution traces in natural language, mutate prompts/text params, keep a Pareto frontier of diverse candidates. Claims ~+10% over GRPO with up to 35x fewer rollouts, and +10-14% over MIPROv2 (summaries) | repo README (repo); claims via search | Experimental-to-Proven: ICLR 2026 oral (search), 7.3k stars, DSPy integration, one production report (Decagon, search) says weak reflection models gave no gain and strong ones ~5-6% | `pip install gepa`; needs a task LM and a *strong* reflection LM; as few as 3 examples; API-only works. Probes on local 4B, reflection on cloud or the 12B at night | MIT | **Experiment first** (E1) |
| 6 | **DSPy** (MIPROv2, BootstrapFewShot, GEPA) [dspy.ai](https://dspy.ai) | Program-your-LM framework with compile-time optimisers | not opened this session | Proven (wide use) | CPU; optimiser calls need an LM | MIT (licence verified 2026-11-03) | **Experiment as a library for E1 only**; do not adopt as a runtime framework ("adopt, don't rebuild" but also "pieces, not frameworks") |
| 7 | **TextGrad** (Yuksekgonul et al.) | Backprop-style natural-language "gradients" through an LLM pipeline | not opened (a search for it returned nothing usable) | Experimental/Proven in-paper | cloud | not checked | **Reject** (superseded in practice by GEPA for prompt optimisation; I could not source it this session) |
| 8 | **ACE - Agentic Context Engineering** [arXiv 2510.04618](https://arxiv.org/abs/2510.04618), [repo](https://github.com/ace-agent/ace) | Generator / Reflector / Curator roles evolve a "playbook" via incremental delta updates; avoids brevity bias and context collapse. +10.6% on agent tasks, matches top AppWorld agent with a smaller open model (self-reported) | repo README (repo); abstract (search) | Experimental; repo has 12 commits, no releases; tool-calling tutorials "coming soon" | Python/uv; default DeepSeek-V3.1 via API; offline/online modes | Apache-2.0 | **Adapt** the playbook/delta data structure for the lesson library |
| 9 | **AlphaEvolve** (DeepMind) and **OpenEvolve** [repo](https://github.com/algorithmicsuperintelligence/openevolve), **ShinkaEvolve** [arXiv 2509.19349](https://arxiv.org/abs/2509.19349) | LLM-guided evolutionary code search with an automated evaluator; OpenEvolve = MAP-Elites + islands + LLM ensembles; supply an evaluator script and mark `EVOLVE-BLOCK` regions | OpenEvolve README (repo); Shinka via search | Experimental; strong on closed numeric problems (circle packing, kernels) - not household behaviours | Python 3.10+; any OpenAI-compatible endpoint (local server plausible, my inference) | OpenEvolve Apache-2.0 | **Experiment, narrowly**: optimise deterministic hot-path code with a real benchmark (e.g. router post-processing, grammar builders), never the persona |
| 10 | **Voyager** [arXiv 2305.16291](https://arxiv.org/abs/2305.16291), [MineDojo/Voyager](https://github.com/MineDojo/Voyager) | Automatic curriculum + ever-growing library of executable skills indexed by description embedding + iterative prompting with environment feedback. 3.3x unique items in Minecraft (self-reported) | search summary | Experimental (2023); the *skill-library* idea is the most-copied part of the field | GPT-4 class; not local | MIT (licence verified 2026-11-03) | **Adapt** the idea (retrieve by description, store only verified skills); Zoe's `skills/` + SKILL.md plan is already this |
| 11 | **Agent Workflow Memory** [arXiv 2409.07429](https://arxiv.org/abs/2409.07429) (ICML 2025) | Induce reusable workflows from successful trajectories and put them in memory; online or offline. +24.6% relative Mind2Web, +51.1% WebArena | search summary | Experimental/Proven in-paper | cheap at inference; induction needs an LLM | not checked | **Adapt**: induce routines from the household's successful multi-step turns (E3) |
| 12 | **Reflexion** [arXiv 2303.11366](https://arxiv.org/abs/2303.11366), **Self-Refine** (Madaan et al.) | Verbal lessons from environment feedback stored for next episode; Self-Refine = same-model critique loop | search summary | Proven as technique, but Self-Refine has no external signal | free | not checked | **Adapt Reflexion only where an external signal exists** (struggle events); **Reject Self-Refine for the 4B** (Zoe's own rule: the 4B must never reflect on itself) |
| 13 | **Live-SWE-agent** [arXiv 2511.13646](https://arxiv.org/abs/2511.13646), [OpenAutoCoder/live-swe-agent](https://github.com/OpenAutoCoder/live-swe-agent) | Starts as a bash-only mini-SWE-agent and evolves its own scaffold *while solving* each task; 77.4% SWE-bench Verified in v3 (self-reported) | search summary | Experimental; shows on-the-fly beats offline DGM-style cost | cloud | not checked | **Reject for household** (coding-specific); note that online self-extension without a benchmark farm is viable |
| 14 | **OpenHands** and **mini-swe-agent** / SWE-agent [SWE-agent org](https://github.com/SWE-agent) | Software-engineering agent platforms, Docker-sandboxed sessions; mini-SWE-agent is the minimal bash-only harness | search summaries only; not opened | Proven (widely used; leaderboard numbers differ by harness, not like-for-like) | Docker + strong model | OpenHands MIT (licence verified 2026-11-03; mini-swe-agent and SWE-agent also MIT) | **Reference for the Omnigent builder lane** (Zoe already has Omnigent/Codex/Claude Code as builders); do not run on the Orin |
| 15 | **LATM - LLMs as Tool Makers** [arXiv 2305.17126](https://arxiv.org/abs/2305.17126) | Strong model writes + unit-tests a Python tool; weak model uses it; tools cached, not answers | search summary | Experimental (ICLR 2024) | maker cloud, user local | not checked | **Adapt**: maker/user split matches Omnigent-builds, Orin-uses |
| 16 | **Sleep-time compute** (Letta + Berkeley) [arXiv 2504.13171](https://arxiv.org/abs/2504.13171), [Letta blog](https://letta.com/blog/sleep-time-compute) | Offline inference between interactions to pre-digest context; ~5x less test-time compute, 2.5x lower amortised cost per query (search summary) | search summary | Experimental, productised in Letta | uses idle capacity (the 12B night window fits) | Letta Apache-2.0 (licence verified 2026-11-03) | **Adopt the principle** (already in the night window); extend to answer pre-computation |
| 17 | **Hermes Agent** (Nous Research) [docs](https://hermes-agent.nousresearch.com/docs) | Open-source personal agent (Feb 2026, per search) with agent-curated memory, autonomous skill creation after multi-step tasks, skill refinement on failure-then-success, FTS5 recall | docs page via search only | Experimental; limited production case studies (search). NOTE Zoe retired its Hermes integration 2026-07-22 | cloud or local model | not checked | **Reference only** - the skill-write loop is what the 10-04 self-building doc already designed; do not resurrect |
| 18 | **RAG-MCP / tool retrieval** [arXiv 2505.03275](https://arxiv.org/abs/2505.03275) | Retrieve the few relevant tool descriptions instead of listing all. 43.1% vs 13.6% selection accuracy, prompt tokens roughly halved on one benchmark (search summary); degrades toward thousands of tools | search summary | Experimental; no independent replication found | negligible | not checked | **Already equivalent** in Zoe (tool-groups.ts + two-stage router); keep |
| 19 | **Anthropic multi-agent research system** [engineering post](https://www.anthropic.com/engineering/multi-agent-research-system) | Orchestrator + parallel subagents; +90.2% vs single agent on an internal research eval; ~15x token cost (secondary) | search summary | Proven in production (their own) | token-heavy | n/a | **Adapt narrowly**: only for the deep-research errand lane, never the voice turn |
| 20 | **tau-bench / tau2-bench** [arXiv 2406.12045](https://arxiv.org/abs/2406.12045) | Tool-agent-user simulation with policy docs; end-state DB comparison; **pass^k** = all k trials succeed | search summaries | Proven as a metric; scores not comparable across setups; simulator noise concern raised | CPU + a user-simulator model | not checked | **Adopt the metric** (E2) |
| 21 | **AppWorld** [arXiv 2407.18901](https://arxiv.org/abs/2407.18901), [stonybrooknlp/appworld](https://github.com/stonybrooknlp/appworld) | 9 everyday apps, 457 APIs, ~100 virtual users, 750 tasks, state-based tests that also detect collateral damage | search summary | Proven (ACL 2024) | Docker-free local engine | not checked | **Adopt the idea of collateral-damage checks** in Zoe's bar; do not adopt the apps |
| 22 | **Zoe's own** nightly loop, struggle detector, router self-train | See section 2.9 | read in this worktree (zoe) | Design + Phase 0 pieces merged; detector shadow; self-train dormant | fits the Orin by measured timetable | in-repo | **Continue**, with the trims below |

Not found / not opened: I did not locate a primary source for TextGrad this session, did not open the DSPy repo, OpenHands repo, Letta repo or the SWE-agent code, and did not read any paper in full. Treat "(search)" rows accordingly.

---

## 2. Analysis per theme

### 2.1 Persistent and long-running autonomous agents

**Field.** The 2026 consumer-agent pattern is a harness around a stateless model with: a durable task object, scheduled wake-ups, memory the agent curates, and skills it can write. Hermes Agent (row 17) and the Letta sleep-time agents (row 16) are the open examples; both are [Experimental] with thin production evidence. Anthropic's lead-plus-workers research system (row 19) is the [Proven] example of persistence-by-orchestration, with the honest ~15x token bill.

**What actually fails in long-running agents** (from the benchmark literature, not Zoe-specific): compounding error across steps (hence tau-bench's pass^k collapse: strong models fall well below 25% at pass^8 by one summary), context collapse when memory is repeatedly rewritten (ACE's named failure), and unrecorded side effects (AppWorld's collateral-damage tests).

**Zoe today (zoe).** Persistence is split across: `commitments.py` promise tracker, the night window (dreaming, night mind, day review), the 2026-10-12 action-agent plan's "errand ledger" (designed, Phase 1 not built), and Flue/Multica boards for engineering. There is no household-level durable *goal* object yet; errands are the proposed one.

**Verdict.** Build the errand ledger first (already the plan). Persistent autonomy beyond that - agents that self-schedule - is unwanted: the owner's rule is no unprompted speech and no questions, so Zoe's autonomy should be *overnight and silent*, surfacing only as "what changed?".

### 2.2 Goal-directed and hierarchical planning

**Field.** Planner-executor splits, tree search over actions (e.g. LATS-class methods), and "plan then act" prompts show gains mainly on long-horizon web/code tasks. I did not source any 2026 result showing planning beats plain tool-calling on short household requests; the household request distribution (timers, lists, calendar, music, home control, recall) is dominated by 1-2 tool calls. [Speculative] that hierarchical planning pays off below ~4 dependent steps.

**Zoe today.** Intent dispatch (`intent_router.py`, 6.1k lines of pattern-first routing) and the two-stage router (MLP shortlist + FunctionGemma-270M under a GBNF grammar; 90.1% on a frozen 81-case corpus, 424 ms p50, brain as fallback - from the module docstring) decide in under a second; the Gemma brain handles the rest with 22 tools and progressive disclosure. That is a *flat* planner by design, and a measured one.

**Verdict.** Do not add a planner to the voice path. Add planning only inside the errand lane (multi-day, multi-step), executed by a stronger model through the broker, with each step a recorded receipt.

### 2.3 Multi-agent orchestration

**Field.** Orchestrator-worker is [Proven] where the task is breadth-first and parallelisable; the same literature shows cost and coordination overhead, and a single well-prompted agent often wins on tightly coupled work. For a household voice turn, latency and RAM forbid it.

**Zoe today.** Multi-agent exists where it earns its keep: Omnigent hosts polly + codex + pi workers for cross-vendor review and the heavy Flue lane (docs/knowledge/omnigent-cross-review.md, with automatic fallback and exit-2 = "not reviewed"). That is a good, narrow use.

**Verdict.** Keep multi-agent on the dev/build side only. For the household, "orchestration" = router + brain + night jobs, which is simpler and measured.

### 2.4 Tool discovery

**Field.** Two moves: retrieve-then-expose (RAG-MCP, row 18) and model-chosen disclosure (activate-by-name tools). Both address the same measured problem: many tool schemas bloat the prompt and degrade selection, worse for small models.

**Zoe today (zoe).** Both are built: `tool-groups.ts` implements an always-on core (get_time, recall_memory, activate_abilities) plus keyword-matched, session-sticky groups, filtered at the model-wire so the 4B only sees active schemas; and the FunctionGemma router takes the shortlist first. This is equal to the field, arguably ahead on small-model fit.

**Gap.** Tool discovery of *external* tools (MCP servers the household did not ship) is deliberately absent. Given the MCP-poisoning attacks noted in the 10-04 research, that is correct; discovery should be a human-or-builder-approved *catalogue*, not live crawling.

### 2.5 Tool creation (the "missing capability" problem)

**Field.** LATM (row 15) and Voyager (row 10) show the maker/user split and the verified-skill-library pattern; Hermes (row 17) productises skill-writing after multi-step tasks. Evidence that this works *safely and reliably for non-programmer households* is [Speculative]: all published results are on coding/game/web benchmarks with a developer watching.

**What a safe Zoe-side pipeline looks like** (builds on docs/research/self-building-skills-2026-10-04.md and zoe-action-agent-2026-10.md):

1. **Recognise the gap (deterministic).** Cluster CANT_DO + FALLTHROUGH + repeated-ask events (struggle detector) by embedding; a cluster over a threshold in N days with a fixed "no matching tool" signature becomes a *capability-gap proposal* in the one proposal queue. No model needed to detect; a model only words the title.
2. **Classify the fix.** (a) config/data (add a list, a schedule), (b) *recipe* (declarative: fetch URL/API -> extract -> render card -> schedule), (c) integration needing credentials, (d) code. Only (a) and (b) are auto-buildable; (c) goes to the vault/QR-on-panel flow; (d) goes to the maintainer PR path.
3. **Build in a sandbox by the builder tier** (Omnigent with the household's own key, synthetic context only, no real member ids - already the design's Tier 2 contract). Output is a recipe file + its tests, not live code.
4. **Prove.** Unit tests with recorded fixtures, a replay against the router corpus to prove no existing route regresses (the two-stage router must not steal turns), the control arm (a no-op recipe must not "improve" the score), skillspector scan (AGENTS.md rule), egress allowlist recorded.
5. **Permission tier.** Read-only public-data recipes may enable after the gate; anything that writes, spends or contacts a person is behind the unified approval object (errand plan Phase 2).
6. **Rollback.** Recipes are files in an overlay; disable = remove from the catalogue; ledger records who/what/why. "Undo that" works.
7. **Credit cap.** A per-night and per-recipe token ceiling recorded in the ledger; exhaustion = defer, never fall back to a different provider.
8. **Human approval.** The owner's rule is "no questions to the household". Resolve it this way: *setup* consent once covers the recipe class; the household is told after the fact ("what changed?"); anything outside the class is not built, only listed on the owner's face.

**Zoe today.** All of 1-8 exist as design pieces; step 1's clustering and step 4's regression replay are not built; the SKILL.md runtime loader was deleted (#1471), so nothing can currently *load* a created skill. That loader decision is the blocker for any creation story.

### 2.6 Agent evaluation frameworks

**Field.** Three durable ideas: state-based end-condition checks (tau-bench, AppWorld) instead of transcript judging; collateral-damage detection (AppWorld); and **pass^k** reliability instead of pass@1 (tau-bench). Caveats: scores are not comparable across harnesses, and a stochastic simulated user contaminates variance.

**Zoe today (zoe).** The bar (23 cells), day-sim (16 asks), person bench (12 cells) and voice regression exist with Wilson-bound margins and pre-registered controls. Round 2 found them partly dishonest (voice gate scores "did not refuse"; bar baseline covers 8 of 23; nightly test workflow fail-open) and Phase 0 fixes those. Judge is the same Gemma family that answers.

**Behind:** no repeated-trial/pass^k view of the same cell, no collateral-damage assertions on write tools (did setting a timer alter anything else?), judge not separated from the answerer except by the planned design.
**Ahead:** zero-for-unmeasured scoring and "floors cap the total" are stricter than any public benchmark leaderboard; the replay corpus is real speech, which no public benchmark has.

### 2.7 Self-improving software agents

Summary table of what each family changes and what it needs:

| Family | Changes | Needs | Failure mode seen |
|---|---|---|---|
| Harness/prompt optimisation (Harness2, GEPA, ACE, DSPy) | text files around a frozen model | evaluator + strong reflection model | weak reflection model = no gain (GEPA production report, search); metric overfit |
| Skill/workflow libraries (Voyager, AWM, Hermes) | memory of verified procedures | success signal per trajectory | stale or poisoned skills; retrieval collisions |
| Evolutionary code search (AlphaEvolve, OpenEvolve, Shinka) | one function/program | numeric fitness | optimises the metric, not the intent |
| Self-modifying agents (DGM, SICA, ADAS, Live-SWE-agent) | the agent's own code | benchmark farm, sandbox | objective hacking; hallucinated test results; cost (DGM: weeks, five-figure dollars, one secondary source) |

**Lesson for Zoe.** Move *down* the table. The first three families change data a human can read in a diff and revert by re-pointing a symlink. The fourth changes executable behaviour and needs infrastructure Zoe will not have (a benchmark farm); the field's own safety discussion (DGM sandboxing, traceability, objective hacking) is the argument against it for a household box.

### 2.8 Controlled self-modification safeguards (what the field does vs what Zoe designed)

| Safeguard | Field practice | Zoe design (self-evolution-nightly-loop.md) |
|---|---|---|
| Sandbox | Docker per run (DGM, SICA, OpenHands) | scratch DB + harness traffic tag + synthetic context + guard refusing real member ids |
| Change surface | whole codebase (DGM/SICA) or one function (Evolve) | overlay directory only; loader refuses unknown keys; restraint/memory authority/forget/guest/identity out of scope by construction |
| Acceptance | benchmark delta | held-out set + targeted margin + two control arms (sycophancy editor, sampling at equal cost) + voice regression + live canary |
| Rollback | archive of agents | symlink re-point + sidecar restart (~10 s) + auto-revert on canary drift |
| Human approval | oversight "proactive and reactive" (DGM, third-party summary) | consent once at setup, then ledger + "undo that" |
| Cost cap | rarely stated | per-night cap recorded in ledger (open decision 1) |
| Rate limit | n/a | one change in canary per component |

Zoe's table is the stronger one on rollback, change-surface and control arms. **Weak spots** worth stating: (1) the judge is the answerer's model family; (2) the sampling control only catches noise, not metric gaming; (3) "no questions" removes the one human check that DGM-class papers lean on - mitigated only by the narrow surface and canary.

### 2.9 Where Zoe's existing design is ahead of, equal to, or behind the field

- **Ahead.** Honest-instrument discipline (unmeasured = 0; floors cap the score; fail-closed baselines), a fixed change surface with forbidden zones, voice-replay as an apply-time canary against real speech, retire-by-removal policy against harness sprawl, cross-vendor review that never substitutes same-vendor, maker/user split already realised (Omnigent builds, Orin runs).
- **Equal.** Propose-Probe-Compose (Harness2), sleep-time compute (night window), tool disclosure/retrieval (tool-groups + two-stage router), skill format (SKILL.md/agentskills.io), lesson libraries (ACE-like playbooks).
- **Behind.** Nothing runs yet: no nightly job, no Zoe score, no proposal queue consumers (716 pending proposals, 2 approved; the completeness doc says "Nothing" runs for the Harness2 loop); no pass^k; no capability-gap clustering; no skill loader; the struggle detector is shadow-only with one run and zero labelled failures; the router self-train is dormant since 2026-07-26 with a REJECT verdict. The field has several running (if research-grade) loops; Zoe has a better-designed but un-run one.

### 2.10 Which approaches beat plain tool-calling for a *household* companion?

Honest answer: **for the request mix of a household, very few published agent techniques have been shown to beat a good tool-calling setup with a router, and none of the evidence is from household tasks.** What I would bet on, ordered by how directly the evidence maps:

1. **Per-turn memory/lesson injection** (ACE playbooks, AWM routines, Reflexion lessons): measured gains in tool-use benchmarks (+10.6%, +24.6% relative, etc., self-reported) and it is what Zoe's own completeness doc identifies as her biggest naturalness gap (recall almost never fires). [Experimental, strongest mapping]
2. **Tool retrieval / disclosure** for small models (RAG-MCP): large selection-accuracy gains with many tools; Zoe has it. [Experimental, already adopted]
3. **Sleep-time pre-computation**: a measured ~5x test-time saving on predictable queries (Letta/Berkeley, search level). Household questions are highly predictable from context (calendar, routines). [Experimental]
4. **Reliability engineering via pass^k and state checks**: not a capability gain, but the only way to know whether any change is real. [Proven as a method]
5. **Orchestrator-workers**: gains on breadth-first research (row 19), irrelevant to a 6 s voice turn. [Proven but off-target]
6. **Code-level self-modification**: [Experimental], big headline numbers on coding benchmarks, no household analogue, highest risk.

---

## 3. Proposed experiments

All ship flag-dark, lab-first, on the existing instruments, under the single test lane lock, in the night slot or the dev box, never during waking hours (project rules). Effort is engineer-days for one person plus agents.

### E1 - GEPA-style reflective improver on one overlay file (the smallest useful experiment)

- **Hypothesis.** A strong reflection model reading the traces of the failing cells in one domain can propose an overlay edit that raises the targeted cells by the pre-registered margin and beats repeated sampling at equal cost, without turning any held-out or sanity cell red.
- **Implementation.** Depends on the overlay seam (#2153, merged) and a scratch DB. Build `labs/harness2-spike` (already Phase 1 in the design): target = one file (start with `manner.md`, not tool guidance - lowest voice-path risk). Use the `gepa` library's optimize loop as the proposer (seed = current overlay text; metric = a thin adapter over the existing bar-cell scorer returning score + textual feedback; reflection LM = Omnigent with the owner's dev credits). Candidates probed on the 4B using the existing bar subset runner. Shadow apply only (record what would be applied).
- **Resources.** ~4 min per probe run on the 4B (measured: bar 240 s); K=2 candidates + 1 reference + 1 sampling-control arm = ~16-20 min per night on the 4B in the 04:00-04:25 slot; reflection calls cloud, small (a few thousand tokens per candidate; set the cap explicitly). No extra RAM beyond what bar runs already use. GEPA itself is a pure-Python library.
- **Evaluation.** Per night: targeted-cell delta, held-out delta, sanity set, control arms, wall time, tokens. Over 14 nights: win rate vs sampling control; judge-gaming check = rerun winners with a different judge prompt not visible to the improver.
- **Success.** >= 3 of 14 nights produce a candidate that passes all gate rules and beats the sampling control with a Wilson lower bound above zero, with no control arm turning green. **Failure/stop.** No better than sampling at equal cost over 14 nights (the design's own stop rule), or any control arm goes green, or the sycophancy editor wins. **Effort.** 8-12 days after Phase 0 honest-instrument fixes; do not start before them.

### E2 - Add pass^k and collateral-damage checks to the bar and day-sim (instrument upgrade, prerequisite for E1's credibility)

- **Hypothesis.** Running each household-realistic cell k=5 times and scoring pass^k exposes brittle "passes" the single-shot bar hides, and write-tool cells that currently pass while altering unrelated state.
- **Implementation.** Extend `scripts/perf/samantha_bar.py` and `samantha_day_sim.py` with a `--trials k` option and a pass^k column; add state-diff assertions on a scratch DB for write tools (timer, list, reminder, calendar) in the spirit of AppWorld's collateral-damage tests. Pure measurement; changes no product behaviour.
- **Resources.** k=5 multiplies bar time (240 s -> ~20 min); run weekly rather than nightly; scratch DB; 4B.
- **Evaluation.** Compare pass@1 vs pass^5 per cell; list cells where they diverge; list state-diff violations.
- **Success.** At least a handful of cells separate (informative metric), and the gap predicts live struggle events for that domain (correlation over a month). **Failure.** pass^5 equals pass@1 everywhere (decorative; drop to keep the instruments lean). **Effort.** 3-4 days.

### E3 - Capability-gap clustering and a ranked "what Zoe could not do" backlog (no model writes anything)

- **Hypothesis.** Clustering CANT_DO, FALLTHROUGH and repeated-ask events (already captured in shadow by the struggle detector) over two weeks yields at least three stable clusters that map to a missing tool, and the owner's review confirms most as real gaps.
- **Implementation.** A nightly deterministic job over `struggle_events`: embed the utterances (the router's existing bge-small embedding), cluster, drop clusters matching an existing tool's shortlist (those are routing failures, not capability gaps), emit proposals with fix class (route / recipe / integration / code) to the proposal queue. No generation, no 12B, no builder.
- **Resources.** CPU seconds; data already exists; needs the detector flipped from shadow to enforce on proposals first (the completeness doc's recommended cheapest step).
- **Evaluation.** Precision of clusters labelled by the owner on a weekly page (or by reading raw cluster members); fraction of "routing failures" vs "true gaps"; stability week to week.
- **Success.** >= 60% of emitted clusters judged real and distinct. **Failure.** Most clusters are routing failures (then feed the router retrain path, which is a smaller win) or the data is too thin (<30 events/week; then the household simply is not hitting gaps - good news). **Effort.** 3-5 days.

### E4 - ACE-style delta playbook as the lesson library, injected per turn (the highest user-felt upside)

- **Hypothesis.** A small per-domain playbook (bullets with helpful/harmful counters, merged by deterministic delta rules, size-capped) injected into the existing recall packet raises targeted day-sim cells and reduces re-asks, without hurting first-token latency beyond a pre-registered budget.
- **Implementation.** Data structure and merge rules from ACE (Generator/Reflector/Curator reduced to: struggle event + success pair -> candidate bullet -> counter update). Store as OKF files under `docs/knowledge/harness-lessons/<domain>/` (the design's location); load via the overlay's `recall-shape.json`. The reflector runs in the night window on the 12B (a rewording job; code owns the counts, as the struggle digest does today).
- **Resources.** Prompt-token budget ~200-400 per turn (must be measured against the 8k context); night 12B minutes small.
- **Evaluation.** Day-sim cell deltas vs reference; first-token p50/p90 delta; replay gate on voice corpus; control: same tokens of random-but-plausible bullets (a placebo playbook).
- **Success.** Targeted delta beats placebo; latency within budget; zero previously-passing cells red. **Failure.** Placebo matches (the gain is just "more context"); latency over budget. **Effort.** 6-8 days; voice-path touch, so replay-gated per AGENTS.md.

### E5 - Sleep-time answer pre-computation for predictable asks (night 12B)

- **Hypothesis.** Pre-computing answers (or recall-ready context) for the day's likely questions during the night window cuts first-token time and brain calls for a measurable share of next-day turns.
- **Implementation.** In the night window after dreaming: derive a candidate list of "likely asks tomorrow" from deterministic sources (calendar entries, timers/routines, recurring list questions) and cache their grounded answer context in the recall cache, invalidated on source change. No change to the live path except a cache read (flag-dark).
- **Resources.** Minutes of 12B time; small cache; fits the window's existing budget.
- **Evaluation.** Hit rate on the next day's real turns (replay), latency saving, staleness errors (must be zero; cache keys include source versions).
- **Success.** >= 10% of turns hit and none stale. **Failure.** Hit rate < 3% (household asks are too unpredictable) - drop. **Effort.** 5-7 days.

### E6 (later) - Recipe-builder pilot via Omnigent, one read-only public-data recipe

- **Hypothesis.** The builder tier can turn an E3 cluster that is a "read-only public data" gap (e.g. a periodic public feed shown as a card) into a tested declarative recipe that passes the gate with no human editing.
- **Implementation.** Recipe schema + loader (the missing piece), builder skill under the Omnigent contract in section 8 of the design, skillspector scan, router-corpus regression, egress allowlist.
- **Resources.** Owner's dev credits for the build; zero Orin cost to run the finished recipe.
- **Evaluation.** Recipe tests green; router corpus unchanged; the control arm (an empty recipe) does not score; owner reviews the diff once.
- **Success.** One recipe lands and survives a 2-week canary with zero rollbacks. **Failure.** Builder needs human edits more than half the time (then the whole class is a maintainer job). **Effort.** 10-15 days incl. loader; do after E1/E3.

**Explicitly not recommended:** DGM/SICA/ADAS-style self-modifying code on any Zoe runtime path; Self-Refine on the 4B; a multi-agent planner on the voice path; adopting a general agent framework.

**Sequencing.** Phase 0 instrument honesty (already designed) -> E2 -> E3 (can run in parallel) -> E1 -> E4 -> E5 -> E6.

---

## 4. Risks and what is speculative

**Risks.**
- **Evaluator gaming.** DGM's paper itself documents agents optimising the measurable proxy instead of the intent (search summary of the hallucination case study). Zoe's judge shares a model family with the brain. Mitigation: judge separation, held-out set, sycophancy and sampling controls; stop auto-apply the day a control arm stays green.
- **Weak improver.** A production GEPA report (Decagon, search level) found a small reflection model produced no gain and stronger ones ~5-6%. The Orin's 12B at 2-7 tok/s may be too weak to propose usefully; the design already measures this in Phase 3 and routes to Tier 2 or a Spark if so. Expect small wins.
- **Statistical thinness.** Household traffic is low and the instruments are synthetic; a nightly loop makes many comparisons. Pre-register margins and cap changes in canary; count multiple-comparison risk.
- **Privacy.** The improver's inputs must stay synthetic; any lesson or trace is a store subject to the forget cascade. A single leak of a real member id into a cloud reflection call defeats the "stays local" promise; the guard must run before the skill sees any payload.
- **Cost.** Cloud reflection calls scale with candidates x nights. Cap per night; exhaustion defers, never switches provider.
- **Supply chain / third-party code.** Anything adopted from the table is third-party code with the agent's privileges: skillspector scan, 14-day age rule (waived in dev per owner but note it), pin versions with `opensrc path` before reading. Several repos here are young (ACE: 12 commits; Harness2: 107 open PRs).
- **Harness sprawl.** Round 1 counted eight prior harness generations. The loop must delete what it replaces (716 stale proposals, the dormant router self-train, the `evolution_proposals` writers) or it is a ninth.
- **Voice-path contention.** Probes share the 4B with the live brain; only inside the 02:xx-06:00 slot and under `night_busy.sh`; never during waking hours (2026-10-20 incident rule).
- **Night reliability.** 2 of the last 4 night windows aborted on memory (completeness doc); the loop's Tier 1 proposer inherits that fragility; E1 should use the cloud proposer so it does not depend on the 12B loading.

**Speculative or unverified in this report.**
- All benchmark numbers are authors' self-reports; for most papers I saw only abstract-level summaries from search results, not the paper. The ~$22k DGM cost, the Hermes "every 15 tasks" cadence, the OpenHands licence and the DSPy licence are unverified.
- That GEPA's gains transfer from QA/benchmark tasks to Zoe's manner/doctrine text is [Speculative] until E1 runs.
- That sleep-time pre-computation hits a worthwhile fraction of household turns is [Speculative]; E5 exists to find out.
- That hierarchical planning offers nothing on short household requests is my inference from the request mix, not a cited result.
- Whether a Spark or any larger local improver changes the Tier 1 picture is unmeasured.

**Owner decisions this report depends on (already open in the design):** nightly credit cap; whether tool guidance is in first scope (this report suggests no: start with manner); canary bands; whether lessons leave the box (opt-in). One new one: whether to restore a skill/recipe loader at all (deleted in #1471) - E6 is meaningless without it.

---

## 5. Appendix

### 5.1 Worked example: one missing capability, end to end (synthetic)

A synthetic household asks, three times in a week, for something no tool covers (a periodic public-data readout). What the pipeline would do, with who acts at each step:

| Step | Actor | Artefact | Gate |
|---|---|---|---|
| 1. Events | struggle detector (CANT_DO, repeated ask) | `struggle_events` rows | detector enforce mode (today: shadow) |
| 2. Cluster | deterministic nightly job (E3) | proposal row, class `recipe`, evidence ids | not a routing failure (no tool shortlist match) |
| 3. Scope check | code | risk tier: read-only public data = auto-buildable | forbidden-zone list; no credentials, no writes, no contact with people |
| 4. Build | Omnigent builder with the household's capped key | recipe file + fixtures + tests, in a scratch overlay | synthetic context only; guard refuses real member ids; skillspector scan |
| 5. Prove | zoe-data gate | router-corpus replay unchanged; recipe tests green; empty-recipe control scores zero | no previously-passing cell turns red |
| 6. Apply | gate | recipe enabled in the catalogue (overlay), canary 3 nights | canary reads empty-transcript, re-ask and struggle rates |
| 7. Tell | ledger | "what changed?" sentence, plain words | no version strings on the household face |
| 8. Undo | household by voice / owner on the page | recipe disabled; ledger line | one step |

Where it breaks today: step 1 (detector in shadow), step 2 (not built), step 4 (needs the recipe schema and a loader), step 6 (canary not built). Steps 5 and 7 are mostly designed and partly built (overlay seam, ledger design, gate pattern from #2128).

### 5.2 Zoe components referenced (all read in this worktree)

- `docs/architecture/self-evolution-nightly-loop.md` - the Harness2-shaped design: spine, overlay, timetable (probes 04:00-04:25, voice regression 04:30), gate, tiers, Zoe score, Omnigent skill contract, phases 0-4, open decisions.
- `docs/architecture/struggle-detector.md` - nine signals (RE_ASK, REPHRASE, CORRECTION, NEGATIVE_FEEDBACK, CANT_DO, FALLTHROUGH, VERBOSE, MISHEAR, ABANDON), shadow/enforce flags, phase 2 proposals, phase 3 router feed (flag-dark).
- `docs/research/completeness-and-issues-2026-11-02.md` section 2 - honest state: nothing of the Harness2 loop runs yet; detector in shadow with zero labelled failures; router self-train dormant; night window failed on memory 2 of the last 4 nights.
- `docs/research/self-building-skills-2026-10-04.md`, `docs/research/zoe-action-agent-2026-10.md` - skill format convergence (SKILL.md / agentskills.io), the seams that are not connected, the errand-ledger plan, the approvals and receipts gaps.
- `docs/knowledge/omnigent-cross-review.md` - cross-vendor, flat-rate review with fail-closed exit 2; the existing multi-agent use.
- `labs/flue-zoe-brain-2x/src/tools/tool-groups.ts` and `zoe-tools.ts` - progressive disclosure at the wire; always-on core of three tools; the brain's tool set (about two dozen tools).
- `services/zoe-data/router_two_stage.py` - MLP shortlist plus FunctionGemma-270M under GBNF; 90.1% on the frozen 81-case corpus, 424 ms p50 (module docstring figures); brain is the fallback on every abstain or failure.
- `services/zoe-data/intent_router.py` - pattern-first intent dispatch, "bypass the LLM for sub-second responses"; note its docstring still mentions Hermes/OpenClaw fall-through (retired) - a stale-text deletion target.

### 5.3 Sources (links opened or surfaced this session)

Opened (repo page fetched): [google-research/harness2](https://github.com/google-research/harness2), [jennyzzt/dgm](https://github.com/jennyzzt/dgm), [gepa-ai/gepa](https://github.com/gepa-ai/gepa), [algorithmicsuperintelligence/openevolve](https://github.com/algorithmicsuperintelligence/openevolve), [MaximeRobeyns/self_improving_coding_agent](https://github.com/MaximeRobeyns/self_improving_coding_agent), [ace-agent/ace](https://github.com/ace-agent/ace).

Surfaced by search only (abstract or summary level; not read in full): [DGM, Sakana](https://sakana.ai/dgm/); [DGM arXiv 2505.22954](https://arxiv.org/abs/2505.22954); [SICA arXiv 2504.15228](https://arxiv.org/abs/2504.15228); [ADAS arXiv 2408.08435](https://arxiv.org/abs/2408.08435); [GEPA arXiv 2507.19457](https://arxiv.org/abs/2507.19457); [ACE arXiv 2510.04618](https://arxiv.org/abs/2510.04618); [Harness2 arXiv 2610.14224](https://hyper.ai/en/papers/2610.14224); [Agent Workflow Memory](https://proceedings.mlr.press/v267/wang25bx.html); [Reflexion arXiv 2303.11366](https://arxiv.org/abs/2303.11366); [Voyager arXiv 2305.16291](https://arxiv.org/abs/2305.16291); [LATM arXiv 2305.17126](https://arxiv.org/abs/2305.17126); [Sleep-time compute arXiv 2504.13171](https://arxiv.org/abs/2504.13171) and [Letta blog](https://letta.com/blog/sleep-time-compute); [Live-SWE-agent arXiv 2511.13646](https://arxiv.org/abs/2511.13646); [RAG-MCP arXiv 2505.03275](https://arxiv.org/abs/2505.03275); [tau-bench arXiv 2406.12045](https://arxiv.org/abs/2406.12045); [AppWorld arXiv 2407.18901](https://arxiv.org/abs/2407.18901); [ShinkaEvolve arXiv 2509.19349](https://arxiv.org/abs/2509.19349); [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system); [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs); [OpenHands / mini-swe-agent overview via search](https://codesota.com/agentic/openhands-vs-swe-agent).

Not found or not opened: TextGrad (no usable result), DSPy repo, OpenHands repo, SWE-agent repo, Letta repo. These rows in the table carry that caveat.

### 5.4 Verdict roll-up

- **Adopt (principle, already in the design):** sleep-time compute, tool retrieval/disclosure, state-based evaluation, pass^k as a metric.
- **Adapt (pattern, own code):** Harness2 propose-probe-compose, ACE delta playbooks, AWM routine induction, Voyager verified-skill library, LATM maker/user split, Reflexion where an external signal exists.
- **Experiment (bounded, lab-first):** GEPA via the `gepa` library (E1), OpenEvolve on deterministic hot-path code with a real benchmark, DSPy only as an optimiser library.
- **Reject for Zoe's runtime:** DGM, SICA, ADAS, Live-SWE-agent (self-modifying code), Self-Refine on the 4B, TextGrad (superseded, unsourced here), any multi-agent planner on the voice path, resurrecting Hermes.
- **Reference only:** OpenHands / mini-SWE-agent / SWE-agent as builder-lane tools on Omnigent, never on the Orin.

None of the experiments changes a rock (Gemma 4 E4B+MTP, Moonshine, Kokoro); all act on the harness around them.
