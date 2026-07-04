/**
 * Unit coverage for per-request acting identity (LAB-ONLY, offline, no network).
 *
 * The mechanism (see src/request-identity.ts): the acting user for a turn is keyed
 * by that turn's AbortSignal — the provider binds it (bindTurnUserId), the tool
 * reads it (currentUserId(signal)). The trusted id reaches the provider on the
 * turn message via an envelope (wrapMessageWithIdentity), which the provider
 * extracts (forwardedIdentityFromMessages) and strips (stripIdentityEnvelope)
 * before the model sees it.
 *
 * Proves, in isolation:
 *   - bindTurnUserId(signal, id) makes currentUserId(signal) return the trimmed id;
 *     a different/absent signal returns '' — so concurrent turns never cross;
 *   - the envelope round-trips: wrap → forwardedIdentityFromMessages recovers the
 *     id, stripIdentityEnvelope removes the marker line (string and array content);
 *   - actingUserId() (via recall_memory's observable behaviour) prefers the
 *     signal-bound user, falls back to the env when nothing is bound for the
 *     signal, and still fails closed on guest identities and the empty id.
 *
 * The signal-vs-env preference is proved END-TO-END through recall_memory: the
 * tool builds its request URL with `user_id=<actingUserId(signal)>`, so we stub
 * fetch, capture the URL, and read back which identity the tool acted as.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/request_identity.test.ts
 *
 * ZOE_BRAIN_USER_ID is set at module scope (before the dynamic imports) because
 * zoe-tools reads ALLOW_WRITES at module load; the original is restored in `after`
 * so sibling test files sharing this worker aren't affected.
 */
const ORIGINAL_ZOE_BRAIN_USER_ID = process.env.ZOE_BRAIN_USER_ID;
process.env.ZOE_BRAIN_USER_ID = 'env-user';

import assert from 'node:assert/strict';
import { after, test } from 'node:test';

after(() => {
  if (ORIGINAL_ZOE_BRAIN_USER_ID === undefined) delete process.env.ZOE_BRAIN_USER_ID;
  else process.env.ZOE_BRAIN_USER_ID = ORIGINAL_ZOE_BRAIN_USER_ID;
});

const {
  bindTurnUserId,
  currentUserId,
  wrapMessageWithIdentity,
  forwardedIdentityFromMessages,
  stripIdentityEnvelope,
} = await import('../src/request-identity.ts');
const { zoeTools } = await import('../src/tools/zoe-tools.ts');

type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};
const recallMemory = zoeTools.find((t) => t.name === 'recall_memory')! as unknown as RunnableTool;

/** A fresh, distinct AbortSignal for a "turn". */
function turnSignal(): AbortSignal {
  return new AbortController().signal;
}

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

// ─── bindTurnUserId / currentUserId (signal-keyed) ───────────────────────────

test('currentUserId(undefined) is empty — no signal, no identity', () => {
  assert.equal(currentUserId(undefined), '');
});

test('currentUserId(signal) is empty before any bind', () => {
  assert.equal(currentUserId(turnSignal()), '');
});

test('bindTurnUserId binds (and trims) the id for that signal only', () => {
  const a = turnSignal();
  const b = turnSignal();
  bindTurnUserId(a, '  alice  ');
  assert.equal(currentUserId(a), 'alice');
  // A DIFFERENT signal is unaffected — concurrent turns never cross.
  assert.equal(currentUserId(b), '');
});

test('two concurrent turns keep independent identities (no race)', () => {
  const a = turnSignal();
  const b = turnSignal();
  bindTurnUserId(a, 'alice');
  bindTurnUserId(b, 'bob');
  // Rebinding one does not disturb the other, regardless of order.
  assert.equal(currentUserId(a), 'alice');
  assert.equal(currentUserId(b), 'bob');
  bindTurnUserId(a, 'alice2');
  assert.equal(currentUserId(a), 'alice2');
  assert.equal(currentUserId(b), 'bob');
});

test('bindTurnUserId with no signal is a no-op (does not throw)', () => {
  assert.doesNotThrow(() => bindTurnUserId(undefined, 'nobody'));
});

// ─── identity envelope round-trip ────────────────────────────────────────────

test('wrap → forwardedIdentityFromMessages recovers the id', () => {
  const wrapped = wrapMessageWithIdentity('what do you know about me?', 'alice');
  assert.equal(
    forwardedIdentityFromMessages([{ role: 'user', content: wrapped }]),
    'alice',
  );
});

