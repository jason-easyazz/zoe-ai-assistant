/**
 * The four zoe-data wire contracts, and the `/new` epoch store.
 *
 * These are the contracts that MUST NOT MOVE across the runtime upgrade — they
 * are what zoe-data implements (services/zoe-data/routers/system.py) and what
 * `scripts/maintenance/zoe_crash_loop_watch.py` and the settings UI depend on
 * indirectly. The beta suite mocked `globalThis.fetch`; this one drives a real
 * loopback HTTP server so the assertions are on the actual wire — method, path,
 * headers, JSON body — rather than on what the client meant to send.
 *
 * ZOE_DATA_URL is set to the mock BEFORE the dynamic import, because src/brain.ts
 * snapshots it at module load.
 */
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';

import { startMockZoeData, type MockZoeData } from './helpers/mock-zoe-data.ts';

let zoeData: MockZoeData;
let brain: typeof import('../src/brain.ts');
const savedEnv = new Map<string, string | undefined>();

function setEnv(key: string, value: string | undefined) {
  if (!savedEnv.has(key)) savedEnv.set(key, process.env[key]);
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
}

before(async () => {
  zoeData = await startMockZoeData();
  setEnv('ZOE_DATA_URL', zoeData.url);
  setEnv('ZOE_BRAIN_URL', undefined);
  setEnv('ZOE_INTERNAL_TOKEN', 'seekrit');
  setEnv('SESSION_EPOCHS_PATH', join(mkdtempSync(join(tmpdir(), 'tg2x-brain-')), 'epochs.json'));
  brain = await import('../src/brain.ts');
});

