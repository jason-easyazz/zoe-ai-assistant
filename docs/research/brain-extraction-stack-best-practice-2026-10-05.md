---
type: Research
title: Brain stack vs its own docs - is Zoe's memory extraction lossless? (2026-10-05)
description: Read-only audit of every LLM memory writer (person_extractor_llm, memory_digest turn/nightly/emotional/idle, the remember_fact tool, expert_dispatch) and the recall-injection seam against the llama.cpp server docs, the grammar docs, the Gemma 4 and FunctionGemma model cards, the Flue 2.x runtime and the field's extraction prompts (mem0, Graphiti, LangMem, LangExtract). Headline - no memory extractor uses constrained decoding, and two deterministic stages after the model drop exactly the new names. Ends with per-extractor JSON schemas, prompt changes with red-before-green tests, and a precision/recall fidelity bar.
tags: [memory, extraction, llama.cpp, json-schema, grammar, gemma4, functiongemma, flue, samantha-bar, research]
timestamp: 2026-10-05T00:00:00Z
---

# Brain stack vs its own docs: is Zoe's memory extraction lossless? (2026-10-05)

Scope: read-only. No brain request was sent, nothing restarted, no PR, no commit. Extends, and does not
repeat, these records (all under `docs/research/` in the repo):
`inference-speech-stack-2026-10-03.md` (flags, SWA checkpoints, MTP),
`samantha-context-engineering-2026-09-29.md` (field survey, "keep the evidence"),
`flue-and-agent-runtimes-2026-10-03.md` (Flue primitives, compaction),
`zoe-context-audit-2026-09-29.md` (slot budget; this is the "context audit" the brief calls
`zoe_context_audit`; no file or symbol of that exact name exists in the tree) and
`memory-fidelity-audit-2026-10-05.md` (authority, provenance, the P1.2 `{"fact","quote"}` anchor).

Evidence labels: **[src]** file:line read in this checkout (worktree of `25948580`) or in the running
llama.cpp build `~/llama.cpp-b11194`; **[doc]** URL fetched 2026-10-05; **[run]** measured today on the box
without touching the brain (process env, local logs, pure-Python reproduction); **[unverified]** inference or
not confirmed. Names in examples are synthetic (Dana, Mika, Biscuit, Tove, Leo).

## 0. Answer first

1. **Headline gap: no memory extractor uses constrained decoding.** Of the local llama-server POST sites in
   zoe-data (`latent_intent_detector`, `nlu_extractor`, `person_extractor_llm`, eight in `memory_digest`,
   `contact_backfill`, `user_portrait`, `proactive/composer`, three in `zoe_agent`, `ui_compose`), only
   `ui_compose.py:136` (`response_format: json_schema`) and the router sidecar (`router_two_stage.py:294`,
   GBNF `grammar`) constrain output **[src; grep of `chat/completions`, `json_schema|response_format|grammar`]**.
   Every extractor asks for "ONLY a JSON array", slices `raw[find("[") : rfind("]")+1]` and, on any failure,
   returns 0 or `[]` silently **[src: person_extractor_llm.py:213-237; memory_digest.py:510-540, :1041-1070]**.
   Nothing in the request forces a list of names to exist, and a reply cut off by `max_tokens` (300 / 256 /
   512) is indistinguishable from "no facts" because `finish_reason` is never read.
2. **Constrained decoding alone cannot make extraction lossless, and the docs say why.** The schema is "only
   used to constrain the model output and is not injected into the prompt" **[doc: grammars/README.md]**, and it
   fixes shape, not completeness. Lossless needs three layers: a schema with a slot for every thing that must
   not be collapsed (`members: [string]`, `names_seen: [string]`, `quote`), a **deterministic coverage
   invariant in code** (every personal name in the user's text is accounted for, or one bounded retry, or the
   verbatim span is kept), and **no lossy stage after the model**.
