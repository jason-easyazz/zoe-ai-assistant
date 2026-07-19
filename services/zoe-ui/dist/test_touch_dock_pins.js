/*
 * Browser test for the estate dock's user-pinned controls (touch/home.html).
 *
 * WHY A REAL BROWSER, NOT A FAKE DOM
 * ----------------------------------
 * Its siblings (test_touch_panel_match.js, test_touch_conversation_mode.js)
 * evaluate the estate script against a hand-built fake DOM. That is fine for
 * pure logic, but it cannot see layout — and the failures this feature is most
 * likely to ship are layout failures: a dock that grows past 74px, a tile that
 * lands on top of #orb, a popover knob that reports the wrong temperature.
 * So this one drives the real page in headless Chromium at the panel's real
 * resolution (1280x720) and reads real bounding boxes.
 *
 * FIXTURE PROVENANCE — read this before changing any fixture below
 * ---------------------------------------------------------------
 * The pinned-config fixtures are NOT hand-written. They were produced by
 * running the REAL backend resolver over the REAL live Home Assistant entity
 * list, so their shape cannot drift from what the panel actually receives:
 *
 *   curl -s localhost:8000/api/ha/entities -o /tmp/live_entities.json
 *   cd services/zoe-data && python3 -c "
 *   import json,sys;sys.path.insert(0,'.')
 *   from routers.panel_config import build_config_payload
 *   e=json.load(open('/tmp/live_entities.json'))
 *   idx={x['entity_id']:x for x in (e if isinstance(e,list) else e['entities'])}
 *   pins=[{'read_eid':'input_boolean.bedroom_light','write_eid':'input_boolean.bedroom_light','name':'Bed'},
 *         {'read_eid':'input_boolean.fan','write_eid':'input_boolean.fan','name':'Fan'},
 *         {'read_eid':'scene.good_night','write_eid':'scene.good_night','name':'Night'},
 *         {'read_eid':'sensor.current_temperature','write_eid':'input_number.thermostat_temperature','name':'Temp'}]
 *   print(json.dumps(build_config_payload('zoe-touch-pi','bedroom',None,'living',pins,idx),indent=1))"
 *
 * On top of that, assertLiveContract() below re-fetches the LIVE endpoint when
 * it is reachable and asserts the fixture's key set matches it exactly. A
 * passing suite against invented fields is the specific way this codebase has
 * shipped dead UI before, so the fixtures are checked, not trusted.
 *
 * Run:  node services/zoe-ui/dist/test_touch_dock_pins.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  DOCK_PIN_SHOTS=<dir>
 * (Not in CI — the repo has no JS lane. This is a local verification gate.)
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');
// Resolve Playwright and Chromium without hardcoding one machine's cache.
// Order: env override -> normal node resolution (node_modules) -> the Zoe
// box's known-good paths. A missing browser exits with an actionable message
// rather than a stack trace, so this stays runnable on a fresh clone.
const PW_CANDIDATES = [
  process.env.PLAYWRIGHT_CORE,
  'playwright-core',
  'playwright',
  '/home/zoe/.openclaw/npm/node_modules/playwright-core',
].filter(Boolean);

function loadChromium() {
  for (const c of PW_CANDIDATES) {
    try { return require(c).chromium; } catch (_) { /* try the next one */ }
  }
  return null;
}

function findChrome(chromium) {
  const explicit = process.env.CHROME_PATH || process.env.PLAYWRIGHT_CHROMIUM;
  if (explicit && fs.existsSync(explicit)) return explicit;
  // Playwright knows where it installed its own browser.
  try { const p = chromium.executablePath(); if (p && fs.existsSync(p)) return p; } catch (_) {}
  const known = [
    '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
  ];
  return known.find((p) => fs.existsSync(p)) || null;
}

const chromium = loadChromium();
if (!chromium) {
  console.error('playwright-core not found. Install it (npm i -D playwright-core) or set\n'
    + 'PLAYWRIGHT_CORE=/path/to/playwright-core. On the Zoe box it lives at\n'
    + '/home/zoe/.openclaw/npm/node_modules/playwright-core.');
  process.exit(2);
}
const CHROME = findChrome(chromium);
if (!CHROME) {
  console.error('No Chromium binary found. Run `npx playwright install chromium`,\n'
    + 'or set CHROME_PATH=/path/to/chrome.');
  process.exit(2);
}
const DIST = __dirname;
const SHOTS = process.env.DOCK_PIN_SHOTS || '/tmp/dock-pins-shots';

// ── fixtures (see provenance header) ────────────────────────────────────────
const PIN_BED = {
  name: 'Bed', kind: 'toggle',
  read_eid: 'input_boolean.bedroom_light', write_eid: 'input_boolean.bedroom_light',
  write_action: 'toggle', state: 'off', setpoint: null,
  friendly_name: 'Bedroom Light', icon: 'mdi:ceiling-light', available: true,
  min: null, max: null, step: null, unit: null,
};
const PIN_FAN = {
  name: 'Fan', kind: 'toggle',
  read_eid: 'input_boolean.fan', write_eid: 'input_boolean.fan',
  write_action: 'toggle', state: 'off', setpoint: null,
  friendly_name: 'Ceiling Fan', icon: 'mdi:fan', available: true,
  min: null, max: null, step: null, unit: null,
};
const PIN_NIGHT = {
  name: 'Night', kind: 'scene',
  read_eid: 'scene.good_night', write_eid: 'scene.good_night',
  write_action: 'turn_on', state: '2026-04-06T04:58:23.796694+00:00', setpoint: null,
  friendly_name: 'Good Night', icon: 'mdi:palette-outline', available: true,
  min: null, max: null, step: null, unit: null,
};
// The live thermostat: a read sensor + a write input_number, 16-30 step 0.5.
// `state`/`setpoint` really do arrive as STRINGS — that is why the slider
// parses with parseFloat rather than parseInt.
const PIN_TEMP = {
  name: 'Temp', kind: 'temp',
  read_eid: 'sensor.current_temperature', write_eid: 'input_number.thermostat_temperature',
  write_action: 'set_value', state: '21.0', setpoint: '21.0',
  friendly_name: 'Current Temperature', icon: 'mdi:thermometer', available: true,
  min: 16.0, max: 30.0, step: 0.5, unit: '°C',
};

