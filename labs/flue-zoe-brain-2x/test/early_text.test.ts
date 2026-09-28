/**
 * Early text: model deltas reach the NDJSON stream before @flue/runtime's 1 s
 * batched storage flush, exactly once (LAB-ONLY, offline).
 *
 * Three layers:
 *   1. the pure pieces — `textDeltaOf` (which interceptor results are deltas) and
 *      `EarlyTextReconciler` (offset dedupe, incl. its fallbacks);
 *   2. the middleware against a fake observe() + a fake early bus, where the
 *      "flush" is simply the test emitting the flushed copy later — pins
 *      ordering, no-duplicates and the kill switch without any timing;
 *   3. the REAL runtime (in-process `start()`, mock model streaming a delta every
 *      120 ms). The flush here is the runtime's own 1 s timer, not a fake. With
 *      the flag OFF the stream must show that ≥ 0.8 s stall — the negative
 *      control proving this test can see the defect — and with it ON the stall
 *      must be gone, the text byte-identical, and tool frames unchanged.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/early_text.test.ts
 */
import assert from 'node:assert/strict';
import { after, before, describe, it, test } from 'node:test';

import {
  EarlyTextReconciler,
  textDeltaOf,
  type EarlyTextListener,
} from '../src/early-text.ts';
import { NDJSON_CONTENT_TYPE, seamAStreamingMiddleware } from '../src/streaming.ts';
import {
  startBrainHarness,
  userMessageBody,
  type BrainHarness,
} from './helpers/harness.ts';

// ─── 1. pure pieces ──────────────────────────────────────────────────────────

test('textDeltaOf: only a live iterator step carrying a non-empty text_delta counts', () => {
  assert.equal(textDeltaOf({ done: false, value: { type: 'text_delta', delta: 'Hi ', contentIndex: 0 } }), 'Hi ');
  assert.equal(textDeltaOf({ done: false, value: { type: 'text_delta', delta: '' } }), null);
  assert.equal(textDeltaOf({ done: false, value: { type: 'thinking_delta', delta: 'hmm' } }), null);
  assert.equal(textDeltaOf({ done: false, value: { type: 'toolcall_delta', delta: '{"a"' } }), null);
  assert.equal(textDeltaOf({ done: true, value: undefined }), null);
  // the stream-creation call and stream.result() are intercepted too
  assert.equal(textDeltaOf({ [Symbol.asyncIterator]: () => ({}) }), null);
  assert.equal(textDeltaOf({ role: 'assistant', content: [{ type: 'text', text: 'x' }] }), null);
  assert.equal(textDeltaOf(undefined), null);
});

test('reconciler: flushed deltas fully covered by the tap emit nothing', () => {
  const r = new EarlyTextReconciler();
  r.allowTurn('t1');
  for (const d of ['It ', 'looks ', 'sunny.']) assert.equal(r.recordEarly('t1', d), true);
  // the flush may re-chunk; offsets, not chunk identity, decide
  assert.equal(r.reconcileFlushed('t1', 'It '), '');
  assert.equal(r.reconcileFlushed('t1', 'looks '), '');
  assert.equal(r.reconcileFlushed('t1', 'sunny.'), '');
  assert.equal(r.deduped, 3);
  assert.equal(r.diverged, 0);
});

test('reconciler: a tap that fell behind → only the uncovered tail is emitted', () => {
  const r = new EarlyTextReconciler();
  r.allowTurn('t1');
  r.recordEarly('t1', 'It lo');
  assert.equal(r.reconcileFlushed('t1', 'It looks '), 'oks ');
  assert.equal(r.reconcileFlushed('t1', 'sunny.'), 'sunny.');
  // …and the tail it emitted is now covered for any late tapped copy's offsets
  assert.equal(r.recordEarly('t1', 'x'), true);
});

test('reconciler: a turn the tap never admitted is observe-only, and stays that way', () => {
  const r = new EarlyTextReconciler();
  // no allowTurn: e.g. a non-agent (compaction) call, or a call joined mid-way
  assert.equal(r.recordEarly('t9', 'early'), false);
  assert.equal(r.reconcileFlushed('t9', 'early'), 'early');
  // once a turn has gone canonical-only, a later tapped delta is never ALSO sent
  r.allowTurn('t9');
  assert.equal(r.recordEarly('t9', ' more'), false);
  assert.equal(r.reconcileFlushed('t9', ' more'), ' more');
  assert.equal(r.deduped, 0);
});