after(async () => {
  await zoeData.close();
  for (const [key, value] of savedEnv) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

// ─── resolver ────────────────────────────────────────────────────────────────

test('resolveTelegramUser: GETs the internal resolver with the internal token', async () => {
  zoeData.links.set('99999', 'jason');
  const before$ = zoeData.requests.length;

  assert.equal(await brain.resolveTelegramUser(99999), 'jason');

  const req = zoeData.requests[before$]!;
  assert.equal(req.method, 'GET');
  assert.equal(req.url, '/api/system/resolve-telegram/99999');
  assert.equal(req.headers['x-internal-token'], 'seekrit');
});

test('resolveTelegramUser: an unlinked id resolves to null (not an error)', async () => {
  assert.equal(await brain.resolveTelegramUser(12345), null);
});

// ─── link tokens ─────────────────────────────────────────────────────────────

test('consumeLinkToken: posts the token + the VERIFIED sender id, stringified', async () => {
  zoeData.tokens.set('tok123', 'jason');
  const before$ = zoeData.requests.length;

  assert.equal(await brain.consumeLinkToken('tok123', 6308082458, 'jbert'), 'jason');

  const req = zoeData.requests[before$]!;
  assert.equal(req.method, 'POST');
  assert.match(req.url, /\/api\/system\/telegram\/consume-link-token$/);
  assert.deepEqual(req.body, {
    token: 'tok123',
    telegram_id: '6308082458',
    telegram_username: 'jbert',
  });
});

test('consumeLinkToken: an invalid/expired token (HTTP 400) resolves to null, not a throw', async () => {
  assert.equal(await brain.consumeLinkToken('nope', 123), null);
});

test('sessionFor: ZOE_TELEGRAM_TRIAL=1 namespaces the session id', () => {
  // The same user has the same chatId in both bots, and zoe-data keys its
  // in-memory context AND persisted chat_messages by the session id — so an
  // unflagged trial writes into the user's PRODUCTION conversation and steers
  // subsequent live turns (cross-review, #1639).
  const prev = process.env.ZOE_TELEGRAM_TRIAL;
  process.env.ZOE_TELEGRAM_TRIAL = '1';
  try {
    const id = brain.sessionFor(4242);
    assert.ok(id.startsWith('trial2x-'), `expected a namespaced id, got ${id}`);
    assert.notEqual(id, 'telegram-4242', 'a trial must not share the live session id');
  } finally {
    if (prev === undefined) delete process.env.ZOE_TELEGRAM_TRIAL;
    else process.env.ZOE_TELEGRAM_TRIAL = prev;
  }
});

test('sessionFor: without the flag the LEGACY id is unchanged', () => {
  // NEGATIVE CONTROL. The legacy id is what carries a user's existing context
  // across cutover — namespacing it unconditionally would silently reset every
  // conversation, which is the same data loss with the sign flipped.
  delete process.env.ZOE_TELEGRAM_TRIAL;
  assert.equal(brain.sessionFor(4242), 'telegram-4242');
});

// ─── bot registration ────────────────────────────────────────────────────────

test('registerBotUsername: posts the @username so the settings UI can build deep links', async () => {
  const before$ = zoeData.requests.length;
  await brain.registerBotUsername('zoe_test_bot');
  const req = zoeData.requests[before$]!;
  assert.match(req.url, /\/api\/system\/telegram\/register-bot$/);
  assert.deepEqual(req.body, { username: 'zoe_test_bot' });
});

test('registerBotUsername: ZOE_TELEGRAM_TRIAL=1 suppresses the global registration', async () => {
  // zoe-data's telegram_link.set_bot_username holds ONE username and every
  // Settings QR / deep link is built from it. A parallel trial that registered
  // would repoint PRODUCTION deep links at the temporary bot and leave them
  // there after the trial stops (cross-review, #1639).
  const prev = process.env.ZOE_TELEGRAM_TRIAL;
  process.env.ZOE_TELEGRAM_TRIAL = '1';
  try {
    const before$ = zoeData.requests.length;
    await brain.registerBotUsername('trial_bot');
    assert.equal(zoeData.requests.length, before$, 'the trial bot must NOT reach zoe-data');
  } finally {
    if (prev === undefined) delete process.env.ZOE_TELEGRAM_TRIAL;
    else process.env.ZOE_TELEGRAM_TRIAL = prev;
  }
});

test('registerBotUsername: the flag is read per call, so cutover still registers', async () => {
  // NEGATIVE CONTROL for the suppression. If the guard were evaluated once at
  // import, or inverted, cutover would silently stop registering the new bot and
  // every deep link would keep pointing at the retired one.
  delete process.env.ZOE_TELEGRAM_TRIAL;
  const before$ = zoeData.requests.length;
  await brain.registerBotUsername('cutover_bot');
  assert.equal(zoeData.requests.length, before$ + 1);
  assert.deepEqual(zoeData.requests[before$]!.body, { username: 'cutover_bot' });
});

// ─── the brain call ──────────────────────────────────────────────────────────

test('askZoeAs: forwards the acting user via X-Zoe-User-Id on the trusted path', async () => {
  zoeData.reply = 'hello jason';
  const before$ = zoeData.requests.length;

  assert.equal(await brain.askZoeAs('hi', 'telegram-42', 'jason'), 'hello jason');

  const req = zoeData.requests[before$]!;
  assert.equal(req.method, 'POST');
  assert.equal(req.url, '/api/chat/?stream=false');
  assert.equal(req.headers['x-zoe-user-id'], 'jason'); // trusted forwarded identity
  assert.equal(req.headers['x-internal-token'], 'seekrit'); // proves trust off-loopback
  assert.deepEqual(req.body, { message: 'hi', session_id: 'telegram-42', channel: 'telegram' });
});

// ─── session epochs (/new) ───────────────────────────────────────────────────

test('bumpSession rotates sessionFor; other chats are unaffected', () => {
  assert.equal(brain.sessionFor(42), 'telegram-42'); // legacy id until first /new
  brain.bumpSession(42);
  assert.equal(brain.sessionFor(42), 'telegram-42-e1');
  brain.bumpSession(42);
  assert.equal(brain.sessionFor(42), 'telegram-42-e2');
  assert.equal(brain.sessionFor(7), 'telegram-7');
});

test('the epoch map is plain JSON on disk — it is OURS, so it survives the 1.x→2.x store reset', async () => {
  const { readFileSync } = await import('node:fs');
  const parsed = JSON.parse(readFileSync(process.env.SESSION_EPOCHS_PATH!, 'utf8')) as Record<
    string,
    number
  >;
  assert.equal(parsed['42'], 2);
  // No Flue schema version, no SQLite header — nothing the runtime upgrade can
  // reject. This is why `/new` history is portable at cutover and Flue's own
  // conversation store is not.
});
