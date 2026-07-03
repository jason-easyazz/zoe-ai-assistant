/**
 * Non-streaming model calls for the Flue Zoe brain — the "I don'm" fix.
 *
 * THE BUG THIS FIXES: llama-server runs Gemma with MTP speculative decoding
 * (`--spec-type draft-mtp --spec-draft-n-max 4`). In STREAMING mode the server
 * emits draft tokens optimistically; server-side verification later corrects a
 * rejected draft, but an SSE stream cannot retract bytes already sent — so a
 * reply occasionally starts with a corrupted contraction ("I don'm", "I don've"),
 * sitting exactly on the "I'm" / "I don't" draft fork. pi-ai's
 * `openai-completions` handler hard-codes `stream: true`, so every sidecar
 * model call rode the vulnerable path. NON-STREAMING calls are immune: the
 * server reconciles the full completion before serializing it once (prod's
 * `stream: False` calls never showed the glitch; byte-level proxy evidence).
 *
 * THE FIX: make the wire call non-streaming and synthesize the
 * `AssistantMessageEventStream` events pi-agent-core expects from the complete
 * response. pi-ai has no non-streaming completions path (verified against
 * pi 0.79.10/0.80.2 source: the only `stream: false` in packages/ai is the
 * images API), so we do the POST ourselves — but we do NOT hand-roll the
 * request body. `captureWireParams` runs the REAL pi-ai handler with an
 * `onPayload` hook (a public seam, invoked after `buildParams` and before the
 * HTTP request — dist/providers/openai-completions.js) that captures the exact
 * params pi-ai built — messages, tool schemas, sampling, compat quirks — and
 * then aborts that probe call by throwing, so pi-ai never issues its own
 * streaming request. We then send the SAME body with `stream: false` (and
 * `stream_options` dropped) and map the parsed completion onto pi-ai's exact
 * event vocabulary and ordering (see the streaming handler's finishBlock/
 * ensure*Block emitters):
 *
 *   start → [thinking_start/delta/end] → [text_start/delta/end]
 *         → per tool call: toolcall_start/delta/end → done
 *
 * The agent loop consumes `done`'s final message (toolCall blocks included),
 * and Flue's observe() feed — which src/streaming.ts turns into the Seam-A
 * `__TOOL__`/`__THINKING__` sentinels — sees the same event sequence as
 * before, except text arrives as ONE final delta instead of token-by-token.
 * That matches prod's current non-streaming voice behaviour.
 *
 * ESCAPE HATCH: ZOE_BRAIN_TOKEN_STREAMING=true restores the old token-level
 * streaming delegation (for experiments / a future upstream pi-ai fix).
 *
 * LAB ONLY.
 */
// The class itself is re-exported type-only from pi-ai's root (types.d.ts wins
// the re-export race), so construct instances via the value-exported factory.
import { createAssistantMessageEventStream, parseStreamingJson } from '@earendil-works/pi-ai';
import type {
  AssistantMessage,
  AssistantMessageEventStream,
  Context,
  Model,
  StopReason,
  StreamOptions,
  TextContent,
  ThinkingContent,
  ToolCall,
} from '@earendil-works/pi-ai';

/** The delegate is one of pi-ai's real streaming handlers (stream or streamSimple). */
export type CompletionsDelegate = (
  model: Model<'openai-completions'>,
  context: Context,
  options?: StreamOptions,
) => AssistantMessageEventStream;

/** Injection seam for the offline unit tests. */
export interface NonStreamingDeps {
  fetchFn?: typeof fetch;
}

/**
 * Escape hatch: `ZOE_BRAIN_TOKEN_STREAMING=true|1|yes|on` restores token-level
 * streaming at the wire. Default OFF (non-streaming) — see module header.
 * Read fresh each call so the flag can be flipped without a code change.
 */
