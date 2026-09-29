/**
 * Seam-A sentinel streaming for the lab Zoe-brain sidecar.
 *
 * Cutover blocker #3 (docs/architecture/zoe-flue-integration.md §10): the
 * sidecar returned whole results only, so the voice tool filler (#844) — which
 * keys off `__TOOL__` phase=start sentinels arriving MID-turn — would go dark
 * on cutover. This module adds a streaming response mode that emits the exact
 * text-delta + `__TOOL__`/`__THINKING__` sentinel stream the Pi-CLI brain
 * emits today, so zoe-data's existing consumers keep working unchanged.
 *
 * THE CONSUMER SIDE IS AUTHORITATIVE — the contract is pinned byte-for-byte to
 * what `services/zoe-data/zoe_core_client.py` yields and what the zoe-data
 * parse sites split on (`routers/chat.py` `brain_tool_sentinel_events` /
 * chunk dispatch, `routers/voice_tts.py` `_voice_tool_name_from_sentinel`):
 *
 *   - plain text deltas (arbitrary strings, may contain newlines);
 *   - `__TOOL__:` + Python `json.dumps(...)` (DEFAULT separators — `", "` and
 *     `": "`, ensure_ascii) of, in prod emission order:
 *       {"phase": "start", "id": <id>, "name": <name>}        (toolcall seen)
 *       {"phase": "args", "id": <id>, "name": <name>, "args": {...}}
 *       {"phase": "result", "id": <id>, "result": <str>}      (tool finished)
 *     Non-string results are stringified with COMPACT separators (`","`/`":"`),
 *     mirroring `zoe_core_client._stringify`.
 *   - `__THINKING__:` + raw thinking text (no JSON).
 *
 * Consumers dispatch per-chunk via `chunk.startswith("__TOOL__:")` etc., so a
 * sentinel must arrive as its OWN chunk, never embedded in a text delta.
 *
 * Wire framing (this sidecar's choice — the seam doc leaves it to us):
 * newline-delimited JSON (`application/x-ndjson`). Each line is either
 *   - a JSON string: exactly one Seam-A chunk (text delta or sentinel), or
 *   - {"done": true}: the turn completed (always the last line on success),
 *     optionally carrying `prompt_cache` — see `PromptCacheRound` — or
 *   - {"error": "<message>"}: the turn failed (always the last line on error).
 * JSON-encoding each chunk keeps chunk boundaries exact (deltas may contain
 * newlines) and is trivial for the Python consumer to map onto the
 * `run_zoe_core_streaming` yield sequence: yield every string line; treat a
 * missing/`error` terminal line as a failed turn (same fallback as today's
 * whole-result error path in `zoe_flue_client`).
 *
 * Mode selection — CONTENT NEGOTIATION, not a breaking change: a client opts
 * in per-request with `Accept: application/x-ndjson` on the existing
 * `POST /agents/:name/:id` route. A plain POST keeps returning the 202
 * admission. Negotiation fits the consumer because `zoe_flue_client` already
 * owns its request headers per-call and can flip modes without a sidecar
 * restart or a second endpoint. Belt-and-braces kill switch:
 * `ZOE_BRAIN_STREAM=0|false|no|off` disables interception entirely.
 *
 * `?wait=result` IS GONE ON 2.x AND THE SPECIAL CASE WITH IT. The beta let
 * `?wait=result` win over the Accept header, because that query returned the
 * whole result. Flue 2.x does not merely drop the query — it REJECTS it: the
 * request handler throws `InvalidRequestError` for ANY `wait` param, any value
 * ("Agent prompts are fire-and-forget and do not support `?wait=result`"). So
 * there is no whole-result mode left to defer to, and carrying the branch would
 * only advertise a contract the runtime refuses. A `?wait=...` request now falls
 * through to the runtime and gets its 400. zoe-data's non-streaming path must
 * move to fire-and-forget + stream-read (or the SDK's `wait()`); that is the
 * Phase-2 client change, tracked in the port report's PHASE2-SPEC.
 *
 * Auth is NOT re-implemented here: streaming requests pass through `next()`
 * first — and the fail-closed bearer gate (src/auth.ts) is mounted AHEAD of this
 * middleware in app.ts, so an unauthorized request never reaches it at all.
 * Flue's payload validation still runs inside the agent router; only a 202
 * admission is upgraded to a stream — a 401/400 passes through verbatim.
 * Identity binding and the ZOE_BRAIN_ALLOW_WRITES gate live in the tools and are
 * untouched.
 *
 * Event source: `observe()` (in-process, full fidelity). The durable stream
 * (`GET /agents/:name/:id?live=...`) is NOT used because it is read back from
 * storage — unusable for voice TTFT.
 *
 * `observe()` IS NOT PROMPT FOR TEXT EITHER (corrected 2026-09-28). Its
 * `text_delta` is published only after the runtime's batched storage write,
 * which flushes at most once a second (`CANONICAL_FLUSH_DELAY_MS = 1e3`), so
 * after the first token the reply arrived in ~1 s bursts. Model text therefore
 * has a SECOND source: `src/early-text.ts` taps the provider stream through the
 * runtime's supported execution interceptor and publishes each delta the moment
 * the model yields it. This middleware forwards that early copy for the latched
 * operation and drops the flushed `observe()` copy by per-turn character offset
 * (`EarlyTextReconciler`), so each character goes out exactly once. Everything
 * else — tool sentinels, thinking, the terminal and its `prompt_cache` — still
 * comes from `observe()` unchanged. Kill switch: `ZOE_FLUE_EARLY_TEXT=0`
 * restores the observe-only stream. One `FLUE_EARLY_TEXT` log line per turn
 * records first-delta / first-sentence latency and the dedupe count.
 *
 * Correlation: the subscriber filters on the instance id, latches the first
 * `operation_start` with `operationKind === 'prompt'` after our admission
 * (session operations are exclusive per instance, and submissions serialize),
 * and finishes on that operation's end event. Known lab limit: two turns
 * admitted CONCURRENTLY for the SAME session id can mis-latch — upstream
 * zoe-data never does this (a session's turns are strictly sequential).
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { observe } from '@flue/runtime';
import type { Context } from '@earendil-works/pi-ai';
import type { MiddlewareHandler } from 'hono';
import { promptSections, type PromptSections } from './providers/capped-completions.ts';
import {
  EarlyTextReconciler,
  earlyTextEnabled,
  subscribeEarlyText,
  type EarlyTextSubscribe,
} from './early-text.ts';

export const NDJSON_CONTENT_TYPE = 'application/x-ndjson';
export const TOOL_SENTINEL_PREFIX = '__TOOL__:';
export const THINKING_SENTINEL_PREFIX = '__THINKING__:';

const DEFAULT_TIMEOUT_S = 180; // mirrors prod ZOE_CORE_TIMEOUT_S

// ── Python json.dumps parity ─────────────────────────────────────────────────

const STRING_ESCAPES: Record<number, string> = {
  0x22: '\\"',
  0x5c: '\\\\',
  0x08: '\\b',
  0x09: '\\t',
  0x0a: '\\n',
  0x0c: '\\f',
  0x0d: '\\r',
};

function pyString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    const esc = STRING_ESCAPES[c];
    if (esc !== undefined) out += esc;
    // ensure_ascii: CPython escapes < 0x20 and > 0x7e. JS strings are UTF-16,
    // so astral chars are two code units here — each escapes to one \uXXXX,
    // which is exactly CPython's surrogate-pair output for the same char.
    else if (c < 0x20 || c > 0x7e) out += '\\u' + c.toString(16).padStart(4, '0');
    else out += s[i];
  }
  return out + '"';
}

function pyNumber(n: number): string {
  // Python emits bare NaN/Infinity by default; values here come from parsed
  // JSON so this is unreachable in practice, but match anyway. Known
  // divergence (accepted): a float with no fraction — Python "1.0", JS "1".
  if (Number.isNaN(n)) return 'NaN';
  if (n === Infinity) return 'Infinity';
  if (n === -Infinity) return '-Infinity';
  return String(n);
}

/**
 * Serialize exactly like CPython `json.dumps(value)` — default separators
 * (`", "` / `": "`) and `ensure_ascii=True` — or, with `compact`, like
 * `json.dumps(value, separators=(",", ":"))`. Key order is insertion order on
 * both sides, so the sentinel payloads built below are byte-identical to
 * `zoe_core_client.py`'s. Pinned by test/sentinel_stream.test.ts.
 */
