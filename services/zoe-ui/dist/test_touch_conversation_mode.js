/*
 * Controlled test for the estate Ask card's live-conversation mode (home.html).
 *
 * The kiosk rules this pins are the ones that bite in the room:
 *   - the LiveKit bundle is fetched ONLY on entry, never on estate boot;
 *   - a live session suppresses ambient-return AND idle->sleep (no drifting
 *     home or sleeping mid-sentence);
 *   - EVERY exit path releases the mic and disconnects — an open mic on a
 *     kiosk is the one unacceptable failure;
 *   - conversation mode is INLINE: it never hides the dock/home/orb.
 *
 * Runs the REAL home.html script in a jsdom-free fake DOM: the script body is
 * extracted from the file and evaluated against mocked document/fetch/LiveKit,
 * mirroring test_touch_panel_match.js (no browser, no network).
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

// ── The estate script is one big self-invoking IIFE; grab it whole, from the
// `(function(){` that precedes GEAR through to its closing `</script>`. ──────
function estateScript() {
  const anchor = html.indexOf("var GEAR='<svg");
  assert(anchor >= 0, 'estate script anchor (GEAR) not found');
  const start = html.lastIndexOf('(function(){', anchor);
  assert(start >= 0, 'estate IIFE start not found');
  const end = html.indexOf('</script>', anchor);
  assert(end > start, 'estate script end not found');
  return html.slice(start, end);
}

// ── Minimal DOM good enough for the conversation paths ──────────────────────
function makeEl(id) {
  const cls = new Set();
  const el = {
    id,
    style: {},
    dataset: {},
    children: [],
    _text: '',
    _attrs: {},
    disabled: false,
    classList: {
      add: (c) => cls.add(c),
      remove: (c) => cls.delete(c),
      toggle: (c, on) => (on === undefined ? (cls.has(c) ? cls.delete(c) : cls.add(c)) : (on ? cls.add(c) : cls.delete(c))),
      contains: (c) => cls.has(c),
    },
    _cls: cls,
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; },
    addEventListener() {},
    removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    remove() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
    closest() { return null; },
    focus() {},
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; },
    get innerHTML() { return this._html || ''; },
    set innerHTML(v) { this._html = v; },
    // The estate's ambient canvas — inert stub, it draws nothing here.
    getContext: () => new Proxy({}, { get: () => () => ({ addColorStop() {} }) }),
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 1280, height: 720 }),
  };
  return el;
}

function run({ tokenOk = true, bundleOk = true } = {}) {
  const els = {};
  const ids = ['orb', 'full', 'cardwrap', 'stage', 'glow', 'cv', 'pick', 'home', 'dock',
    'cmdbar', 'cmdin', 'cmdgo', 'estModal', 'talkBtn', 'askOut', 'authov', 'setVer'];
  ids.forEach((i) => { els[i] = makeEl(i); });

  const trace = { scripts: [], fetches: [], mic: [], disconnects: [], timers: [] };
  const listeners = {};

  const document = {
    // Auto-vivify: the estate boot touches many ids that are irrelevant here,
    // and enumerating them would make this test a maintenance tax.
    getElementById: (i) => (els[i] || (els[i] = makeEl(i))),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    createElement: (tag) => {
      const e = makeEl(tag);
      if (tag === 'script') {
        // Record the lazy-load and resolve it like a real <script> would.
        // bundleOk: true = loads; false = 404 (onerror); 'noglobal' = the script
        // loads but exports no LivekitClient (onload with the global absent).
        Object.defineProperty(e, 'src', {
          set(v) {
            trace.scripts.push(v);
            const ok = typeof bundleOk === 'function' ? bundleOk() : bundleOk;
            setTimeout(() => {
              if (ok === true) { sandbox.window.LivekitClient = fakeLiveKit(trace); e.onload && e.onload(); }
              else if (ok === 'noglobal') { e.onload && e.onload(); }
              else { e.onerror && e.onerror(new Error('404')); }
            }, 0);
          },
          get() { return ''; },
        });
      }
      return e;
    },
    head: makeEl('head'),
    body: makeEl('body'),
  };

  const window = {
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    location: { search: '', pathname: '/touch/home.html' },
    localStorage: { getItem: () => null, setItem: () => {} },
  };

  const fetchImpl = (url) => {
    trace.fetches.push(url);
    if (String(url).indexOf('/api/voice/livekit-token') === 0) {
      return tokenOk
        ? Promise.resolve({ ok: true, json: () => Promise.resolve({ token: 'jwt', url: 'ws://lk' }) })
        : Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });
    }
    // Everything else the estate boots (weather/today/prefs) — inert.
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}), body: null });
  };

  // Record every timer the estate schedules, so we can prove a live session
  // arms neither the ambient-return drift nor the idle->sleep clock.
  const timers = new Map();
  const trackedSetTimeout = (fn, ms) => {
    const id = setTimeout(fn, ms);
    timers.set(id, ms);
    trace.timers.push(ms);
    return id;
  };
  const trackedClearTimeout = (id) => { timers.delete(id); return clearTimeout(id); };
  trace.pending = () => Array.from(timers.values());

  const sandbox = {
    document, window, fetch: fetchImpl, console,
    // Bare globals the estate boot touches (in a browser these are window.*).
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener: () => {},
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1,
    setTimeout: trackedSetTimeout, clearTimeout: trackedClearTimeout, setInterval, clearInterval,
    URLSearchParams, TextDecoder, Promise, Date, Math, JSON, isNaN, parseInt, parseFloat, encodeURIComponent,
    history: { replaceState: () => {} },
    navigator: { mediaDevices: { getUserMedia: () => Promise.resolve({}) } },
    Audio: function () { return { play: () => Promise.resolve(), pause() {}, addEventListener() {} }; },
    localStorage: window.localStorage,
    location: window.location,
  };
  sandbox.window.document = document;
  sandbox.globalThis = sandbox;
  global.window = sandbox.window;

  const vm = require('vm');
  const ctx = vm.createContext(sandbox);
  // The estate script is already self-invoking — run it verbatim.
  vm.runInContext(estateScript(), ctx, { timeout: 5000 });

  return { trace, sandbox, els, listeners };
}

function fakeLiveKit(trace) {
  return {
    Room: function () {
      const handlers = {};
      return {
        on(evt, fn) { handlers[evt] = fn; return this; },
        _emit(evt, arg) { handlers[evt] && handlers[evt](arg); },
        connect: () => Promise.resolve(),
        disconnect(stop) { trace.disconnects.push(stop); return Promise.resolve(); },
        localParticipant: {
          setMicrophoneEnabled(on) { trace.mic.push(on); return Promise.resolve(); },
        },
      };
    },
    RoomEvent: { DataReceived: 'dataReceived', Disconnected: 'disconnected' },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 5));

(async () => {
  // ── 1. The bundle is NOT fetched on boot (memory-tight box; on-demand LK) ──
  {
    const { trace } = run();
    await tick();
    assert.strictEqual(trace.scripts.length, 0, 'LiveKit bundle must NOT load on estate boot');
    assert.ok(!trace.fetches.some((u) => String(u).indexOf('livekit-token') >= 0),
      'no token request on boot — it would spin up the on-demand container');
  }

  // ── 2. Entry lazy-loads the bundle, gets a token, publishes the mic ────────
  {
    const { trace, sandbox } = run();
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.deepStrictEqual(trace.scripts, ['/lib/livekit/livekit-client.umd.min.js'],
      'entry must lazy-load the bundle exactly once');
    assert.ok(trace.fetches.some((u) => String(u).indexOf('/api/voice/livekit-token') === 0), 'entry requests a token');
    assert.deepStrictEqual(trace.mic, [true], 'entry publishes the mic track');
    assert.strictEqual(sandbox.window.__zoeConv.live(), true, 'session is live');
  }

  // ── 3. Explicit stop releases the mic and disconnects ─────────────────────
  {
    const { trace, sandbox } = run();
    await sandbox.window.__zoeConv.start();
    await tick();
    sandbox.window.__zoeConv.stop('user');
    await tick();
    assert.deepStrictEqual(trace.mic, [true, false], 'stop must release the mic');
    assert.deepStrictEqual(trace.disconnects, [true], 'stop must disconnect the room');
    assert.strictEqual(sandbox.window.__zoeConv.live(), false, 'session is no longer live');
  }

  // ── 4. Stop is idempotent (double-exit must not throw or double-release) ──
  {
    const { trace, sandbox } = run();
    await sandbox.window.__zoeConv.start();
    await tick();
    sandbox.window.__zoeConv.stop('user');
    sandbox.window.__zoeConv.stop('user');
    await tick();
    assert.deepStrictEqual(trace.mic, [true, false], 'second stop must be a no-op');
    assert.strictEqual(trace.disconnects.length, 1, 'room disconnected exactly once');
  }

  // ── 5. pagehide/beforeunload release the mic (kiosk reload) ───────────────
  for (const evt of ['pagehide', 'beforeunload']) {
    const { trace, sandbox, listeners } = run();
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.ok((listeners[evt] || []).length, evt + ' listener registered');
    listeners[evt].forEach((fn) => fn());
    await tick();
    assert.deepStrictEqual(trace.mic, [true, false], evt + ' must release the mic');
    assert.strictEqual(trace.disconnects.length, 1, evt + ' must disconnect');
  }

  // ── 6. A failed token/bundle leaves NO live session and NO open mic ───────
  {
    const { trace, sandbox } = run({ tokenOk: false });
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.strictEqual(sandbox.window.__zoeConv.live(), false, 'token failure must not leave a live session');
    assert.ok(trace.mic.indexOf(true) < 0, 'token failure must never open the mic');
  }
  {
    const { sandbox, trace } = run({ bundleOk: false });
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.strictEqual(sandbox.window.__zoeConv.live(), false, 'bundle 404 must not leave a live session');
    assert.ok(trace.mic.indexOf(true) < 0, 'bundle 404 must never open the mic');
  }
  // A script that loads but exports no global must also fail cleanly.
  {
    const { sandbox, trace } = run({ bundleOk: 'noglobal' });
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.strictEqual(sandbox.window.__zoeConv.live(), false, 'missing global must not leave a live session');
    assert.ok(trace.mic.indexOf(true) < 0, 'missing global must never open the mic');
  }

  // ── 6b. A failed load must not poison every later attempt ─────────────────
  // The load promise is cached; if a failure path leaves the rejected promise
  // cached, the Talk button is dead until the kiosk is reloaded. Both failure
  // paths (404 and script-loads-but-no-global) must clear the cache so a retry
  // can actually recover.
  for (const firstFailure of [false, 'noglobal']) {
    let attempt = 0;
    const { trace, sandbox } = run({ bundleOk: () => (++attempt === 1 ? firstFailure : true) });
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.strictEqual(sandbox.window.__zoeConv.live(), false, 'first attempt fails (' + firstFailure + ')');

    await sandbox.window.__zoeConv.start();   // retry — must re-fetch and succeed
    await tick();
    assert.strictEqual(trace.scripts.length, 2,
      'retry after a failed load must re-fetch the bundle (' + firstFailure + '), got ' + trace.scripts.length);
    assert.strictEqual(sandbox.window.__zoeConv.live(), true,
      'retry after a failed load must recover (' + firstFailure + ')');
    assert.ok(trace.mic.indexOf(true) >= 0, 'retry publishes the mic (' + firstFailure + ')');
  }

  // ── 7. A live session suppresses idle->sleep AND ambient-return ───────────
  // The estate arms idle-sleep at 180000ms and the drift at 7000ms+. Neither
  // may be pending while Zoe is being talked to: a regression here sleeps or
  // bounces the panel home mid-sentence. (show('ask') during entry arms the
  // sleep clock before the session flag is set — convStart must clear it.)
  {
    const { trace, sandbox } = run();
    // Let the boot's async display-prefs callback land FIRST — it calls
    // armIdleSleep() and would otherwise clear the timer under test, hiding a
    // real regression behind a race that a real panel (prefs long since
    // resolved) would never reproduce.
    await tick();
    await sandbox.window.__zoeConv.start();
    await tick();
    const pending = trace.pending();
    assert.ok(pending.indexOf(180000) < 0,
      'idle->sleep must NOT be pending during a live session (pending: ' + pending + ')');
    assert.ok(!pending.some((ms) => ms >= 7000 && ms <= 30000),
      'ambient-return drift must NOT be pending during a live session (pending: ' + pending + ')');

    // …and the normal idle clock resumes once the session ends.
    trace.timers.length = 0;
    sandbox.window.__zoeConv.stop('user');
    await tick();
    assert.ok(trace.timers.indexOf(180000) >= 0,
      'idle->sleep must be re-armed after the conversation ends (armed: ' + trace.timers + ')');
  }

  // ── 8. INLINE: conversation mode never hides the dock/home/orb ────────────
  {
    const { sandbox, els } = run();
    await sandbox.window.__zoeConv.start();
    await tick();
    assert.ok(!els.dock._cls.has('hide'), 'dock stays visible (inline mode)');
    assert.ok(!els.home._cls.has('hide'), 'home button stays visible (inline mode)');
    assert.ok(!els.orb._cls.has('hide'), 'orb stays visible (inline mode)');
    assert.ok(els.orb._cls.has('listening'), 'orb shows the listening state while live');
  }

  console.log('conversation mode: all checks passed');
  // The estate boot leaves its clock/poll intervals running, which would keep
  // node alive forever — exit explicitly so CI gets a return code.
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
