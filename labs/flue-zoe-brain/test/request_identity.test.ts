/**
 * Unit coverage for per-request acting identity (LAB-ONLY, offline, no network).
 *
 * Proves:
 *   - runWithUserId(id, fn) makes currentUserId() return `id` inside fn,
 *     including across awaits / nested async, and '' outside any context;
 *   - actingUserId() (via a tool's observable behaviour) prefers the ALS user
 *     when set, falls back to the env when the ALS is empty, and still fails
 *     closed on guest identities and the empty id.
 *
 * The ALS-vs-env preference is proved END-TO-END through recall_memory: the
 * tool builds its request URL with `user_id=<actingUserId()>`, so we stub fetch,
 * capture the URL, and read back which identity the tool acted as.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/request_identity.test.ts
 *
 * Env is set BEFORE importing the modules (zoe-tools reads ZOE_BRAIN_USER_ID at
 * call time, but ALLOW_WRITES at module load — keep the dynamic-import pattern
 * the other tests use so env is always in place first).
 */
process.env.ZOE_BRAIN_USER_ID = 'env-user';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const { runWithUserId, currentUserId } = await import('../src/request-identity.ts');
const { zoeTools } = await import('../src/tools/zoe-tools.ts');

type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};
const recallMemory = zoeTools.find((t) => t.name === 'recall_memory')! as unknown as RunnableTool;

/**
 * Stub global fetch to capture the request URL and return a fixed memory packet,
 * then restore. Returns the last URL string the tool fetched.
 */
async function withCapturedFetch(
  packet: string,
  fn: (getUrl: () => string) => Promise<void>,
): Promise<void> {
  const real = globalThis.fetch;
  let lastUrl = '';
  globalThis.fetch = (async (input: unknown) => {
    lastUrl = String(input);
    return { ok: true, status: 200, json: async () => ({ packet }) };
  }) as unknown as typeof fetch;
  try {
    await fn(() => lastUrl);
  } finally {
    globalThis.fetch = real;
  }
}

// ─── runWithUserId / currentUserId ───────────────────────────────────────────

test('currentUserId() is empty outside any runWithUserId context', () => {
  assert.equal(currentUserId(), '');
});

test('runWithUserId binds the id synchronously inside fn, empty again after', () => {
  const inside = runWithUserId('alice', () => currentUserId());
  assert.equal(inside, 'alice');
  assert.equal(currentUserId(), '');
});

test('runWithUserId propagates across awaits and nested async', async () => {
  await runWithUserId('alice', async () => {
    assert.equal(currentUserId(), 'alice');
    await Promise.resolve();
    assert.equal(currentUserId(), 'alice');
    // A nested async helper still sees the same bound id.
    const nested = async () => {
      await new Promise((r) => setTimeout(r, 1));
      return currentUserId();
    };
    assert.equal(await nested(), 'alice');
  });
  assert.equal(currentUserId(), '');
});

test('runWithUserId trims the id; nested runWithUserId overrides then restores', () => {
  runWithUserId('  bob  ', () => {
    assert.equal(currentUserId(), 'bob');
    runWithUserId('carol', () => assert.equal(currentUserId(), 'carol'));
    // Inner context popped — outer id restored.
    assert.equal(currentUserId(), 'bob');
  });
});

// ─── actingUserId() preference, observed through recall_memory ───────────────

test('recall_memory acts as the ALS user when a request identity is set', async () => {
  await withCapturedFetch('alice-facts', async (getUrl) => {
    const out = await runWithUserId('alice', () => recallMemory.run({ input: {} }));
    assert.equal(out, 'alice-facts');
    assert.match(getUrl(), /[?&]user_id=alice(&|$)/);
  });
});

test('recall_memory falls back to the env user when no ALS identity is set', async () => {
  await withCapturedFetch('env-facts', async (getUrl) => {
    const out = await recallMemory.run({ input: {} });
    assert.equal(out, 'env-facts');
    assert.match(getUrl(), /[?&]user_id=env-user(&|$)/);
  });
});

test('an empty ALS identity falls through to the env user (does not blank identity)', async () => {
  await withCapturedFetch('env-facts', async (getUrl) => {
    const out = await runWithUserId('', () => recallMemory.run({ input: {} }));
    assert.equal(out, 'env-facts');
    assert.match(getUrl(), /[?&]user_id=env-user(&|$)/);
  });
});

test('recall_memory fails closed on a guest ALS identity (no fetch, refuses)', async () => {
  let fetched = false;
  const real = globalThis.fetch;
  globalThis.fetch = (async () => {
    fetched = true;
    return { ok: true, status: 200, json: async () => ({ packet: 'leak' }) };
  }) as unknown as typeof fetch;
  try {
    const out = (await runWithUserId('guest', () => recallMemory.run({ input: {} }))) as string;
    assert.equal(fetched, false, 'must not call zoe-data with a guest identity');
    assert.match(out, /can't do that safely/i);
  } finally {
    globalThis.fetch = real;
  }
});
