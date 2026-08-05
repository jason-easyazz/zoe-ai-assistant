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
 *
 * ── Why there is a second, WRAPPED section below ───────────────────────────
 * The scenarios above hand `voiceFetchWithSession` a stub `fetch` directly. On
 * touch/voice.html that is NOT the fetch the helper actually calls: the page
 * loads /js/auth.js, which replaces `window.fetch` with an interceptor whose
 * default 401 policy is `Promise.reject`. So the retry these tests "proved"
 * could never run in the room — the helper threw before reaching
 * `resp.status === 401`. A harness that stubs the innermost layer is
 * structurally blind to every layer above it, and that blindness is exactly how
 * the bug shipped past a green suite.
 *
 * The wrapped section closes it by composing the two real artifacts: the REAL
 * interceptor extracted from js/auth.js, installed over the stub fetch, with the
 * REAL page helper on top — the same stack the panel runs. It carries its own
 * negative control (the same scenario with the voice path dropped from the
 * interceptor's allow-list, which must fail), so the section cannot decay into a
 * test that passes no matter what the wrapper does.
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
  // `fetchImpl` lets the wrapped section hand the page the SAME fetch the
  // browser would: js/auth.js's interceptor, not the bare stub.
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

// ── The REAL /js/auth.js fetch interceptor, installed over the stub fetch ───
// Extracted the same way the page helpers are: a slice of the shipped file,
// evaluated against stubs. Everything from `const AUTH_CONFIG` to the end of
// `setupFetchInterceptor` is declarations only (the IIFE's first executable
// statement comes after it), so the slice is safe to evaluate on its own.
const AUTH_JS = path.join(__dirname, 'js', 'auth.js');

// auth.js is chatty; keep the harness output readable without hiding failures.
const quietConsole = { log: () => {}, warn: () => {}, error: () => {} };

function loadAuthInterceptor(stubs, mutate) {
  let src = fs.readFileSync(AUTH_JS, 'utf8');
  if (mutate) src = mutate(src);
  const start = src.indexOf('const AUTH_CONFIG = {');
  assert(start >= 0, 'js/auth.js: AUTH_CONFIG anchor not found');
  const fnIdx = src.indexOf('function setupFetchInterceptor()', start);
  assert(fnIdx > start, 'js/auth.js: setupFetchInterceptor anchor not found');
  const tail = src.indexOf('Fetch interceptor installed', fnIdx);
  assert(tail > fnIdx, 'js/auth.js: interceptor install log not found');
  const end = src.indexOf('\n    }\n', tail);
  assert(end > tail, 'js/auth.js: setupFetchInterceptor end not found');
  const body = src.slice(start, end + 6);

  const windowStub = {
    fetch: stubs.fetch,
    location: { protocol: 'http:', origin: 'http://zoe.local', href: 'http://zoe.local/touch/voice.html' },
  };
  const factory = new Function(
    'window', 'localStorage', 'console', 'URL', 'Request',
    body + '\nsetupFetchInterceptor();\nreturn window.fetch;'
  );
  const wrapped = factory(windowStub, stubs.localStorage, quietConsole, URL, Request);
  assert(
    wrapped && wrapped.__zoeInterceptorApplied,
    'the interceptor did not install — this section would be testing the raw stub'
  );
  assert(wrapped !== stubs.fetch, 'wrapped fetch is the stub itself — nothing was intercepted');
  return wrapped;
}

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
// THE REAL STACK: touch page helper ON TOP OF the real js/auth.js interceptor.
//
// Everything above stubs `fetch` directly and so cannot see the wrapper at all.
// These run the composed stack the panel actually executes.
// ═══════════════════════════════════════════════════════════════════════════
const TOUCH_HTML = fs.readFileSync(path.join(__dirname, 'touch', 'voice.html'), 'utf8');
const VOICE_PATHS = ['/api/voice/livekit-token', '/api/voice/livekit-audio', '/api/voice/livekit-cancel'];

// Drops one path from the interceptor's allow-list — the pre-fix wrapper.
const withoutAllowListEntry = (p) => (src) => {
  const entry = `        '${p}',\n`;
  assert(src.includes(entry), `js/auth.js: CALLER_HANDLES_401 entry for ${p} not found`);
  return src.replace(entry, '');
};

// ── The page loads the wrapper at all — the premise of this whole section ──
await check('touch/voice.html loads /js/auth.js (so its fetch IS wrapped)', () => {
  assert(/<script[^>]+src="\/js\/auth\.js/.test(TOUCH_HTML),
    'touch/voice.html no longer loads /js/auth.js — the wrapped section is testing a stack that does not exist');
  const desktop = fs.readFileSync(path.join(__dirname, 'voice.html'), 'utf8');
  assert(!/<script[^>]+src="\/js\/auth\.js/.test(desktop),
    'dist/voice.html now loads /js/auth.js — it needs the wrapped scenarios too');
});

// ── Every voice path the page calls is declared caller-owned ───────────────
await check('every LiveKit path touch/voice.html calls is in CALLER_HANDLES_401', () => {
  const authSrc = fs.readFileSync(AUTH_JS, 'utf8');
  const listStart = authSrc.indexOf('const CALLER_HANDLES_401');
  assert(listStart > 0, 'js/auth.js: CALLER_HANDLES_401 not found');
  const list = authSrc.slice(listStart, authSrc.indexOf(']', listStart));
  for (const p of VOICE_PATHS) {
    if (!TOUCH_HTML.includes(`'${p}'`)) continue;   // path not used by the page
    assert(list.includes(`'${p}'`),
      `${p} is fetched by touch/voice.html but not in CALLER_HANDLES_401 — its 401 will reject before the page's retry`);
  }
});

// ── THE REGRESSION: expired guest session, through the real wrapper ────────
async function expiredGuestThroughWrapper(mutate) {
  const stubs = makeStubs({ session_id: 'stale-guest', role: 'guest' }, (url, init) => {
    if (url === '/api/auth/guest') return ok({ session_id: 'new-guest', role: 'guest' });
    return init.headers['X-Session-ID'] === 'new-guest' ? ok({ ok: true }) : unauthorized();
  });
  const wrapped = loadAuthInterceptor(stubs, mutate);
  const h = loadHelpers(TOUCH_HTML, stubs, wrapped);
  const resp = await h.voiceFetchWithSession('/api/voice/livekit-audio',
    (sid) => ({ headers: { 'X-Session-ID': sid } }));
  return { resp, stubs };
}

await check('touch (wrapped): an expired guest session still retries once and succeeds', async () => {
  const { resp, stubs } = await expiredGuestThroughWrapper(null);
  assert.strictEqual(resp.status, 200, 'the wrapped stack must complete the turn');
  assert.deepStrictEqual(
    stubs.calls.map((c) => c.url),
    ['/api/voice/livekit-audio', '/api/auth/guest', '/api/voice/livekit-audio'],
    'the wrapper must pass the 401 through so the page can re-provision'
  );
  assert.strictEqual(stubs.calls[2].init.headers['X-Session-ID'], 'new-guest');
});

// ── NEGATIVE CONTROL: drop the allow-list entry, the retry must die ────────
// Without this the check above would pass against ANY wrapper, including one
// that swallows the 401 — a green light that proves nothing. Removing the
// single line under test must reproduce the reported bug exactly.
await check('touch (wrapped) NEGATIVE CONTROL: removing the allow-list entry breaks the retry', async () => {
  let threw = null;
  try {
    await expiredGuestThroughWrapper(withoutAllowListEntry('/api/voice/livekit-audio'));
  } catch (err) {
    threw = err;
  }
  assert(threw, 'the pre-fix wrapper must reject before the retry — this control is not controlling');
  assert(/Unauthorized/i.test(String(threw && threw.message)),
    `expected the wrapper's Unauthorized rejection, got: ${threw && threw.message}`);
});

// ── A signed-in user is not downgraded by the pass-through ────────────────
await check('touch (wrapped): a signed-in 401 is passed through without clearing the session', async () => {
  const stubs = makeStubs({ session_id: 'sess-jason', role: 'admin', user_id: 'jason' }, () => unauthorized());
  const wrapped = loadAuthInterceptor(stubs, null);
  const h = loadHelpers(TOUCH_HTML, stubs, wrapped);
  const resp = await h.voiceFetchWithSession('/api/voice/livekit-audio',
    (sid) => ({ headers: { 'X-Session-ID': sid } }));
  assert.strictEqual(resp.status, 401, 'the caller must receive the Response, not a rejection');
  assert.strictEqual(stubs.calls.length, 1, 'no re-provision and no wrapper retry for a signed-in user');
  assert(stubs.store.has('zoe_session'), 'pass-through must not clear a signed-in session');
});

// ── A healthy turn is unchanged by the wrapper ────────────────────────────
await check('touch (wrapped): a valid guest session still sends exactly one request', async () => {
  const stubs = makeStubs({ session_id: 'good-guest', role: 'guest' }, () => ok({ ok: true }));
  const wrapped = loadAuthInterceptor(stubs, null);
  const h = loadHelpers(TOUCH_HTML, stubs, wrapped);
  await h.voiceFetchWithSession('/api/voice/livekit-cancel', (sid) => ({ headers: { 'X-Session-ID': sid } }));
  assert.strictEqual(stubs.calls.length, 1);
  assert.strictEqual(stubs.calls[0].init.headers['X-Session-ID'], 'good-guest');
});

// ── The wrapper's normal 401 policy is untouched for everything else ──────
await check('touch (wrapped): a non-voice path still rejects on 401 (policy not widened)', async () => {
  const stubs = makeStubs({ session_id: 'sess-jason', role: 'admin' }, () => unauthorized());
  const wrapped = loadAuthInterceptor(stubs, null);
  let threw = null;
  try { await wrapped('/api/chat/confirm', { method: 'POST' }); } catch (err) { threw = err; }
  assert(threw && /Unauthorized/i.test(threw.message),
    'the pass-through must apply ONLY to the listed voice paths');
});

}

main().then(() => {
  console.log(`${checks} checks passed`);
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