export function tokenStreamingEnabled(): boolean {
  const v = (process.env.ZOE_BRAIN_TOKEN_STREAMING ?? '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(v);
}

/** Thrown inside the probe's onPayload to stop pi-ai from sending its own request. */
const CAPTURE_ABORT_MESSAGE = 'zoe-nonstreaming: wire params captured';

/**
 * Run the real pi-ai handler just far enough to capture the exact request body
 * it would send (after convertMessages/convertTools/compat handling), then
 * abort it via a throw from the public `onPayload` seam. The discarded probe
 * stream ends with a synthetic error event that nobody consumes.
 *
 * If the handler fails BEFORE building params (e.g. missing API key), the
 * probe's own error is surfaced instead.
 */
export async function captureWireParams(
  delegate: CompletionsDelegate,
  model: Model<'openai-completions'>,
  context: Context,
  options?: StreamOptions,
): Promise<Record<string, unknown>> {
  let captured: Record<string, unknown> | null = null;
  const probe = delegate(model, context, {
    ...options,
    onPayload: async (payload, m) => {
      // Honour any upstream onPayload (none today; defensive parity).
      const upstream = await options?.onPayload?.(payload, m);
      captured = (upstream ?? payload) as Record<string, unknown>;
      throw new Error(CAPTURE_ABORT_MESSAGE);
    },
  });
  const final = await probe.result();
  if (captured) return captured;
  throw new Error(final.errorMessage ?? 'model call failed before the request was built');
}

/** Mirrors pi-ai's mapStopReason (dist/providers/openai-completions.js). */
function mapStopReason(reason: unknown): { stopReason: StopReason; errorMessage?: string } {
  if (reason === null || reason === undefined) return { stopReason: 'stop' };
  switch (reason) {
    case 'stop':
    case 'end':
      return { stopReason: 'stop' };
    case 'length':
      return { stopReason: 'length' };
    case 'function_call':
    case 'tool_calls':
      return { stopReason: 'toolUse' };
    default:
      return { stopReason: 'error', errorMessage: `Provider finish_reason: ${String(reason)}` };
  }
}

/** Mirrors pi-ai's parseChunkUsage for the non-streaming usage object. */
function parseUsage(rawUsage: Record<string, unknown> | undefined): AssistantMessage['usage'] {
  const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const details = (rawUsage?.prompt_tokens_details ?? {}) as Record<string, unknown>;
  const promptTokens = num(rawUsage?.prompt_tokens);
  const cacheRead = num(details.cached_tokens ?? rawUsage?.prompt_cache_hit_tokens);
  const cacheWrite = num(details.cache_write_tokens);
  const input = Math.max(0, promptTokens - cacheRead - cacheWrite);
  const output = num(rawUsage?.completion_tokens);
  return {
    input,
    output,
    cacheRead,
    cacheWrite,
    totalTokens: input + output + cacheRead + cacheWrite,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

/** First non-empty llama.cpp/OpenAI-compat reasoning field on the message. */
function findReasoning(msg: Record<string, unknown>): { field: string; text: string } | null {
  for (const field of ['reasoning_content', 'reasoning', 'reasoning_text']) {
    const value = msg[field];
    if (typeof value === 'string' && value.length > 0) return { field, text: value };
  }
  return null;
}

/** Assistant text from a non-streaming message (string, or array of text parts). */
function extractText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === 'object' && typeof (part as { text?: unknown }).text === 'string'
          ? ((part as { text: string }).text)
          : '',
      )
      .join('');
  }
  return '';
}

/**
 * The non-streaming model call. Captures pi-ai's request body via `delegate`,
 * POSTs it with `stream: false`, and replays the complete response as the
 * standard AssistantMessageEvent sequence.
 */
