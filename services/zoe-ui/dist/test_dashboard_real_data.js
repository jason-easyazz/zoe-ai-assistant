#!/usr/bin/env node
/**
 * Desktop Wave 4b (2026-10-19): the dashboard - the desktop landing page - shows REAL data.
 *
 * The phase-1 audit found: widgets/core/system.js drew Math.random() CPU / memory / disk /
 * uptime and a permanent "All Systems Operational"; home.js drew four hard-coded rooms
 * whatever the house had; weather painted "23°C Sunny" and a fake Mon-Thu forecast until
 * data landed; cards stayed light in dark mode; and two cards of one type shared ONE widget
 * object (and could not be told apart in a saved layout), so the second clobbered the first.
 *
 * This harness runs the REAL widget and dashboard files in a vm against a small DOM shim and
 * a stubbed fetch, and every behavioural check is paired with a negative control: the same
 * assertion run against the old behaviour (or a broken copy) must fail, so a check that can
 * never go red is caught here rather than in production.
 *
 * Run: node services/zoe-ui/dist/test_dashboard_real_data.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const D = __dirname;
const read = (f) => fs.readFileSync(path.join(D, f), 'utf8');
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');
let passed = 0;
const pending = [];
function check(name, fn) {
    pending.push(Promise.resolve().then(fn).then(
        () => { passed++; console.log('  ok  - ' + name); },
        (e) => { console.error('  FAIL - ' + name + '\n        ' + (e && e.stack || e)); process.exitCode = 1; }
    ));
}
/** A negative control: `fn` must THROW (the assertion catches the broken behaviour). */
async function mustFail(label, fn) {
    let threw = false;
    try { await fn(); } catch (_) { threw = true; }
    assert(threw, 'negative control did not go red: ' + label);
}
// Values built inside the vm carry the vm's Array/Object prototypes; compare as plain data.
const plain = (v) => JSON.parse(JSON.stringify(v));
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };

// ── DOM shim ────────────────────────────────────────────────────────────────
function classList() {
    const set = new Set();
    return {
        add: (...c) => c.forEach(x => set.add(x)), remove: (...c) => c.forEach(x => set.delete(x)),
        contains: (c) => set.has(c), toggle: (c, on) => { if (on === undefined ? !set.has(c) : on) set.add(c); else set.delete(c); }
    };
}
function node(tag) {
    const n = {
        tagName: tag || 'div', innerHTML: '', textContent: '', attrs: {}, style: {}, children: [],
        classList: classList(), offsetWidth: 320, offsetHeight: 240, disabled: false,
        setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        appendChild(c) { this.children.push(c); return c; }, addEventListener() {}, removeEventListener() {},
        contains() { return true; }
    };
    // Every selector resolves to one stable child node (enough for a widget's own lookups).
    const kids = {};
    n.querySelector = (sel) => kids[sel] || (kids[sel] = node());
    n.querySelectorAll = (sel) => [n.querySelector(sel)];
    n.kids = kids;
    return n;
}
// A grid item built from the HTML string the dashboard hands gridstack.
function gridItemFrom(html) {
    const item = node('div');
    const m = /<div class="widget ([^"]*)" data-widget-type="([^"]*)" data-widget-uid="([^"]*)">/.exec(html);
    const card = node('div');
    if (m) { card.attrs['data-widget-type'] = m[2]; card.attrs['data-widget-uid'] = m[3]; }
    card.html = html;
    item.querySelector = (sel) => (sel === '.widget' ? (m ? card : null) : null);
    item.card = card;
    return item;
}
function fakeGrid() {
    const g = { engine: { nodes: [] }, handlers: {} };
    g.addWidget = (opts) => {
        const el = gridItemFrom(opts.content);
        const i = g.engine.nodes.length;
        g.engine.nodes.push({ el, x: Number.isInteger(opts.x) ? opts.x : (i * 4) % 12, y: Number.isInteger(opts.y) ? opts.y : Math.floor(i / 3) * 3, w: opts.w, h: opts.h });
        return el;
    };
    g.removeAll = () => { const items = g.engine.nodes.slice(); g.engine.nodes = []; (g.handlers.removed || []).forEach(h => h({}, items)); };
    g.removeWidget = (el) => { const items = g.engine.nodes.filter(n => n.el === el); g.engine.nodes = g.engine.nodes.filter(n => n.el !== el); (g.handlers.removed || []).forEach(h => h({}, items)); };
    g.on = (ev, h) => { (g.handlers[ev] = g.handlers[ev] || []).push(h); };
    return g;
}

// ── a vm context that loads the real files ──────────────────────────────────
function makeContext(fetchImpl) {
    const store = {};
    const timers = [];
    let rng = 0;
    const ctx = {
        console: { log() {}, warn() {}, error() {}, debug() {} },
        setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
        clearTimeout: () => {},
        setInterval: () => 0, clearInterval: () => {},
        setImmediate, Promise, JSON, Date, Math, Map, Set, Uint32Array, Number, String, Array, Object, Error, isFinite, isNaN,
        localStorage: {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; }
        },
        document: {
            readyState: 'complete', body: { classList: classList() },
            getElementById: () => null, querySelector: () => null, createElement: (t) => node(t),
            documentElement: { classList: classList() }
        },
        matchMedia: () => ({ matches: false }),
        addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
        CustomEvent: function CustomEvent(t, o) { this.type = t; this.detail = o && o.detail; },
        // deterministic, distinct per call (the real page uses the browser's crypto)
        crypto: { getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = (++rng * 2654435761) >>> 0; return a; } },
        fetch: (url, opts) => fetchImpl(url, opts || {}),
        confirm: () => true,
        zoeAuth: { getCurrentSession: () => ({ user_info: { user_id: ctx.__member || 'alice' } }) },
        _store: store, _timers: timers
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    for (const f of ['js/widget-base.js', 'js/widget-system.js', 'js/widgets/core/system.js',
        'js/widgets/core/home.js', 'js/widgets/core/weather.js', 'js/dashboard.js']) {
        vm.runInContext(read(f), ctx, { filename: f });
    }
    ctx.$ = (expr) => vm.runInContext(expr, ctx);
    return ctx;
}
const json = (status, body) => Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
const never = () => new Promise(() => {});

