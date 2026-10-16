#!/usr/bin/env node
/**
 * Panel push socket self-heals — no browser, no network (2026-10-14, zoe-touch-pi).
 *
 * The live defect: after a Jetson reboot + deploy restarts, GET /api/panels/zoe-touch-pi/push-status
 * answered {"subscribers":0,"last_connect_at":null,...} for HOURS and every voice turn logged
 * "push channel has ZERO subscribers". Twenty seconds of the page console showed no reconnect
 * attempt and no error: the loop had no timer left. A manual reload fixed it at once.
 *
 * Root cause (js/touch-ui-executor.js): the page-level `beforeunload` listener set
 * `state.unloading = true` and cleared the poll, sync, watchdog and push timers. beforeunload is a
 * request to leave, not a fact — a cancelled / handed-off navigation, or a back/forward-cache
 * restore, leaves the page alive with the flag stuck. Every guard that could revive the panel
 * (schedulePushReconnect, ensurePushSocket, recoverPanelSession, panelWatchdogTick) begins with
 * `if (state.unloading) return`, so nothing ever reconnected again, silently, for guest and member
 * alike. A second hole: an OPEN socket that stopped answering (readyState stays 1) was invisible to
 * every watchdog.
 *
 * This runs the REAL js/touch-ui-executor.js (whole file, real init()) — and, for the shared
 * channel, the real js/websocket-sync.js initPush() — against a fake clock, fake sockets and a fake
 * server that can drop, refuse, hang or go silent:
 *   A. server drops the socket, refuses for an hour, comes back: reconnect within 31 s, never giving
 *      up — guest and member sessions, fallback socket and shared channel (even with
 *      ZOE_WS_MAX_RECONNECT_ATTEMPTS set).
 *   B. a CANCELLED navigation (beforeunload, page stays) must not kill the panel: poll/sync/watchdog
 *      resume and the push socket reconnects after the server comes back; a bfcache restore
 *      (pageshow persisted) resumes at once.
 *   C. an OPEN-but-silent socket (no pongs) is replaced within the heartbeat window.
 *   D. `online` / page-visible reconnects immediately instead of waiting out a 30 s backoff.
 *
 * EXEC_SRC=<path> / SYNC_SRC=<path> point the test at mutated copies (negative controls).
 * Run: node services/zoe-ui/dist/test_touch_push_reconnect.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const execSrc = fs.readFileSync(process.env.EXEC_SRC || path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');
const syncSrc = fs.readFileSync(process.env.SYNC_SRC || path.join(__dirname, 'js/websocket-sync.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function makeClock() {
  let now = Date.parse('2026-10-14T21:00:00Z');
  let seq = 0;
  const timers = new Map();
  class FakeDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }
  return {
    Date: FakeDate,
    now: () => now,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn, every: 0 }); return id; },
    setInterval(fn, ms) { const id = ++seq; timers.set(id, { at: now + ms, fn, every: ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    clearInterval(id) { timers.delete(id); },
    async advance(ms) {
      const end = now + ms;
      while (now < end) {
        const stepEnd = now + Math.min(1000, end - now);
        for (;;) {
          let next = null;
          for (const [id, t] of timers) if (t.at <= stepEnd && (!next || t.at < next[1].at)) next = [id, t];
          if (!next) break;
          const [id, t] = next;
          now = t.at;
          if (t.every) t.at = now + t.every; else timers.delete(id);
          try { t.fn(); } catch (e) { /* a throwing timer must not stop the fake clock; the page would live on too */ }
        }
        now = stepEnd;
        await flush();
      }
    },
  };
}

// A permissive stand-in for DOM objects the executor touches at load but this test does not care about.
const stub = () => {
  const f = function () { return f; };
  return new Proxy(f, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : k === 'length' ? 0 : (k in t ? t[k] : stub())), apply: () => stub(), set: () => true });
};

