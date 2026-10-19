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
    const saved = JSON.parse(ctx._store.zoe_desktop_widgets);
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

Promise.all(pending).then(() => {
    const total = pending.length;
    console.log(`\ndashboard real data: ${passed}/${total} checks passed`);
    if (passed !== total) process.exitCode = 1;
});
