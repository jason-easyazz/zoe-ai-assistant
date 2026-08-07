/**
 * Hard per-turn tool-iteration ceiling for the Flue Zoe brain (Flue 2.x).
 *
 * THE BUG THIS FIXES: the Flue HTTP agent route runs pi-agent-core's `runLoop`
 * directly — a `while (true)` that only exits when the model finally returns an
 * assistant message with NO tool calls (node_modules/@earendil-works/
 * pi-agent-core/dist/agent-loop.js). RE-VERIFIED ON 0.83.0 (2026-08-03): still
 * `while (true)`, still no `maxIterations` / `maxSteps` / `iterationLimit`
 * anywhere in its dist. Flue 2.0.1 keeps `MAX_FOLLOWUPS = 32`, but with the SAME
 * semantics as the beta — it bounds FOLLOW-UP PROMPTS on the result-tools path,
 * not tool-call rounds, and upstream's own comment calls it "a defense-in-depth
 * ceiling against pathological loops". Nothing in 2.x caps tool rounds per turn.
 * So a 4B Gemma with 11 tool schemas can still loop on tool calls forever → the
 * client times out at 120s. `ZOE_BRAIN_MAX_TOOL_ITERS` remains OUR code.
 *
 * THE FIX (supported seam, RE-SEATED FOR 2.x): the beta hooked this via
 * `registerApiProvider({ api, stream, streamSimple })` — a global wire-protocol
 * registry keyed by an `api` slug. That registry is DELETED in 2.x
 * (`registerProvider`/`registerApiProvider` appear nowhere in @flue/runtime@2.0.1's
 * dist, only in its migration guide). The replacement is Pi's own provider object:
 * `setProvider(createProvider({ id, auth, models, api }))`, where `api` takes the
 * very same `{ stream, streamSimple }` pair. So the WRAPPING TECHNIQUE is
 * unchanged — only the registration moved, and it moved somewhere better:
 *
 *   - the pair is bound to THIS provider, not to a process-global slug, so there
 *     is no last-write-wins registry and no idempotence guard to maintain;
 *   - the model can now declare `api: 'openai-completions'` honestly, so the beta's
 *     `asCompletionsModel()` slug-rewrite (which existed only to keep the built-in
 *     handler's URL-based compat detection working under a custom slug) is GONE.
 *
 * On every model call we count the tool-call rounds already taken this turn; once
 * the cap is reached we delegate with the tool list STRIPPED, so the model
 * physically cannot request another tool and MUST answer in plain text — which
 * makes the agent loop exit gracefully this turn. A hard, model-independent
 * ceiling that returns a real assistant message (no error, no hang).
 *
 * Cap is configurable via ZOE_BRAIN_MAX_TOOL_ITERS (default 8).
 *
 * This same wire seam also applies PROGRESSIVE TOOL DISCLOSURE (the port of
 * prod's services/zoe-core/extensions/abilities.ts pattern): before the cap,
 * `context.tools` is filtered to the always-on core plus the request's active
 * ability groups, so the 4B isn't carrying all 21 schemas every call. See
 * src/tools/tool-groups.ts. Policy order: disclosure first, then the cap (past
 * the cap, ALL tools are stripped regardless of disclosure).
 * ZOE_BRAIN_PROGRESSIVE_TOOLS=false disables disclosure for A/B comparison.
 *
 * LAB ONLY (production-reachable via ZOE_BRAIN_BACKEND=flue — prod quality).
 */
import { createProvider } from '@earendil-works/pi-ai';
// FLUE-API: built-in OpenAI-completions wire handlers. In pi-ai 0.79 these lived
// at the flat `@earendil-works/pi-ai/openai-completions` subpath and were named
// `streamOpenAICompletions` / `streamSimpleOpenAICompletions`. On 0.83 the flat
// specifier is GONE (the exports map is ".", "./compat", "./providers/*",
// "./api/*", "./oauth", "./bedrock-provider", "./bun-oauth") and every api module
// exports exactly `stream` and `streamSimple` — the uniform `ProviderStreams`
// shape (pi-ai dist/types.d.ts). Both re-verified present on 0.83.0.
import {
  stream as streamOpenAICompletions,
  streamSimple as streamSimpleOpenAICompletions,
} from '@earendil-works/pi-ai/api/openai-completions';
import type {
  Api,
  AssistantMessageEventStream,
  Context,
  Message,
  Model,
  Provider,
  ProviderStreams,
  SimpleStreamOptions,
  StreamOptions,
} from '@earendil-works/pi-ai';
import {
  bindTurnUserId,
  forwardedIdentityFromMessages,
  stripIdentityEnvelope,
} from '../request-identity.ts';
// .ts extension so the offline strip-types tests can resolve it (see zoe-tools.ts).
import {
  discloseTools,
  progressiveToolsEnabled,
  stripCodingBuiltins,
} from '../tools/tool-groups.ts';
import {
  contextWindowTokens,
  replyReserveTokens,
  windowContextToBudget,
} from '../context-window.ts';

