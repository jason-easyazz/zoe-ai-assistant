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
 *   - {"done": true}: the turn completed (always the last line on success), or
 *   - {"error": "<message>"}: the turn failed (always the last line on error).
 * JSON-encoding each chunk keeps chunk boundaries exact (deltas may contain
 * newlines) and is trivial for the Python consumer to map onto the
 * `run_zoe_core_streaming` yield sequence: yield every string line; treat a
 * missing/`error` terminal line as a failed turn (same fallback as today's
 * whole-result error path in `zoe_flue_client`).
 *
 * Mode selection — CONTENT NEGOTIATION, not a breaking change: a client opts
 * in per-request with `Accept: application/x-ndjson` on the existing
 * `POST /agents/:name/:id` route (without `?wait=result`). Everything else is
 * untouched: `?wait=result` keeps returning the whole result (it wins even if
 * the Accept header is present), and a plain POST keeps returning the 202
 * admission. Negotiation fits the consumer because `zoe_flue_client` already
 * owns its request headers per-call and can flip modes without a sidecar
 * restart or a second endpoint. Belt-and-braces kill switch:
 * `ZOE_BRAIN_STREAM=0|false|no|off` disables interception entirely.
 *
 * Auth is NOT re-implemented here: streaming requests pass through `next()`
 * first, so the agent module's exported fail-closed `route` handler
 * (ZOE_BRAIN_TOKEN / ZOE_BRAIN_OPEN, src/agents/zoe.ts) plus Flue's payload
 * validation run unchanged; only a 202 admission is upgraded to a stream — a
 * 401/400 passes through verbatim. Identity binding (ZOE_BRAIN_USER_ID) and
 * the ZOE_BRAIN_ALLOW_WRITES gate live in the tools and are untouched.
 *
 * Event source: `observe()` (in-process, synchronous, full fidelity). The
 * durable stream (`GET /agents/:name/:id?live=...`) is NOT used because the
 * runtime buffers `text_delta`/`thinking_*` persistence for ~3s
 * (BUFFERED_RUN_EVENT_TYPES in @flue/runtime) — unusable for voice TTFT.
 *
 * Correlation: the subscriber filters on the instance id, latches the first
 * `operation_start` with `operationKind === 'prompt'` after our admission
 * (session operations are exclusive per instance, and submissions serialize),
 * and finishes on that operation's end event. Known lab limit: two turns
 * admitted CONCURRENTLY for the SAME session id can mis-latch — upstream
 * zoe-data never does this (a session's turns are strictly sequential).
 *
 * LAB ONLY.
 */
import { observe } from '@flue/runtime';
import type { MiddlewareHandler } from 'hono';

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

export interface SeamAState {
  /** tool-call ids whose phase=start sentinel has been emitted (dedupe). */
  startedToolIds: Set<string>;
  /** whether any plain text delta has streamed (gates the terminal fallback). */
  streamedText: boolean;
  /** complete text of the last assistant message — the no-deltas fallback. */
  lastAssistantText: string;
}

export function newSeamAState(): SeamAState {
  return { startedToolIds: new Set(), streamedText: false, lastAssistantText: '' };
}

/**
 * Map one observed Flue runtime event to zero or more Seam-A chunks, mutating
 * `state`. Mirrors zoe_core_client._read_turn's mapping of the SAME underlying
 * pi-agent-core activity, defensively (a malformed event maps to nothing):
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
}

/**
 * Hono middleware implementing the content-negotiated streaming mode.
 * Register BEFORE mounting `flue()` — non-streaming requests fall straight
 * through, streaming requests fall through for auth+admission and then have
 * their 202 upgraded to the NDJSON stream.
 */
export function seamAStreamingMiddleware(opts?: StreamingMiddlewareOptions): MiddlewareHandler {
  const observeFn = opts?.observeFn ?? observe;
  return async (c, next) => {
    if (c.req.method !== 'POST' || !streamingEnabled()) return next();
    const url = new URL(c.req.url);
    // ?wait=result wins: the whole-result contract is untouched even if the
    // caller also sent the streaming Accept header.
    if (url.searchParams.get('wait') === 'result') return next();
    const accept = c.req.header('accept') ?? '';
    if (!accept.toLowerCase().includes(NDJSON_CONTENT_TYPE)) return next();
    const match = url.pathname.match(/^\/agents\/([^/]+)\/([^/]+)$/);
    if (!match) return next();
    const instanceId = decodeURIComponent(match[2]);

    // Subscribe BEFORE admission so no event of our turn can be missed.
    const session = openTurnStream(instanceId, observeFn, opts?.timeoutMs ?? timeoutMsFromEnv());
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

/**
 * Subscribe to runtime events for one agent instance and expose the mapped
 * Seam-A chunk stream as NDJSON bytes. Terminates on the latched prompt
 * operation's end event, on timeout, or on consumer cancel.
 */
function openTurnStream(instanceId: string, observeFn: ObserveFn, timeoutMs: number): TurnStream {
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

  let unobserve: () => void = () => {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  const cleanup = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    unobserve();
    unobserve = () => {};
  };
  const finish = (terminal: Record<string, unknown>) => {
    if (finished) return;
    finished = true;
    cleanup();
    push(ndjsonLine(terminal));
  };
  const finishOk = () => {
    // Mirror prod's agent_end fallback: if no text delta ever streamed, the
    // complete last assistant message is the answer — emit it as one chunk.
    if (!finished && !state.streamedText && state.lastAssistantText) {
      push(ndjsonLine(state.lastAssistantText));
    }
    finish({ done: true });
  };

  timer = setTimeout(() => finish({ error: 'brain turn timed out' }), timeoutMs);

  unobserve = observeFn((observation, ctx) => {
    try {
      const event = observation as unknown as Record<string, unknown>;
      if (finished) return;
      if (event.instanceId !== instanceId && ctx?.id !== instanceId) return;
      if (latchedOperationId === null) {
        // First prompt operation to START after we subscribed is our turn
        // (submissions serialize per instance; see the header's known limit).
        if (event.type === 'operation_start' && event.operationKind === 'prompt') {
          latchedOperationId = String(event.operationId ?? '');
        }
        return;
      }
      if (event.type === 'operation' && event.operationKind === 'prompt'
        && String(event.operationId ?? '') === latchedOperationId) {
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
      for (const chunk of seamAFrames(event, state)) push(ndjsonLine(chunk));
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