test('an empty id leaves the message unchanged and yields no forwarded id', () => {
  const msg = 'hello';
  assert.equal(wrapMessageWithIdentity(msg, ''), msg);
  assert.equal(forwardedIdentityFromMessages([{ role: 'user', content: msg }]), '');
});

test('an embedded newline in the id cannot break the single-line envelope', () => {
  // A \n/\r inside the id would otherwise terminate the envelope line early and
  // leak the remainder into the model prompt. The invariant that MUST hold for
  // every input: the recovered id contains no CR/LF, so the envelope is one line.
  for (const raw of ['ali\nce', 'ali\rce', 'ali\r\nce', '\nalice\n', 'alice\ninjected line']) {
    const wrapped = wrapMessageWithIdentity('what do you know about me?', raw);
    const recovered = forwardedIdentityFromMessages([{ role: 'user', content: wrapped }]);
    assert.equal(/[\r\n]/.test(recovered), false, `recovered id has no newline for ${JSON.stringify(raw)}`);
  }
  // The newline-in-"alice" variants all sanitize to exactly 'alice'.
  for (const raw of ['ali\nce', 'ali\rce', 'ali\r\nce', '\nalice\n']) {
    const wrapped = wrapMessageWithIdentity('hi', raw);
    assert.equal(forwardedIdentityFromMessages([{ role: 'user', content: wrapped }]), 'alice',
      `id sanitized to 'alice' for ${JSON.stringify(raw)}`);
  }
});

test('forwardedIdentityFromMessages reads the LAST user message only', () => {
  const messages = [
    { role: 'user', content: wrapMessageWithIdentity('first', 'alice') },
    { role: 'assistant', content: 'ok' },
    { role: 'user', content: wrapMessageWithIdentity('second', 'bob') },
  ];
  assert.equal(forwardedIdentityFromMessages(messages), 'bob');
});

test('stripIdentityEnvelope removes the marker line (string content)', () => {
  const wrapped = wrapMessageWithIdentity('the real message', 'alice');
  const [msg] = stripIdentityEnvelope([{ role: 'user', content: wrapped }]);
  assert.equal(msg.content, 'the real message');
});

test('stripIdentityEnvelope removes the marker from array text content', () => {
  const wrapped = wrapMessageWithIdentity('the real message', 'alice');
  const [msg] = stripIdentityEnvelope([
    { role: 'user', content: [{ type: 'text', text: wrapped }] },
  ]);
  assert.deepEqual(msg.content, [{ type: 'text', text: 'the real message' }]);
});

test('stripIdentityEnvelope leaves envelope-free messages untouched (same ref)', () => {
  const messages = [{ role: 'user', content: 'plain' }];
  assert.equal(stripIdentityEnvelope(messages), messages);
});

// ─── actingUserId() preference, observed through recall_memory ───────────────

test('recall_memory acts as the signal-bound user when one is set', async () => {
  await withCapturedFetch('alice-facts', async (getUrl) => {
    const signal = turnSignal();
    bindTurnUserId(signal, 'alice');
    const out = await recallMemory.run({ input: {}, signal });
    assert.equal(out, 'alice-facts');
    assert.match(getUrl(), /[?&]user_id=alice(&|$)/);
  });
});

test('recall_memory falls back to the env user when no identity is bound', async () => {
  await withCapturedFetch('env-facts', async (getUrl) => {
    const out = await recallMemory.run({ input: {}, signal: turnSignal() });
    assert.equal(out, 'env-facts');
    assert.match(getUrl(), /[?&]user_id=env-user(&|$)/);
  });
});

test('an empty bound identity falls through to the env user (does not blank identity)', async () => {
  await withCapturedFetch('env-facts', async (getUrl) => {
    const signal = turnSignal();
    bindTurnUserId(signal, '');
    const out = await recallMemory.run({ input: {}, signal });
    assert.equal(out, 'env-facts');
    assert.match(getUrl(), /[?&]user_id=env-user(&|$)/);
  });
});

test('recall_memory fails closed on a guest bound identity (no fetch, refuses)', async () => {
  let fetched = false;
  const real = globalThis.fetch;
  globalThis.fetch = (async () => {
    fetched = true;
    return { ok: true, status: 200, json: async () => ({ packet: 'leak' }) };
  }) as unknown as typeof fetch;
  try {
    const signal = turnSignal();
    bindTurnUserId(signal, 'guest');
    const out = (await recallMemory.run({ input: {}, signal })) as string;
    assert.equal(fetched, false, 'must not call zoe-data with a guest identity');
    assert.match(out, /can't do that safely/i);
  } finally {
    globalThis.fetch = real;
  }
});