3. **Two stages after the model are lossy today, reproducible without any model.**
   (a) The word-overlap dedup in `run_turn_digest` and `run_memory_digest` skips a fact as a "duplicate" when
   more than 70 percent of its words are *substrings* of the stored-facts blob: "Dana has two kids Mika and
   Biscuit" scores **0.71**, and "User's friend Dana has two kids Mika and Biscuit" scores **0.78**, against a
   store holding "User's friend Dana has two kids. ..." and is dropped **[run; src: memory_digest.py:605-606,
   :771-776]**. (b) The weekly `_merge_near_duplicates` uses symmetric containment (inter / min) and rewrites
   the lower-ranked row to the keeper's text with no richness check: the sparse "Dana has two kids" has
   containment **1.0** inside the rich row, so if the sparse row ranks first the rich row is rewritten to the
   sparse text **[run; src: memory_digest.py:1150-1195]**. The old row is superseded by `review(edit)`, not
   deleted, so it is recoverable **[src: review(edit) semantics, memory-loss-audit-2026-10-05 and #1869]**.
4. **The LLM extractor invented "mother of" because the prompt pushes it to.** The person prompt orders that a
   relationship value "MUST say whose relative they are ... NEVER a bare role" **[src: person_extractor_llm.py:102-118]**,
   yet the `fact_type` enum offered has no relationship or children member and there is no "not stated"
   escape. A 4B model told to fill an anchor and a role will fill them, and "kids" becomes "mother", a gender
   nobody gave it. The role guard then correctly drops the hallucination, and the real fact, the names, goes
   with it. The fix is a schema whose anchor and role enums include `not_stated`, whose role has no gendered
   members, and whose children live in a `members` array, not a prose value.
5. **Recall injection and the 8k slot are healthy.** `ZOE_SEAM_RECALL_INJECT=true` is live **[run: zoe-data
   process env]**; blocks sit in the last user message after a byte-stable prefix (the recommended pattern);
   over the last 400 `FLUE_CONTEXT_BUDGET` lines the first-call prompt estimate is p50 3,180 / p90 3,717 /
   max 4,661 tokens against a 6,656-token prompt budget **[run: ~/.zoe-logs; src: zoe_flue_client.py:1264-1277]**.
   One lossy detail: each recalled fact is cut at 200 characters, mid-word, with no marker
   **[src: routers/memories.py:514-515]**.
6. **Flue is used as documented, not bypassed.** Flue 2.x has no long-term memory primitive; Zoe's native
   compaction is deliberately off; recall is a tool plus a seam block **[src/doc: flue-and-agent-runtimes
   section 1-2, line 119]**. Nothing to change in Flue for extraction; the gap is in what zoe-data sends to
   llama-server.
7. **The router is off the model card's literal format but on its intent.** FunctionGemma "is intended to be
   fine-tuned" **[doc]**. Zoe fine-tunes it with a short developer line, no tool declarations and `<unusedK>`
   routing tokens, rendered through the model's own chat template so train and inference match
   **[src: labs/functiongemma-finetune/train_lora.py:1-20, :46-54]**. Its `remember_fact` examples are all
   explicit-teach templates, so declarative statements ("My friend Dana has two kids...") never reach the
   router's tool; they belong to the passive extractors, which is where the gap is.
8. **Proposed bar**: a synthetic fidelity corpus (lists, dates, pets, pronoun owners, corrections, negations,
   long pastes) scored per extractor for slot precision, recall and hallucination, in two layers: an offline
   recorded-output layer that is `ci_safe` and catches 3(a), 3(b) and the guards with no model, and a live
   layer in a brain window (section 5).

## 1. What Zoe does today

### 1.1 The writers and how each calls the brain

All call `llama-server` on `127.0.0.1:11434` (`gemma_endpoint.gemma_base()`), single slot (`--parallel 1`,
`--ctx-size 8192`), with `model` set from `MEMORY_DIGEST_MODEL`
**[src: gemma_endpoint.py; llama-server.service; inference-speech-stack section 1.3]**.

| Writer | Cadence | Call shape | Truncation / budget | Output handling | Source |
|---|---|---|---|---|---|
| `memory_extractor.extract_candidates` | every turn | **no model call**: regex templates, correction / pronoun / possessive anchors | excerpt 220 chars, whole turn stored as evidence | n/a | memory_extractor.py:672-733, :791-990 |
| `person_extractor_llm.process_text_llm` | every turn (>= 4 words), voice + chat + notes + journal | chat, `temperature 0.1`, free JSON array, `max_tokens 300` | **`text[:1200]`**, 300 tokens | `find("[")..rfind("]")`; any failure `return 0` at DEBUG | person_extractor_llm.py:185-237 |
| `memory_digest.run_turn_digest` | every turn, background | chat, `0.1`, free JSON array, `max_tokens 256` | **`user_message[:600]`**, 256 tokens | same slice; failure returns the empty result | memory_digest.py:463-545 |
| `memory_digest._extract_facts_with_gemma` | nightly digest, and idle consolidation (~3 min idle sweep) | chat, `0.1`, free JSON array, `max_tokens 512` | **`chat_text[:3000]`, tail dropped** (logged at WARNING) | raises `ExtractorError`, the one path that does not swallow | memory_digest.py:1026-1070; memory_idle_consolidation.py:317 |
| emotional pass, open loops, concept tags, synthesis, contradiction judge | nightly / weekly | chat, free JSON | 3000 chars; 256 / 500 / 60 / 120 / 80 tokens | per-site slice | memory_digest.py:898-960, :1075-1110, :1468-1490, :1730-1780, :1898-2000 |
| Brain tool `remember_fact` (Flue) | model's choice | tool arg `{fact: string}` written by Gemma | none | `memory_store {text}` verbatim | labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts:979-995 |
| `expert_dispatch.store_fact` | explicit teach ("remember that ...") | stores the **verbatim utterance** after correction / question guards | none | `_ingest_or_supersede` | expert_dispatch.py:488-672 |

The two per-turn LLM passes (`run_turn_digest`, `person_extractor_llm`) run **concurrently via
`asyncio.gather`** against one slot, so they serialize; the person pass was measured at 0.6-1.3 s of Gemma
time per call **[src: person_extractor_llm.py:68-72; routers/voice_tts.py:2965-2987]**. Each pass has its own
prompt, output vocabulary and guards; the fidelity audit already tags the person pair as V9.

Live flags that matter **[run: zoe-data process env, 2026-10-05]**: `ZOE_SEAM_RECALL_INJECT=true`,
`ZOE_SEAM_OFFER_INJECT=1`, `ZOE_USER_MODEL_BLOCK=1`, `ZOE_MEMORY_IMPLICIT_SUPERSEDE=1`,
`ZOE_RECALL_EVIDENCE=1`, `ZOE_ROUTER_HEAD=active`. Not set (default off): `ZOE_PERSON_LLM_PREFILTER`,
`ZOE_PERSON_LLM_CONFIDENCE_GATE`. `MEMORY_DIGEST_MODEL=gpt-4o-mini` is set and is what every extractor sends
as `model` **[run]**. llama-server in single-model mode ignores the name, so it works today, but it is a
latent trap (G9).

### 1.2 What is already right

- Static instructions first, volatile text last in every extractor prompt (prefix-cache friendly)
  **[src: person_extractor_llm.py:102; memory_digest.py:341-361]**.
- `--reasoning off` at server level, so no thinking tokens burn the 256-512 budgets
  **[src: llama-server.service; doc: server README `--reasoning`]**.
- Numeric dates normalised to words before the model reads them (`date_locale.normalize_numeric_dates`) and a
  shared "never infer role or gender from a name; a pet is never a child" rule (`people_roles.PROMPT_RULES`),
  the mem0 / Graphiti rule in spirit **[src: people_roles.py:33-39]**.
- Deterministic backstops after the model: anchor validator, role-claim validator, storable-fact gate, and
  `reconcile_for_ingest` with a **richness-aware** ADD / UPDATE / SKIP (`_information`, `_RICHNESS_MARGIN = 4`)
  **[src: memory_quality.py:451-456, :543-560]**. The two lossy stages in finding 3 are the paths that do
  *not* use it.
- A grammar precedent exists in-repo: the card composer ships `response_format: json_schema` plus a
  validator ("proven live 2026-07-03") and the router ships GBNF **[src: ui_compose.py:5-9, :136-139;
  router_two_stage.py:221-241]**.

### 1.3 Where it is lossy, with the evidence

| # | Loss | Where | Evidence |
|---|---|---|---|
| L1 | Free-text JSON, regex-sliced; a reply truncated by `max_tokens` parses as empty | person_extractor_llm.py:213-237; memory_digest.py:517-540 | [src]; `finish_reason` appears in none of the four parse blocks |
| L2 | Input truncated before the model: 1,200 / 600 / 3,000 chars, tail silently dropped (person and turn digest log nothing) | person_extractor_llm.py:213; memory_digest.py:510, :905, :1041 | [src] |
| L3 | Substring word-overlap dedup treats a richer fact as a duplicate | memory_digest.py:605-606 (turn), :771-776 (nightly) | [src]; reproduced **[run]** at 0.71 and 0.78 against threshold 0.7 |
| L4 | Weekly merge rewrites the lower-ranked row to the keeper's text; symmetric containment, no richness check | memory_digest.py:1150-1195 | [src]; sparse-in-rich containment = 1.0 **[run]** |
| L5 | Prompt forces an anchor and role the text does not give; the guard then drops the whole item, names included | person_extractor_llm.py:102-118, :254-310 | [src]; matches the observed "mother of" |
| L6 | The person extractor's guard drops are `logger.info` only: not in the reject ledger (#1869), no counter | person_extractor_llm.py:257-310 vs `memory_quality._record_quality_reject` | [src]; `memory_async_extract_fail_count` only sees exceptions that escape the pass, and these passes swallow theirs |
| L7 | Recall packet cuts each fact at 200 chars mid-word | routers/memories.py:514-515 | [src] |
| L8 | `remember_fact` argument is model-authored prose; no quote, no members | zoe-tools.ts:979-995 | [src] |

## 2. What the docs say

### 2.1 llama.cpp server (README, grammars) vs Zoe's calls

| Feature | What the docs say | Zoe today |
|---|---|---|
| `response_format` / `json_schema` | `/v1/chat/completions` supports "both plain JSON output (e.g. `{"type": "json_object"}`) and schema-constrained JSON"; `/completion` takes `"json_schema": SCHEMA` "to constrain generations" **[doc]** | `ui_compose` only. In the running build `server-common.cpp:1179-1203` maps `response_format.json_schema.schema` to `json_schema`, rejects `json_schema` together with `grammar` ("Cannot use both json_schema and grammar"), and turns an empty schema into `{"type":"object"}` **[src]** |
| GBNF `grammar` | "BNF-like grammar to constrain generations" **[doc]** | Router sidecar only |
| Schema is not in the prompt | "The JSON schema is only used to constrain the model output and is not injected into the prompt" **[doc: grammars/README.md]** | Extractors describe the shape in prose, which must stay |
| Supported schema features | `type, properties, required, items, minItems, maxItems`, `minLength/maxLength`, `enum, const`, `pattern` (must start `^` and end `$`), `anyOf/oneOf/$ref`. Unsupported or broken: "Can't mix `properties` w/ `anyOf` / `oneOf` in the same type", "Nested `$ref`s are broken", `uniqueItems`, `prefixItems`; "additionalProperties defaults to `false`" **[doc]** | `ui_catalog.py` already unrolls depth to avoid recursion for this reason **[src: :90-135]** |
| Property order | Docs silent. `common/json-schema-to-grammar.cpp:705-760` emits **required properties in declared order**, optional ones after **[src]** | A schema can put `quote` first, so the model copies the span before choosing fields |
| Repetition perf | "`x? x? x?` ... may result in extremely slow sampling - use `x{0,N}`" **[doc]** | Use `maxItems`, never optional chains |
| Known Gemma 4 traps | #23990 (closed): assistant prefill plus `json_schema` on Gemma 4 gave "Failed to initialize samplers". #21537 (open): `json_schema` silently ignored by some chat-template handlers; the issue lists Gemma4 among handlers that do honour it **[doc: gh api]** | Never prefill the assistant turn; run a schema-invalid negative control on b11194 before trusting |
| Speculative decoding + grammar | README silent. `server-context.cpp:60-100, :3905-3925` verifies drafts through the grammar-aware sampler (`common_sampler_accept`, a cloned sampler for rollback) **[src]** | Supported in code; **grammar x draft-mtp throughput on this box is unmeasured** [unverified] |
| `n_probs` | "If greater than 0, the response also contains the probabilities of top N tokens for each generated token"; `post_sampling_probs` for post-sampler values **[doc]** | Not used anywhere in zoe-data; the confidence gate asks the model for a verbalised number **[src: person_extractor_llm.py:119-135]** |
| Slots / `cache_prompt` | `cache_prompt`: "Re-use KV cache from a previous request if possible" (default true, `common/common.h:628`); `id_slot`; `--cache-ram`; `--swa-full`; `--ctx-checkpoints` **[doc/src]** | Applied: `--swa-full`, `--cache-ram 1024`, one slot. Extractor prompts share only a short static head, so each call mostly re-prefills and evicts the brain's slot into `--cache-ram` **[src: docs/knowledge/brain-flags-tuning-2026-09.md:39-45]** |
| `--parallel`, batching | "number of server slots"; continuous batching on by default **[doc]** | `--parallel 1` is a correctness choice with draft-mtp (llama.cpp #28286) **[src: unit]**, so extractors queue behind or in front of the voice turn |
| KV quant, `--mlock`, MTP flags, `/metrics` | `-ctk/-ctv` q8_0 etc; `--load-mode mmap+mlock`; `--spec-type draft-mtp`, `--spec-draft-n-max`; `/metrics` only with `--metrics` **[doc]** | All applied and measured in inference-speech-stack section 1.3; nothing new here |
| Truncation signal | "`limit`: Stopped because `n_predict` tokens were generated before stop words or EOS" **[doc]** | Never read by any extractor (L1) |

### 2.2 Gemma 4 model card  **[doc: ai.google.dev model_card_4, prompt-formatting-gemma4]**

- "Use the following standardized sampling configuration across all use cases: temperature=1.0, top_p=0.95,
  top_k=64." The extractors send 0.1 and inherit the server's `--top-k 64 --top-p 0.95`; the brain sends 0.5
  **[src: person_extractor_llm.py:216; inference-speech-stack section 0]**. Low temperature under a grammar is
  fine and is MTP's best case (greedy drafting is the default upstream, #27694); the card's number is for
  open-ended chat.
- Native `system` role is documented. Zoe's `services/zoe-data/AGENTS.md` line 79 says Gemma has "no system
  role - the template folds the system prompt into the first turn". Which template the QAT GGUF embeds is
  **[unverified]**; one `GET /props` (chat_template) settles it, not run here because of the no-touch rule.
- Thinking is opt-in via `<|think|>` in the system prompt; `--reasoning off` matches extraction.
- "Let Me Speak Freely?" (arXiv 2408.02442) found format restriction degrades *reasoning* tasks, and that
  stricter formats hurt more **[doc]**. Extraction is not a reasoning task; the schemas below stay flat and
  put one free-text field (`quote`) first, so the model "speaks" before it commits.

### 2.3 The field's extraction practice vs Zoe's prompts  **[doc, fetched today]**

| System | What it does | Zoe |
|---|---|---|
| **mem0** (`mem0/configs/prompts.py`) | "Do NOT include information from ASSISTANT OR SYSTEM MESSAGES"; "Preserve exact quantities as stated. '416 pages' stays '416 pages'"; proper nouns "must never be generalized"; never infer gender from names; relative dates resolved against the observation date; the 2026 prompt is ADD-only with `attributed_to` and `linked_memory_ids`; the older UPDATE prompt has ADD / UPDATE / DELETE / NONE with worked examples | Has user-only extraction (purity test), no-gender-from-name, day-first dates, ADD / UPDATE / SKIP in code. **Missing**: a "never generalise a proper noun or a list" line, an exact-quantity line, and any few-shot example (none of the extractor prompts has one, positive or negative) |
| **Graphiti / Zep** (`extract_nodes.py`, `extract_edges.py`) | "Paraphrase the sentence structure but NEVER generalize"; for a relative, pet or associate named by a bare term, "extract the entity qualified with the possessor's name"; "Always use the most specific form mentioned"; "NEVER infer attribute values from the entity's name, from related entities, from generic world knowledge"; "If no value is supported by the FACT, set the field to null - do not write a sentence" | Zoe's "whose relative" rule is the possessor-qualification rule **without the null escape**: that is L5. The current `extract_nodes.py` has **no reflexion function** (eight prompts, none of them reflexion), so do not cite "Graphiti reflexion" as current practice [unverified for older versions]; the Zep abstract does not mention it either |
| **LangExtract** (google/langextract) | "Use exact text for extractions. Do not paraphrase or overlap entities"; every extraction maps to a character interval and one that cannot be located gets `char_interval = None`; few-shot examples are verbatim spans; `extraction_passes=3` and chunking (`max_char_buffer`) raise recall on long input | The template for the coverage invariant: verbatim `quote` verified as a substring, a second pass only on a coverage miss. Zoe truncates instead of chunking (L2) |
| **LangMem** (conceptual guide) | Pydantic `schemas` for structured extraction; collection vs profile; `enable_inserts`; "If the system over-extracts, this could lead to reduced precision ... If it under-extracts, this could lead to low recall" | Same trade-off, but Zoe measures neither side (section 5) |
| **Letta** (memory-blocks) | "The description is the main information used by the agent to determine how to read and write to that block" | The `remember_fact` tool description is the only guidance the model gets for what to write (L8). Letta tool names (`memory_replace` etc.) were not on the fetched page [unverified] |
| **Honcho** (via the 2026-09-29 record) | A fine-tuned 8B extractor beat a frontier model on LoCoMo | Evidence that a bar-trained extractor is a later step, after the bar exists |

The common thread is a per-relation or typed output contract. mem0 stores flat strings (weakest on list
fidelity); Graphiti uses typed edges with `null`; LangMem uses Pydantic per memory type. Zoe has the strictest
*guards* of the set and the loosest *output contract*.

### 2.4 Flue 2.x  **[src; upstream docs already fetched in the 2026-10-03 record]**

- No long-term memory primitive upstream; Zoe's memory is MemPalace through `recall_memory` / `remember_*`
  and the seam block (flue-and-agent-runtimes line 119). Native compaction is off twice (`compaction: false`,
  `contextWindow: 0`), on purpose (its section 2.3). Zoe is **not bypassing** a Flue memory feature; there is
  none.
- Context budget: the sidecar reports `context_budget` (system / tools / history / tail / stale / elided) on each
  `{"done"}` terminal, logged as `FLUE_CONTEXT_BUDGET` **[src: zoe_flue_client.py:1264-1277]**. **[run]** Over
  the last 400 lines of the concatenated app logs (includes harness and replay sessions): first-call total p50
  3,180, p90 3,717, max 4,661 estimated tokens (chars/4) vs the 6,656-token prompt budget (8,192 minus a 1,536
  reserve, per the 2026-09-29 audit). Extractor calls (<= ~1.4k prompt + 512 out) also fit the slot.
- Recall seam: block capped at 12 bullets / 1,600 chars (~360-400 tokens), after the identity line in the last
  user message, on a conservative personal-question shape **[src: zoe_flue_client.py:551-690, :766-767,
  :885-915]**. Blocks are elided from older messages only under `ZOE_BRAIN_ELIDE_STALE_BLOCKS`, which is
  flag-dark **[src: services/zoe-data/AGENTS.md line 26]**; whether it is on in the sidecar env is
  [unverified] (the sidecar's env was not readable), so past recall blocks may be replayed on every later
  turn of a session. That is the `stale` field in the budget line and the next thing to read there.
- Tool-result handling: `_fit_tool_result_to_slot` guards the aux callers **[src: brain-flags-tuning-2026-09.md:121-133]**.

### 2.5 FunctionGemma-270M  **[doc: huggingface.co/google/functiongemma-270m-it; ai.google.dev formatting page]**

- "FunctionGemma is intended to be fine-tuned for your specific function-calling task, including multi-turn use
  cases"; "not intended for use as a direct dialogue model". Base 58 percent vs fine-tuned 85 percent on the
  Mobile Actions set.
- Format: a developer turn "You are a model that can do function calling with the following functions",
  `<start_function_declaration>` blocks, `<start_function_call>` / `<end_function_call>`, string values wrapped in
  `<escape>`. Not trained for multi-turn or multi-step chaining; single-turn and parallel calls are supported.
  No sampling guidance, no tool-count guidance, no fine-tuning recipe on the page.
- **Zoe vs the card.** The router (a) fine-tunes, as intended; (b) replaces the declaration block with
  `<unusedK>` routing tokens and a 47-token developer line (the Octopus-style "functok" variant), with targets
  rendered through the model's chat template so train equals inference **[src: train_lora.py:1-20, :46-54;
  router_two_stage.py:84-87]**; (c) decodes under a GBNF restricted to a stage-1 shortlist plus a chat escape,
  `temperature 0`, `max_tokens 64` **[src: router_two_stage.py:221-241, :291-296]**; (d) is single-turn.
  Nothing contradicts the card's dialogue or single-turn limits. What differs is that the **format is
  custom**, so the card's 58 to 85 percent figures do not transfer; Zoe's own 90.1 percent is measured on an
  81-case frozen corpus **[src: router_two_stage.py:15-17]**, a small sample.
- Extraction relevance: the router never authors memory text. `expert_dispatch.store_fact` stores the user's
  utterance, not the router's `args.fact` **[src: expert_dispatch.py:488-672]**. The corpus has 146
  `remember_fact` rows (128 + 8 + 10), all explicit-teach templates, none with a list of names
  **[run: grep of labs/functiongemma-finetune/data]**. A compound utterance ("add milk and remind me at five")
  gets one call by construction of the grammar although the card says parallel calls are supported; whether
  the brain completes the second half is [unverified].
- The sidecar still runs the b9733 build **[src: inference-speech-stack section 1.4]**, so a router grammar
  change and a brain schema change are tested on different binaries.

## 3. Gaps ranked (impact x effort, each with its proving test)

Impact: how much fact loss or wrong fact it prevents. Effort: S under a day, M a few days, L a week plus a
brain window. "Red first" means the test is written, run against current code and shown failing, then the fix.

| # | Gap | Impact | Effort | Proving test (red before green) |
|---|---|---|---|---|
| **G1** | **Lossy dedup in both digests (L3)** | High: silently drops exactly the new names | S | `test_digest_dedup_keeps_novel_names`: stored blob "User's friend Dana has two kids...", fact "Dana has two kids Mika and Biscuit" must reach `ingest`. **Red today** (0.71 > 0.7). Rule: never skip when the fact holds a token absent from the blob that is capitalised, numeric or quoted; compare word-boundary tokens, not substrings. Negative control: an identical fact is still skipped. Pure unit, `ci_safe`, no model |
| **G2** | **Weekly merge can overwrite the richer row (L4)** | High if scheduled live (unverified), recoverable via the superseded row | S | `test_merge_never_replaces_richer_with_sparser`: two rows, the sparse one ranks first; assert the richer text survives (keeper = richer). **Red today** by construction (containment 1.0, no richness test). Reuse `memory_quality._information` as the tie-break |
| **G3** | **No constrained decoding on any extractor (L1); truncation invisible** | High (structural): the headline | M | Offline: recorded raw replies (valid, prose-wrapped, truncated mid-array, `finish_reason=length`) through the new parser: today the truncated and wrapped-with-brackets cases parse to 0 items silently; after, truncation is detected, retried once and counted. Live: bar F1 (section 5), a schema-invalid negative control on b11194, MTP acceptance and extractor latency before and after |
| **G4** | **Contract forces an anchor and role the text lacks (L5)**; no `not_stated` escape | High: the Dana class | S-M (rides G3) | `test_person_schema_not_stated`: "My friend Dana has two kids, Mika and Biscuit" with a recorded output choosing `not_stated` keeps Dana and children `[Mika, Biscuit]`; a recorded output claiming `mother` is dropped by `value_role_unsupported` but the **members survive** (today the whole item, names included, dies with the guard). Red today |
| **G5** | **No coverage invariant**: nothing checks every name in the user's text appears in some item | High: this is the actual lossless guarantee | M | `test_coverage_invariant`: text names {Dana, Mika, Biscuit}; output covers {Dana}; invariant reports uncovered {Mika, Biscuit}, triggers exactly one retry with the missing names, and on a second miss stores the verbatim span as a low-confidence fact. Reuse `mentions_person` / `_CAP_STOP` (person_extractor_llm.py:77-98) so there is one definition of "a name" |
| **G6** | **Input truncation before the model (L2)** | Medium: pasted lists and long days lose their tail | S-M | `test_tail_names_survive`: 1,500-char paste with a name at char 1,400; absent from the prompt today. After: chunk on whole sentences / turns with a small overlap, merge by exact-name key, counter when chunking fires |
| **G7** | **Silent failure and guard drops are unmeasured (L6)** | Medium: you cannot fix what has no number | S | `test_person_llm_drops_hit_reject_ledger`: a guard drop writes a reject-ledger row (reason, source, 120-char text, as #1869 does for the other writers); add `memory_extract_outcome_count{pass,outcome=ok\|empty\|parse_fail\|truncated\|guard_drop\|http_fail}` |
| **G8** | **Two serialized LLM passes per turn** (turn digest + person LLM), two vocabularies (V9) | Medium: latency to the next voice turn, plus disagreement | M | After G3, merge into **one** call with one schema (`people[]` + `facts[]`); compare bar F1 and brain TTFT p50 under the replay probe: one fewer 0.6-1.3 s slot occupation per turn. Negative control: flag off, the old two-pass path still works |
| **G9** | `MEMORY_DIGEST_MODEL=gpt-4o-mini` sent to a local server | Low now; a silent total extraction outage if llama-server is ever run in router mode (every site swallows the 404) | S | `test_extractor_model_name_is_local`: the default resolves to the GGUF alias; a startup check of `GET /v1/models` logs a WARNING when the configured name is absent |
| **G10** | Recall packet cuts a fact at 200 chars mid-word (L7) | Low-medium: a long verbatim list loses its tail at injection | S | `test_packet_never_cuts_mid_word`: a 260-char fact with names at the end renders whole or ends on a word boundary with an ellipsis; total stays under 1,600 chars via the bullet cap |
| **G11** | `remember_fact` argument is free prose (L8) | Low-medium | S | Add `quote` and `members` to the tool input; the handler verifies `quote` against the turn text. Test: a quote that is not a substring of the turn is stored as an unanchored low-confidence row, not an approved fact |
| **G12** | Verbalised confidence instead of token probabilities | Low (the gate is off) | M | After G3: request `n_probs` on the enum token and compare calibration (Brier) against the verbalised 0-1 on the bar corpus. `n_probs` x MTP is [unverified] |
| **G13** | Extractor sampling: `temperature 0.1` plus the server's `top-k 64` leaves residual variance; no `seed` | Low | S | Under a grammar use `temperature 0`, `top_k 1`; bar run N=3 must give identical output across samples (the bar already supports `--samples`) |

## 4. Recommendation

**Do it in this order. Steps 1 and 2 need no brain and no flag.**

1. **G1 + G2 + G7 first** (one small PR, offline `ci_safe` tests): fix the dedup rule, add the richness
   tie-break to the weekly merge, put the person extractor's drops in the reject ledger and add an outcome
   counter. This removes two *deterministic* loss paths and makes every later number visible.
2. **Land the offline fidelity layer** (section 5, layer A) with recorded outputs, including the Dana sentence,
   so G3 to G5 are proven red before they are built.
3. **G3 + G4 + G5 behind one dark flag** (`ZOE_EXTRACT_SCHEMA=1`, default off): the schemas below, quote-first
   property order, `not_stated` escapes, `members[]`, the coverage invariant with one bounded retry, and a
   `finish_reason` check. Roll out per extractor, person pass first (highest loss class). Gate: bar layer B in
   a brain window (RAM gate: at least 2 GB quiet headroom per the voice-pipeline recipe), no voice replay
   regression, MTP acceptance within noise of the 62.5 percent baseline, extractor latency recorded. Do not
   prefill the assistant turn (llama.cpp #23990).
4. **G6 (chunking) and G8 (single merged call)** once layer B shows the schema pass at or above the free-text
   pass on recall without losing precision.
5. **G9, G10, G11** are independent hygiene; slot them anywhere.
6. Defer G12 and any fine-tuned extractor (the Honcho precedent) until the bar has a baseline; the bar is the
   precondition for both, as the router head's own history shows.

### 4.1 Schemas, per extractor

All flat, `additionalProperties:false`, required in the order shown, no `anyOf` mixed with `properties`. Send as
`response_format: {type: "json_schema", json_schema: {name, schema}}` (the shape `ui_compose.py:136` already
uses) and describe the same shape in the prompt, since the schema is not shown to the model **[doc]**. These are
drafts; that each compiles on b11194 is [unverified] until run once (`examples/json_schema_to_grammar.py` is
absent from `~/llama.cpp-b11194`, so there is no offline converter on the box).

**S-P, `person_extractor_llm` (replaces the array)**

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["names_seen", "items"],
  "properties": {
    "names_seen": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 40}},
    "items": {"type": "array", "maxItems": 12, "items": {
      "type": "object", "additionalProperties": false,
      "required": ["quote", "name", "fact_type", "role", "anchor", "value", "members"],
      "properties": {
        "quote":     {"type": "string", "minLength": 3, "maxLength": 200},
        "name":      {"type": "string", "maxLength": 40},
        "fact_type": {"enum": ["preference","birthday","work","meeting","gift_idea","gift_given",
                               "bucket_list","relationship","children","pet","health","other"]},
        "role":      {"enum": ["none","friend","partner","child","parent","sibling","colleague",
                               "boss","neighbour","other_stated","not_stated"]},
        "anchor":    {"enum": ["speaker","other_named","not_stated"]},
        "value":     {"type": "string", "maxLength": 120},
        "members":   {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 40}}
      }}}
  }
}
```

Design notes. `quote` is first, so the model copies the user's span before deciding fields (the converter
keeps declared order for required properties **[src: json-schema-to-grammar.cpp:705-760]**). `role` has **no
gendered members** (no mother, no father; only parent and child), so "mother of" is not expressible, and
`not_stated` makes a missing anchor legal, which removes the prompt's pressure to guess (L5). `members` is where
lists live: for "Dana has two kids, Mika and Biscuit" the item is `{name: Dana, fact_type: children, role:
friend, anchor: speaker, value: "has two kids", members: [Mika, Biscuit]}`, and the count is `len(members)` in
code, never a number the model writes. `names_seen` feeds the coverage invariant. A pet is `fact_type: pet`, so
Biscuit-the-dog cannot be filed as a child.

**S-T, `run_turn_digest`**: `{"facts":[{"quote","type","subject","subject_name","fact","where","when","members"}]}`
with `type` the existing seven-member enum, `subject` an enum `user|named|not_stated`, `where` and `when`
strings (empty when absent; the prose rule "never drop the place or the day" becomes two required fields),
`maxItems 8`.

**S-N, nightly digest and idle consolidation**: the S-T item plus `turn` (integer index of the source user
turn), run over ~2,500-char windows of whole turns and merged by `(subject_name, type, normalised fact)`.

**S-E, emotional pass**: `{"moments":[{"quote","emotion","significance"}]}`, `emotion` the existing enum,
`significance` `{"enum":[2,3]}`, so the prompt's "only >= 2" is structural.

**S-C, `_is_contradiction`**: `{"contradicts": boolean, "reason": {"type":"string","maxLength":90}}`; 80 tokens
suffices; add `n_probs` later (G12).

### 4.2 Prompt changes, each with its red-before-green shape

| # | Change | Why | Test shape |
|---|---|---|---|
| P1 | Add: "Copy proper nouns and every item of a list exactly as written. Never replace names by a count." | mem0 "proper nouns must never be generalized"; Graphiti "NEVER generalize" **[doc]** | Prompt-contract test pins the line (cheap); the behavioural proof is bar recall on `list_names` and `list_long` |
| P2 | Replace the "MUST say whose relative they are" order with: "If the text does not say whose relative someone is, or their role, answer `not_stated`. Never infer gender, role or relation from a name." | Graphiti "set the field to null - do not write a sentence"; mem0 no gender from names **[doc]** | Red today: the old line is present and no `not_stated` exists; green: absent and present respectively |
| P3 | Few-shot: 2 positives (a list of names; a day-first date) and **3 negatives** (a pet is not a child; an unlabelled name list; third-party advice such as "Dana says you should try yoga" gives no user fact). Synthetic, ~120 tokens total | Zero extractor prompts have examples today **[src]**; LangExtract is example-driven **[doc]** | Prompt-length guard (fits the 8k slot with margin) plus bar precision on the negative classes must not fall |
| P4 | Add: "Keep exact numbers, doses, times and amounts as written." | mem0 "416 pages stays 416 pages" **[doc]** | Bar class `quantity`: gold "20 mg" survives verbatim |
| P5 | One shared constant for the anchor and role sentence used by all extractor prompts; today the person prompt restates the anchor rule locally while the digests import `_shared_rules` | **[src: person_extractor_llm.py:102-118 vs :87-93; memory_digest.py:270-279]** | Extend the existing shared-rules equality test to the person prompt |
| P6 | Second pass only on a coverage miss: "These names are in the text but not in your answer: {missing}. Add an item for each, or leave them in `names_seen` only if no fact about them was stated." | LangExtract multi-pass **[doc]**; one bounded retry, never a loop | The G5 test: the retry fires at most once; a second miss stores the verbatim span |

## 5. Measurement: an extraction fidelity bar (extend the Samantha bar harness)

Add `scripts/perf/extraction_fidelity_bar.py`, patterned on `samantha_bar.py` (dry-run plan, artifacts in
`~/.cache/zoe/`, `--record-baseline` / `--compare-baseline`, `--samples N` majority vote, `flock
/tmp/zoe-voice-harness.lock`). There is precedent for the dry call: `scripts/perf/measure_person_extractor.py`
performs the same LLM call and parse "but NEVER invokes `apply_person_fact`" **[src: lines 1-12]**.

**Corpus** (`tests/fixtures/extraction_fidelity.jsonl`, synthetic only, one record per line):
`{"id","class","turns":[...],"gold":[{"kind","subject","slot","value","members":[...]}],"must_not":[...]}`.

| Class | Example (synthetic) | Gold | must_not |
|---|---|---|---|
| list_names | "My friend Dana has two kids, Mika and Biscuit." | Dana friend of user; children members {Mika, Biscuit} | any gendered role (mother / father); a count with no names |
| list_long | 1,500-char paste with a name at char 1,400 | all names present | dropped tail |
| pet_vs_kid | "Dana has two kids and a dog called Biscuit." | children count 2, no names; pet Biscuit | Biscuit as a child |
| date_dayfirst | "Leo's birthday is 7/8/1991." | 7 August 1991 | July 8 |
| quantity | "Tove takes 20 mg every morning." | 20 mg verbatim | "about 20", "20 g" |
| pronoun_owner | T1 "I have a friend Tove." T2 "She's allergic to nuts." | allergy attaches to Tove | allergy on the user |
| correction | "Actually Mika is seven, not six." | supersedes the age | both ages live |
| negation | "Dana doesn't have kids." | no children fact; supersede one if it exists | a children fact |
| third_party_advice | "Dana says I should try yoga." | none (or an advice fact, never a user fact) | "User does yoga" |
| bare_role_trap | "Emily is the wife." (no anchor) | nothing anchored to the user | "user's wife Emily" |
| unlabelled_roster | "Mika, Biscuit, Tove came over." | names recorded, no roles | roles guessed from names |
| event_place_day | "My brother is driving down from Porto on Saturday." | brother, where Porto, when Saturday | dropped place or day |
| negatives | 30 ordinary commands and chit-chat | no memory | any write |

**Scoring** (per extractor, per class, N=3 samples, majority):
- Slot precision = correct extracted slots / all extracted slots; recall = gold slots found / gold slots; F1.
  A slot is correct when `(subject, slot)` match and the value matches after normalisation (lower-case,
  day-first date words, number-word to digit). `members` is compared as a **set**, so "two kids" with no names
  scores zero on a `members` gold.
- **Name recall**: the fraction of gold personal names present anywhere in the extractor's output. This is the
  lossless number; target 1.00 on every class, hard-fail below 0.98.
- **Hallucination rate**: items violating `must_not`, or whose quote is not a substring of the turn.
- **Silent-failure rate**: runs where the model answered but the pass wrote nothing and logged nothing (needs
  G7's outcome counter; not observable today).
- Cost: extractor wall time and `prompt_n` from the response `timings`, MTP acceptance from `/metrics`, so a
  schema change that slows the slot shows up in the same run.

**Two layers**
- **A. Offline, `ci_safe`, no model** (build first): feed *recorded* raw model replies (valid, prose-wrapped,
  truncated, `mother of`-hallucinating, count-collapsed "has two kids") through the real parse, guard, dedup
  and merge functions. This catches L1, L3, L4, L5, L6 and the coverage invariant, and every guard gets a
  negative control (break the fix, the test must go red). It reproduces both findings in section 0.3 today.
- **B. Live, brain window** (RAM gate, serial with the other brain harnesses): the corpus through each extractor
  against the real llama-server, flag off then on, same-session control; `--compare-baseline` exits 1 on a
  class that passed and no longer does. Compare four arms: regex only (`memory_extractor`), regex + free-text
  LLM (today), regex + schema LLM, regex + merged schema LLM.

Instrument checks before trusting a result (the standing rule): a deliberately broken extractor that returns
`[]` must score recall 0 and exit 1; a perfect-oracle run must score 1.0 and not flake across samples; a
timeout or skipped item is `error`, never a pass.

## 6. What this record did not establish  [unverified]

- Whether any grammar in section 4.1 compiles and behaves on b11194 with `--spec-type draft-mtp` (the code path
  exists; throughput and acceptance are unmeasured).
- Which chat template the Gemma 4 QAT GGUF embeds (system role vs folded), and whether `json_schema` is honoured
  by that template's handler on b11194 (issue #21537 lists Gemma4 among those that do).
- Whether `run_weekly_consolidation_for_all` is scheduled live (reachable at `routers/system.py:906` and `:988`);
  G2's impact depends on it.
- Whether `ZOE_BRAIN_ELIDE_STALE_BLOCKS` is on in the Flue sidecar (its env was not readable here).
- The real-world rate of L1 to L6: no counter exists (that is G7); the `ExtractorError` path in
  `memory_digest` is the only place a failure is classified at all.
- FunctionGemma behaviour on compound utterances; `n_probs` with MTP.
- The #1874 regex and the exact LLM output that produced "mother of" were not reproduced (live brain not
  touched); the mechanism in finding 4 is read from the prompt and the guards, not from a captured reply.

## 7. Sources

- llama.cpp server README: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md (fetched 2026-10-05)
- llama.cpp GBNF / JSON-schema conversion: https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md (fetched 2026-10-05)
- llama.cpp issues read via `gh api`: #23990 (Gemma4 + json_schema + prefill, closed), #21537 (json_schema ignored by some chat templates, open), #21228 (`$ref/$defs` rule-count limit, closed); #28286 and #27694 as cited in the unit file and the 2026-10-03 record
- Running build source read: `~/llama.cpp-b11194/tools/server/server-common.cpp:1170-1215`, `tools/server/server-context.cpp:60-100, :3905-3925`, `common/json-schema-to-grammar.cpp:700-760`, `common/common.h:628`
- Gemma 4: https://ai.google.dev/gemma/docs/core/model_card_4 ; https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4 (fetched 2026-10-05)
- FunctionGemma: https://huggingface.co/google/functiongemma-270m-it ; https://ai.google.dev/gemma/docs/functiongemma/formatting-and-best-practices (fetched 2026-10-05)
- mem0: https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/prompts.py
- Graphiti: https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/prompts/extract_nodes.py ; https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/prompts/extract_edges.py ; Zep paper https://arxiv.org/abs/2501.13956
- LangExtract: https://github.com/google/langextract ; LangMem: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/ ; Letta: https://docs.letta.com/guides/agents/memory-blocks
- "Let Me Speak Freely?": https://arxiv.org/abs/2408.02442
- In-repo: `services/zoe-data/{person_extractor_llm,memory_digest,memory_extractor,expert_dispatch,memory_quality,people_roles,zoe_flue_client,router_two_stage,ui_compose,ui_catalog,gemma_endpoint}.py`, `services/zoe-data/routers/{memories,voice_tts}.py`, `labs/flue-zoe-brain-2x/{README.md,src/tools/zoe-tools.ts}`, `labs/functiongemma-finetune/{train_lora.py,data/}`, `scripts/perf/{measure_person_extractor,samantha_bar}.py`, `docs/knowledge/{brain-flags-tuning-2026-09,samantha-bar,memory-loss-audit-2026-10-05}.md`
