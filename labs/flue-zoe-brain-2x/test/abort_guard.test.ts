/**
 * A1/A2/A5 (src/turn-guard.ts), all OPT-IN per request (`x-zoe-abort-on-cancel: 1`):
 * the guard refuses an abort that could reach the next turn; the middleware is
 * inert without the header and with it echoes the submission, aborts on cancel and
 * runs the deadlines (table) into the {"error"} fallback; on the REAL runtime an
 * opt-in cancel closes the model request (what frees llama-server's slot) while the
 * opt-out cancel — the negative control — lets the model call run to completion.
 */import assert from 'node:assert/strict';
import { after, before, describe, it, test } from 'node:test';

import { activityKindOf, type ModelActivity, type ModelActivityListener } from '../src/early-text.ts';
import { NDJSON_CONTENT_TYPE, seamAStreamingMiddleware } from '../src/streaming.ts';
import {
  DEFAULT_FIRST_CHUNK_MS,
  DEFAULT_STALL_MS,
  TurnGuard,
  abortGuardRequested,
  deadlineMsFromEnv,
} from '../src/turn-guard.ts';
import { startBrainHarness, waitFor, type BrainHarness } from './helpers/harness.ts';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ─── 1. TurnGuard ────────────────────────────────────────────────────────────

describe('TurnGuard', () => {
  const admitted = async (guard: TurnGuard, ...subs: string[]) => {
    for (const sub of subs) {
      await guard.beforeAdmission('s');
      guard.afterAdmission('s', sub);
    }
  };

  it('aborts only the live turn: never a superseded, unknown, unnamed or still-admitting one', async () => {
    const calls: string[] = [];
    const guard = new TurnGuard(async (id) => void calls.push(id));
    assert.equal((await guard.abort('s', 'sub-1')).outcome, 'skipped:unknown_instance');
    assert.equal((await guard.abort('s', null)).outcome, 'skipped:no_submission_id');
    await admitted(guard, 'sub-1', 'sub-2');
    assert.equal((await guard.abort('s', 'sub-1')).outcome, 'skipped:superseded');
    void guard.beforeAdmission('s'); // the next turn is being admitted right now
    assert.equal((await guard.abort('s', 'sub-2')).outcome, 'skipped:admission_in_flight');
    guard.afterAdmission('s', null); // it failed admission: sub-2 is live again
    assert.deepEqual(calls, []);
    assert.equal((await guard.abort('s', 'sub-2')).outcome, 'requested');
    assert.deepEqual(calls, ['s']);
  });

  it('holds a new admission until an in-flight abort has been recorded', async () => {
    let release!: () => void;
    const order: string[] = [];
    const guard = new TurnGuard(() => new Promise<void>((r) => {
      release = () => r(void order.push('abort-recorded'));
    }));
    await admitted(guard, 'sub-1');
    const aborting = guard.abort('s', 'sub-1');
    const next = guard.beforeAdmission('s').then(() => order.push('admitted'));
    await sleep(20);
    assert.deepEqual(order, []);
    release();
    await Promise.all([aborting, next]);
    assert.deepEqual(order, ['abort-recorded', 'admitted']);
  });

  it('never throws: a failing abort is reported, not raised', async () => {
    const guard = new TurnGuard(() => {
      throw new Error('boom');
    });
    await admitted(guard, 'sub-1');
    assert.equal((await guard.abort('s', 'sub-1')).outcome, 'error');
  });
});

test('activityKindOf: pi-ai "start" (headers) is not output; done/error/iterator end close the call', () => {
  assert.equal(activityKindOf({ done: false, value: { type: 'start' } }), 'open');
  assert.equal(activityKindOf({ done: false, value: { type: 'text_delta', delta: 'x' } }), 'step');
  assert.equal(activityKindOf({ done: false, value: { type: 'toolcall_delta' } }), 'step');
  assert.equal(activityKindOf({ done: false, value: { type: 'done' } }), 'end');
  assert.equal(activityKindOf({ done: false, value: { type: 'error' } }), 'end');
  assert.equal(activityKindOf({ done: true, value: undefined }), 'end');
  assert.equal(activityKindOf({ [Symbol.asyncIterator]: () => null }), 'idle'); // the stream wrapper
  assert.equal(activityKindOf(undefined), 'idle');
});

test('opt-in header, kill switch and deadline env parsing', () => {
  const withEnv = (name: string, value: string | undefined, fn: () => void) => {
    const saved = process.env[name];
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
    try {
      fn();
    } finally {
      if (saved === undefined) delete process.env[name];
      else process.env[name] = saved;
    }
  };
  withEnv('ZOE_FLUE_ABORT_GUARD', undefined, () => {
    assert.equal(abortGuardRequested('1'), true);
    for (const v of [undefined, null, '', '0', 'true']) assert.equal(abortGuardRequested(v), false);
  });
  withEnv('ZOE_FLUE_ABORT_GUARD', 'off', () => assert.equal(abortGuardRequested('1'), false));
  for (const [raw, want] of [[undefined, DEFAULT_STALL_MS], ['2500', 2500], ['0', 0], ['-5', DEFAULT_STALL_MS], ['abc', DEFAULT_STALL_MS]] as const) {
    withEnv('ZOE_FLUE_STALL_MS', raw, () => assert.equal(deadlineMsFromEnv('ZOE_FLUE_STALL_MS', DEFAULT_STALL_MS), want));
  }
  assert.deepEqual([DEFAULT_FIRST_CHUNK_MS, DEFAULT_STALL_MS], [30_000, 10_000]);
});

