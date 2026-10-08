#!/usr/bin/env node
/**
 * Desktop wave-3 harness (UI deep review 2026-10-04): no browser, no network.
 *
 *  1. desktopAuthGate (js/auth.js) — a desktop data page with no MEMBER session
 *     redirects to /index.html synchronously; public pages, touch pages and
 *     member sessions do not. A guest session is NOT signed in on desktop.
 *  2. createPushHub (js/auth.js) — ONE /ws/push socket per page, only with a
 *     member session, session_id in the URL, capped backoff, keepalive ping.
 *     notifications-panel.js, zoe-orb.js and chat.html open no socket of their own.
 *  3. safeRedirectTarget (auth.html) — ?redirect= cannot leave the origin or
 *     switch scheme (javascript:, //host, backslash tricks).
 *  4. zoePostLoginDestination (index.html) — consumes zoe_redirect_after_login,
 *     same-origin paths only, never /touch/.
 *  5. buildSuggestionToast (js/zoe-orb.js) — notification title/message are
 *     DOM text, buttons carry no on* attribute (stored XSS via reminder titles).
 *  6. renderPeopleSearchResults (people.html) — every field escaped, id as an
 *     escaped JSON literal (stored XSS via contact names).
 *  7. push-notifications.js — a 403 VAPID key resolves null (no TypeError),
 *     `subscription` is declared, no Bearer header, guests never auto-subscribe.
 *
 * Run: node services/zoe-ui/dist/test_desktop_wave3.js
 * CI:  services/zoe-data/tests/test_desktop_wave3_harness.py
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const read = (p) => fs.readFileSync(path.join(__dirname, p), 'utf8');
const authSrc = read('js/auth.js');
const orbSrc = read('js/zoe-orb.js');
const panelSrc = read('js/notifications-panel.js');
const pushSrc = read('js/push-notifications.js');
const authHtml = read('auth.html');
const indexHtml = read('index.html');
const peopleHtml = read('people.html');
const chatHtml = read('chat.html');

function extractFunction(src, name) {
  const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(');
  const m = re.exec(src);
  assert(m, 'missing function ' + name);
  let i = src.indexOf('{', m.index), depth = 0;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return src.slice(m.index, i + 1); }
    else if (ch === '`' || ch === '"' || ch === "'") { // skip string/template literals
      const q = ch; i++;
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++; }
    } else if (ch === '/' && src[i + 1] === '/') { i = src.indexOf('\n', i); }
    else if (ch === '/' && src[i + 1] === '*') { i = src.indexOf('*/', i) + 1; }
    else if (ch === '/') { // a regex literal follows an operator/opening token; skip it (incl. [...] classes)
      let j = i - 1; while (j >= 0 && /\s/.test(src[j])) j--;
      if (j < 0 || /[(,=:\[!&|?{};+\-*%<>~^]/.test(src[j]) || /\breturn$/.test(src.slice(Math.max(0, j - 6), j + 1))) {
        let k = i + 1, cls = false;
        while (k < src.length) { const c = src[k]; if (c === '\\') { k += 2; continue; } if (cls) { if (c === ']') cls = false; } else if (c === '[') cls = true; else if (c === '/') break; else if (c === '\n') break; k++; }
        i = k;
      }
    }
  }
  throw new Error('unterminated ' + name);
}
function extractConst(src, name) {
  const m = new RegExp('const ' + name + ' = new Set\\(\\[[\\s\\S]*?\\]\\);').exec(src);
  assert(m, 'missing const ' + name);
  return m[0];
}
let passed = 0;
function check(name, fn) { fn(); passed++; console.log('  ok  ' + name); }

