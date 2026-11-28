/*
 * The estate stays fresh, and a pushed voice navigate is acknowledged (no browser, no network).
 *
 * Found on a real panel (2026-11-26): a kiosk page that had been open for 16 days ran fewer than half of the voice
 * navigations pushed to it, and the server could not tell - it wrote the navigate `success` on DELIVERY, and the page
 * never acked a pushed navigate at all (the frame carries the row id as `action_id`, the push handler only read `id`).
 *
 * Part A runs the REAL FRESH block of touch/home.html (between its FRESH-BEGIN / FRESH-END markers) with a fake clock:
 *   A1 due only when a kiosk page is a day old and idle on the night clock or home
 *   A2 each reason not to (viewer, young page, recent self-reload, unreadable stamp, other surface, talking, overlay,
 *      timers, recent touch/voice) blocks it on its own
 *   A3 the tick asks for the page first, stamps localStorage, keeps only panel_id + kiosk, reloads once
 *   A4 an unreachable server, a refused page, a stamp that cannot be written, or a wake during the probe = no reload
 *   A5 the interval is armed for a kiosk only
 *   A6 negative control: the same due state run against a copy whose gate ignores `talking` reloads mid-turn,
 *      so A2 can see that guard
 * Part B runs the REAL push handler of js/touch-ui-executor.js:
 *   B1 a pushed panel_navigate carrying only action_id is acked under that id
 *   B2 every other pushed action without an id keeps its local push_ id and is not acked
 *   B3 negative control: the handler without the new line never acks the navigate
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');
const execSrc = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');

const results = [];
function check(name, fn) { fn(); results.push(name); console.log('  ok  ' + name); }

// ── Part A ──────────────────────────────────────────────────────────────────
const b0 = html.indexOf('// FRESH-BEGIN'), b1 = html.indexOf('// FRESH-END');
assert(b0 > 0 && b1 > b0, 'FRESH block markers missing from touch/home.html');
const FRESH_SRC = html.slice(b0, b1);
const H = 3600000;

function bootFresh(src, o) {
  o = o || {};
  const w = {
    now: 1800000000000, intervals: [], fetches: [], reloads: 0, replaced: [], store: Object.assign({}, o.store),
    probe: o.probe || (() => Promise.resolve({ ok: true })), storeThrows: o.storeThrows || null,
    dom: Object.assign({ orb: [], overlay: false }, o.dom),
    est: Object.assign({ cur: 'sleep', convLive: false, voiceConv: false, known: {}, pending: {} }, o.est),
    search: o.search === undefined ? '?panel_id=panel-x&kiosk=1' : o.search,
  };
  const env = {
    Date: { now: () => w.now },
    window: { __kiosk: o.kiosk === undefined ? true : o.kiosk, ZoeVoiceSession: { state: () => ({ open: !!w.est.session }) } },
    document: {
      getElementById: (id) => (id === 'orb' ? { classList: { contains: (c) => w.dom.orb.includes(c) } } : null),
      querySelector: (sel) => { w.selector = sel; return w.dom.overlay ? {} : null; },
    },
    localStorage: {
      getItem: (k) => { if (w.storeThrows === 'get') throw new Error('denied'); return k in w.store ? w.store[k] : null; },
      setItem: (k, v) => { if (w.storeThrows === 'set') throw new Error('denied'); w.store[k] = String(v); },
    },
    location: { get search() { return w.search; }, pathname: '/touch/home.html', reload: () => { w.reloads++; } },
    history: { replaceState: (_s, _t, url) => { w.replaced.push(url); } },
    URLSearchParams,
    setInterval: (fn, ms) => { w.intervals.push({ fn, ms }); return w.intervals.length; },
    zfetch: (url, opts, ms) => { w.fetches.push({ url, opts, ms }); return w.probe(); },
    voiceConvOpen: () => w.est.voiceConv,
    estate: w.est,
  };
  const names = Object.keys(env);
  // The block reads estate variables that live elsewhere in the page's script: give it the same names.
  const pre = 'var _cur, _convLive, _tAlarm, IDLE_SLEEP_MS=180000;\n'
    + 'function _sync(){_cur=estate.cur;_convLive=estate.convLive;_tAlarm={known:estate.known,pending:estate.pending};}\n_sync();\n';
  const post = '\nreturn { freshDue, freshState, freshTick, sync:_sync, touch:function(){_lastActiveAt=Date.now();},'
    + ' consts:{AGE:FRESH_AGE_MS,QUIET:FRESH_QUIET_MS,TICK:FRESH_TICK_MS,KEY:FRESH_KEY} };';
  const api = new Function(...names, pre + src + post)(...names.map((n) => env[n]));
  return { w, api, age(ms) { w.now += ms; }, set(p) { Object.assign(w.est, p); api.sync(); } };
}
const flush = () => new Promise((r) => setImmediate(r));

check('A1 a day-old kiosk page resting on the night clock or home is due', () => {
  const p = bootFresh(FRESH_SRC);
  assert.strictEqual(p.api.consts.AGE, 20 * H, 'about once a day');
  assert.strictEqual(p.api.freshDue(p.api.freshState()), false, 'a page that just loaded is not due');
  p.age(19 * H);
  assert.strictEqual(p.api.freshDue(p.api.freshState()), false, '19 h is not yet');
  p.age(1 * H + 1000);
  assert.strictEqual(p.api.freshDue(p.api.freshState()), true, 'asleep at 20 h');
  p.set({ cur: 'home' });
  assert.strictEqual(p.api.freshDue(p.api.freshState()), true, 'home at 20 h');
  assert(/function armIdleSleep\(ms\)\{if\(!ms\)_lastActiveAt=Date\.now\(\);/.test(html), 'touch, voice and navigation (a bare armIdleSleep) stamp the last activity');
  for (const sel of ['#estModal.on', '#cmdbar.on', '#authov.on', '.apin.on', '#cardwrap.on', '#tmrAlarm.on', '#alarmRing.on']) {
    assert(p.w.selector.split(',').includes(sel), 'the overlay check covers ' + sel);
  }
});

check('A2 every reason not to reload blocks it on its own', () => {
  const due = (o, after) => { const p = bootFresh(FRESH_SRC, o); p.age(21 * H); if (after) after(p); return p.api.freshDue(p.api.freshState()); };
  assert.strictEqual(due(), true, 'baseline is due');
  assert.strictEqual(due({ kiosk: false }), false, 'a member\'s laptop or phone (viewer) never reloads itself');
  assert.strictEqual(due({ kiosk: 'yes' }), false, 'only a real kiosk flag counts');
  assert.strictEqual(due({}, (p) => { p.w.store[p.api.consts.KEY] = String(p.w.now - 2 * H); }), false, 'a self-reload 2 h ago');
  assert.strictEqual(due({}, (p) => { p.w.store[p.api.consts.KEY] = String(p.w.now - 21 * H); }), true, 'a self-reload 21 h ago does not block');
  assert.strictEqual(due({}, (p) => { p.w.store[p.api.consts.KEY] = 'garbage'; }), false, 'an unreadable stamp');
  assert.strictEqual(due({ storeThrows: 'get' }), false, 'storage that throws');
  for (const cur of ['music', 'weather', 'ask', 'timer', 'list', null]) {
    assert.strictEqual(due({ est: { cur } }), false, 'surface ' + cur);
  }
  assert.strictEqual(due({ est: { convLive: true } }), false, 'a live conversation');
  assert.strictEqual(due({ est: { voiceConv: true } }), false, 'an open conversation window');
  assert.strictEqual(due({ est: { session: true } }), false, 'a verified voice session');
  assert.strictEqual(due({ dom: { orb: ['listening'] } }), false, 'the mic is open');
  assert.strictEqual(due({ dom: { orb: ['busy'] } }), false, 'Zoe is answering');
  assert.strictEqual(due({ dom: { overlay: true } }), false, 'a card, modal, sign-in or ringing alarm is up');
  assert.strictEqual(due({ est: { known: { t1: { exp: 1 } } } }), false, 'a timer is counting');
  assert.strictEqual(due({ est: { pending: { t1: 1 } } }), false, 'a timer alarm awaits Dismiss');
  assert.strictEqual(due({}, (p) => { p.api.touch(); p.age(60000); }), false, 'a touch or voice turn a minute ago');
  assert.strictEqual(due({}, (p) => { p.api.touch(); p.age(181000); }), true, 'three quiet minutes later it is due again');
});

(async () => {
  {
    const p = bootFresh(FRESH_SRC, { search: '?heard=x&panel_id=panel-x&kiosk=1&conv=1' });
    p.api.freshTick();
    assert.strictEqual(p.w.fetches.length, 0, 'a young page does not even ask');
    p.age(21 * H);
    p.api.freshTick(); p.api.freshTick();
    assert.strictEqual(p.w.fetches.length, 1, 'one probe at a time');
    assert.strictEqual(p.w.fetches[0].url, '/touch/home.html');
    assert.strictEqual(p.w.fetches[0].opts.method, 'HEAD');
    assert.strictEqual(p.w.reloads, 0, 'nothing reloads before the page has answered');
    await flush();
    assert.strictEqual(p.w.reloads, 1, 'reloaded once');
    assert.deepStrictEqual(p.w.replaced, ['/touch/home.html?panel_id=panel-x&kiosk=1'], 'only panel_id + kiosk survive');
    assert.strictEqual(p.w.store[p.api.consts.KEY], String(p.w.now), 'the stamp survives the reload in localStorage');
    p.api.freshTick(); await flush();
    assert.strictEqual(p.w.reloads, 1, 'and the stamp stops a second one');
    results.push('A3'); console.log('  ok  A3 the tick probes, stamps, keeps panel_id + kiosk and reloads once');
  }
  {
    const run = async (o, during) => { const p = bootFresh(FRESH_SRC, o); p.age(21 * H); p.api.freshTick(); if (during) during(p); await flush(); return p; };
    assert.strictEqual((await run({ probe: () => Promise.reject(new Error('offline')) })).w.reloads, 0, 'server unreachable');
    assert.strictEqual((await run({ probe: () => Promise.resolve({ ok: false, status: 502 }) })).w.reloads, 0, 'page refused');
    const blocked = await run({ storeThrows: 'set' });
    assert.strictEqual(blocked.w.reloads, 0, 'a stamp that cannot be written = no reload (it could loop)');
    const woke = await run({}, (p) => p.set({ cur: 'weather' }));
    assert.strictEqual(woke.w.reloads, 0, 'woken while the page was being asked for');
    assert.strictEqual(woke.w.store[woke.api.consts.KEY], undefined, 'and no stamp is spent on a reload that did not happen');
    const again = await run({ probe: () => Promise.reject(new Error('offline')) });
    again.w.probe = () => Promise.resolve({ ok: true });
    again.api.freshTick(); await flush();
    assert.strictEqual(again.w.reloads, 1, 'a failed probe is retried on the next tick');
    results.push('A4'); console.log('  ok  A4 unreachable / refused / unwritable stamp / woken mid-probe = no reload');
  }
  check('A5 the tick is armed for a kiosk only', () => {
    const k = bootFresh(FRESH_SRC);
    assert.deepStrictEqual(k.w.intervals.map((i) => i.ms), [k.api.consts.TICK]);
    assert(k.api.consts.TICK >= 60000, 'a slow tick');
    assert.deepStrictEqual(bootFresh(FRESH_SRC, { kiosk: false }).w.intervals, [], 'a viewer arms nothing');
  });
  check('A6 negative control: without the talking guard the page reloads mid-turn', () => {
    const BAD = FRESH_SRC.replace('if(s.talking||s.overlay||s.timers)return false;', 'if(s.overlay||s.timers)return false;');
    assert.notStrictEqual(BAD, FRESH_SRC, 'the control must actually change the gate');
    const p = bootFresh(BAD, { est: { convLive: true } }); p.age(21 * H);
    assert.strictEqual(p.api.freshDue(p.api.freshState()), true);
  });

  // ── Part B ────────────────────────────────────────────────────────────────
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
  const constFrom = (src, name) => Number(src.match(new RegExp('const ' + name + '\\s*=\\s*(\\d+);'))[1]);
  function bootPanel(src) {
    const sockets = [];
    function FakeWS(url) { this.url = url; this.sent = []; this.readyState = 1; sockets.push(this); }
    FakeWS.prototype.send = function (d) { this.sent.push(d); };
    const seen = { ran: [], acked: [] };
    const store = { zoe_panel_id: 'panel-x', zoe_touch_panel_id: 'panel-x' };
    const env = {
      state: { panelId: 'panel-x', pushWs: null, pushPing: null, pushRetry: null, pushAttempts: 0, unloading: false, pushPanelId: '' },
      GENERATED_ALIAS_CACHE_KEY: 'zoe_touch_panel_alias_generated',
      PUSH_PING_MS: constFrom(src, 'PUSH_PING_MS'), PUSH_RETRY_MAX_MS: constFrom(src, 'PUSH_RETRY_MAX_MS'),
      window: { location: { protocol: 'https:', host: 'zoe.local', search: '?kiosk=1&panel_id=panel-x' }, dispatchEvent() {} },
      document: { dispatchEvent() {} },
      localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
      URLSearchParams, CustomEvent: class { constructor(t) { this.type = t; } }, WebSocket: FakeWS,
      setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, console: { warn() {}, log() {} },
      panelMatchesAuthTarget: () => true, setOrbMode() {}, showAmbientStatus: undefined, _attemptVoiceNavigation() {},
      executeAction: (a) => { seen.ran.push(a.action_type + ':' + a.id); return Promise.resolve({ status: 'success' }); },
      ackAction: (id, r) => { seen.acked.push(id + ':' + r.status); },
      VoiceOverlay: { onListeningStarted() {}, onTranscript() {}, onThinking() {}, onResponding() {}, onDone() {} },
    };
    const names = Object.keys(env);
    const body = ['isGeneratedPanelAlias', 'isLocalGeneratedPanelAlias', 'collectPanelIdentity', 'panelMatches',
      'panelMatchesStrict', 'addressedToThisPanel', 'schedulePushReconnect', 'connectPushWebSocket']
      .map((n) => extractFrom(src, n)).join('\n');
    new Function(...names, body + '\nreturn { connectPushWebSocket };')(...names.map((n) => env[n])).connectPushWebSocket();
    const ws = sockets[0];
    ws.onmessage({ data: JSON.stringify({ type: 'connected', channel: 'panel_panel-x', panel_id: 'panel-x' }) });
    return { seen, push: (data) => ws.onmessage({ data: JSON.stringify({ type: 'ui_action', channel: 'panel_panel-x', data }) }) };
  }
  // Exactly the frame voice_tts._broadcast_skybridge_ui sends: ui_orchestrator's message (action_id, no id) + payload.
  const NAV = { action_id: 'row0000000000001', user_id: 'u', panel_id: 'panel-x', action_type: 'panel_navigate', status: 'queued',
    payload: { url: '/touch/home.html?domain=weather', label: 'Showing on the panel', panel_id: 'panel-x', source: 'voice:skybridge' } };
  {
    const p = bootPanel(execSrc);
    p.push(JSON.parse(JSON.stringify(NAV)));
    p.push(Object.assign(JSON.parse(JSON.stringify(NAV)), { action_id: 'row0000000000002', action_type: 'panel_navigate_fullscreen' }));
    await flush();
    assert.deepStrictEqual(p.seen.ran, ['panel_navigate:row0000000000001', 'panel_navigate_fullscreen:row0000000000002']);
    assert.deepStrictEqual(p.seen.acked, ['row0000000000001:success', 'row0000000000002:success'], 'the page acks the row the server wrote');
    results.push('B1'); console.log('  ok  B1 a pushed navigate carrying only action_id is acked under it');
  }
  {
    const p = bootPanel(execSrc);
    p.push({ action_id: 'row0000000000003', panel_id: 'panel-x', action_type: 'show_card', payload: {} });
    p.push({ panel_id: 'panel-x', action_type: 'panel_navigate', payload: { url: '/touch/home.html' } });
    await flush();
    assert.strictEqual(p.seen.ran.length, 2);
    assert(p.seen.ran.every((r) => r.split(':')[1].startsWith('push_')), 'both keep a local push_ id: ' + p.seen.ran.join(' '));
    assert.deepStrictEqual(p.seen.acked, [], 'and neither is acked from the push (the poll owns a queued row)');
    results.push('B2'); console.log('  ok  B2 other pushed actions, and a navigate with no row id, are unchanged');
  }
  {
    const line = execSrc.split('\n').find((l) => l.includes('action.action_id') && l.includes("'panel_navigate_fullscreen'") && l.includes('action.id = String('));
    assert(line, 'the ack line is in the push handler');
    const OLD = execSrc.replace(line + '\n', '');
    const p = bootPanel(OLD);
    p.push(JSON.parse(JSON.stringify(NAV)));
    await flush();
    assert.strictEqual(p.seen.ran.length, 1, 'the old handler still ran it');
    assert.deepStrictEqual(p.seen.acked, [], 'and never acked it - the server could not tell');
    results.push('B3'); console.log('  ok  B3 negative control: the old handler never acks a pushed navigate');
  }
  console.log('\n' + results.length + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
