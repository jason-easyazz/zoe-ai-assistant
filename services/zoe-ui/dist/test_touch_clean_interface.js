#!/usr/bin/env node
/**
 * Estate panel — "clean interface" gate (headless 1280x720, the real panel size).
 *
 * Covers two properties of the clean estate:
 *   1. Music card: volume is behind a speaker icon + a VERTICAL popover anchored
 *      to the right edge; "keep playing" (∞) is a round transport button, first
 *      in the row before shuffle. (This is the #1450 layout, which corrected the
 *      original #1446 design of a left-flank ∞ pill + horizontal volume strip.)
 *   2. No per-card settings cogs anywhere; Settings still reachable.
 *
 * The sleep card's pinned controls are NOT covered here — that shipped as
 * #1444 and is gated by test_touch_dock_pins.js.
 *
 * DESIGN OF RECORD: #1450 (operator-reported). Transport order is
 * [keep, shuffle, prev, play, next, repeat, volume]; the volume popover is
 * vertical (104x260 at right:24px) with a 44x176 vertical slider, matching the
 * dock thermostat. Do NOT "restore" the #1446 left-flank/horizontal design — it
 * was deliberately replaced. The popover shares the right edge with the
 * favourite heart by design (see the overlap test).
 *
 * Fixtures are asserted against the LIVE API before the browser starts — a
 * mock that invents fields is how a feature here once passed 50 assertions
 * while being dead on the panel. Geometry is bounding-box asserted, because
 * the first volume placement passed every assertion while sitting on top of
 * the cover art. LOOK AT THE PNGs.
 *
 * This harness is READ-ONLY against the live box: it GETs the panel config to
 * check the contract and never PUTs — the operator's own pins live on
 * zoe-touch-pi. Every write the page makes is stubbed, so no volume change and
 * no playback ever reaches a real speaker.
 *
 * Local gate — there is no JS lane in CI.  Run: node dist/test_touch_clean_interface.js
 * Exit 0 pass / 1 failure / 2 missing playwright or chromium.
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
if (!chromium) { console.error('playwright-core not found. Set PLAYWRIGHT_CORE=/path/to/playwright-core.'); process.exit(2); }
const CHROME = findChrome(chromium);
if (!CHROME) { console.error('No Chromium binary found. Set CHROME_PATH=/path/to/chrome.'); process.exit(2); }

const DIST = __dirname;
const SHOTS = process.env.CLEAN_SHOTS || '/tmp/clean-interface-shots';
const API = process.env.ZOE_API || 'http://127.0.0.1:8000';
const PANEL = process.env.ZOE_PANEL || 'zoe-touch-pi';

// ---------------------------------------------------------------- fixtures
// Shapes mirror the live endpoints; assertLiveContract() proves it below.
const PIN_TOGGLE = {
  name: 'Bedroom', kind: 'toggle',
  read_eid: 'switch.bedroom_1_switch_1', write_eid: 'switch.bedroom_1_switch_1',
  write_action: 'toggle', state: 'on', setpoint: null,
  friendly_name: 'Bedroom 1 Switch 1', icon: 'mdi:toggle-switch-outline',
  available: true, min: null, max: null, step: null, unit: null,
};
function panelCfg(over) {
  return Object.assign({
    device_id: PANEL, location: 'bedroom', room_id: null, room_name: null, room_slug: null,
    default_player: 'RINCON_347E5C9BEC8F01400', default_player_source: 'panel',
    pins_configured: true, pinned: [PIN_TOGGLE], unresolved: [],
    ha_available: true, max_pins: 4,
  }, over || {});
}
function ent(id, name, state) {
  return { entity_id: id, state: state || 'off', attributes: { friendly_name: name },
    last_changed: '2026-07-19T12:00:00+00:00', last_updated: '2026-07-19T12:00:00+00:00' };
}
// Four+ lights so the pins_configured:false fallback has something to show.
const HA_ENTITIES = { entities: [
  ent('light.living_room', 'Living Room Light', 'on'),
  ent('light.kitchen', 'Kitchen Light', 'off'),
  ent('light.bedroom', 'Bedroom Light', 'off'),
  ent('light.porch', 'Porch Light', 'on'),
  ent('light.hall', 'Hall Light', 'off'),
], count: 5 };
const NOW_PLAYING = {
  player_id: 'RINCON_347E5C9BEC8F01400', player_name: 'Bedroom',
  title: 'Teardrop', artist: 'Massive Attack', album: 'Mezzanine',
  image: 'https://img.example/teardrop.jpg', state: 'playing',
  elapsed: 47, duration: 330, volume: 34, shuffle: false, repeat: 'off',
  dont_stop: false, queue_id: 'q1', queue_index: 0, queue_item_id: 'qi1',
};

// Queue items drive the Cover Flow fan. Stubbing `{items: []}` leaves the fan
// empty, which makes every "the popover clears the covers" assertion pass
// against nothing — the exact shape of the bug this suite exists to catch.
// Shape is derived from what cfRender()/cfPaintMeta() read: image,
// queue_item_id, index, name/title, artist, media_item{uri,favorite}.
function qitem(i, name, artist) {
  return { queue_item_id: 'qi' + i, index: i, name: name, title: name, artist: artist,
    image: `https://img.example/cover${i}.jpg`,
    media_item: { uri: `library://track/${i}`, favorite: i === 0 } };
}
const QUEUE = { available: true, items: [
  qitem(0, 'Teardrop', 'Massive Attack'), qitem(1, 'Angel', 'Massive Attack'),
  qitem(2, 'Risingson', 'Massive Attack'), qitem(3, 'Inertia Creeps', 'Massive Attack'),
  qitem(4, 'Dissolved Girl', 'Massive Attack'),
] };

// ------------------------------------------------------- live contract check
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
function sameKeys(what, fixture, live) {
  const f = keys(fixture), l = keys(live);
  const invented = f.filter((k) => !l.includes(k));
  const missed = l.filter((k) => !f.includes(k));
  assert.deepStrictEqual({ invented, missed }, { invented: [], missed: [] },
    `${what}: fixture keys drifted from the LIVE API\n  fixture=${f.join(',')}\n  live   =${l.join(',')}`);
}
// GET only. The operator set this panel's pins himself through the UI — this
// harness must never PUT to it. Configured-panel variants are stubbed in-page.
async function assertLiveContract() {
  const cfg = await getJson(`${API}/api/panels/${encodeURIComponent(PANEL)}/config`);
  if (!cfg) {
    console.log(`  ~ live API unreachable at ${API} — contract check SKIPPED (fixtures UNVERIFIED)`);
    return false;
  }
  sameKeys(`/api/panels/${PANEL}/config`, panelCfg(), cfg);
  assert.ok(Array.isArray(cfg.pinned), 'live config.pinned is not an array');
  if (cfg.pinned.length) sameKeys('config.pinned[0]', PIN_TOGGLE, cfg.pinned[0]);
  else console.log('  ~ live panel has no pins — pin key-shape UNVERIFIED this run');
  const ha = await getJson(`${API}/api/ha/entities`);
  if (ha && Array.isArray(ha.entities) && ha.entities.length) {
    sameKeys('/api/ha/entities.entities[0]', HA_ENTITIES.entities[0], ha.entities[0]);
  }
  const q = await getJson(`${API}/api/music/queue/${NOW_PLAYING.player_id}`);
  if (q && Array.isArray(q.items) && q.items.length) sameKeys('/api/music/queue.items[0]', QUEUE.items[0], q.items[0]);
  else console.log('  ~ live queue is empty — cover-flow item shape UNVERIFIED this run');
  const np = await getJson(`${API}/api/music/now-playing`);
  if (np) {
    sameKeys('/api/music/now-playing', { available: 1, now_playing: 1 }, np);
    if (np.now_playing) {
      sameKeys('now-playing.now_playing', NOW_PLAYING, np.now_playing);
      // The heart's uri must keep coming from the focused cover, not from here.
      assert.ok(!('uri' in np.now_playing), 'now-playing grew a `uri` — the heart may now read the wrong one');
    }
  }
  console.log(`  ✓ fixtures match the LIVE API key-for-key (${API})`);
  return true;
}

// ------------------------------------------------------------------ harness
function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
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
// Opaque, per-URL-distinct cover art. Matched by ORIGIN, not extension: real
// art URLs often have none, and a transparent stub makes every assertion pass
// against a fan of empty glass rectangles.
function coverSvg(url) {
  let h = 0;
  for (let i = 0; i < url.length; i++) h = (h * 31 + url.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">`
    + `<rect width="300" height="300" fill="hsl(${hue},62%,42%)"/>`
    + `<circle cx="150" cy="130" r="62" fill="hsl(${(hue + 40) % 360},70%,68%)"/>`
    + `<rect x="40" y="228" width="220" height="16" rx="8" fill="rgba(255,255,255,.55)"/></svg>`);
}
function newCtx() { return { posts: [], cfgGets: 0, npGets: 0 }; }
async function stub(page, base, ctx, opts) {
  const o = opts || {};
  await page.route((url) => !String(url).startsWith(base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: coverSvg(route.request().url()) }));
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (req.method() !== 'GET') {
      let body = {};
      try { body = JSON.parse(req.postData() || '{}'); } catch (_) {}
      const p = url.split('/api/')[1].split('?')[0];
      ctx.posts.push({ path: p, method: req.method(), body });
      if (o.fail && o.fail(p)) return json({ ok: false });
      return json({ ok: true });
    }
    if (url.includes('/api/panels/')) { ctx.cfgGets++; return json(o.cfg || panelCfg()); }
    if (url.includes('/api/ha/entities')) return json(o.ha || HA_ENTITIES);
    if (url.includes('/api/music/now-playing')) {
      ctx.npGets++;
      return json(o.np === null ? { available: false, now_playing: null }
        : { available: true, now_playing: Object.assign({}, NOW_PLAYING, o.np || {}) });
    }
    if (url.includes('/api/music/queue/')) return json(o.queue || QUEUE);
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    return json({});
  });
}
const lastPost = (ctx, p) => [...ctx.posts].reverse().find((x) => x.path === p);

async function open(browser, base, ctx, opts) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  await stub(page, base, ctx, opts);
  await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  // #authov ("Who's here?") covers the screen and swallows every click.
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => {
    const b = document.getElementById('dbody');
    return b && !b.textContent.includes('…');
  }, { timeout: 10000 });
  page.nav = async (id) => {
    // The sleep surface hides #home and the whole dock — #apps is unreachable
    // from it. Tapping the night clock (anywhere that is not a control) wakes
    // back to home, which is the only way off this surface.
    if (await page.$('.slp')) {
      await page.click('.slp .sclock');
      await page.waitForTimeout(500);
    }
    await page.click('#apps');
    await page.waitForSelector('#stage.lopen', { timeout: 5000 });
    await page.click(`.ltile[data-id="${id}"]`);
    await page.waitForFunction(() => !document.getElementById('stage').classList.contains('lopen'), { timeout: 5000 });
    await page.waitForTimeout(600);
  };
  return page;
}
async function shoot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const f = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: f });
  console.log('      shot: ' + f);
  return f;
}
const box = (page, sel) => page.$eval(sel, (e) => {
  const r = e.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
});
// The INKED extent of an element's text, not its block box. .cfmeta and its
// children are `left:0;right:0` with centred text, so their boxes span the
// whole card and a box-overlap against them is meaningless — the glyphs sit in
// the middle. A Range over the contents gives what is actually painted.
const textBox = (page, sel) => page.$eval(sel, (e) => {
  const rg = document.createRange();
  rg.selectNodeContents(e);
  const r = rg.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
});
function overlaps(a, b) {
  return a.x < b.right && b.x < a.right && a.y < b.bottom && b.y < a.bottom;
}

// ONLY=<regex> runs a subset — used when mutation-testing a single assertion.
// A filter that matches nothing must FAIL, not report a vacuous "ALL PASS".
const ONLY = process.env.ONLY ? new RegExp(process.env.ONLY, 'i') : null;
let failures = 0, ran = 0;
async function t(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  ran++;
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nestate — clean interface\n');
  await t('fixtures match the live API contract', assertLiveContract);

  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  // The box is memory-tight (routinely <1G free). Without --disable-dev-shm-usage
  // chromium dies mid-run and the crash surfaces as a confusing "Target page or
  // browser has been closed" on the NEXT test rather than on the one that OOMed.
  const browser = await chromium.launch({ executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--force-device-scale-factor=1'] });

  // ---------------------------------------------------- 1. music card volume
  await t('volume is NOT permanently on screen; the speaker icon is', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    await page.nav('music');
    await page.waitForSelector('#mTransport', { timeout: 8000 });
    assert.strictEqual(await page.$$eval('.mvol', (e) => e.length), 0,
      'the old always-visible .mvol pill is still in the DOM');
    const t2 = await page.$('#mVolT');
    assert.ok(t2, 'no speaker control (#mVolT) in the transport');
    assert.ok(await page.$eval('#mVolT', (e) => e.closest('#mTransport') !== null),
      'the speaker icon is not inside #mTransport — it did not take ∞\'s slot');
    // The slider exists but is unreachable until the popover opens.
    assert.strictEqual(await page.$eval('.vpop', (e) => getComputedStyle(e).opacity), '0',
      'the volume popover is visible before anyone tapped the speaker');
    assert.strictEqual(await page.$eval('.vpop', (e) => getComputedStyle(e).pointerEvents), 'none',
      'the closed popover still takes clicks');
    await shoot(page, 'music-popover-closed');
    await page.close();
  });

  await t('∞ "keep playing" is a round transport button, first in the row before shuffle', async () => {
    // #1450 corrected #1446: "keep playing" is a round .sml icon button back IN
    // the transport, immediately before shuffle — not the left-flank pill #1446
    // built. Transport order: [keep, shuffle, prev, play, next, repeat, volume].
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    await page.nav('music');
    await page.waitForSelector('#mDS', { timeout: 8000 });
    assert.ok(await page.$eval('#mDS', (e) => e.closest('#mTransport') !== null),
      '∞ is not inside #mTransport — #1450 put it back into the transport row');
    assert.ok(await page.$eval('#mDS', (e) => e.classList.contains('sml')),
      '∞ is not a round .sml transport button');
    // It renders immediately before shuffle in the transport row's DOM order.
    assert.ok(await page.evaluate(() => {
      const ds = document.getElementById('mDS'), sh = document.getElementById('mShuf');
      return !!ds && !!sh
        && (ds.compareDocumentPosition(sh) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    }), '∞ does not precede the shuffle button in the transport row');
    const k = await box(page, '#mDS');
    assert.ok(k.h >= 48 && k.w >= 48,
      `∞ is ${Math.round(k.w)}x${Math.round(k.h)}px — under the 48px finger floor`);
    await page.close();
  });

  await t('tapping the speaker opens a usable volume popover', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    await page.nav('music');
    await page.click('#mVolT');
    await page.waitForTimeout(400);
    assert.ok(await page.$eval('#mVolT', (e) => e.classList.contains('open')), 'the popover did not open');
    assert.strictEqual(await page.$eval('.vpop', (e) => getComputedStyle(e).opacity), '1', 'the popover is still transparent');
    const v = await box(page, '#mVol');
    // #1450: the slider is VERTICAL (44x176), anchored to the right, matching the
    // dock thermostat — not the horizontal strip #1446 built. Usable = tall and
    // wide enough for a finger, and taller than it is wide.
    assert.ok(v.h > 100 && v.w >= 30 && v.h > v.w,
      `the volume slider measures ${Math.round(v.w)}x${Math.round(v.h)} — not a usable vertical slider`);
    // It reads the real volume, not a default.
    assert.strictEqual(await page.$eval('#mVol', (e) => e.value), '34', 'the slider did not adopt the live volume');
    await shoot(page, 'music-popover-open');
    await page.close();
  });

  await t('the open popover clears every cover, the now-playing info and the transport', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    await page.nav('music');
    await page.click('#mVolT');
    await page.waitForTimeout(400);
    const pop = await box(page, '.vpop');
    // The fan must actually be there, or "clears the covers" proves nothing.
    const coverBoxes = await page.$$eval('.mfull .cfc', (els) => els.map((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, w: r.width, h: r.height };
    }));
    assert.ok(coverBoxes.length >= 3,
      `only ${coverBoxes.length} covers rendered — the collision checks below are vacuous`);
    const dead = await page.$$eval('.mfull .cfc img', (e) => e.filter((x) => !x.naturalWidth).length);
    assert.strictEqual(dead, 0, `${dead} covers rendered a blank <img>`);
    // Overlap is tested against the actual cover CARDS (.cfc), never the .cf
    // CONTAINER: .cf spans the full card width (left:0;right:0), so a
    // right-anchored popover "overlaps" it vacuously — the same box-vs-inked
    // trap the .cfmeta note above flags. Check each card individually.
    coverBoxes.forEach((b, i) => {
      // A cover that collapsed to a zero-size box would skip the overlap check
      // and pass vacuously, so assert it rendered before measuring clearance.
      assert.ok(b.w > 0 && b.h > 0,
        `cover ${i} rendered with an unusable ${Math.round(b.w)}x${Math.round(b.h)} box`);
      assert.ok(!overlaps(pop, b), `the open volume popover overlaps cover ${i}`);
    });
    // The favourite heart (.mfav) is deliberately NOT in the must-clear set.
    // #1450 anchors the volume popover to the right edge (right:24px) — the same
    // edge the heart sits on — so the popover sits over the heart while open. The
    // heart is a transient-occluded secondary control, not part of the
    // now-playing readout; occluding it during a volume adjust is the
    // operator-approved layout (the popover dismisses and the heart returns).
    for (const [what, sel] of [['#orb', '#orb'], ['#apps', '#apps'],
      ['the QR panel', '.mqr'],
      ['the seek bar', '#mScrub'], ['the track title', '.mfull .mtitle'],
      ['the artist', '.mfull .martist'], ['the "now playing" kicker', '.mfull .mkick']]) {
      if (!(await page.$(sel))) continue;
      const inked = ['.mfull .mtitle', '.mfull .martist', '.mfull .mkick'].includes(sel);
      const b = await (inked ? textBox : box)(page, sel);
      if (!b.w || !b.h) continue;
      assert.ok(!overlaps(pop, b), `the open volume popover overlaps ${what}`);
    }
    // EVERY real transport button must clear the right-edge popover — not just the
    // centred play/pause. `#mTransport button` sweeps keep/shuffle/prev/play/next/
    // repeat while naturally excluding the volume TILE (#mVolT is a div — the
    // popover's own trigger, expected to sit under it) and the inert .tsp spacers,
    // so a spacing change that slides repeat (the rightmost control) under the
    // popover fails here instead of passing on an unmeasured button.
    const tBtns = await page.$$eval('#mTransport button', (els) => els.map((e) => {
      const r = e.getBoundingClientRect();
      return { id: e.id || e.getAttribute('data-a') || '?',
        x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
    }));
    assert.ok(tBtns.length >= 6,
      `only ${tBtns.length} transport buttons measured — the clear-check is vacuous`);
    tBtns.forEach((b) => {
      assert.ok(b.w > 0 && b.h > 0,
        `transport button ${b.id} rendered with an unusable ${Math.round(b.w)}x${Math.round(b.h)} box`);
      assert.ok(!overlaps(pop, b), `the open volume popover overlaps transport button ${b.id}`);
    });
    await page.close();
  });

  await t('a click INSIDE the popover does not dismiss it', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    await page.nav('music');
    await page.click('#mVolT');
    await page.waitForTimeout(400);
    await page.click('#mVol');
    await page.waitForTimeout(200);
    assert.ok(await page.$eval('#mVolT', (e) => e.classList.contains('open')),
      'dragging the slider dismissed the popover holding it');
    await page.close();
  });

  await t('a 5s poll cannot yank the popover or the knob out from under a drag', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx, { np: { volume: 34 } });
    await page.nav('music');
    await page.click('#mVolT');
    await page.waitForTimeout(300);
    // Take the slider the way a finger does, and move it.
    await page.$eval('#mVol', (e) => {
      e.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1 }));
      e.value = '88';
      e.dispatchEvent(new Event('input', { bubbles: true }));
    });
    // Count the POLL, not writes: loadMusic GETs now-playing, so ctx.posts
    // (which records non-GETs only) never moves and asserting on it would be
    // both vacuous and wrong. If no poll lands during the drag there is no
    // repaint to survive and the two assertions below prove nothing.
    const pollsBefore = ctx.npGets;
    // Let the real 5s loadMusic poll land mid-"drag" — it answers volume 34.
    await page.waitForTimeout(6200);
    assert.ok(ctx.npGets > pollsBefore,
      'no now-playing poll landed during the drag — this test proves nothing');
    assert.strictEqual(await page.$eval('#mVol', (e) => e.value), '88',
      'the poll snapped the knob back to 34 mid-drag');
    assert.ok(await page.$eval('#mVolT', (e) => e.classList.contains('open')),
      'the poll closed the popover mid-drag');
    // And releasing it commits the value the finger chose.
    await page.$eval('#mVol', (e) => e.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 })));
    await page.waitForTimeout(400);
    const p = lastPost(ctx, 'music/control');
    assert.ok(p && p.body.action === 'volume_set' && p.body.value === 88,
      'the drag never reached /api/music/control as volume_set 88');
    await page.close();
  });

  await t('∞ toggles dont-stop, and reverts when MA refuses', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx, { fail: (p) => p.includes('dont-stop') });
    await page.nav('music');
    await page.click('#mDS');
    await page.waitForTimeout(500);
    const p = lastPost(ctx, 'music/dont-stop');
    assert.ok(p, '∞ posted nothing — its direct handler (wireKeep) is not wired');
    assert.strictEqual(p.body.enabled, true, 'the ∞ tap did not ask to enable');
    assert.ok(!(await page.$eval('#mDS', (e) => e.classList.contains('on'))),
      '∞ stayed lit after MA refused the enable');
    // ∞ carries no data-a, but it sits INSIDE #mTransport, whose delegated
    // handler dispatches on data-a. Without a `if(!a)return` guard the tap
    // bubbles there and fires musicControl(null) — a spurious null-action
    // /api/music/control on every keep-playing tap. It must post ONLY dont-stop.
    assert.ok(!ctx.posts.some((x) => x.path === 'music/control'),
      'the ∞ tap also fired /api/music/control (null action) — it bubbled to the transport handler');
    await page.close();
  });

  // ------------------------------------------------------------- 2. the cogs
  await t('no card renders a settings cog, and Settings is still reachable', async () => {
    const ctx = newCtx();
    const page = await open(browser, base, ctx);
    // Every launcher destination, plus the home surface (reached by the #home
    // corner button — it is deliberately not a launcher tile).
    // Every launcher destination in DESTS.
    const surfaces = ['day', 'calendar', 'list', 'weather', 'music', 'rooms',
      'reminder', 'person', 'timer', 'sleep', 'ask', 'settings'];
    for (const s of surfaces) {
      await page.nav(s);
      const n = await page.$$eval('.fcog, .cog', (e) => e.length);
      assert.strictEqual(n, 0, `the ${s} card still renders ${n} cog(s)`);
    }
    await page.click('#home');
    await page.waitForTimeout(600);
    assert.strictEqual(await page.$$eval('.fcog, .cog', (e) => e.length), 0, 'the home surface still renders a cog');
    // The launcher tile is the route that replaces them.
    await page.click('#apps');
    await page.waitForSelector('#stage.lopen', { timeout: 5000 });
    assert.ok(await page.$('.ltile[data-id="settings"]'), 'no Settings tile in the launcher — settings is stranded');
    await page.click('.ltile[data-id="settings"]');
    await page.waitForTimeout(700);
    assert.ok(await page.$('.setf'), 'the Settings tile did not open the settings card');
    await shoot(page, 'settings-reachable');
    await page.nav('weather');
    await shoot(page, 'weather-no-cog');
    await page.close();
  });

  await browser.close();
  srv.close();
  if (!ran) { console.log('\nNO TESTS RAN — the ONLY filter matched nothing\n'); process.exit(1); }
  console.log(failures ? `\nFAILED (${failures}/${ran})\n` : `\nALL PASS (${ran})\n`);
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
