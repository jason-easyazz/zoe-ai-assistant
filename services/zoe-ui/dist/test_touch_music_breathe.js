/*
 * Browser test for "let the music card breathe" (touch/home.html):
 *   1. the dock condenses to the launcher button beside the orb on MUSIC ONLY,
 *   2. Browse is a real surface (a card), not an overlay on the music card,
 *   3. the Cover Flow is back at full prototype scale (CF_K) and still clears
 *      every neighbour it shares the card with.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Every claim here is a LAYOUT claim — "the covers grew and still clear the
 * transport", "the launcher sits beside the orb". A fake DOM cannot see any of
 * that; it would happily pass on a card whose covers are drawn straight through
 * the scrub bar. So this drives real headless Chromium at the panel's real
 * resolution (1280x720) and reads real bounding boxes.
 *
 * FIXTURE PROVENANCE — read this before changing any fixture below
 * ---------------------------------------------------------------
 * The music fixtures are LIVE CAPTURES, not hand-written, taken from the box
 * while a real Sonos queue was playing:
 *
 *   curl -s localhost:8000/api/music/now-playing
 *   curl -s localhost:8000/api/music/queue/RINCON_347E5C9BEC8F01400
 *   curl -s localhost:8000/api/music/recently-played
 *   curl -s localhost:8000/api/music/playlists
 *   curl -s localhost:8000/api/panels/zoe-touch-pi/config
 *
 * On top of that, assertLiveContract() re-fetches those endpoints when they are
 * reachable and asserts the fixture's KEY SET matches live exactly. This repo
 * has already shipped a Cover Flow that passed 50 assertions while being dead on
 * the panel, because its mocks invented fields the API never returns. Fixtures
 * are checked, not trusted; an unreachable backend SKIPS loudly, never silently.
 *
 * Run:  node services/zoe-ui/dist/test_touch_music_breathe.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  BREATHE_SHOTS=<dir>
 *            ZOE_API=<http://host:port>  (live-contract check; default :8000)
 * (Not in CI — the repo has no JS lane. This is a local verification gate.)
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

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
  try { const p = chromium.executablePath(); if (p && fs.existsSync(p)) return p; } catch (_) {}
  const known = [
    '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
  ];
  return known.find((p) => fs.existsSync(p)) || null;
}
const chromium = loadChromium();
if (!chromium) {
  console.error('playwright-core not found. Set PLAYWRIGHT_CORE=/path/to/playwright-core.');
  process.exit(2);
}
const CHROME = findChrome(chromium);
if (!CHROME) { console.error('No Chromium binary found. Set CHROME_PATH=/path/to/chrome.'); process.exit(2); }

const DIST = __dirname;
const SHOTS = process.env.BREATHE_SHOTS || '/tmp/music-breathe-shots';
const API = process.env.ZOE_API || 'http://127.0.0.1:8000';
const PLAYER = 'RINCON_347E5C9BEC8F01400';

// ── fixtures (live captures — see provenance header) ────────────────────────
const NOW_PLAYING = {
  player_id: PLAYER, player_name: 'Bedroom', state: 'playing',
  title: 'Meet Joe Black', artist: 'Thomas Newman', album: '',
  image: 'https://i.ytimg.com/vi/x4fR5RhwyoM/maxresdefault.jpg',
  volume: 18, queue_id: PLAYER, queue_item_id: '731cd09f879c4d69a85c984f11763b59',
  queue_index: 3, shuffle: false, repeat: 'off', elapsed: 70.72586054039002, duration: 105.0,
  dont_stop: false,
};
const QUEUE = [
  ['0ef566db08f146b1afb75664d2ee5ec5', 'Yes', 'lahBKZIkLDM', 126],
  ['a85e5d0423494752b56fd60be4f8d729', 'Everywhere Freesia', 'RfxkV0OozTo', 106],
  ['b3ee1278d5514c2abb1c811380893aeb', 'Walkaway', '2zmaZ19ufZU', 113],
  ['731cd09f879c4d69a85c984f11763b59', 'Meet Joe Black', 'x4fR5RhwyoM', 105],
  ['6780b04613f74f35be4213afb0473a08', 'Peanut Butter Man', '8rv1HVXIPZg', 100],
  ['173cc2fdc3274d37938849d4f05a07d6', 'Whisper Of A Thrill', '_zAOgXiBMe0', 343],
  ['f80e207ac50b44faabaeaacd71cebb09', 'Cheek To Cheek', 'oNQJbgG3Jes', 84],
  ['8bc0fd367cae4bd3b0d5a04841a7af63', 'Cold Lamb Sandwich', 'qkVQZ3rHtI0', 92],
].map(([qiid, title, vid, dur], i) => ({
  queue_id: PLAYER, queue_item_id: qiid, name: 'Thomas Newman - ' + title, title,
  artist: 'Thomas Newman', image: 'https://i.ytimg.com/vi/' + vid + '/maxresdefault.jpg',
  index: 0, sort_index: i, duration: dur, available: true,
  media_item: {}, streamdetails: {}, extra_attributes: {},
}));
const RECENT = [
  { name: 'Walkaway', uri: 'ytmusic--HemJN6vc://track/2zmaZ19ufZU', media_type: 'track', artist: '', album: '', image: 'https://yt3.googleusercontent.com/a=w544-h544' },
  { name: 'Everywhere Freesia', uri: 'ytmusic--HemJN6vc://track/RfxkV0OozTo', media_type: 'track', artist: '', album: '', image: 'https://yt3.googleusercontent.com/b=w544-h544' },
  { name: 'Yes', uri: 'ytmusic--HemJN6vc://track/lahBKZIkLDM', media_type: 'track', artist: '', album: '', image: 'https://yt3.googleusercontent.com/c=w544-h544' },
  { name: 'Meet Joe Black', uri: 'ytmusic--HemJN6vc://album/MPREb_qvT5GYrqvOQ', media_type: 'album', artist: '', album: '', image: 'https://yt3.googleusercontent.com/d=w544-h544' },
];
const PLAYLISTS = [
  { name: '100 Greatest Songs of All Time!', uri: 'library://playlist/12', image: 'https://yt3.ggpht.com/e=s576', count: null },
  { name: '500 Random tracks (from library)', uri: 'library://playlist/4', image: '', count: null },
  { name: 'All favorited tracks', uri: 'library://playlist/1', image: '', count: null },
  { name: 'Country Hits 2024', uri: 'library://playlist/9', image: 'https://yt3.googleusercontent.com/f=w544-h544', count: null },
  { name: 'Liked Music (YouTube Music)', uri: 'library://playlist/7', image: 'https://www.gstatic.com/g.png', count: null },
  { name: "Pop's Biggest Hits", uri: 'library://playlist/11', image: 'https://yt3.googleusercontent.com/h=s576', count: null },
];
// Live panel config: two pins (a toggle and a temp). These are what must be
// GONE on music and BACK everywhere else.
const PANEL_CFG = {
  device_id: 'zoe-touch-pi', location: 'bedroom',
  room_id: null, room_name: null, room_slug: null,
  default_player: PLAYER, default_player_source: 'global',
  pins_configured: true,
  pinned: [
    { name: 'Bed', kind: 'toggle', read_eid: 'input_boolean.bedroom_light', write_eid: 'input_boolean.bedroom_light', write_action: 'toggle', state: 'off', setpoint: null, friendly_name: 'Bedroom Light', icon: 'mdi:ceiling-light', available: true, min: null, max: null, step: null, unit: null },
    { name: 'Temp', kind: 'temp', read_eid: 'sensor.current_temperature', write_eid: 'input_number.thermostat_temperature', write_action: 'set_value', state: '21.0', setpoint: '21.0', friendly_name: 'Current Temperature', icon: 'mdi:thermometer', available: true, min: 16.0, max: 30.0, step: 0.5, unit: '°C' },
  ],
  unresolved: [], ha_available: true, max_pins: 4,
};
const HA_ENTITIES = [
  { entity_id: 'input_boolean.bedroom_light', state: 'off', attributes: { friendly_name: 'Bedroom Light', icon: 'mdi:ceiling-light' } },
  { entity_id: 'sensor.current_temperature', state: '21.0', attributes: { friendly_name: 'Current Temperature', icon: 'mdi:thermometer' } },
];

// ── live-contract check ─────────────────────────────────────────────────────
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
const keys = (o) => Object.keys(o || {}).sort();
// Compare a fixture object's key set against the live one. Reports BOTH
// directions: a field the fixture invented is the bug that shipped dead UI, and
// a field the API grew is how a fixture silently goes stale.
function sameKeys(what, fixture, live) {
  const f = keys(fixture), l = keys(live);
  const invented = f.filter((k) => !l.includes(k));
  const missed = l.filter((k) => !f.includes(k));
  assert.deepStrictEqual({ invented, missed }, { invented: [], missed: [] },
    `${what}: fixture keys drifted from the LIVE API\n  fixture=${f.join(',')}\n  live   =${l.join(',')}`);
}
async function assertLiveContract() {
  const np = await getJson(API + '/api/music/now-playing');
  if (!np) { console.log('  ~ live API unreachable at ' + API + ' — contract check SKIPPED (fixtures unverified)'); return false; }
  sameKeys('/api/music/now-playing', { available: 1, now_playing: 1 }, np);
  if (np.now_playing) sameKeys('now-playing.now_playing', NOW_PLAYING, np.now_playing);
  const pid = (np.now_playing && np.now_playing.player_id) || PLAYER;
  const q = await getJson(API + '/api/music/queue/' + encodeURIComponent(pid));
  if (q && q.items && q.items.length) sameKeys('/api/music/queue item', QUEUE[0], q.items[0]);
  const r = await getJson(API + '/api/music/recently-played');
  if (r && r.items && r.items.length) sameKeys('/api/music/recently-played item', RECENT[0], r.items[0]);
  const p = await getJson(API + '/api/music/playlists');
  if (p && p.playlists && p.playlists.length) sameKeys('/api/music/playlists item', PLAYLISTS[0], p.playlists[0]);
  const c = await getJson(API + '/api/panels/zoe-touch-pi/config');
  if (c) {
    sameKeys('/api/panels/{id}/config', PANEL_CFG, c);
    if (c.pinned && c.pinned.length) sameKeys('config.pinned[]', PANEL_CFG.pinned[0], c.pinned[0]);
  }
  console.log('  ✓ fixtures match the LIVE API key-for-key (' + API + ')');
  return true;
}

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

// Stand-in cover art. Remote art is stubbed rather than fetched: the box may be
// offline, and a test that depends on ytimg.com fails for the wrong reason.
// It must be OPAQUE and per-URL DISTINCT, though — the first cut of this test
// served a transparent 2x2 PNG, every assertion passed, and the screenshot
// showed a fan of empty glass rectangles. Blank covers are the exact defect
// this suite exists to catch, so the fixture must never be able to fake them.
function coverSvg(url) {
  let h = 0;
  for (let i = 0; i < url.length; i++) h = (h * 31 + url.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">`
    + `<rect width="300" height="300" fill="hsl(${hue},62%,42%)"/>`
    + `<circle cx="150" cy="130" r="62" fill="hsl(${(hue + 40) % 360},70%,68%)"/>`
    + `<rect x="40" y="228" width="220" height="16" rx="8" fill="rgba(255,255,255,.55)"/>`
    + `<rect x="40" y="256" width="140" height="12" rx="6" fill="rgba(255,255,255,.32)"/></svg>`);
}

function newCtx() { return { posts: [] }; }

async function stub(page, ctx, base, idle) {
  // Everything OFF-ORIGIN is cover art — matched by origin, not by file
  // extension, because the real art URLs (ytimg, googleusercontent) frequently
  // have no extension at all. An extension-only matcher let those through to a
  // real network fetch, which failed, and the Browse thumbnails rendered as
  // broken-image boxes that no assertion noticed.
  await page.route((url) => !String(url).startsWith(base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: coverSvg(route.request().url()) }));
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (req.method() === 'POST') { ctx.posts.push({ url, body: JSON.parse(req.postData() || '{}') }); return json({ ok: true }); }
    if (url.includes('/api/panels/')) return json(PANEL_CFG);
    if (url.includes('/api/ha/entities')) return json(HA_ENTITIES);
    if (url.includes('/api/music/now-playing')) return json(idle ? { available: true, now_playing: null } : { available: true, now_playing: NOW_PLAYING });
    if (url.includes('/api/music/queue/')) return json({ available: true, items: idle ? [] : QUEUE });
    if (url.includes('/api/music/recently-played')) return json({ available: true, items: RECENT });
    if (url.includes('/api/music/playlists')) return json({ playlists: PLAYLISTS });
    if (url.includes('/api/music/players')) return json({ available: true, players: [{ player_id: PLAYER, name: 'Bedroom' }] });
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    return json({});
  });
}

async function open(browser, ctx, base, idle) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  await stub(page, ctx, base, idle);
  await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  // #authov ("Who's here?") covers the screen and swallows clicks in a harness.
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => {
    const b = document.getElementById('dbody');
    return b && !b.textContent.includes('…');
  }, { timeout: 8000 });
  return page;
}

const goto = async (page, id) => {
  await page.evaluate((i) => window.__show ? window.__show(i) : document.getElementById('stage').__show(i), id).catch(() => {});
};

async function shoot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const f = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: f });
  return f;
}

// Visible bounding box of a selector, or null when absent/hidden.
const box = (page, sel) => page.evaluate((s) => {
  const e = document.querySelector(s);
  if (!e) return null;
  const cs = getComputedStyle(e);
  if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return null;
  const r = e.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return { x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom };
}, sel);

function overlap(a, b) {
  if (!a || !b) return 0;
  const ox = Math.min(a.r, b.r) - Math.max(a.x, b.x);
  const oy = Math.min(a.b, b.b) - Math.max(a.y, b.y);
  return (ox > 0 && oy > 0) ? Math.round(ox * oy) : 0;
}

// ── the run ─────────────────────────────────────────────────────────────────
let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nmusic card breathe — 1280x720\n');
  await assertLiveContract();

  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });
  const ctx = newCtx();
  const page = await open(browser, ctx, base);

  // The estate's router is a closure; drive it the way a user does — through
  // the launcher — so the test exercises the real navigation path. Reaching in
  // to call show() directly would skip exactly the code under test.
  const nav = async (id) => {
    await page.click('#apps');
    await page.waitForSelector('#stage.lopen', { timeout: 4000 });
    await page.click(`.ltile[data-id="${id}"]`);
    await page.waitForFunction((i) => !document.getElementById('stage').classList.contains('lopen'), id, { timeout: 4000 });
    await page.waitForTimeout(500);
  };

  // ── 1. condensed dock, music only ────────────────────────────────────────
  await nav('music');
  await page.waitForSelector('.mfull .cfc.on', { timeout: 6000 });
  await page.waitForTimeout(400);

  await t('music: dock is condensed (.solo)', async () => {
    assert.strictEqual(await page.evaluate(() => document.getElementById('dock').classList.contains('solo')), true);
  });
  await t('music: #dbody and .ddiv are hidden — pins/timer/chip all off', async () => {
    assert.strictEqual(await box(page, '#dbody'), null, '#dbody should not be visible on music');
    assert.strictEqual(await box(page, '#dock .ddiv'), null, '.ddiv should not be visible on music');
  });
  await t('music: #apps is the ONLY dock control, and it sits beside the orb', async () => {
    const apps = await box(page, '#apps'), orb = await box(page, '#orb');
    assert.ok(apps, '#apps must stay visible');
    assert.ok(apps.x > orb.r, `#apps (x=${apps.x}) must be to the RIGHT of #orb (right=${orb.r})`);
    assert.ok(apps.x - orb.r < 40, `#apps should be beside the orb, gap was ${Math.round(apps.x - orb.r)}px`);
    // same row: centres within a few px
    const dy = Math.abs((apps.y + apps.h / 2) - (orb.y + orb.h / 2));
    assert.ok(dy < 6, `#apps and #orb should share a row, centres differ by ${Math.round(dy)}px`);
    // and it is genuinely tappable at panel size
    assert.ok(apps.w >= 60 && apps.h >= 60, `#apps is ${apps.w}x${apps.h}, too small for a finger`);
  });
  await t('music: no now-playing chip (the card has its own transport)', async () => {
    // EXISTENCE, not visibility. #dbody is display:none on music, so a
    // visibility check passes even with the guard deleted — it would be
    // asserting the condensed dock, not the chip rule. Mutation-tested: this
    // must fail when `if(_cur==='music')return;` is removed from paintDockMusic.
    assert.strictEqual(await page.$('#dock .pc.dnp'), null,
      'paintDockMusic built a chip on the music surface');
  });

  // ── 2. geometry: the regrown flow clears everything ──────────────────────
  const CLEARANCE_PEERS = ['#apps', '#orb', '#home', '.mqr', '#mTransport', '.mfull .mscrub', '.mfull .cfmeta'];
  let measured = null;
  await t('music: the centre cover collides with NOTHING on the card', async () => {
    const cover = await box(page, '.mfull .cfc.on');
    assert.ok(cover, 'no centre cover rendered — the flow is dead');
    const report = {};
    for (const sel of CLEARANCE_PEERS) {
      const b = await box(page, sel);
      assert.ok(b, `${sel} is missing from the music card`);
      report[sel] = { overlapPx2: overlap(cover, b), gapY: Math.round(b.y - cover.b) };
      assert.strictEqual(report[sel].overlapPx2, 0, `centre cover overlaps ${sel} by ${report[sel].overlapPx2}px²`);
    }
    measured = { cover, report };
  });
  await t('music: the whole fan stays on screen', async () => {
    const bad = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('.mfull .cfc').forEach((e) => {
        if (+getComputedStyle(e).opacity < 0.02) return;
        const r = e.getBoundingClientRect();
        if (r.left < -2 || r.right > 1282 || r.top < -2 || r.bottom > 722) out.push({ i: e.dataset.i, l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom) });
      });
      return out;
    });
    assert.deepStrictEqual(bad, [], 'covers escaped the 1280x720 panel');
  });
  await t('music: the covers actually GREW (CF_K is applied, not just declared)', async () => {
    const m = await page.evaluate(() => {
      const cf = document.getElementById('mCF');
      return {
        off: document.querySelector('.mfull .cfc').offsetWidth,
        // Read the INLINE property, not the computed one: the CSS carries a
        // fallback equal to the current value, so a computed read passes even if
        // cfApplyScale never ran — it would be asserting the fallback, not the
        // knob. Mutation-tested against removing the cfApplyScale() call.
        card: cf.style.getPropertyValue('--cfcard').trim(),
        persp: cf.style.getPropertyValue('--cfpersp').trim(),
      };
    });
    // CF_K=1 => the prototype's 300px card / 1400px perspective. Old: 260/1214.
    assert.strictEqual(m.off, 300, `cover box is ${m.off}px, expected the prototype's 300px`);
    assert.deepStrictEqual({ card: m.card, persp: m.persp }, { card: '300px', persp: '1400px' },
      'CF_K did not drive the CSS half of the scale — cfApplyScale() did not run');
  });
  await t('music: every cover actually DECODED art (not an empty box)', async () => {
    const imgs = await page.$$eval('.mfull .cfc img', (els) => els.map((e) => ({ w: e.naturalWidth, h: e.naturalHeight })));
    assert.ok(imgs.length >= 5, `only ${imgs.length} covers carry an <img>`);
    const dead = imgs.filter((i) => !i.w || !i.h);
    assert.deepStrictEqual(dead, [], `${dead.length} covers rendered a blank <img>`);
  });
  console.log('    shot: ' + await shoot(page, 'music-card'));

  // ── 3. Browse is a card ──────────────────────────────────────────────────
  await page.click('#mQBtn');
  await page.waitForSelector('.brf', { timeout: 4000 });
  await page.waitForTimeout(600);

  await t('browse: renders as a surface, not an overlay (no .mqueue survives)', async () => {
    assert.strictEqual(await page.$('.mqueue'), null, '.mqueue overlay still exists');
    assert.ok(await box(page, '.brf'), 'the Browse card body is not visible');
    assert.strictEqual(await page.$('#mCF'), null, 'the music card is still mounted underneath — Browse is not its own surface');
  });
  await t('browse: standard card header — title by #home, actions by .chmeta', async () => {
    const ttl = await box(page, '.chttl'), home = await box(page, '#home');
    const meta = await box(page, '.chmeta');
    assert.ok(ttl && home && meta, 'header parts missing');
    assert.ok(ttl.x > home.r, 'title must sit beside the home button');
    // The settings cog was removed from every card — all settings live in the
    // Settings card, reached from the launcher. The header actions now own the
    // top-right corner outright.
    assert.strictEqual(await page.$('.fcog'), null, 'a per-card settings cog came back');
    assert.ok(meta.x > ttl.x, 'actions must sit to the right of the title');
    assert.ok(await box(page, '#mQSave'), 'Save-as-playlist missing from the header');
    assert.ok(await box(page, '#mQClear'), 'Clear-queue missing from the header');
    assert.strictEqual(overlap(meta, ttl), 0, 'header actions collide with the title');
  });
  await t('browse: Recent tab rendered the live-shaped rows', async () => {
    const n = await page.$$eval('.mqlist.recent .rtile', (e) => e.length);
    assert.strictEqual(n, RECENT.length);
    const first = await page.$eval('.rtile .qn', (e) => e.textContent);
    assert.strictEqual(first, RECENT[0].name);
    // Same blindness as the covers: a broken <img> is invisible to a text assert.
    const dead = await page.$$eval('.rtile img.qthumb', (els) => els.filter((e) => !e.naturalWidth).length);
    assert.strictEqual(dead, 0, `${dead} Recent thumbnails failed to decode`);
  });
  await t('browse: gets the FULL dock back — pins and the now-playing chip', async () => {
    assert.strictEqual(await page.evaluate(() => document.getElementById('dock').classList.contains('solo')), false);
    assert.ok(await box(page, '#dbody'), '#dbody must be visible on Browse');
    const pins = await page.$$eval('#dbody .pc.pin', (e) => e.map((x) => x.getAttribute('data-k')));
    assert.deepStrictEqual(pins, ['toggle', 'temp'], 'the operator pins did not come back');
    assert.ok(await box(page, '#dock .pc.dnp'), 'the now-playing chip must show on Browse (it is an "other surface" now)');
  });
  await t('browse: the dock must not cover the list', async () => {
    const dock = await box(page, '#dock'), body = await box(page, '.brf');
    assert.strictEqual(overlap(dock, body), 0, 'the dock overlaps the Browse list');
  });
  console.log('    shot: ' + await shoot(page, 'browse-card'));

  await t('browse: Playlists tab switches and renders', async () => {
    await page.click('.mqtabs button[data-qt="playlists"]');
    await page.waitForSelector('.mqlist.grid .qi.pl', { timeout: 4000 });
    const n = await page.$$eval('.mqlist.grid .qi.pl', (e) => e.length);
    assert.strictEqual(n, PLAYLISTS.length);
    const ttl = await page.$eval('#mQTitle', (e) => e.textContent);
    assert.strictEqual(ttl, 'Your playlists');
  });
  console.log('    shot: ' + await shoot(page, 'browse-playlists'));

  await t('browse: Clear-queue posts against the live player id', async () => {
    ctx.posts.length = 0;
    await page.click('#mQClear');
    await page.waitForSelector('#estModal.on', { timeout: 3000 });
    await page.click('#estModal [data-x="yes"], #estModal .save, #estModal [data-x="ok"]').catch(() => {});
    await page.waitForTimeout(400);
    const clear = ctx.posts.find((p) => p.url.includes('/api/music/queue/clear'));
    assert.ok(clear, 'no queue/clear POST was made');
    assert.strictEqual(clear.body.queue_id, PLAYER);
  });

  await t('browse: Save/Clear refuse to post a null queue_id (nothing ever played)', async () => {
    // Greptile #1429: Save and Clear lacked the `||undefined` guard the play
    // paths have. Its stated premise was wrong (Browse is NOT a launcher tile —
    // #mQBtn on the music card is the only route, so loadMusic always runs
    // first), but "nothing has ever played" genuinely leaves player_id null and
    // a POST with queue_id:null fails opaquely.
    // Driven with a SECOND page whose now-playing is idle — the real null state,
    // reached the real way. A test hook in production code would prove less and
    // cost more.
    const idleCtx = newCtx();
    const idlePage = await open(browser, idleCtx, base, true);
    await idlePage.click('#apps');
    await idlePage.waitForSelector('#stage.lopen');
    await idlePage.click('.ltile[data-id="music"]');
    await idlePage.waitForTimeout(900);
    await idlePage.click('#mQBtn');
    await idlePage.waitForSelector('.brf', { timeout: 4000 });
    await idlePage.waitForTimeout(400);
    assert.strictEqual(await idlePage.evaluate(() => document.querySelector('#dock .pc.dnp')), null,
      'fixture is not actually idle — a chip appeared');
    // Check each button independently and assert BEFORE clicking the next one.
    // Clicking both first and asserting after reads as a 30s click timeout when
    // it fails (the unguarded first click opens a modal that eats the second
    // click) — a real red, but an unreadable one.
    const bad = [];
    for (const sel of ['#mQClear', '#mQSave']) {
      idleCtx.posts.length = 0;
      await idlePage.click(sel);
      await idlePage.waitForTimeout(350);
      const modal = await idlePage.$('#estModal.on');
      const posted = idleCtx.posts.filter((p) => /queue\/(clear|save)/.test(p.url)).map((p) => p.body);
      if (modal) bad.push(`${sel} opened a dialog for an action that cannot run`);
      if (posted.length) bad.push(`${sel} posted ${JSON.stringify(posted)} with no player id`);
      if (modal) await idlePage.keyboard.press('Escape').catch(() => {});
      await idlePage.evaluate(() => { const m = document.getElementById('estModal'); if (m) m.classList.remove('on'); });
    }
    assert.deepStrictEqual(bad, []);
    await idlePage.close();
  });

  // ── 4. leaving music restores the dock everywhere else ───────────────────
  await nav('weather');
  await page.waitForTimeout(600);
  await t('weather: full dock restored — pins intact after the music detour', async () => {
    assert.strictEqual(await page.evaluate(() => document.getElementById('dock').classList.contains('solo')), false);
    const pins = await page.$$eval('#dbody .pc.pin', (e) => e.map((x) => x.getAttribute('data-k')));
    assert.deepStrictEqual(pins, ['toggle', 'temp']);
    const apps = await box(page, '#apps'), dock = await box(page, '#dock');
    assert.ok(Math.abs((dock.x + dock.w / 2) - 640) < 4, 'the dock did not return to centre');
    assert.ok(apps.x < 200 + dock.x, 'the launcher is not back inside the dock');
  });
  await t('weather: the now-playing chip is back', async () => {
    assert.ok(await box(page, '#dock .pc.dnp'));
  });
  console.log('    shot: ' + await shoot(page, 'weather-full-dock'));

  await t('returning to music re-condenses the dock', async () => {
    await nav('music');
    await page.waitForSelector('.mfull .cfc.on', { timeout: 6000 });
    assert.strictEqual(await page.evaluate(() => document.getElementById('dock').classList.contains('solo')), true);
    assert.strictEqual(await box(page, '#dbody'), null);
  });

  // ── report ──────────────────────────────────────────────────────────────
  if (measured) {
    console.log('\n  measured clearances at CF_K=1 (centre cover '
      + `${Math.round(measured.cover.w)}x${Math.round(measured.cover.h)} @ y ${Math.round(measured.cover.y)}..${Math.round(measured.cover.b)}):`);
    for (const [k, v] of Object.entries(measured.report)) {
      console.log(`    ${k.padEnd(20)} overlap=${v.overlapPx2}px²  gap below cover=${v.gapY}px`);
    }
  }
  await browser.close();
  srv.close();
  console.log(failures ? `\nFAILED (${failures})\n` : '\nALL PASS\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
