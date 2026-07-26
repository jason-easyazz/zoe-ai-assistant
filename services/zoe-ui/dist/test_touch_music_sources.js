/*
 * Browser test: Browse → Sources tab — reconnect a music provider (touch/home.html).
 *
 * "have you set up something that would tell a user and then allow them to fix
 *  it?" — the Sources tab lists music services; one that's configured but not
 *  loaded (YouTube Music failing its Premium check) shows amber "Reconnect",
 *  which mints the existing QR setup flow so the owner signs in on their phone.
 *
 * WHY A REAL BROWSER: which POST a tap makes, the amber needs-attention state,
 * the guest→sign-in fallback, and the QR modal are DOM/behaviour claims.
 *
 * Run: node services/zoe-ui/dist/test_touch_music_sources.js
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
const CATALOGUE = [
  { domain: 'ytmusic', name: 'YouTube Music', auth: 'browser', connected: true, needs_attention: true, reason: 'User does not have Youtube Music Premium' },
  { domain: 'spotify', name: 'Spotify', auth: 'oauth', connected: true, needs_attention: false, reason: '' },
  { domain: 'tidal', name: 'Tidal', auth: 'oauth', connected: false, needs_attention: false, reason: '' },
  { domain: 'radiobrowser', name: 'Radio (free)', auth: 'free', connected: false, needs_attention: false, reason: '' },
];

function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
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

/* guest:true → /setup/catalogue + /setup/start 401 (kiosk guest). */
async function open(browser, ctx) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errs = []; page.on('pageerror', (e) => errs.push(String(e.message))); ctx.errs = errs;
  await page.route((url) => !String(url).startsWith(ctx.base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', (route) => {
    const u = route.request().url();
    const json = (b, s) => route.fulfill({ status: s || 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (u.includes('/api/music/setup/catalogue')) {
      if (ctx.guest) return json({ detail: 'auth required' }, 401);
      return json({ providers: CATALOGUE });
    }
    if (u.includes('/api/music/setup/start')) {
      if (ctx.guest) return json({ detail: 'auth required' }, 401);
      const body = JSON.parse(route.request().postData() || '{}');
      ctx.starts.push(body.provider);
      if (body.provider === 'radiobrowser') return json({ ok: true, immediate: true, provider: body.provider });
      return json({ ok: true, provider: body.provider, auth: 'browser', qr_path: '/api/music/setup/qr?token=TOK&provider=' + body.provider, setup_url: 'https://x/setup', expires_in: 300 });
    }
    if (u.includes('/api/music/setup/qr')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220"><rect width="220" height="220" fill="#fff"/></svg>' });
    if (route.request().method() === 'POST') return json({ ok: true });
    if (u.includes('/api/music/now-playing')) return json({ available: true, now_playing: { state: 'idle' } });
    if (u.includes('/api/music/queue/')) return json({ available: true, items: [] });
    if (u.includes('/api/music/players')) return json({ available: true, players: [] });
    if (u.includes('/api/music/recently-played')) return json({ available: true, items: [] });
    if (u.includes('/api/music/playlists')) return json({ playlists: [] });
    if (u.includes('sleep-gate')) return json({ block: false });
    if (u.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    if (u.includes('display/preferences')) return json({ preferences: {} });
    if (u.includes('skybridge/timers')) return json({ timers: [] });
    return json({});
  });
  await page.goto(ctx.base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1&domain=music', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForSelector('#mSpk', { timeout: 8000 });
  await page.waitForTimeout(1000);
  // music → Browse → Sources
  await page.click('#mBrowse');
  await page.waitForSelector('.mqtabs [data-qt="sources"]', { timeout: 5000 });
  await page.click('.mqtabs [data-qt="sources"]');
  await page.waitForTimeout(500);
  return page;
}

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nBrowse → Sources: reconnect a music provider — 1280x720\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  await t('the unhealthy provider shows amber "Reconnect"; healthy shows "Connected"; new shows "Connect"', async () => {
    const ctx = { base, starts: [] }; const page = await open(browser, ctx);
    const rows = await page.$$eval('.srcrow', (els) => els.map((e) => ({
      nm: (e.querySelector('.srcnm') || {}).textContent, warn: e.classList.contains('warn'),
      btn: (e.querySelector('.srcbtn') || {}).textContent || '', ok: !!e.querySelector('.srcok'),
      sub: (e.querySelector('.srcsub') || {}).textContent,
    })));
    const yt = rows.find((r) => r.nm === 'YouTube Music');
    assert.ok(yt && yt.warn && /Reconnect/.test(yt.btn), `YT Music not amber/Reconnect: ${JSON.stringify(yt)}`);
    assert.ok(/Premium/i.test(yt.sub), `reason not shown: ${yt.sub}`);
    assert.ok(rows.find((r) => r.nm === 'Spotify').ok, 'healthy Spotify not marked Connected');
    assert.ok(/Connect$/.test(rows.find((r) => r.nm === 'Tidal').btn), 'unconfigured Tidal not offering Connect');
    assert.strictEqual(ctx.errs.length, 0, 'page errors: ' + ctx.errs.join(' | '));
    await require('fs').promises.mkdir('/tmp/src-shots', { recursive: true }).catch(() => {});
    await page.screenshot({ path: '/tmp/claude-1000/-home-zoe-assistant--claude-worktrees-pedantic-maxwell-3f9763/0c7881cf-ed4b-478f-b5d3-49a70e000628/scratchpad/sources.png' });
    await page.close();
  });

  await t('tapping Reconnect starts the setup flow and shows the QR', async () => {
    const ctx = { base, starts: [] }; const page = await open(browser, ctx);
    await page.click('.srcrow.warn .srcbtn');
    await page.waitForTimeout(500);
    assert.deepStrictEqual(ctx.starts, ['ytmusic'], `setup/start not called for ytmusic: ${JSON.stringify(ctx.starts)}`);
    const qr = await page.$('.estmc .srcqr img');
    assert.ok(qr, 'the QR modal did not open');
    const src = await qr.getAttribute('src');
    assert.ok(/setup\/qr/.test(src), `QR img src wrong: ${src}`);
    await page.screenshot({ path: '/tmp/claude-1000/-home-zoe-assistant--claude-worktrees-pedantic-maxwell-3f9763/0c7881cf-ed4b-478f-b5d3-49a70e000628/scratchpad/sources_qr.png' });
    await page.close();
  });

  await t('a free provider connects immediately (no QR)', async () => {
    const ctx = { base, starts: [] }; const page = await open(browser, ctx);
    // Radio (free) is the last row; click its Connect.
    const btn = await page.evaluateHandle(() => Array.from(document.querySelectorAll('.srcrow')).find((e) => (e.querySelector('.srcnm') || {}).textContent === 'Radio (free)').querySelector('.srcbtn'));
    await btn.asElement().click();
    await page.waitForTimeout(400);
    assert.deepStrictEqual(ctx.starts, ['radiobrowser']);
    assert.ok(!(await page.$('.estmc .srcqr img')), 'a free provider wrongly opened a QR');
    await page.close();
  });

  await t('a guest is prompted to sign in (catalogue is owner-gated)', async () => {
    const ctx = { base, starts: [], guest: true }; const page = await open(browser, ctx);
    const note = await page.$('.srcnote');
    assert.ok(note, 'no sign-in prompt for a guest');
    assert.ok(await page.$('#srcSignin'), 'no Sign in button for a guest');
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