// ─── 2. middleware, offline ──────────────────────────────────────────────────

type Subscriber = (observation: unknown, ctx: { id?: string }) => void;

function fakeTurn(opts: { optIn: boolean; firstChunkMs?: number; stallMs?: number }) {
  let subscriber: Subscriber | null = null;
  const activity = new Set<ModelActivityListener>();
  const aborts: string[] = [];
  const logs: string[] = [];
  const guard = new TurnGuard(async (id) => void aborts.push(id));
  const headers: Record<string, string> = { accept: NDJSON_CONTENT_TYPE };
  if (opts.optIn) headers['x-zoe-abort-on-cancel'] = '1';
  const ctx = {
    req: { method: 'POST', url: 'http://sidecar.local/agents/zoe/sess-1', header: (n: string) => headers[n.toLowerCase()] },
    res: undefined as unknown as Response,
    set: () => {},
  };
  const mw = seamAStreamingMiddleware({
    observeFn: ((cb: Subscriber) => ((subscriber = cb), () => {})) as never,
    timeoutMs: 5_000,
    earlyTextSubscribe: () => () => {},
    log: (line) => logs.push(line),
    guard,
    subscribeActivity: (l) => (activity.add(l), () => void activity.delete(l)),
    firstChunkMs: opts.firstChunkMs ?? 0,
    stallMs: opts.stallMs ?? 0,
  });
  return {
    run: async () => {
      await guard.beforeAdmission('sess-1'); // what turnGuardMiddleware does around it
      await mw(ctx as never, async () => {
        ctx.res = new Response(JSON.stringify({ submissionId: 'sub-1' }), { status: 202 });
      });
      guard.afterAdmission('sess-1', 'sub-1');
    },
    ctx,
    aborts,
    logs,
    emit: (ev: Record<string, unknown>) => subscriber!({ instanceId: 'sess-1', ...ev }, {}),
    act: (kind: ModelActivity['kind'], turnId = 't1') => {
      for (const l of activity) l({ operationId: 'op-1', turnId, kind });
    },
    get activitySubscribed() {
      return activity.size > 0;
    },
  };
}