/** Provider id the sidecar registers; the agent binds to `zoe/local`. */
export const ZOE_PROVIDER_ID = 'zoe';
/** Model id under that provider. The agent's specifier is `zoe/local`. */
export const ZOE_MODEL_ID = 'local';
/** The full model specifier `useModel()` takes. */
export const ZOE_MODEL_SPECIFIER = `${ZOE_PROVIDER_ID}/${ZOE_MODEL_ID}`;

const DEFAULT_MAX_TOOL_ITERS = 8;

/**
 * Per-turn tool-call ceiling. Read fresh each call (no module-load pinning) and
 * validated: Number('')/Number('abc') would be 0/NaN, so fall back to the default
 * for any non-positive / non-finite value.
 */
function maxToolIters(): number {
  const n = Number(process.env.ZOE_BRAIN_MAX_TOOL_ITERS);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_MAX_TOOL_ITERS;
}

/**
 * Tool-call rounds already taken in the CURRENT turn: assistant messages that
 * requested at least one tool since the last user message. Each round is one
 * assistant message with >=1 toolCall followed by its tool result(s), so this is
 * the iteration depth of the loop for this turn.
 */
export function toolIterationDepth(messages: Message[]): number {
  let depth = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === 'user') break;
    if (msg.role === 'assistant' && msg.content.some((c) => c.type === 'toolCall')) {
      depth += 1;
    }
  }
  return depth;
}

export const WRAPUP_NOTE =
  'You have already used several tools this turn and have reached the tool-call ' +
  'limit. Do NOT request any more tools. Give your best final answer now, in plain ' +
  'text, using what you already have.';

/**
 * Apply the cap: at or past the per-turn limit, strip the tools (and add a brief
 * wrap-up note) so the next model call cannot emit a tool call. Otherwise pass the
 * context through untouched.
 *
 * PREFIX-STABILITY (fixed 2026-08-03): the beta appended WRAPUP_NOTE to
 * `context.systemPrompt`. The system prompt is the very FIRST thing on the wire,
 * so mutating it per-round invalidated the llama-server KV prefix cache for the
 * ENTIRE prompt — every byte after the edit, i.e. all of it. The note is now a
 * trailing `user` message instead: everything after the last user message already
 * changes every round, so appending there costs no cache that was not already
 * cold, and the soul + doctrine + tool block prefix stays byte-identical.
 * (`tools: []` is what actually enforces the cap; the note is only steering.)
 * Ordering matters and is guaranteed by `applyPolicies`: the cap runs AFTER
 * disclosure and windowing, so this synthetic message can never feed the
 * keyword matcher in `activeGroups` or the budget maths in `windowContextToBudget`.
 */
export function applyCap(context: Context): Context {
  if (toolIterationDepth(context.messages) < maxToolIters()) return context;
  const note: Message = {
    role: 'user',
    content: WRAPUP_NOTE,
    timestamp: 0,
  };
  return {
    ...context,
    tools: [],
    messages: [...context.messages, note],
  };
}

