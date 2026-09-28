/**
 * Early text: forward the model's streamed text to the NDJSON turn stream the
 * moment the model produces it, instead of after @flue/runtime's batched
 * storage write.
 *
 * THE DELAY THIS REMOVES (measured 2026-09-28,
 * docs/knowledge/panel-ttfa-breakdown-2026-09-28.md). In @flue/runtime 2.1.1 the
 * `text_delta` that `observe()` subscribers see is the PUBLISH CALLBACK of a
 * batched canonical write: `conversation-stream-store-*.mjs` does
 * `enqueueCanonical([assistant_text_delta], () => this.emit({type: 'text_delta'}))`,
 * and `ConversationRecordWriter.enqueue` (`sql-agent-execution-store-*.mjs`)
 * flushes that batch on a microtask only when the previous flush started ≥ 1 s
 * ago — otherwise on `setTimeout(CANONICAL_FLUSH_DELAY_MS = 1e3)`. So the first
 * delta of a reply goes out at once and every later one waits up to a second:
 * one early token, ~0.84–1.09 s of silence, then 17–26 deltas inside 2 ms,
 * while llama-server decodes steadily at 20–27 tok/s. Median first token →
 * first speakable sentence was 1.08 s.
 *
 * `CANONICAL_FLUSH_DELAY_MS` is a module-private `const` — no export, no env
 * knob, no config field (checked on 2.1.1: `FlueInstrumentation`,
 * `AgentRuntimeConfig`, `DurabilityConfig` and the node `start()` options carry
 * nothing for it). Lowering it would mean patching node_modules. This module
 * leaves the runtime and its store completely alone instead.
 *
 * THE TAP. `@flue/runtime` exposes a supported instrumentation seam,
 * `instrument({ observe, interceptor, dispose })`. Every model call of the agent
 * loop runs through the interceptor chain as a `{ type: 'model', turnId }`
 * operation, and — this is the part that matters — so does every `next()` on
 * the provider's event stream (`wrapProviderStream` wraps `iterator.next()` in
 * `interceptExecution`). That provider stream is exactly the one
 * `src/providers/capped-completions.ts` returns, i.e. pi-ai's parse of
 * llama-server's SSE. So an interceptor sees each `text_delta` the instant the
 * agent loop pulls it — BEFORE the loop hands it to the runtime's store — and
 * with the execution context attached (`operationId`, `turnId`), which is the
 * correlation the provider itself does not have (it never learns which agent
 * instance or operation it is serving).
 *
 * This file only PUBLISHES those deltas on an in-process bus. It never touches
 * the event, the stream, or the store: the interceptor returns `next()`'s value
 * unchanged, so the canonical transcript is written exactly as before, and
 * `observe()` still delivers its (now late) `text_delta`s. `src/streaming.ts`
 * forwards the early copy and uses `EarlyTextReconciler` to drop the flushed
 * copy by offset, so no text is ever emitted twice.
 *
 * Flag: `ZOE_FLUE_EARLY_TEXT` — on by default; `0|false|no|off` is the kill
 * switch and restores the pre-tap stream exactly (the streaming middleware then
 * never subscribes, and with no subscriber the interceptor is a pass-through).
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { instrument } from '@flue/runtime';
import type { FlueExecutionInterceptor, FlueInstrumentation } from '@flue/runtime';

/** One model text delta, as the provider produced it, with its correlation. */
export interface EarlyTextDelta {
  /** The agent operation (one `prompt` = one turn) the model call belongs to. */
  operationId: string;
  /** The model call (one tool round) within that operation. */
  turnId: string;
  text: string;
}

export type EarlyTextListener = (delta: EarlyTextDelta) => void;
export type EarlyTextSubscribe = (listener: EarlyTextListener) => () => void;

const listeners = new Set<EarlyTextListener>();

/** Subscribe to early text deltas from every in-process model call. */
export const subscribeEarlyText: EarlyTextSubscribe = (listener) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/** Publish one delta to every subscriber. A throwing subscriber never breaks the model stream. */
export function publishEarlyText(delta: EarlyTextDelta): void {
  for (const listener of [...listeners]) {
    try {
      listener(delta);
    } catch {
      /* a subscriber's bug must never reach the agent loop */
    }
  }
}

/** `ZOE_FLUE_EARLY_TEXT`: default ON; `0|false|no|off` disables. Read per turn. */
export function earlyTextEnabled(): boolean {
  const v = (process.env.ZOE_FLUE_EARLY_TEXT ?? '').trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(v);
}

/**
 * If `result` is a provider-stream iterator step carrying a text delta, return
 * the delta text. The interceptor also wraps the call that CREATES the stream
 * (result = the stream wrapper) and `stream.result()` (result = the final
 * assistant message); neither has the `{ done, value }` iterator shape.
 */
