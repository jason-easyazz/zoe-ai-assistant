/*
 * Browser test: multi-zone speaker selection + per-zone volume (touch/home.html).
 *
 * "pick multiple zones to play music through, and if you hit volume with multiple
 *  zones selected you get multiple volume sliders" + "refine [the picker], it
 *  looks bad" — the picker is now a 2-column row list that is MULTI-select.
 *
 * Model: the playing zone is the anchor (leader). Tapping a groupable, available
 * zone toggles it in/out of the group live via POST /api/music/group; the leader
 * stays. A zone MA can't group (a lone TV: can_group_with=[]) has no check and
 * tapping it SWITCHES (transfer). The volume popover, when >1 zone plays, shows
 * one horizontal slider per zone, each writing volume_set for its own player.
 *
 * WHY A REAL BROWSER: which POST a tap makes, real hit-testing of the rows, and
 * the popover swapping solo↔multi are all DOM/behaviour claims. Real clicks
 * (not evaluate().click()) — the dead-tap lesson from the music-card layout.
 *
 * Run: node services/zoe-ui/dist/test_touch_multizone.js
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PW = [process.env.PLAYWRIGHT_CORE, 'playwright-core',
  '/home/zoe/.openclaw/npm/node_modules/playwright-core'].filter(Boolean);
function loadChromium() { for (const c of PW) { try { return require(c).chromium; } catch (_) {} } return null; }
const chromium = loadChromium();
if (!chromium) { console.error('playwright-core not found.'); process.exit(2); }
const CHROME = [process.env.CHROME_PATH, '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
  '/usr/bin/chromium'].filter(Boolean).find((p) => fs.existsSync(p));
if (!CHROME) { console.error('No Chromium binary found.'); process.exit(2); }

const DIST = __dirname;
const BED = 'RINCON_bed', LIV = 'liv', BATH = 'bath', SAM = 'sam';
const PLAYERS = [
  { player_id: BED, name: 'Bedroom', display_name: 'Bedroom', available: true, kind: 'speaker', kind_label: 'Sonos Beam' },
  { player_id: LIV, name: 'Living Room', display_name: 'Living Room', available: true, kind: 'speaker', kind_label: 'Sonos Arc' },
  { player_id: BATH, name: 'Bathroom speaker', display_name: 'Bathroom speaker', available: true, kind: 'speaker', kind_label: 'Home Mini' },
  { player_id: SAM, name: 'Samsung Q80CA 98', display_name: 'Samsung Q80CA 98', available: true, kind: 'tv', kind_label: 'Samsung TV' },
];
const VOL = { [BED]: 35, [LIV]: 60, [BATH]: 45 };
// `members` controls the current group; the test flips it to simulate MA state.
function groupsPlayers(members) {
  return PLAYERS.map((p) => ({
    ...p, volume: VOL[p.player_id] != null ? VOL[p.player_id] : 50,
    group_member_ids: members.indexOf(p.player_id) >= 0 ? members.slice() : (p.player_id === members[0] ? members.slice() : []),
    can_group_with: p.kind === 'speaker' ? PLAYERS.filter((x) => x.player_id !== p.player_id && x.kind === 'speaker').map((x) => x.player_id) : [],
  }));
}
const NOW = { player_id: BED, player_name: 'Bedroom', state: 'playing', title: 'Meet Joe Black', artist: 'Thomas Newman', image: '', volume: 35, queue_id: BED, queue_item_id: 'q0', queue_index: 0, shuffle: false, repeat: 'off', elapsed: 20, duration: 200 };

function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' };
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

async function open(browser, ctx) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errs = []; page.on('pageerror', (e) => errs.push(String(e.message))); ctx.errs = errs;
  await page.route((url) => !String(url).startsWith(ctx.base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', (route) => {
    const u = route.request().url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      ctx.posts.push({ url: u.split('/api/')[1].split('?')[0], body });
      // reflect a group change so a re-open sees the new membership
      if (u.includes('/api/music/group')) {
        if (body.add) body.add.forEach((id) => { if (ctx.members.indexOf(id) < 0) ctx.members.push(id); });
        if (body.remove) ctx.members = ctx.members.filter((id) => body.remove.indexOf(id) < 0);
      }
      return json({ ok: true });
    }
    if (u.includes('now-playing')) return json({ available: true, now_playing: NOW });
    if (u.includes('/api/music/queue/')) return json({ available: true, items: [{ queue_id: BED, queue_item_id: 'q0', title: 'x', artist: 'y', image: '', index: 0, sort_index: 0, duration: 200, available: true, media_item: { uri: 'y://0' } }] });
    if (u.includes('/api/music/players')) return json({ available: true, players: PLAYERS });
    if (u.includes('/api/music/groups')) return json({ available: true, players: groupsPlayers(ctx.members), groups: [] });
    if (u.includes('sleep-gate')) return json({ block: false });
    if (u.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    if (u.includes('display/preferences')) return json({ preferences: {} });
    if (u.includes('skybridge/timers')) return json({ timers: [] });
    return json({});
  });
  await page.goto(ctx.base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1&domain=music', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForSelector('#mSpk', { timeout: 8000 });
  await page.waitForTimeout(1400);
  return page;
}
const openPicker = async (page) => { await page.click('#mSpk'); await page.waitForSelector('.spkopt', { timeout: 5000 }); await page.waitForTimeout(300); };
const rowByName = (page, nm) => page.evaluateHandle((n) => Array.from(document.querySelectorAll('.spkopt')).find((e) => (e.querySelector('.snm') || {}).textContent === n), nm);

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nmulti-zone speaker picker + per-zone volume — 1280x720\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  await t('the playing zone shows checked + "Playing"; groupable zones have a check; a TV does not', async () => {
    const ctx = { base, posts: [], members: [BED] }; const page = await open(browser, ctx);
    await openPicker(page);
    const rows = await page.$$eval('.spkopt', (els) => els.map((e) => ({
      nm: (e.querySelector('.snm') || {}).textContent, on: e.classList.contains('on'),
      chk: !!e.querySelector('.schk'), chip: (e.querySelector('.schip') || {}).textContent || '',
      group: e.getAttribute('data-group'),
    })));
    const bed = rows.find((r) => r.nm === 'Bedroom'), sam = rows.find((r) => r.nm === 'Samsung Q80CA 98'), liv = rows.find((r) => r.nm === 'Living Room');
    assert.ok(bed.on && /Playing/i.test(bed.chip), 'the playing zone is not shown checked/Playing');
    assert.ok(liv.chk && liv.group === '1', 'a groupable speaker has no check');
    assert.ok(!sam.chk && sam.group === '0', 'a TV MA cannot group wrongly shows a group check');
    assert.strictEqual(ctx.errs.length, 0, 'page errors: ' + ctx.errs.join(' | '));
    await page.close();
  });

  await t('tapping a groupable zone ADDS it to the group (POST /group add)', async () => {
    const ctx = { base, posts: [], members: [BED] }; const page = await open(browser, ctx);
    await openPicker(page);
    const liv = await rowByName(page, 'Living Room'); await liv.asElement().click();
    await page.waitForTimeout(400);
    const g = ctx.posts.find((p) => p.url === 'music/group');
    assert.ok(g, `no /group POST; posts=${JSON.stringify(ctx.posts.map((p) => p.url))}`);
    assert.strictEqual(g.body.target_player_id, BED, 'group target is not the playing leader');
    assert.deepStrictEqual(g.body.add, [LIV], `expected add:[${LIV}], got ${JSON.stringify(g.body.add)}`);
    assert.ok(await page.evaluate((n) => Array.from(document.querySelectorAll('.spkopt')).find((e) => (e.querySelector('.snm') || {}).textContent === n).classList.contains('on'), 'Living Room'), 'the added zone did not become selected');
    await page.close();
  });

  await t('tapping a selected companion REMOVES it (POST /group remove)', async () => {
    const ctx = { base, posts: [], members: [BED, LIV] }; const page = await open(browser, ctx);
    await openPicker(page);
    const liv = await rowByName(page, 'Living Room'); await liv.asElement().click();
    await page.waitForTimeout(400);
    const g = ctx.posts.find((p) => p.url === 'music/group');
    assert.ok(g && g.body.remove && g.body.remove[0] === LIV, `expected remove:[${LIV}], got ${JSON.stringify(g && g.body)}`);
    await page.close();
  });

  await t('tapping the leader is a no-op (the anchor stays; no /group)', async () => {
    const ctx = { base, posts: [], members: [BED, LIV] }; const page = await open(browser, ctx);
    await openPicker(page);
    const bed = await rowByName(page, 'Bedroom'); await bed.asElement().click();
    await page.waitForTimeout(300);
    assert.ok(!ctx.posts.find((p) => p.url === 'music/group'), 'tapping the leader wrongly posted a group change');
    await page.close();
  });

  await t('tapping a non-groupable TV SWITCHES (transfer), not group', async () => {
    const ctx = { base, posts: [], members: [BED] }; const page = await open(browser, ctx);
    await openPicker(page);
    const sam = await rowByName(page, 'Samsung Q80CA 98'); await sam.asElement().click();
    await page.waitForTimeout(400);
    assert.ok(ctx.posts.find((p) => p.url === 'music/transfer'), 'a non-groupable tile did not transfer');
    assert.ok(!ctx.posts.find((p) => p.url === 'music/group'), 'a non-groupable tile wrongly grouped');
    await page.close();
  });

  await t('volume popover with a group shows one slider per zone; moving one writes that zone', async () => {
    const ctx = { base, posts: [], members: [BED, LIV] }; const page = await open(browser, ctx);
    await page.click('#mVolT');
    await page.waitForTimeout(500);
    const zones = await page.$$eval('.vzrow', (els) => els.map((e) => ({ z: (e.querySelector('.vzn') || {}).textContent, v: (e.querySelector('.vzv') || {}).textContent })));
    assert.strictEqual(zones.length, 2, `expected 2 zone sliders, got ${zones.length}`);
    assert.deepStrictEqual(zones.map((z) => z.z).sort(), ['Bedroom', 'Living Room']);
    // move the Living Room slider and confirm the write targets LIV
    await page.$eval('.vzrow input.vz[data-pid="' + LIV + '"]', (el) => {
      el.value = 80; el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForTimeout(400);
    const vs = ctx.posts.filter((p) => p.url === 'music/control' && p.body.action === 'volume_set');
    const liv = vs.find((p) => p.body.player_id === LIV);
    assert.ok(liv, `no volume_set for the Living Room zone; posts=${JSON.stringify(vs.map((p) => p.body))}`);
    assert.strictEqual(liv.body.value, 80, `Living Room volume wrote ${liv.body.value}, expected 80`);
    await page.close();
  });

  await t('solo playback keeps the single vertical slider (no per-zone rows)', async () => {
    const ctx = { base, posts: [], members: [BED] }; const page = await open(browser, ctx);
    await page.click('#mVolT');
    await page.waitForTimeout(400);
    assert.strictEqual(await page.$$eval('.vzrow', (e) => e.length), 0, 'solo playback wrongly showed per-zone rows');
    assert.ok(await page.evaluate(() => !!document.getElementById('mVol')), 'the single volume slider is gone');
    await page.close();
  });

  // ── layout / disambiguation guards (folded in from the retired
  //    test_touch_speaker_picker.js; the redesign supersedes its single-select
  //    assertions, but these design-agnostic ones still matter) ──────────────
  const BIG = [
    { player_id: 'up88', name: 'Zoe Panel (AirPlay)', display_name: 'Zoe Panel (AirPlay)', available: true, kind: 'speaker', kind_label: 'AirPlay speaker' },
    { player_id: 'mac', name: 'Jason’s MacBook Pro (2)', display_name: 'Jason’s MacBook Pro (2)', available: true, kind: 'computer', kind_label: 'MacBook Pro' },
    { player_id: 'sam2', name: 'Samsung Q80CA 98', display_name: 'Samsung Q80CA 98', available: true, kind: 'tv', kind_label: 'Samsung TV' },
    { player_id: 'lg', name: '[LG] webOS TV OLED55B8STB', display_name: '[LG] webOS TV OLED55B8STB', available: true, kind: 'tv', kind_label: 'LG TV' },
    { player_id: 'house', name: 'House', display_name: 'House', available: true, kind: 'group', kind_label: 'Speaker group' },
    { player_id: 'kit', name: 'Kitchen Display', display_name: 'Kitchen Display', available: true, kind: 'display', kind_label: 'Nest Hub' },
    { player_id: 'bath2', name: 'Bathroom speaker', display_name: 'Bathroom speaker', available: true, kind: 'speaker', kind_label: 'Home Mini' },
    { player_id: 'RINCON_arc', name: 'Living Room', display_name: 'Living Room', available: true, kind: 'speaker', kind_label: 'Sonos Arc' },
    { player_id: 'RINCON_beam', name: 'Bedroom', display_name: 'Bedroom', available: true, kind: 'speaker', kind_label: 'Sonos Beam' },
    { player_id: 'atv_bed', name: 'Bedroom', display_name: 'Bedroom', available: false, kind: 'tv', kind_label: 'Apple TV' },
    { player_id: 'ztouch', name: 'Zoe-touch (AirPlay)', display_name: 'Zoe-touch (AirPlay)', available: true, kind: 'speaker', kind_label: 'AirPlay speaker' },
    { player_id: 'sp1', name: 'Spare 1', display_name: 'Spare 1', available: true, kind: 'speaker', kind_label: 'Home Mini' },
    { player_id: 'sp2', name: 'Spare 2', display_name: 'Spare 2', available: true, kind: 'speaker', kind_label: 'Home Mini' },
    { player_id: 'sp3', name: 'Spare 3', display_name: 'Spare 3', available: true, kind: 'speaker', kind_label: 'Home Mini' },
    { player_id: 'sp4', name: 'Spare 4', display_name: 'Spare 4', available: true, kind: 'speaker', kind_label: 'Home Mini' },
    { player_id: 'sp5', name: 'Spare 5', display_name: 'Spare 5', available: true, kind: 'speaker', kind_label: 'Home Mini' },
  ];
  async function openBig(ctx) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await page.route((url) => !String(url).startsWith(ctx.base), (route) =>
      route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
    await page.route('**/api/**', (route) => {
      const u = route.request().url();
      const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
      if (route.request().method() === 'POST') return json({ ok: true });
      if (u.includes('now-playing')) return json({ available: true, now_playing: { state: 'idle' } });   // idle → single-select, but layout is the same
      if (u.includes('/api/music/queue/')) return json({ available: true, items: [] });
      if (u.includes('/api/music/players')) return json({ available: true, players: BIG });
      if (u.includes('/api/music/groups')) return json({ available: true, players: BIG.map((p) => ({ ...p, group_member_ids: [], can_group_with: [] })), groups: [] });
      if (u.includes('sleep-gate')) return json({ block: false });
      if (u.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
      if (u.includes('display/preferences')) return json({ preferences: {} });
      if (u.includes('skybridge/timers')) return json({ timers: [] });
      return json({});
    });
    await page.goto(ctx.base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1&domain=music', { waitUntil: 'domcontentloaded' });
    await page.addStyleTag({ content: '#authov{display:none !important}' });
    await page.waitForSelector('#mSpk', { timeout: 8000 }); await page.waitForTimeout(1200);
    await page.click('#mSpk'); await page.waitForSelector('.spkopt', { timeout: 5000 }); await page.waitForTimeout(300);
    return page;
  }

  await t('a big roster fits the 720 stage: modal capped, grid scrolls, Cancel visible', async () => {
    const ctx = { base }; const page = await openBig(ctx);
    const m = await page.evaluate(() => {
      const grid = document.querySelector('.spkgrid'), mc = document.querySelector('.estmc'), cancel = document.querySelector('.estmc [data-x="cancel"]');
      const r = (e) => { const b = e.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom) }; };
      return { mc: r(mc), cancel: r(cancel), mcScroll: mc.scrollHeight, mcClient: mc.clientHeight, gridScroll: grid.scrollHeight, gridClient: grid.clientHeight };
    });
    assert.ok(m.mc.top >= 0 && m.mc.bottom <= 720, `modal escapes the stage (${m.mc.top}..${m.mc.bottom})`);
    assert.ok(m.cancel.top >= 0 && m.cancel.bottom <= 720, `Cancel off-screen (${m.cancel.top}..${m.cancel.bottom})`);
    assert.ok(m.mcScroll <= m.mcClient + 1, 'the modal itself scrolled — only the grid should');
    assert.ok(m.gridScroll > m.gridClient, '16 rows did not overflow the grid — the scroll path is untested');
    await page.close();
  });

  await t('two same-name "Bedroom" zones are tellable apart; the unavailable one is dimmed', async () => {
    const ctx = { base }; const page = await openBig(ctx);
    const beds = await page.$$eval('.spkopt', (els) => els.filter((e) => (e.querySelector('.snm') || {}).textContent === 'Bedroom').map((e) => ({
      sub: (e.querySelector('.ssub') || {}).textContent, off: e.classList.contains('off'), icon: (e.querySelector('.si svg') || {}).outerHTML || '',
    })));
    assert.strictEqual(beds.length, 2, `expected two Bedrooms, got ${beds.length}`);
    assert.notStrictEqual(beds[0].sub, beds[1].sub, 'both Bedrooms show the same subtitle');
    assert.notStrictEqual(beds[0].icon, beds[1].icon, 'both Bedrooms drew the same type icon');
    assert.ok(beds.some((b) => b.off && /Unavailable/i.test(b.sub)), 'the unavailable Bedroom is not dimmed/marked');
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
