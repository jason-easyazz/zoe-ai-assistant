/*
 * Browser test for the presence-aware idle sleep guard (touch/home.html).
 *
 * "the sleep card keeps coming up on zoe, even though I'm not asleep anymore".
 * The `sleep` surface is a plain INACTIVITY timer — it never knew whether a
 * human was present, so it drifted to the night clock while the operator sat in
 * a lit room. There is no presence sensor in this house (44 HA entities, zero
 * binary_sensor), so the operator chose "light fully blocks sleep": a toggle on
 * in the panel's OWN room keeps the panel awake.
 *
 * WHY A REAL BROWSER
 * ------------------
 * The claim is about a TIMER racing two in-flight requests, then a real
 * surface transition. The interesting failure is a scheduling one — a vote that
 * already arrived being discarded by a slower sibling request. A fake DOM with
 * fake timers proves nothing about that; this drives real headless Chromium,
 * with the idle window shortened to 1s via the display-preferences endpoint
 * (sleep_seconds), exactly the knob the client already honours.
 *
 * The sleep surface is identified by `.slp` / `#slClock` (FULL.sleep).
 *
 * Run:  node services/zoe-ui/dist/test_touch_sleep_gate.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>
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
  return ['/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser'].find((p) => fs.existsSync(p)) || null;
}
const chromium = loadChromium();
if (!chromium) { console.error('playwright-core not found.'); process.exit(2); }
const CHROME = findChrome(chromium);
if (!CHROME) { console.error('No Chromium binary found.'); process.exit(2); }

const DIST = __dirname;
const IDLE_S = 1;   // shortened idle window so the test runs in seconds

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

/* opts: { block, playing, hangGate } */
async function boot(browser, base, opts) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.route((url) => !String(url).startsWith(base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (url.includes('/sleep-gate')) {
      // A hung request must never resolve — that is the whole point of case 3.
      if (opts.hangGate) return;                      // deliberately never fulfilled
      return json({ block: !!opts.block, reason: opts.block ? 'room-occupied' : 'room-dark', entities: [] });
    }
    if (url.includes('/api/system/display/preferences')) return json({ preferences: { sleep_enabled: true, sleep_seconds: IDLE_S } });
    if (url.includes('/api/music/now-playing')) {
      return json({ available: true, now_playing: opts.playing ? { state: 'playing', title: 'x', player_name: 'Zoe Panel' } : { state: 'idle' } });
    }
    if (url.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', location: 'bedroom', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    return json({});
  });
  await page.goto(base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForTimeout(1200);   // let boot + display-prefs land and arm the timer
  return page;
}
const asleep = (page) => page.evaluate(() => !!document.querySelector('.slp'));

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\nidle sleep gate — 1280x720\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  await t('a DARK room still sleeps (the guard stays narrow)', async () => {
    const page = await boot(browser, base, { block: false, playing: false });
    await page.waitForTimeout(6000);
    assert.strictEqual(await asleep(page), true, 'panel did NOT sleep in a dark, idle room — the screensaver is broken');
    await page.close();
  });

  await t('a LIT room keeps the panel awake (the reported bug)', async () => {
    const page = await boot(browser, base, { block: true, playing: false });
    await page.waitForTimeout(6000);
    assert.strictEqual(await asleep(page), false, 'panel drifted to the sleep clock while its room was occupied');
    await page.close();
  });

  await t('music still wins even if the room gate HANGS (vote not discarded)', async () => {
    // The regression this guards: the 4s timeout deciding a hard `false` would
    // throw away the "music is playing" vote that already arrived, and sleep
    // mid-song. Wait well past the 4s race.
    const page = await boot(browser, base, { playing: true, hangGate: true });
    await page.waitForTimeout(9000);
    assert.strictEqual(await asleep(page), false, 'slept mid-song: a hung gate request discarded the music vote');
    await page.close();
  });

  await t('both signals quiet + gate hangs still falls through to SLEEPING', async () => {
    // Unknown must never latch the panel awake.
    const page = await boot(browser, base, { playing: false, hangGate: true });
    await page.waitForTimeout(9000);
    assert.strictEqual(await asleep(page), true, 'panel latched awake on a hung request — it must fail toward sleeping');
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