/**
 * All wire-level policies for one model call, in order:
 *   1. prompt-fit history windowing (drop the OLDEST whole user-turn blocks so
 *      the assembled prompt always fits the model context — the fix for the
 *      unbounded-session 400/500 wedge; see src/context-window.ts). Runs FIRST
 *      so the current turn always survives whole and the iteration-depth count
 *      below is unaffected;
 *   2. strip pi/Flue coding built-ins (read/write/edit/bash/grep/glob/task) —
 *      UNCONDITIONAL safety floor, so a family voice brain is never handed
 *      bash/write/edit/task even with the disclosure kill switch off. NOTE: on
 *      2.x an agent without `useSandbox()` has no filesystem and no such tools
 *      at all (migration.md, "Sandboxes"), so the hazard this guards is gone
 *      upstream — the strip is kept as defence-in-depth against a future
 *      `useSandbox()` and costs nothing when the list is already clean;
 *   3. progressive tool disclosure (shrink the Zoe schemas the model sees to
 *      core + active groups) — also strips the coding built-ins, but step 2
 *      guarantees it regardless of ZOE_BRAIN_PROGRESSIVE_TOOLS;
 *   4. the iteration cap (past the cap, strip ALL tools so the turn must finish
 *      in plain text).
 *
 * DISCLOSURE BASIS (fixed 2026-08-03): steps 2-3 derive the active ability
 * groups from the PRE-WINDOW message list, not the windowed one. Deriving them
 * from the windowed list made the rendered tool block non-monotone — a group
 * activated by a tool call that later aged out of the window silently
 * retracted, so the tool section of the prompt oscillated between rounds and
 * turns. tool-groups.ts documents the set as growing monotonically per session;
 * windowing had quietly broken that. Passing the full basis restores it AND
 * makes the tool block a stable prompt prefix.
 */
export function applyPolicies(context: Context): Context {
  // Strip the acting-identity envelope BEFORE any other policy so the model — and
  // every downstream transform — only ever sees the human-authored message text.
  const clean = { ...context, messages: stripIdentityEnvelope(context.messages) };
  const windowed = windowContextToBudget(clean);
  const safe = stripCodingBuiltins(windowed);
  const disclosed = progressiveToolsEnabled()
    ? discloseTools(safe, clean.messages)
    : safe;
  return applyCap(disclosed);
}

/**
 * Bind the trusted acting identity for the current turn, keyed by the turn's
 * AbortSignal. Flue calls the provider on every model round of a turn — with that
 * turn's own `context.messages` and `options.signal` as plain arguments — and
 * pi-agent-core threads that SAME signal to every tool execution this turn (see
 * src/request-identity.ts). So we read the seam-forwarded id from this turn's
 * message envelope and bind it to `signal`; the tool reads it back by its own
 * `signal`, race-free across concurrent turns. Re-applied on every round
 * (idempotent). No signal (non-agent path) → no binding; tools fall back to env.
 * `applyPolicies` strips the envelope so the model never sees it.
 *
 * RE-VERIFIED ON 2.x — see the header of src/request-identity.ts. The 2.x render
 * model changed (the agent function re-renders per turn), but the AbortSignal
 * threading this depends on is a pi-agent-core property, and `signal` is still
 * on `ToolContext` (@flue/runtime types-*.d.mts). Proven adversarially by
 * test/request_identity_concurrency.test.ts, not assumed.
 */
export function bindIdentityForRound(context: Context, signal?: AbortSignal): void {
  bindTurnUserId(signal, forwardedIdentityFromMessages(context.messages));
}

const DEFAULT_TEMPERATURE = 0.5;

/**
 * Sampling temperature for every brain model call, matching the canonical prod
 * brain (services/zoe-data/zoe_agent.py pins 0.5). Without this, pi-ai sends no
 * temperature and llama-server's default (0.7) applies — hotter sampling that
 * measurably raises the MTP draft-acceptance token glitch ("I don'm …") at
 * "I'm"/"I don't" fork points: flue at 0.7 corrupted ~3.5% of fork-heavy
 * replies (5/128 pooled); prod at 0.5 was 0/74 and flue at 0.5 was 0/60.
 * Overridable via ZOE_BRAIN_TEMPERATURE; validated (finite, 0..2) with the
 * prod-parity default as fallback. Read per call, not at module load.
 */
export function brainTemperature(): number {
  // Number('') is 0, which would silently mean GREEDY sampling — treat an
  // empty/whitespace env as unset before validating.
  const raw = (process.env.ZOE_BRAIN_TEMPERATURE ?? '').trim();
  if (!raw) return DEFAULT_TEMPERATURE;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 2 ? n : DEFAULT_TEMPERATURE;
}

/**
 * Merge the brain temperature into the caller's options without clobbering an
 * explicitly-set one (pi may pass its own temperature in some flows; an
 * explicit caller value wins). Exported for the offline unit tests only.
 */
export function withBrainTemperature<T extends { temperature?: number }>(
  options: T | undefined,
): T {
  const merged = { ...(options ?? {}) } as T;
  if (merged.temperature === undefined) merged.temperature = brainTemperature();
  return merged;
}

