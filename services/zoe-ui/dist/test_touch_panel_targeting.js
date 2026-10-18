/*
 * Two panels, one house (A11, architecture deep dive 2026-10-18) - no browser, no network.
 *
 * Runs the REAL push handler (connectPushWebSocket) and the REAL panel matchers (collectPanelIdentity, panelMatches,
 * panelMatchesStrict, addressedToThisPanel) from js/touch-ui-executor.js for TWO kiosks, panel-a and panel-b, behind a
 * fake server that delivers exactly like zoe-data push.py: each panel socket is on "all" and on its own panel_<id>
 * channel, and every frame carries the channel it was sent on.
 *
 *   T1 a turn on panel A, sent the way the server sends it now (panel_panel-a): A shows it, B sees nothing.
 *   T2 the same frames on "all" naming panel-a (an older server, or broadcast_to_panel's fallback): A acts, B ignores.
 *   T3 voice:* frames on "all" that name NO panel: neither panel acts (before A11 BOTH did - a turn on SOME panel).
 *   T4 a household-wide ui_action on "all" with no panel id still reaches both (global frames stay broadcast).
 *   T5 negative control: the same T3 run against a copy of the executor whose strict matcher accepts a missing id
 *      (the old rule) shows both panels acting, so T3 can see the leak.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const REAL_SRC = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');

function extractFrom(src, name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, 'missing function ' + name);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error('unbalanced braces for ' + name);
}
function constFrom(src, name) {
  const m = src.match(new RegExp('const ' + name + '\\s*=\\s*(\\d+);'));
  assert(m, 'missing const ' + name);
  return Number(m[1]);
}

// One kiosk: its own state/localStorage/URL, the real functions, a fake WebSocket.
function bootPanel(src, panelId) {
  const sockets = [];
  function FakeWS(url) { this.url = url; this.sent = []; this.readyState = 1; sockets.push(this); }
  FakeWS.prototype.send = function (d) { this.sent.push(d); };
  const seen = { voice: [], actions: [] };
  const store = { zoe_panel_id: panelId, zoe_touch_panel_id: panelId };
  const env = {
    state: { panelId, pushWs: null, pushPing: null, pushRetry: null, pushAttempts: 0, unloading: false, pushPanelId: '' },
    GENERATED_ALIAS_CACHE_KEY: 'zoe_touch_panel_alias_generated',
    PUSH_PING_MS: constFrom(src, 'PUSH_PING_MS'),
    PUSH_RETRY_MAX_MS: constFrom(src, 'PUSH_RETRY_MAX_MS'),
    window: { location: { protocol: 'https:', host: 'zoe.local', search: '?kiosk=1&panel_id=' + panelId }, dispatchEvent() {} },
    document: { dispatchEvent() {} },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    URLSearchParams,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    WebSocket: FakeWS,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    console: { warn() {}, log() {} },
    panelMatchesAuthTarget: () => true,
    setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
    executeAction: (a) => { seen.actions.push(a.action_type); return Promise.resolve({ status: 'success' }); },
    ackAction() {},
    VoiceOverlay: {
      onListeningStarted: () => seen.voice.push('listening'),
      onTranscript: (t) => seen.voice.push('transcript:' + t),
      onThinking: () => seen.voice.push('thinking'),
      onResponding: (t) => seen.voice.push('responding:' + t),
      onDone: () => seen.voice.push('done'),
    },
  };
  const names = Object.keys(env);
  const body = ['isGeneratedPanelAlias', 'isLocalGeneratedPanelAlias', 'collectPanelIdentity', 'panelMatches',
    'panelMatchesStrict', 'addressedToThisPanel', 'schedulePushReconnect', 'connectPushWebSocket']
    .map((n) => extractFrom(src, n)).join('\n');
  const fns = new Function(...names, body + '\nreturn { connectPushWebSocket };')(...names.map((n) => env[n]));
  fns.connectPushWebSocket();
  return { id: panelId, ws: sockets[0], seen, env };
}

// The fake server: push.py semantics (connect_panel = "all" + panel_<id>; every frame names its channel).
function makeHouse(src) {
  const panels = [bootPanel(src, 'panel-a'), bootPanel(src, 'panel-b')];
  const subs = { all: panels.slice() };
  for (const p of panels) {
    subs['panel_' + p.id] = [p];
    p.ws.onmessage({ data: JSON.stringify({ type: 'connected', channel: 'panel_' + p.id, panel_id: p.id }) });
  }
  let seq = 0;
  function send(channel, type, data) {
    for (const p of subs[channel] || []) {
      p.ws.onmessage({ data: JSON.stringify({ type, channel, data, sequence: ++seq }) });
    }
  }
  function turn(channel, panelIdInData) {
    const d = (extra) => Object.assign(panelIdInData ? { panel_id: panelIdInData } : {}, extra || {});
    send(channel, 'voice:listening_started', d());
    send(channel, 'voice:transcript', d({ text: 'what is on my calendar' }));
    send(channel, 'voice:thinking', d());
    send(channel, 'voice:responding', d({ text: 'Dentist at three.' }));
    send(channel, 'ui_action', { action: { id: 'card-1', action_type: 'show_card', payload: { type: 'text' } } });
    send(channel, 'voice:done', d());
  }
  return { a: panels[0], b: panels[1], send, turn };
}

const results = [];
function check(name, fn) { fn(); results.push(name); console.log('  ok  ' + name); }

check('T1 a turn on panel A over its own channel reaches A only', () => {
  const h = makeHouse(REAL_SRC);
  assert.strictEqual(h.a.env.state.pushPanelId, 'panel-a', 'the connected frame records the server-side panel id');
  h.turn('panel_panel-a', 'panel-a');
  assert.deepStrictEqual(h.a.seen.voice,
    ['listening', 'transcript:what is on my calendar', 'thinking', 'responding:Dentist at three.', 'done']);
  assert.deepStrictEqual(h.a.seen.actions, ['show_card'], 'the answer card shows on the panel that asked');
  assert.deepStrictEqual(h.b.seen.voice, [], 'panel B shows nothing of panel A\'s turn');
  assert.deepStrictEqual(h.b.seen.actions, []);
});

check('T2 frames on "all" naming panel A: A acts, B ignores', () => {
  const h = makeHouse(REAL_SRC);
  h.send('all', 'voice:transcript', { panel_id: 'panel-a', text: 'turn on the lights' });
  h.send('all', 'voice:responding', { panel_id: 'panel-a', text: 'Done.' });
  h.send('all', 'ui_action', { panel_id: 'panel-a', action: { id: 'c2', action_type: 'show_card', payload: {} } });
  assert.deepStrictEqual(h.a.seen.voice, ['transcript:turn on the lights', 'responding:Done.']);
  assert.deepStrictEqual(h.a.seen.actions, ['show_card']);
  assert.deepStrictEqual(h.b.seen.voice, []);
  assert.deepStrictEqual(h.b.seen.actions, []);
});

check('T3 voice:* on "all" naming no panel: neither panel acts', () => {
  const h = makeHouse(REAL_SRC);
  h.send('all', 'voice:transcript', { text: 'whose turn is this' });
  h.send('all', 'voice:responding', { text: 'Nobody knows.' });
  h.send('all', 'voice:done', {});
  h.send('all', 'voice:responding', { panel_id: 'unknown', text: 'An unknown panel.' });
  assert.deepStrictEqual(h.a.seen.voice, []);
  assert.deepStrictEqual(h.b.seen.voice, []);
});

check('T4 a household-wide ui_action (no panel id) on "all" still reaches both panels', () => {
  const h = makeHouse(REAL_SRC);
  h.send('all', 'ui_action', { action: { id: 'g1', action_type: 'panel_navigate', payload: { url: '/touch/home.html' } } });
  assert.deepStrictEqual(h.a.seen.actions, ['panel_navigate']);
  assert.deepStrictEqual(h.b.seen.actions, ['panel_navigate']);
});

check('T5 negative control: the old "absent = any panel" rule leaks T3 to both panels', () => {
  const OLD = REAL_SRC.replace(/(function panelMatchesStrict\(targetPanelId\) \{\s*const wanted = String\(targetPanelId \|\| ''\)\.trim\(\);\s*if \(!wanted\) return) false;/,
    '$1 true;');
  assert.notStrictEqual(OLD, REAL_SRC, 'the mutation must apply');
  const h = makeHouse(OLD);
  h.send('all', 'voice:responding', { text: 'Nobody knows.' });
  assert.deepStrictEqual(h.a.seen.voice, ['responding:Nobody knows.']);
  assert.deepStrictEqual(h.b.seen.voice, ['responding:Nobody knows.']);
});

console.log(`panel targeting: ${results.length} checks passed`);
