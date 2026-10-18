/*
 * Controlled test for dist/voice.html — the desktop / phone voice page (the
 * Telegram `/talk` target).
 *
 * 1. Its LiveKit mode (`?mode=livekit`) calls POST /api/voice/livekit-token and
 *    /livekit-audio, which refuse anonymous callers. The helper block
 *    (`ensureSessionId` / `voiceFetchWithSession`) mints a guest session when
 *    there is none, retries a 401 EXACTLY ONCE (bounded), and never silently
 *    downgrades a signed-in user. Run the REAL helper against stub
 *    localStorage/fetch — no browser, no network, no jsdom.
 * 2. Its default local push-to-talk lane posts ONE 16 kHz mono PCM WAV per turn
 *    to POST /api/voice/turn (the voice_command path), members only — the
 *    retired /ws/voice/ socket is never opened, and a signed-out phone is asked
 *    to sign in instead of being given a guest session.
 * 3. No page that loads /js/auth.js calls a LiveKit voice endpoint (its
 *    interceptor rejects a 401 before a page's own retry can run, #1652).
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PAGES = [
  { name: 'desktop dist/voice.html', file: 'voice.html', source: 'desktop-voice' },
];

let checks = 0;
// Awaits whatever the body returns — an async assertion that is not awaited
// fails silently, which would make this harness a green light that proves
// nothing. Labels surface in the failure message.
async function check(label, fn) {
  try {
    await fn();
  } catch (err) {
    console.error(`FAILED: ${label}`);
    throw err;
  }
  checks += 1;
}

// ── Extract the helper block from the page, evaluate it against stubs ───────
function loadHelpers(html, stubs, fetchImpl) {
  const start = html.indexOf('function getSessionId(');
  assert(start >= 0, 'getSessionId anchor not found');
  const vf = html.indexOf('async function voiceFetchWithSession(', start);
  assert(vf > start, 'voiceFetchWithSession anchor not found');
  const end = html.indexOf('\n}\n', vf);
  assert(end > vf, 'voiceFetchWithSession end not found');
  const body = html.slice(start, end + 3);
  const factory = new Function(
    'localStorage', 'fetch',
    body + '\nreturn { getSessionId, isStoredSessionGuest, provisionGuestSession, ensureSessionId, voiceFetchWithSession };'
  );
  return factory(stubs.localStorage, fetchImpl || stubs.fetch);
}

function makeStubs(stored, responder) {
  const store = new Map();
  if (stored) store.set('zoe_session', JSON.stringify(stored));
  const calls = [];
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
    removeItem: (k) => store.delete(k),
  };
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return responder(url, init, calls);
  };
  return { localStorage, fetch, calls, store };
}

const ok = (body) => ({ status: 200, ok: true, json: async () => body });
const unauthorized = () => ({ status: 401, ok: false, json: async () => ({ detail: 'Invalid or expired session' }) });

async function main() {
for (const page of PAGES) {
  const html = fs.readFileSync(path.join(__dirname, page.file), 'utf8');

  // ── Both LiveKit call sites go through the helper ────────────────────────
  await check(`${page.name}: livekit calls go through voiceFetchWithSession`, () => {
    const audioIdx = html.indexOf("'/api/voice/livekit-audio'");
    assert(audioIdx > 0, `${page.name}: livekit-audio call site not found`);
    const before = html.slice(Math.max(0, audioIdx - 200), audioIdx);
    assert(
      before.includes('voiceFetchWithSession'),
      `${page.name}: the livekit-audio upload must go through voiceFetchWithSession`
    );
    const tokenIdx = html.indexOf("'/api/voice/livekit-token'");
    assert(tokenIdx > 0, `${page.name}: livekit-token call site not found`);
    assert(
      html.slice(Math.max(0, tokenIdx - 200), tokenIdx).includes('voiceFetchWithSession'),
      `${page.name}: the livekit-token fetch must go through voiceFetchWithSession`
    );
  });

  // ── No stored session → mint a guest one, then send it ───────────────────
  await check(`${page.name}: no session mints a guest session`, () => {
    const stubs = makeStubs(null, (url) =>
      url === '/api/auth/guest' ? ok({ session_id: 'fresh-guest', role: 'guest' }) : ok({ ok: true })
    );
    const h = loadHelpers(html, stubs);
    return h.voiceFetchWithSession('/api/voice/livekit-audio', (sid) => ({ headers: { 'X-Session-ID': sid } }))
      .then((resp) => {
        assert.strictEqual(resp.status, 200);
        assert.strictEqual(stubs.calls[0].url, '/api/auth/guest');
        assert.strictEqual(stubs.calls[1].url, '/api/voice/livekit-audio');
        assert.strictEqual(stubs.calls[1].init.headers['X-Session-ID'], 'fresh-guest');
        assert.strictEqual(stubs.calls.length, 2);
        // The minted session is persisted, so the next turn does not re-mint.
        assert(JSON.parse(stubs.store.get('zoe_session')).session_id === 'fresh-guest');
        // The device_info tag names the page — the two copies stay distinct.
        assert(String(stubs.calls[0].init.body).includes(page.source));
      });
  });

  // ── Expired guest session → exactly one re-provision + retry ─────────────
  await check(`${page.name}: an expired guest session retries once with a fresh one`, () => {
    const stubs = makeStubs({ session_id: 'stale-guest', role: 'guest' }, (url, init) => {
      if (url === '/api/auth/guest') return ok({ session_id: 'new-guest', role: 'guest' });
      return init.headers['X-Session-ID'] === 'new-guest' ? ok({ ok: true }) : unauthorized();
    });
    const h = loadHelpers(html, stubs);
    return h.voiceFetchWithSession('/api/voice/livekit-audio', (sid) => ({ headers: { 'X-Session-ID': sid } }))
      .then((resp) => {
        assert.strictEqual(resp.status, 200);
        assert.deepStrictEqual(
          stubs.calls.map((c) => c.url),
          ['/api/voice/livekit-audio', '/api/auth/guest', '/api/voice/livekit-audio']
        );
        assert.strictEqual(stubs.calls[2].init.headers['X-Session-ID'], 'new-guest');
      });
  });

  // ── A persistent 401 fails after ONE retry — never a loop ────────────────
  await check(`${page.name}: a persistent 401 is bounded at one retry`, () => {
    const stubs = makeStubs({ session_id: 'stale-guest', role: 'guest' }, (url) =>
      url === '/api/auth/guest' ? ok({ session_id: 'new-guest', role: 'guest' }) : unauthorized()
    );
    const h = loadHelpers(html, stubs);
    return h.voiceFetchWithSession('/api/voice/livekit-audio', (sid) => ({ headers: { 'X-Session-ID': sid } }))
      .then((resp) => {
        assert.strictEqual(resp.status, 401);
        const uploads = stubs.calls.filter((c) => c.url === '/api/voice/livekit-audio');
        assert.strictEqual(uploads.length, 2, 'exactly two attempts, never a loop');
      });
  });

  // ── A signed-in user is never downgraded to a guest session ──────────────
  await check(`${page.name}: a signed-in session is not downgraded on 401`, () => {
    const stubs = makeStubs({ session_id: 'sess-jason', role: 'admin', user_id: 'jason' }, () => unauthorized());
    const h = loadHelpers(html, stubs);
    return h.voiceFetchWithSession('/api/voice/livekit-audio', (sid) => ({ headers: { 'X-Session-ID': sid } }))
      .then((resp) => {
        assert.strictEqual(resp.status, 401);
        assert.strictEqual(stubs.calls.length, 1, 'no re-provision for a signed-in user');
        assert(stubs.store.has('zoe_session'), 'the signed-in session must survive');
      });
  });

  // ── A working session costs no extra requests ────────────────────────────
  await check(`${page.name}: a valid session sends exactly one request`, () => {
    const stubs = makeStubs({ session_id: 'sess-jason', role: 'admin' }, () => ok({ ok: true }));
    const h = loadHelpers(html, stubs);
    return h.voiceFetchWithSession('/api/voice/livekit-audio', (sid) => ({ headers: { 'X-Session-ID': sid } }))
      .then(() => {
        assert.strictEqual(stubs.calls.length, 1);
        assert.strictEqual(stubs.calls[0].init.headers['X-Session-ID'], 'sess-jason');
      });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// THE LOCAL PUSH-TO-TALK LANE (A4, 2026-10-19): one POST /api/voice/turn per
// spoken turn — the same voice_command path the wall panel uses — replacing the
// retired /ws/voice/ socket. Run the REAL functions out of the page against stubs.
// ═══════════════════════════════════════════════════════════════════════════
const vm = require('vm');
const DESKTOP_HTML = fs.readFileSync(path.join(__dirname, 'voice.html'), 'utf8');

function sliceFn(html, startAnchor, endAnchor) {
  const start = html.indexOf(startAnchor);
  assert(start >= 0, `voice.html: ${startAnchor} not found`);
  const end = html.indexOf(endAnchor, start);
  assert(end > start, `voice.html: end of ${startAnchor} not found`);
  return html.slice(start, end);
}

// The page code under test: the session helpers, the WAV encoder, and the turn sender.
const LANE_SRC = [
  sliceFn(DESKTOP_HTML, 'function getSessionId(', '// The LiveKit voice endpoints'),
  sliceFn(DESKTOP_HTML, 'function isStoredSessionGuest(', 'async function provisionGuestSession('),
  sliceFn(DESKTOP_HTML, "const SIGN_IN_TEXT", 'function handleVoiceEvent('),
  sliceFn(DESKTOP_HTML, 'async function sendRecordingToServer(', '// ── LiveKit HTTP audio upload'),
].join('\n');

// A fake decode/render pipeline: `samples` are what the "recording" decodes to at 16 kHz.
function laneContext({ stored, samples, responder }) {
  const store = new Map();
  if (stored) store.set('zoe_session', JSON.stringify(stored));
  const calls = [];
  const statuses = [];
  const transcript = [];
  const played = [];
  const offlineArgs = [];
  const ctx = {
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) },
    fetch: async (url, init) => { calls.push({ url, init }); return responder(url, init); },
    Blob: class { constructor(parts, opts) { this.parts = parts; this.type = opts && opts.type; } async arrayBuffer() { return new ArrayBuffer(8); } },
    audioCtx: { decodeAudioData: (_buf, ok) => ok({ duration: samples.length / 16000 }) },
    OfflineAudioContext: class {
      constructor(ch, frames, rate) { offlineArgs.push({ ch, frames, rate }); }
      createBufferSource() { return { connect() {}, start() {} }; }
      get destination() { return {}; }
      async startRendering() { return { getChannelData: () => Float32Array.from(samples) }; }
    },
    btoa: (bin) => Buffer.from(bin, 'binary').toString('base64'),
    // setOrbState writes its own status line in the page: record it, so a message it would bury is caught
    setOrbState: (st) => statuses.push('orb:' + st), scheduleAutoListen: () => {}, startRecording: () => {},
    setStatus: (t) => statuses.push(t),
    setTimeout: () => 0, clearTimeout: () => {}, AbortController,
    document: { getElementById: () => ({ classList: { remove() {} } }) },
    addTranscriptLine: (role, text) => transcript.push([role, text]),
    playAudioBase64: (b64, type, done) => { played.push([b64, type]); if (done) done(); },
    console: { error: () => {}, warn: () => {}, log: () => {} },
    Math, String, Uint8Array, ArrayBuffer, DataView, Float32Array, JSON, Promise, Error,
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(
    LANE_SRC + '\nvar voiceReady = true, serverBusy = false, convMode = false, _silentCycles = 0;' +
    '\nvar maxRecordedRms = 0.5, audioChunks = [], mediaRecorder = { mimeType: "audio/webm" };' +
    '\nvar autoListenTimer = null, speaking = false, isRecording = false;',
    ctx,
  );
  return { ctx, calls, statuses, transcript, played, offlineArgs, store };
}

async function sendOneTurn(env) {
  vm.runInContext('audioChunks = [1]', env.ctx);
  await vm.runInContext('sendRecordingToServer()', env.ctx);
}

await check('voice.html never opens the retired /ws/voice/ socket', () => {
  assert(!DESKTOP_HTML.includes('/ws/voice'.concat('/?')), 'voice.html still builds a /ws/voice/ URL');
  assert(!/new WebSocket\(/.test(DESKTOP_HTML), 'voice.html still opens a WebSocket');
});

await check('a member turn posts ONE 16 kHz mono PCM16 WAV to /api/voice/turn and plays the reply', async () => {
  const env = laneContext({
    stored: { session_id: 'sess-member', role: 'member', user_id: 'member-1' },
    samples: [0, 0.5, -0.5, 1, -1, 2],
    responder: () => ok({ ok: true, text: 'what time is it', reply: 'It is noon.', audio_base64: 'QUJD', content_type: 'audio/wav' }),
  });
  await sendOneTurn(env);
  assert.strictEqual(env.calls.length, 1, 'exactly one request per spoken turn');
  const [call] = env.calls;
  assert.strictEqual(call.url, '/api/voice/turn');
  assert.strictEqual(call.init.method, 'POST');
  assert.strictEqual(call.init.headers['X-Session-ID'], 'sess-member');
  const wav = Buffer.from(JSON.parse(call.init.body).audio_base64, 'base64');
  assert.strictEqual(wav.toString('ascii', 0, 4), 'RIFF', 'the STT reads WAV only (Moonshine load_wav_file)');
  assert.strictEqual(wav.toString('ascii', 8, 12), 'WAVE');
  assert.strictEqual(wav.readUInt16LE(20), 1, 'PCM');
  assert.strictEqual(wav.readUInt16LE(22), 1, 'mono');
  assert.strictEqual(wav.readUInt32LE(24), 16000, '16 kHz');
  assert.strictEqual(wav.readUInt16LE(34), 16, '16-bit');
  assert.strictEqual(wav.readUInt32LE(40), 12, 'data chunk = 6 samples x 2 bytes');
  assert.deepStrictEqual([0, 1, 2, 3, 4, 5].map((i) => wav.readInt16LE(44 + i * 2)), [0, 16383, -16384, 32767, -32768, 32767]);
  assert.deepStrictEqual(env.offlineArgs, [{ ch: 1, frames: 6, rate: 16000 }]);
  assert.deepStrictEqual(env.transcript, [['user', 'what time is it'], ['zoe', 'It is noon.']]);
  assert.deepStrictEqual(env.played, [['QUJD', 'audio/wav']]);
  assert.strictEqual(vm.runInContext('serverBusy', env.ctx), false, 'the turn must release the mic');
});

await check('a signed-out phone sends nothing, is asked to sign in (no guest is minted) and leaves conversation mode', async () => {
  const env = laneContext({ stored: null, samples: [0.1], responder: () => ok({}) });
  vm.runInContext('convMode = true', env.ctx);
  await sendOneTurn(env);
  assert.strictEqual(env.calls.length, 0, 'no request: the voice lane answers members only');
  assert(/sign in/i.test(env.statuses[env.statuses.length - 1]), `the sign-in line must be the LAST status: ${env.statuses}`);
  assert.strictEqual(vm.runInContext('serverBusy', env.ctx), false);
  assert.strictEqual(vm.runInContext('convMode', env.ctx), false, 'a sign-in problem must not re-arm the mic on every noise');
});

await check('an empty transcript counts toward the back-off instead of re-listening at once', async () => {
  const env = laneContext({ stored: { session_id: 'm', role: 'member' }, samples: [0.1],
    responder: () => ok({ ok: true, text: '', reply: '', audio_base64: null }) });
  vm.runInContext('convMode = true', env.ctx);
  await sendOneTurn(env);
  assert.strictEqual(vm.runInContext('_silentCycles', env.ctx), 1);
  assert.deepStrictEqual(env.transcript, []);
});

await check('a guest session sends nothing either', async () => {
  const env = laneContext({ stored: { session_id: 'g1', role: 'guest' }, samples: [0.1], responder: () => ok({}) });
  await sendOneTurn(env);
  assert.strictEqual(env.calls.length, 0);
  assert(env.statuses.some((t) => /sign in/i.test(t)));
});

await check('a 401 from the lane asks for sign-in and is NOT retried', async () => {
  const env = laneContext({ stored: { session_id: 'expired', role: 'member' }, samples: [0.1], responder: () => unauthorized() });
  await sendOneTurn(env);
  assert.strictEqual(env.calls.length, 1, 'no retry, no guest re-provision');
  assert(/sign in/i.test(env.statuses[env.statuses.length - 1]), `the sign-in line must be the LAST status: ${env.statuses}`);
  assert(env.store.has('zoe_session'), 'a member session is never cleared or downgraded here');
});

// ── No page that loads /js/auth.js calls a LiveKit voice endpoint ──────────
// auth.js's interceptor REJECTS a 401 by default, which kills a page's own
// bounded re-provision retry before it can run (#1652). The only page that had
// both (touch/voice.html) is retired, and so is the interceptor's pass-through
// list. If a new page combines them, it needs that pass-through back.
await check('no auth.js-wrapped page fetches a LiveKit voice endpoint', () => {
  const pages = [];
  (function walk(dir) {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) { if (!['lib', 'node_modules'].includes(ent.name)) walk(full); }
      else if (ent.name.endsWith('.html')) pages.push(full);
    }
  })(__dirname);
  assert(pages.length > 10, 'page walk found too few pages to mean anything');
  const offenders = pages.filter((f) => {
    const html = fs.readFileSync(f, 'utf8');
    return /<script[^>]+src="\/?js\/auth\.js/.test(html) && /['"]\/api\/voice\/livekit-/.test(html);
  });
  assert.deepStrictEqual(offenders.map((f) => path.relative(__dirname, f)), []);
  assert(!/<script[^>]+src="\/?js\/auth\.js/.test(DESKTOP_HTML), 'dist/voice.html now loads /js/auth.js');
});

}

main().then(() => {
  console.log(`${checks} checks passed`);
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