function cappedStream(
  model: Model<Api>,
  context: Context,
  options?: StreamOptions,
): AssistantMessageEventStream {
  bindIdentityForRound(context, options?.signal);
  return streamOpenAICompletions(
    model as Model<'openai-completions'>,
    applyPolicies(context),
    withBrainTemperature(options),
  );
}

function cappedStreamSimple(
  model: Model<Api>,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  bindIdentityForRound(context, options?.signal);
  return streamSimpleOpenAICompletions(
    model as Model<'openai-completions'>,
    applyPolicies(context),
    withBrainTemperature(options),
  );
}

/**
 * The capped wire-protocol pair. In 2.x this is handed straight to
 * `createProvider({ api })` instead of being registered under a global slug.
 * Exported so tests can drive the policy chain through the real wire path.
 */
export const cappedApi: ProviderStreams = {
  stream: cappedStream,
  streamSimple: cappedStreamSimple,
};

/**
 * pi-ai 0.83.0's own output-clamp safety margin — `CONTEXT_SAFETY_TOKENS` in
 * node_modules/@earendil-works/pi-ai/dist/api/simple-options.js. It is a
 * module-private `const` with no export and no env knob, so the only way to
 * account for it is to mirror the number here. Re-check it on every pi-ai bump;
 * `test/output_budget_clamp.test.ts` imports the REAL `clampMaxTokensToContext`
 * from that dist file, so an upstream change to the FORMULA fails the suite —
 * but a change to this CONSTANT alone would only shrink the margin silently, so
 * the test also pins the value it observed.
 */
export const PI_AI_CONTEXT_SAFETY_TOKENS = 4096;

/**
 * The output budget every turn on this brain is meant to have — `model.maxTokens`
 * below, and the number `declaredContextWindow()` is sized to protect.
 *
 * IT IS THE REPLY RESERVE, not a separate constant (changed 2026-08-07; the port
 * declared a flat 2048). `src/context-window.ts` windows the prompt to
 * `contextWindowTokens() − replyReserveTokens()`, so the reserve IS the room
 * left inside llama-server's slot for the reply — asking for more than it is
 * asking for tokens the server has nowhere to put.
 *
 * WHY THAT MATTERS HERE, and it is a direct consequence of the fix below.
 * `clampMaxTokensToContext` exists upstream to stop a caller requesting more
 * output than the context can hold. `declaredContextWindow()` deliberately
 * defeats that clamp, so this deployment must honour the constraint itself or it
 * has removed a guard and put nothing in its place. Concretely: llama-server
 * runs `--ctx-size 16384 --parallel 2`, i.e. an 8192-token SLOT per lane, and
 * context shifting is OFF on this build (`get_can_shift()` is false whenever the
 * SWA and base caches differ in size, which they do without `--swa-full` — see
 * scripts/setup/systemd/llama-server.service). So generation that reaches the
 * end of the slot simply stops with `finish_reason: "length"`. A flat 2048
 * against a prompt at the full 6656-token budget would ask for 8704 tokens of
 * slot and be silently cut at 1536 — re-creating, from the other direction, the
 * exact truncation this PR exists to remove, and making the flip runbook's
 * zero-length-stop assertion unsound. Tying the cap to the reserve gives
 * `prompt + output ≤ (W − reserve) + reserve = W`: the request always fits.
 *
 * Not a regression against 1.x, which declared no `maxTokens` at all and was
 * bounded by the server's own slot: at a full prompt that bound is exactly this
 * reserve, and at a short prompt this is merely the more conservative of the two
 * (a 7000-token reply from a voice brain is a defect, not a feature).
 *
 * WINDOWING OFF is the one case that needs a fallback. `replyReserveTokens` is
 * clamped to half its argument, so `replyReserveTokens(0)` is 0 — which would
 * declare `maxTokens: 0` and, being falsy, drop the cap from the wire entirely.
 * With `ZOE_BRAIN_CONTEXT_WINDOW=0` there is no prompt bound and therefore no
 * `prompt + output ≤ W` to honour, so size the reserve against the default slot
 * instead: the operator still gets a sane, configurable reply cap in the
 * unguarded A/B mode rather than an accidental one.
 */
const SLOT_TOKENS_WHEN_WINDOWING_OFF = 8192;

export function outputBudgetTokens(): number {
  return replyReserveTokens(contextWindowTokens() || SLOT_TOKENS_WHEN_WINDOWING_OFF);
}