export function streamNonStreamingCompletions(
  delegate: CompletionsDelegate,
  model: Model<'openai-completions'>,
  context: Context,
  options?: StreamOptions,
  deps?: NonStreamingDeps,
): AssistantMessageEventStream {
  const stream = createAssistantMessageEventStream();
  const fetchFn = deps?.fetchFn ?? fetch;

  (async () => {
    const output: AssistantMessage = {
      role: 'assistant',
      content: [],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: parseUsage(undefined),
      stopReason: 'stop',
      timestamp: Date.now(),
    };

    try {
      const params = await captureWireParams(delegate, model, context, options);
      const body: Record<string, unknown> = { ...params, stream: false };
      delete body.stream_options;

      // Header precedence mirrors pi-ai's createClient: model defaults, then
      // caller headers override; Authorization from the resolved api key.
      const headers: Record<string, string> = {
        'content-type': 'application/json',
        ...(options?.apiKey ? { authorization: `Bearer ${options.apiKey}` } : {}),
        ...(model.headers ?? {}),
        ...(options?.headers ?? {}),
      };
      const signals: AbortSignal[] = [];
      if (options?.signal) signals.push(options.signal);
      if (options?.timeoutMs !== undefined) signals.push(AbortSignal.timeout(options.timeoutMs));

      const url = `${String(model.baseUrl).replace(/\/+$/, '')}/chat/completions`;
      // Wire-mode audit line (ZOE_BRAIN_WIRE_DEBUG=1): proves on the box which
      // mode a given model call actually used — used to verify the eval runs.
      if (process.env.ZOE_BRAIN_WIRE_DEBUG === '1') {
        console.error(`[zoe-nonstreaming] POST ${url} stream=${String(body.stream)}`);
      }
      const resp = await fetchFn(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        ...(signals.length > 0
          ? { signal: signals.length === 1 ? signals[0] : AbortSignal.any(signals) }
          : {}),
      });
      await options?.onResponse?.(
        { status: resp.status, headers: Object.fromEntries(resp.headers.entries()) },
        model,
      );
      if (!resp.ok) {
        const detail = await resp.text().catch(() => '');
        throw new Error(`model endpoint returned ${resp.status}: ${detail.slice(0, 2000)}`);
      }
      const completion = (await resp.json()) as Record<string, unknown>;

      const choices = completion.choices;
      const choice = (Array.isArray(choices) ? choices[0] : undefined) as
        | Record<string, unknown>
        | undefined;
      if (!choice) throw new Error('model response carried no choices');
      const msg = (choice.message ?? {}) as Record<string, unknown>;

      if (typeof completion.id === 'string' && completion.id.length > 0) {
        output.responseId = completion.id;
      }
      if (
        typeof completion.model === 'string' &&
        completion.model.length > 0 &&
        completion.model !== model.id
      ) {
        output.responseModel = completion.model;
      }
      if (completion.usage && typeof completion.usage === 'object') {
        output.usage = parseUsage(completion.usage as Record<string, unknown>);
      }

      const finish = mapStopReason(choice.finish_reason);
      output.stopReason = finish.stopReason;
      if (finish.errorMessage) output.errorMessage = finish.errorMessage;
      if (output.stopReason === 'error') {
        throw new Error(output.errorMessage ?? 'Provider returned an error stop reason');
      }

      stream.push({ type: 'start', partial: output });
      const blocks = output.content;

      // Thinking first, then text, then tool calls — the arrival order of the
      // streaming path for the same completion.
      const reasoning = findReasoning(msg);
      if (reasoning) {
        const block: ThinkingContent = {
          type: 'thinking',
          thinking: '',
          thinkingSignature: reasoning.field,
        };
        blocks.push(block);
        const contentIndex = blocks.indexOf(block);
        stream.push({ type: 'thinking_start', contentIndex, partial: output });
        block.thinking = reasoning.text;
        stream.push({ type: 'thinking_delta', contentIndex, delta: reasoning.text, partial: output });
        stream.push({ type: 'thinking_end', contentIndex, content: block.thinking, partial: output });
      }

      const text = extractText(msg.content);
      if (text.length > 0) {
        const block: TextContent = { type: 'text', text: '' };
        blocks.push(block);
        const contentIndex = blocks.indexOf(block);
        stream.push({ type: 'text_start', contentIndex, partial: output });
        block.text = text;
        stream.push({ type: 'text_delta', contentIndex, delta: text, partial: output });
        stream.push({ type: 'text_end', contentIndex, content: block.text, partial: output });
      }

      const rawToolCalls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
      for (let i = 0; i < rawToolCalls.length; i++) {
        const raw = (rawToolCalls[i] ?? {}) as Record<string, unknown>;
        const fn = (raw.function ?? {}) as Record<string, unknown>;
        const argsText = typeof fn.arguments === 'string' ? fn.arguments : '';
        const block: ToolCall = {
          type: 'toolCall',
          id: typeof raw.id === 'string' && raw.id.length > 0 ? raw.id : `call_${i}`,
          name: typeof fn.name === 'string' ? fn.name : '',
          arguments: parseStreamingJson(argsText),
        };
        blocks.push(block);
        const contentIndex = blocks.indexOf(block);
        stream.push({ type: 'toolcall_start', contentIndex, partial: output });
        stream.push({ type: 'toolcall_delta', contentIndex, delta: argsText, partial: output });
        stream.push({ type: 'toolcall_end', contentIndex, toolCall: block, partial: output });
      }

      if (options?.signal?.aborted) throw new Error('Request was aborted');

      stream.push({
        type: 'done',
        reason: output.stopReason as Extract<StopReason, 'stop' | 'length' | 'toolUse'>,
        message: output,
      });
      stream.end();
    } catch (error) {
      output.stopReason = options?.signal?.aborted ? 'aborted' : 'error';
      output.errorMessage = error instanceof Error ? error.message : JSON.stringify(error);
      stream.push({
        type: 'error',
        reason: output.stopReason as Extract<StopReason, 'aborted' | 'error'>,
        error: output,
      });
      stream.end();
    }
  })();

  return stream;
}
