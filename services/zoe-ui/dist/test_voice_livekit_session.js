/*
 * Controlled test for the session handling the LiveKit voice pages need now
 * that POST /api/voice/livekit-audio and /livekit-cancel refuse anonymous
 * callers (they run the full STT → brain → TTS pipeline on the Jetson).
 *
 * The rules this pins are the ones that bite in the room:
 *   - a page with NO stored session mints a guest one rather than failing the
 *     mic silently — the panel must keep working;
 *   - a 401 on an expired guest session re-provisions and retries EXACTLY ONCE
 *     (bounded — a retry loop against the voice pipeline is worse than a 401);
 *   - a signed-in user is NEVER silently downgraded to a guest session;
 *   - both LiveKit call sites on each page go through the helper.
 *
 * Runs the REAL helper block extracted from each page against stub
 * localStorage/fetch — no browser, no network, no jsdom. Desktop and touch are
 * checked INDEPENDENTLY: they carry their own copies by design.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PAGES = [
  { name: 'desktop dist/voice.html', file: 'voice.html', source: 'desktop-voice' },
  { name: 'touch/voice.html', file: 'touch/voice.html', source: 'panel-voice' },
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
function loadHelpers(html, stubs) {
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
  return factory(stubs.localStorage, stubs.fetch);
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

}

main().then(() => {
  console.log(`${checks} checks passed`);
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