export function pyJsonDumps(value: unknown, opts?: { compact?: boolean }): string {
  const itemSep = opts?.compact ? ',' : ', ';
  const kvSep = opts?.compact ? ':' : ': ';
  const ser = (v: unknown): string => {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return pyNumber(v);
    if (typeof v === 'string') return pyString(v);
    if (Array.isArray(v)) return '[' + v.map(ser).join(itemSep) + ']';
    if (typeof v === 'object') {
      const parts: string[] = [];
      for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
        if (val === undefined) continue; // no Python-dict equivalent; drop like JSON.stringify
        parts.push(pyString(k) + kvSep + ser(val));
      }
      return '{' + parts.join(itemSep) + '}';
    }
    throw new TypeError(`not JSON-serializable: ${typeof v}`);
  };
  return ser(value);
}

// ── Sentinel builders (prod contract, zoe_core_client.py) ────────────────────

/** `__TOOL__` phase=start — mirrors zoe_core_client.py's toolcall_start emit. */
export function toolStartSentinel(id: string, name: string): string {
  return TOOL_SENTINEL_PREFIX + pyJsonDumps({ phase: 'start', id, name });
}

/** `__TOOL__` phase=args — mirrors zoe_core_client._tool_args_sentinels. */
export function toolArgsSentinel(id: string, name: string, args: unknown): string {
  return TOOL_SENTINEL_PREFIX + pyJsonDumps({ phase: 'args', id, name, args: args ?? {} });
}

