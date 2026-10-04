/**
 * Voice notes end to end, entirely offline, with the flags ON
 * (ZOE_TELEGRAM_VOICE_NOTES=1, ZOE_TELEGRAM_VOICE_REPLY=both, ZOE_BASE_URL set).
 *
 * Drives the REAL grammY handler registration against the mock Bot API and the
 * mock zoe-data: a linked sender's voice note is fetched via getFile + the
 * file route, posted to telegram/transcribe with the internal token, run
 * through /api/chat AS the member on the telegram channel, synthesised, and
 * leaves as an uploaded OGG through sendVoice PLUS a sendMessage; an unlinked
 * or forwarded note is refused with ZERO getFile and ZERO media calls; `/talk`
 * answers with a URL button to the existing voice page.
 *
 * test/voice_note_off.test.ts is the control: same updates, flags unset, nothing
 * happens. (One process per file — node --test — so each sees its own env.)
 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import { textUpdate, voiceUpdate } from './helpers/mock-telegram.ts';
import { waitFor } from './helpers/mock-zoe-data.ts';
import { startChannelHarness, type ChannelHarness } from './helpers/harness.ts';

let h: ChannelHarness;
const OGG_NOTE = new Uint8Array([0x4f, 0x67, 0x67, 0x53, 0x00, 0x02, 0x01, 0x02, 0x03, 0x04]);

function hits(prefix: string) {
  return h.zoeData.requests.filter((r) => r.url.startsWith(prefix));
}

before(async () => {
  h = await startChannelHarness({
    env: {
      ZOE_TELEGRAM_VOICE_NOTES: '1',
      ZOE_TELEGRAM_VOICE_REPLY: 'both',
      ZOE_BASE_URL: 'https://zoe.example',
    },
  });
  h.zoeData.links.set('99999', 'jason');
  h.telegram.addFile('note-1', OGG_NOTE);
  h.telegram.addFile('note-fwd', OGG_NOTE);
  const up = await waitFor(() => h.health.polling, 15_000);
  assert.ok(up, 'the bot never reported polling — takeover failed against the mock Bot API');
});

after(async () => {
  await h.stop();
});

describe('a linked sender sends a voice note', () => {
  it('is fetched, transcribed, answered AS the member, and replied to in kind (voice + text)', async () => {
    h.telegram.push(voiceUpdate(99999, 42, 'note-1', { duration: 4 }));
    const done = await waitFor(() => h.telegram.voices.length > 0 && h.telegram.sent.length > 0);
    assert.ok(done, 'no voice reply and text reply arrived');

    // getFile, then the download route (path only; the token-bearing URL is never recorded anywhere).
    assert.ok(h.telegram.methods.includes('getFile'));
    assert.deepEqual(h.telegram.downloads, ['voice/note-1.oga']);

    // The OGG went to zoe-data's transcribe contract with the internal token, as raw bytes.
    const tr = hits('/api/system/telegram/transcribe');
    assert.equal(tr.length, 1);
    assert.equal(tr[0]!.method, 'POST');
    assert.equal(tr[0]!.headers['x-internal-token'], 'seekrit');
    assert.deepEqual(Array.from(tr[0]!.raw), Array.from(OGG_NOTE));

    // The brain turn is the SAME /api/chat contract the text lane uses, as the member.
    const chat = hits('/api/chat/');
    assert.equal(chat.length, 1);
    assert.equal(chat[0]!.headers['x-zoe-user-id'], 'jason');
    assert.deepEqual(chat[0]!.body, { message: 'what time is it', session_id: 'telegram-42', channel: 'telegram' });

    // Synthesised, then uploaded through sendVoice as the OGG zoe-data returned.
    const syn = hits('/api/system/telegram/synthesize');
    assert.equal(syn.length, 1);
    assert.deepEqual(syn[0]!.body, { text: h.zoeData.reply });
    assert.equal(h.telegram.voices[0]!.chat_id, 42);
    assert.deepEqual(Array.from(h.telegram.voices[0]!.bytes), Array.from(h.zoeData.ogg));
    assert.equal(h.telegram.voices[0]!.filename, 'zoe.ogg');
    // mode=both: the text too.
    assert.deepEqual(h.telegram.sent, [{ chat_id: 42, text: h.zoeData.reply }]);
  });

  it('with Kokoro down (503) the reply still arrives as text', async () => {
    const sentBefore = h.telegram.sent.length;
    const voicesBefore = h.telegram.voices.length;
    h.zoeData.synthOk = false;
    try {
      h.telegram.push(voiceUpdate(99999, 42, 'note-1', { duration: 3 }));
      const done = await waitFor(() => h.telegram.sent.length > sentBefore);
      assert.ok(done, 'no text fallback arrived');
      assert.equal(h.telegram.voices.length, voicesBefore, 'no voice note can be sent without audio');
    } finally {
      h.zoeData.synthOk = true;
    }
  });
});

describe('controls', () => {
  it('an unlinked sender: onboarding text, ZERO getFile, ZERO media calls', async () => {
    const methodsBefore = h.telegram.methods.filter((m) => m === 'getFile').length;
    const mediaBefore = hits('/api/system/telegram/transcribe').length;
    const sentBefore = h.telegram.sent.length;
    h.telegram.push(voiceUpdate(77777, 43, 'note-1'));
    const replied = await waitFor(() => h.telegram.sent.length > sentBefore);
    assert.ok(replied);
    assert.match(h.telegram.sent.at(-1)!.text, /not linked/i);
    assert.equal(h.telegram.methods.filter((m) => m === 'getFile').length, methodsBefore);
    assert.equal(hits('/api/system/telegram/transcribe').length, mediaBefore);
  });

  it('a FORWARDED note from a linked sender: refused, ZERO getFile', async () => {
    const methodsBefore = h.telegram.methods.filter((m) => m === 'getFile').length;
    const sentBefore = h.telegram.sent.length;
    h.telegram.push(voiceUpdate(99999, 42, 'note-fwd', { forwarded: true }));
    const replied = await waitFor(() => h.telegram.sent.length > sentBefore);
    assert.ok(replied);
    assert.match(h.telegram.sent.at(-1)!.text, /forwarded/i);
    assert.equal(h.telegram.methods.filter((m) => m === 'getFile').length, methodsBefore);
  });

  it('an over-long note: refused BEFORE getFile', async () => {
    const methodsBefore = h.telegram.methods.filter((m) => m === 'getFile').length;
    const sentBefore = h.telegram.sent.length;
    h.telegram.push(voiceUpdate(99999, 42, 'note-1', { duration: 61 }));
    const replied = await waitFor(() => h.telegram.sent.length > sentBefore);
    assert.ok(replied);
    assert.match(h.telegram.sent.at(-1)!.text, /too long/i);
    assert.equal(h.telegram.methods.filter((m) => m === 'getFile').length, methodsBefore);
  });
});

describe('/talk', () => {
  it('answers a linked sender with ONE URL button to the existing push-to-talk page, never the brain', async () => {
    const chatBefore = hits('/api/chat/').length;
    const sentBefore = h.telegram.sent.length;
    h.telegram.push(textUpdate(99999, 42, '/talk'));
    const replied = await waitFor(() => h.telegram.sent.length > sentBefore);
    assert.ok(replied);
    const msg = h.telegram.sent.at(-1)!;
    assert.match(msg.text, /press and hold/i);
    assert.deepEqual(msg.reply_markup, {
      inline_keyboard: [[{ text: 'Talk to Zoe', url: 'https://zoe.example/voice.html' }]],
    });
    assert.equal(hits('/api/chat/').length, chatBefore, '/talk must not fall through to the brain');
  });
});