/**
 * The `contextWindow` this model DECLARES to pi-ai — deliberately NOT the real
 * llama-server window, and deliberately DECOUPLED from the windowing budget.
 *
 * THE BUG THIS FIXES (reproduced 3×, PR #1616): pi-ai 0.83.0 added
 * `clampMaxTokensToContext` (dist/api/simple-options.js), which every
 * openai-completions request now flows through via `buildBaseOptions`:
 *
 *     available = model.contextWindow − estimateContextTokens(context) − 4096
 *     maxTokens = min(maxTokens, max(1, available))
 *
 * That file does not exist in 0.79.10 (the 1.x lane), and the 1.x provider
 * declared no `contextWindow` at all, so nothing clamped there. Declaring the
 * REAL 8192 window here meant a ~4090-token prompt left `8192 − 4090 − 4096 ≈ 6`
 * tokens of output — replies truncated to 1-8 tokens with `stopReason: "length"`
 * (verbatim from the 2.x store: output 1, 3, 7, 8). Worse, pi-agent-core 0.83.0's
 * `failToolCallsFromTruncatedMessage` refuses to execute tool calls off a
 * length-stopped message and writes a `tool_outcome` with no
 * `tool_results_committed`, so the very next reduce throws
 * `ConversationRecordInvariantError` and kills the whole turn. Fixing the clamp
 * makes that second failure unreachable: no `"length"` stop, no refusal path.
 *
 * WHY DECOUPLING IS SAFE, AND WHY IT IS THE RIGHT LEVER. The original rationale
 * — "read from the SAME env knob the windowing budget uses so the two can never
 * drift" — is exactly what caused the bug, because under 0.83.0 this field is no
 * longer a description of the server's window; it is the INPUT to an output
 * clamp. The real prompt budget is enforced independently and earlier, by
 * `windowContextToBudget` in src/context-window.ts (budget =
 * `contextWindowTokens()` − `replyReserveTokens()`), which runs inside
 * `applyPolicies` on every model call. Flue's own threshold compaction is pinned
 * off, so nothing else reads this field.
 *
 * THE ARITHMETIC, stated as the invariant the test pins. With
 *
 *     declared = W + PI_AI_CONTEXT_SAFETY_TOKENS + outputBudgetTokens()
 *
 * where `W = contextWindowTokens()` is llama-server's slot size, the clamp
 * yields `available = W + outputBudgetTokens() − prompt`, so for ANY prompt that
 * would fit the server at all (`prompt ≤ W`) the clamp leaves at least the full
 * intended output budget, and `min(budget, available)` is the budget. The
 * guarantee therefore holds for the real tokenizer count, not merely for our
 * chars/4 estimate — a prompt big enough to break it would already have been
 * rejected by llama-server with a 400.
 *
 * The two halves fit together in one line, and this is the property the flip
 * runbook's zero-length-stop assertion rests on:
 *
 *     prompt ≤ W − reserve   (our windowing)
 *     output ≤ reserve       (outputBudgetTokens, protected by the declaration)
 *     ⇒ prompt + output ≤ W  (the request always fits the slot)
 *
 * Stated honestly, that bounds what the CLAMP and the SLOT can do to a reply; it
 * does not promise a `"length"` stop is impossible. A model that genuinely wants
 * to write past the reserve still stops on it — which on a voice brain whose
 * replies run to tens of tokens is itself an anomaly worth investigating, and is
 * why the gate says "any length stop is a bug", not "cannot happen".
 *
 * WINDOWING OFF ⇒ CLAMP OFF. `ZOE_BRAIN_CONTEXT_WINDOW=0` explicitly disables
 * our windowing (the documented A/B escape hatch), which removes the `prompt ≤ W`
 * premise entirely. We then declare `0`, which pi-ai reads as "no clamp"
 * (`if (model.contextWindow <= 0) return max(1, maxTokens)`) — restoring the
 * clamp-free 1.x behaviour rather than silently strangling output by an unknown
 * amount. The guard in that mode is llama-server's own loud 400, not a silent
 * truncation. (The pre-fix code declared 8192 here instead, which is the one
 * configuration where this fix changes behaviour by ADDING an overflow risk that
 * was already the stated cost of turning windowing off.)
 *
 * SNAPSHOT SEMANTICS, stated because the two sides are read at different times.
 * `Model` is a plain object in a static `models: []` list, so this function runs
 * ONCE, when `createZoeProvider()` builds the provider at module load — the same
 * boot-time snapshot `baseUrl` already takes (see test/helpers/harness.ts).
 * `contextWindowTokens()` inside `windowContextToBudget`, by contrast, is read
 * fresh on every model call. The `prompt ≤ W` premise therefore assumes the env
 * does not change after boot, which for a systemd unit it cannot. Mutating
 * ZOE_BRAIN_CONTEXT_WINDOW mid-process (only tests do) moves the windowing
 * budget without moving the declaration; that is the pre-existing behaviour of
 * this field, not something the fix introduces, and it is safe in the direction
 * that matters — a LOWERED window shrinks the prompt against an unchanged, now
 * over-generous declaration.
 */
