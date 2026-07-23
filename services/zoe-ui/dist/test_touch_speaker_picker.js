/*
 * Browser test for the typed speaker picker (touch/home.html).
 *
 * The operator reported issues about "Play on…": it should be BIGGER, he "still
 * can't select the bedroom Sonos", it should show WHAT each device is (TV /
 * speaker / …), and — after the panel-as-a-speaker work grew the roster past
 * what fits — "the cancel button doesnt fit ... optimise the page to fit on the
 * touch panels display", plus label the panel's own output ("Zoe Panel", its
 * room, and that it's THIS device). The Bedroom bug is the two-same-name hazard
 * (a live Sonos Beam and a dead AirPlay Apple TV); the panel's own output
 * repeats it (an available "Zoe Panel (AirPlay)" and a dead one of the SAME
 * name). This test proves the picker: renders every typed tile, tells
 * same-named players apart,
 * marks the unavailable ones, CAPS the modal to the 720px stage so the grid
 * scrolls internally while the Cancel button stays on-screen, badges ONLY the
 * available self as "This device" (with its room) and hoists it first, and still
 * posts the right call.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Every claim is a LAYOUT/RENDER claim — "the modal fits the stage", "the two
 * Bedroom tiles look different", "the dead one is dimmed". A fake DOM sees
 * none of that. This drives real headless Chromium at the panel's resolution and
 * reads real bounding boxes, and it SCREENSHOTS the open picker to be eyeballed.
 *
 * FIXTURE PROVENANCE — read before changing any fixture below
 * ----------------------------------------------------------
 * PLAYERS is a field-for-field capture of the live `GET /api/music/players`
 * (MA behind zoe-data, 2026-07-23/24) — ids/names/providers/types/device_info/
 * available copied verbatim. The roster CHURNS (a TV sleeps, a receiver is
 * removed), so entries carry `expectLive`; the contract check counts only the
 * live-expected ones rather than freezing a total. `kind`/`kind_label` are the fields the
 * server now adds (routers/music.py::resolve_player_kind); their values here are
 * the DESIGNED mapping, hand-written, and separately proven + mutation-tested by
 * services/zoe-data/tests/test_music_player_kind.py. assertLiveContract()
 * re-fetches /api/music/players when reachable and asserts every fixture
 * player's BASE fields match live (kind/kind_label are skipped there because
 * prod has not shipped this change yet — that is the point of the PR).
 *
 * Run:  node services/zoe-ui/dist/test_touch_speaker_picker.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  PICKER_SHOTS=<dir>
 *            ZOE_API=<http://host:port>  (live-contract check; default :8000)
 * (Not in CI — the repo has no browser JS lane. This is a local gate.)
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PW_CANDIDATES = [
  process.env.PLAYWRIGHT_CORE, 'playwright-core', 'playwright',
  '/home/zoe/.openclaw/npm/node_modules/playwright-core',
].filter(Boolean);
function loadChromium() {
  for (const c of PW_CANDIDATES) { try { return require(c).chromium; } catch (_) {} }
  return null;
}
function findChrome(chromium) {
  const explicit = process.env.CHROME_PATH || process.env.PLAYWRIGHT_CHROMIUM;
  if (explicit && fs.existsSync(explicit)) return explicit;
  try { const p = chromium.executablePath(); if (p && fs.existsSync(p)) return p; } catch (_) {}
  const known = [
    '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
  ];
  return known.find((p) => fs.existsSync(p)) || null;
}
const chromium = loadChromium();
if (!chromium) { console.error('playwright-core not found. Set PLAYWRIGHT_CORE=.'); process.exit(2); }
const CHROME = findChrome(chromium);
if (!CHROME) { console.error('No Chromium binary found. Set CHROME_PATH=.'); process.exit(2); }

const DIST = __dirname;
const SHOTS = process.env.PICKER_SHOTS || '/tmp/speaker-picker-shots';
const API = process.env.ZOE_API || 'http://127.0.0.1:8000';

// The player the card is "playing on" when the picker opens — a Sonos Arc in the
// Living Room. Distinct from the two Bedrooms so the current-highlight assertion
// is not circular with the tap-the-Sonos-Beam assertion.
const CURRENT = 'RINCON_38420B45B65001400';
const BEAM = 'RINCON_347E5C9BEC8F01400';   // the real Bedroom Sonos (available)
const APPLETV = 'ap40cbc0db9fb8';           // the dead Bedroom AirPlay (unavailable)
const SELF = 'up88a29e0a953f';              // the panel's OWN AirPlay output (available)
const SELF_DEAD = 'upc134dd3b3b2a';         // its dead AirPlay-1 predecessor, SAME name

// `expectLive:false` marks a fixture DELIBERATELY retained after it left the
// live roster, because it encodes a hazard worth keeping under test. The
// live-contract check skips those and counts only the rest, so ordinary MA
// churn (a TV going offline) cannot silently invalidate the whole fixture.
function P(player_id, name, provider, ptype, model, manufacturer, available, kind, kind_label, expectLive) {
  return {
    player_id, name, display_name: name, provider, type: ptype, available,
    device_info: { model, manufacturer },
    kind, kind_label,
    expectLive: expectLive !== false,
  };
}
// ── live-captured 14-player inventory (see provenance header) ────────────────
const PLAYERS = [
  P('up286412cf6eb7', 'Jason’s MacBook Pro (2)', 'universal_player', 'player', 'MacBook Pro (MacBookPro18,2)', 'Apple', true, 'computer', 'MacBook Pro'),
  P('upe0036b3da273', 'Samsung Q80CA 98', 'universal_player', 'player', 'QCQ80', 'Samsung', true, 'tv', 'Samsung TV'),
  P('up2ce55d131cec', '[LG] webOS TV OLED55B8STB', 'universal_player', 'player', 'OLED55B8STB', 'LG Electronics', true, 'tv', 'LG TV'),
  P('up00bfafdf46d2', 'NT72563_AU(192.168.1.234)', 'universal_player', 'player', 'TCL Media Renderer', 'TCL', false, 'tv', 'TCL TV'),
  P('up7931634c4d664cb4e3fa033fcb105adf', 'MAD Bedroom TV', 'universal_player', 'player', 'Smart TV', 'Unknown manufacturer', false, 'tv', 'Smart TV'),
  P('e0951e90-9fad-424a-84ac-08a1e0d720a6', 'House', 'chromecast', 'group', 'Google Cast Group', 'Google Inc.', true, 'group', 'Speaker group'),
  P('b72b454a-9e06-e7a2-61fd-ba158dd2c831', 'Kitchen Display', 'chromecast', 'player', 'Google Nest Hub', 'Google Inc.', true, 'display', 'Nest Hub'),
  P('60580bba-90c8-ae48-f22f-6b441b3d2a4e', 'Bedroom 2 TV', 'chromecast', 'player', 'Chromecast HD', 'Google Inc.', true, 'tv', 'Chromecast'),
  P('f2f19f55-f07f-4604-d698-cbb4d3da43bc', 'Bedroom 3 TV', 'chromecast', 'player', 'Chromecast HD', 'Google Inc.', true, 'tv', 'Chromecast'),
  P('07af8dad-cc27-a42f-dffb-b9025e92344b', 'Bathroom speaker', 'chromecast', 'player', 'Google Home Mini', 'Google Inc.', true, 'speaker', 'Home Mini'),
  P(CURRENT, 'Living Room', 'sonos', 'player', 'Arc', 'SONOS', true, 'speaker', 'Sonos Arc'),
  P(BEAM, 'Bedroom', 'sonos', 'player', 'Beam', 'SONOS', true, 'speaker', 'Sonos Beam'),
  P(APPLETV, 'Bedroom', 'airplay', 'player', 'Apple TV 4K', 'Apple', false, 'tv', 'Apple TV'),
  // Three shairport-sync AirPlay receivers ADDED this session (the panel-as-a-
  // speaker work): a stale "Zoe-touch" ghost, the panel's OWN output, and its
  // dead AirPlay-1 predecessor. The last two share the name "Zoe Panel (AirPlay)"
  // — the exact self-identification hazard the "This device" logic must handle.
  P(SELF, 'Zoe Panel (AirPlay)', 'universal_player', 'player', 'ShairportSync', 'AirPlay', true, 'speaker', 'AirPlay speaker'),
  // RETAINED after the operator cleared it from MA (2026-07-24): an available
  // player sharing its name with a dead one is the exact hazard the "This
  // device" badge must survive, and it WILL recur (every shairport restart
  // can strand a config). Not expected in the live API.
  P(SELF_DEAD, 'Zoe Panel (AirPlay)', 'universal_player', 'player', 'ShairportSync', 'AirPlay', false, 'speaker', 'AirPlay speaker', false),
  P('up7cdc65abf98f', 'Zoe-touch (AirPlay)', 'universal_player', 'player', 'ShairportSync', 'AirPlay', true, 'speaker', 'AirPlay speaker'),
];

const NOW_PLAYING = {
  player_id: CURRENT, player_name: 'Living Room', state: 'playing',
  title: 'Meet Joe Black', artist: 'Thomas Newman', album: '',
  image: 'https://i.ytimg.com/vi/x4fR5RhwyoM/maxresdefault.jpg',
  volume: 18, queue_id: CURRENT, queue_item_id: 'q1', queue_index: 0,
  shuffle: false, repeat: 'off', elapsed: 10, duration: 105, dont_stop: false,
};
const QUEUE = [{
  queue_id: CURRENT, queue_item_id: 'q1', name: 'Thomas Newman - Meet Joe Black',
  title: 'Meet Joe Black', artist: 'Thomas Newman',
  image: 'https://i.ytimg.com/vi/x4fR5RhwyoM/maxresdefault.jpg',
  index: 0, sort_index: 0, duration: 105, available: true,
  media_item: {}, streamdetails: {}, extra_attributes: {},
}];
const PANEL_CFG = {
  device_id: 'zoe-touch-pi', location: 'bedroom', room_id: 'r-bed', room_name: 'Bedroom',
  room_slug: 'bedroom', default_player: CURRENT, default_player_source: 'global',
  pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4,
};

// ── live-contract check ──────────────────────────────────────────────────────
function getJson(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: 4000 }, (res) => {
      let b = ''; res.on('data', (c) => { b += c; });
      res.on('end', () => { try { resolve(JSON.parse(b)); } catch (_) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}
async function assertLiveContract() {
  const d = await getJson(API + '/api/music/players');
  if (!d || !d.players) { console.log('  ~ live /api/music/players unreachable at ' + API + ' — contract check SKIPPED'); return; }
  const live = {};
  for (const p of d.players) live[p.player_id] = p;
  let checked = 0;
  for (const f of PLAYERS.filter((x) => x.expectLive)) {
    const l = live[f.player_id];
    if (!l) { assert.fail(`fixture player ${f.player_id} (${f.name}) is not in the LIVE API — fixture is stale`); }
    assert.strictEqual(l.name, f.name, `${f.player_id}: name drifted (live=${l.name})`);
    assert.strictEqual(l.provider, f.provider, `${f.player_id}: provider drifted (live=${l.provider})`);
    assert.strictEqual(l.type, f.type, `${f.player_id}: type drifted (live=${l.type})`);
    assert.strictEqual(l.available, f.available, `${f.player_id}: available drifted (live=${l.available})`);
    assert.strictEqual((l.device_info || {}).model, f.device_info.model, `${f.player_id}: model drifted (live=${(l.device_info||{}).model})`);
    checked++;
  }
  const expected = PLAYERS.filter((x) => x.expectLive).length;
  assert.strictEqual(checked, expected, `expected ${expected} live fixture players, checked ${checked}`);
  assert.strictEqual(d.players.length, expected, `live API now returns ${d.players.length} players, not ${expected} — fixture drifted`);
  console.log(`  ✓ all ${expected} live fixture players match the LIVE API base fields (${API})`);
}

// ── plumbing ─────────────────────────────────────────────────────────────────
function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(DIST, '.' + path.sep + rel);
    if ((file !== DIST && !file.startsWith(DIST + path.sep)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end('nope'); return;
    }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}
function coverSvg() {
  return Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="hsl(210,55%,42%)"/></svg>');
}
async function open(browser, ctx, base) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  await page.route((url) => !String(url).startsWith(base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: coverSvg() }));
  await page.route('**/api/**', async (route) => {
    const req = route.request(); const url = req.url();
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (req.method() === 'POST') { ctx.posts.push({ url, body: JSON.parse(req.postData() || '{}') }); return json({ ok: true }); }
    if (url.includes('/api/panels/')) return json(PANEL_CFG);
    if (url.includes('/api/music/now-playing')) return json({ available: true, now_playing: NOW_PLAYING });
    if (url.includes('/api/music/queue/')) return json({ available: true, items: QUEUE });
    if (url.includes('/api/music/players')) {
      let list = PLAYERS;
      if (ctx.padRoster) {
        list = PLAYERS.slice();
        for (let i = list.length; i < ctx.padRoster; i++) {
          list.push(P('pad' + i, 'Spare Speaker ' + i, 'chromecast', 'player', 'Google Home Mini', 'Google Inc.', true, 'speaker', 'Home Mini', false));
        }
      }
      return json({ available: true, players: list });
    }
    if (url.includes('/api/music/recently-played')) return json({ available: true, items: [] });
    if (url.includes('/api/music/playlists')) return json({ playlists: [] });
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    return json({});
  });
  await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => {
    const b = document.getElementById('dbody');
    return b && !b.textContent.includes('…');
  }, { timeout: 8000 });
  return page;
}
async function shoot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const f = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: f });
  return f;
}

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nspeaker picker — 1280x720\n');
  await assertLiveContract();

  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });
  const ctx = { posts: [] };
  const page = await open(browser, ctx, base);

  // Drive to the music card through the launcher, the way a user does.
  await page.click('#apps');
  await page.waitForSelector('#stage.lopen', { timeout: 4000 });
  await page.click('.ltile[data-id="music"]');
  await page.waitForFunction(() => !document.getElementById('stage').classList.contains('lopen'), null, { timeout: 4000 });
  await page.waitForSelector('.mfull .cfc.on', { timeout: 6000 });
  await page.waitForTimeout(400);

  // Open the picker.
  await page.click('#mSpk');
  await page.waitForSelector('.spkgrid .spkopt', { timeout: 4000 });
  await page.waitForTimeout(250);
  console.log('    shot: ' + await shoot(page, 'picker-open'));

  const tiles = () => page.$$eval('.spkgrid .spkopt', (els) => els.map((e) => ({
    pid: e.getAttribute('data-pid'),
    name: e.querySelector('.snm') ? e.querySelector('.snm').textContent : '',
    sub: e.querySelector('.ssub') ? e.querySelector('.ssub').textContent : '',
    off: e.classList.contains('off'),
    cur: e.classList.contains('cur'),
    icon: e.querySelector('.si svg') ? e.querySelector('.si svg').outerHTML : '',
    h: Math.round(e.getBoundingClientRect().height),
    w: Math.round(e.getBoundingClientRect().width),
  })));

  await t('every player renders as a tile', async () => {
    const ts = await tiles();
    assert.strictEqual(ts.length, PLAYERS.length, `rendered ${ts.length} tiles, expected ${PLAYERS.length}`);
  });

  // 17 players (the AirPlay receivers pushed the roster past 14) no longer fit
  // without scrolling. The operator's requirement changed accordingly: "the
  // cancel button doesnt fit ... optimise the page to fit on the touch panels
  // display." So the modal is CAPPED to the 720px stage, ONLY the grid scrolls
  // internally, and the title + Cancel row stay pinned and on-screen.
  await t('the picker fits the 720px stage — modal + Cancel never escape it', async () => {
    const m = await page.evaluate(() => {
      const grid = document.querySelector('.spkgrid');
      const mc = document.querySelector('.estmc');
      const cancel = document.querySelector('.estmc [data-x="cancel"]');
      const r = (el) => { const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom) }; };
      return {
        mc: r(mc), cancel: r(cancel),
        mcScroll: mc.scrollHeight, mcClient: mc.clientHeight,
        gridScroll: grid.scrollHeight, gridClient: grid.clientHeight,
        bodyScrollW: document.documentElement.scrollWidth, bodyClientW: document.documentElement.clientWidth,
        bodyScrollH: document.documentElement.scrollHeight, bodyClientH: document.documentElement.clientHeight,
      };
    });
    // The modal is a flex column capped to the stage: it must NOT itself scroll…
    assert.ok(m.mcScroll <= m.mcClient + 1, `the modal itself scrolls (${m.mcScroll} > ${m.mcClient}) — only the grid should`);
    // …it must sit entirely inside the 720px stage…
    assert.ok(m.mc.top >= 0 && m.mc.bottom <= 720, `modal escapes the stage (top=${m.mc.top} bottom=${m.mc.bottom})`);
    // …the Cancel button — the reported casualty — must be fully visible…
    assert.ok(m.cancel.top >= 0 && m.cancel.bottom <= 720, `Cancel button off-screen (top=${m.cancel.top} bottom=${m.cancel.bottom})`);
    // …any overflow is absorbed by the GRID, never by the modal or the page…
    // (deliberately not asserting the grid DOES overflow: that depends on how
    // many speakers happen to be powered on, and a test that only passes on a
    // busy night is a flaky test. The forced-overflow case below proves the
    // scroll path directly.)
    // …and the page body never scrolls in either axis.
    assert.ok(m.bodyScrollW <= m.bodyClientW + 1, `page scrolls horizontally (${m.bodyScrollW} > ${m.bodyClientW})`);
    assert.ok(m.bodyScrollH <= m.bodyClientH + 1, `page scrolls vertically (${m.bodyScrollH} > ${m.bodyClientH})`);
    console.log(`      modal ${m.mc.bottom - m.mc.top}px (top ${m.mc.top}, bottom ${m.mc.bottom}); Cancel bottom ${m.cancel.bottom}<=720; grid scrolls ${m.gridScroll}>${m.gridClient}`);
  });

  await t('the panel\'s OWN output is badged "This device", shows its room, and sits first', async () => {
    const ts = await tiles();
    // The badge lands on exactly one tile — the AVAILABLE self, never the dead
    // same-named ghost.
    const badged = await page.$$eval('.spkopt', (els) => els
      .filter((e) => e.querySelector('.sbadge'))
      .map((e) => ({ pid: e.getAttribute('data-pid'), badge: e.querySelector('.sbadge').textContent, off: e.classList.contains('off') })));
    assert.strictEqual(badged.length, 1, `expected exactly one "This device" badge, got ${badged.length}: ${JSON.stringify(badged)}`);
    assert.strictEqual(badged[0].pid, SELF, `badge is on ${badged[0].pid}, expected the live self ${SELF}`);
    assert.strictEqual(badged[0].badge, 'This device', `badge text was "${badged[0].badge}"`);
    assert.ok(!badged[0].off, 'the badged tile is the dimmed/unavailable one');
    // Its subtitle is the panel's room (from panel config), not the generic type.
    const self = ts.find((x) => x.pid === SELF);
    assert.strictEqual(self.sub, 'Bedroom', `self subtitle was "${self.sub}", expected the room "Bedroom"`);
    // The dead same-named ghost is NOT badged and stays marked unavailable.
    const dead = ts.find((x) => x.pid === SELF_DEAD);
    assert.ok(dead && dead.off && /Unavailable/i.test(dead.sub), `the dead "Zoe Panel" ghost is mishandled: ${JSON.stringify(dead)}`);
    // Self is hoisted to the front so it isn't hidden below the scroll.
    assert.strictEqual(ts[0].pid, SELF, `self is at index ${ts.findIndex((x) => x.pid === SELF)}, expected first`);
    console.log(`      "This device" on ${badged[0].pid} — "${self.name}" · ${self.sub}, at index 0`);
  });

  // The scroll path must be proven even on a quiet night when few speakers are
  // powered on. Force a roster far past what fits and re-open the picker.
  await t('with a roster too big to fit, the GRID scrolls and Cancel stays visible', async () => {
    await page.evaluate(() => {
      const el = document.querySelector('.estmc [data-x="cancel"]');
      if (el) el.click();
    });
    await page.waitForTimeout(200);
    ctx.padRoster = 30;                         // route below pads to 30 players
    await page.click('#mSpk');
    await page.waitForSelector('.spkgrid .spkopt', { timeout: 4000 });
    await page.waitForTimeout(250);
    const m = await page.evaluate(() => {
      const grid = document.querySelector('.spkgrid');
      const mc = document.querySelector('.estmc');
      const cancel = document.querySelector('.estmc [data-x="cancel"]');
      const r = (el) => { const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom) }; };
      return {
        tiles: document.querySelectorAll('.spkopt').length,
        gridScroll: grid.scrollHeight, gridClient: grid.clientHeight,
        mcScroll: mc.scrollHeight, mcClient: mc.clientHeight,
        mc: r(mc), cancel: r(cancel),
      };
    });
    assert.ok(m.tiles >= 30, `expected a padded roster, rendered ${m.tiles}`);
    assert.ok(m.gridScroll > m.gridClient, `grid did not overflow with ${m.tiles} tiles`);
    assert.ok(m.mcScroll <= m.mcClient + 1, 'the modal itself scrolled — only the grid should');
    assert.ok(m.mc.top >= 0 && m.mc.bottom <= 720, `modal escaped the stage (${m.mc.top}..${m.mc.bottom})`);
    assert.ok(m.cancel.top >= 0 && m.cancel.bottom <= 720, `Cancel off-screen (${m.cancel.top}..${m.cancel.bottom})`);
    console.log(`      ${m.tiles} tiles: grid ${m.gridScroll}>${m.gridClient}, Cancel bottom ${m.cancel.bottom}<=720`);
    ctx.padRoster = 0;
    await page.evaluate(() => { const el = document.querySelector('.estmc [data-x="cancel"]'); if (el) el.click(); });
    await page.waitForTimeout(200);
    await page.click('#mSpk');
    await page.waitForSelector('.spkgrid .spkopt', { timeout: 4000 });
    await page.waitForTimeout(200);
  });

  await t('every tile is a >=48px finger target', async () => {
    const ts = await tiles();
    const small = ts.filter((x) => x.h < 48 || x.w < 48);
    assert.deepStrictEqual(small, [], `some tiles are under 48px: ${JSON.stringify(small.map((x) => x.name + ' ' + x.w + 'x' + x.h))}`);
  });

  await t('the two "Bedroom" tiles are tellable apart (different subtitle AND icon)', async () => {
    const ts = await tiles();
    const beds = ts.filter((x) => x.name === 'Bedroom');
    assert.strictEqual(beds.length, 2, `expected exactly two "Bedroom" tiles, got ${beds.length}`);
    const beam = beds.find((x) => x.pid === BEAM);
    const atv = beds.find((x) => x.pid === APPLETV);
    assert.ok(beam && atv, 'could not find both Bedroom tiles by id');
    assert.notStrictEqual(beam.sub, atv.sub, `both Bedrooms show the same subtitle "${beam.sub}"`);
    assert.ok(/Sonos Beam/.test(beam.sub), `Sonos Bedroom subtitle was "${beam.sub}"`);
    assert.ok(/Apple TV/.test(atv.sub), `AirPlay Bedroom subtitle was "${atv.sub}"`);
    assert.notStrictEqual(beam.icon, atv.icon, 'both Bedrooms drew the SAME type icon');
    console.log(`      "${beam.name}" -> ${beam.sub}  vs  "${atv.name}" -> ${atv.sub}`);
  });

  await t('the unavailable AirPlay Bedroom is dimmed and marked Unavailable', async () => {
    const ts = await tiles();
    const atv = ts.find((x) => x.pid === APPLETV);
    assert.ok(atv, 'AirPlay Bedroom tile missing');
    assert.ok(atv.off, 'AirPlay Bedroom tile is not dimmed (.off missing)');
    assert.ok(/Unavailable/i.test(atv.sub), `AirPlay Bedroom not marked unavailable: "${atv.sub}"`);
    // available Sonos Beam must NOT be dimmed
    const beam = ts.find((x) => x.pid === BEAM);
    assert.ok(!beam.off, 'the live Sonos Beam was wrongly dimmed');
  });

  await t('the currently-playing speaker keeps the accent highlight', async () => {
    const ts = await tiles();
    const cur = ts.filter((x) => x.cur);
    assert.strictEqual(cur.length, 1, `expected exactly one highlighted tile, got ${cur.length}`);
    assert.strictEqual(cur[0].pid, CURRENT, `highlight is on ${cur[0].pid}, expected ${CURRENT}`);
    const border = await page.evaluate((pid) => {
      const el = document.querySelector('.spkopt[data-pid="' + pid + '"]');
      return getComputedStyle(el).borderTopColor;
    }, CURRENT);
    assert.notStrictEqual(border, 'rgba(0, 0, 0, 0)', 'current tile has no visible accent border');
  });

  await t('tapping the Bedroom SONOS posts a transfer to the Sonos Beam id', async () => {
    ctx.posts.length = 0;
    await page.click('.spkopt[data-pid="' + BEAM + '"]');
    await page.waitForTimeout(300);
    const transfer = ctx.posts.find((p) => p.url.includes('/api/music/transfer'));
    assert.ok(transfer, `no transfer POST captured; posts=${JSON.stringify(ctx.posts.map((p) => p.url))}`);
    assert.strictEqual(transfer.body.target_player_id, BEAM, `transfer targeted ${transfer.body.target_player_id}, not the Sonos Beam`);
    console.log(`      POST /api/music/transfer target=${transfer.body.target_player_id}`);
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