// ── 1. desktopAuthGate ───────────────────────────────────────────────────────
{
  const code = extractConst(authSrc, 'DESKTOP_PUBLIC_PATHS') + '\n' +
    extractFunction(authSrc, 'isExpiredSessionObject') + '\n' +
    extractFunction(authSrc, 'isGuestSessionObject') + '\n' +
    extractFunction(authSrc, 'desktopAuthGate') + '\n; ({ desktopAuthGate })';
  const { desktopAuthGate } = vm.runInNewContext(code, { Date, Number, String, Set });
  const member = { session_id: 'abc', user_id: 'jason', role: 'admin' };
  const guest = { session_id: 'g', user_id: 'guest', role: 'guest' };
  const expired = { session_id: 'x', user_id: 'jason', expires_at: '2000-01-01T00:00:00Z' };
  check('gate: data page + no session → /index.html', () => assert.strictEqual(desktopAuthGate('/dashboard.html', null), '/index.html'));
  check('gate: data page + GUEST session → /index.html (desktop is members-only)', () => assert.strictEqual(desktopAuthGate('/chat.html', guest), '/index.html'));
  check('gate: data page + expired member session → /index.html', () => assert.strictEqual(desktopAuthGate('/calendar.html', expired), '/index.html'));
  check('gate: data page + member session → stays', () => assert.strictEqual(desktopAuthGate('/people.html', member), null));
  for (const p of ['/', '/index.html', '/auth.html', '/404.html', '/offline.html', '/jukebox.html', '/setup-music.html', '/setup-device.html'])
    check('gate: public page ' + p + ' never redirects', () => assert.strictEqual(desktopAuthGate(p, null), null));
  check('gate: the estate / touch pages are not gated here', () => assert.strictEqual(desktopAuthGate('/touch/home.html', null), null));
  check('auth.js runs the gate synchronously after the interceptor (before any page init)', () => {
    const i = authSrc.indexOf('setupFetchInterceptor();\n');
    const g = authSrc.indexOf('desktopAuthGate(window.location.pathname, getSessionObject())');
    assert(i > 0 && g > i && g < authSrc.indexOf('window.zoeAuthReady = new Promise'));
    assert(/window\.location\.replace\(gateTarget\)/.test(authSrc));
    assert(/sessionStorage\.setItem\('zoe_redirect_after_login'/.test(authSrc.slice(i, g + 600)));
  });
}

// ── 2. createPushHub ─────────────────────────────────────────────────────────
{
  const code = extractFunction(authSrc, 'createPushHub') + '\n; ({ createPushHub })';
  const { createPushHub } = vm.runInNewContext(code, { JSON, Set, Math, String, encodeURIComponent });
  function harness(sid) {
    const sockets = []; const timers = []; let now = 0;
    class FakeWS { constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
      send(d) { this.sent.push(d); } close() { if (this.onclose) this.onclose(); } }
    const setT = (fn, ms) => { const t = { at: now + ms, fn, kind: 't' }; timers.push(t); return t; };
    const setI = (fn, ms) => { const t = { at: now + ms, fn, kind: 'i', ms }; timers.push(t); return t; };
    const clear = (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); };
    const advance = (ms) => { const end = now + ms; for (;;) { const due = timers.filter(t => t.at <= end).sort((a, b) => a.at - b.at)[0]; if (!due) break; now = due.at; if (due.kind === 'i') due.at = now + due.ms; else clear(due); due.fn(); } now = end; };
    const hub = createPushHub({ WebSocket: FakeWS, getSessionId: () => sid, location: { protocol: 'https:', host: 'zoe.test' },
      setTimeout: setT, clearTimeout: clear, setInterval: setI, clearInterval: clear });
    return { hub, sockets, advance };
  }
  check('hub: no member session → NO socket, no retries', () => {
    const h = harness(''); h.hub.subscribe(() => {}); h.advance(120000);
    assert.strictEqual(h.sockets.length, 0);
  });
  check('hub: member session → ONE socket with session_id, shared by every subscriber', () => {
    const h = harness('S1'); const got = [];
    h.hub.subscribe(m => got.push('a:' + m.type)); h.hub.subscribe(m => got.push('b:' + m.type));
    assert.strictEqual(h.sockets.length, 1);
    assert.strictEqual(h.sockets[0].url, 'wss://zoe.test/ws/push?channel=all&session_id=S1');
    h.sockets[0].onopen(); h.sockets[0].onmessage({ data: JSON.stringify({ type: 'notification_created' }) });
    assert.deepStrictEqual(got, ['a:notification_created', 'b:notification_created']);
  });
  check('hub: keepalive ping every 30 s (server drops a silent socket at 120 s)', () => {
    const h = harness('S1'); h.hub.subscribe(() => {}); h.sockets[0].onopen(); h.advance(95000);
    assert.strictEqual(h.sockets[0].sent.filter(d => JSON.parse(d).type === 'ping').length, 3);
  });
  check('hub: close → reconnect with doubling backoff capped at 30 s', () => {
    const h = harness('S1'); h.hub.subscribe(() => {});
    h.sockets[0].onclose(); h.advance(1999); assert.strictEqual(h.sockets.length, 1);
    h.advance(1); assert.strictEqual(h.sockets.length, 2);                       // 2 s
    h.sockets[1].onclose(); h.advance(4000); assert.strictEqual(h.sockets.length, 3);   // 4 s
    for (let i = 0; i < 6; i++) { h.sockets[h.sockets.length - 1].onclose(); h.advance(30000); }
    assert.strictEqual(h.hub._state().retryMs, 30000);
    assert(h.sockets.length <= 10, 'bounded');
  });
  check('notifications-panel.js, zoe-orb.js and chat.html open no /ws/push socket of their own', () => {
    for (const [name, src] of [['notifications-panel.js', panelSrc], ['zoe-orb.js', orbSrc], ['chat.html', chatHtml]]) {
      assert(!/new WebSocket\(/.test(src), name + ' still constructs a WebSocket');
      assert(src.includes('window.zoePush'), name + ' does not subscribe through zoePush');
    }
  });
}

// ── 3. safeRedirectTarget (auth.html) ────────────────────────────────────────
{
  const { safeRedirectTarget } = vm.runInNewContext(extractFunction(authHtml, 'safeRedirectTarget') + '\n; ({ safeRedirectTarget })', { URL, String });
  const O = 'https://zoe.test';
  check('redirect: javascript: is refused', () => assert.strictEqual(safeRedirectTarget('javascript:alert(1)//.html', O), '/dashboard.html'));
  check('redirect: //evil.com is refused', () => assert.strictEqual(safeRedirectTarget('//evil.com/x.html', O), '/dashboard.html'));
  check('redirect: backslash trick is refused', () => assert.strictEqual(safeRedirectTarget('/\\evil.com', O), '/dashboard.html'));
  check('redirect: absolute foreign URL is refused', () => assert.strictEqual(safeRedirectTarget('https://evil.com/a.html', O), '/dashboard.html'));
  check('redirect: same-origin path survives with query', () => assert.strictEqual(safeRedirectTarget('/touch/pair.html?code=1', O), '/touch/pair.html?code=1'));
  check('redirect: default is the dashboard', () => assert.strictEqual(safeRedirectTarget(null, O), '/dashboard.html'));
  check('auth.html redirectToApp uses the guard', () => assert(/window\.location\.href = safeRedirectTarget\(urlParams\.get\('redirect'\), window\.location\.origin\)/.test(authHtml)));
}

// ── 4. zoePostLoginDestination (index.html) ──────────────────────────────────
{
  const code = extractFunction(indexHtml, 'zoePostLoginDestination') + '\n; ({ zoePostLoginDestination })';
  const run = (stored) => { const store = new Map(stored == null ? [] : [['zoe_redirect_after_login', stored]]);
    const ctx = { sessionStorage: { getItem: k => store.get(k) ?? null, removeItem: k => store.delete(k) }, String };
    return { dest: vm.runInNewContext(code, ctx).zoePostLoginDestination(), left: store.has('zoe_redirect_after_login') }; };
  check('login: no stored page → dashboard', () => assert.strictEqual(run(null).dest, '/dashboard.html'));
  check('login: stored desktop page is honoured and consumed', () => { const r = run('/calendar.html?x=1'); assert.strictEqual(r.dest, '/calendar.html?x=1'); assert(!r.left); });
  check('login: a touch page is never a desktop destination', () => assert.strictEqual(run('/touch/home.html').dest, '/dashboard.html'));
  check('login: //host and backslashes are refused', () => { assert.strictEqual(run('//evil.com').dest, '/dashboard.html'); assert.strictEqual(run('/\\evil').dest, '/dashboard.html'); });
  check('index.html uses it at both login sites', () => assert.strictEqual((indexHtml.match(/window\.location\.href = zoePostLoginDestination\(\);/g) || []).length, 2));
}

// ── DOM shim: text is escaped on serialisation, innerHTML is emitted RAW ─────
function makeDoc() {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  function el(tag) {
    const node = { tag, children: [], attrs: {}, listeners: [], style: {}, className: '', nodeType: 1, _raw: null,
      appendChild(c) { this.children.push(c); return c; },
      addEventListener(t, fn) { this.listeners.push(t); },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      get textContent() { return this.children.map(c => c.nodeType === 3 ? c.text : c.textContent).join(''); },
      set textContent(v) { this.children = [{ nodeType: 3, text: String(v) }]; },
      set innerHTML(v) { this._raw = String(v); this.children = []; },
      get outerHTML() { if (this._raw != null) return '<' + this.tag + '>' + this._raw + '</' + this.tag + '>';
        const a = Object.entries(this.attrs).map(([k, v]) => ' ' + k + '="' + esc(v) + '"').join('');
        return '<' + this.tag + a + '>' + this.children.map(c => c.nodeType === 3 ? esc(c.text) : c.outerHTML).join('') + '</' + this.tag + '>'; },
    };
    return node;
  }
  return { createElement: el, createTextNode: (t) => ({ nodeType: 3, text: String(t) }) };
}

// ── 5. buildSuggestionToast (zoe-orb.js) ─────────────────────────────────────
{
  const doc = makeDoc();
  const calls = [];
  const ctx = { document: doc, String, suggestionAction: (id, a) => calls.push([id, a]), handleSuggestionWithChat: () => calls.push(['chat']), window: {} };
  const { buildSuggestionToast } = vm.runInNewContext(extractFunction(orbSrc, 'buildSuggestionToast') + '\n; ({ buildSuggestionToast })', ctx);
  const hostile = { id: 'n1', title: '<img src=x onerror=alert(1)>', message: '"><svg onload=alert(2)>' };
  const html = buildSuggestionToast(hostile, doc).outerHTML;
  check('orb toast: hostile title/message are escaped text, never markup', () => {
    assert(!html.includes('<img'), html); assert(!html.includes('<svg'), html);
    assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  });
  check('orb toast: buttons carry listeners, not on* attributes', () => {
    assert(!/onclick=/.test(html));
    assert.strictEqual((html.match(/<button/g) || []).length, 4);
  });
  check('orb toast: zoe-orb.js no longer builds the toast as an HTML string', () => {
    assert(!/const html = `[\s\S]*\$\{n\.message\}/.test(orbSrc));
    assert(!/el\.innerHTML = text/.test(orbSrc));
    assert(!/suggestionDiv\.innerHTML/.test(orbSrc));
  });
}

// ── 6. renderPeopleSearchResults (people.html) ───────────────────────────────
{
  const code = extractFunction(peopleHtml, 'escapeHtml') + '\n' + extractFunction(peopleHtml, 'renderPeopleSearchResults') + '\n; ({ renderPeopleSearchResults })';
  const { renderPeopleSearchResults } = vm.runInNewContext(code, { String, JSON, document: { createElement: () => ({ set textContent(v) { this._t = v; }, get innerHTML() { return String(this._t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }) } });
  const out = renderPeopleSearchResults([{ id: "1' onmouseover='alert(1)", name: '"><svg onload=alert(1)>', category: '<b>x</b>' }]);
  check('people search: hostile name/category/id are escaped', () => {
    assert(!out.includes('<svg'), out); assert(!out.includes('<b>x</b>'), out);
    assert(!/onclick="selectPerson\('1' onmouseover/.test(out), out);
    assert(/selectPerson\(&quot;/.test(out) || /selectPerson\(&#/.test(out), 'id must be an escaped JSON literal: ' + out);
  });
  check('people.html searchPeople routes through the escaped renderer', () => assert(/sidebar\.innerHTML = renderPeopleSearchResults\(filtered\)/.test(peopleHtml)));
}

// ── 7. push-notifications.js ─────────────────────────────────────────────────
{
  check('push: a 403 VAPID response resolves null instead of throwing on undefined', async () => {
    const code = "const API_BASE='/api/push'; let vapidPublicKey=null;\n" + extractFunction(pushSrc, 'getVapidPublicKey') + '\n; ({ getVapidPublicKey })';
    const ctx = { fetch: async () => ({ ok: false, status: 403, json: async () => ({ detail: 'no' }) }), console: { warn() {} } };
    const r = await vm.runInNewContext(code, ctx).getVapidPublicKey();
    assert.strictEqual(r, null);
  });
  check('push: `subscription` is declared (was a strict-mode ReferenceError)', () => {
    const body = extractFunction(pushSrc, 'subscribeToPush');
    assert(/const subscription = await registration\.pushManager\.subscribe\(/.test(body));
    assert(/if \(!publicKey\) throw/.test(body));
  });
  check('push: no Bearer access_token anywhere; identity is X-Session-ID via the interceptor', () => {
    assert(!/'Authorization'/.test(pushSrc)); assert(!/access_token=/.test(pushSrc)); assert(!/getItem\('access_token'\)/.test(pushSrc));
  });
  check('push: guests and logged-out visitors never auto-subscribe or get prompted', () => {
    const { shouldAutoSubscribe } = vm.runInNewContext(extractFunction(pushSrc, 'shouldAutoSubscribe') + '\n; ({ shouldAutoSubscribe })', {});
    assert.strictEqual(shouldAutoSubscribe(null, { permission: 'default' }), false);
    assert.strictEqual(shouldAutoSubscribe({ isAuthenticatedNonGuestSession: () => false }, { permission: 'default' }), false);
    assert.strictEqual(shouldAutoSubscribe({ isAuthenticatedNonGuestSession: () => true }, { permission: 'denied' }), false);
    assert.strictEqual(shouldAutoSubscribe({ isAuthenticatedNonGuestSession: () => true }, { permission: 'default' }), true);
    assert(/if \(!shouldAutoSubscribe\(window\.zoeAuth, window\.Notification\)\)/.test(extractFunction(pushSrc, 'autoSubscribe')));
  });
  check('push: subscription id is never stored as the string "undefined"', () => assert(/data\.subscription_id != null/.test(pushSrc)));
}

// ── every members-only desktop page loads js/auth.js (the gate + the interceptor) ──
// 2026-10-08: music.html and settings.html never loaded it — ungated, and music.html's
// notifications panel 403'd because nothing attached the session to its fetches.
check('gate: every desktop page outside DESKTOP_PUBLIC_PATHS loads js/auth.js', () => {
  const PUBLIC = vm.runInNewContext(extractConst(authSrc, 'DESKTOP_PUBLIC_PATHS') + '\n; DESKTOP_PUBLIC_PATHS', {});
  const pages = fs.readdirSync(__dirname).filter(f => f.endsWith('.html'));
  const missing = pages.filter(f => !PUBLIC.has('/' + f) && !/<script src="\/?js\/auth\.js[^"]*"/.test(read(f)));
  assert.deepStrictEqual(missing, [], 'members-only pages without js/auth.js: ' + missing.join(', '));
  assert(pages.includes('music.html') && pages.includes('settings.html'));
});

// ── websocket-sync: per-resource sockets carry the session (behavioural) ──────────
// The server closes 1008 before accept without ?session_id= (a browser cannot set
// X-Session-ID on a handshake). Run the real class against a fake WebSocket.
function wsSandbox({ zoeAuthSession, storedSession }) {
  const made = []; const warned = [];
  class FakeWS { constructor(url) { this.url = url; this.readyState = 0; made.push(url); } close() {} send() {} }
  FakeWS.OPEN = 1;
  const window = { location: { protocol: 'https:', host: 'zoe.local' }, addEventListener() {}, WebSocket: FakeWS };
  if (zoeAuthSession !== undefined) window.zoeAuth = { getSession: () => zoeAuthSession };
  const ctx = { window, self: window, document: { hidden: false, addEventListener() {} },
    localStorage: { getItem: () => storedSession === undefined ? null : JSON.stringify({ session_id: storedSession }) },
    WebSocket: FakeWS, console: { log() {}, warn: (m) => warned.push(m), error() {} },
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {}, Number, JSON, String, Math, URLSearchParams };
  const { ZoeWebSocketSync } = vm.runInNewContext(read('js/websocket-sync.js') + '\n; ({ ZoeWebSocketSync })', ctx);
  return { ZoeWebSocketSync, made, warned };
}
check('ws: connect() appends the zoeAuth session, URL-encoded', () => {
  const { ZoeWebSocketSync, made } = wsSandbox({ zoeAuthSession: 'sid with/slash' });
  new ZoeWebSocketSync('/api/lists/ws', 'jason').connect();
  assert.deepStrictEqual(made, ['wss://zoe.local/api/lists/ws/jason?session_id=sid%20with%2Fslash']);
});
check('ws: without zoeAuth the stored zoe_session is used', () => {
  const { ZoeWebSocketSync, made } = wsSandbox({ storedSession: 'abc123' });
  new ZoeWebSocketSync('/api/calendar/ws', 'jason').connect();
  assert.deepStrictEqual(made, ['wss://zoe.local/api/calendar/ws/jason?session_id=abc123']);
});
check('ws: disconnect() is a STOP — the close it triggers schedules no reconnect; connect() re-arms', () => {
  const { ZoeWebSocketSync, made } = wsSandbox({ zoeAuthSession: 'sid' });
  const sock = new ZoeWebSocketSync('/api/lists/ws', 'jason'); sock.connect();
  assert.strictEqual(made.length, 1);
  const ws = sock.ws; sock.disconnect();
  ws.onclose && ws.onclose();            // the browser fires close after a deliberate close()
  assert.strictEqual(made.length, 1, 'no new socket after disconnect');
  assert(!sock.reconnectTimeout, 'no reconnect timer armed');
  sock.connect(); assert.strictEqual(made.length, 2, 'a deliberate connect() re-arms');
});
check('ws: no session at all → no query, and a warning (the server will refuse it)', () => {
  const { ZoeWebSocketSync, made, warned } = wsSandbox({});
  new ZoeWebSocketSync('/api/lists/ws', 'jason').connect();
  assert.deepStrictEqual(made, ['wss://zoe.local/api/lists/ws/jason']);
  assert(warned.some(m => /no session/.test(m)));
});

// ── music.html: the MA socket is retried only while the BACKEND says MA is up ──────
// Runs the page's real refreshMAStatus/onMAUp/connectWS/onWSClose/watchForMA against
// fake fetch/WebSocket/timers. statusReplies are consumed one per /api/music/status call.
const musicHtml = read('music.html');
function musicSandbox(statusReplies, S = {}) {
  const calls = { ws: 0, setup: [], players: 0, offline: [], reconnectIn: [], watcher: null, cleared: 0 };
  const ctx = {
    S: Object.assign({ ws: null, wsRetries: 0, wsRetryDelay: 2000, wsRetryTimer: null, maAvailable: false }, S),
    MA_WS: 'wss://zoe.local/modules/music-assistant/ws', zoeHdrs: () => ({}),
    fetch: async () => { const r = statusReplies.shift() || { ok: false }; return { ok: !!r.ok, json: async () => r.json }; },
    WebSocket: class { constructor() { calls.ws++; } close() {} },
    showOffline: (k) => calls.offline.push(k), showSetup: (ids) => calls.setup.push([...ids]), hideSetup: () => { calls.hideSetup = (calls.hideSetup || 0) + 1; },
    fetchPlayersFromBackend: async () => { calls.players++; }, onWSOpen() {}, onWSMsg() {},
    setTimeout: (fn, ms) => { calls.reconnectIn.push(ms); return 1; }, clearTimeout() {},
    setInterval: (fn, ms) => { calls.watcher = fn; calls.watcherMs = ms; return 7; }, clearInterval: () => { calls.cleared++; },
    Set, Math, JSON, console: { log() {}, warn() {} },
  };
  const code = ['refreshMAStatus', 'goOffline', 'onMAUp', 'connectWS', 'onWSClose', 'watchForMA'].map(n => extractFunction(musicHtml, n)).join('\n')
    + '\nlet _maWatchTimer = null;\n; ({ refreshMAStatus, goOffline, onMAUp, connectWS, onWSClose, watchForMA })';
  return { fns: vm.runInNewContext(code, ctx), calls, S: ctx.S };
}
{
  check('music: a socket close re-asks the backend — MA reaped after load → offline + watcher, NO reconnect', async () => {
    const { fns, calls, S } = musicSandbox([{ ok: true, json: { available: false } }], { maAvailable: true });
    await fns.onWSClose();
    assert.strictEqual(S.maAvailable, false);
    assert.deepStrictEqual(calls.reconnectIn, []);
    assert.deepStrictEqual(calls.offline, ['offline']);
    assert.strictEqual(calls.hideSetup, 1, 'the setup wizard (and its poll) is left before going offline');
    assert(typeof calls.watcher === 'function' && calls.watcherMs === 15000);
  });
  check('music: a socket close while the backend says MA is up → one reconnect with backoff, no watcher', async () => {
    const { fns, calls } = musicSandbox([{ ok: true, json: { available: true, provider_count: 2 } }], { maAvailable: true });
    await fns.onWSClose();
    assert.deepStrictEqual(calls.reconnectIn, [3000]);
    assert.strictEqual(calls.watcher, null); assert.deepStrictEqual(calls.offline, []);
  });
  check('music: backend unreachable on close → offline shown at once, no reconnect, watcher started', async () => {
    const { fns, calls } = musicSandbox([{ ok: false }], { maAvailable: true });
    await fns.onWSClose();
    assert.deepStrictEqual(calls.offline, ['offline']); assert.deepStrictEqual(calls.reconnectIn, []);
    assert(typeof calls.watcher === 'function');
  });
  check('music: a watcher poll that definitively says "down" shows the offline state (an unreachable backend keeps watching silently)', async () => {
    const { fns, calls } = musicSandbox([{ ok: false }, { ok: true, json: { available: false } }, { ok: true, json: { available: false } }]);
    fns.watchForMA();
    await calls.watcher(); assert.deepStrictEqual(calls.offline, []);
    await calls.watcher(); await calls.watcher();
    assert.deepStrictEqual(calls.offline, ['offline', 'offline']); assert.strictEqual(calls.hideSetup, 2); assert.strictEqual(calls.cleared, 0); assert.strictEqual(calls.ws, 0);
  });
  check('music: watcher recovery with NO providers takes the setup branch (not an empty player)', async () => {
    const { fns, calls } = musicSandbox([{ ok: true, json: { available: true, provider_count: 0, providers: [{ domain: 'YTMusic' }] } }]);
    fns.watchForMA(); await calls.watcher();
    assert.deepStrictEqual(calls.setup, [['ytmusic']]); assert.strictEqual(calls.players, 0);
    assert.strictEqual(calls.ws, 1); assert.strictEqual(calls.cleared, 1);
  });
  check('music: watcher recovery with providers loads players then opens the socket', async () => {
    const { fns, calls } = musicSandbox([{ ok: true, json: { available: true, provider_count: 1 } }]);
    fns.watchForMA(); await calls.watcher();
    assert.strictEqual(calls.players, 1); assert.strictEqual(calls.ws, 1); assert.deepStrictEqual(calls.setup, []);
  });
  check('music: watcher tick while MA is still down opens nothing and keeps watching', async () => {
    const { fns, calls } = musicSandbox([{ ok: true, json: { available: false } }, { ok: false }]);
    fns.watchForMA(); await calls.watcher(); await calls.watcher();
    assert.strictEqual(calls.ws, 0); assert.strictEqual(calls.players, 0); assert.strictEqual(calls.cleared, 0);
    fns.watchForMA(); // idempotent: a second call does not start a second interval
  });
}

// ── 2026-10-09: dead API paths are gone; music transport uses the real HA control route ──
check('no desktop page or script calls a route that never existed (warm-up, tools, media upload, HA service, music similar)', () => {
  const dead = ['/api/chat/warm', '/api/tools/call', '/api/media/upload', '/api/ha/service', '/api/music/similar'];
  const files = ['music.html', 'settings.html', 'journal.html', 'js/zoe-orb.js', 'js/chat-sessions.js', 'js/widgets/core/journal.js', 'js/widgets/music/library.js'];
  for (const f of files) { const src = read(f); for (const d of dead) assert(!src.includes("'" + d) && !src.includes('`' + d), f + ' still calls ' + d); }
  for (const gone of ['js/widgets/music/suggestions.js', 'js/widgets/music/playlists.js', 'js/widgets/music/queue.js', 'js/widgets/music/search.js', 'js/voice/voice-controller.js'])
    assert(!fs.existsSync(path.join(__dirname, gone)), gone + ' should be deleted (no page loads it)');
});
check('music: transport uses Music Assistant routes with the MA player_id, never a fabricated HA entity', () => {
  const src = read('music.html');
  assert(!/haService|activeEntityId|\/api\/ha\/control|media_player\.\$\{/.test(src));
  assert(/function maControl\(action, value\)[\s\S]{0,300}maPost\('\/api\/music\/control', body\)/.test(src));
  assert(/maPost\('\/api\/music\/seek', \{ position_seconds: seconds, player_id: S\.activeId \}\)/.test(src));
  assert(/maPost\('\/api\/music\/queue\/clear', \{ queue_id: S\.activeId \}\)/.test(src));
  for (const call of ["maControl('pause')", "maControl('play')", "maControl('previous')", "maControl('next')", "maControl('volume_set', Math.round(val))", "maControl('shuffle_set', !!S.shuffle)", "maControl('repeat_set', S.repeat ? 'all' : 'off')", "maSeek(Math.round(S.currentTime))", "maQueueClear()"]) assert(src.includes(call), 'missing ' + call);
});
check('journal: no photo picker is offered while there is no upload backend (page + dashboard widget)', () => {
  assert(!/class="filepond"|FilePond\.|\/lib\/filepond/.test(read('journal.html')), 'journal.html must carry no FilePond wiring while there is no upload backend');
  assert(!/journalPhoto/.test(read('js/widgets/core/journal.js')));
});
check('updates: the page header wraps at phone width', () => assert(/@media \(max-width: 600px\) \{\s*\.page-header \{ flex-wrap: wrap; \}/.test(read('updates.html'))));

// ── chat.html: the guest pool is never listed ────────────────────────────────
check('chat: loadSessions refuses to list sessions without a member session, and sends no ?user_id=', () => {
  const body = extractFunction(chatHtml, 'loadSessions');
  assert(/isAuthenticatedNonGuestSession/.test(body));
  assert(/Sign in to see your chats/.test(body));
  assert(!/user_id=\$\{userId\}/.test(body));
  assert(/apiRequest\('\/api\/chat\/sessions\/'\)/.test(body));
});

(async () => {
  // the async check above registers synchronously; give its promise a tick
  await new Promise(r => setTimeout(r, 50));
  console.log('desktop wave 3: ' + passed + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
