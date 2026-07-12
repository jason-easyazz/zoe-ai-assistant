/**
 * Unit tests for the resolve→forward / refuse-unlinked decision (handler.ts) and
 * the brain.ts bridge (resolver + trusted forwarded-identity headers).
 *
 * Run: `node --test --experimental-strip-types src/*.test.ts`
 * (see package.json "test"). No bot token / no network — global fetch is mocked.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { handleIncoming, startReply, unlinkedMessage } from './handler.ts';

// ─── handler: resolve → forward, and unlinked → refuse ───────────────────────

test('linked sender: forwards the turn AS the resolved user and relays the reply', async () => {
  const asks: Array<{ text: string; session: string; userId: string }> = [];
  const replies: string[] = [];

  await handleIncoming(99999, 42, 'what did I have for lunch?', {
    resolve: async (id) => (id === 99999 ? 'jason' : null),
    ask: async (text, session, userId) => {
      asks.push({ text, session, userId });
      return "You mentioned a burrito.";
    },
    session: (chatId) => `telegram-${chatId}`,
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.deepEqual(asks, [
    { text: 'what did I have for lunch?', session: 'telegram-42', userId: 'jason' },
  ]);
  assert.deepEqual(replies, ['You mentioned a burrito.']);
});

test('unlinked sender: refuses with the link instructions incl. their numeric id, never asks the brain', async () => {
  let asked = false;
  const replies: string[] = [];

  await handleIncoming(77777, 42, 'hi', {
    resolve: async () => null, // not linked
    ask: async () => {
      asked = true;
      return 'should not be called';
    },
    session: (chatId) => `telegram-${chatId}`,
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.equal(asked, false, 'the brain must NOT be asked for an unlinked sender');
  assert.equal(replies.length, 1);
  assert.equal(replies[0], unlinkedMessage(77777));
  assert.match(replies[0], /77777/); // includes their numeric id to copy
  assert.match(replies[0], /not linked/i);
});

// ─── startReply: /start deep-link outcomes ───────────────────────────────────

test('startReply: successful link is a friendly confirmation', () => {
  const msg = startReply('jason', true);
  assert.match(msg, /Linked/i);
});

test('startReply: invalid/expired token tells them to regenerate', () => {
  const msg = startReply(null, true);
  assert.match(msg, /expired|invalid/i);
  assert.match(msg, /Settings/i);
});

test('startReply: bare /start (no token) is a welcome with instructions', () => {
  const msg = startReply(null, false);
  assert.match(msg, /Settings/i);
  assert.doesNotMatch(msg, /expired/i);
});

// ─── brain.ts: resolver + trusted forwarded-identity headers ─────────────────

async function withMockedFetch(
  impl: (url: string, init?: RequestInit) => Promise<Response>,
  fn: () => Promise<void>,
): Promise<void> {
  const original = globalThis.fetch;
  globalThis.fetch = impl as typeof fetch;
  try {
    await fn();
  } finally {
    globalThis.fetch = original;
  }
}

test('resolveTelegramUser: hits the internal resolver and returns the user_id', async () => {
  process.env.ZOE_DATA_URL = 'http://127.0.0.1:8000';
  process.env.ZOE_INTERNAL_TOKEN = 'seekrit';
  const { resolveTelegramUser } = await import('./brain.ts');

  let seenUrl = '';
  let seenToken: string | null = null;
  await withMockedFetch(
    async (url, init) => {
      seenUrl = url;
      seenToken = new Headers(init?.headers).get('X-Internal-Token');
      return new Response(JSON.stringify({ user_id: 'jason' }), { status: 200 });
    },
    async () => {
      const uid = await resolveTelegramUser(99999);
      assert.equal(uid, 'jason');
    },
  );
  assert.equal(seenUrl, 'http://127.0.0.1:8000/api/system/resolve-telegram/99999');
  assert.equal(seenToken, 'seekrit'); // internal token forwarded
});

test('resolveTelegramUser: unlinked id resolves to null', async () => {
  const { resolveTelegramUser } = await import('./brain.ts');
  await withMockedFetch(
    async () => new Response(JSON.stringify({ user_id: null }), { status: 200 }),
    async () => {
      assert.equal(await resolveTelegramUser(12345), null);
    },
  );
});

test('consumeLinkToken: posts token + verified sender id and returns the linked user', async () => {
  process.env.ZOE_INTERNAL_TOKEN = 'seekrit';
  const { consumeLinkToken } = await import('./brain.ts');

  let seenUrl = '';
  let seenBody: any = null;
  await withMockedFetch(
    async (url, init) => {
      seenUrl = url;
      seenBody = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ ok: true, user_id: 'jason' }), { status: 200 });
    },
    async () => {
      const uid = await consumeLinkToken('tok123', 6308082458, 'jbert');
      assert.equal(uid, 'jason');
    },
  );
  assert.match(seenUrl, /\/api\/system\/telegram\/consume-link-token$/);
  assert.equal(seenBody.token, 'tok123');
  assert.equal(seenBody.telegram_id, '6308082458'); // stringified verified id
  assert.equal(seenBody.telegram_username, 'jbert');
});

test('consumeLinkToken: invalid/expired token (HTTP 400) resolves to null', async () => {
  const { consumeLinkToken } = await import('./brain.ts');
  await withMockedFetch(
    async () => new Response(JSON.stringify({ detail: 'invalid or expired link token' }), { status: 400 }),
    async () => {
      assert.equal(await consumeLinkToken('bad', 123), null);
    },
  );
});

test('askZoeAs: forwards the acting user via X-Zoe-User-Id on the trusted path', async () => {
  process.env.ZOE_INTERNAL_TOKEN = 'seekrit';
  const { askZoeAs } = await import('./brain.ts');

  let seenUserId: string | null = null;
  let seenToken: string | null = null;
  let seenBody: any = null;
  await withMockedFetch(
    async (url, init) => {
      assert.match(url, /\/api\/chat\/\?stream=false$/);
      const h = new Headers(init?.headers);
      seenUserId = h.get('X-Zoe-User-Id');
      seenToken = h.get('X-Internal-Token');
      seenBody = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ response: 'hello jason' }), { status: 200 });
    },
    async () => {
      const reply = await askZoeAs('hi', 'telegram-42', 'jason');
      assert.equal(reply, 'hello jason');
    },
  );
  assert.equal(seenUserId, 'jason'); // trusted forwarded identity
  assert.equal(seenToken, 'seekrit'); // internal token proves trust off-loopback
  assert.equal(seenBody.channel, 'telegram');
  assert.equal(seenBody.session_id, 'telegram-42');
});

// ─── session epochs (/new) ────────────────────────────────────────────────────

test('bumpSession rotates sessionFor and persists across module reload', async (t) => {
  const { mkdtempSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  process.env.SESSION_EPOCHS_PATH = join(mkdtempSync(join(tmpdir(), 'tg-epochs-')), 'epochs.json');

  // dynamic import AFTER env is set so brain.ts reads the temp path
  const { sessionFor, bumpSession } = await import('./brain.ts');

  assert.equal(sessionFor(42), 'telegram-42'); // legacy id until first /new
  bumpSession(42);
  assert.equal(sessionFor(42), 'telegram-42-e1');
  bumpSession(42);
  assert.equal(sessionFor(42), 'telegram-42-e2');
  // other chats unaffected
  assert.equal(sessionFor(7), 'telegram-7');
});