/**
 * `__TOOL__` phase=result from a Flue `tool` event — mirrors
 * zoe_core_client._tool_result_sentinel: probe the likely result carriers in
 * order, unwrap one nested envelope level, stringify non-strings compactly,
 * and return null (emit nothing) when neither an id nor a result is carried.
 */
export function toolResultSentinel(event: Record<string, unknown>): string | null {
  const tcId = event.id ?? event.toolCallId ?? event.callId;
  let result: unknown = null;
  for (const key of ['result', 'output', 'content']) {
    if (event[key] !== null && event[key] !== undefined) {
      result = event[key];
      break;
    }
  }
  if (result !== null && typeof result === 'object' && !Array.isArray(result)) {
    const nested = result as Record<string, unknown>;
    for (const key of ['content', 'text', 'output', 'result']) {
      if (nested[key] !== null && nested[key] !== undefined) {
        result = nested[key];
        break;
      }
    }
  }
  if ((result === null || result === undefined) && (tcId === null || tcId === undefined)) {
    return null;
  }
  const payload: Record<string, unknown> = { phase: 'result' };
  if (tcId !== null && tcId !== undefined) payload.id = String(tcId);
  if (result !== null && result !== undefined) {
    payload.result = typeof result === 'string' ? result : stringifyCompact(result);
  }
  return TOOL_SENTINEL_PREFIX + pyJsonDumps(payload);
}

/** Best-effort compact string — mirrors zoe_core_client._stringify. */
function stringifyCompact(value: unknown): string {
  try {
    return pyJsonDumps(value, { compact: true });
  } catch {
    return String(value);
  }
}

// ── Flue-event → Seam-A chunk reducer ────────────────────────────────────────

/**
 * llama-server prompt-cache accounting for ONE model call (one tool round) of
 * the turn, from the Flue `turn` event's `response.usage` (the documented
 * per-call `PromptUsage`, @flue/runtime docs/reference/events.md): llama-server reports
 * `usage.prompt_tokens_details.cached_tokens` and pi-ai maps it to
 * `usage.cacheRead`, with `usage.input = prompt_tokens - cached`. So
 * `prompt_n` is the tokens RE-PREFILLED this call (llama-server's
 * `timings.prompt_n`) and `cache_n` the tokens served from the KV/prompt cache
 * (`timings.cache_n`). A small `prompt_n` on the first round is a cache hit; a
 * few hundred is a miss that costs ~1.7 ms/token before the first token.
 * Forwarded on the `{"done": true}` terminal so zoe-data can log it per turn;
 * the consumer ignores unknown terminal fields, so this is additive.
 */