/** shared: true = the page also loads websocket-sync.js, so init() uses initPush() (the shared channel). */
function boot({ shared = false, session = 'member', maxReconnect } = {}) {
  const clock = makeClock();
  const store = new Map([['zoe_kiosk', '1']]);
  if (session === 'member') store.set('zoe_session', JSON.stringify({ session_id: 'mem1', user_id: 'u1', username: 'member', role: 'user', expires_at: new Date(clock.now() + 3600e3).toISOString() }));
  const ls = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };

  // fake server
  const server = { mode: 'accept', slowClose: false, blackhole: false, attempts: 0, polls: 0 };
  const sockets = [];
  function FakeWS(url) {
    this.url = url; this.readyState = 0; this.sent = []; this.deaf = false;
    sockets.push(this); server.attempts++;
    clock.setTimeout(() => {
      if (this.readyState !== 0) return;
      if (server.mode === 'accept') {
        this.readyState = 1;
        if (this.onopen) this.onopen({});
        if (this.onmessage) this.onmessage({ data: JSON.stringify({ type: 'connected' }) });
      } else if (server.mode === 'refuse') {
        this.readyState = 3;
        if (this.onerror) this.onerror({});
        if (this.onclose) this.onclose({ code: 1006 });
      } // 'hang': the handshake never completes
    }, 20);
  }
  FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
  FakeWS.prototype.send = function (d) {
    this.sent.push(d);
    if (d === 'ping' && this.readyState === 1 && !this.deaf) {
      clock.setTimeout(() => { if (this.readyState === 1 && this.onmessage) this.onmessage({ data: '{"type":"pong"}' }); }, 10);
    }
  };
  FakeWS.prototype.close = function () {
    if (this.readyState >= 2) return;
    if (this.deaf) { this.readyState = 2; return; }       // a dead peer never completes the close handshake
    if (server.slowClose) {                               // a close handshake that completes later, on demand
      this.readyState = 2;
      this.finishClose = () => { this.readyState = 3; if (this.onclose) this.onclose({ code: 1005 }); };
      return;
    }
    this.readyState = 3;
    clock.setTimeout(() => { if (this.onclose) this.onclose({ code: 1005 }); }, 5);
  };
  server.dropAll = (code = 1012) => { for (const s of sockets) if (s.readyState === 1) { s.readyState = 3; if (s.onclose) s.onclose({ code }); } };
  server.silence = () => { for (const s of sockets) if (s.readyState === 1) s.deaf = true; };   // OPEN but dead
  server.live = () => sockets.filter((s) => s.readyState === 1 && !s.deaf);

  const listeners = { window: {}, document: {} };
  const on = (bag) => (evt, fn) => { (bag[evt] = bag[evt] || []).push(fn); };
  const fire = (bag, evt, ev = {}) => (bag[evt] || []).forEach((fn) => fn(ev));

  const swPort = { postMessage() {} };
  const location = { search: '?panel_id=zoe-touch-pi&kiosk=1', protocol: 'https:', host: 'zoe.local', pathname: '/touch/home.html', reload() { reloads.push(clock.now()); }, assign() {}, href: 'https://zoe.local/touch/home.html' };
  const reloads = [];
  const win = {
    __kiosk: true, location, localStorage: ls, sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    addEventListener: on(listeners.window), removeEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }),
    ZOE_WS_MAX_RECONNECT_ATTEMPTS: maxReconnect,
  };
  win.window = win;
  const doc = {
    readyState: 'complete', hidden: false, addEventListener: on(listeners.document), body: stub(), documentElement: stub(), head: stub(),
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], createElement: () => stub(),
  };
  const fetch = async (url) => {
    if (String(url).startsWith('/api/ui/actions/pending')) server.polls++;
    return { ok: true, status: 200, json: async () => ({ actions: [] }) };
  };
  const ctx = {
    window: win, document: doc, localStorage: ls, sessionStorage: win.sessionStorage, location,
    navigator: { serviceWorker: { ready: Promise.resolve({ active: swPort }), controller: swPort, addEventListener() {} } },
    console: { log() {}, warn() {}, info() {}, error() {} }, WebSocket: FakeWS, fetch,
    URLSearchParams, URL, JSON, Promise, Set, Map, Math, Number, String, Array, Object, Error, RegExp, encodeURIComponent, parseInt, parseFloat, isNaN, Symbol,
    Date: clock.Date, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    CustomEvent: function () {}, Event: function () {}, BroadcastChannel: function () { return stub(); }, requestAnimationFrame: () => 0, getComputedStyle: () => ({}),
  };
  vm.createContext(ctx);
  if (shared) vm.runInContext(syncSrc, ctx, { filename: 'websocket-sync.js' });
  vm.runInContext(execSrc, ctx, { filename: 'touch-ui-executor.js' });
  return { clock, server, sockets, listeners, fire, win, doc, reloads };
}

