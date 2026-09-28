/*
 * Controlled test for the panel's voice-text lifecycle (no browser, no network).
 *
 * Two live bugs (2026-09-28, zoe-touch-pi):
 *   1. "let's talk" turns showed NO text. Their replies are chat turns, and
 *      chat text reaches the panel ONLY over the /ws/push socket. The executor's
 *      socket sent one ping on open and never reconnected, so the server's 120 s
 *      idle timeout (or any zoe-data restart) left the kiosk deaf. Regular turns
 *      still "worked" because domain commands arrive by a DB-queued, POLLED
 *      panel_navigate (?heard=&say=), which also reloads the page.
 *   2. After a regular turn the answer never went away. The daemon re-opens the
 *      mic for a follow-up window and /api/voice/wake fires voice:listening_started;
 *      the estate's listening() CANCELLED the drift home, and an unanswered
 *      window ends silently, so nothing ever re-armed it.
 *
 * Part A runs the REAL estate IIFE from touch/home.html (same extraction as
 * test_touch_conversation_mode.js) under a fake clock and drives
 * window.ZoeEstateVoice exactly as touch-ui-executor.js does.
 * Part B runs the REAL connectPushWebSocket/schedulePushReconnect from
 * js/touch-ui-executor.js against a fake WebSocket.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');
const execSrc = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');

// ── Fake clock: timers only run when the test advances time ─────────────────
function makeClock() {
  let now = 1790600000000;
  let seq = 0;
  const timers = new Map();
  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }
  return {
    Date: FakeDate,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return ++seq; },   // boot pollers/clocks: inert here
    clearInterval() {},
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
      }
      now = end;
    },
  };
}

// ── Part A: the estate (touch/home.html) ─────────────────────────────────────
function estateScript() {
  const anchor = html.indexOf('function askShell(');
  assert(anchor >= 0, 'estate script anchor (askShell) not found');
  const start = html.lastIndexOf('(function(){', anchor);
  const end = html.indexOf('</script>', anchor);
  assert(start >= 0 && end > start, 'estate IIFE not found');
  return html.slice(start, end);
}

function makeEl(id) {
  const cls = new Set();
  return {
    id, style: {}, dataset: {}, children: [], _text: '', _html: '', _attrs: {}, disabled: false,
    classList: {
      add: (c) => cls.add(c), remove: (...c) => c.forEach((x) => cls.delete(x)),
      toggle: (c, on) => (on === undefined ? (cls.has(c) ? cls.delete(c) : cls.add(c)) : (on ? cls.add(c) : cls.delete(c))),
      contains: (c) => cls.has(c),
    },
    setAttribute(k, v) { this._attrs[k] = v; }, getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; },
    addEventListener() {}, removeEventListener() {}, appendChild(c) { this.children.push(c); return c; }, remove() {},
    querySelectorAll() { return []; }, querySelector() { return null; }, closest() { return null; }, focus() {},
    get textContent() { return this._text; }, set textContent(v) { this._text = v; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; },
    getContext: () => new Proxy({}, { get: () => () => ({ addColorStop() {} }) }),
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 1280, height: 720 }),
  };
}

function bootEstate() {
  const clock = makeClock();
  const els = {};
  const document = {
    getElementById: (i) => (els[i] || (els[i] = makeEl(i))),
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, createElement: (t) => makeEl(t),
    head: makeEl('head'), body: makeEl('body'),
  };
  const store = new Map();
  const window = {
    addEventListener() {},
    location: { search: '?panel_id=zoe-touch-pi&kiosk=1', pathname: '/touch/home.html' },
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) },
  };
  const sandbox = {
    document, window, console,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), body: null }),
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    Date: clock.Date, URLSearchParams, TextDecoder, Promise, Math, JSON, isNaN, parseInt, parseFloat, encodeURIComponent,
    history: { replaceState() {} },
    navigator: { mediaDevices: { getUserMedia: () => Promise.resolve({}) } },
    Audio: function () { return { play: () => Promise.resolve(), pause() {}, addEventListener() {} }; },
    localStorage: window.localStorage, location: window.location,
  };
  sandbox.window.document = document;
  sandbox.globalThis = sandbox;
  vm.runInContext(estateScript(), vm.createContext(sandbox), { timeout: 5000 });
  const V = sandbox.window.ZoeEstateVoice;
  assert(V, 'window.ZoeEstateVoice must exist');
  return {
    V, clock,
    onAsk: () => els.full.innerHTML.indexOf('talkBtn') >= 0,
    answer: () => (els.askOut ? els.askOut.textContent : ''),
  };
}

// A regular brain/chat turn as the push socket delivers it (touch-ui-executor
// maps voice:* and show_card onto these calls). Starts from wherever we are.
function chatTurn(e, heard, reply, doneData) {
  e.V.transcript(heard);
  e.V.thinking();
  e.V.responding(reply);
  e.V.done(doneData || null);
  e.clock.advance(1300);   // the estate defers a chat answer 1200 ms before showing Ask
}

const HOLD = 10000;
const CONV_HOLD = 30000;

(async () => {
  // A1. Bug 2 — the answer goes away ~10 s after an unanswered follow-up window.
  {
    const e = bootEstate();
    e.V.listening();                                  // "Hey Zoe"
    chatTurn(e, 'what is a quokka', 'A small wallaby.');
    assert(e.onAsk(), 'the chat answer is shown on the Ask surface');
    assert.strictEqual(e.answer(), 'A small wallaby.');
    e.clock.advance(3000);                            // Zoe is still speaking
    e.V.listening();                                  // daemon follow-up window opens…
    e.clock.advance(HOLD - 500);
    assert(e.onAsk(), 'held while a follow-up could still come');
    e.clock.advance(1000);                            // …and closes silently
    assert(!e.onAsk(), 'the answer must auto-dismiss ~10 s after the follow-up window opens (was: forever)');
  }

  // A2. A new turn resets the clock; the new answer gets its own dismissal.
  {
    const e = bootEstate();
    chatTurn(e, 'first question', 'First answer.');
    e.V.listening();
    e.clock.advance(HOLD - 2000);
    e.V.transcript('second question');                // the user DID follow up
    e.clock.advance(HOLD);                            // longer than the old hold
    assert(e.onAsk(), 'a follow-up turn in progress must not be dismissed under it');
    e.V.thinking();
    e.V.responding('Second answer.');
    e.V.done(null);
    assert.strictEqual(e.answer(), 'Second answer.');
    e.V.listening();
    e.clock.advance(HOLD + 500);
    assert(!e.onAsk(), 'the follow-up answer dismisses on its own clock');
  }

  // A3. "Let's talk": the text stays up for the whole conversation, turn by turn.
  {
    const e = bootEstate();
    e.V.listening();
    chatTurn(e, "Let's talk", "I'm listening.", { panel_id: 'zoe-touch-pi', conversation_mode: true });
    assert(e.onAsk(), 'the opener ack is shown');
    assert.strictEqual(e.answer(), "I'm listening.");
    e.V.listening();                                  // conversation window 1 (12 s on the daemon)
    e.clock.advance(HOLD + 5000);                     // past the regular hold: a regular turn would be gone
    assert(e.onAsk(), 'inside an open conversation the text must NOT drift between turns');
    e.V.transcript("I've been going to Perth on the weekends");
    e.V.thinking();
    e.V.responding('That sounds like a lot.');
    e.V.done(null);                                   // a mid-conversation turn carries no flag
    assert.strictEqual(e.answer(), 'That sounds like a lot.', 'each conversation turn shows its reply');
    e.V.listening();                                  // window 2 — silent
    e.clock.advance(12000);
    e.V.listening();                                  // window 3 — silent, the daemon then closes
    e.clock.advance(CONV_HOLD - 1000);
    assert(e.onAsk(), 'held for the conversation hold after the last listen window');
    e.clock.advance(2000);
    assert(!e.onAsk(), 'a conversation that ended in silence is dismissed after the hold');

    // …and the conversation state lapsed with it: the next regular turn is back
    // to the short hold.
    chatTurn(e, 'what time is it', 'Nine.');
    e.V.listening();
    e.clock.advance(HOLD + 500);
    assert(!e.onAsk(), 'after the conversation lapses, a regular turn uses the regular hold');
  }

  // A4. An ender closes the conversation; its ack gets the normal reading dwell.
  {
    const e = bootEstate();
    chatTurn(e, "Let's talk", 'Of course.', { conversation_mode: true });
    e.V.listening();
    e.V.transcript("that's all");
    e.V.responding('Okay.');                          // already on Ask: shown at once
    e.V.done({ conversation_end: true });
    assert.strictEqual(e.answer(), 'Okay.');
    e.clock.advance(7000 + 'Okay.'.length * 110 + 200);
    assert(!e.onAsk(), 'the ender ack dismisses on the normal dwell, not the conversation hold');
  }

  // A5. Voice on the home face never navigates by itself.
  {
    const e = bootEstate();
    e.V.listening();
    e.clock.advance(HOLD + 1000);
    assert(!e.onAsk(), 'a wake with nothing said leaves the home face alone');
  }

  // ── Part B: the executor's push socket (js/touch-ui-executor.js) ──────────
  function extract(name) {
    const start = execSrc.indexOf('function ' + name + '(');
    assert(start >= 0, 'missing function ' + name);
    let depth = 0;
    for (let j = execSrc.indexOf('{', start); j < execSrc.length; j++) {
      if (execSrc[j] === '{') depth++;
      else if (execSrc[j] === '}' && --depth === 0) return execSrc.slice(start, j + 1);
    }
    throw new Error('unbalanced braces for ' + name);
  }
  function constValue(name) {
    const m = execSrc.match(new RegExp('const ' + name + '\\s*=\\s*(\\d+);'));
    assert(m, 'missing const ' + name);
    return Number(m[1]);
  }

  function bootSocket() {
    const clock = makeClock();
    const intervals = new Map();
    let iseq = 0;
    const sockets = [];
    function FakeWS(url) { this.url = url; this.sent = []; this.readyState = 1; sockets.push(this); }
    FakeWS.prototype.send = function (d) { this.sent.push(d); };
    const done = [];
    const env = {
      state: { panelId: 'zoe-touch-pi', pushWs: null, pushPing: null, pushRetry: null, pushAttempts: 0, unloading: false },
      PUSH_PING_MS: constValue('PUSH_PING_MS'),
      PUSH_RETRY_MAX_MS: constValue('PUSH_RETRY_MAX_MS'),
      window: { location: { protocol: 'https:', host: 'zoe.local' } },
      WebSocket: FakeWS,
      setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
      setInterval: (fn, ms) => { const id = ++iseq; intervals.set(id, { fn, ms }); return id; },
      clearInterval: (id) => intervals.delete(id),
      console: { warn() {}, log() {} },
      panelMatches: () => true, panelMatchesAuthTarget: () => true,
      setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
      VoiceOverlay: { onListeningStarted() {}, onTranscript() {}, onThinking() {}, onResponding() {}, onDone: (d) => done.push(d) },
    };
    const names = Object.keys(env);
    const fns = new Function(...names,
      extract('schedulePushReconnect') + '\n' + extract('connectPushWebSocket') +
      '\nreturn { connectPushWebSocket, schedulePushReconnect };')(...names.map((n) => env[n]));
    return { fns, env, clock, intervals, sockets, done };
  }

  // B1. Keepalive: a ping every PUSH_PING_MS, well inside the server's 120 s idle timeout.
  {
    const s = bootSocket();
    assert(s.env.PUSH_PING_MS > 0 && s.env.PUSH_PING_MS < 120000, 'ping interval must beat the 120 s idle timeout');
    s.fns.connectPushWebSocket();
    const ws = s.sockets[0];
    assert.strictEqual(ws.url, 'wss://zoe.local/ws/push?panel_id=zoe-touch-pi');
    ws.onopen();
    assert.deepStrictEqual(ws.sent, ['ping']);
    const pings = [...s.intervals.values()].filter((i) => i.ms === s.env.PUSH_PING_MS);
    assert.strictEqual(pings.length, 1, 'exactly one keepalive interval (was: none — the socket died after 120 s)');
    pings[0].fn(); pings[0].fn();
    assert.deepStrictEqual(ws.sent, ['ping', 'ping', 'ping']);
  }

  // B2. A closed socket reconnects (idle timeout, zoe-data restart), with capped backoff.
  {
    const s = bootSocket();
    s.fns.connectPushWebSocket();
    s.sockets[0].onopen();
    s.sockets[0].onclose();
    assert.strictEqual(s.intervals.size, 0, 'the dead socket\'s keepalive is cleared');
    s.clock.advance(1999);
    assert.strictEqual(s.sockets.length, 1, 'first retry waits the backoff');
    s.clock.advance(1);
    assert.strictEqual(s.sockets.length, 2, 'a closed push socket must reconnect (was: never)');
    // The server stays down: the backoff grows, and is capped.
    let prev = 2000;
    for (let k = 0; k < 8; k++) {
      s.sockets[s.sockets.length - 1].onclose();
      const want = Math.min(prev * 2, s.env.PUSH_RETRY_MAX_MS);
      const before = s.sockets.length;
      s.clock.advance(want - 1);
      assert.strictEqual(s.sockets.length, before, 'backoff step ' + k + ' waits ' + want + ' ms');
      s.clock.advance(1);
      assert.strictEqual(s.sockets.length, before + 1, 'backoff step ' + k + ' reconnects');
      prev = want;
    }
    assert.strictEqual(prev, s.env.PUSH_RETRY_MAX_MS, 'the backoff is capped');
    // Back up: a successful open resets the backoff.
    const live = s.sockets[s.sockets.length - 1];
    live.onopen();
    live.onclose();
    const n = s.sockets.length;
    s.clock.advance(2000);
    assert.strictEqual(s.sockets.length, n + 1, 'an open resets the backoff to its first step');
  }

  // B3. A superseded socket's late close, or a page unload, never spawns a reconnect.
  {
    const s = bootSocket();
    s.fns.connectPushWebSocket();
    const first = s.sockets[0];
    first.onclose();
    s.clock.advance(2000);
    const second = s.sockets[1];
    second.onopen();
    first.onclose();                                  // stale event from the dead socket
    s.clock.advance(60000);
    assert.strictEqual(s.sockets.length, 2, 'a stale close must not open a third socket');
    s.env.state.unloading = true;
    second.onclose();
    s.clock.advance(60000);
    assert.strictEqual(s.sockets.length, 2, 'no reconnect while the page unloads');
  }

  // B4. voice:done carries the conversation flags through to the estate.
  {
    const s = bootSocket();
    s.fns.connectPushWebSocket();
    const data = { panel_id: 'zoe-touch-pi', conversation_mode: true };
    s.sockets[0].onmessage({ data: JSON.stringify({ type: 'voice:done', data }) });
    assert.deepStrictEqual(s.done, [data], 'voice:done must hand its payload to VoiceOverlay.onDone');
  }
  assert(/function onDone\(data\)[\s\S]{0,200}E\.done\(data/.test(execSrc),
    'VoiceOverlay.onDone must pass the payload to ZoeEstateVoice.done');

  console.log('voice text lifecycle: all checks passed');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