export interface PromptCacheRound {
  prompt_n: number;
  cache_n: number;
}

export interface SeamAState {
  /** tool-call ids whose phase=start sentinel has been emitted (dedupe). */
  startedToolIds: Set<string>;
  /** whether any plain text delta has streamed (gates the terminal fallback). */
  streamedText: boolean;
  /** complete text of the last assistant message — the no-deltas fallback. */
  lastAssistantText: string;
  /** per-model-call prompt-cache accounting, in round order (see above). */
  promptCache: PromptCacheRound[];
  /** estimated prompt sections of the turn's FIRST model call, from its
   *  `turn_request` (the round that gates first token); `context_budget` on the
   *  terminal. Additive, like `prompt_cache`; nothing model-visible changes. */
  contextBudget?: PromptSections;
}

export function newSeamAState(): SeamAState {
  return {
    startedToolIds: new Set(),
    streamedText: false,
    lastAssistantText: '',
    promptCache: [],
  };
}

/** The call's prompt-cache numbers, or null when the response carries no usage. */
export function promptCacheRound(response: unknown): PromptCacheRound | null {
  if (!response || typeof response !== 'object') return null;
  const usage = (response as Record<string, unknown>).usage as Record<string, unknown> | undefined;
  if (!usage || typeof usage !== 'object') return null;
  const input = usage.input;
  const cacheRead = usage.cacheRead;
  if (typeof input !== 'number' || !Number.isFinite(input)) return null;
  const cache = typeof cacheRead === 'number' && Number.isFinite(cacheRead) ? cacheRead : 0;
  if (input <= 0 && cache <= 0) return null; // no usage reported (all-zero default)
  return { prompt_n: input, cache_n: cache };
}

/**
 * Map one observed Flue runtime event to zero or more Seam-A chunks, mutating
 * `state`. Mirrors zoe_core_client._read_turn's mapping of the SAME underlying
 * pi-agent-core activity, defensively (a malformed event maps to nothing):
 *
 * EVENT VOCABULARY RE-VERIFIED ON @flue/runtime 2.0.1 AND 2.1.1 (docs/reference/events.md;
 * the only 2.0.1→2.1.1 diff is `contextCompacted` now being populated, unused here):
 * every case below survives the 2.x redesign with the same field names —
 * `{ type: 'text_delta'; text }`, `{ type: 'thinking_end'; content }`,
 * `{ type: 'message_end'; message }`, `{ type: 'tool_start'; toolName;
 * toolCallId }`, and `tool`. The correlation machinery survives too: `instanceId`
 * is still the envelope field and `operationKind: 'prompt' | 'skill' | 'task' |
 * 'shell' | 'compact'` is still on `operation_start`/`operation`, so the
 * latch-the-first-prompt-operation trick below is unchanged. The deltas that do
 * NOT touch us: the envelope version moved `v: 2` → `v: 3`, `run_start`/`run_end`
 * became `agent_start`/`agent_end` (unused here), and `dispatchId` became
 * `submissionId` (unused here). `observe()` keeps its exact signature.
 *
 *   text_delta                  → the delta text
 *   thinking_end                → __THINKING__:<complete thinking text>
 *   message_end (assistant)     → per toolCall block: phase=start, phase=args
 *                                 (prod emits start at toolcall_start and args
 *                                 at message_end — both pre-execution; Flue's
 *                                 earliest reliable point for both is
 *                                 message_end, so relative order is preserved)
 *   tool_start                  → phase=start (only if message_end didn't —
 *                                 defensive against message-shape drift)
 *   tool                        → phase=result
 *   turn (purpose=agent)        → no chunk; records the call's prompt-cache
 *                                 usage for the `{"done": true}` terminal
 *
 * Thinking is mapped at thinking_end (one complete block), not per delta: the
 * consumer renders the payload as a one-shot activity label
 * (chat.py: "Using <label>…"), so partial fragments would flicker nonsense.
 */