let n = 0;
const failures = [];
async function run(label, fn) {
  try { await fn(); n++; console.log('  ok  ' + label); }
  catch (e) { failures.push(label); console.log('  FAIL ' + label + '\n       ' + String(e && e.message).split('\n')[0]); }
}

(async () => {
  const variants = [
    { name: 'fallback socket, member session', opts: { shared: false, session: 'member' } },
    { name: 'fallback socket, guest session', opts: { shared: false, session: 'guest' } },
    { name: 'shared push channel, member session', opts: { shared: true, session: 'member' } },
    { name: 'shared push channel, guest session, ZOE_WS_MAX_RECONNECT_ATTEMPTS=3', opts: { shared: true, session: 'guest', maxReconnect: 3 } },
  ];

  // ── A. server drops us, refuses for an hour, comes back ───────────────────
  for (const v of variants) {
    await run(`A. ${v.name}: dropped, an hour of refusals, server back -> reconnected within 31 s, one live socket`, async () => {
      const t = boot(v.opts);
      await t.clock.advance(3000);
      assert.strictEqual(t.server.live().length, 1, 'connected at boot');
      t.server.mode = 'refuse';
      t.server.dropAll(1012);                                   // zoe-data restarted: "service restart"
      await t.clock.advance(3600e3);                            // an hour of refused handshakes
      assert.strictEqual(t.server.live().length, 0);
      assert(t.server.attempts > 100, `still retrying after an hour (${t.server.attempts} attempts): it must never give up`);
      t.server.mode = 'accept';                                 // the server is back
      await t.clock.advance(31000);                             // capped backoff: <= 30 s + the handshake
      assert.strictEqual(t.server.live().length, 1, 'reconnected within 31 s of the server coming back');
      await t.clock.advance(5 * 60e3);
      assert.strictEqual(t.server.live().length, 1, 'exactly one live socket five minutes later (no leak, no flapping)');
    });
  }

  // ── B. a cancelled navigation must not kill the panel ──────────────────────
  for (const v of variants.slice(0, 3)) {
    await run(`B. ${v.name}: a cancelled navigation leaves poll, watchdog and push reconnect alive`, async () => {
      const t = boot(v.opts);
      await t.clock.advance(3000);
      const pollsBefore = t.server.polls;
      t.fire(t.listeners.window, 'beforeunload');               // navigation requested...
      await t.clock.advance(20000);                             // ...and the page is STILL here: it was cancelled / handed off
      assert(t.server.polls > pollsBefore + 3, 'the action poll must run again after a cancelled unload');
      t.server.mode = 'refuse';
      t.server.dropAll(1012);
      await t.clock.advance(120e3);
      t.server.mode = 'accept';
      await t.clock.advance(31000);
      assert.strictEqual(t.server.live().length, 1, 'the push socket reconnects after a cancelled unload');
      t.server.mode = 'refuse'; t.server.dropAll(1006);
      await t.clock.advance(300e3);
      t.server.mode = 'accept';
      await t.clock.advance(31000);
      assert.strictEqual(t.server.live().length, 1, 'and again');
    });
  }
  await run('B. a back/forward-cache restore after beforeunload resumes at once', async () => {
    const t = boot({ shared: false, session: 'member' });
    await t.clock.advance(3000);
    t.fire(t.listeners.window, 'beforeunload');
    t.server.mode = 'refuse'; t.server.dropAll(1006);
    t.fire(t.listeners.window, 'pageshow', { persisted: true });   // back/forward-cache restore
    t.server.mode = 'accept';
    await t.clock.advance(2000);
    assert.strictEqual(t.server.live().length, 1, 'reconnects without waiting for the grace timer');
  });

  // ── C. an OPEN socket that stopped answering is replaced ───────────────────
  for (const v of variants.slice(0, 3)) {
    await run(`C. ${v.name}: an OPEN-but-silent socket is replaced within 150 s`, async () => {
      const t = boot(v.opts);
      await t.clock.advance(3000);
      assert.strictEqual(t.server.live().length, 1);
      t.server.silence();                                       // readyState stays 1; no pongs, no close ever arrives
      await t.clock.advance(150e3);                             // 75 s heartbeat window + one 30 s ping cycle + margin
      assert.strictEqual(t.server.live().length, 1, 'the silent socket was replaced by a live one');
    });
  }

  // ── D. online / visible: reconnect now, not after the backoff ──────────────
  for (const v of variants.slice(0, 3)) {
    await run(`D. ${v.name}: online / page-visible reconnects within 1 s`, async () => {
      const t = boot(v.opts);
      await t.clock.advance(3000);
      t.server.mode = 'refuse'; t.server.dropAll(1006);
      await t.clock.advance(600e3);                             // deep in the 30 s backoff
      t.server.mode = 'accept';
      t.fire(t.listeners.window, 'online');
      await t.clock.advance(1000);
      assert.strictEqual(t.server.live().length, 1, "'online' reconnects within 1 s");
      t.server.mode = 'refuse'; t.server.dropAll(1006);
      await t.clock.advance(600e3);
      t.server.mode = 'accept';
      t.fire(t.listeners.document, 'visibilitychange');
      await t.clock.advance(1000);
      assert.strictEqual(t.server.live().length, 1, 'a visible page reconnects within 1 s');
    });
  }

  // ── E. a superseded CLOSING socket's late close must not kill the new socket's heartbeat ──
  await run('E. shared push channel: a CLOSING socket with a pending onclose, replaced via a nudge, cannot stop the new socket\'s heartbeat', async () => {
    const t = boot({ shared: true, session: 'member' });
    await t.clock.advance(3000);
    const closing = t.win.zoePushWs.ws;
    t.server.slowClose = true;
    closing.close();                                          // CLOSING (readyState 2), onclose still pending
    assert.strictEqual(closing.readyState, 2);
    t.fire(t.listeners.window, 'online');                     // nudge: a new socket replaces it
    await t.clock.advance(1000);
    assert.strictEqual(t.server.live().length, 1, 'the replacement socket is open');
    closing.finishClose();                                    // the old socket's close finally arrives
    await t.clock.advance(2000);
    assert.strictEqual(t.server.live().length, 1, 'the late close did not drop or duplicate the live socket');
    t.server.silence();                                       // the new socket now goes OPEN-but-silent
    await t.clock.advance(150e3);
    assert.strictEqual(t.server.live().length, 1, 'the heartbeat survived the late close: the silent socket was replaced');
  });
  await run('E. executor trusts a shared socket only while it is fresh (pushIsUp staleness)', async () => {
    const t = boot({ shared: true, session: 'member' });
    await t.clock.advance(3000);
    t.server.silence();
    await t.clock.advance(100e3);                             // > 75 s with the OPEN socket silent: replaced by someone
    assert.strictEqual(t.server.live().length, 1);
  });

  if (failures.length) { console.error(failures.length + ' scenario(s) FAILED'); process.exit(1); }
  console.log('push reconnect: ' + n + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
