# Flue deep dive + agent-runtime field comparison (2026-10-03)

Research date: 2026-10-03. Nothing on the box was modified. The live brain store was copied to a
scratch directory and queried there, and no service was touched.

Evidence labels:
- **[src]**: checked in installed source, packaged docs or `.d.ts`.
- **[store]**: measured on a copy of the live `labs/flue-zoe-brain-2x/data/zoe-brain.db` (last write 2026-09-30 11:44).
- **[doc]**: official upstream docs, changelog or release notes.
- **[2nd]**: secondary source, not checked against a primary.
- **[inf]**: my inference.

Fixed by owner rule and not reopened here: the Gemma 4 E4B brain, local-first/private, and
"nothing slows the hot path without a measurement plan".

Companion reports:
- [samantha-context-engineering-2026-09-29.md](samantha-context-engineering-2026-09-29.md) covers the *memory layer* field (Letta, Mem0, Zep, Honcho, ChatGPT/Claude memory).
- [zoe-context-audit-2026-09-29.md](zoe-context-audit-2026-09-29.md) covers the prompt order per lane.

This report covers the *runtime*: the agent loop, turn lifecycle, storage, interruption and budgets.

---

## TL;DR

1. **Interrupted turns are never aborted.**
   - When zoe-data stops reading (barge-in, disconnect, or a B1.1 speculative cancel), the sidecar only unsubscribes. `src/streaming.ts` `cancel()` documents this: "The turn itself keeps running to completion inside Flue".
   - Flue ships a durable `POST /agents/zoe/:id/abort`, yet **0 of 4,620 stored submissions ever recorded an abort** [store].
   - llama-server runs **one** 8,192-token slot (`--parallel 1`). An abandoned generation therefore holds the GPU slot while the user's next turn waits behind it.
   - The full unheard reply is persisted as if it had been said.
2. **The voice agent runs on Flue's default durability budget: 10 attempts and one hour per submission.**
   - Every stored submission has `max_attempts=10` and `timeout_at − accepted_at = 3600 s` [store].
   - After a sidecar restart, Flue's startup reconciliation re-runs interrupted work *ahead of* newly delivered work. For a voice turn, minutes-old work is worthless.
   - One static (`Zoe.durability`) fixes this, at no hot-path cost.
3. **The store has no retention, and upstream has no deletion API.**
   - The persistence docs say: "Sessions are append-only for the life of the agent instance; the contract has no per-session deletion" (`docs/reference/data-persistence-api.md`) [src].
   - The live store is 145 MB: 1,432 conversations and 4,620 submissions.
   - **About 98 % of the submissions are harness traffic** (1,080 `bar-*`, 203 `replay-*` and 48 `umab-*` conversations, among others). Only 98 submissions are voice or Telegram [store].
4. **Flue 2.2.2 is now the latest release.**
   - 2.2.0 final shipped on 2026-09-28; the 09-26 ecosystem note saw only `2.2.0-next`.
   - The public surface, the store schema (`FLUE_FORMAT_VERSION = 1`) and the `instrument()` API are unchanged. There is **no store wipe and no tap rewrite**.
   - The catch: it forces pi-ai from 0.83 to 0.87.1. pi-ai 0.86.0's `TranscriptContext` change breaks `capped-completions.ts`, and the `tools: []` cap strip would silently stop working.
   - Nothing in 2.2.x touches compaction, memory, retention, budgets or interruption. **Hold until a measured reason appears.**
5. **Our refusal of Flue's native compaction is now better supported.**
   - `src/context-window.ts` already gives three measured reasons. A fourth: the summary prompt is hard-coded for a coding agent ("AI coding assistant … Preserve exact file paths"), and neither 2.1.1 nor 2.2.2 lets us override it [src].
   - The field's consensus is to compact **off the hot path**: Anthropic background compaction, Pipecat's async summariser, Letta sleep-time, Mastra's Observer.
   - Zoe's nightly digest already does the memory half of that.
6. **What the field does that Zoe lacks, by impact:**
   - storing only what the user actually *heard* (LiveKit, Pipecat and OpenAI Realtime all truncate to it);
   - aborting the turn on barge-in;
   - bounded per-turn wall-clock and first-chunk budgets (Vercel AI SDK v7 `timeout`);
   - a durable, size-bounded store.

   In several places Zoe is *ahead* of the field (§5).

---

## 1. Flue as we use it today

**Versions [src]:**
- `@flue/runtime`, `cli` and `vite` are **2.1.1**. The installed copy is byte-identical to the 2.1.1 tarball.
- `@earendil-works/pi-ai` and `pi-agent-core` are **0.83.0**.

**Where Flue comes from.** Flue is `withastro/flue` (Apache-2.0). It is built on earendil-works'
pi: pi-ai supplies providers and the wire, and pi-agent-core supplies only the `Agent` loop class.
Flue 2.1.1 declares `pi-ai ^0.83.0`, which for a 0.x version means `<0.84.0`. So the pi pin is
forced by Flue, not chosen by us.

**How the sidecar is built.** It is about 4.9k lines of TypeScript with 30 test files:
- a Flue agent function (`src/agents/zoe.ts`);
- a custom pi provider (`src/providers/capped-completions.ts`) that wraps pi-ai's `openai-completions` stream and runs a **policy chain on every model call** (`applyPolicies`), in this order:
  1. strip the envelopes;
  2. elide stale blocks;
  3. add the user-model block;
  4. window the history;
  5. strip the coding built-ins;
  6. apply progressive disclosure;
  7. apply the iteration cap;