export function textDeltaOf(result: unknown): string | null {
  if (!result || typeof result !== 'object') return null;
  const step = result as { done?: unknown; value?: unknown };
  if (step.done !== false || !step.value || typeof step.value !== 'object') return null;
  const event = step.value as { type?: unknown; delta?: unknown };
  if (event.type !== 'text_delta' || typeof event.delta !== 'string' || !event.delta) return null;
  return event.delta;
}

/**
 * The execution interceptor. Pass-through for everything; for a model
 * operation's iterator steps, publishes text deltas AFTER `next()` resolves and
 * returns its value untouched. Skips all work when nothing is subscribed.
 */
export const earlyTextInterceptor: FlueExecutionInterceptor = async (operation, ctx, next) => {
  if (operation.type !== 'model' || listeners.size === 0) return next();
  const result = await next();
  try {
    const text = textDeltaOf(result);
    const operationId = ctx?.operationId;
    const turnId = operation.turnId ?? ctx?.turnId;
    if (text !== null && typeof operationId === 'string' && operationId && turnId) {
      publishEarlyText({ operationId, turnId, text });
    }
  } catch {
    /* never let the tap fail the model call */
  }
  return result;
};

/**
 * ONE module-level instrumentation object: `instrument()` returns the existing
 * disposer when handed the same object again (no `key`, so no dev-mode
 * duplicate-key error), which makes `installEarlyTextTap()` idempotent across the
 * test suite's repeated `createApp()` calls.
 */
const EARLY_TEXT_INSTRUMENTATION: FlueInstrumentation = {
  observe: () => {},
  interceptor: earlyTextInterceptor,
  dispose: () => {},
};

/** Install the tap (idempotent). Called once from `createApp()`. */
export function installEarlyTextTap(): () => Promise<void> {
  return instrument(EARLY_TEXT_INSTRUMENTATION);
}

/**
 * Per-turn dedupe between the early (tapped) copy of the model's text and the
 * runtime's flushed `text_delta` copy.
 *
 * Both copies are the same deltas in the same order — the tap sees each one
 * strictly before the agent loop enqueues it for the store — but the flushed
 * `text_delta` observation carries no sequence number, only the decorated
 * `turnId`. So this reconciles by CHARACTER OFFSET per model call: each flushed
 * delta consumes the next `text.length` characters of what the tap already
 * forwarded for that turn.
 *
 *   - fully covered  → emit nothing (the normal case; counted as `deduped`);
 *   - partly covered → emit only the uncovered tail (the tap fell behind);
 *   - no tap record for the turn → emit the flushed delta as-is, and pin the
 *     turn canonical-only so a later tapped delta for it is never ALSO sent;
 *   - covered text differs → emit nothing (counted as `diverged`). The listener
 *     already heard the tapped text; sending a second, different version would
 *     be worse than either alone. Unreachable given the ordering above, and
 *     visible in the per-turn log line if it ever happens.
 */
export class EarlyTextReconciler {
  private readonly turns = new Map<string, { early: string; canon: number; canonicalOnly: boolean }>();
  private readonly allowed = new Set<string>();
  deduped = 0;
  diverged = 0;

  /** Admit a model call (from its `turn_request`, purpose `agent`) for early forwarding. */
  allowTurn(turnId: string): void {
    if (turnId) this.allowed.add(turnId);
  }

  /** Record a tapped delta. Returns whether it should be forwarded now. */
  recordEarly(turnId: string, text: string): boolean {
    if (!text || !this.allowed.has(turnId)) return false;
    let rec = this.turns.get(turnId);
    if (!rec) {
      rec = { early: '', canon: 0, canonicalOnly: false };
      this.turns.set(turnId, rec);
    }
    if (rec.canonicalOnly) return false;
    rec.early += text;
    return true;
  }

  /** Reconcile one flushed `text_delta`; returns the text still to emit ('' = none). */
  reconcileFlushed(turnId: unknown, text: string): string {
    if (!text) return '';
    const key = typeof turnId === 'string' ? turnId : '';
    let rec = key ? this.turns.get(key) : undefined;
    if (!rec) {
      if (key) this.turns.set(key, { early: '', canon: text.length, canonicalOnly: true });
      return text;
    }
    if (rec.canonicalOnly) {
      rec.canon += text.length;
      return text;
    }
    const covered = rec.early.slice(rec.canon, rec.canon + text.length);
    rec.canon += text.length;
    if (covered === text) {
      this.deduped += 1;
      return '';
    }
    if (text.startsWith(covered)) {
      const rest = text.slice(covered.length);
      rec.early += rest;
      if (covered) this.deduped += 1;
      return rest;
    }
    this.diverged += 1;
    return '';
  }
}
