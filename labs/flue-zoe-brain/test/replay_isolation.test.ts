/**
 * Replay write-isolation — the sidecar half.
 *
 * THE LOAD-BEARING PAIR, both halves against a real in-process zoe-data stand-in
 * with ZOE_BRAIN_ALLOW_WRITES=true (the live sidecar's actual configuration):
 *
 *   - marker ABSENT  → every write tool POSTs to /api/system/intent-dispatch,
 *                      exactly as today. This is the CONTROL: it proves the test
 *                      would notice if isolation leaked into live traffic, and
 *                      that the isolation half is not passing vacuously.
 *   - marker PRESENT → ZERO mutating HTTP, and each tool still returns
 *                      success-shaped text (so replay_samples.py::_classify keeps
 *                      scoring the turn exactly as it did).
 *
 * Reads are deliberately UNAFFECTED — the replay gate needs real recall to score
 * said-vs-did, so only the write gate is diverted.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/replay_isolation.test.ts
 */
import assert from 'node:assert/strict';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { test } from 'node:test';

import { bindTurnReplayMode, isReplayTurn, wrapMessageWithReplay } from '../src/replay-mode.ts';
import {
  applyPolicies,
  bindIdentityForRound,
} from '../src/providers/capped-completions.ts';
import { currentUserId } from '../src/request-identity.ts';
import { wrapMessageWithIdentity } from '../src/request-identity.ts';

const ACTING_USER = 'replay-isolation-user';

type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};

/**
 * Every model-facing WRITE tool, with the success-shaped text each must still
 * produce under isolation. `set_timer` is here on purpose: it is the ONE write
 * that does not route through runWrite (it carries its own inline gate), so a fix
 * applied only to runWrite would leave it dispatching — this case is what catches
 * that.
 */
const WRITE_CASES: Array<{ name: string; input: Record<string, unknown>; expect: RegExp }> = [
  { name: 'shopping_list_add', input: { item: 'oat milk' }, expect: /Added "oat milk" to your shopping list\./ },
  { name: 'add_to_list', input: { item: 'call the plumber', list_type: 'tasks' }, expect: /Added "call the plumber" to your tasks list\./ },
  { name: 'list_remove', input: { item: 'bread', list_type: 'shopping' }, expect: /Removed "bread" from your shopping list\./ },
  { name: 'add_reminder', input: { title: 'take bins out' }, expect: /remind you to take bins out/ },
  { name: 'add_calendar_event', input: { title: 'dentist', date: '2026-07-04' }, expect: /Added "dentist" to your calendar\./ },
  { name: 'create_note', input: { title: 'n', content: 'body' }, expect: /Saved your note\./ },
  { name: 'journal', input: { action: 'create', content: 'a good day' }, expect: /Saved your journal entry\./ },
  { name: 'people', input: { action: 'create', name: 'Sarah', relationship: 'colleague' }, expect: /Added Sarah to your contacts\./ },
  { name: 'remember_fact', input: { fact: 'my anniversary is June 3rd' }, expect: /Got it — I'll remember that\./ },
  { name: 'remember_emotional_moment', input: { moment: 'anxious about settlement' }, expect: /Got it — I'll keep that in mind\./ },
  { name: 'media', input: { action: 'play', query: 'some jazz' }, expect: /Playing some jazz\./ },
  { name: 'media', input: { action: 'control', command: 'pause' }, expect: /Done\./ },
  { name: 'media', input: { action: 'set_music_volume', level: 30 }, expect: /Music volume set to 30\./ },
  { name: 'media', input: { action: 'system_volume', direction: 'up' }, expect: /Turned my speaking volume up\./ },
  { name: 'media', input: { action: 'setup' }, expect: /get your music connected/ },
  { name: 'home', input: { action: 'on', room: 'kitchen' }, expect: /Turned the kitchen lights on\./ },
  { name: 'set_timer', input: { minutes: 7, label: 'tea' }, expect: /can't reliably start a real timer/ },
];

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : undefined;
}

type Fake = { baseUrl: string; posts: unknown[]; close: () => Promise<void> };

async function startFakeZoeData(): Promise<Fake> {
  const posts: unknown[] = [];
  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const body = await readJson(req);
    posts.push(body);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    // A confirming response with an EMPTY result, so the live (non-isolated) path
    // falls through to the tool's own successFallback — the very string the
    // isolated path returns. That makes the two halves textually comparable.
    res.end(JSON.stringify({ intent: (body as { intent?: string })?.intent ?? '', ok: true, result: '' }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    posts,
    close: () => new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve()))),
  };
}

