/**
 * B1.1 speculative turn — the sidecar echoes the turn id on its tool writes.
 *
 * zoe-data holds a speculative voice turn's brain-tool writes for the daemon's
 * verdict, keyed on the ORIGINATING turn (never on the user, which would also hold
 * the same user's writes from another session). That only works if this sidecar
 * (1) binds the seam's outermost " zoe-spec:<id>" line to the turn, (2) keeps it
 * away from the model, and (3) echoes it as `speculative_turn_id` on every
 * intent-dispatch — and sends a byte-identical body on every ordinary turn.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/speculative_turn.test.ts
 */
import assert from 'node:assert/strict';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { test } from 'node:test';

import { applyPolicies, bindIdentityForRound } from '../src/providers/capped-completions.ts';
import { isReplayTurn, wrapMessageWithReplay } from '../src/replay-mode.ts';
import { currentUserId, wrapMessageWithIdentity } from '../src/request-identity.ts';
import {
  bindTurnSpeculativeId,
  currentSpeculativeTurnId,
  wrapMessageWithSpeculativeTurn,
} from '../src/speculative-turn.ts';

type RunnableTool = {
  name: string;
  run: (ctx: { data: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : undefined;
}

async function withFakeZoeData(fn: (tools: RunnableTool[], posts: unknown[]) => Promise<void>) {
  const posts: unknown[] = [];
  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    posts.push(await readJson(req));
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ intent: 'list_add', ok: true, result: '' }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const prev = { url: process.env.ZOE_DATA_URL, uid: process.env.ZOE_BRAIN_USER_ID, w: process.env.ZOE_BRAIN_ALLOW_WRITES };
  process.env.ZOE_DATA_URL = `http://127.0.0.1:${address.port}`;
  process.env.ZOE_BRAIN_USER_ID = 'spec-user';
  process.env.ZOE_BRAIN_ALLOW_WRITES = 'true';
  try {
    const mod = await import(`../src/tools/zoe-tools.ts?spec=${Date.now()}-${Math.random()}`);
    await fn(mod.zoeTools as unknown as RunnableTool[], posts);
  } finally {
    for (const [k, v] of Object.entries({ ZOE_DATA_URL: prev.url, ZOE_BRAIN_USER_ID: prev.uid, ZOE_BRAIN_ALLOW_WRITES: prev.w })) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

function tool(tools: RunnableTool[], name: string): RunnableTool {
  const t = tools.find((c) => c.name === name);
  assert.ok(t, `expected ${name}`);
  return t;
}

test('wire order: spec line outermost, then replay, then identity — all bind, model sees none', () => {
  // Exactly how services/zoe-data/zoe_flue_client.py assembles the message.
  const wire = wrapMessageWithSpeculativeTurn(
    wrapMessageWithReplay(wrapMessageWithIdentity('add milk', 'jason'), true), 'abc123');
  assert.equal(wire, ' zoe-spec:abc123\n zoe-replay:1\n zoe-uid:jason\nadd milk');
  const context = { messages: [{ role: 'user', content: wire }] } as never;
  const signal = new AbortController().signal;
  bindIdentityForRound(context, signal);
  assert.equal(currentSpeculativeTurnId(signal), 'abc123');
  assert.equal(isReplayTurn(signal), true, 'replay must still bind behind the spec line');
  assert.equal(currentUserId(signal), 'jason', 'identity must still bind behind both lines');
  const cleaned = applyPolicies(context) as unknown as { messages: { content: string }[] };
  assert.equal(cleaned.messages[0].content, 'add milk');
});

test('an ordinary turn binds no speculative id (parser control)', () => {
  const context = { messages: [{ role: 'user', content: wrapMessageWithIdentity('hi', 'jason') }] } as never;
  const signal = new AbortController().signal;
  bindIdentityForRound(context, signal);
  assert.equal(currentSpeculativeTurnId(signal), '');
  assert.equal(currentUserId(signal), 'jason');
});

test('ids outside the seam charset are never bound', () => {
  const signal = new AbortController().signal;
  bindTurnSpeculativeId(signal, 'bad id!');
  assert.equal(currentSpeculativeTurnId(signal), '');
});

test('dispatch echoes the turn id for the speculative turn, and ONLY for it', async () => {
  await withFakeZoeData(async (tools, posts) => {
    const spec = new AbortController().signal;
    const other = new AbortController().signal;
    bindTurnSpeculativeId(spec, 'abc123');
    await tool(tools, 'shopping_list_add').run({ data: { item: 'milk' }, signal: spec });
    await tool(tools, 'shopping_list_add').run({ data: { item: 'eggs' }, signal: other });
    assert.equal(posts.length, 2);
    assert.equal((posts[0] as Record<string, unknown>).speculative_turn_id, 'abc123');
    assert.ok(!('speculative_turn_id' in (posts[1] as Record<string, unknown>)),
      'an ordinary turn must send the body unchanged');
  });
});