export function declaredContextWindow(): number {
  const windowTokens = contextWindowTokens();
  if (windowTokens <= 0) return 0;
  return windowTokens + PI_AI_CONTEXT_SAFETY_TOKENS + outputBudgetTokens();
}

/**
 * Seam M: the Gemma rock, as a Pi provider object.
 *
 * `baseUrl` defaults to the live llama-server on :11434 (the same
 * OpenAI-compatible endpoint the prod brain uses) and is overridable via
 * ZOE_BRAIN_BASE_URL — which is also how the tests point it at an in-process
 * mock model server.
 *
 * MODEL METADATA IS NOW OURS TO DECLARE. The beta's `registerProvider` zero-filled
 * a catalog entry; 2.x has "no catalog hydration or zero-fill for custom providers"
 * (migration.md, "Providers"), so every field below is load-bearing:
 *   - `reasoning: false` — this brain has no extended-thinking mode; declaring it
 *     true would forward a thinkingLevel the wire silently drops.
 *   - `input: ['text']` — Moonshine STT + text chat only. An image would be
 *     replaced with an "(image omitted)" placeholder rather than blowing up.
 *   - `contextWindow` — NOT the llama-server window: on 0.83.0 this field feeds
 *     only pi-ai's output clamp, so it is sized to protect the output budget.
 *     See `declaredContextWindow` above; the real prompt budget is enforced by
 *     src/context-window.ts.
 *   - `maxTokens` — LOAD-BEARING on this path, and it is CLAMPED. (Corrected
 *     2026-08-07; the previous comment claimed "metadata only … the
 *     openai-completions handler sends `options.maxTokens`, never
 *     `model.maxTokens`". That is FALSE for 0.83.0: `buildBaseOptions` computes
 *     `clampMaxTokensToContext(model, context, options?.maxTokens ??
 *     model.maxTokens)`, so `model.maxTokens` is the fallback the agent path
 *     actually uses — pi-agent-core never sets `options.maxTokens` on this loop
 *     — and passing one explicitly would not escape the clamp either.) It is the
 *     reply reserve, so the request always fits llama-server's slot; see
 *     `outputBudgetTokens`.
 *   - `cost` — all zeros; a local model bills nothing.
 * Threshold compaction is disabled at the agent (`useModel(..., { compaction:
 * false })`) — src/context-window.ts explains why this deployment windows at the
 * wire instead.
 */
export function zoeLocalModel(): Model<'openai-completions'> {
  return {
    id: ZOE_MODEL_ID,
    name: 'Zoe local Gemma brain',
    api: 'openai-completions',
    provider: ZOE_PROVIDER_ID,
    baseUrl: process.env.ZOE_BRAIN_BASE_URL ?? 'http://127.0.0.1:11434/v1',
    reasoning: false,
    input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: declaredContextWindow(),
    maxTokens: outputBudgetTokens(),
  };
}

/**
 * Build the capped `zoe` provider. Registered by `app.ts` via `setProvider(...)`
 * at module top level, before any agent runs.
 *
 * llama-server ignores the key, but the OpenAI-completions client wants a
 * non-empty one; ZOE_BRAIN_API_KEY overrides the harmless placeholder. The
 * beta's `apiKey` registration field is gone — a credential is now the
 * provider's own `auth.apiKey.resolve()`, which runs per request.
 */
export function createZoeProvider(): Provider {
  return createProvider({
    id: ZOE_PROVIDER_ID,
    name: 'Zoe local brain',
    auth: {
      apiKey: {
        name: 'Zoe local llama-server',
        resolve: async () => ({
          auth: { apiKey: process.env.ZOE_BRAIN_API_KEY ?? 'local-no-key' },
        }),
      },
    },
    models: [zoeLocalModel()],
    api: cappedApi,
  });
}
