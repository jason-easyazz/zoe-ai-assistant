/**
 * `POST /internal/notify` (src/notify.ts): zoe-data → a linked member's chat, sent with THIS bot's
 * token ("send to my phone", A9 2026-10-18). Every gate is a negative control: it must not send.
 * Pure decision first, then the REAL route map against the mock Bot API + mock zoe-data.
 */
import assert from 'node:assert/strict';
import { after, before, describe, it, test } from 'node:test';

import { bearerMatches, handleNotify, isLoopbackAddress, type NotifyDeps } from '../src/notify.ts';
import { waitFor } from './helpers/mock-zoe-data.ts';
import { startChannelHarness, type ChannelHarness } from './helpers/harness.ts';

function deps(over: Partial<NotifyDeps> = {}) {
  const sent: unknown[] = [];
  const d: NotifyDeps = {
    token: 'seekrit',
    resolve: async (id) => (id === 777 ? 'jason' : null),
    send: async (chatId, text, link) => void sent.push({ chatId, text, link }),
    ...over,
  };
  return { d, sent };
}

const OK = { telegram_id: '777', text: 'Tap to finish', url: 'https://zoe.example/x#t=1', label: 'Open on this phone' };

test('a linked member gets the message with ONE url button', async () => {
  const { d, sent } = deps();
  assert.deepEqual(await handleNotify('Bearer seekrit', OK, d), { status: 200, body: { ok: true } });
  assert.deepEqual(sent, [{ chatId: 777, text: 'Tap to finish', link: { label: 'Open on this phone', url: OK.url } }]);
});

test('NEGATIVE: every refusal sends nothing', async () => {
  const cases: Array<[string | undefined, unknown, Partial<NotifyDeps>, number]> = [
    ['Bearer ', OK, { token: undefined }, 503], ['Bearer ', OK, { token: '  ' }, 503],  // secret unset = route off
    [undefined, OK, {}, 401], ['seekrit', OK, {}, 401], ['Bearer wrong', OK, {}, 401], ['bearer seekrit', OK, {}, 401],
    ['Bearer seekrit', { ...OK, telegram_id: '12345' }, {}, 403],                      // not a linked member
    ['Bearer seekrit', null, {}, 400], ['Bearer seekrit', { ...OK, telegram_id: '-5' }, {}, 400],
    ['Bearer seekrit', { ...OK, text: '' }, {}, 400], ['Bearer seekrit', { ...OK, text: 'x'.repeat(1001) }, {}, 400],
    ['Bearer seekrit', { ...OK, url: 'javascript:alert(1)' }, {}, 400],
  ];
  for (const [auth, body, over, status] of cases) {
    const { d, sent } = deps(over);
    assert.equal((await handleNotify(auth, body, d)).status, status, JSON.stringify([auth, body]));
    assert.equal(sent.length, 0);
  }
});

test('a resolver or Telegram failure is a 502, never a throw', async () => {
  const boom = async () => {
    throw new Error('down');
  };
  assert.equal((await handleNotify('Bearer seekrit', OK, deps({ resolve: boom }).d)).status, 502);
  assert.equal((await handleNotify('Bearer seekrit', OK, deps({ send: boom }).d)).status, 502);
});

test('loopback and bearer checks are exact', () => {
  for (const a of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) assert.equal(isLoopbackAddress(a), true, a);
  for (const a of ['192.168.1.61', '::ffff:192.168.1.61', '', 'fe80::1', '1127.0.0.1']) assert.equal(isLoopbackAddress(a), false, a);
  assert.equal(bearerMatches('Bearer abc', 'abc'), true);
  assert.equal(bearerMatches('Bearer abc ', 'abc'), false);
});

describe('the real route map', () => {
  let h: ChannelHarness;
  before(async () => {
    h = await startChannelHarness();
    assert.ok(await waitFor(() => h.health.polling, 15_000), 'the bot never reported polling');
    h.zoeData.links.set('99999', 'jason');
  });
  after(async () => {
    await h.stop();
  });

  const send = (auth: string, body: unknown, remoteAddress: string | null = '127.0.0.1') =>
    h.app.fetch(new Request('http://telegram.test/internal/notify', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: auth },
      body: JSON.stringify(body),
    }), remoteAddress ? { incoming: { socket: { remoteAddress } } } : undefined);

  it('sends to a linked member with the URL button, from loopback', async () => {
    const res = await send('Bearer seekrit', { telegram_id: '99999', text: 'Tap', url: 'https://zoe.example/p', label: 'Open' },
      '::ffff:127.0.0.1');
    assert.equal(res.status, 200);
    const msg = h.telegram.sent.at(-1)!;
    assert.equal(String(msg.chat_id), '99999');
    assert.deepEqual(msg.reply_markup, { inline_keyboard: [[{ text: 'Open', url: 'https://zoe.example/p' }]] });
  });

  it('NEGATIVE: the wrong secret, an unlinked chat and a LAN caller send nothing', async () => {
    const before = h.telegram.sent.length;
    assert.equal((await send('Bearer nope', { telegram_id: '99999', text: 'hi' })).status, 401);
    assert.equal((await send('Bearer seekrit', { telegram_id: '424242', text: 'hi' })).status, 403);
    assert.equal((await send('Bearer seekrit', { telegram_id: '99999', text: 'hi' }, '192.168.1.61')).status, 403);
    assert.equal((await send('Bearer seekrit', { telegram_id: '99999', text: 'hi' }, null)).status, 403);  // unknown peer
    assert.equal(h.telegram.sent.length, before);
  });
});