/** Load zoe-tools with writes ENABLED — the live sidecar's real configuration. */
async function withWriteEnabledTools(baseUrl: string, fn: (tools: RunnableTool[]) => Promise<void>) {
  const prev = {
    url: process.env.ZOE_DATA_URL,
    uid: process.env.ZOE_BRAIN_USER_ID,
    writes: process.env.ZOE_BRAIN_ALLOW_WRITES,
  };
  process.env.ZOE_DATA_URL = baseUrl;
  process.env.ZOE_BRAIN_USER_ID = ACTING_USER;
  process.env.ZOE_BRAIN_ALLOW_WRITES = 'true';
  try {
    // ALLOW_WRITES is read at module evaluation, so bust the ESM cache per load.
    const mod = await import(`../src/tools/zoe-tools.ts?replay=${Date.now()}-${Math.random()}`);
    await fn(mod.zoeTools as unknown as RunnableTool[]);
  } finally {
    for (const [k, v] of Object.entries({
      ZOE_DATA_URL: prev.url, ZOE_BRAIN_USER_ID: prev.uid, ZOE_BRAIN_ALLOW_WRITES: prev.writes,
    })) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
}

function byName(tools: RunnableTool[], name: string): RunnableTool {
  const t = tools.find((c) => c.name === name);
  assert.ok(t, `expected ${name} to be registered`);
  return t;
}

// ── THE PAIR ────────────────────────────────────────────────────────────────

test('CONTROL — no replay marker: every write tool still POSTs (writes commit as today)', async () => {
  const fake = await startFakeZoeData();
  try {
    await withWriteEnabledTools(fake.baseUrl, async (tools) => {
      for (const c of WRITE_CASES) {
        // A live turn's signal with NOTHING bound — the ordinary case.
        const signal = new AbortController().signal;
        assert.equal(isReplayTurn(signal), false, 'unbound signal must never read as replay');
        await byName(tools, c.name).run({ input: c.input, signal });
      }
      assert.equal(
        fake.posts.length, WRITE_CASES.length,
        'unmarked turns must dispatch one write each — otherwise the isolation test below proves nothing',
      );
    });
  } finally {
    await fake.close();
  }
});

test('ISOLATED — replay marker bound: ZERO writes dispatched, success text preserved', async () => {
  const fake = await startFakeZoeData();
  try {
    await withWriteEnabledTools(fake.baseUrl, async (tools) => {
      for (const c of WRITE_CASES) {
        const signal = new AbortController().signal;
        bindTurnReplayMode(signal, true);
        const out = String(await byName(tools, c.name).run({ input: c.input, signal }));
        assert.match(out, c.expect, `${c.name} must still read as success under isolation`);
        assert.doesNotMatch(out, /WRITE DISABLED/, `${c.name} must not surface the lab refusal text`);
      }
      assert.deepEqual(fake.posts, [], 'a replay turn must not emit ANY intent-dispatch write');
    });
  } finally {
    await fake.close();
  }
});

test('an identity-less replay turn keeps the fail-closed line and still writes nothing', async () => {
  // Isolation must not INVENT a success the live lane would not have given. With
  // no acting identity, a live turn answers "I'm not sure whose data this would
  // touch…" — a can't-do line the replay scorer reads as CANT_DO. If the marker
  // short-circuited ahead of that check, isolation would have flipped a verdict.
  const fake = await startFakeZoeData();
  const prevUid = process.env.ZOE_BRAIN_USER_ID;
  try {
    await withWriteEnabledTools(fake.baseUrl, async (tools) => {
      delete process.env.ZOE_BRAIN_USER_ID; // no env fallback, no bound identity
      const signal = new AbortController().signal;
      bindTurnReplayMode(signal, true);
      const out = String(await byName(tools, 'add_reminder').run({ input: { title: 'x' }, signal }));
      assert.match(out, /whose data this would touch/, 'must keep the live fail-closed line');
      assert.deepEqual(fake.posts, [], 'and must still not write');
    });
  } finally {
    if (prevUid === undefined) delete process.env.ZOE_BRAIN_USER_ID;
    else process.env.ZOE_BRAIN_USER_ID = prevUid;
    await fake.close();
  }
});

test('reads are unaffected by the replay marker (the gate still scores said-vs-did)', async () => {
  const fake = await startFakeZoeData();
  try {
    await withWriteEnabledTools(fake.baseUrl, async (tools) => {
      const signal = new AbortController().signal;
      bindTurnReplayMode(signal, true);
      await byName(tools, 'show_calendar').run({ input: {}, signal });
      await byName(tools, 'list_reminders').run({ input: {}, signal });
      assert.equal(fake.posts.length, 2, 'read intents must still reach zoe-data under isolation');
    });
  } finally {
    await fake.close();
  }
});

// ── envelope plumbing ───────────────────────────────────────────────────────

test('turns are independent — one replay turn does not isolate a concurrent live turn', async () => {
  const replay = new AbortController().signal;
  const live = new AbortController().signal;
  bindTurnReplayMode(replay, true);
  bindTurnReplayMode(live, false);
  assert.equal(isReplayTurn(replay), true);
  assert.equal(isReplayTurn(live), false);
  assert.equal(isReplayTurn(undefined), false, 'no signal → never isolated');
});

test('wire order: the replay line rides ahead of the identity line and both parse', () => {
  // Exactly how services/zoe-data/zoe_flue_client.py assembles the message.
  const wire = wrapMessageWithReplay(wrapMessageWithIdentity('hi', 'jason'), true);
  assert.equal(wire, ' zoe-replay:1\n zoe-uid:jason\nhi');

  const context = { messages: [{ role: 'user', content: wire }] } as never;
  const signal = new AbortController().signal;
  bindIdentityForRound(context, signal);

  assert.equal(isReplayTurn(signal), true, 'replay marker must bind');
  assert.equal(currentUserId(signal), 'jason', 'identity must STILL bind behind the replay line');

  // The model must never see either control line.
  const cleaned = applyPolicies(context) as unknown as { messages: { content: string }[] };
  assert.equal(cleaned.messages[0].content, 'hi');
});

test('an unmarked wire message binds no replay mode (control for the parser)', () => {
  const wire = wrapMessageWithIdentity('hi', 'jason');
  assert.equal(wire, ' zoe-uid:jason\nhi');
  const context = { messages: [{ role: 'user', content: wire }] } as never;
  const signal = new AbortController().signal;
  bindIdentityForRound(context, signal);
  assert.equal(isReplayTurn(signal), false);
  assert.equal(currentUserId(signal), 'jason');
});