test('reconciler: divergent flushed text is suppressed and counted, never sent twice', () => {
  const r = new EarlyTextReconciler();
  r.allowTurn('t1');
  r.recordEarly('t1', 'Hello there.');
  assert.equal(r.reconcileFlushed('t1', 'Howdy'), '');
  assert.equal(r.diverged, 1);
});

test('reconciler: offsets are per model call — rounds never cross-consume', () => {
  const r = new EarlyTextReconciler();
  r.allowTurn('round1');
  r.allowTurn('round2');
  r.recordEarly('round1', 'Checking. ');
  r.recordEarly('round2', 'Sunny.');
  assert.equal(r.reconcileFlushed('round2', 'Sunny.'), '');
  assert.equal(r.reconcileFlushed('round1', 'Checking. '), '');
  assert.equal(r.reconcileFlushed(undefined, 'orphan'), 'orphan');
});

// ─── 2. middleware, fake observe + fake early bus ────────────────────────────

function fakeHarness() {
  let subscriber: ((ev: unknown, ctx: { id?: string }) => void) | null = null;
  let earlyListener: EarlyTextListener | null = null;
  let earlySubscribed = false;
  const logs: string[] = [];
  let res: Response | undefined;
  const ctx = {
    req: {
      method: 'POST',
      url: 'http://sidecar.local/agents/zoe/sess-1',
      header: (name: string) => (name.toLowerCase() === 'accept' ? NDJSON_CONTENT_TYPE : undefined),
    },
    get res() {
      return res as Response;
    },
    set res(r: Response) {
      res = r;
    },
  };
  const next = async () => {
    res = new Response('{}', { status: 202 });
  };
  const mw = seamAStreamingMiddleware({
    observeFn: ((fn: (ev: unknown, ctx: { id?: string }) => void) => {
      subscriber = fn;
      return () => {
        subscriber = null;
      };
    }) as never,
    earlyTextSubscribe: (listener) => {
      earlySubscribed = true;
      earlyListener = listener;
      return () => {
        earlyListener = null;
      };
    },
    log: (line) => logs.push(line),
    timeoutMs: 5_000,
  });
  return {
    run: () => mw(ctx as never, next),
    ctx,
    logs,
    get earlySubscribed() {
      return earlySubscribed;
    },
    get earlyReleased() {
      return earlySubscribed && earlyListener === null;
    },
    obs: (ev: Record<string, unknown>) => subscriber?.({ instanceId: 'sess-1', ...ev }, {}),
    early: (turnId: string, text: string, operationId = 'op-1') =>
      earlyListener?.({ operationId, turnId, text }),
  };
}

async function bodyLines(res: Response): Promise<unknown[]> {
  const text = await res.text();
  return text.split('\n').filter((l) => l.length > 0).map((l) => JSON.parse(l));
}

