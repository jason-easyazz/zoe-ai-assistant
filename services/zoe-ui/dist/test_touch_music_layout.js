/*
 * Browser test for the cleaned-up music card layout (touch/home.html).
 *
 * Operator asks (all one surface):
 *  - transport reduced to back / play / next; play stays CENTRED;
 *  - a TOOLS button (left) that REPLACES the progress bar with keep-playing +
 *    shuffle + repeat, then AUTO-HIDES ("tools you touch once and leave");
 *  - volume stays to the RIGHT of the controls;
 *  - the speaker selector hard against the top-right; the playlist button gone;
 *  - two buttons flanking the cover flow in its styling: a search magnifier one
 *    side, playlists/radio/sources the other.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Every claim is geometry (centred play, corner buttons, flanking the covers) or
 * a timed state swap (tools replaces scrub, then auto-hides). A fake DOM sees
 * none of it. Real headless Chromium at the panel's 1280x720.
 *
 * Run:  node services/zoe-ui/dist/test_touch_music_layout.js
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
const P = 'RINCON_347E5C9BEC8F01400';
const TRK = ['Meet Joe Black', 'Whisper of the Heart', 'Threnody', 'Gattaca', 'The Village'];
const QUEUE = TRK.map((t, i) => ({
  queue_id: P, queue_item_id: 'q' + i, name: t, title: t, artist: 'Thomas Newman', image: '',
  index: i, sort_index: i, duration: 200, available: true,
  media_item: { uri: 'y://' + i, favorite: false }, streamdetails: {}, extra_attributes: {},
}));
function nowPlaying(state) {
  return {
    player_id: P, player_name: 'Bedroom', state: state || 'playing', title: state === 'idle' ? '' : TRK[0],
    artist: 'Thomas Newman', image: '', volume: 30, queue_id: P, queue_item_id: 'q0', queue_index: 0,
    shuffle: false, repeat: 'off', elapsed: 70, duration: 200, dont_stop: false,
  };
}

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

async function open(browser, ctx, base, state) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errs = []; page.on('pageerror', (e) => errs.push(String(e.message))); ctx.errs = errs;
  await page.route('**://localhost:7777/**', (route) => {
    ctx.activates.push(route.request().method());
    return ctx.daemon === false ? route.abort('connectionrefused')
      : route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route((url) => !String(url).startsWith(base) && !String(url).includes('localhost:7777'), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', (route) => {
    const u = route.request().url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (route.request().method() === 'POST') { ctx.posts.push(u.split('/api/')[1].split('?')[0]); return json({ ok: true }); }
    if (u.includes('now-playing')) return json({ available: true, now_playing: nowPlaying(state) });
    if (u.includes('/api/music/queue/')) return json({ available: true, items: QUEUE });
    if (u.includes('/api/music/players')) return json({ available: true, players: [{ player_id: P, name: 'Bedroom', display_name: 'Bedroom', available: true, kind: 'speaker', kind_label: 'Sonos Beam' }] });
    if (u.includes('sleep-gate')) return json({ block: false });
    if (u.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    if (u.includes('display/preferences')) return json({ preferences: {} });
    if (u.includes('skybridge/timers')) return json({ timers: [] });
    return json({});
  });
  await page.goto(base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1&domain=music', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  // .mfull holds only absolutely-positioned children, so it collapses to zero
  // height — wait for it ATTACHED, not "visible", or Playwright calls it hidden.
  await page.waitForSelector('.mfull', { state: 'attached', timeout: 8000 });
  if (state === 'idle') await page.waitForSelector('#mTitle', { timeout: 8000 });
  else await page.waitForSelector('.mfull .cfc.on', { timeout: 8000 });
  await page.waitForTimeout(600);
  return page;
}
const box = (page, sel) => page.evaluate((s) => {
  const e = document.querySelector(s); if (!e) return null;
  const r = e.getBoundingClientRect();
  return { x: Math.round(r.left), r: Math.round(r.right), y: Math.round(r.top), b: Math.round(r.bottom), cx: Math.round(r.left + r.width / 2), vis: r.width > 0 && getComputedStyle(e).display !== 'none' };
}, sel);

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nmusic card layout — 1280x720\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  await t('playlist button gone; search flanks left, browse flanks right of the covers', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    const gone = await page.evaluate(() => !document.getElementById('mQBtn'));
    assert.ok(gone, 'the old #mQBtn playlist button is still present');
    const cf = await box(page, '#mCF'), s = await box(page, '#mSearch'), br = await box(page, '#mBrowse');
    assert.ok(s && br, 'search/browse flank buttons missing');
    // #mCF spans the card width; the COVERS sit centred within it. So the flank
    // test is: search hugs the LEFT card edge, browse the RIGHT, and both are
    // vertically centred on the cover band (between cf.y and cf.b).
    assert.ok(s.cx < 140, `search is not against the left edge (cx=${s.cx})`);
    assert.ok(br.cx > 1280 - 140, `browse is not against the right edge (cx=${br.cx})`);
    const band = (b) => b.y >= cf.y - 4 && b.b <= cf.b + 4;
    assert.ok(band(s) && band(br), `flank buttons not centred on the cover band (cf ${cf.y}..${cf.b}, s ${s.y}..${s.b}, br ${br.y}..${br.b})`);
    assert.strictEqual(ctx.errs.length, 0, 'page errors: ' + ctx.errs.join(' | '));
    await page.close();
  });

  await t('transport is back/play/next only, and play stays centred', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    const n = await page.evaluate(() => document.querySelectorAll('#mTransport button').length);
    assert.strictEqual(n, 3, `transport has ${n} buttons, expected 3 (back/play/next)`);
    const pp = await box(page, '#mPP');
    assert.ok(Math.abs(pp.cx - 640) <= 8, `play button centre is ${pp.cx}, expected ~640`);
    await page.close();
  });

  await t('tools left + volume right are the bottom corners, on the play row', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    const tb = await box(page, '#mToolsBtn'), v = await box(page, '#mVolT'), pp = await box(page, '#mPP');
    assert.ok(tb.cx < pp.x, `tools button is not left of play (tools.cx=${tb.cx} play.x=${pp.x})`);
    assert.ok(v.cx > pp.r, `volume is not right of play (vol.cx=${v.cx} play.r=${pp.r})`);
    assert.ok(Math.abs(tb.b - pp.b) <= 4 && Math.abs(v.b - pp.b) <= 4, 'tools/volume not on the play row');
    await page.close();
  });

  await t('speaker selector sits hard against the top-right', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    const spk = await box(page, '#mSpk');
    assert.ok(spk.r >= 1280 - 60, `speaker right edge is ${spk.r}, expected within ~44px of 1280`);
    await page.close();
  });

  await t('tools REPLACES the scrub (never both), and shows keep/shuffle/repeat', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    const before = await page.evaluate(() => ({
      scrub: getComputedStyle(document.querySelector('.mfull .mscrub')).display !== 'none',
      tools: getComputedStyle(document.getElementById('mTools')).display !== 'none',
    }));
    assert.ok(before.scrub && !before.tools, 'scrub should show and tools be hidden by default');
    await page.click('#mToolsBtn');
    await page.waitForTimeout(250);
    const after = await page.evaluate(() => ({
      scrub: getComputedStyle(document.querySelector('.mfull .mscrub')).display !== 'none',
      tools: getComputedStyle(document.getElementById('mTools')).display !== 'none',
      controls: ['mDS', 'mShuf', 'mRep'].filter((id) => document.getElementById(id)).length,
    }));
    assert.ok(!after.scrub && after.tools, 'tools did not replace the scrub');
    assert.strictEqual(after.controls, 3, 'tools row missing keep/shuffle/repeat');
    await page.close();
  });

  await t('the tools row AUTO-HIDES after the timer', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base);
    await page.click('#mToolsBtn');
    await page.waitForTimeout(300);
    assert.strictEqual(await page.evaluate(() => document.querySelector('.mfull').classList.contains('tools')), true, 'tools did not open');
    await page.waitForTimeout(5200);
    assert.strictEqual(await page.evaluate(() => document.querySelector('.mfull').classList.contains('tools')), false, 'tools did not auto-hide');
    // scrub is back
    assert.ok(await page.evaluate(() => getComputedStyle(document.querySelector('.mfull .mscrub')).display !== 'none'), 'scrub did not return after auto-hide');
    await page.close();
  });

  await t('search hands the mic to the daemon (voice, not a text box)', async () => {
    const ctx = { posts: [], activates: [], daemon: true }; const page = await open(browser, ctx, base);
    await page.click('#mSearch');
    await page.waitForTimeout(500);
    assert.deepStrictEqual(ctx.activates, ['POST'], `search sent ${JSON.stringify(ctx.activates)} to the daemon`);
    const s = await page.evaluate(() => ({ listening: document.getElementById('orb').classList.contains('listening'), toast: document.getElementById('saytoast').textContent }));
    assert.ok(s.listening, 'orb not showing a listening state after search');
    assert.ok(/hear/i.test(s.toast), `search prompt was ${JSON.stringify(s.toast)}`);
    await page.close();
  });

  await t('idle hides the tools button and volume (nothing to act on)', async () => {
    const ctx = { posts: [], activates: [] }; const page = await open(browser, ctx, base, 'idle');
    await page.waitForTimeout(400);
    const v = await page.evaluate(() => ({
      tools: getComputedStyle(document.getElementById('mToolsBtn')).display !== 'none',
      vol: getComputedStyle(document.getElementById('mVolT')).display !== 'none',
    }));
    assert.ok(!v.tools && !v.vol, `idle left tools=${v.tools} vol=${v.vol} visible`);
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
