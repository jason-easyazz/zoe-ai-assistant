/**
 * Voice notes + /talk, as pure decisions (src/voice.ts). No grammY, no token,
 * no network. Every gate carries a negative control: an unlinked sender, a
 * forwarded note and an over-long note must never reach `download`; an empty
 * transcript must never reach the brain; a voice outage must fall back to text.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  MAX_NOTE_BYTES,
  allowForwarded,
  deliverReply,
  framedTranscript,
  handleTalk,
  handleVoiceNote,
  optionsFromEnv,
  redactToken,
  refusalFor,
  talkPageUrl,
  voiceMaxSeconds,
  voiceNotesEnabled,
  voiceReplyMode,
  type VoiceDeps,
  type VoiceNote,
} from '../src/voice.ts';

const note = (over: Partial<VoiceNote> = {}): VoiceNote => ({
  kind: 'voice',
  fileId: 'f1',
  fileUniqueId: 'u-f1',
  duration: 5,
  fileSize: 60_000,
  forwarded: false,
  ...over,
});

const OPTS = { mode: 'voice' as const, maxSeconds: 60, allowForwarded: false };

interface Trace {
  downloads: string[];
  transcribed: number[];
  asks: Array<{ text: string; session: string; userId: string }>;
  synth: string[];
  replies: string[];
  voices: number[];
  logs: string[];
}

function deps(over: Partial<VoiceDeps> & { linked?: boolean; transcript?: string; synthOk?: boolean } = {}) {
  const trace: Trace = { downloads: [], transcribed: [], asks: [], synth: [], replies: [], voices: [], logs: [] };
  const d: VoiceDeps = {
    resolve: async () => (over.linked === false ? null : 'jason'),
    session: (chatId) => `telegram-${chatId}`,
    ask: async (text, session, userId) => {
      trace.asks.push({ text, session, userId });
      return 'It is ten past three.';
    },
    download: async (fileId) => {
      trace.downloads.push(fileId);
      return new Uint8Array([1, 2, 3]);
    },
    transcribe: async (audio) => {
      trace.transcribed.push(audio.byteLength);
      return over.transcript ?? 'what time is it';
    },
    synthesize: async (text) => {
      trace.synth.push(text);
      return over.synthOk === false ? null : new Uint8Array([0x4f, 0x67, 0x67, 0x53]);
    },
    reply: async (t) => {
      trace.replies.push(t);
    },
    replyVoice: async (ogg) => {
      trace.voices.push(ogg.byteLength);
    },
    log: (line) => {
      trace.logs.push(line);
    },
    ...over,
  };
  return { d, trace };
}

// ─── flags: every default is OFF / today's behaviour ────────────────────────

test('flags: defaults are off / voice / 60 / forwarded refused / no talk page', () => {
  const env = {};
  assert.equal(voiceNotesEnabled(env), false);
  assert.equal(voiceReplyMode(env), 'voice');
  assert.equal(voiceMaxSeconds(env), 60);
  assert.equal(allowForwarded(env), false);
  assert.equal(talkPageUrl(env), null);
  assert.deepEqual(optionsFromEnv(env), { mode: 'voice', maxSeconds: 60, allowForwarded: false });
});

test('flags: parsing', () => {
  assert.equal(voiceNotesEnabled({ ZOE_TELEGRAM_VOICE_NOTES: '1' }), true);
  assert.equal(voiceNotesEnabled({ ZOE_TELEGRAM_VOICE_NOTES: 'off' }), false);
  assert.equal(voiceReplyMode({ ZOE_TELEGRAM_VOICE_REPLY: 'both' }), 'both');
  assert.equal(voiceReplyMode({ ZOE_TELEGRAM_VOICE_REPLY: 'TEXT' }), 'text');
  assert.equal(voiceReplyMode({ ZOE_TELEGRAM_VOICE_REPLY: 'garbage' }), 'voice');
  assert.equal(voiceMaxSeconds({ ZOE_TELEGRAM_VOICE_MAX_S: '120' }), 120);
  assert.equal(voiceMaxSeconds({ ZOE_TELEGRAM_VOICE_MAX_S: '-3' }), 60);
  assert.equal(allowForwarded({ ZOE_TELEGRAM_ALLOW_FORWARDED: 'true' }), true);
  assert.equal(talkPageUrl({ ZOE_BASE_URL: 'https://zoe.example/' }), 'https://zoe.example/voice.html');
});

// ─── refusals happen BEFORE download ─────────────────────────────────────────

test('refusalFor: forwarded (default), over-long, oversize; otherwise null', () => {
  assert.equal(refusalFor(note(), OPTS), null);
  assert.match(refusalFor(note({ forwarded: true }), OPTS)!, /forwarded/i);
  assert.equal(refusalFor(note({ forwarded: true }), { ...OPTS, allowForwarded: true }), null);
  assert.match(refusalFor(note({ duration: 61 }), OPTS)!, /under 60 seconds/);
  assert.equal(refusalFor(note({ duration: 60 }), OPTS), null);
  assert.match(refusalFor(note({ fileSize: MAX_NOTE_BYTES + 1 }), OPTS)!, /too big/i);
  assert.match(refusalFor(note({ duration: Number.NaN }), OPTS)!, /too long/i);
});

test('unlinked sender NEGATIVE CONTROL: onboarding text, nothing downloaded, brain never asked', async () => {
  const { d, trace } = deps({ linked: false });
  await handleVoiceNote(77777, 42, note(), d, OPTS);
  assert.deepEqual(trace.downloads, []);
  assert.deepEqual(trace.asks, []);
  assert.equal(trace.replies.length, 1);
  assert.match(trace.replies[0]!, /not linked/i);
  assert.match(trace.replies[0]!, /77777/);
});

test('forwarded note NEGATIVE CONTROL: refused before getFile (someone else\'s words never run as the member)', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note({ forwarded: true }), d, OPTS);
  assert.deepEqual(trace.downloads, []);
  assert.deepEqual(trace.asks, []);
  assert.match(trace.replies[0]!, /forwarded/i);
});

test('forwarded note when ALLOWED is framed as quoted material, not a command', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note({ forwarded: true }), d, { ...OPTS, allowForwarded: true });
  assert.equal(trace.downloads.length, 1);
  assert.match(trace.asks[0]!.text, /^\(Forwarded voice note .*quoted, not my instruction\) "what time is it"$/);
  assert.equal(framedTranscript('x', note()), 'x');
});

test('over-long note NEGATIVE CONTROL: refused before getFile', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note({ duration: 61 }), d, OPTS);
  assert.deepEqual(trace.downloads, []);
  assert.match(trace.replies[0]!, /too long/i);
});

// ─── the happy path: same identity, same brain call, reply in kind ──────────

test('linked sender, mode=voice: download → transcribe → /api/chat AS the member → one voice note, no text', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(99999, 42, note(), d, OPTS);
  assert.deepEqual(trace.downloads, ['f1']);
  assert.deepEqual(trace.transcribed, [3]);
  assert.deepEqual(trace.asks, [{ text: 'what time is it', session: 'telegram-42', userId: 'jason' }]);
  assert.deepEqual(trace.synth, ['It is ten past three.']);
  assert.deepEqual(trace.voices, [4]);
  assert.deepEqual(trace.replies, []);
  assert.equal(trace.logs.length, 1);
  assert.match(trace.logs[0]!, /^TELEGRAM_VOICE kind=voice file=u-f1 dur=5s mode=voice delivered=voice download_ms=\d+ stt_ms=\d+ brain_ms=\d+ reply_ms=\d+ total_ms=\d+$/);
  assert.doesNotMatch(trace.logs[0]!, /what time|ten past/); // timings + ids only; never the transcript or reply
});

test('mode=text: text only, synthesis never called', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note(), d, { ...OPTS, mode: 'text' });
  assert.deepEqual(trace.synth, []);
  assert.deepEqual(trace.voices, []);
  assert.deepEqual(trace.replies, ['It is ten past three.']);
});

test('mode=both: voice note AND the text', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note(), d, { ...OPTS, mode: 'both' });
  assert.deepEqual(trace.voices, [4]);
  assert.deepEqual(trace.replies, ['It is ten past three.']);
});

test('mode=voice with synthesis unavailable: falls back to text so Zoe is never silent', async () => {
  const { d, trace } = deps({ synthOk: false });
  await handleVoiceNote(1, 42, note(), d, OPTS);
  assert.deepEqual(trace.voices, []);
  assert.deepEqual(trace.replies, ['It is ten past three.']);
  assert.match(trace.logs[0]!, /delivered=text/);
});

test('deliverReply: a synthesis that THROWS also falls back to text', async () => {
  const { d, trace } = deps({
    synthesize: async () => {
      throw new Error('zoe-data down');
    },
  });
  assert.equal(await deliverReply('hi', d, 'both'), 'text');
  assert.deepEqual(trace.replies, ['hi']);
});

test('empty transcript NEGATIVE CONTROL: the brain is never asked', async () => {
  const { d, trace } = deps({ transcript: '   ' });
  await handleVoiceNote(1, 42, note(), d, OPTS);
  assert.deepEqual(trace.asks, []);
  assert.match(trace.replies[0]!, /couldn't make out/i);
});

test('download/transcribe failure is answered generically — no stack, no URL, brain never asked', async () => {
  const { d, trace } = deps({
    download: async () => {
      throw new Error('file download HTTP 500 https://api.telegram.org/file/bot123:SECRET/x');
    },
  });
  await handleVoiceNote(1, 42, note(), d, OPTS);
  assert.deepEqual(trace.asks, []);
  assert.equal(trace.replies.length, 1);
  assert.match(trace.replies[0]!, /couldn't take in/i);
  assert.doesNotMatch(trace.replies[0]!, /SECRET|http/);
});

test('redactToken strips a bot token from any error text that is logged', () => {
  assert.equal(
    redactToken(new Error('request to https://api.telegram.org/file/bot123456:AAE-secret_x/voice/a.oga failed')),
    'Error: request to https://api.telegram.org/file/bot<redacted>/voice/a.oga failed',
  );
  assert.equal(redactToken('plain'), 'plain');
});

test('audio messages take the same path, tagged kind=audio', async () => {
  const { d, trace } = deps();
  await handleVoiceNote(1, 42, note({ kind: 'audio' }), d, OPTS);
  assert.match(trace.logs[0]!, /kind=audio/);
});

// ─── /talk ───────────────────────────────────────────────────────────────────

test('/talk: a linked sender gets ONE URL button to the existing talk page', async () => {
  const links: Array<{ text: string; label: string; url: string }> = [];
  await handleTalk(1, 'https://zoe.example/voice.html', {
    resolve: async () => 'jason',
    replyWithLink: async (text, label, url) => {
      links.push({ text, label, url });
    },
    reply: async () => assert.fail('plain reply must not be used for a linked sender'),
  });
  assert.deepEqual(links, [
    { text: 'Tap below, then press and hold to talk to me from your phone.', label: 'Talk to Zoe', url: 'https://zoe.example/voice.html' },
  ]);
});

test('/talk NEGATIVE CONTROL: an unlinked sender gets no link', async () => {
  const replies: string[] = [];
  await handleTalk(1, 'https://zoe.example/voice.html', {
    resolve: async () => null,
    replyWithLink: async () => assert.fail('an unlinked sender must not receive the talk link'),
    reply: async (t) => {
      replies.push(t);
    },
  });
  assert.match(replies[0]!, /link your Zoe account/i);
});