test('middleware: tapped deltas go out before the flush; the flushed copy is dropped; tool frames intact', async () => {
  const h = fakeHarness();
  await h.run();
  assert.equal(h.earlySubscribed, true);
  // a tapped delta before our operation latched is ignored (observe-only)
  h.early('t0', 'PRE-LATCH');
  h.obs({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
  // round 1: a tool call
  h.obs({ type: 'turn_request', purpose: 'agent', turnId: 't1', operationId: 'op-1' });
  h.early('t1', 'Let me check. ');
  h.early('t1', 'FOREIGN', 'op-other'); // another operation's model call
  h.obs({ type: 'text_delta', text: 'Let me check. ', turnId: 't1', operationId: 'op-1' });
  h.obs({
    type: 'message_end',
    message: {
      role: 'assistant',
      content: [
        { type: 'text', text: 'Let me check. ' },
        { type: 'toolCall', id: 'call_1', name: 'get_weather', arguments: {} },
      ],
    },
  });
  h.obs({ type: 'tool', toolCallId: 'call_1', result: 'Sunny.' });
  // round 2: the answer — all tapped deltas BEFORE the flushed batch arrives
  h.obs({ type: 'turn_request', purpose: 'agent', turnId: 't2', operationId: 'op-1' });
  h.early('t2', 'It is ');
  h.early('t2', 'sunny.');
  h.obs({ type: 'text_delta', text: 'It is ', turnId: 't2', operationId: 'op-1' });
  h.obs({ type: 'text_delta', text: 'sunny.', turnId: 't2', operationId: 'op-1' });
  h.obs({ type: 'operation', operationKind: 'prompt', operationId: 'op-1', isError: false });

  assert.deepEqual(await bodyLines(h.ctx.res), [
    'Let me check. ',
    '__TOOL__:{"phase": "start", "id": "call_1", "name": "get_weather"}',
    '__TOOL__:{"phase": "args", "id": "call_1", "name": "get_weather", "args": {}}',
    '__TOOL__:{"phase": "result", "id": "call_1", "result": "Sunny."}',
    'It is ',
    'sunny.',
    { done: true },
  ]);
  assert.equal(h.earlyReleased, true, 'the early subscription is released at the terminal');
  assert.equal(h.logs.length, 1);
  assert.match(h.logs[0], /^FLUE_EARLY_TEXT first_delta_ms=\d+ first_sentence_ms=\d+ deltas=3 deduped=3 enabled=1$/);
});

test('middleware: a model call without a turn_request stays observe-only (no double send)', async () => {
  const h = fakeHarness();
  await h.run();
  h.obs({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
  h.early('t1', 'Hi.'); // no turn_request for t1 seen → not forwarded early
  h.obs({ type: 'text_delta', text: 'Hi.', turnId: 't1', operationId: 'op-1' });
  h.obs({ type: 'operation', operationKind: 'prompt', operationId: 'op-1', isError: false });
  assert.deepEqual(await bodyLines(h.ctx.res), ['Hi.', { done: true }]);
});

test('kill switch ZOE_FLUE_EARLY_TEXT=0: no subscription, flushed deltas pass through as before', async () => {
  process.env.ZOE_FLUE_EARLY_TEXT = '0';
  try {
    const h = fakeHarness();
    await h.run();
    assert.equal(h.earlySubscribed, false);
    h.obs({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
    h.obs({ type: 'turn_request', purpose: 'agent', turnId: 't1', operationId: 'op-1' });
    h.obs({ type: 'text_delta', text: 'It is ', turnId: 't1' });
    h.obs({ type: 'text_delta', text: 'sunny.', turnId: 't1' });
    h.obs({ type: 'operation', operationKind: 'prompt', operationId: 'op-1', isError: false });
    assert.deepEqual(await bodyLines(h.ctx.res), ['It is ', 'sunny.', { done: true }]);
    assert.match(h.logs[0], / deltas=2 deduped=0 enabled=0$/);
  } finally {
    delete process.env.ZOE_FLUE_EARLY_TEXT;
  }
});

// ─── 3. the real runtime and its real 1 s flush ──────────────────────────────

const REPLY = 'Sure thing. It should stay dry and mild all afternoon, with a light breeze later.';
const DELTA_MS = 120;

interface Frame {
  at: number;
  value: unknown;
}

/** POST one streaming turn and timestamp every NDJSON line as it arrives. */
async function timedTurn(h: BrainHarness, sid: string, text: string): Promise<Frame[]> {
  const t0 = performance.now();
  const res = await h.app.fetch(
    new Request(`http://brain.test/agents/zoe/${sid}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${h.token}`,
        accept: NDJSON_CONTENT_TYPE,
      },
      body: userMessageBody(text),
    }),
  );
  assert.equal(res.status, 200);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  const frames: Frame[] = [];
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const at = performance.now() - t0;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      if (line) frames.push({ at, value: JSON.parse(line) });
    }
  }
  return frames;
}

const isText = (f: Frame) => typeof f.value === 'string' && !(f.value as string).startsWith('__');
const textOf = (frames: Frame[]) => frames.filter(isText).map((f) => f.value as string).join('');
/** Largest silence between consecutive text frames. */
function maxTextGap(frames: Frame[]): number {
  const at = frames.filter(isText).map((f) => f.at);
  let gap = 0;
  for (let i = 1; i < at.length; i++) gap = Math.max(gap, at[i] - at[i - 1]);
  return gap;
}

describe('early text on the real runtime (mock model, real 1 s flush)', () => {
  let harness: BrainHarness;
  const logs: string[] = [];
  const realLog = console.log;

  before(async () => {
    console.log = (...args: unknown[]) => {
      const line = args.map(String).join(' ');
      if (line.startsWith('FLUE_EARLY_TEXT')) logs.push(line);
      else realLog(...args);
    };
    harness = await startBrainHarness((call) => {
      // Calls 0/1: plain replies (flag off, then on). Calls 2-5: a tool turn
      // each way — a tool call, then the streamed answer.
      if (call === 2 || call === 4) {
        return { toolCalls: [{ name: 'get_weather', args: {}, id: `call_w${call}` }] };
      }
      return { text: REPLY, deltaDelayMs: DELTA_MS };
    });
  });
  after(async () => {
    console.log = realLog;
    delete process.env.ZOE_FLUE_EARLY_TEXT;
    await harness.stop();
  });

  it('flag OFF (negative control): the runtime flush stalls text ≥ 0.8 s; flag ON: no stall, same bytes, no duplicates', async () => {
    process.env.ZOE_FLUE_EARLY_TEXT = '0';
    const off = await timedTurn(harness, 'early-off', 'tell me something nice');
    process.env.ZOE_FLUE_EARLY_TEXT = '1';
    const on = await timedTurn(harness, 'early-on', 'tell me something nice');

    // Same text, character for character — nothing lost, nothing sent twice.
    assert.equal(textOf(off), REPLY);
    assert.equal(textOf(on), REPLY);
    assert.deepEqual(on[on.length - 1].value, off[off.length - 1].value, 'identical terminal frame');

    // The defect is visible without the tap: the flushed batches arrive ~1 s apart.
    assert.ok(maxTextGap(off) >= 800, `flag off should stall on the 1 s flush, max gap ${maxTextGap(off)} ms`);
    // With the tap, text arrives at the model's own pace (one delta per 120 ms).
    assert.ok(maxTextGap(on) < 600, `flag on must not stall, max gap ${maxTextGap(on)} ms`);
    // One NDJSON text frame per model delta: every flushed copy was deduped.
    const words = REPLY.match(/\S+\s*/g)!.length;
    assert.equal(on.filter(isText).length, words);

    // The first sentence ("Sure thing. ") is out ≥ 0.5 s earlier with the tap.
    const firstSentence = (frames: Frame[]) => {
      let acc = '';
      for (const f of frames.filter(isText)) {
        acc += f.value as string;
        if (/[.!?]\s/.test(acc)) return f.at;
      }
      return Infinity;
    };
    assert.ok(
      firstSentence(on) + 500 <= firstSentence(off),
      `first sentence on=${firstSentence(on).toFixed(0)} ms off=${firstSentence(off).toFixed(0)} ms`,
    );

    assert.match(logs[0], /enabled=0$/);
    assert.match(logs[1], new RegExp(`deltas=${words} deduped=${words} enabled=1$`));
  });

  it('a tool turn streams identical frames with the flag on and off (tool sentinels untouched)', async () => {
    process.env.ZOE_FLUE_EARLY_TEXT = '0';
    const off = await timedTurn(harness, 'tool-off', 'what is the weather like outside?');
    process.env.ZOE_FLUE_EARLY_TEXT = '1';
    const on = await timedTurn(harness, 'tool-on', 'what is the weather like outside?');

    const shape = (frames: Frame[]) =>
      frames.map((f) =>
        typeof f.value === 'string'
          ? (f.value as string).replace(/call_w\d/g, 'call_w')
          : JSON.stringify(f.value),
      );
    const sentinels = (frames: Frame[]) => shape(frames).filter((v) => v.startsWith('__TOOL__:'));
    assert.ok(sentinels(on).length >= 3, 'start + args + result sentinels present');
    assert.deepEqual(sentinels(on), sentinels(off), 'tool sentinels byte-identical');
    // Sentinels precede the answer text in both modes (round order preserved).
    const firstTextIdx = (frames: Frame[]) => frames.findIndex(isText);
    const lastSentinelIdx = (frames: Frame[]) =>
      shape(frames).map((v, i) => (v.startsWith('__TOOL__:') ? i : -1)).reduce((a, b) => Math.max(a, b), -1);
    assert.ok(lastSentinelIdx(on) < firstTextIdx(on));
    assert.ok(lastSentinelIdx(off) < firstTextIdx(off));
    assert.equal(textOf(on), REPLY);
    assert.equal(textOf(off), REPLY);
    assert.ok(maxTextGap(on) < 600, `flag on must not stall, max gap ${maxTextGap(on)} ms`);
    // done terminal (incl. any prompt_cache field) identical in shape
    assert.deepEqual(on[on.length - 1].value, off[off.length - 1].value);
  });
});