function cfg(over) {
  return Object.assign({
    device_id: 'zoe-touch-pi', location: 'bedroom',
    // The panel's Zoe room, added by the rooms work. Present-but-null is the
    // real shape for a panel in no room — the client must never branch on key
    // existence, so these are always sent. assertLiveContract() compares this
    // key set against the live endpoint and caught their absence the moment
    // the API shipped them.
    room_id: null, room_name: null, room_slug: null,
    default_player: 'living', default_player_source: 'global',
    pins_configured: true, pinned: [], unresolved: [],
    ha_available: true, max_pins: 4,
  }, over || {});
}

// Real entity rows, copied from the live /api/ha/entities (used by the
// unpinned fallback path and the settings entity picker).
const HA_ENTITIES = [
  { entity_id: 'input_boolean.living_room_light', state: 'off', attributes: { friendly_name: 'Living Room Light', icon: 'mdi:ceiling-light' } },
  { entity_id: 'input_boolean.kitchen_light', state: 'on', attributes: { friendly_name: 'Kitchen Light', icon: 'mdi:ceiling-light' } },
  { entity_id: 'input_boolean.bedroom_light', state: 'off', attributes: { friendly_name: 'Bedroom Light', icon: 'mdi:ceiling-light' } },
  { entity_id: 'input_boolean.porch_light', state: 'off', attributes: { friendly_name: 'Porch Light', icon: 'mdi:outdoor-lamp' } },
  { entity_id: 'input_boolean.fan', state: 'off', attributes: { friendly_name: 'Ceiling Fan', icon: 'mdi:fan' } },
  { entity_id: 'input_boolean.tv', state: 'off', attributes: { friendly_name: 'TV', icon: 'mdi:television' } },
  { entity_id: 'scene.good_night', state: 'unknown', attributes: { friendly_name: 'Good Night' } },
  { entity_id: 'input_number.thermostat_temperature', state: '21.0', attributes: { friendly_name: 'Thermostat', icon: 'mdi:thermostat', min: 16.0, max: 30.0, step: 0.5, unit_of_measurement: '°C' } },
  { entity_id: 'sensor.current_temperature', state: '21.0', attributes: { friendly_name: 'Current Temperature', icon: 'mdi:thermometer' } },
];

const SATURATED_TITLE = 'Everything In Its Right Place (Remastered)';

// ── plumbing ────────────────────────────────────────────────────────────────
function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    // Resolve, then anchor the containment check at a separator: a bare
    // startsWith(DIST) also accepts a SIBLING whose name merely begins with it
    // (dist2, dist-legacy), because the prefix matches with no path boundary.
    const file = path.resolve(DIST, '.' + path.sep + rel);
    if ((file !== DIST && !file.startsWith(DIST + path.sep))
        || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end('nope'); return;
    }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}

// Every write the dock makes, in order, so assertions can inspect them.
function newCtx() { return { writes: [], puts: [] }; }

async function stub(page, ctx, opts) {
  const o = opts || {};
  const panelCfg = o.cfg || cfg();
  const nowPlaying = o.nowPlaying || null;
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

    if (req.method() === 'POST' && url.includes('/api/ha/control')) {
      ctx.writes.push(JSON.parse(req.postData() || '{}'));
      return json({ ok: true });
    }
    if (req.method() === 'PUT' && url.includes('/api/panels/')) {
      ctx.puts.push(JSON.parse(req.postData() || '{}'));
      return json(panelCfg);
    }
    if (url.includes('/api/panels/')) return json(panelCfg);
    if (url.includes('/api/ha/entities')) return json(o.entities || HA_ENTITIES);
    if (url.includes('/api/music/now-playing')) {
      return json(nowPlaying ? { now_playing: nowPlaying } : { now_playing: null });
    }
    if (url.includes('/api/music/players')) {
      return json({ available: true, players: [{ player_id: 'living', name: 'Living Room', provider: 'sonos' }] });
    }
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    if (url.includes('/api/system/status')) return json({ llama_server: 'ok', ha_bridge: 'ok', database: 'ok' });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    return json({});
  });
}

async function open(browser, ctx, opts) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  // The dock refreshes on a 30s setInterval and NOTHING else triggers it —
  // there is no focus/visibilitychange listener. Any test claiming to exercise
  // a refresh must drive that timer, or it asserts against a page that never
  // re-rendered and passes for the wrong reason.
  if (opts && opts.clock) await page.clock.install();
  await stub(page, ctx, opts);
  await page.goto((opts && opts.base) + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  // #authov ("Who's here?") covers the screen and swallows clicks in a harness.
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => {
    const b = document.getElementById('dbody');
    return b && !b.textContent.includes('…');
  }, { timeout: 8000 });
  return page;
}

