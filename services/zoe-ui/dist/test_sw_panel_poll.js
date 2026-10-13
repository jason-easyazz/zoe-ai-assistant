#!/usr/bin/env node
/**
 * Service-worker panel poll vs. the member->guest handover (PR #1999 review, 2026-10-13) — no
 * browser, no network. Runs the REAL panel-executor block of sw.js (extracted between its two
 * section markers) in a vm against a fake fetch, a fake clients registry and a fake clock.
 *
 * The defect: a PIN session expires -> the SW's poll gets a 401 -> it drops to a guest poll.
 * The server's _authorize_panel refuses that guest with a 403 for up to 300 s (the former
 * owner's ui_panel_sessions row is still fresh). The page-level poll rides that window out
 * (PANEL_GUEST_RECLAIM_GRACE_MS), but the SW treated the very first 403 as fatal: it cleared its
 * timer AND its panel id, and nothing restarts it after reclaim succeeds — so navigation /
 * background fallback delivery stayed dead until a reload.
 *
 *   S1: a guest 403 inside the reclaim window keeps the SW poll alive and delivery resumes
 *       the moment the guest is allowed again (no START_PANEL_POLL needed).
 *   S2: the grace is bounded — a guest still refused after it stops (no forever-poll).
 *   S3: a 403 on a MEMBER-session poll still stops at once (genuinely not allowed).
 *   S4: a successful poll resets the window (a later, unrelated 403 gets a fresh grace).
 *
 * SW_SRC=<path> points the test at a mutated copy of sw.js (negative controls).
 * Run: node services/zoe-ui/dist/test_sw_panel_poll.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const swPath = process.env.SW_SRC || path.join(__dirname, 'sw.js');
const swSrc = fs.readFileSync(swPath, 'utf8');
const a = swSrc.indexOf('let _panelPollTimer = null;');
const b = swSrc.indexOf('// ===== INSTALLATION');
assert(a > 0 && b > a, 'sw.js panel executor block not found');
const block = swSrc.slice(a, b);

const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

function boot() {
  let now = Date.parse('2026-10-13T09:00:00Z');
  const t = { intervals: new Map(), seq: 0, calls: [], posted: [], mode: 'ok' };
  class FakeDate extends Date {
    constructor(...x) { if (x.length) super(...x); else super(now); }
    static now() { return now; }
  }
  const res = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const fakeFetch = async (url, o = {}) => {
    const sid = (o.headers || {})['X-Session-ID'] || null;
    t.calls.push({ url, sid });
    if (t.gate && sid === t.gate.sid) await t.gate.p;   // hold THIS request in flight
    if (t.mode === 'mem1-dead') return sid === 'mem1' ? res(401, {}) : res(200, { actions: [] });
    if (t.mode === 'mem1-forbid') return sid === 'mem1' ? res(403, {}) : res(200, { actions: [] });
    if (t.mode === 'ok') return res(200, { actions: [] });
    if (t.mode === '401') return res(401, {});
    if (t.mode === 'forbid') return res(403, {});
    // 'reclaim': the guest (no session header) is refused while the former owner's row is fresh
    if (t.mode === 'reclaim') return sid ? res(200, { actions: [] }) : res(403, {});
    return res(500, {});
  };
  const ctx = {
    Date: FakeDate, console: { log() {}, warn() {} }, fetch: fakeFetch, encodeURIComponent,
    setInterval: (fn) => { const id = ++t.seq; t.intervals.set(id, fn); return id; },
    clearInterval: (id) => { t.intervals.delete(id); },
    clients: { matchAll: async () => [{ postMessage: (m) => t.posted.push(m) }] },
    self: { location: { origin: 'http://x' } },
  };
  vm.createContext(ctx);
  vm.runInContext(block, ctx);
  t.ctx = ctx;
  t.start = async (panel, sid) => { vm.runInContext(`_startPanelPoll(${JSON.stringify(panel)}, ${JSON.stringify(sid)})`, ctx); await flush(); };
  t.tick = async () => { for (const fn of [...t.intervals.values()]) fn(); await flush(); };
  t.advance = async (ms) => { now += ms; };
  t.panelId = () => vm.runInContext('_panelId', ctx);
  t.alive = () => t.intervals.size === 1 && t.panelId() === 'zoe-touch-pi';
  return t;
}

let n = 0; const ok = (m) => { n++; console.log('  ok  ' + m); };

(async () => {
  // S1 + S4: the real handover — member poll -> 401 -> guest poll -> 403s -> guest allowed.
  {
    const t = boot();
    t.mode = '401';
    await t.start('zoe-touch-pi', 'mem1');
    assert(t.posted.some((m) => m.type === 'SW_PANEL_AUTH_EXPIRED'), 'precondition: the page is told the session died');
    assert(t.alive(), 'a 401 leaves the SW poll alive (as a guest)');
    t.mode = 'reclaim';
    for (let i = 0; i < 6; i++) { await t.advance(5000); await t.tick(); }
    assert(t.alive(), 'S1: the guest 403 inside the reclaim window must NOT stop the SW poll');
    await t.advance(250000); await t.tick();           // ~280 s in: still inside 330 s
    assert(t.alive(), 'S1: still alive deep inside the window');
    t.mode = 'ok';                                     // the member row went stale: the guest is allowed
    const before = t.calls.length;
    await t.advance(5000); await t.tick();
    assert(t.calls.length === before + 1 && t.alive(), 'S1: delivery resumes by itself once the guest is allowed');
    ok('S1: a guest 403 inside the reclaim window keeps the SW poll alive and delivery resumes without a restart');
    t.mode = 'reclaim';                                // S4: the success reset the window
    await t.advance(5000); await t.tick();
    await t.advance(200000); await t.tick();
    assert(t.alive(), 'S4: a success resets the grace window');
    ok('S4: a successful poll resets the guest-refusal window');
  }
  // S2: bounded.
  {
    const t = boot();
    t.mode = '401';
    await t.start('zoe-touch-pi', 'mem1');
    t.mode = 'reclaim';
    await t.advance(5000); await t.tick();
    await t.advance(340000); await t.tick();           // past the 330 s grace and still refused
    assert(!t.alive() && t.panelId() === null, 'S2: a guest still refused after the grace stops the SW poll');
    ok('S2: the grace is bounded - a guest still refused after 330 s stops the SW poll');
  }
  // S3: a member session refused outright still stops at once.
  {
    const t = boot();
    t.mode = 'forbid';
    await t.start('zoe-touch-pi', 'mem1');
    assert(!t.alive() && t.panelId() === null, 'S3: a 403 on a member-session poll stops at once');
    ok('S3: a 403 on a member session still stops the SW poll immediately');
  }
  // S5: a LATE 401 for an old session must not wipe a newer one issued by a fresh PIN sign-in.
  {
    const t = boot();
    t.mode = 'mem1-dead';
    let release; t.gate = { sid: 'mem1', p: new Promise((r) => { release = r; }) };
    await t.start('zoe-touch-pi', 'mem1');                 // the poll with mem1 is now in flight
    await t.start('zoe-touch-pi', 'mem2');                 // the page signed in again and re-sent START_PANEL_POLL
    assert.strictEqual(vm.runInContext('_panelSessionId', t.ctx), 'mem2');
    release(); await flush();                              // the old request comes back 401
    assert.strictEqual(vm.runInContext('_panelSessionId', t.ctx), 'mem2', 'S5: a late 401 for mem1 must not drop the newer mem2');
    assert(!t.posted.some((m) => m.type === 'SW_PANEL_AUTH_EXPIRED'), 'S5: and must not tell the page its session died');
    assert(t.alive());
    ok('S5: a late 401 for an old session leaves the newer session in place and the page un-notified');
  }
  // S6: a 401 for the CURRENT session still drops it and tells the page WHICH session was refused.
  {
    const t = boot();
    t.mode = '401';
    await t.start('zoe-touch-pi', 'mem1');
    const msg = t.posted.find((m) => m.type === 'SW_PANEL_AUTH_EXPIRED');
    assert(msg && msg.sessionId === 'mem1', 'S6: the notice carries the refused session id');
    assert.strictEqual(vm.runInContext('_panelSessionId', t.ctx), null);
    ok('S6: a 401 for the current session still drops it and names the refused session id');
  }
  // S7: a STALE guest 403 (sent inside the reclaim window) that lands AFTER a fresh PIN sign-in
  // re-sent START_PANEL_POLL must not stop the poll the new member session is running.
  {
    const t = boot();
    t.mode = 'reclaim';
    let release; t.gate = { sid: null, p: new Promise((r) => { release = r; }) };
    await t.start('zoe-touch-pi', null);                  // guest poll in flight (will be refused 403)
    await t.start('zoe-touch-pi', 'mem2');                 // the page signed in with a PIN and re-sent START_PANEL_POLL
    assert.strictEqual(vm.runInContext('_panelSessionId', t.ctx), 'mem2');
    release(); await flush();                              // the old guest request comes back 403
    assert(t.alive(), 'S7: a stale guest 403 must not stop the new session\'s SW poll');
    assert.strictEqual(vm.runInContext('_panelSessionId', t.ctx), 'mem2', 'S7: and must not clear the newer session');
    const before = t.calls.length;
    await t.advance(5000); await t.tick();
    assert(t.calls.length === before + 1 && t.calls[t.calls.length - 1].sid === 'mem2', 'S7: delivery continues under mem2');
    ok('S7: a stale guest 403 landing after a fresh PIN login leaves the SW poll running');
  }
  // S8: same for a stale MEMBER 403 (an older member session replaced by a newer one).
  {
    const t = boot();
    t.mode = 'mem1-forbid';
    let release; t.gate = { sid: 'mem1', p: new Promise((r) => { release = r; }) };
    await t.start('zoe-touch-pi', 'mem1');
    await t.start('zoe-touch-pi', 'mem2');
    release(); await flush();
    assert(t.alive() && vm.runInContext('_panelSessionId', t.ctx) === 'mem2', 'S8: a stale member 403 must not stop the newer session\'s poll');
    ok('S8: a stale 403 for a replaced member session leaves the newer session polling');
  }
  console.log(`  ${n} checks passed`);
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
