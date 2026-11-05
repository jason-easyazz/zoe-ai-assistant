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
process.env.TZ = 'UTC';  // the editor's day maths reads the panel's local clock: pin it so the run is the same on any host
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
  const calls = []; const shown = [];
  const snd = { starts: [], stops: 0, previews: [], start(id, el, ramp) { snd.starts.push({ id, el, ramp }); return true; },
    stop() { snd.stops += 1; }, preview(id) { snd.previews.push(id); } };
  const host = {
    apiGet: (url) => { calls.push(['GET', url]); const r = opts.get ? opts.get(url) : { ringing: [] };
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    apiJson: (m, url, b) => { calls.push([m, url, b]); const r = opts.post ? opts.post(m, url, b) : {};
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    show: (id) => { shown.push(id); }, cur: () => 'home', sound: snd,
  };
  const store = {};
  const window = { localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } } };
  const ctx = vm.createContext({ window, document, setTimeout: setTimeoutF, setInterval: setIntervalF, clearTimeout: clearF, clearInterval: clearF,
    Date: { now: () => now }, console, Promise });
  // `new Date(ms)` is used for the ring clock; keep the real constructor but a fixed now() for skew maths
  ctx.Date = class extends Date { static now() { return now; } };
  vm.runInContext(SRC, ctx);
  return { window, document, host, calls, shown, advance, clock: () => now, store, snd, all, byId, stage };
}
const flush = () => new Promise((r) => setImmediate(r));
const ring = (over) => Object.assign({ id: 'a1', label: 'Gym', time_hhmm: '07:00', sound_kind: 'tone', sound_id: 'sunrise', sound_name: 'Sunrise',
  ramp_seconds: 30, snooze_minutes: 9, snooze_count: 0, can_snooze: true, ring_started_at: 1000, stops_at: 1600, music_state: '' }, over || {});
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

  // a ring takes the sleep page, shows the label and the snooze hint, and plays its tone (ramped from the server's ring start)
  { const b = await ringing();
    const ov = b.byId().alarmRing;
    ok(ov && ov.classList.contains('on'), 'overlay shown');
    ok(b.shown.includes('sleep'), 'navigates to the sleep clock page');
    ok(ov.querySelector('.allabel').textContent === 'Gym', 'label via textContent');
    ok(ov.querySelector('.alhint').textContent === 'Tap anywhere to snooze 9 min', 'hint');
    ok(b.snd.starts.length === 1 && b.snd.starts[0].id === 'sunrise' && b.snd.starts[0].ramp === 30, 'audible: the alarm\'s tone, with its ramp');
    b.advance(5000); const el = b.snd.starts[0].el(); ok(el > 4 && el < 6, 'elapsed is measured from the server ring start: ' + el);
    // TAP ANYWHERE snoozes
    fire(ov.querySelector('.alclock'), 'click');
    await flush();
    ok(posts(b, '/api/alarms/a1/snooze').length === 1, 'tap on the clock snoozes');
    ok(!ov.classList.contains('on') && b.snd.stops >= 1, 'silenced and hidden at once');
    const n = b.snd.starts.length; b.advance(5000); ok(b.snd.starts.length === n, 'no sound after snooze'); }

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

  // the server goes away mid-ring (zoe-data restart, DB down): the panel still stops itself past stops_at, never rings on and on
  { let down = false;
    const b = boot({ get: (u) => (down ? new Error('http 503') : u === '/api/alarms/ringing' ? { now: 1000, ringing: [ring()] } : {}) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    const ov = b.byId().alarmRing; ok(ov.classList.contains('on'), 'ringing before the outage');
    down = true; b.advance(590 * 1000); await flush(); ok(ov.classList.contains('on'), 'still ringing inside the limit');
    b.advance(20 * 1000); await flush();
    ok(!ov.classList.contains('on') && b.snd.stops >= 1, 'stopped itself past stops_at with the server unreachable'); }
  // a ringing row whose limit has already passed (the server's auto-stop tick is failing) is never rung
  for (const sa of [990, 998]) {  // 998: inside the 5 s stop grace, still past stops_at - the grace never STARTS a ring
    const b = await ringing({ stops_at: sa });
    ok(!(b.byId().alarmRing && b.byId().alarmRing.classList.contains('on')) && b.snd.starts.length === 0, 'an overdue ring is not started (stops_at ' + sa + ')'); }

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
    ok(!b.byId().alarmRing.classList.contains('on') && b.snd.stops >= 1, 'removed when the server no longer lists it'); }

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
    // the editor is a full-stage sheet on #stage (above the dock), not inside the pane
    const stg = b.document.getElementById('stage');
    ok(stg.querySelectorAll('.aled').length === 1 && a1.querySelectorAll('.aled').length === 0, 'editor opens on the stage');
    const chips = stg.querySelectorAll('.alchip');
    fire(chips.find((c) => c.textContent === 'Weekdays'), 'click');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === 'Bell'), 'click');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === '15 min'), 'click');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === 'Save'), 'click'); await flush();
    ok(stg.querySelectorAll('.aled').length === 0, 'a save closes the sheet');
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && JSON.stringify(p[2]) === JSON.stringify({ time_hhmm: '07:00', repeat: 'weekdays', snooze_minutes: 15, sound_kind: 'tone', sound_id: 'bell' }), 'save body: ' + JSON.stringify(p && p[2])); }


  // AN ALARM IS NEVER SILENT: music is trusted only while the server says it is playing
  async function musicRing(stateFn, over) {
    let b = null;  // the fake server's clock IS the fake page clock (skew 0), so "10 s after the ring started" is real
    b = boot({ get: (u) => (u === '/api/alarms/ringing' ? { now: b.clock() / 1000, ringing: [ring(Object.assign({ sound_kind: 'music', sound_id: 'last', music_state: stateFn() }, over))] } : {}) });
    b.window.ZoeAlarms.init(b.host); b.advance(300); await flush(); await flush();
    return b;
  }
  { let state = 'playing'; const b = await musicRing(() => state);
    b.advance(20000); await flush(); await flush();
    ok(b.snd.starts.length === 0, 'music playing: no tone over it'); }
  { const b = await musicRing(() => 'starting');
    b.advance(8000); await flush(); await flush(); ok(b.snd.starts.length === 0, 'still waiting for music at 8 s');
    b.advance(4000); await flush(); await flush();
    ok(b.snd.starts.length === 1 && b.snd.starts[0].id === 'sunrise', 'music not playing by the 10 s deadline: the tone plays'); }
  { const b = await musicRing(() => 'failed');
    ok(b.snd.starts.length === 1 && b.snd.starts[0].id === 'sunrise', 'music failed: the tone plays at once'); }
  { const b = await musicRing(() => '');
    b.advance(12000); await flush(); await flush();
    ok(b.snd.starts.length === 1, 'unknown music state (zoe-data restarted mid-ring): the tone plays after the deadline'); }
  { let state = 'failed'; const b = await musicRing(() => state);
    state = 'playing'; b.advance(2100); await flush(); await flush();
    ok(b.snd.starts.length === 1 && b.snd.stops === 0, 'late music never silences the fallback tone (the server fails it past the deadline)'); }

  // SIMPLER EDITOR + ONE-OFF ON A DAY (owner 2026-11-06): time, repeat and (for Once) the day show; the rest waits behind More options
  async function editorOpen(clockMs, alarmsRows, tz) {
    const b = boot({ get: (u) => (u === '/api/alarms' ? { alarms: alarmsRows || [], tones: { sunrise: 'Sunrise', bell: 'Bell' }, now: 1, tz: tz || 'UTC' } : { now: clockMs / 1000, ringing: [] }) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    const tabs = b.document.createElement('div'); const t1 = b.document.createElement('div'); const a1 = b.document.createElement('div');
    b.window.ZoeAlarms.attachTabs({ tabsEl: tabs, timersEl: t1, alarmsEl: a1 })('alarms'); await flush(); await flush();
    return { b, a1, stg: b.document.getElementById('stage') };
  }
  const chipT = (root, txt) => root.querySelectorAll('.alchip').find((c) => c.textContent === txt);
  { const { b, a1, stg } = await editorOpen(1000000);
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    const more = stg.querySelector('.almore'), box = stg.querySelector('.almorebox');
    ok(more && box && box.hidden === true && /More options/.test(more.textContent), 'sound/music/snooze start collapsed behind More options');
    fire(more, 'click'); ok(box.hidden === false && more.textContent === 'Fewer options', 'More options opens them');
    const dayRow = stg.querySelectorAll('.alrow').find((r) => r.children[0].textContent === 'Day');
    ok(dayRow && dayRow.hidden === false, 'Once (the default) shows the day row');
    const strip = dayRow.querySelectorAll('.alchip').map((c) => c.textContent);
    ok(strip[0] === 'Today' && strip[1] === 'Tomorrow' && strip.length === 15 && strip[14] === 'Pick a date', 'Today, Tomorrow, two weeks and Pick a date: ' + strip.join('|'));
    ok(dayRow.querySelectorAll('.alchip.on').map((c) => c.textContent).join() === 'Today', 'a 7:00 alarm with 7:00 still ahead defaults to Today');
    fire(chipT(stg, 'Weekdays'), 'click'); ok(dayRow.hidden === true, 'a repeat hides the day row');
    fire(chipT(stg, 'Once'), 'click'); fire(chipT(dayRow, 'Tomorrow'), 'click');
    fire(chipT(stg, 'Save'), 'click'); await flush();
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && p[2].repeat === 'once' && p[2].once_date === '1970-01-02', 'Tomorrow sends once_date: ' + JSON.stringify(p && p[2])); }
  // Pick a date: a month grid, past days untappable, a later date sent and shown first in the strip
  { const { b, a1, stg } = await editorOpen(1000000 + 9 * 86400000);  // the panel clock: 10 Jan 1970
    b.advance(9 * 86400000); await flush();
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    fire(chipT(stg, 'Pick a date'), 'click');
    const grid = stg.querySelector('.aldp'); ok(!!grid, 'Pick a date opens the month grid');
    const days = grid.querySelectorAll('.aldpday');
    ok(days.length === 31 && days[8].disabled === true && !days[9].disabled, 'January: the 9th is past and cannot be tapped, the 10th can');
    fire(grid.querySelectorAll('.alnav')[1], 'click');
    const feb = stg.querySelector('.aldp').querySelectorAll('.aldpday'); ok(feb.length === 28, 'next month draws February');
    fire(feb[19], 'click'); ok(!stg.querySelector('.aldp'), 'picking a day closes the grid');
    const dayRow = stg.querySelectorAll('.alrow').find((r) => r.children[0].textContent === 'Day');
    ok(dayRow.querySelectorAll('.alchip')[0].textContent === 'Fri 20 Feb' && dayRow.querySelectorAll('.alchip')[0].classList.contains('on'), 'the picked date leads the strip, selected');
    fire(chipT(stg, 'Save'), 'click'); await flush();
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && p[2].once_date === '1970-02-20', 'the picked date is sent: ' + JSON.stringify(p && p[2])); }
  // a time already gone today is refused before it reaches the server; Days with no day is refused too
  { const { b, a1, stg } = await editorOpen(1000000 + 8 * 3600000);  // 08:16, a 7:00 alarm
    b.advance(8 * 3600000); await flush();
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    const dayRow = stg.querySelectorAll('.alrow').find((r) => r.children[0].textContent === 'Day');
    ok(dayRow.querySelectorAll('.alchip.on').map((c) => c.textContent).join() === 'Tomorrow', '7:00 already gone: the default is Tomorrow');
    fire(chipT(dayRow, 'Today'), 'click'); fire(chipT(stg, 'Save'), 'click'); await flush();
    ok(!b.calls.some((c) => c[0] === 'POST' && c[1] === '/api/alarms') && /already passed/.test(stg.querySelector('.alerr').textContent), 'Today at a past time is refused in place');
    fire(chipT(stg, 'Days'), 'click'); fire(chipT(stg, 'Save'), 'click'); await flush();
    ok(!b.calls.some((c) => c[0] === 'POST') && /at least one day/.test(stg.querySelector('.alerr').textContent), 'Days with none picked is refused');
    fire(stg.querySelectorAll('.aldays .alchip')[4], 'click'); fire(chipT(stg, 'Save'), 'click'); await flush();
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && p[2].repeat === 'days:5' && !('once_date' in p[2]), 'Days → Fri sends a repeat, no once_date: ' + JSON.stringify(p && p[2])); }
  // the HOUSEHOLD zone decides today, not the panel's OS zone (this file pins TZ=UTC): 00:16 UTC is already 08:16 in Perth
  { const { stg, a1 } = await editorOpen(1000000, [], 'Australia/Perth');
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    const dayRow = stg.querySelectorAll('.alrow').find((r) => r.children[0].textContent === 'Day');
    ok(dayRow.querySelectorAll('.alchip.on').map((c) => c.textContent).join() === 'Tomorrow', 'household zone: 7:00 has gone in Perth, so Tomorrow');
    const P = boot().window.ZoeAlarms.pure;
    ok(JSON.stringify(P.householdNow(1000000, 'Australia/Perth')) === '{"ymd":"1970-01-01","hm":"08:16"}' && P.householdNow(1000000, 'UTC').hm === '00:16', 'householdNow reads the given zone');
    ok(P.householdNow(1000000, 'Not/AZone').ymd === '1970-01-01', 'an unknown zone falls back to the browser clock'); }
  // a Today whose time went by while the editor stood open is refused - never quietly saved for tomorrow
  { const { b, a1, stg } = await editorOpen(1000000 + 6 * 3600000 + 30 * 60000);  // 06:46 UTC, a 7:00 alarm: Today is the default
    b.advance(6 * 3600000 + 30 * 60000); await flush();
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    const dayRow = stg.querySelectorAll('.alrow').find((r) => r.children[0].textContent === 'Day');
    ok(dayRow.querySelectorAll('.alchip.on').map((c) => c.textContent).join() === 'Today', 'Today while 7:00 is ahead');
    b.advance(20 * 60000);  // 07:06: the editor stood open past 7:00
    fire(chipT(stg, 'Save'), 'click'); await flush();
    ok(!b.calls.some((c) => c[0] === 'POST' && c[1] === '/api/alarms') && /already passed/.test(stg.querySelector('.alerr').textContent), 'refused in place, not moved to tomorrow'); }
  // the month grid stops at a year ahead to the DAY, not at the end of that month
  { const { b, a1, stg } = await editorOpen(1000000 + 5 * 86400000);  // 6 Jan 1970
    b.advance(5 * 86400000); await flush();
    fire(chipT(a1, '+ New alarm'), 'click'); await flush();
    fire(chipT(stg, 'Pick a date'), 'click');
    for (let i = 0; i < 12; i++) fire(stg.querySelector('.aldp').querySelectorAll('.alnav')[1], 'click');
    const jan71 = stg.querySelector('.aldp').querySelectorAll('.aldpday');
    ok(jan71.length === 31 && !jan71[5].disabled && jan71[6].disabled === true && jan71[30].disabled === true, 'Jan 1971: the 6th can be picked, the 7th and later cannot');
    ok(stg.querySelector('.aldp').querySelectorAll('.alnav')[1].disabled === true, 'no month past the year'); }

  // one day's change shows on the row and a tap puts that day back (DELETE .../overrides/<date>); someone else's is shown, not tappable
  { const row = (ed) => ({ id: 'w1', time_hhmm: '06:30', repeat_rule: 'weekdays', repeat_phrase: 'on weekdays', enabled: true, when: 'x', label: 'Work',
                           editable: ed, overrides: [{ date: '1970-01-09', action: 'shift', time_hhmm: '05:30' }] });
    const { b, a1 } = await editorOpen(1000000, [row(true)]);
    const ov = a1.querySelectorAll('.alov')[0];
    ok(ov && ov.tagName === 'BUTTON' && ov.textContent === 'Fri 9 Jan: 5:30 am  ·  Undo', 'override shown on the row: ' + (ov && ov.textContent));
    fire(ov, 'click'); await flush();
    ok(b.calls.some((c) => c[0] === 'DELETE' && c[1] === '/api/alarms/w1/overrides/1970-01-09'), 'tap clears that day');
    ok(!b.calls.some((c) => c[0] === 'PATCH'), 'and does not open the editor or change the alarm');
    const r2 = await editorOpen(1000000, [row(false)]);
    const ov2 = r2.a1.querySelectorAll('.alov')[0];
    ok(ov2 && ov2.tagName === 'DIV' && !/Undo/.test(ov2.textContent), 'not editable: shown, not tappable'); }

  // the list names a once-off's day
  { const P = boot().window.ZoeAlarms.pure;
    ok(P.subline({ repeat_rule: 'once', once_date: '2026-11-07', enabled: true }, '2026-11-05') === 'Once · Sat 7 Nov', 'Once · Sat 7 Nov');
    ok(P.subline({ repeat_rule: 'once', once_date: '2026-11-06', enabled: true }, '2026-11-05') === 'Once · Tomorrow', 'Once · Tomorrow');
    ok(P.subline({ repeat_rule: 'once', once_date: '2026-11-07', enabled: false }, '2026-11-05') === 'Once · Off', 'a disabled once-off says Off');
    ok(P.subline({ repeat_rule: 'weekdays', repeat_phrase: 'on weekdays', enabled: true, when: '6:30 am tomorrow' }, '2026-11-05') === 'on weekdays · 6:30 am tomorrow', 'repeats unchanged');
    ok(P.dayLabel('2026-12-01', '2026-11-25') === 'Tue 1 Dec' && P.addDays('2026-12-31', 1) === '2027-01-01', 'month and year turn'); }

  // the editor offers music and saves it; tones stay available
  { const b = boot({ get: (u) => (u === '/api/alarms' ? { alarms: [], tones: { sunrise: 'Sunrise' }, now: 1 }
      : u === '/api/music/playlists' ? { playlists: [{ name: 'Morning' }, { name: 'Jazz' }] } : { now: 1, ringing: [] }) });
    b.window.ZoeAlarms.init(b.host); b.advance(400); await flush(); await flush();
    const tabs = b.document.createElement('div'); const t1 = b.document.createElement('div'); const a1 = b.document.createElement('div');
    b.window.ZoeAlarms.attachTabs({ tabsEl: tabs, timersEl: t1, alarmsEl: a1 })('alarms'); await flush(); await flush();
    fire(a1.querySelectorAll('.alchip').find((c) => c.textContent === '+ New alarm'), 'click'); await flush(); await flush();
    const stg = b.document.getElementById('stage');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === 'Sunrise'), 'click');
    ok(b.snd.previews.includes('sunrise'), 'tone chip previews');
    ok(stg.querySelectorAll('.alscroll').length === 2 && stg.querySelectorAll('.alscroll .alchip').some((c) => c.textContent === 'Morning'), 'playlists (and the once-off days) are single scrolling rows');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === 'Morning'), 'click');
    fire(stg.querySelectorAll('.alchip').find((c) => c.textContent === 'Save'), 'click'); await flush();
    const p = b.calls.find((c) => c[0] === 'POST' && c[1] === '/api/alarms');
    ok(p && p[2].sound_kind === 'music' && p[2].sound_id === 'playlist:Morning' && p[2].sound_name === 'Morning', 'music save body: ' + JSON.stringify(p && p[2]));
  }

  // the tone synthesiser: ramp, plan, start/stop, parity with the server's ids
  { const SND = fs.readFileSync(process.env.SND_SRC || path.join(__dirname, 'js/touch-alarm-sound.js'), 'utf8');
    const made = { osc: [], gains: [] }; let disc = 0; const tm = [];
    class Ctx { constructor() { this.currentTime = 10; this.state = 'running'; this.destination = {}; }
      createOscillator() { const o = { frequency: {}, connect() {}, start() {}, stopped: 0, stop() { o.stopped += 1; } }; made.osc.push(o); return o; }
      createGain() { const g = { gain: { value: 1, setValueAtTime(v) { g.gain.last = v; }, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} },
        connect() {}, disconnect() { disc += 1; } }; made.gains.push(g); return g; } }
    const w = { AudioContext: Ctx };
    const c2 = vm.createContext({ window: w, setInterval: (fn, ms) => { tm.push({ fn, ms, on: true }); return tm.length; }, clearInterval: (i) => { if (tm[i - 1]) tm[i - 1].on = false; }, Math });
    vm.runInContext(SND, c2);
    const S = w.ZoeAlarmSound;
    ok(S.rampGain(0, 30) === 0.05 && S.rampGain(30, 30) === 1 && S.rampGain(99, 30) === 1 && S.rampGain(-5, 30) === 0.05 && S.rampGain(5, 0) === 1, 'ramp ends');
    ok(Math.abs(S.rampGain(15, 30) - 0.525) < 1e-9, 'ramp midpoint');
    ok(['sunrise', 'chime', 'bell', 'beep'].every((id) => S.TONES[id] && S.plan(id).length > 0 && S.plan(id).every((n) => n.freq > 0 && n.dur > 0 && n.gain > 0)), 'every tone has a plan');
    ok(Object.keys(S.TONES).sort().join() === 'beep,bell,chime,sunrise', 'tone ids');
    ok(S.start('bell', () => 15, 30) === true && made.osc.length === S.plan('bell').length, 'start schedules the first phrase');
    ok(Math.abs(made.gains[0].gain.last - 0.525) < 1e-9, 'master gain follows the server clock (reload resumes mid-ramp)');
    tm.filter((t) => t.on).forEach((t) => t.fn());   // loop + ramp timers fire
    ok(made.osc.length === 2 * S.plan('bell').length, 'the phrase repeats');
    S.stop();
    ok(made.osc.every((o) => o.stopped >= 2) && disc === 1 && tm.every((t) => !t.on), 'stop silences every note and clears the timers');
    // a suspended context (autoplay policy) is woken again by every ramp tick, and a rejected resume is not fatal
    const sus = new Ctx(); sus.state = 'suspended'; let resumes = 0; sus.resume = () => { resumes += 1; return Promise.reject(new Error('blocked')); };
    const tm2 = []; const w2 = { AudioContext: function () { return sus; } };
    vm.runInContext(SND, vm.createContext({ window: w2, setInterval: (fn, ms) => { tm2.push({ fn, on: true }); return tm2.length; }, clearInterval: (i) => { if (tm2[i - 1]) tm2[i - 1].on = false; }, Math }));
    ok(w2.ZoeAlarmSound.start('chime', () => 0, 30) === true && resumes >= 1, 'start wakes a suspended context');
    tm2.filter((x) => x.on).forEach((x) => x.fn()); ok(resumes >= 2, 'the ramp timer keeps asking (a rejected resume is not fatal)'); w2.ZoeAlarmSound.stop();
    const before = made.osc.length; S.start('sunrise', () => 0, 30); S.preview('chime'); ok(made.osc.length === before + S.plan('sunrise').length, 'a preview never overlaps a ring'); S.stop(); }

  // the estate wires it: script tag before the inline code, init with the host's api, tabs on the timer screen
  ok(HOME.includes('<script src="/js/touch-alarms.js?v=al-4"></script>') && HOME.includes('/js/touch-alarm-sound.js?v=as-1'), 'home.html loads the modules');
  ok(HOME.includes('ZoeAlarms.init({apiGet:apiGet,apiJson:apiJson,show:show') && HOME.includes('sound:window.ZoeAlarmSound'), 'home.html inits it with the estate api and the tone synth');
  ok(HOME.includes('ZoeAlarms.attachTabs({tabsEl:document.getElementById(\'alTabs\')'), 'timer screen attaches the tabs');
  ok(HOME.indexOf('/js/touch-alarms.js') < HOME.indexOf('function wireTimer'), 'module loads before the wiring runs');
  // fit on the 1280x720 panel (owner, 2026-11-06): the screen is named Timers/Alarms, the list stops above the dock, the editor is a
  // full-stage sheet that leaves with its pane, and playlists are one swipeable row instead of a wall of chips
  ok(HOME.includes('<div class="chttl">Timers/Alarms</div>') && HOME.includes("{id:'timer',n:'Timers/Alarms'"), 'the screen and its launcher tile read Timers/Alarms');
  ok(/\.alfull\{[^}]*bottom:130px/.test(HOME) && /\.aled\{[^}]*z-index:60/.test(HOME) && /\.alchips\.alscroll\{[^}]*flex-wrap:nowrap/.test(HOME), 'list clears the dock, editor sits above it, playlists scroll in one row');
  ok(SRC.includes("section('Wake to music', 'alscroll', moreBox)") && SRC.includes('(stage || root).appendChild(ed)') && SRC.includes('if (!root.isConnected) close()'), 'editor mounts on the stage and closes when its pane goes');

  console.log(`${checks} checks passed`);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
