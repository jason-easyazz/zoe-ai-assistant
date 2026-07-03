/**
 * Route-level coverage for per-request identity threading (LAB-ONLY, offline).
 *
 * Mounts the exported `route` handler on a real Hono app and drives it with a
 * POST whose body carries a forwarded `user_id`. A stub `next` (installed as the
 * downstream handler) reads currentUserId() — proving the id set in trusted
 * route code reaches the code that runs the agent + tools, exactly as it does in
 * production where flue's handleAgentRequest runs inside this route's next().
 *
 * This does NOT stand up the flue runtime (no llama-server, no agent loop); it
 * verifies the ONE thing this fix owns: the route reads the trusted body user_id
 * and wraps the downstream in runWithUserId(...). Auth fail-closed is covered too.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/route_identity.test.ts
 */
process.env.ZOE_BRAIN_OPEN = '1'; // open the route for the offline test

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Hono } from 'hono';

const { route } = await import('../src/agents/zoe.ts');
const { currentUserId } = await import('../src/request-identity.ts');

/**
 * Build a Hono app that mounts `route` and, as the downstream handler, records
 * the currentUserId() it observes, then returns 202 (mirroring flue's admission
 * status so the middleware chain looks realistic).
 */
function appRecording(seen: { userId: string }): Hono {
  const app = new Hono();
  app.post('/agents/zoe/:id', route, (c) => {
    seen.userId = currentUserId();
    return c.json({ ok: true }, 202);
  });
  return app;
}

async function post(app: Hono, body: unknown, headers?: Record<string, string>): Promise<Response> {
  return app.request('/agents/zoe/sess-1', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

test('route threads the forwarded body user_id to the downstream handler', async () => {
  const seen = { userId: '<unset>' };
  const res = await post(appRecording(seen), { message: 'hi', user_id: 'alice' });
  assert.equal(res.status, 202);
  assert.equal(seen.userId, 'alice');
});

test('a DIFFERENT forwarded user_id threads through as that user', async () => {
  const seen = { userId: '<unset>' };
  const res = await post(appRecording(seen), { message: 'hi', user_id: 'family-admin' });
  assert.equal(res.status, 202);
  assert.equal(seen.userId, 'family-admin');
});

test('no user_id in the body → downstream sees empty (env fallback applies in tools)', async () => {
  const seen = { userId: '<unset>' };
  const res = await post(appRecording(seen), { message: 'hi' });
  assert.equal(res.status, 202);
  assert.equal(seen.userId, '');
});

test('a non-string user_id is ignored (downstream sees empty)', async () => {
  const seen = { userId: '<unset>' };
  const res = await post(appRecording(seen), { message: 'hi', user_id: 12345 });
  assert.equal(res.status, 202);
  assert.equal(seen.userId, '');
});

test('reading the body does not starve the downstream handler of the body', async () => {
  // COVERAGE NOTE: this exercises HONO'S body caching — a second c.req.json() in
  // the same request returns the cached parse — NOT Flue's actual raw-clone path.
  // In the real runtime, flue's agentRouteHandler clones c.req.raw BEFORE this
  // route middleware runs and parses the turn payload from that separate clone
  // (never from Hono's JSON cache), which is what truly makes our peek safe. That
  // internal behaviour can't be reproduced without standing up the flue runtime,
  // so if a future flue version stopped pre-cloning (e.g. read c.req.json()
  // itself), this test would still pass while production broke — re-verify the
  // clone assumption on any @flue/runtime bump (see the peek rationale in
  // src/agents/zoe.ts forwardedUserId()).
  //
  // The downstream (flue) parses the turn payload from its own clone of the raw
  // request; here we assert our peek left a re-readable body for a second reader.
  const app = new Hono();
  let downstreamBody: unknown = null;
  app.post('/agents/zoe/:id', route, async (c) => {
    downstreamBody = await c.req.json(); // Hono caches — same object our route read
    return c.json({ ok: true }, 202);
  });
  const res = await post(app, { message: 'hello', user_id: 'alice' });
  assert.equal(res.status, 202);
  assert.deepEqual(downstreamBody, { message: 'hello', user_id: 'alice' });
});

test('fail-closed: with a token set and no bearer, the route 401s and never runs downstream', async () => {
  delete process.env.ZOE_BRAIN_OPEN;
  process.env.ZOE_BRAIN_TOKEN = 'secret';
  try {
    const seen = { userId: '<unset>' };
    const res = await post(appRecording(seen), { message: 'hi', user_id: 'alice' });
    assert.equal(res.status, 401);
    assert.equal(seen.userId, '<unset>', 'downstream must not run on an unauthorized request');
  } finally {
    delete process.env.ZOE_BRAIN_TOKEN;
    process.env.ZOE_BRAIN_OPEN = '1';
  }
});

test('fail-closed: with a token set, a matching bearer authorizes AND threads identity', async () => {
  delete process.env.ZOE_BRAIN_OPEN;
  process.env.ZOE_BRAIN_TOKEN = 'secret';
  try {
    const seen = { userId: '<unset>' };
    const res = await post(appRecording(seen), { message: 'hi', user_id: 'alice' }, {
      authorization: 'Bearer secret',
    });
    assert.equal(res.status, 202);
    assert.equal(seen.userId, 'alice');
  } finally {
    delete process.env.ZOE_BRAIN_TOKEN;
    process.env.ZOE_BRAIN_OPEN = '1';
  }
});