const tiles = (page) => page.$$eval('#dbody .pc.pin', (els) => els.map((e) => ({
  cls: e.className,
  k: e.getAttribute('data-k'),
  eid: e.getAttribute('data-eid'),
  act: e.getAttribute('data-act'),
  nm: (e.querySelector('.nm') || e.querySelector('.tl') || {}).textContent,
  svg: (e.querySelector('svg') || {}).innerHTML,
  w: e.getBoundingClientRect().width,
})));

async function shoot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const f = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: f });
  return f;
}

// Drive the real launcher -> Settings -> expand "This panel", the way a finger
// would. No internal function is called directly.
async function openPanelSettings(page) {
  await page.click('#apps');
  await page.waitForTimeout(450);
  await page.click('.ltile[data-id="settings"]');
  await page.waitForTimeout(700);
  await page.evaluate(() => {
    document.querySelectorAll('.setsec .sh').forEach((h) => {
      if (/This panel/.test(h.textContent)) h.click();
    });
  });
  await page.waitForTimeout(400);
  await page.waitForSelector('#setPanel .srow2[data-p="pins"]', { timeout: 5000 });
}

function overlaps(a, b) {
  return !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
}

// ── the live-contract guard ─────────────────────────────────────────────────
// Fixtures that invent fields are how dead UI ships while tests pass. When the
// live API is up, assert our fixture's key set IS the live key set.
async function assertLiveContract() {
  const live = await new Promise((resolve) => {
    const req = http.get('http://localhost:8000/api/panels/zoe-touch-pi/config', (res) => {
      let s = ''; res.on('data', (d) => (s += d));
      res.on('end', () => { try { resolve(JSON.parse(s)); } catch (_) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(2500, () => { req.destroy(); resolve(null); });
  });
  if (!live) { console.log('  ~ live API unreachable — fixture/live key check SKIPPED'); return false; }

  const liveTop = Object.keys(live).sort();
  const fixTop = Object.keys(cfg()).sort();
  assert.deepStrictEqual(fixTop, liveTop,
    'fixture top-level keys drifted from the live API\n live: ' + liveTop + '\n fix:  ' + fixTop);

  // Pin key set: compare against a live pin when one exists, else against the
  // backend's own documented stable key set.
  const EXPECT_PIN_KEYS = ['available', 'friendly_name', 'icon', 'kind', 'max', 'min', 'name',
    'read_eid', 'setpoint', 'state', 'step', 'unit', 'write_action', 'write_eid'].sort();
  for (const p of [PIN_BED, PIN_FAN, PIN_NIGHT, PIN_TEMP]) {
    assert.deepStrictEqual(Object.keys(p).sort(), EXPECT_PIN_KEYS, 'pin fixture key set drift: ' + p.name);
  }
  if (Array.isArray(live.pinned) && live.pinned.length) {
    assert.deepStrictEqual(Object.keys(live.pinned[0]).sort(), EXPECT_PIN_KEYS,
      'LIVE pin key set differs from fixture pin key set');
    console.log('  ✓ fixture keys match the LIVE API (top-level + a live pin)');
  } else {
    console.log('  ✓ fixture top-level keys match the LIVE API (live panel has no pins to compare)');
  }
  return true;
}

// ── tests ───────────────────────────────────────────────────────────────────
const results = [];
async function t(name, fn) {
  try { await fn(); results.push([true, name]); console.log('  ✓ ' + name); }
  catch (e) { results.push([false, name]); console.log('  ✗ ' + name + '\n      ' + e.message); }
}

(async () => {
  console.log('\ndock pinned controls — 1280x720 headless\n');
  await assertLiveContract();

  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

  // 1. unconfigured => the old fallback survives untouched
  await t('pins_configured:false keeps the slice(0,3) fallback', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cls: cfg({ pins_configured: false }) , cfg: cfg({ pins_configured: false }) });
    const pins = await tiles(page);
    assert.strictEqual(pins.length, 0, 'no .pin tiles should render when unconfigured');
    const fallback = await page.$$eval('#dbody .pc.light', (e) => e.length);
    assert.strictEqual(fallback, 3, 'fallback should render 3 light pills, got ' + fallback);
    await shoot(page, '1_fallback');
    await page.close();
  });

  // 2. pins_configured:true + [] => show nothing (NOT the fallback)
  await t('pins_configured:true with no pins shows nothing', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [] }) });
    assert.strictEqual(await page.$$eval('#dbody .pc.light', (e) => e.length), 0,
      'an explicit empty pin list must not fall back to lights');
    await page.close();
  });

  // 3. each kind renders in its own language
  await t('toggle / scene / temp each render correctly', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_BED, PIN_FAN, PIN_NIGHT, PIN_TEMP] }) });
    const pins = await tiles(page);
    assert.strictEqual(pins.length, 4, 'expected 4 tiles');

    assert.ok(/\bpc pin light\b/.test(pins[0].cls), 'toggle uses the .pc.light pill');
    assert.strictEqual(pins[0].nm, 'Bed');
    assert.ok(!/\bon\b/.test(pins[0].cls), 'state off => no .on');

    // The load-bearing icon assertion: a fan must NOT draw a lightbulb.
    assert.notStrictEqual(pins[1].svg, pins[0].svg, 'fan and light must not share an icon');

    assert.ok(/\bscene\b/.test(pins[2].cls), 'scene uses .pc.scene');
    assert.ok(!/\bon\b/.test(pins[2].cls), 'a scene must NEVER carry state');
    assert.strictEqual(pins[2].nm, 'Night');

    assert.ok(/\btemp\b/.test(pins[3].cls), 'temp uses .pc.temp');
    assert.strictEqual(await page.$eval('#dbody .pc.temp .tv', (e) => e.textContent), '21°');
    await shoot(page, '3_all_kinds');
    await page.close();
  });

  // 4. an "on" toggle glows, and writes the deterministic service
  await t('toggle writes turn_off when switching a lit pin off', async () => {
    const ctx = newCtx();
    const lit = Object.assign({}, PIN_BED, { state: 'on' });
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [lit] }) });
    assert.ok(await page.$eval('#dbody .pc.pin', (e) => e.classList.contains('on')), 'state on => .on');
    await page.click('#dbody .pc.pin');
    await page.waitForTimeout(200);
    assert.strictEqual(ctx.writes.length, 1);
    assert.strictEqual(ctx.writes[0].entity_id, 'input_boolean.bedroom_light');
    assert.strictEqual(ctx.writes[0].action, 'turn_off');
    await page.close();
  });

  // 5. THE temp test: real range, real step, right service, right param key
  await t('temp popover writes set_value snapped to the real 16-30/0.5 range', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_TEMP] }) });
    await page.click('#dbody .pc.temp');
    await page.waitForTimeout(350);
    assert.ok(await page.$eval('#dbody .pc.temp', (e) => e.classList.contains('open')), 'tap opens .tpop');
    await shoot(page, '5_temp_open');

    const sl = await page.$('#dbody .pc.temp .tsl');
    const box = await sl.boundingBox();

    // Drag to the very top => must clamp to max (30), not the old hardcoded 28.
    await page.mouse.move(box.x + box.width / 2, box.y + 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y - 40);
    await page.mouse.up();
    await page.waitForTimeout(200);
    let w = ctx.writes[ctx.writes.length - 1];
    assert.strictEqual(w.action, 'set_value', 'must use the pin write_action, not set_temperature');
    assert.ok('value' in w.params, 'set_value takes {value}, got ' + JSON.stringify(w.params));
    assert.strictEqual(w.params.value, 30, 'top of the slider is max=30, got ' + w.params.value);

    // Drag to the exact middle => 23, and prove a half-step survives: a
    // position 1/28th above the middle is 23.5, which parseInt would have eaten.
    const half = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, half);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2, half); await page.mouse.up();
    await page.waitForTimeout(200);
    w = ctx.writes[ctx.writes.length - 1];
    assert.strictEqual(w.params.value, 23, 'middle of 16-30 is 23, got ' + w.params.value);

    const oneStep = box.height / ((30 - 16) / 0.5);
    await page.mouse.move(box.x + box.width / 2, half - oneStep);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2, half - oneStep); await page.mouse.up();
    await page.waitForTimeout(200);
    w = ctx.writes[ctx.writes.length - 1];
    assert.strictEqual(w.params.value, 23.5, 'one 0.5 step above middle is 23.5, got ' + w.params.value);

    // Bottom clamps to min=16, not the old hardcoded 15. The press must START
    // inside the slider (pointer capture then follows the finger off the end) —
    // a pointerdown outside it is not a drag at all.
    await page.mouse.move(box.x + box.width / 2, box.y + box.height - 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height + 60);
    await page.mouse.up();
    await page.waitForTimeout(200);
    w = ctx.writes[ctx.writes.length - 1];
    assert.strictEqual(w.params.value, 16, 'bottom of the slider is min=16, got ' + w.params.value);
    await page.close();
  });

  // 6. a REAL 30s poll must not destroy the popover mid-drag
  await t('a 30s refresh does not re-render while the temp popover is open', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, clock: true, cfg: cfg({ pinned: [PIN_TEMP] }) });
    await page.click('#dbody .pc.temp');
    await page.waitForTimeout(300);
    await page.evaluate(() => { window.__tile = document.querySelector('#dbody .pc.temp'); });
    // Drive the real interval, twice over, and let its fetches settle.
    await page.clock.runFor(31000);
    await page.waitForTimeout(600);
    await page.clock.runFor(31000);
    await page.waitForTimeout(600);
    assert.ok(await page.evaluate(() => window.__tile === document.querySelector('#dbody .pc.temp')),
      'the open popover element was replaced by a poll');
    assert.ok(await page.$eval('#dbody .pc.temp', (e) => e.classList.contains('open')), 'still open');
    await page.close();
  });

  // 7. scene fires, flashes accent, never shows state
  await t('scene fires turn_on, flashes, and never shows state', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_NIGHT] }) });
    await page.click('#dbody .pc.scene');
    await page.waitForTimeout(120);
    assert.ok(await page.$eval('#dbody .pc.scene', (e) => e.classList.contains('fire')), 'flashes .fire');
    assert.ok(!(await page.$eval('#dbody .pc.scene', (e) => e.classList.contains('on'))), 'must never gain .on');
    // Let the .25s tile transition settle before sampling, then compare
    // CHANNELS rather than an exact string: the point is that it reads blue
    // (b > g > r), the opposite of the light pill's amber (r > g > b).
    await page.waitForTimeout(320);
    const bg = await page.$eval('#dbody .pc.scene', (e) => getComputedStyle(e).backgroundColor);
    const [r, g, b] = bg.match(/[\d.]+/g).map(Number);
    assert.ok(b > g && g > r, 'flash must read accent blue, not light amber; got ' + bg);
    await shoot(page, '7_scene_fire');
    assert.strictEqual(ctx.writes[0].action, 'turn_on');
    await page.waitForTimeout(950);
    assert.ok(!(await page.$eval('#dbody .pc.scene', (e) => e.classList.contains('fire'))), 'flash clears');
    await page.close();
  });

  // 8. HA down => pins still render
  await t('ha_available:false still renders the pins', async () => {
    const ctx = newCtx();
    const dark = [PIN_BED, PIN_NIGHT, PIN_TEMP].map((p) => Object.assign({}, p, {
      state: null, setpoint: null, available: false, friendly_name: null,
    }));
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: dark, ha_available: false }) });
    const pins = await tiles(page);
    assert.strictEqual(pins.length, 3, 'pins must survive an HA outage, got ' + pins.length);
    assert.ok(pins.every((p) => /unavail/.test(p.cls)), 'unavailable pins render muted');
    assert.ok(!pins.some((p) => /\bon\b/.test(p.cls)), 'no pin should claim to be on');
    await shoot(page, '8_ha_down');
    await page.close();
  });

  // 9. degraded pin: unknown entity dropped server-side, reported in unresolved
  await t('a stale pin degrades without breaking the dock', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, cfg: cfg({ pinned: [PIN_BED], unresolved: ['input_boolean.deleted_thing'] }),
    });
    assert.strictEqual((await tiles(page)).length, 1, 'the surviving pin still renders');
    await page.close();
  });

  // 10. dock height budget
  await t('the dock stays within 74px tall', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base,
      cfg: cfg({ pinned: [PIN_BED, PIN_FAN, PIN_NIGHT, PIN_TEMP] }),
      nowPlaying: { state: 'playing', title: SATURATED_TITLE, artist: 'Radiohead', player_id: 'living' },
    });
    await page.waitForTimeout(400);
    const h = await page.$eval('#dock', (e) => e.getBoundingClientRect().height);
    assert.ok(h <= 74, 'dock is ' + h + 'px tall, budget is 74px');
    await page.close();
  });

  // 11. no collision with the orb / home button
  await t('the dock does not collide with #orb or #home', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base,
      cfg: cfg({ pinned: [PIN_BED, PIN_FAN, PIN_NIGHT, PIN_TEMP] }),
      nowPlaying: { state: 'playing', title: SATURATED_TITLE, artist: 'Radiohead', player_id: 'living' },
    });
    await page.waitForTimeout(400);
    const boxes = await page.evaluate(() => {
      const r = (s) => { const e = document.querySelector(s); if (!e) return null;
        const st = getComputedStyle(e);
        if (st.display === 'none' || st.visibility === 'hidden' || +st.opacity === 0) return null;
        const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; };
      return { dock: r('#dock'), orb: r('#orb'), home: r('#home') };
    });
    for (const k of ['orb', 'home']) {
      if (!boxes[k]) continue;
      assert.ok(!overlaps(boxes.dock, boxes[k]),
        'dock overlaps #' + k + ': ' + JSON.stringify(boxes.dock) + ' vs ' + JSON.stringify(boxes[k]));
    }
    await shoot(page, '11_full_dock_music');
    await page.close();
  });

  // 12. the width budget the spec measured
  await t('4 pins + a saturated now-playing title fits 1280', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base,
      cfg: cfg({ pinned: [PIN_BED, PIN_FAN, PIN_NIGHT, PIN_TEMP] }),
      nowPlaying: { state: 'playing', title: SATURATED_TITLE, artist: 'Radiohead', player_id: 'living' },
    });
    await page.waitForTimeout(400);
    const b = await page.$eval('#dock', (e) => { const r = e.getBoundingClientRect(); return { w: r.width, x: r.x }; });
    console.log('      measured dock width with 4 pins + saturated music: ' + Math.round(b.w) + 'px');
    assert.ok(b.w <= 1280, 'dock is ' + b.w + 'px wide');
    assert.ok(b.x >= 0, 'dock overflows the left edge');
    await page.close();
  });

  // 13. settings: the panel section renders its three rows from the live shape
  await t('Settings › This panel renders room / rooms / location / speaker / dock controls', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_BED, PIN_TEMP] }) });
    await openPanelSettings(page);
    const rows = await page.$$eval('#setPanel .srow2', (els) => els.map((e) => e.textContent));
    assert.strictEqual(rows.length, 5, 'expected 5 rows, got ' + JSON.stringify(rows));
    // ROOM leads: it is the structured "where is this panel", and what makes
    // "turn off the light" mean the light in here. Null room reads "Not set".
    assert.ok(/Room/.test(rows[0]) && /Not set/.test(rows[0]), 'room row: ' + rows[0]);
    assert.ok(/Manage rooms/.test(rows[1]), 'rooms management row: ' + rows[1]);
    // The legacy free-text label keeps its own row, relabelled so it cannot be
    // mistaken for the room — nothing reads it for behaviour.
    assert.ok(/Location label/.test(rows[2]) && /bedroom/.test(rows[2]), rows[2]);
    assert.ok(/speaker/i.test(rows[3]) && /household default/.test(rows[3]),
      'speaker row must surface default_player_source: ' + rows[3]);
    assert.ok(/2 of 4/.test(rows[4]), 'pin count row: ' + rows[4]);
    await shoot(page, '13_settings_panel');
    await page.close();
  });

  // The room row must show the NAME, never the opaque id — the panel resolves
  // it server-side precisely so the operator never sees a uuid.
  await t('Settings › the room row shows the room NAME when the panel is in one', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base,
      cfg: cfg({ room_id: 'r-bed', room_name: 'Bedroom', room_slug: 'bedroom' }),
    });
    await openPanelSettings(page);
    const row = await page.$eval('#setPanel .srow2[data-p="room"]', (e) => e.textContent);
    assert.ok(/Bedroom/.test(row), 'room row should name the room: ' + row);
    assert.ok(!/r-bed/.test(row), 'room row must never show the raw id: ' + row);
    await page.close();
  });

  // 14. the one-word rule is enforced in the UI with a sentence, not a raw 400
  await t('the pin editor rejects a two-word name before it can hit the API', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [] }) });
    await openPanelSettings(page);
    await page.click('#setPanel .srow2[data-p="pins"]');
    await page.waitForTimeout(300);
    await page.click('#estModal .padd');
    await page.waitForTimeout(500);
    await page.click('#estModal .eopt');           // pick the first candidate entity
    await page.waitForTimeout(300);

    // The suggested name is already one word, derived from the friendly name.
    const suggested = await page.$eval('#estModal [data-f="nm"]', (e) => e.value);
    assert.ok(suggested && !/\s/.test(suggested), 'suggested name should be one word, got ' + suggested);

    await page.fill('#estModal [data-f="nm"]', 'Living Room');
    await page.click('#estModal [data-x="add"]');
    await page.waitForTimeout(200);
    const err = await page.$eval('#estModal .perr', (e) => e.textContent);
    assert.ok(/One word/i.test(err), 'expected a helpful one-word message, got: ' + err);
    assert.strictEqual(ctx.puts.length, 0, 'a bad name must never reach the API');
    await shoot(page, '14_oneword_rule');

    // A valid one-word name goes through and saves the pair the API expects.
    await page.fill('#estModal [data-f="nm"]', 'Living');
    await page.click('#estModal [data-x="add"]');
    await page.waitForTimeout(250);
    await page.click('#estModal [data-x="save"]');
    await page.waitForTimeout(400);
    assert.strictEqual(ctx.puts.length, 1, 'save should PUT once');
    const body = ctx.puts[0];
    assert.ok(Array.isArray(body.pinned) && body.pinned.length === 1, JSON.stringify(body));
    const p = body.pinned[0];
    assert.strictEqual(p.name, 'Living');
    assert.ok(p.read_eid && p.write_eid, 'must PUT the read/write pair form');
    assert.ok(!('entity_id' in p), 'mixing entity_id with the pair form is a 400 server-side');
    await page.close();
  });

  // 15. the pin editor can reorder and remove
  await t('the pin editor reorders and removes pins', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_BED, PIN_FAN, PIN_NIGHT] }) });
    await openPanelSettings(page);
    await page.click('#setPanel .srow2[data-p="pins"]');
    await page.waitForTimeout(300);
    assert.strictEqual((await page.$$('#estModal .pinrow')).length, 3);
    await shoot(page, '15_pin_editor');

    // move the 2nd pin up, then delete the last one (rows are not the only
    // children of .estmc, so index the rows themselves rather than nth-child)
    await page.click('#estModal [data-mv="1,-1"]');
    await page.waitForTimeout(250);
    await page.click('#estModal [data-rm="2"]');
    await page.waitForTimeout(250);
    await page.click('#estModal [data-x="save"]');
    await page.waitForTimeout(400);
    const names = ctx.puts[0].pinned.map((p) => p.name);
    assert.deepStrictEqual(names, ['Fan', 'Bed'], 'reorder+remove should yield Fan,Bed; got ' + names);
    await page.close();
  });

  // 16. an unresolved pin must not be silently deleted by pressing Save
  await t('Save is blocked while a pin is unresolved (no silent deletion)', async () => {
    const ctx = newCtx();
    // The operator has 2 pins stored; HA is only returning one of them.
    const page = await open(browser, ctx, {
      base, cfg: cfg({ pinned: [PIN_BED], unresolved: ['input_boolean.kitchen_light'] }),
    });
    await openPanelSettings(page);
    await page.click('#setPanel .srow2[data-p="pins"]');
    await page.waitForTimeout(300);
    const disabled = await page.$eval('#estModal [data-x="save"]', (e) => e.disabled);
    assert.ok(disabled, 'Save must be disabled while a pin is unresolved');
    const msg = await page.$eval('#estModal .perr', (e) => e.textContent);
    assert.ok(/Saving would delete (it|them)/.test(msg), 'must explain why: ' + msg);
    assert.ok(/1 pinned control isn’t/.test(msg), 'singular copy for one stale pin: ' + msg);
    await page.click('#estModal [data-x="save"]', { force: true }).catch(() => {});
    await page.waitForTimeout(300);
    assert.strictEqual(ctx.puts.length, 0, 'a truncated pin list must never be PUT');
    await shoot(page, '16_unresolved_guard');
    await page.close();
  });

  // 17. a failed config REFRESH must not revert a pinned dock to the fallback
  await t('a failed config refresh keeps the pinned dock (no fallback flip)', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, { base, clock: true, cfg: cfg({ pinned: [PIN_BED, PIN_NIGHT] }) });
    assert.strictEqual((await tiles(page)).length, 2, 'starts pinned');
    // Break the panel-config endpoint, then drive the REAL 30s refresh.
    await page.route('**/api/panels/**', (route) => route.fulfill({ status: 503, body: 'down' }));
    await page.clock.runFor(31000);
    await page.waitForTimeout(800);
    const after = await tiles(page);
    assert.strictEqual(after.length, 2, 'pins must survive a failed refresh, got ' + after.length);
    assert.strictEqual(await page.$$eval('#dbody .pc.light:not(.pin)', (e) => e.length), 0,
      'must NOT fall back to unrelated HA lights on a transient failure');
    await page.close();
  });

  // 18. BOOT-time config failure: the cached pins render, not the wrong devices
  await t('a config failure at BOOT renders cached pins, not the fallback', async () => {
    const ctx = newCtx();
    // First boot succeeds and warms the cache.
    let page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_BED, PIN_NIGHT] }) });
    assert.strictEqual((await tiles(page)).length, 2, 'warm-up boot should be pinned');
    // The cache is keyed by _panelDevId(), which the panel GENERATES on first
    // boot (zoe_touch_panel_id) — it is not 'default'. Read back whatever key
    // it actually used, and re-seed the same device identity next boot.
    const saved = await page.evaluate(() => {
      const k = Object.keys(localStorage).find((x) => x.indexOf('zoe_panel_cfg_') === 0);
      return { key: k, val: k ? localStorage.getItem(k) : null,
        devId: localStorage.getItem('zoe_touch_panel_id') };
    });
    assert.ok(saved.key && saved.val, 'config should be cached under a device-scoped key');
    assert.strictEqual(JSON.parse(saved.val).pinned.length, 2, 'both pins cached');
    await page.close();

    // Second boot: the config endpoint is down from the very first request.
    page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await stub(page, ctx, { cfg: cfg() });
    await page.route('**/api/panels/**', (route) => route.fulfill({ status: 503, body: 'down' }));
    await page.addInitScript((s) => {
      if (s.devId) localStorage.setItem('zoe_touch_panel_id', s.devId);
      localStorage.setItem(s.key, s.val);
    }, saved);
    await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
    await page.addStyleTag({ content: '#authov{display:none !important}' });
    await page.waitForTimeout(1500);

    const pins = await tiles(page);
    assert.strictEqual(pins.length, 2, 'cached pins must render at boot, got ' + pins.length);
    assert.strictEqual(await page.$$eval('#dbody .pc.light:not(.pin)', (e) => e.length), 0,
      'must NOT render unrelated fallback lights when the operator has saved pins');
    // Cached pins must not assert a stale on/off — state is not durable.
    assert.ok(pins.every((p) => /unavail/.test(p.cls)), 'cached pins render state-less');
    await shoot(page, '18_boot_cached');
    await page.close();
  });

  // 19. a malformed 200 is "we didn't learn the config", not "no pins"
  await t('a malformed 200 at boot falls back to the cache, not to HA lights', async () => {
    const ctx = newCtx();
    let page = await open(browser, ctx, { base, cfg: cfg({ pinned: [PIN_BED, PIN_NIGHT] }) });
    const saved = await page.evaluate(() => {
      const k = Object.keys(localStorage).find((x) => x.indexOf('zoe_panel_cfg_') === 0);
      return { key: k, val: k ? localStorage.getItem(k) : null,
        devId: localStorage.getItem('zoe_touch_panel_id') };
    });
    await page.close();

    page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await stub(page, ctx, { cfg: cfg() });
    // 200 OK, but not the documented shape (e.g. a sick proxy returning a blob).
    await page.route('**/api/panels/**', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ error: 'nope' }),
    }));
    await page.addInitScript((s) => {
      if (s.devId) localStorage.setItem('zoe_touch_panel_id', s.devId);
      localStorage.setItem(s.key, s.val);
    }, saved);
    await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
    await page.addStyleTag({ content: '#authov{display:none !important}' });
    await page.waitForTimeout(1500);

    assert.strictEqual((await tiles(page)).length, 2, 'cached pins must render on a malformed 200');
    assert.strictEqual(await page.$$eval('#dbody .pc.light:not(.pin)', (e) => e.length), 0,
      'a bad response must not put unrelated controls on a pinned dock');
    await page.close();
  });

  // 20. a pin going unresolved WHILE the editor is open must still block Save
  await t('Save re-checks unresolved at click time, not paint time', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true, cfg: cfg({ pinned: [PIN_BED, PIN_FAN] }),
    });
    await openPanelSettings(page);
    await page.click('#setPanel .srow2[data-p="pins"]');
    await page.waitForTimeout(300);
    // Editor opened with everything resolved: Save is live.
    assert.ok(!(await page.$eval('#estModal [data-x="save"]', (e) => e.disabled)),
      'Save should start enabled when all pins resolve');

    // Now HA drops one, and the 30s refresh picks that up while the modal sits open.
    await page.route('**/api/panels/**', (route) => {
      if (route.request().method() === 'PUT') {
        ctx.puts.push(JSON.parse(route.request().postData() || '{}'));
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(cfg({ pinned: [PIN_BED], unresolved: ['input_boolean.fan'] })) });
    });
    await page.clock.runFor(31000);
    await page.waitForTimeout(700);

    await page.click('#estModal [data-x="save"]', { force: true });
    await page.waitForTimeout(400);
    assert.strictEqual(ctx.puts.length, 0,
      'Save must not PUT a list that would delete the newly-unresolved pin');
    assert.ok(await page.$eval('#estModal [data-x="save"]', (e) => e.disabled),
      'the repaint should disable Save and explain');
    await page.close();
  });

  // ── the night screen renders the SAME pins ──────────────────────────────────
  // It used to render `_ha.lights.slice(0,4)` with a hardcoded BULB: it ignored
  // the operator's pins entirely and drew a light bulb on the fan — the exact
  // domain-derived-icon bug the dock resolves server-side. These cases pin the
  // SOURCE and SEMANTICS, not the sizing: sleep tiles stay large deliberately,
  // because that surface is hit in the dark.

  const sleepTiles = (page) => page.$$eval('#slDock .sc', (els) => els.map((e) => ({
    name: (e.querySelector('.nm') || {}).textContent || '',
    on: e.classList.contains('on'),
    kind: e.getAttribute('data-k'),
    eid: e.getAttribute('data-eid'),
    unavail: e.classList.contains('unavail'),
    icon: (e.querySelector('svg') || {}).innerHTML || '',
  })));

  // The real route in: no touch for IDLE_SLEEP_MS (180s). `show()` is scoped
  // inside the estate's IIFE, so a test cannot call it — and a window hook
  // added for the harness would prove less than driving the actual timer.
  async function toSleep(page) {
    // Reaching the night clock is NOT just "advance past IDLE_SLEEP_MS". When
    // the idle timer fires the estate asks the server whether music is playing
    // (deliberately, rather than trusting a cached flag) and only then calls
    // show('sleep'). That decision races the request against a 4s fallback
    // timer, so two different clocks are involved:
    //   * the request resolves in REAL time — a mocked-clock tick does not
    //     advance it, so we have to yield actual time for the promise;
    //   * the 4s fallback is a setTimeout, so it needs mocked time if the
    //     request lost the race.
    // Driving only one of them leaves `.slp` present-but-hidden, which is
    // exactly how this helper failed its first run.
    await page.clock.runFor(181000);   // the idle window elapses
    await page.waitForTimeout(400);    // let the now-playing request settle
    await page.clock.runFor(5000);     // …or let the 4s fallback decide
    await page.waitForSelector('.slp', { state: 'visible', timeout: 8000 });
    await page.waitForTimeout(400);
  }

  await t('sleep renders the operator pins, in order, with the server icon', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true, cfg: cfg({ pinned: [PIN_BED, PIN_FAN] }),
    });
    await toSleep(page);
    const st = await sleepTiles(page);
    assert.deepStrictEqual(st.map((s) => s.name), ['Bed', 'Fan'],
      'sleep must show the pins in the operator order, not HA friendly names');
    assert.deepStrictEqual(st.map((s) => s.eid),
      ['input_boolean.bedroom_light', 'input_boolean.fan'],
      'sleep tiles must act on the pinned WRITE entities');
    // The bug this whole change exists to kill: every tile drew the same bulb.
    assert.notStrictEqual(st[0].icon, st[1].icon,
      'the fan must not draw the light icon — sleep hardcoded BULB for every tile');
    await page.close();
  });

  await t('sleep keeps the legacy fallback when nobody has pinned anything', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true, cfg: cfg({ pins_configured: false, pinned: [] }),
    });
    await toSleep(page);
    assert.ok((await sleepTiles(page)).length > 0,
      'pins_configured:false must not leave a fresh panel with an empty night screen');
    await page.close();
  });

  await t('sleep shows nothing when the operator pinned nothing', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true, cfg: cfg({ pins_configured: true, pinned: [] }),
    });
    await toSleep(page);
    assert.strictEqual((await sleepTiles(page)).length, 0,
      'pins_configured:true with an empty list is a real choice and must NOT fall back');
    await page.close();
  });

  await t('sleep temp tile is adjustable and survives the 30s refresh', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true, cfg: cfg({ pinned: [PIN_TEMP] }),
    });
    await toSleep(page);
    await page.click('#slDock .sc.temp');
    await page.waitForTimeout(250);
    assert.ok(await page.$('#slDock .sc.temp.open'),
      'tapping the night temp tile must open its popover');
    // Same trap as the dock: the poll would wipe the popover out from under a
    // drag. Driving the timer is the only way to prove the guard holds.
    await page.clock.runFor(31000);
    await page.waitForTimeout(500);
    assert.ok(await page.$('#slDock .sc.temp.open'),
      'the 30s refresh must not destroy an open popover mid-drag');
    await page.close();
  });

  await t('an unreachable pin renders muted on sleep, never blank', async () => {
    const ctx = newCtx();
    const page = await open(browser, ctx, {
      base, clock: true,
      cfg: cfg({ pinned: [Object.assign({}, PIN_BED, { available: false })] }),
    });
    await toSleep(page);
    const st = await sleepTiles(page);
    assert.strictEqual(st.length, 1, 'an unavailable pin must still render — the operator chose it');
    assert.ok(st[0].unavail, 'it must be visibly muted so a dead control cannot pass as live');
    await page.close();
  });

  await browser.close();
  srv.close();

  const failed = results.filter((r) => !r[0]);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' passed');
  console.log('screenshots: ' + SHOTS);
  if (failed.length) { console.log('FAILED:\n  ' + failed.map((f) => f[1]).join('\n  ')); process.exit(1); }
})().catch((e) => { console.error(e); process.exit(1); });
