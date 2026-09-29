# Samantha-grade "knowing you" on a 16 GB Jetson: context manager vs training

Research date: 2026-09-29. Web sources are cited inline and listed in §6. Items marked **[unverified]** are my inference or could not be confirmed from a primary source.

---

## 1. Direct answer

**Yes, build a context manager. It is the thing that makes an assistant seem to know you. Training is not.** Every production system that feels like it knows its user does this through context assembly, not weight updates. That includes ChatGPT memory, Claude's memory tool, Gemini Personal Intelligence, Letta, Mem0, Zep and Honcho. Each keeps a small, always-present user profile and adds retrieved episodes to it. A background "sleep-time" or "dreaming" process rewrites that profile while the user is away. The evidence on training facts into weights is consistently negative:

- RAG beats unsupervised fine-tuning for new facts: 0.875 vs 0.504 accuracy for Mistral-7B on current events ([Ovadia et al., EMNLP 2024](https://arxiv.org/abs/2312.05934)).
- Fine-tuning on new facts linearly increases hallucination ([Gekhman et al., EMNLP 2024](https://arxiv.org/abs/2405.05904)).
- LoRA on new facts cost 71% of NaturalQuestions F1 ([Lin et al., Meta, Oct 2025](https://arxiv.org/abs/2510.15103)).
- For personalization specifically, RAG gave +14.92% and PEFT gave +1.07% over a non-personalized model; the two combined gave +15.98% ([Salemi & Zamani, ICTIR 2025](https://arxiv.org/abs/2409.09510)).

The RAM problem therefore does not block Samantha-like behaviour, because the weights are the wrong place for "what I know about Jason" anyway. Weights cannot be corrected, deleted or dated. The workable route is:

1. **A context manager on the Jetson.** It keeps a stable, cache-friendly prefix holding a compact user model, rebuilt nightly by the existing dreaming pass.
2. **Small side models trained off-box or on-box**, in the same pattern as the router head. For example: a salience/reranker head, and a "should I bring this up" gate.
3. **Optionally, later, a style-only LoRA** trained off-box and loaded with llama.cpp `--lora`. It can shape how Zoe talks. It cannot reliably carry facts.

---

## 2. How the leading projects do it

| Project | Representation | Write path | Read path | Proactive / "knows you" mechanism | Local? |
|---|---|---|---|---|---|
| **Letta / MemGPT** | Labelled **memory blocks** (label, value, size limit, description), with "human" and "persona" as the defaults. Archival store. Now "MemFS", a git-backed memory filesystem ([blocks, May 2025](https://www.letta.com/blog/memory-blocks/); [memory & dreaming docs](https://docs.letta.com/letta-agent/memory)) | The agent edits blocks through tools. A **sleep-time agent** shares the blocks and rewrites them asynchronously, by default every 5 steps (`sleeptime_agent_frequency`) ([docs](https://docs.letta.com/guides/agents/architectures/sleeptime/)). "Dreaming" runs after N steps or on compaction | **Blocks are always in context.** The archival store is searched through a tool | Sleep-time compute turns "raw context" into "learned context". The paper reports **5× less test-time compute** for equal accuracy and **up to 18%** accuracy gain on Stateful AIME. The gain correlates with how predictable the query is ([arXiv 2504.13171](https://arxiv.org/abs/2504.13171)) | Yes (OSS server; any model) |
| **Mem0** | Atomic facts in a vector store. The 2026 version does entity linking inside the ranker instead of using an external graph DB ([State of Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)) | Extraction on every turn, then update/consolidation against existing memories ([arXiv 2504.19413](https://arxiv.org/abs/2504.19413)). The April 2026 version extracts in a single pass and treats agent-stated facts as first-class | Retrieval that combines semantic, keyword and entity scoring | Retrieval only; there is no proactive layer. Vendor-reported LoCoMo is 92.5 at ~6.9k tokens per query **[vendor claim]**. In the paper, graph memory added only ~2% over base Mem0 | Yes (OSS) |
| **Zep / Graphiti** | **Temporal knowledge graph** with episodes, entities and communities. Facts carry validity intervals ([arXiv 2501.13956](https://arxiv.org/abs/2501.13956)) | Ingestion builds and invalidates edges as facts change | Graph plus hybrid search, assembled into a context string | None built in. Reports up to +18.5% on LongMemEval and 90% lower latency vs full context | Graphiti is OSS; it needs a graph DB and an LLM |
| **Honcho (Plastic Labs)** | **Peer representations**: conclusions, summaries and a **peer card** holding biographical essentials ([reasoning docs](https://honcho.dev/docs/v3/documentation/core-concepts/reasoning)) | The **Deriver** extracts explicit and deductive conclusions from each message. The **Dreamer** runs asynchronously to consolidate, induce and abduce | **Dialectic API**: you ask Honcho in natural language ("what does this user need?"). `get_context` is budgeted by tokens ([docs](https://docs.honcho.to/)) | Theory-of-mind inference. Its extractor, Neuromancer XR, is a **fine-tuned Qwen3-8B** that scores 86.9% on LoCoMo, against 69.6% for base Qwen3-8B and 80.0% for Claude 4 Sonnet ([Aug 2025](https://plasticlabs.ai/blog/research/Introducing-Neuromancer-XR)) | Self-hostable. The extractor model is not clearly open **[unverified]** |
| **ChatGPT memory** | Six injected sections: saved memories ("Model Set Context"), inferred **response preferences**, topic highlights, **user insights**, ~40 recent chat summaries, and interaction metadata ([reverse-engineered, May 2025](https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/)) | Saved memories come from explicit requests or model-initiated saves. The profile sections are rebuilt out of band on a cadence | **Mostly always-injected**, not retrieved per turn (per the reverse-engineering above). "Reference chat history" launched 10 Apr 2025 ([OpenAI](https://openai.com/index/memory-and-new-controls-for-chatgpt/)) | Precomputed profile summaries. Mem0 reports OpenAI Memory at 52.9 on LoCoMo **[vendor claim]** | No |
| **Claude memory tool + context editing** | Files in a `/memories` directory that the model reads and writes ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)) | The model writes memory itself through tool calls | **Memory as a tool** (just-in-time). Context editing clears stale tool results | None. Anthropic reports +39% (memory plus editing) and +29% (editing alone) on agentic search, and 84% fewer tokens over 100 turns ([29 Sep 2025](https://claude.com/blog/context-management)) | No |
| **Gemini Personal Intelligence** | Past chats plus connected Gmail, Photos and YouTube ([Jan 2026 overview](https://gemini.google/overview/personal-intelligence/)) | Implicit | The model decides per query whether past chats or apps would help ([help](https://support.google.com/gemini/answer/16598469)) | "Anticipates needs" across the connected apps | No |
| **Character.AI** | Persona field (~150 words free), up to 15 **pinned memories** that never slide out, a 400-character Chat Memories box, and auto-extracted "Facts" on the paid tier ([C.AI blog](https://blog.character.ai/helping-characters-remember-what-matters-most/); third-party summary [TukiAI](https://tukiai.com/character-ai-memory-explained/)) **[numbers from secondary sources]** | User pins plus automatic fact extraction | Pinned and persona memory always in context | None | No |
| **Generative Agents (Park 2023)** | An episodic memory stream plus reflections | Every observation is scored for importance (1–10). A **reflection** runs when summed importance exceeds ~150 ([summary](https://agentpatterns.ai/agent-design/generative-agents-memory-stream/)) | Retrieval score = recency (exponential decay) + importance + relevance | Plans and reflections drive behaviour | Research code |
| **MemoryBank / SiliconFriend** | Memories plus a user personality summary | An Ebbinghaus forgetting curve: recall reinforces a memory, time decays it ([arXiv 2305.10250](https://arxiv.org/abs/2305.10250)) | Dense retrieval | A persona portrait drives empathy | Research code |
| **A-MEM** | Zettelkasten notes with keywords, tags and links ([NeurIPS 2025](https://github.com/WujiangXu/A-mem)) | A new note triggers linking and **evolution** of older notes | Retrieval over the linked graph | None | Research code |
| **HippoRAG 2** | Knowledge graph plus Personalized PageRank ("non-parametric continual learning", ICML 2025) | Offline indexing | Associative retrieval over the graph | None | Research code |
| **Khoj** | Documents and notes plus a "memories" feature ([GitHub](https://github.com/khoj-ai/khoj)) | **[unverified detail]** | RAG over the user's corpus | Scheduled automations | Yes (self-host) |

**Four patterns recur across these systems:**

1. **A small always-present profile, plus retrieval for everything else.** Letta has blocks, ChatGPT has insights and preferences, Honcho has the peer card, Character.AI has persona and pins. Nobody relies on retrieval alone for identity-level facts.
2. **Writing is split into a cheap per-turn extraction and a slow background consolidation.** Letta sleep-time, Honcho's Dreamer and ChatGPT's out-of-band profile rebuilds all follow this split.
3. **Facts must carry time and validity.** Zep uses validity intervals and Mem0 has its update step. The 2026 STALE benchmark shows the best model reaching only **55.2%** at detecting memories invalidated implicitly ([arXiv 2605.06527](https://arxiv.org/abs/2605.06527)).
4. **Keep the evidence.** Extracting facts alone loses information. Verbatim chunks beat extracted artifacts by **15.9 points on LoCoMo** (43.9% vs 28.0%) and **22.0 points on LongMemEval-S** inside the same retrieval pipeline. The authors conclude structure should "augment verbatim text rather than replace it" ([arXiv 2601.00821, v4 Jul 2026](https://arxiv.org/abs/2601.00821)).

For the context-engineering guidance itself:

- **Anthropic** recommends "the smallest set of high-signal tokens", a hybrid of pre-retrieval and just-in-time retrieval, and structured note-taking ([29 Sep 2025](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **LangChain** groups context work into four moves: write, select, compress and isolate ([blog](https://www.langchain.com/blog/context-engineering-for-agents)).
- **Manus** says KV-cache hit rate is the most important production metric. Keep a stable prefix and an append-only context, and "recite" goals at the end of the context ([18 Jul 2025](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).
- **Chroma's "Context Rot"** found performance declines with input length on all 18 models tested, and distractors make it worse ([Jul 2025](https://www.trychroma.com/research/context-rot)).

For an 8k-context 4B model, this argues for injecting *less and better*, not more.

---

## 3. Training on user data vs a context manager

### What the evidence says

- **Facts belong in retrieval, not weights.** The four results in §1 (Ovadia, Gekhman, Lin et al., Salemi & Zamani) all point the same way. PEFT's benefit also grows with the amount of user data, so it does worst exactly where Zoe is: one user with limited labelled data.
- **Per-user adapters exist in research.** OPPU gives "one PEFT per user", combined with retrieval ([EMNLP 2024](https://arxiv.org/abs/2402.04401)). PLUME uses shared-subspace user modulation with 95% fewer per-user parameters ([arXiv 2609.04715, Sep 2026](https://arxiv.org/abs/2609.04715)). Both are evaluated on stylistic and personalized text-generation benchmarks (LaMP-style), not on "remember that my sister moved to Perth".
- **Apple's shipped approach** is adapters over a frozen ~3B on-device model, exposed to developers for *task/feature* fine-tuning ([Apple FM tech report 2025](https://arxiv.org/abs/2507.13575)). Per-user fact adapters are not the published pattern **[my reading of the report]**.
- **Plug-in parametric memory is a research direction, not a tool.** Memory Decoder is a small decoder that imitates a retriever; it cut perplexity by 6.17 on average in domain adaptation ([NeurIPS 2025](https://arxiv.org/abs/2508.09874)). Cartridges trains a compact KV cache offline and matches in-context learning with 38.6× less memory ([ICLR 2026](https://arxiv.org/abs/2506.06266)). But a 2026 study found precomputed memory degrades when it is composed from parts, needs frequent rebuilds, and handles corrections poorly ([arXiv 2608.30647](https://arxiv.org/abs/2608.30647)). I found no llama.cpp support for either **[unverified]**.

### Feasibility of the off-box LoRA route for Gemma 4 E4B

**Training memory.** Unsloth's current docs list **E4B QLoRA at 10 GB VRAM and E4B LoRA at 17 GB** ([Unsloth Gemma 4 train](https://unsloth.ai/docs/models/gemma-4/train)). The Jetson has ~1.5 GB free while the stack is live, so on-box training is out unless the whole voice stack is stopped. Even then, 10 GB of unified memory is tight, and bitsandbytes/Unsloth support on aarch64 Tegra is **[unverified]**. The practical option is a **LAN desktop with a ≥12–16 GB GPU**, which keeps the data in the house, or cloud training on de-identified data. A third-party guide claims ~30–60 minutes for 1,000 samples × 3 epochs on an RTX 4090 ([gemma4-ai.com](https://gemma4-ai.com/blog/gemma4-fine-tuning)) **[secondary source]**.

**Runtime.** llama.cpp supports this today:

- `--lora`, `--lora-scaled FNAME:SCALE` and `--lora-init-without-apply`.
- `GET/POST /lora-adapters` for global hot-swap.
- A **per-request `lora` field** of `[{id, scale}]` on `/completion` and `/v1/chat/completions`.

The documented caveat is that "requests with different LoRA configurations will not be batched together" ([server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md); [PR #10994](https://github.com/ggml-org/llama.cpp/pull/10994)). Adapters are converted from PEFT format with `convert_lora_to_gguf.py`, with two known limits:

- Adapters that touch embeddings or `lm_head` are rejected or ignored.
- A Gemma conversion bug exists where no `tokenizer.model` is present ([#19152](https://github.com/ggml-org/llama.cpp/issues/19152)).

Unsloth warns that a wrong chat template or EOS token at inference is the most common cause of a fine-tune looking worse.

**Zoe-specific risks** (all **[unverified; test before trusting]**):

- **Quantization mismatch.** The adapter would be trained against bf16/NF4 weights but applied to Zoe's QAT quant, so the quality delta needs measuring.
- **Speculative-decoding slowdown.** The **MTP draft head is not adapted**, so the draft/target agreement could drop and speculative decoding could slow down.
- **Prompt-cache invalidation.** Switching adapters per request would invalidate llama.cpp's prompt/KV cache, because cached KV was computed under the other adapter. A single always-on adapter avoids this.
- **Voice-path change.** Any adapter is a voice-path change, so the replay gate applies.

**What a LoRA would give:** Zoe's register, phrasing, brevity, humour, and habits such as "Jason prefers the answer first". These are the stylistic priors PEFT does move.

**What it would not give:**

- Reliable recall of facts.
- Updates or deletions ("forget that") without retraining.
- Temporal validity.
- Any improvement to the "Zoe remembered my thing" moment, which is where Samantha-likeness actually lives.

It also bakes private data into an artifact that is harder to audit than a Postgres row.

**Recommendation:** use the context manager now. Train **small side models** on Zoe's own logs, following the router-head pattern, because they are cheap, gated and replaceable. Treat a style LoRA as an optional **Phase 3 experiment** behind a flag and the replay gate, never as a memory store.

---

## 4. Recommended architecture for Zoe, as a gap list

Items are ordered by expected impact per unit of work. They assume Zoe's existing per-turn packet, portrait, open loops, continuity injection, dreaming pass and router.

1. **Make the user model a bounded, always-present, cache-stable block.** Rebuild it in the nightly dreaming pass (the Letta sleep-time and ChatGPT out-of-band-profile pattern). Target **≤300–400 tokens**, which is my suggested budget for 8k context, not a sourced number. It should hold identity essentials, response preferences, current life chapters and relationships, in the manner of a Honcho peer card. It must be byte-identical all day so llama.cpp's prefix cache holds it (Manus lesson 1). Portrait text that varies per turn defeats the cache.

2. **Fix the ordering into a stable prefix followed by a volatile tail.** The order should be: system/identity → user-model block → a "today" block (date, calendar, weather, open loops; rebuilt at most hourly) → **[cache boundary]** → recent-thread summary → retrieved memories → user turn. Put retrieved episodes and the "why this matters now" line last, next to the query. That is the recitation effect. Give each section a hard token budget and log actual usage per turn.

3. **Keep verbatim evidence alongside distilled facts.** A retrieved memory should carry its source utterance and date, not only the extracted fact (arXiv 2601.00821: +15.9 on LoCoMo). This also addresses Zoe's known dedup problem, where distilled facts fail to dedupe against richer existing entries: store the fact as an index over the evidence, not as a replacement for it.

4. **Adjudicate state at write time.** Each fact gets `valid_from`/`invalid_at` and a `supersedes` link, as in Graphiti. The dreaming pass explicitly looks for **implicit** conflicts: "moved to Perth" invalidates "lives in Geraldton" (see STALE). Retrieval then filters out invalidated facts by default. This protects against the most embarrassing companion failure, which is confidently stating something stale.

5. **Add a proactivity selector to dreaming (precompute, don't improvise).** Nightly, produce at most a few candidate "things worth raising". Each gets a **trigger condition** (time, cue word, room, mood), a salience score in the Generative-Agents style of importance × recency × relevance, and a cooldown. At runtime Zoe surfaces **at most one per conversation, only when the cue matches**. This is prospective memory, as measured by [PM-Bench](https://arxiv.org/abs/2607.12385). It avoids list-reading because the selector is ranked and capped rather than dumped. The Inner Thoughts framework (CHI 2025) scores "motivation to speak" before speaking, and a single scored candidate is its cheap version. ProAct reports −14.8% interaction turns and −28.1% hallucinations from idle-time anticipation ([arXiv 2605.25971](https://arxiv.org/abs/2605.25971)).

6. **Add memory as a tool for deliberate recall.** Keep automatic pre-retrieval as the default, then add a `recall(query, time_range)` tool the router can select for "when did I…", "are you sure?" and "what did I say about…". This is Anthropic's hybrid pattern, and FunctionGemma already routes tools better than the brain.

7. **Add "ask-to-remember".** Have Zoe occasionally ask for a reusable preference the current task does not need, capped and sensitive to mood. ATRBench shows default agents fall **≥62 points** below an oracle and that prompting alone "closes little of it" ([arXiv 2605.28108](https://arxiv.org/abs/2605.28108)), so this needs an explicit policy rather than prompting alone.

8. **Train small side models, not the brain.** Candidates, each following the router-head pattern:
   - (a) A **memory reranker/salience head** trained on Zoe's own signals: which injected memories the brain actually used or the user confirmed or corrected.
   - (b) A "**should-I-surface**" gate for item 5.
   - (c) Possibly a fine-tuned extractor for dreaming, run in the training or idle window.

   Honcho's Neuromancer XR is the existence proof: a fine-tuned 8B extractor beat a frontier model on LoCoMo extraction.

9. **Optional, last: an off-box style LoRA.** See §3. It sits behind a flag and the replay gate, and should use a single always-on adapter to protect the prompt cache.

---

## 5. Evals worth adopting

1. **LongMemEval**, the ICLR 2025 subset. Its five abilities include **knowledge updates and abstention**, and commercial assistants drop ~30% ([arXiv 2410.10813](https://arxiv.org/abs/2410.10813)). Run the "S" variant offline against the Jetson brain plus Zoe's context manager in a training window.
2. **STALE**, for implicit-conflict and premise-resistance checks on item 4 ([arXiv 2605.06527](https://arxiv.org/abs/2605.06527)).
3. **PersonaMem / PersonaMem-v2**, for implicit preferences that evolve across up to 60 sessions ([COLM 2025](https://arxiv.org/abs/2504.14225); [v2](https://arxiv.org/abs/2512.06688)). This is the closest proxy for "knows me".
4. **PM-Bench** (prospective memory) or **ATRBench** (ask-to-remember), for items 5 and 7.
5. **LoCoMo**, only for comparability with vendor numbers ([ACL 2024](https://arxiv.org/abs/2402.17753)). Conversations average ~300 turns and ~9k tokens. Vendor LoCoMo scores are self-reported and not directly comparable.

Also consider **MemArena**, an on-device memory benchmark that measured 7–87 ms search latency on edge hardware ([arXiv 2608.02613](https://arxiv.org/abs/2608.02613)). Keep Zoe's own **Samantha bar** as the product gate. For every change, measure whether the injected block changed behaviour with an **ablation**: run with and without the block and compare. The 2026 "Harness the Memory" study found no single memory substrate dominates, and retrieval-heavy setups can hurt decision tasks ([arXiv 2608.15008](https://arxiv.org/abs/2608.15008)).

---

## 6. Sources

- Letta: Memory Blocks (14 May 2025): https://www.letta.com/blog/memory-blocks/
- Letta: Sleep-time Compute blog (21 Apr 2025): https://www.letta.com/blog/sleep-time-compute/
- Lin et al., Sleep-time Compute, arXiv 2504.13171 (17 Apr 2025): https://arxiv.org/abs/2504.13171
- Letta sleep-time agents docs: https://docs.letta.com/guides/agents/architectures/sleeptime/
- Letta memory & dreaming docs (MemFS): https://docs.letta.com/letta-agent/memory
- Mem0 paper, arXiv 2504.19413 (28 Apr 2025, ECAI 2025): https://arxiv.org/abs/2504.19413
- Mem0, State of AI Agent Memory 2026 (vendor): https://mem0.ai/blog/state-of-ai-agent-memory-2026
- Zep: Temporal KG, arXiv 2501.13956 (Jan 2025): https://arxiv.org/abs/2501.13956
- Honcho docs: https://docs.honcho.to/ ; reasoning (Deriver/Dreamer/peer cards): https://honcho.dev/docs/v3/documentation/core-concepts/reasoning
- Plastic Labs, Neuromancer XR (18 Aug 2025): https://plasticlabs.ai/blog/research/Introducing-Neuromancer-XR
- OpenAI, Memory and new controls: https://openai.com/index/memory-and-new-controls-for-chatgpt/ ; Memory FAQ: https://help.openai.com/en/articles/8590148-memory-faq
- Embrace The Red, ChatGPT memory internals (4 May 2025): https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/
- Anthropic, Managing context (29 Sep 2025): https://claude.com/blog/context-management ; Memory tool docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Anthropic, Effective context engineering for AI agents (29 Sep 2025): https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Manus, Context Engineering lessons (18 Jul 2025): https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- LangChain, Context Engineering: https://www.langchain.com/blog/context-engineering-for-agents
- Chroma, Context Rot (Jul 2025): https://www.trychroma.com/research/context-rot
- Google Gemini Personal Intelligence (Jan 2026): https://gemini.google/overview/personal-intelligence/ ; past chats help: https://support.google.com/gemini/answer/16598469 ; 9to5Google (26 Feb 2026): https://9to5google.com/2026/02/26/gemini-past-chats-free/
- Character.AI memory blog: https://blog.character.ai/helping-characters-remember-what-matters-most/ ; https://blog.character.ai/memory/ ; limits (secondary): https://tukiai.com/character-ai-memory-explained/
- Park et al., Generative Agents (2023), pattern summary: https://agentpatterns.ai/agent-design/generative-agents-memory-stream/
- MemoryBank, arXiv 2305.10250 (AAAI 2024): https://arxiv.org/abs/2305.10250
- A-MEM (NeurIPS 2025): https://github.com/WujiangXu/A-mem ; https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html
- Khoj: https://github.com/khoj-ai/khoj
- Ovadia et al., Fine-Tuning or Retrieval? (EMNLP 2024): https://arxiv.org/abs/2312.05934
- Gekhman et al., Fine-tuning on new knowledge → hallucinations (EMNLP 2024): https://arxiv.org/abs/2405.05904
- Lin et al., Continual Learning via Sparse Memory Finetuning (Oct 2025): https://arxiv.org/abs/2510.15103
- Salemi & Zamani, RAG vs PEFT for personalization (ICTIR 2025): https://arxiv.org/abs/2409.09510
- OPPU, per-user PEFT (EMNLP 2024): https://arxiv.org/abs/2402.04401
- PLUME (4 Sep 2026): https://arxiv.org/abs/2609.04715
- Apple Intelligence Foundation Models Tech Report 2025: https://arxiv.org/abs/2507.13575
- Memory Decoder (NeurIPS 2025): https://arxiv.org/abs/2508.09874
- Cartridges (ICLR 2026): https://arxiv.org/abs/2506.06266
- Shepard, Costs of precomputed memory (31 Aug 2026): https://arxiv.org/abs/2608.30647
- Unsloth Gemma 4 fine-tuning guide: https://unsloth.ai/docs/models/gemma-4/train
- llama.cpp server README (LoRA): https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ; per-request LoRA PR #10994: https://github.com/ggml-org/llama.cpp/pull/10994 ; Gemma converter issue #19152: https://github.com/ggml-org/llama.cpp/issues/19152 ; GGUF-my-LoRA: https://huggingface.co/blog/ngxson/gguf-my-lora
- Fidelity Before Structure, arXiv 2601.00821 (v4 22 Jul 2026): https://arxiv.org/abs/2601.00821
- STALE, arXiv 2605.06527 (7 May 2026): https://arxiv.org/abs/2605.06527
- User as Code, arXiv 2606.16707 (15 Jun 2026): https://arxiv.org/abs/2606.16707
- Beyond Recall / Behavioral Specification, arXiv 2605.28969 (Aug 2026 rev): https://arxiv.org/abs/2605.28969
- ATRBench (Ask Now, Use Later), arXiv 2605.28108 (Sep 2026 rev): https://arxiv.org/abs/2605.28108
- ProAct (Anticipate and Learn), arXiv 2605.25971 (May 2026): https://arxiv.org/abs/2605.25971
- PM-Bench, arXiv 2607.12385: https://arxiv.org/abs/2607.12385 **[abstract not fetched; cited from search listing]**
- Inner Thoughts (CHI 2025), arXiv 2501.00383: https://arxiv.org/abs/2501.00383
- Harness the Memory, arXiv 2608.15008 (15 Aug 2026): https://arxiv.org/abs/2608.15008
- MemArena, arXiv 2608.02613 (May 2026): https://arxiv.org/abs/2608.02613
- LongMemEval (ICLR 2025): https://arxiv.org/abs/2410.10813
- LoCoMo (ACL 2024): https://arxiv.org/abs/2402.17753
- PersonaMem (COLM 2025): https://arxiv.org/abs/2504.14225 ; PersonaMem-v2: https://arxiv.org/abs/2512.06688

Not verified: HippoRAG 2's "+7% associative" figure (search-snippet only), Khoj memory internals, Honcho extractor licensing, bitsandbytes/Unsloth on Jetson aarch64, LoRA × QAT-quant × MTP interaction, and Gemma 4 LoRA conversion working end-to-end in current llama.cpp.