export function seamAFrames(event: Record<string, unknown>, state: SeamAState): string[] {
  switch (event?.type) {
    case 'text_delta': {
      const text = typeof event.text === 'string' ? event.text : '';
      if (!text) return [];
      state.streamedText = true;
      return [text];
    }
    case 'thinking_end': {
      const content = typeof event.content === 'string' ? event.content : '';
      return content ? [THINKING_SENTINEL_PREFIX + content] : [];
    }
    case 'message_end': {
      const message = event.message as Record<string, unknown> | undefined;
      if (!message || message.role !== 'assistant' || !Array.isArray(message.content)) return [];
      const frames: string[] = [];
      const textParts: string[] = [];
      for (const block of message.content as Array<Record<string, unknown>>) {
        if (!block || typeof block !== 'object') continue;
        if (block.type === 'text' && typeof block.text === 'string') textParts.push(block.text);
        if (block.type !== 'toolCall') continue;
        const id = block.id;
        const name = block.name;
        if (!id || !name) continue; // mirrors prod: skip blocks missing id/name
        const idStr = String(id);
        if (!state.startedToolIds.has(idStr)) {
          state.startedToolIds.add(idStr);
          frames.push(toolStartSentinel(idStr, String(name)));
        }
        frames.push(toolArgsSentinel(idStr, String(name), block.arguments));
      }
      const text = textParts.join('').trim();
      if (text) state.lastAssistantText = text;
      return frames;
    }
    case 'tool_start': {
      const id = event.toolCallId;
      const name = event.toolName;
      if (!id || !name) return [];
      const idStr = String(id);
      if (state.startedToolIds.has(idStr)) return [];
      state.startedToolIds.add(idStr);
      return [toolStartSentinel(idStr, String(name))];
    }
    case 'tool': {
      const sentinel = toolResultSentinel(event);
      return sentinel === null ? [] : [sentinel];
    }
    case 'turn_request': {
      const input = (event.request as { input?: Record<string, unknown> } | undefined)?.input;
      if (event.purpose !== 'agent' || state.contextBudget || !Array.isArray(input?.messages)) return [];
      try {
        state.contextBudget = promptSections(input as unknown as Context);
      } catch {
        /* accounting must never break the stream */
      }
      return [];
    }
    case 'turn': {
      // One model call (one tool round). Accounting only — never a chunk.
      if (event.purpose !== undefined && event.purpose !== 'agent') return [];
      const round = promptCacheRound(event.response);
      if (round) state.promptCache.push(round);
      return [];
    }
    default:
      return [];
  }
}

// ── NDJSON framing ───────────────────────────────────────────────────────────

/** One NDJSON line. The WIRE framing is plain JSON (consumer json.loads's each
 *  line); only the decoded chunk strings carry the Python-parity bytes. */
export function ndjsonLine(value: unknown): string {
  return JSON.stringify(value) + '\n';
}

// ── The streaming middleware ─────────────────────────────────────────────────

function streamingEnabled(): boolean {
  const v = (process.env.ZOE_BRAIN_STREAM ?? '').trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(v);
}

function timeoutMsFromEnv(): number {
  const raw = Number(process.env.ZOE_BRAIN_STREAM_TIMEOUT_S);
  const s = Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_S;
  return s * 1000;
}

type ObserveFn = typeof observe;

export interface StreamingMiddlewareOptions {
  /** injection seam for offline tests; defaults to @flue/runtime's observe. */
  observeFn?: ObserveFn;
  /** overall turn deadline; defaults to ZOE_BRAIN_STREAM_TIMEOUT_S (180s). */
  timeoutMs?: number;
  /** injection seam for offline tests; defaults to the src/early-text.ts bus. */
  earlyTextSubscribe?: EarlyTextSubscribe;
  /** per-turn log sink; defaults to console.log. */
  log?: (line: string) => void;
}

/**
 * Hono middleware implementing the content-negotiated streaming mode.
 * Register AFTER the auth gate and BEFORE mounting `createAgentRouter(...)` —
 * non-streaming requests fall straight through, streaming requests fall through
 * for admission and then have their 202 upgraded to the NDJSON stream.
 */
