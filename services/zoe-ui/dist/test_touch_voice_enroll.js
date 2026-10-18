/*
 * Voice enrolment on the estate's talk screen ("Zoe, learn my voice") - no browser, no network.
 *
 * Part A runs the REAL connectPushWebSocket from js/touch-ui-executor.js against a fake WebSocket: a voice_enroll frame that names THIS
 *   panel is relayed as a zoe:voice-enroll window event; one with no panel_id, or another panel's, is dropped (the frame is a private
 *   moment on one wall, so absent is not "any panel").
 * Part B runs the REAL js/touch-voice-enroll.js against a DOM shim whose innerHTML setter throws (text is set with textContent only) and
 *   whose storage / cookie access throws (nothing persists): the phrase as large text, the "2 / 5" counter and dots, the "recording"
 *   state with the phrase still up, the tick on accept, the gentle re-ask, the saving and closing lines, and EVERY way it goes away - a
 *   clear frame (abort / timeout / shutdown), the closing line expiring, the watchdog when the daemon dies mid-run, and a tap.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const execSrc = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');
const veSrc = fs.readFileSync(path.join(__dirname, 'js/touch-voice-enroll.js'), 'utf8');
const homeHtml = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

function makeClock() {
  let now = 0; let seq = 0; const timers = new Map();
  return {
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    pending() { return timers.size; },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]); now = next[1].at; next[1].fn();
      }
      now = end;
    },
  };
}

// ── Part A: the executor relay ────────────────────────────────────────────────
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

{
  const clock = makeClock();
  const sockets = [];
  function FakeWS(url) { this.url = url; this.sent = []; this.readyState = 1; sockets.push(this); }
  FakeWS.prototype.send = function (d) { this.sent.push(d); };
  const relayed = [];
  class CE { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
  const env = {
    state: { panelId: 'zoe-touch-pi', pushWs: null, pushPing: null, pushRetry: null, pushAttempts: 0, unloading: false },
    PUSH_PING_MS: constValue('PUSH_PING_MS'),
    PUSH_RETRY_MAX_MS: constValue('PUSH_RETRY_MAX_MS'),
    window: { location: { protocol: 'https:', host: 'zoe.local' }, dispatchEvent: (e) => relayed.push(e) },
    CustomEvent: CE,
    WebSocket: FakeWS,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: () => 1, clearInterval() {},
    console: { warn() {}, log() {} },
    panelMatches: (id) => !String(id || '').trim() || String(id).trim() === 'zoe-touch-pi',
    panelMatchesStrict: (id) => String(id || '').trim() === 'zoe-touch-pi',
    addressedToThisPanel: () => false,
    panelMatchesAuthTarget: () => true,
    setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
    VoiceOverlay: { onListeningStarted() {}, onTranscript() {}, onThinking() {}, onResponding() {}, onDone() {} },
  };
  const names = Object.keys(env);
  const fns = new Function(...names,
    extract('schedulePushReconnect') + '\n' + extract('connectPushWebSocket') +
    '\nreturn { connectPushWebSocket };')(...names.map((n) => env[n]));
  fns.connectPushWebSocket();
  const ws = sockets[0];
  const send = (type, data) => ws.onmessage({ data: JSON.stringify({ type, data }) });

  send('voice_enroll', { panel_id: 'zoe-touch-pi', state: 'phrase', phrase: 'Add milk.', index: 1, total: 5, line: 'Say:' });
  assert.strictEqual(relayed.length, 1);
  assert.strictEqual(relayed[0].type, 'zoe:voice-enroll');
  assert.strictEqual(relayed[0].detail.phrase, 'Add milk.');
  assert.strictEqual(relayed[0].detail.index, 1);
  send('voice_enroll', { panel_id: 'zoe-touch-pi', state: 'clear' });
  assert.strictEqual(relayed.length, 2, 'a clear is relayed too');
  // No panel_id, or another panel: dropped.
  send('voice_enroll', { state: 'phrase', phrase: 'Add milk.', index: 1, total: 5 });
  send('voice_enroll', { panel_id: 'kitchen-panel', state: 'phrase', phrase: 'Add milk.', index: 1, total: 5 });
  assert.strictEqual(relayed.length, 2, 'an enrolment frame that does not name THIS panel must never be relayed');
}

// ── Part B: the talk-screen overlay ───────────────────────────────────────────
function makeDom() {
  const all = new Set();
  function makeEl(tag) {
    const attrs = {};
    const el = {
      tagName: tag, id: '', className: '', children: [], parentNode: null, _text: '', listeners: {},
      setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
      appendChild(c) { c.parentNode = el; el.children.push(c); all.add(c); return c; },
      removeChild(c) { el.children = el.children.filter((x) => x !== c); c.parentNode = null; all.delete(c); return c; },
      addEventListener(t, fn) { (el.listeners[t] = el.listeners[t] || []).push(fn); },
      get firstChild() { return el.children[0] || null; },
      get textContent() { return el._text + el.children.map((c) => c.textContent).join(''); },
      set textContent(v) { el._text = String(v); },
      set innerHTML(_v) { throw new Error('innerHTML used: text must be set with textContent'); },
    };
    return el;
  }
  const head = makeEl('head'); const body = makeEl('body');
  const document = {
    head, body, documentElement: head,
    createElement: makeEl,
    getElementById: (id) => [...all].find((e) => e.id === id) || null,
    get cookie() { throw new Error('cookie read: nothing may persist'); },
    set cookie(_v) { throw new Error('cookie write: nothing may persist'); },
  };
  return { document, all };
}

function boot() {
  const clock = makeClock();
  const { document } = makeDom();
  const listeners = {};
  const trap = () => { throw new Error('browser storage touched: nothing may persist'); };
  const storage = { getItem: trap, setItem: trap, removeItem: trap, clear: trap };
  const window = { addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); }, localStorage: storage, sessionStorage: storage };
  const ctx = {
    window, document, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, String, Array, Math, Number, isFinite,
    get localStorage() { return trap(); }, get sessionStorage() { return trap(); }, get indexedDB() { return trap(); },
  };
  vm.createContext(ctx);
  // browser storage must be unreachable: any access (even inside a try/catch) is recorded, and the harness fails on it
  vm.runInContext("globalThis.__storageHits = 0; for (const n of ['localStorage','sessionStorage','indexedDB']) Object.defineProperty(globalThis, n, { get() { globalThis.__storageHits++; throw new Error('storage touched'); } });", ctx);
  vm.runInContext(veSrc, ctx);
  const fire = (detail) => (listeners['zoe:voice-enroll'] || []).forEach((fn) => fn({ detail }));
  const storageHits = () => vm.runInContext('globalThis.__storageHits', ctx);
  const root = () => document.getElementById('zve');
  const part = (cls) => {
    const r = root(); if (!r) return null;
    return r.children.find((c) => c.className === cls || c.className.split(' ').includes(cls)) || null;
  };
  const dots = () => { const d = part('zve-dots'); return d ? d.children.map((c) => c.className) : []; };
  return { fire, root, part, dots, clock, window, storageHits };
}

const P = 'zoe-touch-pi';
const W = ['Hey Zoe, what is the weather looking like today?', 'Add milk, bread, and coffee.', 'Remind me to call my mum tomorrow afternoon.',
  'Turn the living room lights down to half.', 'Play some quiet music in the kitchen.'];

// The happy path, step by step.
{
  const s = boot();
  assert.strictEqual(s.root(), null, 'nothing on screen before a frame');
  s.fire({ panel_id: P, state: 'phrase', phrase: W[1], index: 2, total: 5, line: 'Say:' });
  assert(s.root(), 'the overlay is up');
  assert.strictEqual(s.part('zve-line').textContent, 'Say:');
  assert.strictEqual(s.part('zve-phrase').textContent, W[1], 'the phrase to say, as text');
  assert.strictEqual(s.part('zve-count').textContent, '2 / 5', 'progress: 2 of 5');
  assert.deepStrictEqual(s.dots(), ['zve-dot done', 'zve-dot cur', 'zve-dot', 'zve-dot', 'zve-dot']);
  assert.strictEqual(s.part('zve-mark').textContent, '');
  assert.strictEqual(s.root().getAttribute('data-state'), 'phrase');
  assert.strictEqual(s.root().getAttribute('role'), 'status');

  // recording: the SAME phrase stays up (so it can be read off the screen while speaking) and the state says so
  s.fire({ panel_id: P, state: 'recording', phrase: W[1], index: 2, total: 5, line: 'Recording...' });
  assert.strictEqual(s.root().getAttribute('data-state'), 'recording');
  assert.strictEqual(s.part('zve-line').textContent, 'Recording...');
  assert.strictEqual(s.part('zve-phrase').textContent, W[1], 'the phrase stays up while recording');
  assert.strictEqual(s.part('zve-count').textContent, '2 / 5');
  s.clock.advance(60000);
  assert(s.root(), 'a long recording + prompt does not time the screen out (watchdog is longer)');

  // accepted: a tick, and the clip counts as done
  s.fire({ panel_id: P, state: 'accepted', phrase: W[1], index: 2, total: 5 });
  assert.strictEqual(s.part('zve-mark').textContent, '\u2713', 'a tick when the clip is accepted');
  assert.deepStrictEqual(s.dots(), ['zve-dot done', 'zve-dot done', 'zve-dot', 'zve-dot', 'zve-dot']);

  // the next phrase replaces it
  s.fire({ panel_id: P, state: 'phrase', phrase: W[2], index: 3, total: 5, line: 'Say:' });
  assert.strictEqual(s.part('zve-phrase').textContent, W[2]);
  assert.strictEqual(s.part('zve-count').textContent, '3 / 5');
  assert.strictEqual(s.part('zve-mark').textContent, '', 'the tick is gone for the next clip');

  // saving: no phrase, every dot done, counter from the last frame
  s.fire({ panel_id: P, state: 'saving', line: 'Saving...' });
  assert.strictEqual(s.part('zve-line').textContent, 'Saving...');
  assert.strictEqual(s.part('zve-phrase').textContent, '');
  assert.deepStrictEqual(s.dots(), Array(5).fill('zve-dot done'));

  // done: tick + line, and it removes itself after a few seconds, never before the line can be read
  s.fire({ panel_id: P, state: 'done', line: "Done - I'll recognise your voice" });
  assert.strictEqual(s.part('zve-line').textContent, "Done - I'll recognise your voice");
  assert.strictEqual(s.part('zve-mark').textContent, '\u2713');
  assert.strictEqual(s.part('zve-phrase').textContent, '');
  s.clock.advance(7000);
  assert(s.root(), 'the closing line is readable for several seconds');
  s.clock.advance(1100);
  assert.strictEqual(s.root(), null, 'the closing line clears itself');
  assert.strictEqual(s.clock.pending(), 0, 'no timer left behind');
  assert.strictEqual(s.window.ZoeVoiceEnroll.state().visible, false);
}

// The gentle re-ask.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'reask', phrase: W[3], index: 4, total: 5, line: "That one sounded different - let's try it again" });
  assert.strictEqual(s.root().getAttribute('data-state'), 'reask');
  assert.strictEqual(s.part('zve-line').textContent, "That one sounded different - let's try it again");
  assert.strictEqual(s.part('zve-phrase').textContent, W[3], 'the re-ask shows the phrase to say again');
  assert.strictEqual(s.part('zve-count').textContent, '4 / 5');
  assert.deepStrictEqual(s.dots(), ['zve-dot done', 'zve-dot done', 'zve-dot done', 'zve-dot cur', 'zve-dot']);
  assert.strictEqual(s.part('zve-mark').textContent, '', 'no tick on a re-ask');
}

// A failure: the line, no dots, no tick, and it goes by itself.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'phrase', phrase: W[0], index: 1, total: 5, line: 'Say:' });
  s.fire({ panel_id: P, state: 'failed', line: "That didn't work - nothing was kept" });
  assert.strictEqual(s.part('zve-line').textContent, "That didn't work - nothing was kept");
  assert.strictEqual(s.part('zve-mark').textContent, '');
  assert.deepStrictEqual(s.dots(), []);
  assert.strictEqual(s.part('zve-phrase').textContent, '');
  s.clock.advance(8001);
  assert.strictEqual(s.root(), null);
}

// ABORT: a clear removes the phrase AT ONCE, from any state, and leaves no timer to resurrect anything.
for (const state of ['phrase', 'recording', 'accepted', 'reask', 'saving']) {
  const s = boot();
  s.fire({ panel_id: P, state, phrase: W[0], index: 1, total: 5, line: 'x' });
  assert(s.root(), state + ': up');
  s.fire({ panel_id: P, state: 'clear' });
  assert.strictEqual(s.root(), null, 'clear while ' + state + ' removes the overlay immediately');
  assert.strictEqual(s.window.ZoeVoiceEnroll.state().visible, false);
  assert.strictEqual(s.clock.pending(), 0, state + ': the watchdog timer is cancelled too');
  s.clock.advance(120000);
  assert.strictEqual(s.root(), null, 'nothing comes back later');
}
// ... and a clear when nothing is up is harmless.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'clear' });
  assert.strictEqual(s.root(), null);
}

// WATCHDOG: the daemon died mid-run (no clear ever arrives) - the wall must not keep the phrase.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'recording', phrase: W[0], index: 1, total: 5, line: 'Recording...' });
  s.clock.advance(74000);
  assert(s.root(), 'still within the allowed gap between frames');
  s.clock.advance(1100);
  assert.strictEqual(s.root(), null, 'a stale phrase never stays on the wall');
  // A newer frame restarts the watchdog rather than letting the first one expire the overlay.
  s.fire({ panel_id: P, state: 'phrase', phrase: W[0], index: 1, total: 5, line: 'Say:' });
  s.clock.advance(50000);
  s.fire({ panel_id: P, state: 'recording', phrase: W[0], index: 1, total: 5, line: 'Recording...' });
  s.clock.advance(50000);
  assert(s.root(), 'each frame re-arms the watchdog');
}

// TAP: hides it (a stuck overlay can never lock the wall); the next frame brings it back.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'phrase', phrase: W[0], index: 1, total: 5, line: 'Say:' });
  s.root().listeners.click.forEach((fn) => fn({}));
  assert.strictEqual(s.root(), null);
  s.fire({ panel_id: P, state: 'recording', phrase: W[0], index: 1, total: 5, line: 'Recording...' });
  assert(s.root());
}

// Text is text; unknown states and silly numbers are ignored / clamped; nothing is stored (storage + cookie traps throw).
{
  const s = boot();
  s.fire({ panel_id: P, state: 'phrase', phrase: '<img src=x onerror=alert(1)>', index: 1, total: 5, line: '<b>Say</b>' });
  assert.strictEqual(s.part('zve-phrase').textContent, '<img src=x onerror=alert(1)>', 'markup in a phrase is text, never HTML');
  assert.strictEqual(s.part('zve-line').textContent, '<b>Say</b>');
  s.fire({ panel_id: P, state: 'bogus', phrase: 'nope', index: 1, total: 5 });
  assert.strictEqual(s.part('zve-phrase').textContent, '<img src=x onerror=alert(1)>', 'an unknown state changes nothing');
  s.fire({ panel_id: P, state: 'phrase', phrase: 'p', index: 9999, total: 9999, line: 'Say:' });
  assert(s.dots().length <= 12, 'the dot row is bounded');
  s.fire({ panel_id: P, state: 'phrase', phrase: 'p', index: 'two', total: null });
  assert.deepStrictEqual(s.dots(), []);
  assert.strictEqual(s.part('zve-count').textContent, '');
  s.fire({ panel_id: P, state: 'phrase', phrase: 'no line', index: 1, total: 3 });
  assert.strictEqual(s.part('zve-line').textContent, 'Say:', 'a missing line falls back to a plain "Say:"');
  s.fire(undefined);
  s.fire(null);
  s.fire({ panel_id: P, state: 'clear' });
  assert.strictEqual(s.storageHits(), 0, 'nothing may touch localStorage / sessionStorage / indexedDB');
}

// Privacy: the overlay only ever shows what the frame carries, and a frame never carries a member.
{
  const s = boot();
  s.fire({ panel_id: P, state: 'phrase', phrase: W[1], index: 2, total: 5, line: 'Say:' });
  const text = s.root().textContent;
  assert(!/casey|dana|member/i.test(text));
  assert.strictEqual(text, 'Say:' + W[1] + '2 / 5');
}

// The estate loads the script (before the executor that relays to it), cache-busted.
{
  const veAt = homeHtml.indexOf('/js/touch-voice-enroll.js?v=');
  const exAt = homeHtml.indexOf('/js/touch-ui-executor.js');
  assert(veAt > 0 && exAt > veAt, 'home.html must load touch-voice-enroll.js?v=... before the executor');
  assert(/touch-ui-executor\.js\?v=estate-(?!8")/.test(homeHtml), 'the executor changed: its ?v= must be bumped past estate-8');
}

console.log('voice enrol screen: all checks passed');
