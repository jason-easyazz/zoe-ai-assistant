/**
 * The whole channel, end to end, entirely offline.
 *
 * This is the test the beta suite did not have: it drives the REAL grammY
 * transport, the REAL handler registration order, and the REAL route map against
 * a mock Bot API and a mock zoe-data. It is what makes the port's claim of
 * "same external behavior" checkable rather than asserted, because it exercises
 * the parts that only exist once everything is wired together:
 *
 *   - takeover (deleteWebhook → getMe → getUpdates) flips /health 503 → 200;
 *   - a linked sender's message resolves, calls /api/chat AS them, and the reply
 *     leaves through sendMessage;
 *   - an unlinked sender gets the link instructions and /api/chat is NEVER called;
 *   - `/start <token>` links through the token redeemer;
 *   - `/new` is intercepted by the command handler and never falls through to the
 *     brain — the ordering bug that a naive port would introduce silently.
 *
 * NO REAL TELEGRAM AND NO REAL ZOE-DATA. Both are loopback mocks on ephemeral
 * ports, wired in by env BEFORE the dynamic import (see helpers/harness.ts).
 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import { textUpdate } from './helpers/mock-telegram.ts';
import { waitFor } from './helpers/mock-zoe-data.ts';
import { startChannelHarness, type ChannelHarness } from './helpers/harness.ts';

let h: ChannelHarness;

/** Requests the mock zoe-data saw for a given path prefix. */
function hits(prefix: string) {
  return h.zoeData.requests.filter((r) => r.url.startsWith(prefix));
}

before(async () => {
  h = await startChannelHarness();
  // The bot takes the token over at module load; wait for onStart.
  const up = await waitFor(() => h.health.polling, 15_000);
  assert.ok(up, 'the bot never reported polling — takeover failed against the mock Bot API');
});

after(async () => {
  await h.stop();
});

describe('takeover + health', () => {
  it('cleared the webhook before polling (queued updates are never dropped)', async () => {
    // `onStart` (and therefore health.polling) fires BEFORE the first getUpdates
    // is issued, so wait for the poll itself rather than assuming it has landed.
    const polled = await waitFor(() => h.telegram.methods.includes('getUpdates'), 15_000);
    assert.ok(polled, 'the bot never issued a getUpdates');
    assert.ok(h.telegram.methods.includes('deleteWebhook'));
    assert.ok(
      h.telegram.methods.indexOf('deleteWebhook') < h.telegram.methods.indexOf('getUpdates'),
      'deleteWebhook must precede the first getUpdates — dropping queued updates during a ' +
        'takeover loses messages neither poller can recover',
    );
  });

  it('/health is 200 with polling:true once the loop is up', async () => {
    const res = await h.health$();
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), {
      ok: true,
      service: 'flue-zoe-telegram',
      polling: true,
    });
  });

  it('registered the @username with zoe-data so the settings UI can build deep links', async () => {
    const seen = await waitFor(() => hits('/api/system/telegram/register-bot').length > 0);
    assert.ok(seen, 'register-bot was never called after takeover');
    assert.deepEqual(hits('/api/system/telegram/register-bot')[0]!.body, {
      username: 'zoe_test_bot',
    });
  });
});

describe('a linked sender', () => {
  it('reaches the brain AS their Zoe user and the reply comes back over Telegram', async () => {
    h.zoeData.links.set('99999', 'jason');
    h.zoeData.reply = 'You mentioned a burrito.';
    const chatsBefore = hits('/api/chat/').length;

    h.telegram.push(textUpdate(99999, 42, 'what did I have for lunch?'));

    const answered = await waitFor(
      () => h.telegram.sent.some((m) => m.text === 'You mentioned a burrito.'),
      15_000,
    );
    assert.ok(answered, 'the reply never left through sendMessage');

    const chat = hits('/api/chat/')[chatsBefore]!;
    assert.equal(chat.headers['x-zoe-user-id'], 'jason');
    assert.deepEqual(chat.body, {
      message: 'what did I have for lunch?',
      session_id: 'telegram-42',
      channel: 'telegram',
    });
    assert.equal(h.telegram.sent.at(-1)!.chat_id, 42);
  });
});

describe('an unlinked sender', () => {
  it('is guided to link and NEVER reaches /api/chat', async () => {
    const chatsBefore = hits('/api/chat/').length;

    h.telegram.push(textUpdate(77777, 43, 'hi'));

    const answered = await waitFor(
      () => h.telegram.sent.some((m) => m.chat_id === 43),
      15_000,
    );
    assert.ok(answered, 'the unlinked sender got no reply at all');
    assert.match(h.telegram.sent.at(-1)!.text, /not linked/i);
    assert.match(h.telegram.sent.at(-1)!.text, /77777/);
    assert.equal(
      hits('/api/chat/').length,
      chatsBefore,
      'the brain must not be called for an unlinked sender',
    );
  });
});

describe('/start linking', () => {
  it('redeems a valid token and confirms the link', async () => {
    h.zoeData.tokens.set('good-token', 'kate');

    h.telegram.push(textUpdate(55555, 44, '/start good-token', 'katie_t'));

    const answered = await waitFor(() => h.telegram.sent.some((m) => m.chat_id === 44), 15_000);
    assert.ok(answered);
    assert.match(h.telegram.sent.at(-1)!.text, /Linked/i);

    const consume = hits('/api/system/telegram/consume-link-token').at(-1)!;
    assert.deepEqual(consume.body, {
      token: 'good-token',
      telegram_id: '55555',
      telegram_username: 'katie_t',
    });
  });
});

describe('/new command ordering', () => {
  it('is handled as a COMMAND and never falls through to the brain', async () => {
    h.zoeData.links.set('99999', 'jason');
    const chatsBefore = hits('/api/chat/').length;

    h.telegram.push(textUpdate(99999, 42, '/new'));

    const answered = await waitFor(
      () => h.telegram.sent.some((m) => /fresh conversation/i.test(m.text)),
      15_000,
    );
    assert.ok(answered, '/new was not answered by the command handler');
    assert.equal(
      hits('/api/chat/').length,
      chatsBefore,
      '/new leaked to the brain — the command handler must be registered before message:text',
    );
  });

  it('rotated the session id, so the NEXT turn opens a fresh zoe-data session', async () => {
    const chatsBefore = hits('/api/chat/').length;
    h.zoeData.reply = 'fresh start';

    h.telegram.push(textUpdate(99999, 42, 'still there?'));

    const answered = await waitFor(() => hits('/api/chat/').length > chatsBefore, 15_000);
    assert.ok(answered);
    assert.equal(
      (hits('/api/chat/').at(-1)!.body as { session_id: string }).session_id,
      'telegram-42-e1',
    );
  });
});
