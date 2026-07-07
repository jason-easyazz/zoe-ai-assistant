/**
 * Hard per-turn tool-iteration ceiling for the Flue Zoe brain.
 *
 * THE BUG THIS FIXES: the built-in Flue HTTP agent route (`POST /agents/zoe/:id`)
 * runs pi-agent-core's `runLoop` directly — a `while (true)` that only exits when
 * the model finally returns an assistant message with NO tool calls (see
 * node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js:84-160). Flue's
 * harness constructs that `Agent` WITHOUT a `shouldStopAfterTurn` / `afterToolCall`
 * hook (persisted-image-placement-*.mjs ~line 1939), and a `defineAgent` runtime
 * config exposes no iteration field (handle-agent-*.mjs AGENT_RUNTIME_FIELDS). The
 * framework's `MAX_FOLLOWUPS=32` ceiling only applies to `session.prompt(text,
 * {result})` and only counts turns where the model STOPPED calling tools — it does
 * NOT bound a model that keeps emitting tool calls every turn. So a 4B Gemma with
 * 11 tool schemas can loop on tool calls forever → the client times out at 120s.
 *
 * THE FIX (supported seam): Flue's per-assistant-turn model call goes through
 * pi-ai's `streamSimple(model, …)`, which dispatches by `model.api` to a registered
 * api handler (pi-ai/dist/stream.js:30 → getApiProvider(model.api)). We register a
 * thin custom api (`zoe-capped-completions`) via Flue's `registerApiProvider` that
 * wraps the built-in `openai-completions` handler. On every model call we count the
 * tool-call rounds already taken this turn; once the cap is reached we delegate
 * with the tool list STRIPPED, so the model physically cannot request another tool
 * and MUST answer in plain text — which makes the agent loop exit gracefully this
 * turn. This is a hard, model-independent ceiling that returns a real assistant
 * message (no error, no hang). `app.ts` points the `zoe` provider at this api.
 *
 * Cap is configurable via ZOE_BRAIN_MAX_TOOL_ITERS (default 8).
 *
 * This same wire seam also applies PROGRESSIVE TOOL DISCLOSURE (the port of
 * prod's services/zoe-core/extensions/abilities.ts pattern): before the cap,
 * `context.tools` is filtered to the always-on core plus the request's active
 * ability groups, so the 4B isn't carrying all 11 schemas every call. The
 * active set is derived statelessly from the request's own message window —
 * see src/tools/tool-groups.ts for the mechanism, sources, and trade-offs.
 * Policy order: disclosure first, then the cap (past the cap, ALL tools are
 * stripped regardless of disclosure). ZOE_BRAIN_PROGRESSIVE_TOOLS=false
 * disables disclosure for A/B comparison.
 *
 * LAB ONLY.
 */
import { registerApiProvider } from '@flue/runtime';
import {
  bindTurnUserId,
  forwardedIdentityFromMessages,
  stripIdentityEnvelope,
} from '../request-identity.ts';
// FLUE-API: built-in OpenAI-completions wire handlers, imported via pi-ai's public
// subpath export and verified present (streamOpenAICompletions / streamSimpleOpenAICompletions).
import {
  streamOpenAICompletions,
  streamSimpleOpenAICompletions,
} from '@earendil-works/pi-ai/openai-completions';
import type {
  Api,
  AssistantMessageEventStream,
  Context,
  Message,
  Model,
  SimpleStreamOptions,
  StreamOptions,
} from '@earendil-works/pi-ai';
// .ts extension so the offline strip-types tests can resolve it (see zoe-tools.ts).
import {
  discloseTools,
  progressiveToolsEnabled,
  stripCodingBuiltins,
} from '../tools/tool-groups.ts';
import { windowContextToBudget } from '../context-window.ts';

/** Custom api id this module registers; `app.ts` binds the `zoe` provider to it. */
export const CAPPED_COMPLETIONS_API = 'zoe-capped-completions';

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
function toolIterationDepth(messages: Message[]): number {
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

const WRAPUP_NOTE =
  '\n\nYou have already used several tools this turn and have reached the tool-call ' +
  'limit. Do NOT request any more tools. Give your best final answer now, in plain ' +
  'text, using what you already have.';

/**
 * Apply the cap: at or past the per-turn limit, strip the tools (and add a brief
 * wrap-up note) so the next model call cannot emit a tool call. Otherwise pass the
 * context through untouched.
 */
function applyCap(context: Context): Context {
  if (toolIterationDepth(context.messages) < maxToolIters()) return context;
  return {
    ...context,
    tools: [],
    systemPrompt: (context.systemPrompt ?? '') + WRAPUP_NOTE,
  };
}

/**
 * All wire-level policies for one model call, in order:
 *   1. prompt-fit history windowing (drop the OLDEST whole user-turn blocks so
 *      the assembled prompt always fits the model context — the fix for the
 *      unbounded-session 400/500 wedge; see src/context-window.ts). Runs FIRST
 *      so tool disclosure derives the active set from the same message window
 *      the model actually sees; the current turn always survives whole, so the
 *      iteration-depth count below is unaffected;
 *   2. strip pi/Flue coding built-ins (read/write/edit/bash/grep/glob/task)
 *      that the harness injects on every turn — UNCONDITIONAL safety floor, so
 *      a family voice brain is never handed bash/write/edit/task even with the
 *      disclosure kill switch off (see tool-groups.ts CODING_BUILTIN_TOOL_NAMES);
 *   3. progressive tool disclosure (shrink the Zoe schemas the model sees to
 *      core + active groups) — also strips the coding built-ins, but step 2
 *      guarantees it regardless of ZOE_BRAIN_PROGRESSIVE_TOOLS;
 *   4. the iteration cap (past the cap, strip ALL tools so the turn must finish
 *      in plain text).
 * Exported for the offline unit tests only.
 */
export function applyPolicies(context: Context): Context {
  // Strip the acting-identity envelope BEFORE any other policy so the model — and
  // every downstream transform — only ever sees the human-authored message text.
  const clean = { ...context, messages: stripIdentityEnvelope(context.messages) };
  const windowed = windowContextToBudget(clean);
  const safe = stripCodingBuiltins(windowed);
  const disclosed = progressiveToolsEnabled() ? discloseTools(safe) : safe;
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
 */
export function bindIdentityForRound(context: Context, signal?: AbortSignal): void {
  bindTurnUserId(signal, forwardedIdentityFromMessages(context.messages));
}

/**
 * Delegate to the built-in handler under its own api id, so the handler's
 * URL-based OpenAI-compat detection is unaffected by our custom api slug.
 */
function asCompletionsModel(model: Model<Api>): Model<'openai-completions'> {
  return { ...model, api: 'openai-completions' } as Model<'openai-completions'>;
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
    asCompletionsModel(model),
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
    asCompletionsModel(model),
    applyPolicies(context),
    withBrainTemperature(options),
  );
}

let registered = false;

/**
 * Register the capped api. Idempotent: pi-ai's registry is last-write-wins per api
 * string, but a module-load guard keeps repeat imports cheap. Call before
 * `registerProvider('zoe', { api: CAPPED_COMPLETIONS_API, … })`.
 */
export function registerCappedCompletions(): void {
  if (registered) return;
  registered = true;
  registerApiProvider({
    api: CAPPED_COMPLETIONS_API,
    stream: cappedStream,
    streamSimple: cappedStreamSimple,
  });
}
