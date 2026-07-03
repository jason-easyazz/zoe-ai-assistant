/**
 * Route + turn-chain coverage for per-request identity (LAB-ONLY, offline).
 *
 * Two things are proved here without standing up the flue runtime / llama-server:
 *
 *  1. AUTH FAIL-CLOSED — the exported `route` (mounted on a real Hono app) rejects
 *     an unauthorized caller and never runs the downstream handler; a matching
 *     bearer (or ZOE_BRAIN_OPEN=1) lets it through. The route no longer threads
 *     identity itself — it only gates access.
 *
 *  2. THE ROUTE→PROVIDER→TOOL CHAIN (the regression that #998 missed) — the acting
 *     identity now travels on the turn MESSAGE envelope and is bound INSIDE the
 *     turn by the capped-completions provider, keyed by the turn's AbortSignal, so
 *     the tool reads it back by that same signal. We simulate exactly the sequence
 *     flue runs for one turn:
 *       envelope message  →  provider.bindIdentityForRound(context, signal)
 *                         →  provider.applyPolicies(context)  (strips the envelope)
 *                         →  tool.run({ input, signal })      (acts as that user)
 *     and assert (a) the tool acts as the enveloped user, (b) a DIFFERENT signal
 *     stays isolated (concurrent turns don't cross), and (c) the model never sees
 *     the envelope. This is the full HTTP-equivalent chain the tool-only #998 test
 *     never exercised.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/route_identity.test.ts
 */
process.env.ZOE_BRAIN_OPEN = '1'; // open the route for the offline test

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Hono } from 'hono';

const { route } = await import('../src/agents/zoe.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');
const { bindIdentityForRound, applyPolicies } = await import('../src/providers/capped-completions.ts');
const { zoeTools } = await import('../src/tools/zoe-tools.ts');

type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};
const recallMemory = zoeTools.find((t) => t.name === 'recall_memory')! as unknown as RunnableTool;

// ─── auth fail-closed ────────────────────────────────────────────────────────

function appRan(seen: { ran: boolean }): Hono {
  const app = new Hono();
  app.post('/agents/zoe/:id', route, (c) => {
    seen.ran = true;
    return c.json({ ok: true }, 202);
  });
  return app;
}

async function post(app: Hono, headers?: Record<string, string>): Promise<Response> {
  return app.request('/agents/zoe/sess-1', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify({ message: 'hi' }),
  });
}

test('open mode lets the request through to the downstream handler', async () => {
  const seen = { ran: false };
  const res = await post(appRan(seen));
  assert.equal(res.status, 202);
  assert.equal(seen.ran, true);
});

test('fail-closed: token set, no bearer → 401 and downstream never runs', async () => {
  delete process.env.ZOE_BRAIN_OPEN;
  process.env.ZOE_BRAIN_TOKEN = 'secret';
  try {
    const seen = { ran: false };
    const res = await post(appRan(seen));
    assert.equal(res.status, 401);
    assert.equal(seen.ran, false, 'downstream must not run on an unauthorized request');
  } finally {
    delete process.env.ZOE_BRAIN_TOKEN;
    process.env.ZOE_BRAIN_OPEN = '1';
  }
});

test('fail-closed: token set, matching bearer → authorized (downstream runs)', async () => {
  delete process.env.ZOE_BRAIN_OPEN;
  process.env.ZOE_BRAIN_TOKEN = 'secret';
  try {
    const seen = { ran: false };
    const res = await post(appRan(seen), { authorization: 'Bearer secret' });
    assert.equal(res.status, 202);
    assert.equal(seen.ran, true);
  } finally {
    delete process.env.ZOE_BRAIN_TOKEN;
    process.env.ZOE_BRAIN_OPEN = '1';
  }
});

// ─── the route→provider→tool chain (regression for #998) ─────────────────────

/** Stub fetch, capture the URL, return a per-user packet keyed off user_id. */
async function withCapturedFetch(
  fn: (getUrl: () => string) => Promise<void>,
): Promise<void> {
  const real = globalThis.fetch;
  let lastUrl = '';
  globalThis.fetch = (async (input: unknown) => {
    lastUrl = String(input);
    const uid = new URL(String(input)).searchParams.get('user_id') ?? '';
    return { ok: true, status: 200, json: async () => ({ packet: `${uid}-facts` }) };
  }) as unknown as typeof fetch;
  try {
    await fn(() => lastUrl);
  } finally {
    globalThis.fetch = real;
  }
}

/** One turn's worth of context, as the provider receives it from flue. */
function contextFor(userId: string) {
  return {
    messages: [
      { role: 'user' as const, content: wrapMessageWithIdentity('what do you know about me?', userId) },
    ],
  };
}

test('chain: provider binds the enveloped user by signal; the tool acts as THAT user', async () => {
  await withCapturedFetch(async (getUrl) => {
    const signal = new AbortController().signal;
    // Provider round: bind identity for this turn's signal.
    bindIdentityForRound(contextFor('family-admin'), signal);
    // Tool runs later this same turn with the SAME signal.
    const out = await recallMemory.run({ input: {}, signal });
    assert.equal(out, 'family-admin-facts');
    assert.match(getUrl(), /[?&]user_id=family-admin(&|$)/);
  });
});

test('chain: two concurrent turns stay isolated (no cross-user leak)', async () => {
  await withCapturedFetch(async () => {
    const sigA = new AbortController().signal;
    const sigB = new AbortController().signal;
    // Both turns' providers bind before either tool runs (interleaved, as under load).
    bindIdentityForRound(contextFor('family-admin'), sigA);
    bindIdentityForRound(contextFor('zzz-nobody-verify'), sigB);
    const outA = await recallMemory.run({ input: {}, signal: sigA });
    const outB = await recallMemory.run({ input: {}, signal: sigB });
    assert.equal(outA, 'family-admin-facts', 'turn A must act as family-admin');
    assert.equal(outB, 'zzz-nobody-verify-facts', 'turn B must act as zzz-nobody-verify');
  });
});

test('chain: applyPolicies strips the identity envelope so the model never sees it', () => {
  const clean = applyPolicies(contextFor('family-admin'));
  const text = clean.messages.find((m) => m.role === 'user')?.content;
  assert.equal(text, 'what do you know about me?');
  assert.ok(!String(text).includes('zoe-uid:'), 'envelope must not reach the model');
});