export function seamAStreamingMiddleware(opts?: StreamingMiddlewareOptions): MiddlewareHandler {
  const observeFn = opts?.observeFn ?? observe;
  return async (c, next) => {
    if (c.req.method !== 'POST' || !streamingEnabled()) return next();
    const url = new URL(c.req.url);
    const accept = c.req.header('accept') ?? '';
    if (!accept.toLowerCase().includes(NDJSON_CONTENT_TYPE)) return next();
    const match = url.pathname.match(/^\/agents\/([^/]+)\/([^/]+)$/);
    if (!match) return next();
    const instanceId = decodeURIComponent(match[2]);

    // Subscribe BEFORE admission so no event of our turn can be missed.
    const session = openTurnStream(instanceId, observeFn, opts?.timeoutMs ?? timeoutMsFromEnv(), {
      earlyTextSubscribe: earlyTextEnabled() ? (opts?.earlyTextSubscribe ?? subscribeEarlyText) : null,
      log: opts?.log ?? ((line) => console.log(line)),
    });
    try {
      await next(); // flue(): fail-closed route auth + payload validation + admission
    } catch (err) {
      session.abandon();
      throw err;
    }
    if (c.res.status !== 202) {
      // Auth failure / invalid payload / anything unexpected: pass through verbatim.
      session.abandon();
      return;
    }
    // Upgrade the 202 admission to the live NDJSON stream. Hono's res setter
    // carries the 202's Location / Stream-Next-Offset headers over, so the
    // client can still fall back to the durable stream after a disconnect.
    c.res = new Response(session.readable, {
      status: 200,
      headers: { 'content-type': NDJSON_CONTENT_TYPE, 'x-accel-buffering': 'no' },
    });
  };
}

interface TurnStream {
  readable: ReadableStream<Uint8Array>;
  abandon: () => void;
}