async function readAll(res: Response): Promise<unknown[]> {
  const text = await res.text();
  return text.split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

for (const optIn of [false, true]) {
  test(`${optIn ? 'opt-in: echoes the submission, aborts it on cancel' : 'opt-out: no echo, no activity tap, never aborts (today)'}`, async () => {
    const t = fakeTurn({ optIn, firstChunkMs: optIn ? 0 : 20, stallMs: optIn ? 0 : 20 });
    await t.run();
    assert.equal(t.ctx.res.headers.get('x-flue-submission-id'), optIn ? 'sub-1' : null);
    assert.equal(t.activitySubscribed, false); // deadlines 0 (opt-in) / no opt-in: no tap
    t.emit({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
    t.emit({ type: 'text_delta', text: 'Hello there' });
    const reader = t.ctx.res.body!.getReader();
    await reader.read();
    await reader.cancel();
    await waitFor(() => t.logs.some((l) => l.startsWith('FLUE_ABORT')), optIn ? 1_000 : 50);
    assert.deepEqual(t.aborts, optIn ? ['sess-1'] : []);
    const line = t.logs.find((l) => l.startsWith('FLUE_ABORT')) ?? '';
    if (optIn) assert.match(line, /session=sess-1 submission=sub-1 reason=disconnect emitted_chars=11 deltas=1 outcome=requested/);
    else assert.equal(line, '');
  });
}

test('opt-in: a turn that already finished is not aborted by a late cancel', async () => {
  const t = fakeTurn({ optIn: true });
  await t.run();
  t.emit({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
  t.emit({ type: 'operation', operationKind: 'prompt', operationId: 'op-1' });
  assert.deepEqual((await readAll(t.ctx.res)).at(-1), { done: true });
  assert.deepEqual(t.aborts, []);
});

// Deadlines, table-driven. Timings in ms; first=80 / stall=60 armed.
const DEADLINES: Array<{
  name: string;
  script: Array<[number, ModelActivity['kind']]>;
  expect: 'first_chunk' | 'stall' | null;
  optIn?: false;
}> = [
  { name: 'silent model past the first-chunk deadline', script: [[0, 'wait']], expect: 'first_chunk' },
  { name: 'headers only (pi-ai "start") do not count as output', script: [[0, 'wait'], [10, 'open'], [0, 'wait']], expect: 'first_chunk' },
  { name: 'output then silence past the stall deadline', script: [[0, 'wait'], [20, 'step'], [0, 'wait']], expect: 'stall' },
  { name: 'steady output inside both deadlines', script: [[0, 'wait'], [40, 'step'], [0, 'wait'], [40, 'step'], [0, 'wait'], [40, 'step'], [0, 'end']], expect: null },
  { name: 'a finished call (tool running after it) is not timed', script: [[0, 'wait'], [20, 'step'], [0, 'end']], expect: null },
  // negative control for the table: the same silent model without the opt-in
  { name: 'opt-out: a silent model is never cut off', script: [[0, 'wait']], expect: null, optIn: false },
];

for (const row of DEADLINES) {
  test(`deadline: ${row.name}`, async () => {
    const t = fakeTurn({ optIn: row.optIn ?? true, firstChunkMs: 80, stallMs: 60 });
    await t.run();
    t.emit({ type: 'operation_start', operationKind: 'prompt', operationId: 'op-1' });
    for (const [delay, kind] of row.script) {
      if (delay) await sleep(delay);
      t.act(kind);
    }
    await sleep(220);
    if (row.expect === null) {
      t.emit({ type: 'operation', operationKind: 'prompt', operationId: 'op-1' });
      assert.deepEqual((await readAll(t.ctx.res)).at(-1), { done: true });
      assert.deepEqual(t.aborts, []);
      return;
    }
    const last = (await readAll(t.ctx.res)).at(-1) as { error?: string };
    // The {"error"} terminal is the seam's existing fallback path (zoe-data
    // serves its canned reply when no text went out), never a hang.
    assert.match(String(last.error), new RegExp(`stalled \\(${row.expect}:`));
    await waitFor(() => t.aborts.length > 0, 1_000);
    assert.deepEqual(t.aborts, ['sess-1']);
    assert.ok(t.logs.some((l) => l.includes(`reason=${row.expect} `)));
  });
}

// ─── 3. the real runtime ─────────────────────────────────────────────────────

describe('real runtime (in-process start(), mock model)', () => {
  let h: BrainHarness;
  const long = { text: Array.from({ length: 40 }, (_, i) => `word${i}`).join(' '), deltaDelayMs: 40 };
  before(async () => {
    h = await startBrainHarness((call) => (call === 2 ? { text: 'Second reply.' } : long));
  });
  after(async () => {
    await h.stop();
  });

  const turn = (id: string, body: string, optIn: boolean) =>
    h.send(id, body, {
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${h.token}`,
        accept: NDJSON_CONTENT_TYPE,
        ...(optIn ? { 'x-zoe-abort-on-cancel': '1' } : {}),
      },
    });

  const cancelAfterFirstText = async (res: Response) => {
    const reader = res.body!.getReader();
    let seen = '';
    while (!seen.includes('word2')) seen += new TextDecoder().decode((await reader.read()).value);
    await reader.cancel();
  };

  it('opt-out cancel: the model call runs on to completion (the defect this fixes)', async () => {
    const before = h.model.disconnects;
    await cancelAfterFirstText(await turn('rt-off', 'story', false));
    await sleep(500);
    assert.equal(h.model.disconnects, before);
  });

  it('opt-in cancel: the model request is closed at once and the next turn is untouched', async () => {
    await waitFor(() => h.model.callCount === 1, 5_000); // the opt-out call above
    await sleep(1_800); // let it finish so it cannot be the one we observe
    const before = h.model.disconnects;
    const res = await turn('rt-on', 'story', true);
    const sub = res.headers.get('x-flue-submission-id');
    assert.match(String(sub), /^sub_/);
    await cancelAfterFirstText(res);
    assert.ok(await waitFor(() => h.model.disconnects === before + 1, 2_000), 'model call not aborted');

    // The very next turn on the same session completes normally.
    const next = await turn('rt-on', 'and now?', true);
    const lines = await readAll(next);
    assert.equal(lines.filter((l) => typeof l === 'string').join(''), 'Second reply.');
    assert.equal((lines.at(-1) as { done?: boolean }).done, true);

    // A late guarded abort for the OLD submission is refused, not instance-wide.
    const late = await h.app.fetch(new Request('http://brain.test/agents/zoe/rt-on/abort', {
      method: 'POST',
      headers: { authorization: `Bearer ${h.token}`, 'x-zoe-abort-submission': String(sub) },
    }));
    assert.equal(((await late.json()) as { outcome: string }).outcome, 'skipped:superseded');
  });
});

test('A2: bounded durability is pinned on the agent', async () => {
  const { Zoe } = await import('../src/agents/zoe.ts');
  assert.deepEqual((Zoe as unknown as { durability: unknown }).durability, { maxAttempts: 2, timeoutMs: 120_000 });
});