- streaming: a Hono middleware over in-process `observe()`, plus an `instrument()` interceptor tap for early text.

### 1.1 Feature matrix

Status key:
- **Used**: we use the Flue feature as designed.
- **Re-impl**: the capability is implemented outside Flue, at the sidecar wire or in zoe-data, for the reason noted.
- **Unused**: Flue has it and we don't use it.
- **Absent**: neither 2.1.1 nor 2.2.2 has it.

| Capability | Status | Where / evidence | Notes |
|---|---|---|---|
| Agent function, `useModel`, `useTool` | Used | `src/agents/zoe.ts` | The `'use agent'` directive is load-bearing and fails silently. Pinned by `agent_registration.test.ts` |
| Custom provider (`setProvider(createProvider)`) | Used | `capped-completions.ts` `createZoeProvider` | `contextWindow: 0` on purpose, because of pi-ai 0.83's output clamp (#1661) |
| Per-call context transform (windowing, disclosure, elision, user-model block) | Re-impl at the sidecar wire | `applyPolicies` | Flue exposes no per-step hook equivalent to `prepareStep`/`transformContext`. pi-agent-core *has* `transformContext`/`prepareNextTurn` [src], but Flue does not surface them. Conditional `useTool` exists, but Flue's docs warn it invalidates the prompt cache (`guide/tools.md`), and it would break execution of a tool called from an earlier round's schema |
| Per-turn tool-iteration cap | Absent upstream; Re-impl | `applyCap`, `ZOE_BRAIN_MAX_TOOL_ITERS=8` | The pi-agent-core loop is `while (true)`. Flue's `MAX_FOLLOWUPS=32` and `MAX_AGENT_FINISH_CYCLES=32` bound other things |
| Threshold compaction | Unused (deliberately off) | `useModel(…, { compaction: false })` | See §2.3 |
| Overflow recovery (compact-and-retry) | Present as a backstop | Flue internal | Cannot fire usefully on an 8k slot. Windowing keeps prompts under budget |
| Explicit compaction (`harness.compact()`) | Unused | — | Only compacts a harness scratch conversation [src] |
| Tool `timeoutMs` (2.1.0+) | Unused; Re-impl | `fetchSignal` = `AbortSignal.any([turn, timeout(8 s)])` in `zoe-tools.ts` | The native version gives a typed `ToolTimeoutError` that the model sees. Ours covers HTTP only |
| `durability` static (`maxAttempts`, `timeoutMs`) | **Unused**: defaults 10 / 3,600 s | [store] all 4,620 rows | Action A2 in §4 |
| Abort (`POST /:id/abort`, `abort()`) | **Unused** | [store] 0 `abort_requested_at`; no `/abort` caller in zoe-data | Action A1 in §4 |
| Turn-boundary joins / steering | Unused (implicitly) | zoe-data serialises a session's turns | The streaming latch documents its "two concurrent turns per session" limit |
| `observe()` runtime events | Used | `src/streaming.ts` | Text arrives up to 1 s late because of `CANONICAL_FLUSH_DELAY_MS = 1e3` |
| `instrument()` interceptor | Used | `src/early-text.ts` (#1761) | A supported seam. The tap reads pi-ai iterator steps |
| OpenTelemetry / Sentry / Braintrust integrations | Unused | — | Zoe logs `VOICE TIMING`, `FLUE_PROMPT_CACHE`, `FLUE_CONTEXT_BUDGET` and `FLUE_EARLY_TEXT` instead |
| Per-turn token usage (`turn.response.usage`) | Used | `prompt_cache` on the NDJSON terminal | llama-server timings ride along with it |
| `usePersistentState` | Unused | — | Identity and other turn-scoped data are keyed per turn on the `AbortSignal` (see §5) |
| `useAgentStart` / `useResponseStart/Finish` | Unused | — | `useAgentStart` is *awaited* before the model runs. Our user-model fetch runs in the background only (`user-model.ts`), which is better for time to first token |
| `useInitialData` | Unused | — | It is fixed when the instance is created, but voice identity can change per turn, so the ` zoe-uid:` envelope stays |
| `useDataWriter` (structured UI data) | Unused | — | The `__TOOL__`/`__THINKING__` sentinels over NDJSON are pinned to zoe-data's parsers |
| Subagents (`useSubagent`, `task` tool) | Unused; the `task` tool is stripped | `stripCodingBuiltins` | Each delegation would be a fresh-context Gemma run on the single slot |
| Skills (`useSkill`, SKILL.md) | Unused | — | Tools are the right primitive for a 4B voice brain |
| Sandbox, MCP | Unused | — | Without a sandbox there are no filesystem tools (2.x) |
| Structured output (`harness.prompt({ result })`) | Unused | — | Tool arguments are already Valibot-validated |
| Schedules / `dispatch()` | Unused | zoe-data APScheduler and the 03:00 digest loop | Correct as is: scheduling belongs in zoe-data |
| Evals (vitest-evals blueprint) | Unused | parity/, the replay gate, the Samantha bar | Flue's evals are just Vitest tests; there is no eval framework [src] |
| SQLite persistence (`sqlite()`) | Used | `src/db.ts` | No retention. 145 MB [store] |
| Durable stream read (`GET /:id`) | Unused for voice | `streaming.ts` header | Reads back from storage, which is too slow for time to first token |
| Channels (`@flue/telegram`) | Unused | `labs/flue-zoe-telegram-2x` is grammY → zoe-data `/api/chat` | Its Flue agent is a pass-through; Flue only hosts it |
| Long-term memory primitive | **Absent** upstream | MemPalace (zoe-data), via the `recall_memory` / `remember_*` tools | — |
| Retention / TTL / per-session delete | **Absent** upstream (2.1.1 and 2.2.2) | — | Action A4 in §4 |
| Tool-result caching; model routing or fallback chains | **Absent** upstream | zoe-data `brain_dispatch` fails over to the core lane | — |

### 1.2 What zoe-data injects that a runtime could own

`services/zoe-data/zoe_flue_client.py` (1,699 lines) folds two kinds of content into the **user
message**:
- **envelopes**: ` zoe-spec:`, ` zoe-replay:`, ` zoe-uid:`;
- **context blocks**: the `[MEMORY CONTEXT]` recall floor, `[PENDING CONTACT OFFER]`, continuity, `[Today …]` and `[RAISE …]`.

The user-model card is the exception. The sidecar pulls it itself and appends it to the **system
prompt** (`user-model.ts`, #1783/#1792).

**How other frameworks place the same content:**
- LiveKit uses `on_user_turn_completed`: ephemeral RAG injection with its own measured delay.
- Pydantic AI and Mastra use history/input processors.
- Flue's closest equivalent is `useAgentStart` plus instructions. That hook is awaited before the model runs, and it would change the system prompt every turn, which kills the prefix cache.

**Decision:** keep the blocks in the user message, with wire-side elision of stale copies (#1785).
That is the cache-correct choice, and not moving them is deliberate.

---

## 2. Upgrade path and what newer Flue gives us

### 2.1 Timeline [doc]

| @flue/runtime | Date | Relevant content |
|---|---|---|
| 2.1.0 | 2026-09-18 | Tool `timeoutMs`, MCP annotations, trace `contentBudgetBytes` |
| **2.1.1 (ours)** | 2026-09-23 | OTel `gen_ai.tool.call.*` attributes, linear trace truncation |
| 2.2.0 | 2026-09-28 | Bounded history reads (`history({limit})`, `historyBefore`, `observe({limit})`, `?view=history`); PDF `document` attachments; a server `timestamp`; **pi → 0.87.1** |
| 2.2.1 / **2.2.2 (latest)** | 2026-09-28 | Model-ID `-`/`.` shim; Cloudflare AI Gateway IDs |

As of 2026-10-03:
- Upstream has no commits since 2.2.2 and no pending changesets.
- pi has moved on to 0.99.x and then **1.0.1** (2026-10-03). pi-agent-core 1.0.0 *removed* its experimental harness, sessions, compaction and durable runtime.
- **No released Flue accepts pi ≥ 0.88.** A future Flue built on pi 1.x will therefore be a bigger break than 2.2.

### 2.2 Measured upgrade risk, item by item

| Risk surface | 2.1.1 → 2.2.2 | Evidence |
|---|---|---|
| **Store schema / one-way boundary** | **None.** `FLUE_FORMAT_VERSION = 1` and fold-checkpoint format `3` in both versions, and the DDL is identical. Unlike 1.x→2.x, no wipe is needed and rollback is possible | [src] `ddl-2.1.1` = `ddl-2.2.2`; [store] `flue_meta.format_version = '1'` |
| **`src/db.ts` reservation** | None. `PersistenceAdapter`, the three store interfaces and the `./adapter` exports are unchanged. `db.ts` stays reserved by the Vite build | [src] `.d.ts` region diff |
| **#1761 early-text tap** | Low. `instrument`, `FlueInstrumentation`, `FlueExecutionInterceptor` and every event type are unchanged. The tap's one private dependency is the *shape* of pi-ai iterator steps (`{done:false, value:{type:'text_delta', delta}}`), which pi 0.85.0 "normalised to standard event sequences". A change there would be caught by `test/early_text.test.ts`, which runs the **real runtime** with a negative control (a stall of at least 0.8 s). `CANONICAL_FLUSH_DELAY_MS` is still private | [src], plus the test header |
| **Provider wrapper (`capped-completions.ts`, `context-window.ts`)** | **High.** From pi-ai 0.86.0, `ProviderStreams.stream/streamSimple` receive `TranscriptContext = { messages }`, and the system prompt and tools travel as transcript system messages (read via `getCurrentSystemPrompt()`/`getCurrentTools()`). `applyCap`'s `tools: []`, `estimateContextTokens`, disclosure and the user-model suffix all read or write `context.systemPrompt`/`context.tools`. The cap strip would become a **silent no-op**, and the type-check may not catch every site | [doc] pi-ai CHANGELOG 0.86.0; ecosystem-watch §4 |
| Removed record field | `toolAddition` was dropped from a resource-snapshot record | [src] `conversation-records.d.ts` |
| SDK behaviour | `readSubmissionReply()` now requires `settlements`. zoe-data does not use the SDK | [doc] |
| pi 0.84–0.87 behaviour to replay-gate | `toolChoice` on openai-completions; `supportsFinishReason`; strict tool schemas no longer sent to unknown endpoints (#9816, the one llama.cpp fix that reaches Flue); bodyless 400/413 no longer misread as overflow; **a fix for quadratic `EventStream` drain CPU (0.86.0)** | [doc] |

**What 2.2.x would buy Zoe:**
- bounded history reads: irrelevant, because we never read history over HTTP;
- PDF attachments: replaced by a placeholder on openai-completions anyway;
- timestamps;
- the pi fixes above. Only the `EventStream` CPU fix plausibly affects the hot path [inf], and that is measurable.

**Verdict: hold on 2.1.1.** Upgrade only when one of these is true:
- a pi fix is measured to matter on the Orin; or
- a Flue release touches retention, interruption or compaction.

The port recipe (TranscriptContext plus a cap negative control) is already written in
`docs/knowledge/ecosystem-watch-2026-09-26.md` §4. That note's "2.2.0 is `next`-only" line is now
stale: 2.2.0 through 2.2.2 are final releases.

### 2.3 Native compaction compared with our windowing and elision

**How Flue's compaction works [src]:**
- **Trigger:** it fires when `contextTokens > contextWindow − reserveTokens`. Defaults are `reserveTokens = min(20000, maxTokens)` and `keepRecentTokens = 8000`.
- **Inert for us:** the trigger returns false when `contextWindow <= 0`. Our `contextWindow: 0` declaration therefore disables it on top of `compaction: false`.
- **Cost:** each compaction makes two parallel model calls (history and `compaction_prefix`).
- **Prompt:** the summary prompt is hard-coded and shaped for a coding agent ("## Goal / Constraints & Preferences / Progress / Key Decisions / Next Steps / Critical Context … Preserve exact file paths"). There is no `instructions` override, unlike Anthropic's `compact_20260112`.

**The decision stands.** `context-window.ts` already gives three measured reasons:
1. compaction cannot rescue a session that is already over the wall;
2. it adds one or two Gemma calls at unpredictable boundaries;
3. its defaults cannot converge on an 8k window.

Add a fourth: **the summary would be written for a coding agent.**

**The field reaches the same answer from the other side: never compact on the hot path.**

| Framework | How it keeps compaction off the hot path |
|---|---|
| Anthropic (`compact-2026-09-04`) | Background compaction swaps the summary in for exactly the first N messages sent |
| Pipecat | Auto-summarisation runs "asynchronously in the background, without blocking the pipeline" |
| Letta | Sleep-time / "Dreaming" agents |
| Mastra | Observer/Reflector at token thresholds, with async pre-buffering |
| Hermes | Clears old tool results first (no LLM call), then summarises with an auxiliary model |

**Zoe's equivalents today:**
- drop-only windowing: deterministic, zero model calls, anchor-quantised for the KV prefix cache;
- stale-block elision (#1785; live, `ZOE_BRAIN_ELIDE_STALE_BLOCKS=1`);
- the 03:00 `run_nightly_digest_pass` (fact extraction plus supersede), which is our "dreaming".

**What is missing:** only an *in-session* summary of windowed-out turns. That is the low-ranked
action A7, and it is measure-first. Voice sessions roll over on an idle TTL
(`voice_tts._get_or_create_voice_session`), so windowing rarely engages on voice [inf, to measure].

---

## 3. Field comparison

Versions as of 2026-10-03:

| Framework | Version |
|---|---|
| Pipecat | 1.12.0 |
| livekit-agents | 1.8.4 |
| Pydantic AI | v2 (2.0 on 2026-06-23) |
| Vercel AI SDK | v7 (2026-06-25) |
| LangGraph | 1.x |
| OpenAI Agents SDK | Python, 2026 |
| Mastra | `@mastra/memory` 1.1 |
| Letta | Server 0.16.x / Letta Code 0.3x |
| Hermes Agent | v0.21.5 |
| pi | v1.0.0, now `earendil-works/pi` |

| Framework | The 2–3 pieces that matter for a local voice companion | Zoe equivalent | Verdict |
|---|---|---|---|
| **LiveKit Agents 1.x** | (1) Truncates history to **what was heard**: `synchronized_transcript` is aligned to the playback position and the message is stored as `ChatMessage(interrupted=True)` [doc]. (2) `preemptive_generation` (LLM only; TTS off by default; `max_retries` 3), plus **false-interruption resume** (`false_interruption_timeout` 2 s) [doc]. (3) An `on_user_turn_completed` RAG hook whose cost is measured as `on_user_turn_completed_delay`, and a local `v1-mini` audio turn detector (~0.5 GB) [doc] | (1) **None**: the full generated reply is stored and the turn is never aborted. (2) The B1.1 speculative turn (flag-dark), which *also defers side effects*, something LiveKit does not do. No false-interruption resume. (3) Seam blocks plus `VOICE TIMING` (`pre_brain_ms`, `memory_packet_ms`); Smart Turn on the LiveKit path, flag-dark (`ZOE_SMART_TURN_ENABLED=0`) | Copy (1) and the false-interruption idea |
| **Pipecat 1.12** | (1) Assistant context is built from `TTSTextFrame`s on the playback clock, so it holds spoken text, not generated tokens. This is still buggy upstream (#4466, #5305, #4111) [doc]. (2) **Background** auto-summarisation (`max_context_tokens` 8000, `target_context_tokens` 3000, a separate `llm`) [doc]. (3) Smart Turn v3.2 (8 MB, about 10–12 ms on CPU) and a per-turn `LatencyBreakdown.contributions` [doc] | (1) None. (2) Nightly digest only. (3) `VOICE TIMING` plus the `panel-ttfa-breakdown` method, which is equivalent and more rigorous (a two-clock join) | Copy (1). Do (2) only after measuring |
| **OpenAI Realtime / Agents SDK** | (1) `conversation.item.truncate {audio_end_ms}`, the canonical "remember only what was heard" contract. The docs admit the alignment is approximate [doc]. (2) Guardrails with tripwires; Realtime output guardrails are **debounced** over streamed text [doc]. (3) `max_turns` with `error_handlers["max_turns"]` returning a graceful reply; `result.cancel(mode="immediate"\|"after_turn")` [doc]. The cascaded `VoicePipeline` has **no** built-in interruption handling [doc] | (1) None. (2) The prompt-confidentiality doctrine plus `untrusted-content.ts` fencing; no streaming output guard. (3) `applyCap` returns a real assistant message when the cap is hit, which is the same graceful-exit behaviour | (1) is the contract to copy |
| **Vercel AI SDK v7** | (1) `prepareStep` returns `activeTools`, `messages` and `model` per step [doc]. (2) **First-class timeouts** `{ totalMs, stepMs, firstChunkMs, chunkMs, toolMs }` [doc]. (3) `stopWhen: isStepCount(20)`; a durable `WorkflowAgent`; GenAI spans via `@ai-sdk/otel` [doc] | (1) The same thing at the wire (`applyPolicies`), but **cache-stable** (sticky and append-only), which `prepareStep` is not by default. (2) Partial: an 8 s tool HTTP timeout, a 180 s middleware timeout and voice filler racing (#1106), but **no first-chunk or inter-chunk deadline in the sidecar**. (3) The cap of 8 | Copy (2) |
| **Pydantic AI v2** | (1) `UsageLimits` (`request_limit`, `tool_calls_limit`, token limits) [doc]. (2) First-party cancellation: `CancellationToken`, and `RunCancelled.from_cancellation()` returns **resumable history** [doc]. (3) Capability hooks at run, node, model-request and tool level; pydantic-evals with span-based trajectory evaluators [doc] | (1) The cap of 8. (2) None (see A1). (3) The wire chain; evals are the replay corpus, the Samantha bar and the parity suite | A pattern for A1: what history remains after a cancel |
| **LangGraph / LangChain 1.x** | (1) A checkpoint per super-step, with `durability="exit"\|"async"\|"sync"` per call [doc]. (2) Middleware: `SummarizationMiddleware(trigger, keep)`, `ContextEditingMiddleware`, and `ModelCallLimitMiddleware`/`ToolCallLimitMiddleware(exit_behavior)` [doc]. (3) LangMem `ReflectionExecutor(after_seconds=N)`: **debounced** extraction once the conversation settles [doc] | (1) Flue durable submissions. (2) Windowing, elision and the cap. (3) The per-turn background extractor plus the nightly digest | Nothing missing that matters |
| **Claude Agent SDK / API** | (1) `clear_tool_uses_20250919` clears old tool results (`keep` 3), paired with a pre-clear warning from the memory tool [doc]. (2) **Background compaction** (`compact-2026-09-04`) [doc]. (3) Hooks: `PreToolUse` (`allow/deny/ask/defer`), `PostToolUse`, `PreCompact`, and async fire-and-forget hooks [doc] | (1) #1785 elides stale *injected blocks*, but old **tool results** are not cleared; they only age out by windowing. (2) None in-session. (3) The write gate `ZOE_BRAIN_ALLOW_WRITES`, the intent-dispatch token gate and B1.1 deferral | (1) is a cheap candidate (A8) |
| **Mastra** | (1) Working memory (template or schema, resource-scoped) plus semantic recall [doc]. (2) **Observational memory**: an Observer (at 30k tokens) feeds a Reflector (at 40k tokens), giving a stable, cacheable observation log [doc]. (3) Processors including `TokenLimiter`, `ToolCallFilter` and a `TripWire` abort [doc] | (1) The user-model card (#1792) plus `recall_memory`. (2) The nightly digest and supersede pass. Thresholds sized for 128k-context models do not transfer to an 8k slot [inf]. (3) The wire chain | Already covered by the memory plan |
| **Letta** | (1) Memory blocks pinned in the system prompt; in 2026, "MemFS" git-backed memory files [doc]. (2) **Sleep-time / Dreaming agents** own memory edits [doc]. (3) Tool rules (`InitToolRule`, `TerminalToolRule`, `MaxCountPerStepToolRule`), and `self_compact_*` modes that keep the prompt cache valid [doc]. The `voice_convo_agent` type is now labelled "legacy" [doc] | (1) The user-model card (byte-stable, version-hashed). (2) The 03:00 digest (`memory_digest.py` cites "DREAMING MEMORY", arXiv 2604.20943). (3) The activator doctrine, `activate_abilities` and the cap | Equivalent; see the memory report |
| **Hermes Agent** | (1) MEMORY.md/USER.md with hard caps that **error when full**, which forces consolidation. Both are frozen at session start for cache stability [doc]. (2) A context compressor that replaces tool results with placeholders first, then summarises with an auxiliary model [doc]. (3) FTS5 session search, then a summary of the top hits [doc] | (1) The user-model card (≤1,400 chars), bound per turn. (2) Windowing and elision. (3) MemPalace recall | Retired here as a runtime; its patterns are already absorbed |
| **pi (our substrate)** | (1) `agent.abort()`. An aborted pi-ai message keeps the **generated** partial (`stopReason:"aborted"`) [doc]. (2) `steer`/`followUp` queues, and the `finishTurn`/`prepareRequest` hooks (0.87) [doc]. (3) Session `context_edit` entries, which replace or omit an entry for *future model context* only. That is the native primitive for heard-text truncation [doc] | Flue 2.1.1 has **no `context_edit` record type** (no match in dist) [src]. It does have an aborted-partial "continuation" upgrade used in recovery [src] | Truncation has to be done at our wire (A3) |

**The common pattern [inf]:**
- Every mature *voice* runtime treats an interruption as an edit to history ("the user heard up to X"), not just as stopping audio.
- Every mature *agent* runtime does memory work and compaction off the hot path.
- Every mature agent runtime also bounds its loops with explicit budgets that end in a graceful reply.

Zoe has the second and third. It lacks the first.

---

## 4. Ranked action list

Ranking is by (user-visible gain × confidence) ÷ (risk × effort). Every item names its hot-path
effect and how to measure it. The AGENTS.md replay gate applies to anything on the voice path.

| # | Change | Gain | Risk | Hot-path impact & how to measure | Effort |
|---|---|---|---|---|---|
| **A1** | **Abort the Flue turn when its consumer goes away.** In `streaming.ts` `cancel()`, and on the B1.1 speculative-cancel path, record a durable abort for the latched operation through `POST /agents/zoe/:id/abort` or the in-process handle. Put it behind a kill switch (e.g. `ZOE_FLUE_ABORT_ON_CANCEL`) | Frees the single llama slot immediately, so the next turn no longer queues behind an unheard reply. The speculative "prefix retained" residual (b1-speculative-turn-start.md, Residuals) shrinks to an aborted partial | Aborting during a **write** tool makes Flue settle that call with an unknown-outcome error (`durability.md`). Writes are already gated or deferred, but this needs a test. llama-server stops a streaming generation when its client goes away (`server-context.cpp` `should_stop` in a source checkout; **not yet verified on the live build**). Also unverified: what Flue puts in the model's context after an aborted partial. Check that first in the lab | **Removes latency.** Measure with the barge-in replay (the `test_voice_barge_in` corpus plus a scripted interrupt 1 s into a long reply): compare the next turn's `brain_ttft_ms` and `FLUE_PROMPT_CACHE` with and without the change. Negative control: with the flag off, the queueing must show. Count `abort_requested_at` in the store | S |
| **A2** | **Set `Zoe.durability = { maxAttempts: 2, timeoutMs: 120_000 }`**, aligned with the 180 s seam timeout | A sidecar crash or restart can no longer replay a stale voice turn **ahead of** the live one for up to an hour. (Startup reconciliation preserves order and runs recovered work first.) | Very low. Recovery semantics are unchanged; they are only bounded | **None** in normal operation: the static is read while the agent is not running. Measure in the lab: kill -9 mid-turn, restart, and check that the recovered submission settles `failed` within the budget and does not delay the next turn. In the store, new rows should show `max_attempts=2` | XS |
| **A3** | **Heard-text truncation.** The panel daemon knows which TTS sentence chunks reached `aplay` before the barge-in, so zoe-data can learn the played prefix. It forwards that on the *next* turn's envelope (` zoe-heard:<chars>`) or posts it to a sidecar endpoint keyed by session. `applyPolicies` then trims the interrupted assistant message on the wire, the way `elideStaleBlocks` works. The store stays append-only; Flue has no `context_edit` | The model stops believing it said things the user never heard. This is the LiveKit, Pipecat and Realtime contract | Alignment is sentence-level only (Pipecat's word-timestamp version is still buggy upstream). It must not break the prefix cache: the trimmed message is the *last* assistant turn, so only the tail changes [inf] | A string operation on the wire, about 0 ms. Measure with a Samantha-bar scenario ("interrupt mid-answer, then ask 'what were you saying?'") and confirm `FLUE_PROMPT_CACHE` `f_keep` is unchanged | M |
| **A4** | **Store retention by isolation and rotation.** (a) Point harness runs at a **throwaway** store: `bar-*`, `umab-*`, `par-*`, `bf-*` and the other harness prefixes are 96–98 % of rows. Use a second sidecar instance with `ZOE_BRAIN_DB` on scratch, or Flue's in-memory default. The replay gate stays on the live instance, because it must exercise the real path. (b) Rotate the live file at 03:00, next to the digest: drain, stop, move the file aside, start, and delete old files after N days. Long-term memory lives in MemPalace, so only in-session history is lost (and Telegram already has `/new`). (c) Do **not** hand-delete rows; that is outside the contract | Bounded disk, a faster cold start and startup reconciliation, and no harness transcripts mixed in with real ones | (b) resets live sessions mid-conversation unless they are drained first. Schedule it for idle time, after the digest pass | None per turn, because per-session reads are indexed by `path`. Measure the sidecar's cold-start-to-`/health` time and the first turn's `brain_ttft_ms` before and after a rotation, and track store size over time | S |
| **A5** | **Sidecar-side first-chunk and inter-chunk deadlines**, like AI SDK v7's `firstChunkMs`/`chunkMs`. If the model produces no delta within X ms of `turn_request`, or stalls for Y ms mid-reply, abort the turn and emit `{"error"}` so the seam falls over or fills | Turns a wedged llama slot into a fast, honest failure instead of a 180 s wait | False aborts under heavy prefill: a cold re-prefill of 2.4k tokens measured about 0.6–1.5 s. Set X from the `FLUE_PROMPT_CACHE` distribution, not a guess | None when healthy. Take the distribution of first-delta ms from the `FLUE_EARLY_TEXT` lines and set X ≥ 2 × p99.9. Negative control: a stalled mock model | S |
| **A6** | **Hold Flue at 2.1.1.** Re-evaluate when a Flue release touches interruption, retention or compaction, or when the pi `EventStream` CPU fix is measured to matter. When upgrading, do the `TranscriptContext` port with a cap negative control (the cap must still trip) | Avoids a high-risk provider rewrite for features we don't use | — | n/a. When upgrading: the replay gate, the real-runtime control in `early_text.test.ts` and the Samantha bar | M (when done) |
| **A7** | **An idle-time, in-session summary of windowed-out turns** (the off-hot-path compaction pattern). Do this only if measurement shows windowing actually drops content in real sessions | Within-day continuity in long Telegram and chat sessions | It is a Gemma call on the single slot, so it must run only when idle and be abortable when a turn arrives | Measure first how often `windowHeadIndex` returns non-null on real (non-harness) sessions, by adding a counter to `FLUE_CONTEXT_BUDGET` | M |
| **A8** | **Clear stale tool results on the wire** (as Anthropic's `clear_tool_uses` and Hermes's first phase do): replace tool-result bodies older than the last K turns with a one-line placeholder, the same way #1785 handles injected blocks | A smaller history, and less recall-packet noise replayed every turn | Prefix-cache churn if the boundary slides every turn. It must be anchor-quantised like windowing | First measure the bytes involved (extend `promptSections` to report them, as it does for stale blocks), then A/B `f_keep` | S–M |
| **A9** | **Local OTel via `@flue/opentelemetry`**, through `instrument()`, with a local-only collector for privacy | Spans per model call and per tool, without hand-written log lines | The interceptor runs on every iterator step, i.e. per delta, and the dependency adds weight on the Orin | Measure the interceptor overhead using the timing from the `early_text` real-runtime test. Adopt only if the overhead is under 0.1 ms per delta | S–M |

**Explicitly not recommended:**
- **Flue native compaction**: see §2.3.
- **Flue subagents**: each is a fresh-context Gemma run competing for the one slot.
- **Flue skills on the voice brain**.
- **`useAgentStart` for context**: it is awaited on the hot path, and our background fetch is strictly faster.
- **`usePersistentState` / `useInitialData` for identity**: identity changes per turn, not per instance.
- **Flue's eval blueprint**: the replay corpus, the Samantha bar and the parity suite are stronger.
- **Moving zoe-data's seam blocks into the system prompt**: it breaks the prompt cache.

### Top 5

1. A1: abort on cancel.
2. A2: durability budget.
3. A3: heard-text truncation.
4. A4: store isolation and rotation.
5. A5: first-chunk and stall deadlines.

---

## 5. What Zoe does better than the frameworks

**Prompt-prefix stability is engineered, not accidental.** Several pieces combine here:
- windowing is anchor-quantised, so the window head never moves mid-turn (`windowHeadIndex`);
- tool disclosure is session-sticky and append-only (the tool block sits inside Gemma's system turn);
- when the tool cap is hit, the wrap-up note is appended as a trailing user message instead of mutating the system prompt;
- the user-model block is version-hashed and bound per turn to its `AbortSignal`.

On a cache hit, only 11–78 tokens are re-prefilled against a cached prefix of about 2.8k tokens
(`FLUE_PROMPT_CACHE` lines, 2026-09-28/29). By contrast, the field's per-step tool switching
(`prepareStep`/`activeTools`, Flue's conditional `useTool`) invalidates the cache by default, and
Flue's own docs say so.

**Early text arrives ahead of the runtime's own storage flush (#1761).** This is a supported-seam
tap with a real-runtime negative control. It removed a ~1 s stall that the runtime imposes on
every consumer of `observe()`.

**Speculative turns gate side effects, not just audio (B1.1).** LiveKit's `preemptive_generation`
and Pipecat's eager end-of-turn only run the LLM speculatively. Zoe's gate also holds every write
until the verdict, and fails closed on an unknown turn id.

**Upstream defaults were rejected on measurement, with executable controls:**
- compaction: three measured reasons;
- pi-ai 0.83's output clamp (#1661): reproduced three times, with the arithmetic proof that no additive constant fixes it;
- sampling temperature: moving from 0.7 to 0.5 cut MTP fork-point glitches from 5/128 to 0/60.

**Trust boundaries fail closed:**
- identity, replay isolation and speculative-turn ids are bound per turn on the `AbortSignal` that pi-agent-core threads through to every tool, proven by a concurrency test;
- writes are dry-run unless `ZOE_BRAIN_ALLOW_WRITES` is set;
- bearer auth sits in front of every agent route;
- coding built-ins are stripped unconditionally.

**Evaluation is voice-specific.** Zoe has a said-vs-did replay gate on the owner's real-voice
corpus, a Samantha bar, a parity suite and a two-clock TTFA breakdown. None of the frameworks
surveyed ships an eval that runs on the user's own voice.

**Memory work already runs off the hot path.** There is per-turn background extraction, the 03:00
digest-and-supersede ("dreaming") pass, and the nightly user-model card. This is the Letta
sleep-time pattern, built without a second always-on agent competing for the GPU.

---

## 6. Sources

**Local evidence**
- `labs/flue-zoe-brain-2x/src/**`: agents/zoe.ts, providers/capped-completions.ts, context-window.ts, context-blocks.ts, user-model.ts, early-text.ts, streaming.ts, speculative-turn.ts, tools/*
- `labs/flue-zoe-telegram-2x/src/brain.ts`
- `services/zoe-data/zoe_flue_client.py`
- `services/zoe-data/voice_speculation.py`
- `services/zoe-data/memory_digest.py`
- `services/zoe-data/routers/voice_tts.py`
- `docs/architecture/b1-speculative-turn-start.md`
- `docs/knowledge/panel-ttfa-breakdown-2026-09-28.md`
- `docs/knowledge/ecosystem-watch-2026-09-26.md`
- `scripts/setup/systemd/llama-server.service`

**Installed Flue 2.1.1 docs and source** (`node_modules/@flue/runtime/docs/{guide,reference}`)
- `durability.md`, `agent-hooks.md`, `models.md` (Compaction), `tools.md`
- `data-persistence-api.md` (no per-session deletion)
- `observability.md`, `evals.md`, `subagents.md`
- `dist/conversation-stream-store-*.mjs`, `dist/dispatch-*.mjs`

**Live store snapshot** (copied 2026-10-03 22:25 AWST to scratch)
- Table counts, `flue_meta`, submission `max_attempts` / `timeout_at` / `abort_requested_at` / `attempt_count`, session-key prefixes.

**Zoe logs**
- `~/.zoe-logs/zoe-data.{stderr,app}.log`: `FLUE_PROMPT_CACHE` and `FLUE_CONTEXT_BUDGET` lines.

**Flue and pi**
- https://github.com/withastro/flue (releases `@flue/runtime@2.1.0` through `@2.2.2`)
- https://flueframework.com/
- https://registry.npmjs.org/@flue/runtime
- https://github.com/earendil-works/pi: `packages/ai/CHANGELOG.md`, `packages/agent/CHANGELOG.md`, `packages/agent/README.md`, `packages/coding-agent/docs/{compaction,session-format,rpc,extensions}.md`, `packages/durable/README.md`
- https://github.com/earendil-works/pi/releases/tag/v1.0.0

**LiveKit Agents**
- https://pypi.org/project/livekit-agents/
- https://docs.livekit.io/agents/build/turns/
- https://docs.livekit.io/agents/build/turns/turn-detector/
- https://docs.livekit.io/reference/agents/turn-handling-options/
- https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/
- https://docs.livekit.io/agents/build/nodes/
- https://docs.livekit.io/agents/build/tasks/
- https://docs.livekit.io/agents/multimodality/audio/background-audio/
- https://docs.livekit.io/agents/observability/data/
- https://github.com/livekit/agents/issues/5038

**Pipecat**
- https://raw.githubusercontent.com/pipecat-ai/pipecat/main/CHANGELOG.md
- https://docs.pipecat.ai/pipecat/migration/migration-1.0
- https://docs.pipecat.ai/pipecat/fundamentals/context-summarization
- https://docs.pipecat.ai/pipecat/learn/text-to-speech
- https://github.com/pipecat-ai/pipecat/issues/4466
- https://github.com/pipecat-ai/pipecat/issues/5305
- https://github.com/pipecat-ai/pipecat/issues/4111
- https://github.com/pipecat-ai/smart-turn
- https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/

**OpenAI**
- https://developers.openai.com/api/docs/guides/realtime-conversations
- https://developers.openai.com/api/reference/resources/realtime/client-events
- https://developers.openai.com/api/docs/guides/voice-latency-cost
- https://openai.github.io/openai-agents-python/running_agents/
- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/sessions/
- https://openai.github.io/openai-agents-python/realtime/guide/
- https://openai.github.io/openai-agents-python/voice/pipeline/

**Anthropic**
- https://platform.claude.com/docs/en/build-with-claude/context-editing
- https://platform.claude.com/docs/en/build-with-claude/compaction
- https://platform.claude.com/docs/en/build-with-claude/compaction-background
- https://code.claude.com/docs/en/agent-sdk/hooks

**Vercel AI SDK**
- https://ai-sdk.dev/docs/agents/loop-control
- https://ai-sdk.dev/docs/migration-guides/migration-guide-7-0
- https://ai-sdk.dev/docs/ai-sdk-core/settings
- https://vercel.com/changelog/ai-sdk-7

**Pydantic AI**
- https://pydantic.dev/docs/ai/core-concepts/agent/
- https://pydantic.dev/docs/ai/core-concepts/hooks/
- https://github.com/pydantic/pydantic-ai-harness
- https://pydantic.dev/docs/ai/evals/

**LangChain / LangGraph / LangMem**
- https://docs.langchain.com/oss/python/langchain/middleware/built-in
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://reference.langchain.com/python/langgraph/types/Durability
- https://langchain-ai.github.io/langmem/guides/delayed_processing/

**Mastra**
- https://mastra.ai/docs/memory/observational-memory
- https://mastra.ai/docs/agents/processors

**Letta**
- https://docs.letta.com/guides/agents/architectures/sleeptime
- https://docs.letta.com/guides/agents/compaction/
- https://docs.letta.com/guides/agents/tool-rules
- https://www.letta.com/blog/context-repositories

**Hermes Agent**
- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
- https://github.com/NousResearch/hermes-agent/releases
