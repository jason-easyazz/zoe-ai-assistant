/**
 * FLAG-OFF CONTROL for voice notes: with ZOE_TELEGRAM_VOICE_NOTES unset (and
 * ZOE_BASE_URL unset) the channel behaves exactly as before this feature —
 * a voice note is received by the poll loop and ignored (no reply, no getFile,
 * no zoe-data media call), and `/talk` falls through to the brain as text.
 *
 * Its own file because node --test runs each file in its own process and the
 * handler set is decided at module load from the env.
 */
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';

import { textUpdate, voiceUpdate } from './helpers/mock-telegram.ts';
import { waitFor } from './helpers/mock-zoe-data.ts';
import { startChannelHarness, type ChannelHarness } from './helpers/harness.ts';

let h: ChannelHarness;

before(async () => {
  h = await startChannelHarness({
    env: { ZOE_TELEGRAM_VOICE_NOTES: undefined, ZOE_BASE_URL: undefined },
  });
  h.zoeData.links.set('99999', 'jason');
  h.telegram.addFile('note-1', new Uint8Array([0x4f, 0x67, 0x67, 0x53]));
  const up = await waitFor(() => h.health.polling, 15_000);
  assert.ok(up, 'the bot never reported polling');
});

after(async () => {
  await h.stop();
});

test('flag off: a voice note from a LINKED sender is ignored — no reply, no getFile, no media call', async () => {
  h.telegram.push(voiceUpdate(99999, 42, 'note-1'));
  // Prove the update was delivered and consumed: a following text turn is answered.
  h.telegram.push(textUpdate(99999, 42, 'hello'));
  const answered = await waitFor(() => h.telegram.sent.length > 0);
  assert.ok(answered, 'the text turn after the voice note was never answered');
  assert.deepEqual(h.telegram.sent, [{ chat_id: 42, text: h.zoeData.reply }]);
  assert.equal(h.telegram.voices.length, 0);
  assert.ok(!h.telegram.methods.includes('getFile'), 'flag off must never call getFile');
  assert.deepEqual(h.telegram.downloads, []);
  assert.equal(h.zoeData.requests.filter((r) => r.url.includes('/telegram/transcribe') || r.url.includes('/telegram/synthesize')).length, 0);
});

test('ZOE_BASE_URL unset: /talk is not a command and falls through to the brain as text (today\'s behaviour)', async () => {
  const sentBefore = h.telegram.sent.length;
  h.telegram.push(textUpdate(99999, 42, '/talk'));
  const replied = await waitFor(() => h.telegram.sent.length > sentBefore);
  assert.ok(replied);
  const chat = h.zoeData.requests.filter((r) => r.url.startsWith('/api/chat/'));
  assert.deepEqual(chat.at(-1)!.body, { message: '/talk', session_id: 'telegram-42', channel: 'telegram' });
  assert.equal(h.telegram.sent.at(-1)!.reply_markup, undefined);
});