// ── 1. No Math.random anywhere in the widget code ───────────────────────────
function mathRandomSites(files) {
    return files.filter(f => /\bMath\s*\.\s*random\s*\(/.test(stripComments(f.src))).map(f => f.name);
}
function widgetFiles() {
    const out = [];
    const walk = (dir) => fs.readdirSync(path.join(D, dir)).forEach(name => {
        const rel = dir + '/' + name;
        if (fs.statSync(path.join(D, rel)).isDirectory()) walk(rel);
        else if (name.endsWith('.js')) out.push({ name: rel, src: read(rel) });
    });
    walk('js/widgets');
    ['js/dashboard.js', 'js/widget-system.js', 'js/widget-base.js'].forEach(f => out.push({ name: f, src: read(f) }));
    return out;
}
check('no widget or dashboard file calls Math.random (static, comments stripped)', async () => {
    const files = widgetFiles();
    assert(files.length >= 20, 'expected the widget tree, found ' + files.length + ' files');
    assert.deepStrictEqual(mathRandomSites(files), []);
    await mustFail('a file that calls Math.random is caught', () => {
        assert.deepStrictEqual(mathRandomSites([{ name: 'old-system.js', src: "el.textContent = Math.floor(Math.random() * 30 + 30) + '%';" }]), []);
    });
    // A mention in a comment is prose, not a call.
    assert.deepStrictEqual(mathRandomSites([{ name: 'c.js', src: '// it used to call Math.random()\nconst a = 1;' }]), []);
});

check('home.js carries no hard-coded rooms or entity ids', async () => {
    const src = stripComments(read('js/widgets/core/home.js'));
    for (const fake of ['light.living_room', 'Living Room', 'Kitchen', 'Bedroom', 'Office', 'zoe_home_rooms']) {
        assert(!src.includes(fake), 'home.js still carries ' + fake);
    }
});

// ── 2. System card: loading, real numbers, unavailable, denied, stale ───────
check('System card shows a loading state first, then the server\'s numbers', async () => {
    let answer = never;
    const ctx = makeContext((url) => (url === '/api/system/stats' ? answer() : json(404, {})));
    const w = new ctx.SystemWidget();
    const tmpl = w.getTemplate();
    assert(/Loading system status/.test(tmpl) && !/\d+\s*%|\d+(\.\d+)?\s*GB|\d+d \d+h/.test(tmpl), 'template must hold no numbers');
    const el = node();
    w.init(el);
    const body = el.kids['[data-role="system-body"]'];
    const badge = el.kids['[data-role="system-badge"]'];
    assert(/Loading system status/.test(body.innerHTML), 'loading text');
    assert.strictEqual(body.getAttribute('aria-busy'), 'true');
    assert.strictEqual(badge.textContent, '…');

    answer = () => json(200, {
        cpu: { percent: 40.2, cores: 6, load_1m: 1 },
        memory: { total_bytes: 16 * 1024 ** 3, used_bytes: 4 * 1024 ** 3, available_bytes: 12 * 1024 ** 3, percent: 25 },
        disk: { path: '/', total_bytes: 1000 * 1024 ** 3, used_bytes: 300 * 1024 ** 3, free_bytes: 700 * 1024 ** 3, percent: 30 },
        uptime_seconds: 2 * 86400 + 5 * 3600,
        services: [{ id: 'database', name: 'Database', status: 'ok' }, { id: 'brain', name: '<b>Brain</b>', status: 'down' }]
    });
    await w.load();
    const h = body.innerHTML;
    for (const want of ['40%', '25%', '30%', '2d 5h', '6 cores', '4.0 GB of 16.0 GB', '700 GB free', 'Database', 'zw-svc--down']) {
        assert(h.includes(want), 'System card should show ' + want + '\n' + h);
    }
    assert(h.includes('&lt;b&gt;Brain&lt;/b&gt;') && !h.includes('<b>Brain'), 'service names are escaped');
    assert.strictEqual(badge.textContent, '1 needs attention');
    assert.strictEqual(body.getAttribute('aria-busy'), 'false');

    // stale: a failed refresh keeps the last REAL reading, marked
    answer = () => Promise.reject(new Error('offline'));
    await w.load();
    assert(/Couldn’t refresh/.test(body.innerHTML) && body.innerHTML.includes('40%'));
    assert.strictEqual(badge.textContent, 'Stale');
});

check('System card says unavailable / sign in, never a number, when there is no answer', async () => {
    const ctx = makeContext(() => json(503, {}));
    const w = new ctx.SystemWidget();
    const el = node();
    w.init(el);
    await flush();
    const body = el.kids['[data-role="system-body"]'];
    assert(/unavailable/.test(body.innerHTML), body.innerHTML);
    assert(!/\d+%/.test(body.innerHTML));
    const ctx2 = makeContext(() => json(403, {}));
    const w2 = new ctx2.SystemWidget();
    const el2 = node();
    w2.init(el2);
    await flush();
    assert(/Sign in/.test(el2.kids['[data-role="system-body"]'].innerHTML));
    // negative control: the OLD card filled numbers in with no server at all
    await mustFail('the Math.random card is caught', () => {
        const oldBody = "<div id=\"cpuUsage\">45%</div>";
        assert(!/\d+%/.test(oldBody));
    });
});

check('System render: a missing reading is "—", never invented', async () => {
    const ctx = makeContext(() => json(404, {}));
    const html = ctx.SystemWidget.render({ kind: 'data', data: { cpu: null, memory: { percent: null }, disk: null, uptime_seconds: null, services: [] } });
    assert(!/\d+%/.test(html), html);
    assert((html.match(/—/g) || []).length >= 4, 'each unknown reading shows a dash');
});

// ── 3. Home card: the house's real rooms + devices, or an honest empty state ─
check('Home card: loading, then the real rooms and switchable devices', async () => {
    let rooms = never, ents = never;
    const posts = [];
    const ctx = makeContext((url, opts) => {
        if (url === '/api/rooms') return rooms();
        if (url === '/api/ha/entities') return ents();
        if (url === '/api/ha/control') { posts.push(JSON.parse(opts.body)); return json(200, { ok: true }); }
        return json(404, {});
    });
    const w = new ctx.HomeWidget();
    assert(/Loading your home/.test(w.getTemplate()) && !/Living Room|Kitchen/.test(w.getTemplate()));
    const el = node();
    w.init(el);
    const body = el.kids['[data-role="home-body"]'];
    assert(/Loading your home/.test(body.innerHTML));
    rooms = () => json(200, { rooms: [{ id: 1, name: 'Study <3', devices: [{ entity_id: 'light.desk', state: 'on' }, { entity_id: 'switch.fan', state: 'off' }] }], ha_available: true });
    ents = () => json(200, { entities: [
        { entity_id: 'light.desk', state: 'on', attributes: { friendly_name: 'Desk lamp' } },
        { entity_id: 'input_boolean.tv', state: 'off', attributes: { friendly_name: 'TV' } },
        { entity_id: 'sensor.backup_state', state: 'ok', attributes: {} },
        { entity_id: 'light.gone', state: 'unavailable', attributes: { friendly_name: 'Gone' } }
    ], count: 4 });
    await w.load();
    const h = body.innerHTML;
    assert(h.includes('Study &lt;3') && h.includes('1 of 2 on'), h);
    assert(h.includes('Desk lamp') && h.includes('aria-pressed="true"') && h.includes('TV') && h.includes('aria-pressed="false"'), h);
    assert(!h.includes('backup_state') && !h.includes('Gone'), 'sensors and unavailable devices are not switches');
    assert.strictEqual(el.kids['[data-role="home-badge"]'].textContent, '1 on');
    const btn = node('button');
    btn.attrs['data-entity-id'] = 'input_boolean.tv';
    await w.toggle(btn);
    assert.deepStrictEqual(posts, [{ entity_id: 'input_boolean.tv', action: 'toggle' }]);
});

check('Home card: empty house, HA down, signed out - each says so', async () => {
    const ctx = makeContext(() => json(404, {}));
    const H = ctx.HomeWidget;
    const empty = H.stateFrom({ data: { rooms: [], ha_available: true } }, { data: { entities: [], count: 0 } });
    assert.strictEqual(empty.kind, 'empty');
    assert(/No rooms or devices yet/.test(H.render(empty)));
    assert.strictEqual(H.stateFrom({ failed: true }, { failed: true }).kind, 'unavailable');
    assert.strictEqual(H.stateFrom({ denied: true }, { denied: true }).kind, 'denied');
    assert.strictEqual(H.stateFrom({ data: { rooms: [], ha_available: false } }, { failed: true }).kind, 'unavailable');
    const roomsOnly = H.stateFrom({ data: { rooms: [{ name: 'Lounge', devices: [] }], ha_available: false } }, { failed: true });
    assert(roomsOnly.kind === 'data' && roomsOnly.haDown && /isn’t answering/.test(H.render(roomsOnly)) && /Lounge/.test(H.render(roomsOnly)));
    // negative control: the old widget's markup is the four fixed rooms, which this rejects
    await mustFail('fixed rooms are caught', () => {
        const old = '<button class="room-btn" data-room="living_room">💡 Living Room</button>';
        assert(!/Living Room/.test(old));
    });
});

// ── 4. Weather: a loading state, never placeholder temperatures ─────────────
check('Weather card loads with no placeholder temperatures, then real data or "unavailable"', async () => {
    let cur = never, fc = never;
    const ctx = makeContext((url) => {
        if (url.startsWith('/api/weather/current')) return cur();
        if (url.startsWith('/api/weather/forecast')) return fc();
        return json(404, {});
    });
    const w = new ctx.WeatherWidget();
    const tmpl = w.getTemplate();
    assert(!/\d+\s*°/.test(tmpl), 'template must not carry a temperature: ' + (tmpl.match(/\d+\s*°/) || [])[0]);
    assert(!/\d+\s*km\/h|\d+%/.test(tmpl), 'template must not carry wind/humidity numbers');
    assert(/Loading weather/.test(tmpl) && /Loading forecast/.test(tmpl));
    assert(!/user_id=/.test(stripComments(read('js/widgets/core/weather.js'))), 'weather sends no user_id (the server reads the session)');
    assert(!/geolocation/.test(stripComments(read('js/widgets/core/weather.js'))), 'no location prompt for coordinates the server ignores');

    const el = node();
    w.init(el);
    cur = () => Promise.reject(new Error('down'));
    fc = () => json(503, {});
    await w.loadWeather();
    await w.loadForecast();
    assert.strictEqual(el.kids['.weather-condition'].textContent, 'Weather unavailable');
    assert.strictEqual(el.kids['.weather-temp'].textContent, '—');
    const fbox = el.kids['.weather-forecast-compact'];
    assert(fbox.children.some(c => c.textContent === 'Forecast unavailable'));

    cur = () => json(200, { temp: 21.4, description: 'clear sky', icon: '01d', humidity: 40, wind_speed: 2 });
    fc = () => json(200, { daily: [{ day: '2026-10-20', high: 24.6, low: null, description: 'rain' }] });
    await w.loadWeather();
    await w.loadForecast();
    assert.strictEqual(el.kids['.weather-temp'].textContent, '21°C');
    assert(/25°/.test(fbox.innerHTML) && /—/.test(fbox.innerHTML) && !/>0°</.test(fbox.innerHTML), 'a missing low is a dash, not 0°');
    // a later failure keeps the real reading
    cur = () => json(500, {});
    await w.loadWeather();
    assert.strictEqual(el.kids['.weather-temp'].textContent, '21°C');
    await mustFail('the old placeholder template is caught', () => {
        assert(!/\d+\s*°/.test('<div class="weather-temp">23°C</div>'));
    });
});

// ── 5. Duplicate widgets: one instance per card, kept apart in the layout ───
function boardWith(ctx) {
    const WM = ctx.$('WidgetManager');
    ['SystemWidget', 'HomeWidget', 'WeatherWidget'].forEach(c => WM.register(new ctx[c]()));
    const Dashboard = ctx.$('Dashboard');
    const d = new Dashboard();
    d.grid = fakeGrid();
    d.grid.on('removed', (e, items) => (items || []).forEach(n => n && n.el && d._releaseGridItem(n.el)));
    return { d, WM };
}
function assertTwoIndependentCards(d, WM) {
    const a = d.addWidget('weather');
    const b = d.addWidget('weather');
    const ua = a.card.getAttribute('data-widget-uid');
    const ub = b.card.getAttribute('data-widget-uid');
    assert(ua && ub && ua !== ub, 'two cards need two uids');
    const ia = WM.instances.get(ua);
    const ib = WM.instances.get(ub);
    assert(ia && ib && ia !== ib, 'two cards need two widget objects');
    assert(ia.element === a.card && ib.element === b.card, 'each object owns its own card element');
    return { a, b, ua, ub };
}
check('two cards of one type are two independent instances', async () => {
    const ctx = makeContext(() => never());
    const { d, WM } = boardWith(ctx);
    d.serverSync = false;
    const { ua, ub } = assertTwoIndependentCards(d, WM);
    d.saveLayout();
    const saved = JSON.parse(ctx._store['zoe_desktop_widgets']);
    assert.deepStrictEqual(saved.map(e => e.type), ['weather', 'weather']);
    assert.deepStrictEqual(saved.map(e => e.uid), [ua, ub]);
    assert(saved.every(e => !('order' in e)), 'order is redundant with x/y');
    // restore keeps both, with their uids
    d.loadFromData(saved);
    assert.strictEqual(d.grid.engine.nodes.length, 2);
    assert.deepStrictEqual(plain(d.currentLayout().map(e => e.uid)), [ua, ub]);
    assert.strictEqual(WM.instances.size, 2, 'the old instances were released on reload');
    // negative control: the singleton (createInstance handing back the shared module) is caught
    const ctx2 = makeContext(() => never());
    const b2 = boardWith(ctx2);
    b2.d.serverSync = false;
    b2.WM.createInstance = (t) => b2.WM.modules[t];
    await mustFail('a shared singleton per type is caught', () => assertTwoIndependentCards(b2.d, b2.WM));
});

check('removing a card stops only that card; a legacy layout gets distinct uids', async () => {
    const ctx = makeContext(() => never());
    const { d, WM } = boardWith(ctx);
    d.serverSync = false;
    const { a, ua, ub } = assertTwoIndependentCards(d, WM);
    let destroyed = 0;
    WM.instances.get(ua).destroy = () => { destroyed++; };
    d.grid.removeWidget(a);
    assert.strictEqual(destroyed, 1);
    assert(!WM.instances.has(ua) && WM.instances.has(ub));
    // a pre-4b layout: no uid, `id` named the type, a repeated or hostile uid
    d.loadFromData([
        { id: 'weather', x: 0, y: 0, w: 4, h: 3 },
        { type: 'weather', uid: 'dup', x: 4, y: 0, w: 4, h: 3 },
        { type: 'weather', uid: 'dup', x: 8, y: 0, w: 4, h: 3 },
        { type: 'system', uid: '"><img src=x onerror=alert(1)>', x: 0, y: 3, w: 4, h: 3 },
        { type: 'not-a-widget', x: 0, y: 6, w: 4, h: 3 },
        { type: 'constructor', x: 4, y: 6, w: 4, h: 3 },   // an inherited property is not a widget
        { type: '__proto__', x: 8, y: 6, w: 4, h: 3 }
    ]);
    const uids = plain(d.currentLayout().map(e => e.uid));
    assert.strictEqual(uids.length, 4, 'unknown types are skipped, the rest kept');
    assert.strictEqual(new Set(uids).size, 4, 'every card has its own uid');
    assert(uids.every(u => /^[A-Za-z0-9_-]{1,64}$/.test(u)), 'a hostile uid is replaced, never interpolated');
});

check('the desktop board saves to and restores from /api/dashboard/layout/', async () => {
    const puts = [];
    const serverLayout = [{ uid: 's1', type: 'system', x: 0, y: 0, w: 4, h: 3 }, { uid: 's2', type: 'system', x: 4, y: 0, w: 4, h: 3 }];
    const ctx = makeContext((url, opts) => {
        if (url === '/api/dashboard/layout/' && opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok' }); }
        if (url === '/api/dashboard/layout/') return json(200, { layout: serverLayout });
        return never();
    });
    const { d } = boardWith(ctx);
    assert.strictEqual(d.serverSync, true, 'the desktop board syncs');
    await d.loadLayout();
    assert.deepStrictEqual(plain(d.currentLayout().map(e => e.uid)), ['s1', 's2']);
    d.saveLayout();
    ctx._timers.splice(0).forEach(fn => fn());
    await flush();
    assert.strictEqual(puts.length, 1);
    assert.deepStrictEqual(puts[0].layout.map(e => [e.uid, e.type]), [['s1', 'system'], ['s2', 'system']]);
    // the touch board stays browser-local
    const ctx2 = makeContext(() => { throw new Error('touch must not call the server'); });
    ctx2.document.body.classList.add('touch-dashboard');
    const b2 = boardWith(ctx2);
    assert.strictEqual(b2.d.serverSync, false);
});

check('a board emptied of cards never refreshes the template widgets', async () => {
    const ctx = makeContext(() => never());
    const { d, WM } = boardWith(ctx);
    d.serverSync = false;
    const card = d.addWidget('weather');
    d.grid.removeWidget(card);
    assert.strictEqual(WM.instances.size, 0);
    let templateUpdates = 0;
    Object.values(WM.modules).forEach(m => { m.update = () => { templateUpdates++; }; });
    const probe = () => {
        templateUpdates = 0;
        WM.updateAll();
        assert.strictEqual(WM.updateType('weather'), true, 'handled: the board has no weather card to refresh');
        assert.strictEqual(templateUpdates, 0, 'a never-mounted template was refreshed');
    };
    probe();
    // negative control: inferring the mode from instances.size (the reviewed bug) is caught
    WM.instanceMode = false;
    await mustFail('falling back to templates on an empty board is caught', probe);
});

check('an emptied server layout stays empty; only a missing one falls back to this browser', async () => {
    const stored = [{ uid: 'old1', type: 'weather', x: 0, y: 0, w: 4, h: 3 }];
    const run = async (serverBody, patch) => {
        const puts = [];
        const ctx = makeContext((url, opts) => {
            if (url === '/api/dashboard/layout/' && opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok' }); }
            if (url === '/api/dashboard/layout/') return json(200, serverBody);
            return never();
        });
        const { d } = boardWith(ctx);
        ctx._store['zoe_desktop_widgets:alice'] = JSON.stringify(stored);
        d.createDefaultLayout = () => { d.defaulted = true; };
        if (patch) patch(d);
        await d.loadLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        return { d, puts };
    };
    // the member removed every card: the board stays empty and nothing is saved back
    const emptied = await run({ layout: [] });
    assert.strictEqual(emptied.d.grid.engine.nodes.length, 0, 'removed cards were resurrected');
    assert(!emptied.d.defaulted, 'the default board replaced an emptied one');
    assert.strictEqual(emptied.puts.length, 0, 'an emptied board must not be PUT back with cards');
    // never saved on the server (layout: null): this browser's old board is used and moved up
    const fresh = await run({ layout: null });
    assert.deepStrictEqual(plain(fresh.d.currentLayout().map(e => e.uid)), ['old1']);
    assert.strictEqual(fresh.puts.length, 1, 'a browser-only board moves to the server once');
    // negative control: the reviewed bug (an empty server layout read as "no layout") is caught
    await mustFail('treating [] as missing is caught', async () => {
        const bad = await run({ layout: [] }, (d) => {
            d._fetchServerLayout = async () => {
                const data = { layout: [] };
                return (data && Array.isArray(data.layout) && data.layout.length) ? data.layout : null;
            };
        });
        assert.strictEqual(bad.d.grid.engine.nodes.length, 0);
    });
});

check('a singleton-dispatched type (zoe-orb) cannot be added twice; other types still can', async () => {
    const ctx = makeContext(() => never());
    const { d, WM } = boardWith(ctx);
    d.serverSync = false;
    ctx.$("WidgetManager.register(new (class extends WidgetModule { constructor() { super('zoe-orb'); } getTemplate() { return '<i></i>'; } })());");
    assert(WM.modules['zoe-orb'], 'the orb is registered');
    const first = d.addWidget('zoe-orb');
    assert(first, 'the first orb mounts');
    assert.strictEqual(d.addWidget('zoe-orb'), null, 'a second orb must be refused');
    assert.strictEqual(d.grid.engine.nodes.length, 1);
    assert(d.addWidget('weather') && d.addWidget('weather'), 'ordinary types may repeat');
    // a stored layout with two orbs keeps one (and both weathers)
    d.loadFromData([
        { uid: 'o1', type: 'zoe-orb', x: 0, y: 0, w: 3, h: 3 }, { uid: 'o2', type: 'zoe-orb', x: 3, y: 0, w: 3, h: 3 },
        { uid: 'w1', type: 'weather', x: 6, y: 0, w: 4, h: 3 }, { uid: 'w2', type: 'weather', x: 0, y: 3, w: 4, h: 3 }
    ]);
    assert.deepStrictEqual(plain(d.currentLayout().map(e => e.type)).sort(), ['weather', 'weather', 'zoe-orb']);
    // removing the orb frees the slot
    d.grid.removeWidget(d.grid.engine.nodes.find(n => n.el.card.getAttribute('data-widget-type') === 'zoe-orb').el);
    assert(d.addWidget('zoe-orb'), 'a removed orb can be added again');
    // negative control: without the guard a second orb mounts
    const ctx2 = makeContext(() => never());
    const b2 = boardWith(ctx2);
    b2.d.serverSync = false;
    ctx2.$("WidgetManager.register(new (class extends WidgetModule { constructor() { super('zoe-orb'); } getTemplate() { return '<i></i>'; } })());");
    b2.d._hasCardOfType = () => false;
    await mustFail('a second orb mounting is caught', async () => {
        b2.d.addWidget('zoe-orb');
        assert.strictEqual(b2.d.addWidget('zoe-orb'), null);
    });
});

check('lists.html and the touch dashboard still register these widgets (registerAllWidgets)', async () => {
    // The legacy route every other consumer uses: the manifest + window[ClassName].
    const manifest = JSON.parse(read('js/widgets/widget-manifest.json'));
    manifest.widgets = manifest.widgets.filter(w => ['system', 'home', 'weather'].includes(w.id));
    assert.strictEqual(manifest.widgets.length, 3, 'manifest still lists system, home and weather');
    const run = async (mutate) => {
        const ctx = makeContext((url) => (url === '/js/widgets/widget-manifest.json' ? json(200, manifest) : never()));
        ctx.setTimeout = (fn, ms) => setTimeout(fn, Math.min(ms || 0, 5));
        if (mutate) mutate(ctx);
        const count = await ctx.$('registerAllWidgets()');
        const WM = ctx.$('WidgetManager');
        assert.strictEqual(count, 3, 'registered ' + count + ' of 3');
        assert(WM.modules.system instanceof ctx.SystemWidget && WM.modules.home instanceof ctx.HomeWidget
            && WM.modules.weather instanceof ctx.WeatherWidget);
        assert.strictEqual(WM.instanceMode, false, 'a page without the dashboard stays on the module path');
    };
    await run(null);
    await mustFail('a widget that stops exposing its class is caught', () => run((ctx) => { delete ctx.HomeWidget; }));
});

// ── 6. Dark mode: cards follow the theme tokens ─────────────────────────────
function cssBlock(src, selectorRe) {
    const m = selectorRe.exec(src);
    if (!m) return null;
    const start = src.indexOf('{', m.index);
    return src.slice(start + 1, src.indexOf('}', start));
}
function tokenProblems(css) {
    // every colour in the card-state block must come from a token (white on the
    // primary "on" chip is the one fixed value)
    const problems = [];
    for (const m of css.matchAll(/(?:^|[;{\s])(color|background|background-color|border-color)\s*:\s*([^;}]+)/g)) {
        const v = m[2].trim();
        if (!/^var\(--clr-/.test(v) && v !== '#fff' && v !== 'transparent' && v !== 'currentColor') problems.push(m[1] + ': ' + v);
    }
    return problems;
}
check('dark mode: the cards read the theme tokens, and dark-mode-shared swaps them', async () => {
    const page = read('dashboard.html');
    const root = cssBlock(page, /:root\s*\{/);
    assert(/--surface:\s*var\(--clr-glass-bg\)/.test(root), 'card surface must be the --clr-glass-bg token');
    assert(/--text-primary:\s*var\(--clr-text\)/.test(root), 'card text must be the --clr-text token');
    const widget = cssBlock(page, /\n\s*\.widget\s*\{/);
    assert(/background:\s*var\(--surface\)/.test(widget), '.widget background must be var(--surface)');
    const shared = read('css/dark-mode-shared.css');
    const dark = cssBlock(shared, /html\.dark-mode :root,\s*html\.dark-mode\s*\{/);
    const glass = /--clr-glass-bg:\s*rgba\(255,\s*255,\s*255,\s*(0?\.\d+)\)/.exec(dark);
    assert(glass && parseFloat(glass[1]) <= 0.1, 'dark mode swaps the card surface to a translucent dark');
    assert(/--clr-text:\s*rgba\(255,\s*255,\s*255/.test(dark), 'dark mode swaps card text to light');
    assert(page.indexOf('css/dark-mode-shared.css') > page.indexOf(':root {'), 'the shared dark sheet loads after the page tokens');
    const premium = read('css/widgets-premium.css');
    const block = premium.slice(premium.indexOf('Card states + the System / Home cards'));
    assert(block.length > 500, 'the card-state block exists');
    assert.deepStrictEqual(tokenProblems(block), []);
    await mustFail('a fixed light colour is caught', () => {
        assert.deepStrictEqual(tokenProblems('.zw-stat { background: rgba(255,255,255,0.5); color: #666; }'), []);
    });
    for (const f of ['js/widgets/core/system.js', 'js/widgets/core/home.js']) {
        const src = stripComments(read(f));
        assert(!/style="[^"]*(?:color|background)\s*:\s*(?:#|rgb)/.test(src), f + ' must not inline a fixed colour');
    }
});

check('journal (a bare `journalWidget` global) is a singleton type, and so is every widget that publishes itself on window', async () => {
    const journal = "WidgetManager.register(new (class extends WidgetModule { constructor() { super('journal'); } getTemplate() { return '<i></i>'; } })());";
    const run = (ctx) => {
        const { d } = boardWith(ctx);
        d.serverSync = false;
        ctx.$(journal);
        assert(d.addWidget('journal'), 'the first journal mounts');
        assert.strictEqual(d.addWidget('journal'), null, 'a second journal would take over window.journalWidget');
        assert.strictEqual(d.grid.engine.nodes.length, 1);
        d.loadFromData([{ uid: 'j1', type: 'journal', x: 0, y: 0, w: 6, h: 4 }, { uid: 'j2', type: 'journal', x: 6, y: 0, w: 6, h: 4 }]);
        assert.strictEqual(d.grid.engine.nodes.length, 1, 'a stored layout with two journals keeps one');
    };
    run(makeContext(() => never()));
    // class check: any widget file that does `window.x = this` must be listed
    const selfPublishing = widgetFiles().filter(f => f.name.startsWith('js/widgets/') && /\bwindow\.[A-Za-z_$][\w$]*\s*=\s*this\b/.test(stripComments(f.src)))
        .map(f => path.basename(f.name, '.js'));
    assert(selfPublishing.includes('journal'), 'the scan still finds journal.js');
    const ctx = makeContext(() => never());
    selfPublishing.forEach(t => assert.strictEqual(ctx.$(`SINGLETON_WIDGET_TYPES.has(${JSON.stringify(t)})`), true, t + ' publishes itself on window but is not a singleton type'));
    // negative controls: without 'journal' in the set a second journal mounts / the scan goes red
    await mustFail('a second journal mounting is caught', () => {
        const c = makeContext(() => never());
        c.$("SINGLETON_WIDGET_TYPES.delete('journal')");
        run(c);
    });
    await mustFail('a self-publishing widget missing from the set is caught', () => {
        const c = makeContext(() => never());
        c.$("SINGLETON_WIDGET_TYPES.delete('journal')");
        selfPublishing.forEach(t => assert.strictEqual(c.$(`SINGLETON_WIDGET_TYPES.has(${JSON.stringify(t)})`), true));
    });
});

check('an unreadable server layout (HTTP error / network failure) is never written over; only a readable null migrates', async () => {
    const stored = [{ uid: 'old1', type: 'weather', x: 0, y: 0, w: 4, h: 3 }];
    const run = async (getImpl, { local = true, patch } = {}) => {
        const puts = [];
        const ctx = makeContext((url, opts) => {
            if (url === '/api/dashboard/layout/' && opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok' }); }
            if (url === '/api/dashboard/layout/') return getImpl();
            return never();
        });
        const { d } = boardWith(ctx);
        if (local) ctx._store['zoe_desktop_widgets:alice'] = JSON.stringify(stored);
        if (patch) patch(d);
        await d.loadLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        // a later edit in the same session must not reach the server either
        d.saveLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        return { d, ctx, puts };
    };
    const failures = {
        'HTTP 503': () => json(503, {}),
        'HTTP 401': () => json(401, {}),
        'network error': () => Promise.reject(new Error('offline'))
    };
    for (const [name, get] of Object.entries(failures)) {
        const withLocal = await run(get);
        assert.deepStrictEqual(plain(withLocal.d.currentLayout().map(e => e.uid)), ['old1'], name + ": this browser's copy is shown");
        assert.strictEqual(withLocal.puts.length, 0, name + ': the server layout must not be overwritten');
        const noLocal = await run(get, { local: false });
        assert(noLocal.d.grid.engine.nodes.length > 0, name + ': the default board is shown');
        assert.strictEqual(noLocal.puts.length, 0, name + ': the default board must not be PUT over the server layout');
    }
    // a successful GET that says layout: null still migrates the browser copy
    const fresh = await run(() => json(200, { layout: null }));
    assert(fresh.puts.length >= 1, 'a readable "nothing saved" migrates');
    // negative control: the reviewed bug (a failed GET read as "no saved layout") is caught
    const asMissing = (d) => {
        const real = d._fetchServerLayout.bind(d);
        d._fetchServerLayout = async () => {
            const v = await real();
            return typeof v === 'symbol' ? null : v;
        };
    };
    await mustFail('a failed GET read as null is caught', async () => {
        const bad = await run(failures['HTTP 503'], { patch: asMissing });
        assert.strictEqual(bad.puts.length, 0);
    });
    await mustFail('a failed GET on the default board is caught', async () => {
        const bad = await run(failures['network error'], { local: false, patch: asMissing });
        assert.strictEqual(bad.puts.length, 0);
    });
});

check('layout PUTs are serialised: one in flight, the latest layout goes next', async () => {
    const run = async (patch) => {
        const started = [];
        const releases = [];
        const ctx = makeContext((url, opts) => {
            if (url === '/api/dashboard/layout/' && opts.method === 'PUT') {
                started.push(JSON.parse(opts.body));
                return new Promise((res) => releases.push(() => res({ ok: true, status: 200, json: async () => ({}) })));
            }
            return never();
        });
        const { d } = boardWith(ctx);
        if (patch) patch(d, ctx);
        const fire = async () => { ctx._timers.splice(0).forEach(fn => fn()); await flush(); };
        const lay = (n) => [{ uid: 'u' + n, type: 'weather', x: 0, y: 0, w: 4, h: 3 }];
        d._queueServerSave(lay(1)); await fire();
        assert.strictEqual(started.length, 1, 'the first PUT starts');
        d._queueServerSave(lay(2)); await fire();
        d._queueServerSave(lay(3)); await fire();
        assert.strictEqual(started.length, 1, 'no second PUT while one is in flight');
        releases[0](); await flush();
        assert.strictEqual(started.length, 2, 'exactly one more PUT after the first settles (2 and 3 coalesce)');
        assert.strictEqual(started[1].layout[0].uid, 'u3', 'the latest layout is the one sent');
        releases[1](); await flush();
        assert.strictEqual(started.length, 2, 'nothing left to send');
        return started;
    };
    await run();
    // a failing PUT must not wedge the queue
    const ctx = makeContext((url, opts) => (opts.method === 'PUT' ? Promise.reject(new Error('offline')) : never()));
    const { d } = boardWith(ctx);
    d._queueServerSave([{ uid: 'a', type: 'weather', x: 0, y: 0, w: 4, h: 3 }]);
    ctx._timers.splice(0).forEach(fn => fn());
    await flush();
    assert.strictEqual(d._serverSaveInFlight, false, 'a failed PUT releases the slot');
    // negative control: the old fire-and-forget PUT (overlapping requests) is caught
    await mustFail('overlapping PUTs are caught', () => run((dd, cc) => {
        dd._queueServerSave = function (layout) {
            this._serverSaveTimer = cc.setTimeout(() => {
                cc.fetch('/api/dashboard/layout/', { method: 'PUT', body: JSON.stringify({ layout }) });
            }, 600);
        };
    }));
});

// ── Review round (PR #2164): unsynced layout, stale-board saves, Home badge, Notes hook ─
check('a layout PUT that fails is kept and recovered after a reload, not replaced by the older server copy', async () => {
    const stale = [{ uid: 'w1', type: 'weather', x: 0, y: 0, w: 4, h: 3 }];
    const run = async (patch) => {
        const ctx = makeContext((url, opts) => {
            if (opts.method === 'PUT') return json(503, {});
            if (url === '/api/dashboard/layout/') return json(200, { layout: stale, version: 'v1' });
            return never();
        });
        const { d } = boardWith(ctx);
        if (patch) patch(d);
        await d.loadLayout();
        d.grid.removeWidget(d.grid.engine.nodes[0].el);   // the member removes the last card
        d.saveLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        // reload: a new page over the same browser storage; the server still holds the old board
        const puts = [];
        const ctx2 = makeContext((url, opts) => {
            if (opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok', version: 'v2' }); }
            if (url === '/api/dashboard/layout/') return json(200, { layout: stale, version: 'v1' });
            return never();
        });
        Object.assign(ctx2._store, ctx._store);
        const b2 = boardWith(ctx2);
        await b2.d.loadLayout();
        ctx2._timers.splice(0).forEach(fn => fn());
        await flush();
        return { d2: b2.d, puts, ctx2 };
    };
    const { d2, puts, ctx2 } = await run();
    assert.deepStrictEqual(plain(d2.currentLayout()), [], 'the removal survives the reload');
    assert.strictEqual(puts.length, 1, 'and is saved again');
    assert.deepStrictEqual(puts[0].layout, []);
    assert.strictEqual(puts[0].base_version, 'v1', 'against the version read');
    assert(!('zoe_desktop_widgets:alice_unsynced' in ctx2._store), 'acknowledged: the marker is cleared');
    // negative control: never marking the layout unsynced (the reviewed bug) is caught
    await mustFail('an unmarked failed save is caught', async () => {
        const r = await run((d) => { d._setUnsynced = () => {}; });
        assert.deepStrictEqual(plain(r.d2.currentLayout()), []);
    });
});

check('a board saving over a layout chat changed merges the new cards instead of erasing them', async () => {
    const run = async (patch) => {
        const puts = [];
        const chatCard = { uid: 'c1', type: 'system', x: 0, y: 3, w: 4, h: 3 };
        const ctx = makeContext((url, opts) => {
            if (opts.method === 'PUT') {
                const body = JSON.parse(opts.body);
                puts.push(body);
                if (body.base_version === 'v1') {
                    return json(409, { detail: { error: 'layout_changed', layout: [{ uid: 'w1', type: 'weather', x: 0, y: 0, w: 4, h: 3 }, chatCard], version: 'v2' } });
                }
                return json(200, { status: 'ok', version: 'v3' });
            }
            if (url === '/api/dashboard/layout/') return json(200, { layout: [{ uid: 'w1', type: 'weather', x: 0, y: 0, w: 4, h: 3 }], version: 'v1' });
            return never();
        });
        const { d } = boardWith(ctx);
        if (patch) patch(d);
        await d.loadLayout();
        d.saveLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        return { d, puts };
    };
    const { d, puts } = await run();
    assert.strictEqual(puts.length, 2, 'the conflict is retried once');
    assert.deepStrictEqual(puts[1].layout.map(e => e.uid).sort(), ['c1', 'w1'], "chat's card is kept in the save");
    assert.strictEqual(puts[1].base_version, 'v2');
    assert.deepStrictEqual(plain(d.currentLayout().map(e => e.uid)).sort(), ['c1', 'w1'], 'and shown on the board');
    // negative control: dropping the merge (the reviewed bug) is caught
    await mustFail('a save that ignores the conflict is caught', async () => {
        const r = await run((dd) => { dd._adoptServerCards = () => {}; });
        assert.deepStrictEqual(r.puts[1].layout.map(e => e.uid).sort(), ['c1', 'w1']);
    });
});

check('Home badge counts every switchable device and says Unknown when the device read failed', async () => {
    const ctx = makeContext(() => never());
    const H = ctx.HomeWidget;
    const ents = [];
    for (let i = 0; i < 10; i++) {
        const n = String.fromCharCode(97 + i);
        ents.push({ entity_id: 'light.l' + n, state: i === 9 ? 'on' : 'off', attributes: { friendly_name: 'Lamp ' + n } });
    }
    const rooms = { data: { rooms: [{ id: 1, name: 'R', devices: [] }], ha_available: true } };
    const st = H.stateFrom(rooms, { data: { entities: ents } });
    assert.strictEqual(st.devices.length, 8, 'still shows eight');
    assert.strictEqual(H.badge(st), '1 on', 'a device beyond the eight shown is counted');
    assert.strictEqual(H.badge(H.stateFrom(rooms, { failed: true })), 'Unknown', 'rooms loaded, devices unreadable: not "All off"');
    assert.strictEqual(H.badge(H.stateFrom(rooms, { data: { entities: ents.slice(0, 2) } })), 'All off');
});

check('a removed Notes card stops fetching; live cards still refresh', async () => {
    const fetched = [];
    const ctx = makeContext((url) => { fetched.push(url); return json(200, { notes: [] }); });
    vm.runInContext(read('js/widgets/core/notes.js'), ctx, { filename: 'notes.js' });
    const { d, WM } = boardWith(ctx);
    WM.register(new (ctx.$('NotesWidget'))());
    d.serverSync = false;
    const a = d.addWidget('notes');
    const b = d.addWidget('notes');
    assert(a && b, 'two notes cards mounted');
    await flush();
    fetched.length = 0;
    ctx.window.loadNotes();
    await flush();
    assert.strictEqual(fetched.length, 2, 'both live cards refresh');
    d.grid.removeWidget(a);
    fetched.length = 0;
    ctx.window.loadNotes();
    await flush();
    assert.strictEqual(fetched.length, 1, 'the removed card no longer fetches');
});

// ── Review round 3 (PR #2164): member-keyed copy, recovery merge, no duplicate adopt, widget teardown ─
const W = (uid, type = 'weather') => ({ uid, type, x: 0, y: 0, w: 4, h: 3 });

check("another member signing in never loads or sends the first member's unsaved board", async () => {
    const run = async (patch) => {
        const puts = [];
        const ctx = makeContext((url, opts) => {
            if (opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok', version: 'v8' }); }
            if (url === '/api/dashboard/layout/') return json(200, { layout: [W('b1')], version: 'v7' });
            return never();
        });
        // alice's failed save, plus the unkeyed copy + marker an older page left behind
        ctx._store['zoe_desktop_widgets:alice'] = JSON.stringify([W('a1')]);
        ctx._store['zoe_desktop_widgets:alice_unsynced'] = '1';
        ctx._store.zoe_desktop_widgets = JSON.stringify([W('old')]);
        ctx._store.zoe_desktop_widgets_unsynced = '1';
        ctx.__member = 'bob';
        const { d } = boardWith(ctx);
        if (patch) patch(d);
        await d.loadLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        assert.deepStrictEqual(plain(d.currentLayout().map(e => e.uid)), ['b1'], "bob sees his own board");
        assert.strictEqual(puts.length, 0, "nothing of alice's is sent over bob's board");
        // bob's later edit lands under bob's key, alice's copy is untouched
        d.saveLayout();
        assert(ctx._store['zoe_desktop_widgets:bob'], "bob's copy is keyed by bob");
        assert.strictEqual(ctx._store['zoe_desktop_widgets:alice'], JSON.stringify([W('a1')]), "alice's copy is untouched");
        return { puts };
    };
    await run();
    await mustFail('an unkeyed local copy is caught', () => run((d) => { d._layoutKey = () => 'zoe_desktop_widgets'; }));
    // no known member: no browser copy at all
    const ctx = makeContext(() => never());
    ctx.__member = '';
    ctx.zoeAuth = { getCurrentSession: () => ({ session_id: null }) };
    const { d } = boardWith(ctx);
    d.saveLayout();
    assert(!Object.keys(ctx._store).some(k => k.startsWith('zoe_desktop_widgets')), 'no member, nothing stored');
});

check('recovering an unsaved board keeps a card the server gained meanwhile; a card removed here stays removed', async () => {
    const run = async (patch, local) => {
        const puts = [];
        const ctx = makeContext((url, opts) => {
            if (opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return json(200, { status: 'ok', version: 'v3' }); }
            if (url === '/api/dashboard/layout/') return json(200, { layout: [W('w1'), W('c1', 'system')], version: 'v2' });
            return never();
        });
        ctx._store['zoe_desktop_widgets:alice'] = JSON.stringify(local);
        ctx._store['zoe_desktop_widgets:alice_base'] = JSON.stringify(['w1']);
        ctx._store['zoe_desktop_widgets:alice_unsynced'] = '1';
        const { d } = boardWith(ctx);
        if (patch) patch(d);
        await d.loadLayout();
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        return puts;
    };
    const puts = await run(null, [W('w1')]);
    assert.strictEqual(puts.length, 1);
    assert.deepStrictEqual(puts[0].layout.map(e => e.uid).sort(), ['c1', 'w1'], "the server's new card is in the replay");
    assert.strictEqual(puts[0].base_version, 'v2');
    const removed = await run(null, []);
    assert.deepStrictEqual(removed[0].layout.map(e => e.uid), ['c1'], 'w1 (removed here, known to the server) stays removed; c1 (new there) joins');
    await mustFail('a replay that ignores the server additions is caught', async () => {
        const bad = await run((d) => { d._adoptServerCards = () => {}; }, [W('w1')]);
        assert.deepStrictEqual(bad[0].layout.map(e => e.uid).sort(), ['c1', 'w1']);
    });
});

check('a lost PUT response retried into a 409 does not mount the board\'s own new card twice', async () => {
    const run = async (patch) => {
        const puts = [];
        let newUid = null;
        const ctx = makeContext((url, opts) => {
            if (opts.method === 'PUT') {
                const body = JSON.parse(opts.body);
                puts.push(body);
                if (puts.length === 1) {
                    // the first PUT committed on the server but its response never arrived
                    return json(409, { detail: { error: 'layout_changed', layout: [W('w1'), W(newUid)], version: 'v2' } });
                }
                return json(200, { status: 'ok', version: 'v3' });
            }
            if (url === '/api/dashboard/layout/') return json(200, { layout: [W('w1')], version: 'v1' });
            return never();
        });
        const { d } = boardWith(ctx);
        if (patch) patch(d);
        await d.loadLayout();
        d.addWidget('weather');
        newUid = d.currentLayout().map(e => e.uid).find(u => u !== 'w1');
        ctx._timers.splice(0).forEach(fn => fn());
        await flush();
        const uids = d.currentLayout().map(e => e.uid);
        assert.strictEqual(new Set(uids).size, uids.length, 'no duplicate uid on the board');
        assert.strictEqual(puts.length, 2);
        const sent = puts[1].layout.map(e => e.uid);
        assert.strictEqual(new Set(sent).size, sent.length, 'the retried PUT has no duplicate uid');
    };
    await run();
    await mustFail('adopting an already-mounted card is caught', () => run((d) => {
        d._adoptServerCards = function (serverLayout) {
            const known = this._syncedUids || new Set();
            serverLayout.forEach(card => {
                if (!card || known.has(card.uid)) return;
                if (this._mountCard(card.type, card, card.options || null, card.uid)) known.add(card.uid);
            });
            this._syncedUids = known;
        };
    }));
});

check('a removed Multica board / Time card drops its document and window listeners', async () => {
    const run = async (cls, file, type, patch) => {
        const fetched = [];
        const ctx = makeContext((url) => { fetched.push(url); return json(200, { available: true, groups: {} }); });
        const live = new Map();   // event -> Set of listeners
        const reg = (t, f) => { if (!live.has(t)) live.set(t, new Set()); live.get(t).add(f); };
        const unreg = (t, f) => { if (live.has(t)) live.get(t).delete(f); };
        ctx.document.addEventListener = reg; ctx.document.removeEventListener = unreg;
        ctx.addEventListener = reg; ctx.removeEventListener = unreg;
        ctx.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
        vm.runInContext(read(file), ctx, { filename: file });
        const { d, WM } = boardWith(ctx);
        WM.register(new (ctx.$(cls))());
        if (patch) patch(ctx.$(cls));
        d.serverSync = false;
        const a = d.addWidget(type);
        d.addWidget(type);
        await flush();
        const count = () => Array.from(live.values()).reduce((n, set) => n + set.size, 0);
        const before = count();
        d.grid.removeWidget(a);
        return { before, after: count(), ctx, live, fetched };
    };
    const mb = await run('MulticaBoardWidget', 'js/widgets/core/multica-board.js', 'multica-board');
    assert(mb.before - mb.after >= 2, 'both ticket listeners of the removed card are gone');
    const tm = await run('TimeWidget', 'js/widgets/core/time.js', 'time');
    assert(tm.before - tm.after >= 1, 'the settings listener of the removed card is gone');
    await mustFail('a Multica card that keeps its listeners is caught', async () => {
        const bad = await run('MulticaBoardWidget', 'js/widgets/core/multica-board.js', 'multica-board', (C) => { C.prototype.destroy = function () { if (this.updateTimer) clearInterval(this.updateTimer); }; });
        assert(bad.before - bad.after >= 2);
    });
});

Promise.all(pending).then(() => {
    const total = pending.length;
    console.log(`\ndashboard real data: ${passed}/${total} checks passed`);
    if (passed !== total) process.exitCode = 1;
});
