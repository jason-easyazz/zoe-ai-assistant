/**
 * The channel's identity gates, as pure decisions.
 *
 * Ported verbatim in intent from labs/flue-zoe-telegram/src/handler.test.ts, and
 * EXTENDED to the two gates the beta could not test: `/start` (deliberately
 * ungated, so the test pins that it links only on a valid token) and `/new`
 * (identity-gated, and the negative control below is the one that matters — an
 * unlinked stranger must not be able to rotate a session epoch).
 *
 * No grammY, no bot token, no network.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  handleIncoming,
  handleNew,
  handleStart,
  startReply,
  unlinkedMessage,
} from '../src/handler.ts';

// ─── handleIncoming: resolve → forward, and unlinked → refuse ────────────────

test('linked sender: forwards the turn AS the resolved user and relays the reply', async () => {
  const asks: Array<{ text: string; session: string; userId: string }> = [];
  const replies: string[] = [];

  await handleIncoming(99999, 42, 'what did I have for lunch?', {
    resolve: async (id) => (id === 99999 ? 'jason' : null),
    ask: async (text, session, userId) => {
      asks.push({ text, session, userId });
      return 'You mentioned a burrito.';
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
  assert.match(replies[0]!, /77777/); // includes their numeric id to copy
  assert.match(replies[0]!, /not linked/i);
});

// ─── startReply: /start deep-link outcomes ───────────────────────────────────

test('startReply: successful link is a friendly confirmation', () => {
  assert.match(startReply('jason', true), /Linked/i);
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

// ─── handleStart: linking only ever happens through a valid signed token ─────

test('handleStart: a valid token links and confirms, forwarding the VERIFIED sender id', async () => {
  const consumed: Array<{ token: string; telegramId: number; username?: string }> = [];
  const replies: string[] = [];

  await handleStart(6308082458, ' tok123 ', 'jbert', {
    consume: async (token, telegramId, username) => {
      consumed.push({ token, telegramId, username });
      return 'jason';
    },
    reply: async (t) => {
      replies.push(t);
    },
  });

  // The token is trimmed; the id is the Telegram-verified one, never user input.
  assert.deepEqual(consumed, [{ token: 'tok123', telegramId: 6308082458, username: 'jbert' }]);
  assert.match(replies[0]!, /Linked/i);
});

test('handleStart NEGATIVE CONTROL: a bare /start never calls the token redeemer at all', async () => {
  let consumeCalled = false;
  const replies: string[] = [];

  await handleStart(6308082458, '   ', undefined, {
    consume: async () => {
      consumeCalled = true;
      return 'jason';
    },
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.equal(consumeCalled, false, 'an empty payload must not reach the redeemer');
  assert.doesNotMatch(replies[0]!, /Linked/i);
});

test('handleStart: a rejected token replies "expired", links nobody', async () => {
  const replies: string[] = [];
  await handleStart(1, 'bad', undefined, {
    consume: async () => null,
    reply: async (t) => {
      replies.push(t);
    },
  });
  assert.match(replies[0]!, /expired|invalid/i);
});

test('handleStart: a redeemer failure is answered generically, not with a stack trace', async () => {
  const replies: string[] = [];
  await handleStart(1, 'tok', undefined, {
    consume: async () => {
      throw new Error('zoe-data exploded: secret-looking detail');
    },
    reply: async (t) => {
      replies.push(t);
    },
  });
  assert.match(replies[0]!, /went wrong/i);
  assert.doesNotMatch(replies[0]!, /secret-looking/);
});

// ─── handleNew: the /new session-epoch gate ─────────────────────────────────

test('handleNew: a linked sender rotates the epoch for THEIR chat', async () => {
  const bumped: number[] = [];
  const replies: string[] = [];

  await handleNew(99999, 42, {
    resolve: async () => 'jason',
    bump: (chatId) => {
      bumped.push(chatId);
    },
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.deepEqual(bumped, [42]);
  assert.match(replies[0]!, /fresh conversation/i);
});

test('handleNew NEGATIVE CONTROL: an unlinked stranger cannot rotate a session epoch', async () => {
  let bumped = false;
  const replies: string[] = [];

  await handleNew(77777, 42, {
    resolve: async () => null,
    bump: () => {
      bumped = true;
    },
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.equal(bumped, false, 'an unlinked sender must NOT reach bumpSession');
  assert.match(replies[0]!, /link your Zoe account/i);
});

test('handleNew: a resolver failure does not rotate the epoch either (fail closed)', async () => {
  let bumped = false;
  const replies: string[] = [];

  await handleNew(77777, 42, {
    resolve: async () => {
      throw new Error('resolver down');
    },
    bump: () => {
      bumped = true;
    },
    reply: async (t) => {
      replies.push(t);
    },
  });

  assert.equal(bumped, false, 'a resolver error must fail closed, not open');
  assert.match(replies[0]!, /went wrong/i);
});
