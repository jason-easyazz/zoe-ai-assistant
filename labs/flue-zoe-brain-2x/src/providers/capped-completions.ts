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
import {
  bindTurnReplayMode,
  forwardedReplayFromMessages,
  stripReplayEnvelope,
} from '../replay-mode.ts';
// .ts extension so the offline strip-types tests can resolve it (see zoe-tools.ts).
import {
  discloseTools,
  progressiveToolsEnabled,
  stripCodingBuiltins,
} from '../tools/tool-groups.ts';
import { contextWindowTokens, windowContextToBudget } from '../context-window.ts';

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
  // Strip the control envelopes BEFORE any other policy so the model — and every
  // downstream transform — only ever sees the human-authored message text. The
  // replay marker rides AHEAD of the identity line on the wire (both parsers are
  // ^-anchored), so it must come off first or the identity line is never at the
  // start of the message. See src/replay-mode.ts "WIRE ORDER".
  const unwrapped = stripReplayEnvelope(context.messages);
  const clean = { ...context, messages: stripIdentityEnvelope(unwrapped) };
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
  // Replay isolation binds from the SAME trusted envelope, on the same signal key,
  // for the same reason (Flue drops every body field but the message). Read it
  // first and hand the identity parser the replay-stripped messages: the replay
  // line sits ahead of the identity line on the wire and both regexes are
  // ^-anchored, so parsing identity off the raw messages would find nothing.
  bindTurnReplayMode(signal, forwardedReplayFromMessages(context.messages));
  bindTurnUserId(signal, forwardedIdentityFromMessages(stripReplayEnvelope(context.messages)));
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
 *   - `contextWindow` — the llama-server `--ctx-size` rock (default 8192), read
 *     from the SAME env knob the windowing budget uses so the two can never drift.
 *   - `maxTokens` — metadata only on this path: the openai-completions handler
 *     sends `options.maxTokens`, never `model.maxTokens` (pi-ai dist/api/
 *     openai-completions.js), so this does not cap replies on the wire.
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
    contextWindow: contextWindowTokens() || 8192,
    maxTokens: 2048,
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
