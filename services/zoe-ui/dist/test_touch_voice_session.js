/*
 * Panel voice session on the estate (ZOE_VOICE_SESSION) — no browser, no network.
 *
 * Part A runs the REAL connectPushWebSocket from js/touch-ui-executor.js against a fake WebSocket: a
 *   voice_session / voice_card frame for THIS panel is relayed as a window event; one with no panel_id or another
 *   panel's id is dropped (unlike voice:*, absent is not "any panel": the frame carries one member's data); voice:*
 *   handling is unchanged.
 * Part B runs the REAL js/touch-voice-session.js against a DOM shim whose innerHTML setter throws (rows are set with
 *   textContent only): the "<name> · voice" badge, the private card with the real rows, a card for another member
 *   ignored, everything private gone on close, the privacy-safe card, and the PIN-path card fading on its own.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const execSrc = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');
const vsSrc = fs.readFileSync(path.join(__dirname, 'js/touch-voice-session.js'), 'utf8');
const homeHtml = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

function makeClock() {
  let now = 0; let seq = 0; const timers = new Map();
  return {
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
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
  const done = [];
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
    // The real matcher's rule for a KNOWN identity: only this panel's ids match; '' matches anything.
    panelMatches: (id) => !String(id || '').trim() || String(id).trim() === 'zoe-touch-pi',
    panelMatchesStrict: (id) => String(id || '').trim() === 'zoe-touch-pi',
    addressedToThisPanel: () => false,
    panelMatchesAuthTarget: () => true,
    setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
    VoiceOverlay: { onListeningStarted() {}, onTranscript() {}, onThinking() {}, onResponding() {}, onDone: (d) => done.push(d) },
  };
  const names = Object.keys(env);
  const fns = new Function(...names,
    extract('schedulePushReconnect') + '\n' + extract('connectPushWebSocket') +
    '\nreturn { connectPushWebSocket };')(...names.map((n) => env[n]));
  fns.connectPushWebSocket();
  const ws = sockets[0];
  const send = (type, data) => ws.onmessage({ data: JSON.stringify({ type, data }) });

  send('voice_session', { panel_id: 'zoe-touch-pi', state: 'open', member_id: 'casey', display_name: 'Casey' });
  assert.strictEqual(relayed.length, 1);
  assert.strictEqual(relayed[0].type, 'zoe:voice-session');
  assert.strictEqual(relayed[0].detail.member_id, 'casey');
  send('voice_card', { panel_id: 'zoe-touch-pi', member_id: 'casey', kind: 'calendar', private: true, items: [] });
  assert.strictEqual(relayed.length, 2);
  assert.strictEqual(relayed[1].type, 'zoe:voice-card');
  // Absent panel_id: dropped (since A11, 2026-10-18, a voice:* frame without one is dropped too — checked below).
  send('voice_card', { member_id: 'casey', kind: 'calendar', private: true, items: [{ text: 'Dentist' }] });
  send('voice_card', { panel_id: 'kitchen-panel', member_id: 'casey', kind: 'calendar', private: true, items: [] });
  send('voice_session', { panel_id: 'kitchen-panel', state: 'open', member_id: 'dana' });
  assert.strictEqual(relayed.length, 2, 'a private frame without THIS panel\'s id must never be relayed');
  send('voice:done', { text: 'ok' });
  assert.strictEqual(done.length, 0, 'a voice:* frame that names no panel is a turn on SOME panel: never acted on (A11)');
  send('voice:done', { panel_id: 'kitchen-panel', text: 'ok' });
  assert.strictEqual(done.length, 0, 'another panel\'s voice:* frame is ignored');
  send('voice:done', { panel_id: 'zoe-touch-pi', text: 'ok' });
  assert.strictEqual(done.length, 1, 'this panel\'s voice:* frame is handled');
}

// ── Part B: the estate badge + cards ─────────────────────────────────────────
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
      get textContent() { return el._text + el.children.map((c) => c.textContent).join(''); },
      set textContent(v) { el._text = String(v); },
      set innerHTML(_v) { throw new Error('innerHTML used: rows must be set with textContent'); },
    };
    return el;
  }
  const head = makeEl('head'); const body = makeEl('body');
  const document = {
    head, body, documentElement: head,
    createElement: makeEl,
    getElementById: (id) => [...all].find((e) => e.id === id) || null,
  };
  return { document, all };
}

function bootVs() {
  const clock = makeClock();
  const { document } = makeDom();
  const listeners = {};
  const window = { addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); } };
  const ctx = { window, document, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, String, Array };
  vm.createContext(ctx);
  vm.runInContext(vsSrc, ctx);
  const fire = (type, detail) => (listeners[type] || []).forEach((fn) => fn({ detail }));
  const byId = (id) => document.getElementById(id);
  return { fire, byId, clock, window };
}

{
  const s = bootVs();
  const P = 'zoe-touch-pi';
  s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey', badge: 'voice' });
  assert.strictEqual(s.byId('zvs-badge').textContent, 'Casey · voice');
  s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'calendar', private: true, title: 'Your calendar',
    subtitle: 'today', items: [{ text: 'Dentist', meta: '9 AM' }, { text: '<img src=x onerror=alert(1)>', meta: '' }] });
  const card = s.byId('zvs-card');
  assert(card, 'private card shown inside the session');
  assert(card.textContent.includes('Dentist') && card.textContent.includes('9 AM'));
  assert(card.textContent.includes('<img src=x onerror=alert(1)>'), 'markup in a row is text, never HTML');
  assert.strictEqual(card.getAttribute('data-member'), 'casey');
  // A card for ANOTHER member while casey's session is open is never shown.
  s.fire('zoe:voice-card', { panel_id: P, member_id: 'dana', kind: 'lists', private: true, title: 'x', items: [{ text: 'secret' }] });
  assert(!s.byId('zvs-card').textContent.includes('secret'));
  // The session stays as long as it is renewed: the card does not fade on its own.
  s.clock.advance(10 * 60 * 1000);
  assert(s.byId('zvs-card'), 'a private card under a session lives as long as the session');
  // Close: badge AND every private card go.
  s.fire('zoe:voice-session', { panel_id: P, state: 'closed', member_id: 'casey', reason: 'unverified_voice' });
  assert.strictEqual(s.byId('zvs-badge'), null);
  assert.strictEqual(s.byId('zvs-card'), null, 'private rows must disappear when the session closes');
  assert.strictEqual(s.window.ZoeVoiceSession.state().open, false);
}

{
  const s = bootVs();
  const P = 'zoe-touch-pi';
  // The privacy-safe card: body only, no rows, and it fades.
  s.fire('zoe:voice-card', { panel_id: P, kind: 'locked', private: false, title: 'This is private',
    body: "Say it again so I know it's you, or unlock with your PIN.", items: [{ text: 'leak' }] });
  const lc = s.byId('zvs-card');
  assert(lc && lc.textContent.includes('Say it again') && !lc.textContent.includes('leak'));
  s.clock.advance(20001);
  assert.strictEqual(s.byId('zvs-card'), null, 'the locked card is a hint, not a fixture');
  // A private card without a member id is never shown.
  s.fire('zoe:voice-card', { panel_id: P, kind: 'calendar', private: true, items: [{ text: 'x' }] });
  assert.strictEqual(s.byId('zvs-card'), null);
  // PIN path (no voice session): the card shows, then fades on its own.
  s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'reminders', private: true, title: 'Your reminders', items: [] , empty: 'No reminders set.' });
  assert(s.byId('zvs-card').textContent.includes('No reminders set.'));
  s.clock.advance(90001);
  assert.strictEqual(s.byId('zvs-card'), null);
  // The card can land a moment BEFORE its session's open frame: it then lives as long as the session, not 90 s.
  s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'calendar', private: true, title: 'c', items: [{ text: 'Dentist' }] });
  s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
  s.clock.advance(5 * 60 * 1000);
  assert(s.byId('zvs-card'), 'an early card must not fade while its session is open');
  s.fire('zoe:voice-session', { panel_id: P, state: 'closed', member_id: 'casey', reason: 'timeout' });
  assert.strictEqual(s.byId('zvs-card'), null);
  // A locked card is cleared when the member's session opens.
  s.fire('zoe:voice-card', { panel_id: P, kind: 'locked', private: false, title: 't', body: 'b' });
  s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
  assert.strictEqual(s.byId('zvs-card'), null);
}

// The estate loads the script, before the executor that relays to it.
{
  const vsAt = homeHtml.indexOf('/js/touch-voice-session.js');
  const exAt = homeHtml.indexOf('/js/touch-ui-executor.js');
  assert(vsAt > 0 && exAt > vsAt, 'home.html must load touch-voice-session.js (before the executor)');
}

console.log('voice session: all checks passed');
