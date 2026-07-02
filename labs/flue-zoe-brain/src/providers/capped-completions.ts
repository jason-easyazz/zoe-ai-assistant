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
 * FOLLOW-UP (NOT in this PR — keep it small): 11 tool schemas every turn is also
 * prompt bloat that hurts the 4B's tool reliability/speed. Port prod's progressive
 * tool disclosure (services/zoe-core/.../abilities.ts setActiveTools) so only the
 * relevant tools are offered per turn. See PR description.
 *
 * LAB ONLY.
 */
import { registerApiProvider } from '@flue/runtime';
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
 * Delegate to the built-in handler under its own api id, so the handler's
 * URL-based OpenAI-compat detection is unaffected by our custom api slug.
 */
function asCompletionsModel(model: Model<Api>): Model<'openai-completions'> {
  return { ...model, api: 'openai-completions' } as Model<'openai-completions'>;
}

function cappedStream(
  model: Model<Api>,
  context: Context,
  options?: StreamOptions,
): AssistantMessageEventStream {
  return streamOpenAICompletions(asCompletionsModel(model), applyCap(context), options);
}

function cappedStreamSimple(
  model: Model<Api>,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  return streamSimpleOpenAICompletions(asCompletionsModel(model), applyCap(context), options);
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
