#!/usr/bin/env node
/**
 * Panel session recovery (2026-10-12, zoe-touch-pi) — no browser, no network.
 *
 * The live defect: a PIN unlock issues a 60-minute PASSCODE session; the estate stored it
 * WITHOUT expires_at; at expiry GET /api/ui/actions/pending returned 401; the executor sent
 * that to stopPanelServices(), which cleared the poll+sync timers, CLOSED the push socket with
 * onclose=null (no reconnect) and stopped the service-worker poll. The kiosk keepalive then
 * re-minted a guest session, but nothing restarted the stopped services, so every later
 * voice:* event and ui_action was lost until a manual reload (regressed in #1921).
 *
 * This runs the REAL functions from js/touch-ui-executor.js (extracted by name, same technique
 * as test_touch_voice_text_lifecycle.js) against a fake server, fake sockets, a fake service
 * worker and a fake clock:
 *   A. a 401 on the poll leaves the push socket open and the poll alive, re-mints a guest
 *      session, refreshes state.sessionId, re-sends START_PANEL_POLL, and delivery resumes.
 *   B. a 403 viewer stops poll+sync (+SW poll) only — the kiosk keeps its push socket; off a
 *      kiosk the socket is closed and never reconnects.
 *   C. an expiring member session is downgraded to guest by the watchdog BEFORE the server
 *      refuses it, without stopping anything.
 *   D. the watchdog heals a push socket that stays down (restart, then a rate-limited reload)
 *      and resumes stopped services once the refused identity changes.
 *   E. a persistent 401 cannot become a guest-mint loop.
 *
 * EXEC_SRC=<path> points the test at a mutated copy of the executor (negative controls).
 * Run: node services/zoe-ui/dist/test_touch_panel_session_recovery.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const execPath = process.env.EXEC_SRC || path.join(__dirname, 'js/touch-ui-executor.js');
const execSrc = fs.readFileSync(execPath, 'utf8');

function extract(name) {
  const m = new RegExp('(?:async )?function ' + name + '\\(').exec(execSrc);
  assert(m, 'missing function ' + name);
  let depth = 0;
  for (let j = execSrc.indexOf('{', m.index); j < execSrc.length; j++) {
    if (execSrc[j] === '{') depth++;
    else if (execSrc[j] === '}' && --depth === 0) return execSrc.slice(m.index, j + 1);
  }
  throw new Error('unbalanced braces for ' + name);
}
function constValue(name) {
  const m = execSrc.match(new RegExp('const ' + name + '\\s*=\\s*([^;]+);'));
  assert(m, 'missing const ' + name);
  // eslint-disable-next-line no-new-func
  return new Function('return (' + m[1] + ');')();
}
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

// ── fake clock: intervals + timeouts + a Date that follows it ───────────────
function makeClock() {
  let now = Date.parse('2026-10-12T09:00:00Z');
  let seq = 0;
  const timers = new Map();
  class FakeDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }
  const clock = {
    Date: FakeDate,
    now: () => now,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn, every: 0 }); return id; },
    setInterval(fn, ms) { const id = ++seq; timers.set(id, { at: now + ms, fn, every: ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    clearInterval(id) { timers.delete(id); },
    liveIntervals: () => [...timers.values()].filter((t) => t.every).length,
    // advance in 1 s steps, flushing promises between so async callbacks complete
    async advance(ms) {
      const end = now + ms;
      while (now < end) {
        const step = Math.min(1000, end - now);
        const stepEnd = now + step;
        for (;;) {
          let next = null;
          for (const [id, t] of timers) if (t.at <= stepEnd && (!next || t.at < next[1].at)) next = [id, t];
          if (!next) break;
          const [id, t] = next;
          now = t.at;
          if (t.every) t.at = now + t.every; else timers.delete(id);
          t.fn();
        }
        now = stepEnd;
        await flush();
      }
    },
  };
  return clock;
}

const ISO = (ms) => new Date(ms).toISOString();
const MEMBER = { session_id: 'mem1', user_id: 'u1', username: 'member', role: 'user' };   // NO expires_at: the live bug

function boot(opts = {}) {
  const kiosk = opts.kiosk !== false;
  const clock = makeClock();
  const store = new Map();
  if (opts.session !== null) store.set('zoe_session', JSON.stringify(opts.session || MEMBER));
  if (kiosk) store.set('zoe_kiosk', '1');
  if (opts.marker !== undefined) store.set('zoe_member_handover_at', String(opts.marker));
  const localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
  const sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };

  // fake server: knows which session ids are alive; a header with a dead id is a 401.
  const server = { alive: new Set(opts.alive || []), mints: 0, calls: [], queue: [], forbid: () => false, mintOk: true };
  const res = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const fetch = async (url, o = {}) => {
    const sid = (o.headers || {})['X-Session-ID'];
    server.calls.push({ url, sid, method: o.method || 'GET' });
    if (server.gate && sid === server.gate.sid && url.startsWith('/api/ui/')) await server.gate.p;   // hold this request in flight
    if (url.startsWith('/api/auth/guest')) {
      if (!server.mintOk) return res(500, {});
      server.mints++;
      const id = 'guest' + server.mints;
      server.alive.add(id);
      return res(200, { session_id: id, role: 'guest', user_id: 'guest', expires_at: ISO(clock.now() + 30 * 60000) });
    }
    if (url.startsWith('/api/ui/')) {
      if (server.forbid(sid)) return res(403, {});
      // routers/ui_actions._authorize_panel: a GUEST (no header) is refused while the panel row is
      // still owned by a member whose last_seen_at is fresher than 300 s.
      if (server.ownerRow) {
        if (sid === server.ownerRow.sid) server.ownerRow.lastSeen = clock.now();
        else if (!sid && clock.now() - server.ownerRow.lastSeen < 300000) return res(403, {});
      }
      if (server.refuseAll || (sid && !server.alive.has(sid))) return res(401, {});
      return res(200, { actions: server.queue.splice(0) });
    }
    return res(200, {});
  };

  const sockets = [];
  function FakeWS(url) { this.url = url; this.sent = []; this.readyState = 1; this.closed = false; sockets.push(this); }
  // the real server answers every 'ping' with {"type":"pong"}; the executor's heartbeat relies on it
  FakeWS.prototype.send = function (d) { this.sent.push(d); if (d === 'ping' && this.readyState === 1 && typeof this.onmessage === 'function') this.onmessage({ data: '{"type":"pong"}' }); };
  FakeWS.prototype.close = function () { this.closed = true; this.readyState = 3; };

  const swMessages = [];
  const swPort = { postMessage: (m) => swMessages.push(m) };
  const executed = [];
  const responding = [];
  const reloads = [];
  const state = {
    panelId: 'zoe-touch-pi', sessionId: opts.session === null ? null : (opts.session || MEMBER).session_id,
    processing: false, pollTimer: null, syncTimer: null, panelServicesStopped: false, stopReason: '', stoppedSessionId: null,
    pushStopped: false, recovering: false, lastRecoverAt: 0, guestReclaimUntil: 0, pushDownSince: 0, healedPush: false, lastHealAt: 0, watchdogTimer: null,
    seenActions: new Set(), pushWs: null, pushPing: null, pushRetry: null, pushAttempts: 0, unloading: false, viewer: false,
  };
  const window = {
    __kiosk: kiosk ? true : undefined,
    location: { search: kiosk ? '?panel_id=zoe-touch-pi&kiosk=1' : '?panel_id=zoe-touch-pi', protocol: 'https:', host: 'zoe.local', pathname: '/touch/home.html', reload: () => reloads.push(clock.now()) },
    localStorage, sessionStorage,
  };
  const env = {
    state, window, localStorage, fetch, navigator: { serviceWorker: { ready: Promise.resolve({ active: swPort }), controller: swPort } },
    location: window.location, console: { warn() {}, info() {}, log() {} }, WebSocket: FakeWS, URLSearchParams, JSON, Date: clock.Date, Number, String, Promise,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    PUSH_PING_MS: constValue('PUSH_PING_MS'), PUSH_RETRY_MAX_MS: constValue('PUSH_RETRY_MAX_MS'), PUSH_STALE_MS: constValue('PUSH_STALE_MS'),
    PANEL_RECOVER_MIN_MS: constValue('PANEL_RECOVER_MIN_MS'), SESSION_DOWNGRADE_MARGIN_MS: constValue('SESSION_DOWNGRADE_MARGIN_MS'),
    PANEL_WATCHDOG_MS: constValue('PANEL_WATCHDOG_MS'), PANEL_DEAF_AFTER_MS: constValue('PANEL_DEAF_AFTER_MS'),
    PANEL_GUEST_RECLAIM_GRACE_MS: constValue('PANEL_GUEST_RECLAIM_GRACE_MS'), PANEL_HEAL_MIN_MS: constValue('PANEL_HEAL_MIN_MS'), PANEL_RELOAD_MIN_MS: constValue('PANEL_RELOAD_MIN_MS'), PANEL_RELOAD_KEY: 'zoe_panel_watchdog_reload_at', PANEL_HANDOVER_KEY: 'zoe_member_handover_at',
    buildContext: () => ({}), executeAction: async (a) => { executed.push(a.id); return { status: 'success' }; }, ackAction: async () => {},
    panelMatches: () => true, panelMatchesAuthTarget: () => true, setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
    VoiceOverlay: { onListeningStarted() {}, onTranscript() {}, onThinking() {}, onResponding: (t) => responding.push(t), onDone() {} },
  };
  const names = Object.keys(env);
  const fnNames = ['isExpiredSession', 'isGuestSession', 'getSession', 'getDataApiSession', 'api', 'isKioskContext', 'dataApiSessionId',
    'sessionNeedsDowngrade', 'noteMemberHandover', 'adoptMemberHandover', 'dropStoredSession', 'storeSession', 'mintGuestSession', 'pushSocketStale', 'pushIsUp', 'restartFallbackPushSocket', 'ensurePushSocket', 'startServiceWorkerPanelPoll',
    'startPanelServices', 'recoverPanelSession', 'stopServiceWorkerPanelPoll', 'stopPanelServices', 'panelAuthFailed', 'handlePanelAuthFailure',
    'reloadPageRateLimited', 'panelWatchdogTick', 'bindPanel', 'syncState', 'pollActions', 'schedulePushReconnect', 'startPushPing', 'connectPushWebSocket'];
  const fns = new Function(...names, fnNames.map(extract).join('\n') + '\nreturn { ' + fnNames.join(', ') + ' };')(...names.map((n) => env[n]));
  // what init() does on a kiosk
  function start() {
    fns.adoptMemberHandover();                           // init() does this before bindPanel
    fns.connectPushWebSocket();
    sockets[0].onopen();
    state.pollTimer = clock.setInterval(fns.pollActions, 2000);
    state.syncTimer = clock.setInterval(fns.syncState, 5000);
    if (kiosk) state.watchdogTimer = clock.setInterval(fns.panelWatchdogTick, env.PANEL_WATCHDOG_MS);
  }
  const stored = () => { const raw = store.get('zoe_session'); return raw ? JSON.parse(raw) : null; };
  return { fns, state, clock, server, sockets, swMessages, executed, responding, reloads, store, stored, start, env };
}

let n = 0; const ok = (m) => { n++; console.log('  ok  ' + m); };

(async () => {
  // ── A. a 401 on the poll: nothing stops, the session is re-minted, delivery resumes ──
  {
    const t = boot({ alive: [] });                       // 'mem1' is dead server-side (expired), and has no expires_at client-side
    t.start();
    assert.strictEqual(t.sockets.length, 1);
    await t.clock.advance(2000);                          // the poll tick: 401
    assert.strictEqual(t.server.mints, 1, 'a guest session was minted');
    const s = t.stored();
    assert(s && s.session_id === 'guest1' && s.role === 'guest', 'the dead member session was dropped and replaced by a guest session');
    assert.strictEqual(t.state.sessionId, 'guest1', 'state.sessionId follows the new session');
    assert.strictEqual(t.state.panelServicesStopped, false, 'a 401 never stops the panel services');
    assert(t.state.pollTimer && t.state.syncTimer, 'poll and sync timers are still armed');
    assert.strictEqual(t.sockets[0].closed, false, 'the push socket is NOT closed by a 401');
    assert.strictEqual(typeof t.sockets[0].onclose, 'function', 'the push socket keeps its onclose (so it can reconnect)');
    const starts = t.swMessages.filter((m) => m.type === 'START_PANEL_POLL');
    assert(starts.length >= 1 && starts[starts.length - 1].panelId === 'zoe-touch-pi', 'START_PANEL_POLL is re-sent to the service worker');
    assert.strictEqual(starts[starts.length - 1].sessionId, null, 'the SW is handed no guest session id (the data API maps a missing header to guest)');
    assert(!t.swMessages.some((m) => m.type === 'STOP_PANEL_POLL'), 'the service-worker poll is not stopped');
    ok('A1: a 401 on poll keeps socket + poll + sync alive, drops the dead session, mints a guest, refreshes state.sessionId, re-sends START_PANEL_POLL');

    // delivery resumes: the queued ui_action arrives by poll, and a voice event arrives over the SAME socket
    t.server.queue.push({ id: 'act1', action_type: 'show_card' });
    await t.clock.advance(2000);
    assert.deepStrictEqual(t.executed, ['act1'], 'the poll delivers again after the re-mint');
    assert(t.server.calls.filter((c) => c.url.startsWith('/api/ui/actions/pending')).slice(-1)[0].sid === undefined, 'the guest poll carries no X-Session-ID');
    t.sockets[0].onmessage({ data: JSON.stringify({ type: 'voice:responding', data: { panel_id: 'zoe-touch-pi', text: 'Hello there.' } }) });
    assert.deepStrictEqual(t.responding, ['Hello there.'], 'a voice:* event is still handled on the original socket');
    assert.strictEqual(t.sockets.length, 1, 'no second socket was needed');
    ok('A2: after the re-mint a queued action is delivered by poll and a voice:responding arrives over the same push socket');
  }

  // ── A3. a 401 from sync or bind recovers too ──
  {
    const t = boot({ alive: [] });
    t.start();
    await t.clock.advance(5000);
    assert.strictEqual(t.state.panelServicesStopped, false);
    assert.strictEqual(t.stored().session_id, 'guest1');
    assert.strictEqual(t.sockets[0].closed, false);
    ok('A3: a 401 seen by sync/bind/poll within one window mints exactly one guest session (single-flight)');
    assert.strictEqual(t.server.mints, 1);
  }

  // ── B. a 403: poll+sync (+SW) stop; the kiosk keeps its push socket, off-kiosk closes it ──
  {
    const t = boot({ alive: ['mem1'] });
    t.server.forbid = (sid) => sid === 'mem1';             // an unbound member
    t.start();
    await t.clock.advance(2000);
    assert.strictEqual(t.state.panelServicesStopped, true, 'a 403 stops the panel services');
    assert.strictEqual(t.state.pollTimer, null); assert.strictEqual(t.state.syncTimer, null);
    assert(t.swMessages.some((m) => m.type === 'STOP_PANEL_POLL'), 'the SW poll is stopped on a 403');
    assert.strictEqual(t.sockets[0].closed, false, 'a KIOSK keeps its push socket on a 403');
    t.sockets[0].onmessage({ data: JSON.stringify({ type: 'voice:responding', data: { text: 'Still here.' } }) });
    assert.deepStrictEqual(t.responding, ['Still here.']);
    t.sockets[0].onclose();                                 // and it reconnects like any kiosk socket
    await t.clock.advance(2000);
    assert.strictEqual(t.sockets.length, 2, 'a kiosk push socket reconnects after a 403 stop');
    const sync = t.server.calls.filter((c) => c.url.startsWith('/api/ui/state/sync')).length;
    await t.clock.advance(20000);
    assert.strictEqual(t.server.calls.filter((c) => c.url.startsWith('/api/ui/state/sync')).length, sync, 'a stopped sync never posts again');
    ok('B1: a 403 on a kiosk stops poll + sync + SW poll only; the push socket stays up and reconnects');
  }
  {
    const t = boot({ alive: ['mem1'], kiosk: false });
    t.server.forbid = (sid) => sid === 'mem1';
    t.start();
    await t.clock.advance(2000);
    assert.strictEqual(t.state.panelServicesStopped, true);
    assert.strictEqual(t.sockets[0].closed, true, 'off a kiosk the unbound member\'s push socket is closed');
    t.sockets[0].onclose && t.sockets[0].onclose();
    await t.clock.advance(60000);
    assert.strictEqual(t.sockets.length, 1, 'and never reconnects');
    ok('B2: a 403 off a kiosk still closes the push socket for good (the #1921 intent for an unbound member)');
  }

  // ── C. an expiring member session is downgraded cleanly by the watchdog ──
  {
    const exp = Date.parse('2026-10-12T10:00:00Z');
    const t = boot({ alive: ['mem1'], session: Object.assign({}, MEMBER, { expires_at: ISO(exp) }) });
    t.start();
    await t.clock.advance(30 * 60000);                      // 09:30 — far from expiry
    assert.strictEqual(t.stored().session_id, 'mem1', 'a healthy member session is left alone');
    assert.strictEqual(t.server.mints, 0);
    await t.clock.advance(29 * 60000 + 30000);              // 09:59:30 — inside the 60 s margin
    const s = t.stored();
    assert(s && s.role === 'guest', 'the member session was downgraded to guest BEFORE its expiry');
    assert(t.clock.now() < exp, 'before the server would have refused it');
    assert.strictEqual(t.state.sessionId, s.session_id);
    assert.strictEqual(t.state.panelServicesStopped, false);
    assert.strictEqual(t.sockets[0].closed, false);
    ok('C1: the watchdog downgrades a member session shortly before expires_at — no 401 needed, nothing stops');
    const mints = t.server.mints;
    await t.clock.advance(5 * 60000);
    assert.strictEqual(t.server.mints, mints, 'no further mints once on a guest session');
    ok('C2: once on the guest session the watchdog does not keep minting');
  }
  {
    const e = boot({ alive: ['mem1'], session: Object.assign({}, MEMBER, { expires_at: ISO(Date.parse('2026-10-12T09:00:20Z')) }) });
    assert.strictEqual(e.fns.sessionNeedsDowngrade(JSON.parse(e.store.get('zoe_session')), e.clock.now(), 60000), true);
    assert.strictEqual(e.fns.sessionNeedsDowngrade({ role: 'guest', expires_at: ISO(e.clock.now() - 1) }, e.clock.now(), 60000), false, 'a guest is never downgraded');
    assert.strictEqual(e.fns.sessionNeedsDowngrade(MEMBER, e.clock.now(), 60000), false, 'no expires_at: left to the 401 path');
    assert.strictEqual(e.fns.sessionNeedsDowngrade({ role: 'user', expires_at: 'garbage' }, e.clock.now(), 60000), false);
    ok('C3: sessionNeedsDowngrade — member inside the margin yes; guest / no expires_at / garbage no');
  }

  // ── D. watchdog: a push socket that stays down is restarted, then (rate-limited) the page reloads ──
  {
    const t = boot({ alive: ['mem1'] });
    t.start();
    t.sockets[0].readyState = 3;                            // the socket silently died, no close event reached us
    await t.clock.advance(45000);
    assert.strictEqual(t.sockets.length, 1, 'inside the grace window nothing is restarted');
    await t.clock.advance(30000);
    assert(t.sockets.length >= 2, 'a push socket down > 60 s is restarted by the watchdog');
    t.sockets[t.sockets.length - 1].readyState = 3;         // the new one is dead too
    assert.strictEqual(t.reloads.length, 0, 'no reload on the first rung');
    await t.clock.advance(75000);
    assert.strictEqual(t.reloads.length, 1, 'still down after the restart + another window: last-resort reload');
    await t.clock.advance(5 * 60000);
    assert.strictEqual(t.reloads.length, 1, 'rate-limited: no second reload within 10 minutes');
    ok('D1: watchdog restarts a push socket down > 60 s, then reloads once, rate-limited');
  }
  {
    const t = boot({ alive: ['mem1'] });
    t.start();
    t.store.set('zoe_panel_watchdog_reload_at', String(t.clock.now() - 60000));   // reloaded a minute ago (survives the reload)
    t.sockets[0].readyState = 3;
    await t.clock.advance(300000);
    assert.strictEqual(t.reloads.length, 0, 'the persisted stamp rate-limits across page loads');
    ok('D2: the reload limiter is persisted (no reload loop across page loads)');
  }
  {
    const t = boot({ alive: ['mem1', 'guestX'] });
    t.server.forbid = (sid) => sid === 'mem1';
    t.start();
    await t.clock.advance(2000);                            // 403 -> stopped
    assert.strictEqual(t.state.panelServicesStopped, true);
    await t.clock.advance(120000);
    assert.strictEqual(t.state.panelServicesStopped, true, 'a genuine 403 for the SAME identity stays stopped (no retry loop)');
    t.store.set('zoe_session', JSON.stringify({ session_id: 'guestX', role: 'guest', user_id: 'guest' }));   // identity changed: the member signed out
    await t.clock.advance(31000);
    assert.strictEqual(t.state.panelServicesStopped, false, 'the watchdog resumes stopped services once the identity changed');
    assert(t.state.pollTimer && t.state.syncTimer);
    ok('D3: stopped services stay stopped for the same refused identity and resume when it changes');
  }
  {
    const t = boot({ alive: ['mem1'] });
    t.start();
    t.state.panelServicesStopped = true;                    // defensively: stopped for any non-403 reason
    t.state.stopReason = 'something else';
    t.state.pollTimer = null; t.state.syncTimer = null;
    await t.clock.advance(31000);
    assert.strictEqual(t.state.panelServicesStopped, false);
    assert(t.state.pollTimer && t.state.syncTimer);
    ok('D4: services stopped for any reason other than a same-identity 403 are restarted by the watchdog');
  }

  // ── F. member -> guest downgrade vs _authorize_panel's 300 s owner-staleness window ──
  {
    const exp = Date.parse('2026-10-12T10:00:00Z');
    const t = boot({ alive: ['mem1'], session: Object.assign({}, MEMBER, { expires_at: ISO(exp) }) });
    t.server.ownerRow = { sid: 'mem1', lastSeen: t.clock.now() };   // the member's panel row
    t.start();
    await t.clock.advance(59 * 60000 + 30000);                       // 09:59:30 -> watchdog downgrades
    assert.strictEqual(t.stored().role, 'guest', 'precondition: downgraded to guest');
    const downgradedAt = t.clock.now();
    await t.clock.advance(20000);                                    // the guest's bind/sync/poll are refused (403)
    const refused = t.server.calls.filter((c) => c.url.startsWith('/api/ui/') && !c.sid && c.at === undefined).length;
    assert(refused > 0, 'the guest calls did reach the server');
    assert.strictEqual(t.state.panelServicesStopped, false, 'the expected 403 must NOT stop the services (that is the deafness again)');
    assert(t.state.pollTimer && t.state.syncTimer, 'poll and sync keep ticking');
    assert.strictEqual(t.sockets[0].closed, false, 'push socket stays up');
    assert(!t.swMessages.some((m) => m.type === 'STOP_PANEL_POLL'), 'SW poll not stopped');
    await t.clock.advance(300000 - 20000 + 5000);                    // the owner row goes stale (300 s since the member's last call)
    t.server.queue.push({ id: 'after-reclaim', action_type: 'show_card' });
    await t.clock.advance(4000);
    assert.deepStrictEqual(t.executed, ['after-reclaim'], 'delivery resumes by itself once the server lets the guest reclaim the panel');
    assert.strictEqual(t.state.panelServicesStopped, false);
    ok('F1: a member->guest downgrade rides out the 300 s panel-reclaim 403s without stopping anything, then delivers');
    void downgradedAt;
  }
  {
    // a genuine 403 for a guest long after any handover still stops (the grace is bounded)
    const t = boot({ alive: [], session: null });
    t.server.forbid = () => true;
    t.state.guestReclaimUntil = t.clock.now() - 1;
    t.start();
    await t.clock.advance(2000);
    assert.strictEqual(t.state.panelServicesStopped, true, 'outside the grace window a 403 still stops poll+sync');
    ok('F2: the reclaim grace is bounded — a 403 outside it stops as before');
  }

  // ── G. the handover survives a page load (PR #1999 review) ──
  // touch/home.html's boot guard drops an EXPIRED member session before the executor runs and a
  // guest is minted, so the executor boots as a guest with no memory of the member; the server still
  // holds the panel row for the member for 300 s and refuses the guest (403).
  const GUEST_LIVE = { session_id: 'guestA', role: 'guest', user_id: 'guest', expires_at: '2026-10-12T09:30:00Z' };
  {
    const t = boot({ alive: ['guestA'], session: GUEST_LIVE, marker: Date.parse('2026-10-12T09:00:00Z') - 5000 });
    t.server.ownerRow = { sid: 'mem1', lastSeen: t.clock.now() - 5000 };
    t.start();
    await t.clock.advance(60000);
    assert(t.server.calls.some((c) => c.url.startsWith('/api/ui/') && !c.sid), 'the guest calls reached the server');
    assert.strictEqual(t.state.panelServicesStopped, false, 'a boot as guest right after a member handover must ride out the 403s');
    assert(t.state.pollTimer && t.state.syncTimer);
    await t.clock.advance(300000);                                   // the member row goes stale
    t.server.queue.push({ id: 'after-boot-handover', action_type: 'show_card' });
    await t.clock.advance(4000);
    assert.deepStrictEqual(t.executed, ['after-boot-handover'], 'delivery resumes once the server lets the guest reclaim');
    ok('G1: a guest boot inside a persisted handover window keeps retrying through the reclaim 403s, then delivers');
  }
  {
    const t = boot({ alive: ['guestA'], session: GUEST_LIVE });      // no stamp: the pre-fix behaviour
    t.server.ownerRow = { sid: 'mem1', lastSeen: t.clock.now() - 5000 };
    t.start();
    await t.clock.advance(5000);
    assert.strictEqual(t.state.panelServicesStopped, true, 'with no handover stamp a genuine 403 still stops');
    ok('G2: without a handover stamp a guest 403 still stops (the grace is only for a real handover)');
  }
  {
    const base = Date.parse('2026-10-12T09:00:00Z');
    const stale = boot({ alive: ['guestA'], session: GUEST_LIVE, marker: base - 331000 });
    stale.start();
    assert.strictEqual(stale.state.guestReclaimUntil, 0, 'a stamp older than the grace opens no window');
    const junk = boot({ alive: ['guestA'], session: GUEST_LIVE, marker: 'garbage' });
    junk.start();
    assert.strictEqual(junk.state.guestReclaimUntil, 0, 'a garbage stamp opens no window');
    const future = boot({ alive: ['guestA'], session: GUEST_LIVE, marker: base + 10 * 3600000 });
    future.start();
    assert(future.state.guestReclaimUntil <= base + 330000, 'a stamp from the future cannot extend the window past the grace');
    ok('G3: stale, garbage and future stamps are bounded');
  }
  {
    // recoverPanelSession persists the handover, so a reload inside the window re-opens it
    const exp = Date.parse('2026-10-12T10:00:00Z');
    const a = boot({ alive: ['mem1'], session: Object.assign({}, MEMBER, { expires_at: ISO(exp) }) });
    a.start();
    await a.clock.advance(59 * 60000 + 30000);
    assert.strictEqual(a.stored().role, 'guest', 'precondition: downgraded');
    const stamp = a.store.get('zoe_member_handover_at');
    assert(stamp && Number(stamp) > 0, 'the downgrade persisted a handover stamp');
    const b = boot({ alive: ['guestA'], session: GUEST_LIVE, marker: Number(stamp) - (59 * 60000 + 30000) });   // same instant, new page (fresh clock origin)
    b.start();
    assert(b.state.guestReclaimUntil > b.clock.now(), 'the reloaded page re-opens the window from the stamp');
    ok('G4: recoverPanelSession stamps the handover; a reloaded page adopts it');
  }

  // ── H. a LATE 401 for an old session must not delete a NEWER member session (PR #1999 review) ──
  {
    const t = boot({ alive: ['mem2'] });                    // 'mem1' is dead server-side; 'mem2' is the fresh PIN sign-in
    let release; t.server.gate = { sid: 'mem1', p: new Promise((r) => { release = r; }) };
    t.start();
    const inflight = t.fns.pollActions();                   // goes out with X-Session-ID: mem1 and is held
    await flush();
    const mem2 = { session_id: 'mem2', user_id: 'u1', username: 'member', role: 'user', expires_at: ISO(t.clock.now() + 3600000) };
    t.store.set('zoe_session', JSON.stringify(mem2));       // a fresh PIN sign-in lands while the old poll is in flight
    t.state.sessionId = 'mem2';
    release(); await inflight; await flush();               // ...and the old poll comes back 401
    assert.strictEqual(t.stored().session_id, 'mem2', 'the newer member session must survive a late 401 for mem1');
    assert.strictEqual(t.server.mints, 0, 'no guest was minted over the fresh sign-in');
    assert.strictEqual(t.state.sessionId, 'mem2');
    assert.strictEqual(t.state.panelServicesStopped, false);
    assert.strictEqual(t.state.guestReclaimUntil, 0, 'no member->guest handover was opened');
    assert(!t.store.get('zoe_member_handover_at'), 'and no handover stamp was persisted');
    const starts = t.swMessages.filter((m) => m.type === 'START_PANEL_POLL');
    assert.strictEqual(starts[starts.length - 1].sessionId, 'mem2', 'services are (re)started under the newer session');
    ok('H1: a late 401 for an old session id leaves the newer member session in place, no guest mint, no handover');
  }
  {
    // the service worker names the refused session; a stale notice is ignored, a current one recovers
    const t = boot({ alive: ['mem2'] });
    t.start();
    const mem2 = { session_id: 'mem2', user_id: 'u1', username: 'member', role: 'user', expires_at: ISO(t.clock.now() + 3600000) };
    t.store.set('zoe_session', JSON.stringify(mem2));
    await t.fns.recoverPanelSession('service worker poll refused: HTTP 401', { force: true, refusedSessionId: 'mem1' });
    assert.strictEqual(t.stored().session_id, 'mem2', 'a stale SW notice (mem1) does not drop mem2');
    assert.strictEqual(t.server.mints, 0);
    await t.fns.recoverPanelSession('service worker poll refused: HTTP 401', { force: true, refusedSessionId: 'mem2' });
    assert.strictEqual(t.stored().role, 'guest', 'a notice naming the CURRENT session still recovers');
    ok('H2: recoverPanelSession(refusedSessionId) only drops the session that was actually refused');
  }
  {
    // a 401 for a request that carried NO session (guest) must not downgrade a newer member session either
    const t = boot({ alive: ['mem2'], session: Object.assign({}, MEMBER, { session_id: 'mem2', expires_at: ISO(Date.parse('2026-10-12T10:00:00Z')) }) });
    await t.fns.recoverPanelSession('late guest 401', { force: true, refusedSessionId: null });
    assert.strictEqual(t.stored().session_id, 'mem2', 'a refused guest request (no session id) does not touch a member session');
    ok('H3: a refused request that carried no session id never deletes a member session');
  }

  // ── E. a persistent 401 is not a mint loop ──
  {
    const t = boot({ alive: [] });
    t.server.refuseAll = true;                              // every /api/ui call is a 401, whatever the session (e.g. auth is down)
    t.start();
    await t.clock.advance(5 * 60000);
    const polls = t.server.calls.filter((c) => c.url.startsWith('/api/ui/actions/pending')).length;
    assert(polls > 100, 'the poll keeps running (' + polls + ' polls)');
    assert.strictEqual(t.server.mints, 1, 'ONE guest session is minted, not one per refused poll (got ' + t.server.mints + ')');
    assert.strictEqual(t.state.panelServicesStopped, false, 'and the services still never stop');
    assert.strictEqual(t.sockets[0].closed, false);
    ok('E: a persistent 401 is not a mint storm — one guest session, services keep running');
  }

  console.log('panel session recovery: ' + n + ' checks passed');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