/** Sentence end in the text streamed so far — for the per-turn latency log only. */
const SENTENCE_END_RE = /[.!?\u2026]["')\]]*(?:\s|$)/;

interface TurnStreamOptions {
  /** early-text bus; `null` = ZOE_FLUE_EARLY_TEXT off (observe-only stream). */
  earlyTextSubscribe: EarlyTextSubscribe | null;
  log: (line: string) => void;
}

/**
 * Subscribe to runtime events for one agent instance and expose the mapped
 * Seam-A chunk stream as NDJSON bytes. Terminates on the latched prompt
 * operation's end event, on timeout, or on consumer cancel.
 */
function openTurnStream(
  instanceId: string,
  observeFn: ObserveFn,
  timeoutMs: number,
  opts: TurnStreamOptions,
): TurnStream {
  const encoder = new TextEncoder();
  const state = newSeamAState();
  const pending: string[] = [];
  let wake: (() => void) | null = null;
  let finished = false;
  let latchedOperationId: string | null = null;

  const push = (line: string) => {
    pending.push(line);
    wake?.();
    wake = null;
  };

  // Text-latency accounting for the per-turn FLUE_EARLY_TEXT line. The clock
  // starts when the request reaches the middleware (before admission).
  const startedAt = performance.now();
  const earlyText = opts.earlyTextSubscribe ? new EarlyTextReconciler() : null;
  let textFrames = 0;
  let firstDeltaMs = -1;
  let firstSentenceMs = -1;
  let textSoFar = '';
  const pushText = (text: string) => {
    state.streamedText = true;
    textFrames += 1;
    const at = Math.round(performance.now() - startedAt);
    if (firstDeltaMs < 0) firstDeltaMs = at;
    if (firstSentenceMs < 0) {
      textSoFar += text;
      if (SENTENCE_END_RE.test(textSoFar)) firstSentenceMs = at;
    }
    push(ndjsonLine(text));
  };

  let unobserve: () => void = () => {};
  let unsubscribeEarly: () => void = () => {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  const cleanup = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    unobserve();
    unobserve = () => {};
    unsubscribeEarly();
    unsubscribeEarly = () => {};
  };
  const finish = (terminal: Record<string, unknown>) => {
    if (finished) return;
    finished = true;
    cleanup();
    push(ndjsonLine(terminal));
    try {
      opts.log(
        `FLUE_EARLY_TEXT first_delta_ms=${firstDeltaMs} first_sentence_ms=${firstSentenceMs}`
        + ` deltas=${textFrames} deduped=${earlyText?.deduped ?? 0}`
        + ` enabled=${earlyText ? 1 : 0}`
        + (earlyText?.diverged ? ` diverged=${earlyText.diverged}` : ''),
      );
    } catch {
      /* logging must never break the stream */
    }
  };
  const finishOk = () => {
    // Mirror prod's agent_end fallback: if no text delta ever streamed, the
    // complete last assistant message is the answer — emit it as one chunk.
    if (!finished && !state.streamedText && state.lastAssistantText) {
      pushText(state.lastAssistantText);
    }
    finish({
      done: true,
      ...(state.promptCache.length > 0 ? { prompt_cache: state.promptCache } : {}),
      ...(state.contextBudget ? { context_budget: state.contextBudget } : {}),
    });
  };

  timer = setTimeout(() => finish({ error: 'brain turn timed out' }), timeoutMs);

  // Early text (src/early-text.ts): the model's own deltas, ahead of the
  // runtime's 1 s storage flush. Forwarded only for the latched operation and
  // only for model calls whose `turn_request` (purpose `agent`) we saw — so a
  // call we joined mid-way, or a non-conversation call, stays observe-only.
  if (earlyText && opts.earlyTextSubscribe) {
    unsubscribeEarly = opts.earlyTextSubscribe((delta) => {
      try {
        if (finished || latchedOperationId === null) return;
        if (delta.operationId !== latchedOperationId) return;
        if (earlyText.recordEarly(delta.turnId, delta.text)) pushText(delta.text);
      } catch {
        /* never let the tap kill the stream; observe() still carries the text */
      }
    });
  }

  unobserve = observeFn((observation, ctx) => {
    try {
      const event = observation as unknown as Record<string, unknown>;
      if (finished) return;
      if (event.instanceId !== instanceId && ctx?.id !== instanceId) return;
      if (latchedOperationId === null) {
        // First prompt operation to START after we subscribed is our turn
        // (submissions serialize per instance; see the header's known limit).
        // Latch only a real id: if the runtime ever emitted an operation_start
        // WITHOUT one, an empty-string latch would let any id-less `operation`
        // event terminate the stream — instead we never latch and the turn
        // fails safe via the timeout.
        if (event.type === 'operation_start' && event.operationKind === 'prompt'
          && typeof event.operationId === 'string' && event.operationId !== '') {
          latchedOperationId = event.operationId;
        }
        return;
      }
      if (event.type === 'operation' && event.operationKind === 'prompt'
        && event.operationId === latchedOperationId) {
        if (event.isError) {
          const err = event.error;
          let message = 'brain turn failed';
          if (typeof err === 'string') message = err;
          else if (err && typeof err === 'object'
            && typeof (err as Record<string, unknown>).message === 'string') {
            message = (err as Record<string, unknown>).message as string;
          }
          finish({ error: message });
        } else {
          finishOk();
        }
        return;
      }
      if (earlyText) {
        if (event.type === 'turn_request' && event.purpose === 'agent'
          && event.operationId === latchedOperationId && typeof event.turnId === 'string') {
          earlyText.allowTurn(event.turnId);
        }
        if (event.type === 'text_delta') {
          // The flushed copy: forward only what the early tap has not already sent.
          const text = typeof event.text === 'string' ? event.text : '';
          const rest = earlyText.reconcileFlushed(event.turnId, text);
          if (rest) pushText(rest);
          return;
        }
      }
      for (const chunk of seamAFrames(event, state)) {
        if (event.type === 'text_delta') pushText(chunk);
        else push(ndjsonLine(chunk));
      }
    } catch {
      // A malformed event must never kill the stream; the timeout still bounds
      // the turn if the terminal event itself was the malformed one.
    }
  });

  let cancelled = false;
  const readable = new ReadableStream<Uint8Array>({
    async pull(controller) {
      while (pending.length === 0 && !finished) {
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
      if (cancelled) return;
      while (pending.length > 0) controller.enqueue(encoder.encode(pending.shift()!));
      if (finished) controller.close();
    },
    cancel() {
      // Consumer went away mid-turn: stop observing. The turn itself keeps
      // running to completion inside Flue (same as an abandoned prod stream).
      cancelled = true;
      finished = true;
      cleanup();
      wake?.();
      wake = null;
    },
  });

  return {
    readable,
    abandon: () => {
      finished = true;
      cleanup();
      wake?.();
      wake = null;
    },
  };
}
