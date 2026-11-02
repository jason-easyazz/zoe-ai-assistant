/*
 * Panel alarms on the estate (ZOE_ALARMS) - no browser, no network. Runs the REAL js/touch-alarms.js against a DOM shim whose
 * innerHTML setter throws (every server string is textContent), a fake fetch layer and a manual clock.
 *
 * Pins: a ringing alarm takes the sleep page and shows the overlay; TAP ANYWHERE snoozes; the Turn off button is press-and-HOLD
 * 1 s (early release does nothing and never snoozes; a tap on it never snoozes); no snoozes left = tap does nothing; a snooze that
 * could not reach the server silences the panel at once and is retried by every poll (and not re-rung); a ring the server no
 * longer lists is removed; a 404 (flag off) hides the tabs; the editor saves the right body.
 * Negative controls: AL_SRC=<mutated copy> - e.g. HOLD_MS=0 (tap turns off), drop the stopPropagation on the off button (a tap on it
 * snoozes), drop the ack (a failed snooze re-rings) - each turns a named check red.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const SRC = fs.readFileSync(process.env.AL_SRC || path.join(__dirname, 'js/touch-alarms.js'), 'utf8');
const HOME = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

function makeEl(tag, all) {
  const attrs = {};
  const el = {
    tagName: String(tag).toUpperCase(), id: '', className: '', children: [], parentNode: null, _text: '', listeners: {}, style: {}, hidden: false,
    scrollTop: 0, value: '',
    get classList() {
      const cl = () => el.className.split(/\s+/).filter(Boolean);
      return { add: (...c) => { el.className = [...new Set([...cl(), ...c])].join(' '); },
        remove: (...c) => { el.className = cl().filter((x) => !c.includes(x)).join(' '); },
        toggle: (c, f) => { const has = cl().includes(c); const want = f === undefined ? !has : !!f; want ? el.classList.add(c) : el.classList.remove(c); },
        contains: (c) => cl().includes(c) };
    },
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = el; el.children.push(c); all.add(c); return c; },
    removeChild(c) { el.children = el.children.filter((x) => x !== c); c.parentNode = null; return c; },
    addEventListener(t, fn) { (el.listeners[t] = el.listeners[t] || []).push(fn); },
    scrollTo(o) { el.scrollTop = o.top; },
    matches(sel) { return sel.split(',').some((s1) => {
      s1 = s1.trim();
      if (s1[0] === '#') return el.id === s1.slice(1);
      if (s1[0] === '.') return s1.slice(1).split('.').every((c) => el.className.split(/\s+/).includes(c));
      return el.tagName === s1.toUpperCase(); }); },
    closest(sel) { let n = el; while (n && n.matches) { if (n.matches(sel)) return n; n = n.parentNode; } return null; },
    querySelectorAll(sel) {
      const parts = sel.trim().split(/\s+/); const out = [];
      (function walk(n, depth) { n.children.forEach((c) => { const hit = c.matches(parts[depth]);
        if (hit && depth === parts.length - 1) out.push(c); walk(c, hit && depth < parts.length - 1 ? depth + 1 : depth); }); })(el, 0);
      return out; },
    querySelector(sel) { return el.querySelectorAll(sel)[0] || null; },
    get textContent() { return el._text + el.children.map((c) => c.textContent).join(''); },
    set textContent(v) { el._text = String(v); if (v === '' || v == null) el.children = []; },
    set innerHTML(_v) { throw new Error('innerHTML used: server strings must be set with textContent'); },
  };
  return el;
}
function fire(el, type, extra) {
  let stopped = false;
  const ev = Object.assign({ type, target: el, stopPropagation() { stopped = true; } }, extra || {});
  for (let n = el; n && !stopped; n = n.parentNode) { (n.listeners[type] || []).forEach((fn) => fn(Object.assign(ev, { currentTarget: n }))); }
}

function boot(opts) {
  opts = opts || {};
  const all = new Set();
  const body = makeEl('body', all); const stage = makeEl('div', all); stage.id = 'stage'; body.appendChild(stage);
  const byId = () => { const m = {}; all.forEach((e) => { if (e.id) m[e.id] = e; }); return m; };
  const document = { body, createElement: (t) => makeEl(t, all), createTextNode: (t) => { const e = makeEl('#text', all); e._text = String(t); return e; },
    getElementById: (id) => byId()[id] || null, querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s) };
  let t = 0; const timers = [];
  const setTimeoutF = (fn, ms) => { timers.push({ id: ++t, at: now + (ms || 0), fn, every: 0 }); return t; };
  const setIntervalF = (fn, ms) => { timers.push({ id: ++t, at: now + ms, fn, every: ms }); return t; };
  const clearF = (id) => { const i = timers.findIndex((x) => x.id === id); if (i >= 0) timers.splice(i, 1); };
  let now = 1000000;
  function advance(ms) { const end = now + ms; for (;;) { timers.sort((a, b) => a.at - b.at); const n = timers[0];
    if (!n || n.at > end) break; now = n.at; if (n.every) n.at += n.every; else timers.splice(0, 1); n.fn(); } now = end; }
  const calls = []; const shown = []; let beeps = 0; let stops = 0;
  const host = {
    apiGet: (url) => { calls.push(['GET', url]); const r = opts.get ? opts.get(url) : { ringing: [] };
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    apiJson: (m, url, b) => { calls.push([m, url, b]); const r = opts.post ? opts.post(m, url, b) : {};
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    show: (id) => { shown.push(id); }, cur: () => 'home', beep: () => { beeps += 1; }, stopSound: () => { stops += 1; },
  };
  const store = {};
  const window = { localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } } };
  const ctx = vm.createContext({ window, document, setTimeout: setTimeoutF, setInterval: setIntervalF, clearTimeout: clearF, clearInterval: clearF,
    Date: { now: () => now }, console, Promise });
  // `new Date(ms)` is used for the ring clock; keep the real constructor but a fixed now() for skew maths
  ctx.Date = class extends Date { static now() { return now; } };
  vm.runInContext(SRC, ctx);
  return { window, document, host, calls, shown, advance, store, beeps: () => beeps, stops: () => stops, all, byId, stage };
}
const flush = () => new Promise((r) => setImmediate(r));
const ring = (over) => Object.assign({ id: 'a1', label: 'Gym', time_hhmm: '07:00', sound_kind: 'tone', sound_id: 'sunrise', sound_name: 'Sunrise',
  ramp_seconds: 30, snooze_minutes: 9, snooze_count: 0, can_snooze: true, ring_started_at: 100, stops_at: 700 }, over || {});
const posts = (b, suffix) => b.calls.filter((c) => c[0] === 'POST' && c[1].endsWith(suffix));

async function ringing(over, postFn) {
  const b = boot({ get: (u) => (u === '/api/alarms/ringing' ? { now: 1000, ringing: [ring(over)] } : {}), post: postFn });
  b.window.ZoeAlarms.init(b.host);
  b.advance(400); await flush(); await flush();
  return b;
}

(async () => {
  let checks = 0;
  const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1; };

  // pure helpers
  { const p = boot().window.ZoeAlarms.pure;
    ok(p.to24h(12, 5, 'am') === '00:05' && p.to24h(12, 0, 'pm') === '12:00' && p.to24h(7, 30, 'pm') === '19:30' && p.to24h(1, 0, 'am') === '01:00', 'to24h');
    const f = p.from24h('00:05'); ok(f.h12 === 12 && f.mi === 5 && f.mer === 'am', 'from24h midnight');
    ok(p.clockText('19:30') === '7:30 pm', 'clockText');
    ok(p.wheelIndex(-50, 64, 12) === 0 && p.wheelIndex(64 * 5 + 20, 64, 12) === 5 && p.wheelIndex(99999, 64, 12) === 11, 'wheelIndex clamps/rounds');
    ok(p.repeatOf('weekdays', []) === 'weekdays' && p.repeatOf('', [5, 1]) === 'days:1,5' && p.repeatOf('', []) === 'once' && p.repeatOf('', [1, 2, 3, 4, 5, 6, 7]) === 'daily', 'repeatOf');
    ok(JSON.stringify(p.chipsOf('days:1,3')) === '{"preset":"","days":[1,3]}' && p.chipsOf('daily').preset === 'daily', 'chipsOf'); }

  // a ring takes the sleep page, shows the label and the snooze hint, and beeps
  { const b = await ringing();
    const ov = b.byId().alarmRing;
    ok(ov && ov.classList.contains('on'), 'overlay shown');
    ok(b.shown.includes('sleep'), 'navigates to the sleep clock page');
    ok(ov.querySelector('.allabel').textContent === 'Gym', 'label via textContent');
    ok(ov.querySelector('.alhint').textContent === 'Tap anywhere to snooze 9 min', 'hint');
    ok(b.beeps() >= 1, 'audible');
    // TAP ANYWHERE snoozes
    fire(ov.querySelector('.alclock'), 'click');
    await flush();
    ok(posts(b, '/api/alarms/a1/snooze').length === 1, 'tap on the clock snoozes');
    ok(!ov.classList.contains('on') && b.stops() >= 1, 'silenced and hidden at once');
    const n = b.beeps(); b.advance(5000); ok(b.beeps() === n, 'no beeps after snooze'); }

  // tapping the off button never snoozes and never turns off; early release does nothing
  { const b = await ringing();
    const off = b.byId().alarmRing.querySelector('.aloff');
    fire(off, 'pointerdown'); b.advance(500); fire(off, 'pointerup'); fire(off, 'click'); b.advance(2000); await flush();
    ok(posts(b, '/snooze').length === 0, 'a tap/early release on Turn off does not snooze');
    ok(posts(b, '/dismiss').length === 0, 'an early release does not turn off');
    ok(b.byId().alarmRing.classList.contains('on'), 'still ringing'); }

  // holding for 1 s turns off (not at 999 ms)
  { const b = await ringing();
    const off = b.byId().alarmRing.querySelector('.aloff');
    fire(off, 'pointerdown'); ok(off.classList.contains('holding'), 'fill starts');
    b.advance(b.window.ZoeAlarms.HOLD_MS - 1); await flush(); ok(posts(b, '/dismiss').length === 0, 'not at 999 ms');
    b.advance(2); await flush();
    ok(posts(b, '/api/alarms/a1/dismiss').length === 1, 'hold 1 s turns off');
    ok(posts(b, '/snooze').length === 0 && !b.byId().alarmRing.classList.contains('on'), 'off, not snoozed'); }

  // out of snoozes: tap does nothing, hint says hold
  { const b = await ringing({ can_snooze: false, snooze_count: 8 });
    const ov = b.byId().alarmRing;
    ok(ov.querySelector('.alhint').textContent === 'Hold the button to turn off', 'hint without snooze');
    fire(ov, 'click'); await flush();
    ok(posts(b, '/snooze').length === 0 && ov.classList.contains('on'), 'tap does nothing when no snoozes are left'); }

  // a snooze the server did not receive: silenced locally, retried, not re-rung
  { let fail = true;
    const b = await ringing({}, () => (fail ? new Error('http 500') : {}));
    const ov = b.byId().alarmRing;
    fire(ov, 'click'); await flush(); await flush();
    ok(!ov.classList.contains('on'), 'silenced although the request failed');
    ok(Object.keys(JSON.parse(b.store.zoe_alarm_acks)).length === 1, 'ack persisted');
    const before = posts(b, '/snooze').length;
    b.advance(2100); await flush(); await flush();   // next poll: the server still lists it as ringing
    ok(!ov.classList.contains('on'), 'not re-rung from this panel');
    ok(posts(b, '/snooze').length > before, 'the snooze is retried');
    fail = false; b.advance(2100); await flush(); await flush();
    ok(Object.keys(JSON.parse(b.store.zoe_alarm_acks)).length === 0, 'ack cleared once the server confirms'); }

  // a POST still in flight when the next poll lands is not sent twice
  { let release = null;
    const b = await ringing({}, () => new Promise((r) => { release = r; }));
    fire(b.byId().alarmRing, 'click'); await flush();
    b.advance(2100); await flush(); await flush(); b.advance(2100); await flush(); await flush();
    ok(posts(b, '/snooze').length === 1, 'one snooze POST while the first is unresolved: ' + posts(b, '/snooze').length);
    release({}); await flush(); }

  // the flag flips off at runtime: the Alarms tab goes away and the Timers pane comes back
  { let off = false;
    const b = boot({ get: (u) => (off ? new Error('http 404') : u === '/api/alarms' ? { alarms: [], tones: {}, now: 1 } : { now: 1, ringing: [] }) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    const tabs = b.document.createElement('div'); const t1 = b.document.createElement('div'); const a1 = b.document.createElement('div');
    b.window.ZoeAlarms.attachTabs({ tabsEl: tabs, timersEl: t1, alarmsEl: a1 })('alarms'); await flush(); await flush();
    ok(tabs.hidden === false && a1.hidden === false && t1.hidden === true, 'alarms tab showing');
    off = true; b.advance(2100); await flush(); await flush();
    ok(tabs.hidden === true && t1.hidden === false && a1.hidden === true, 'runtime 404 hides the tab and restores Timers'); }

  // acks for rings the server no longer lists are dropped (no unbounded localStorage)
  { let listed = true;
    const b = boot({ get: (u) => (u === '/api/alarms/ringing' ? { now: b0clock(), ringing: listed ? [ring()] : [] } : {}), post: () => new Error('http 500') });
    function b0clock() { return 1000; }
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    fire(b.byId().alarmRing, 'click'); await flush(); await flush();
    ok(Object.keys(JSON.parse(b.store.zoe_alarm_acks)).length === 1, 'ack kept while the server still lists it');
    listed = false; b.advance(2100); await flush(); await flush();
    ok(Object.keys(JSON.parse(b.store.zoe_alarm_acks)).length === 0, 'ack pruned once the server stops listing it'); }

  // the server stops listing it (auto-stop / answered elsewhere): overlay goes
  { let listed = true;
    const b = boot({ get: (u) => (u === '/api/alarms/ringing' ? { now: 1000, ringing: listed ? [ring()] : [] } : {}) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    ok(b.byId().alarmRing.classList.contains('on'), 'rings');
    listed = false; b.advance(2100); await flush(); await flush();
    ok(!b.byId().alarmRing.classList.contains('on') && b.stops() >= 1, 'removed when the server no longer lists it'); }

  // flag off: 404 hides the tabs and slows the poll
  { const b = boot({ get: () => new Error('http 404') });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    ok(b.window.ZoeAlarms._state.enabled === false, 'disabled on 404');
    const n = b.calls.length; b.advance(299000); await flush(); ok(b.calls.length === n, 'does not poll again before the 5 min recheck');
    b.advance(2000); await flush(); await flush(); ok(b.calls.length === n + 1, 'rechecks once after 5 min');
    const tabs = b.document.createElement('div'); const t1 = b.document.createElement('div'); const a1 = b.document.createElement('div');
    b.window.ZoeAlarms.attachTabs({ tabsEl: tabs, timersEl: t1, alarmsEl: a1 });
    ok(tabs.hidden === true, 'tabs hidden when the server has no alarms'); }

  // tabs appear when enabled, and the editor saves the right body
  { const b = boot({ get: (u) => (u === '/api/alarms' ? { alarms: [], tones: { sunrise: 'Sunrise', bell: 'Bell' }, now: 1 } : { now: 1, ringing: [] }) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    const tabs = b.document.createElement('div'); const t1 = b.document.createElement('div'); const a1 = b.document.createElement('div');
    const setTab = b.window.ZoeAlarms.attachTabs({ tabsEl: tabs, timersEl: t1, alarmsEl: a1 });
    ok(tabs.hidden === false && tabs.children.length === 2, 'tabs shown');
    setTab('alarms'); await flush(); await flush();
    ok(t1.hidden === true && a1.hidden === false, 'Alarms tab swaps the panes');
    const neu = a1.querySelectorAll('.alchip').find((c) => c.textContent === '+ New alarm'); ok(!!neu, 'new alarm button');
    fire(neu, 'click');
    const chips = a1.querySelectorAll('.alchip');
    fire(chips.find((c) => c.textContent === 'Weekdays'), 'click');
    fire(a1.querySelectorAll('.alchip').find((c) => c.textContent === 'Bell'), 'click');
    fire(a1.querySelectorAll('.alchip').find((c) => c.textContent === '15 min'), 'click');
    fire(a1.querySelectorAll('.alchip').find((c) => c.textContent === 'Save'), 'click'); await flush();
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && JSON.stringify(p[2]) === JSON.stringify({ time_hhmm: '07:00', repeat: 'weekdays', snooze_minutes: 15, sound_kind: 'tone', sound_id: 'bell' }), 'save body: ' + JSON.stringify(p && p[2])); }

  // the estate wires it: script tag before the inline code, init with the host's api, tabs on the timer screen
  ok(HOME.includes('<script src="/js/touch-alarms.js?v=al-1"></script>'), 'home.html loads the module');
  ok(HOME.includes('ZoeAlarms.init({apiGet:apiGet,apiJson:apiJson,show:show') && HOME.includes('stopSound:function(){try{if(_tAlarm.ctx)'), 'home.html inits it with the estate api AND a real stopSound');
  ok(HOME.includes('ZoeAlarms.attachTabs({tabsEl:document.getElementById(\'alTabs\')'), 'timer screen attaches the tabs');
  ok(HOME.indexOf('/js/touch-alarms.js') < HOME.indexOf('function wireTimer'), 'module loads before the wiring runs');

  console.log(`${checks} checks passed`);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
