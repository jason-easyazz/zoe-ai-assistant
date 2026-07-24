/*
 * Browser tests for two panel defects (touch/home.html):
 *
 * 1. ORB = VOICE, not a keyboard.
 *    "when you touch the zoe orb ... I want it to activate the hey zoe beep,
 *     and for it to be ready for voice and prompt users to ask zoe something
 *     (currently it just brings up a text input box, which is no good on a box
 *     that doesnt have a keyboard)".
 *    The orb used to `cmdbar.classList.toggle('on')` and focus a text input.
 *    It now POSTs the panel daemon's /activate — the SAME endpoint the wake
 *    word and the contacts search use, which is what plays the beep and opens
 *    the mic. The typed bar survives ONLY as the off-panel fallback.
 *
 * 2. THE DOCK MUST NOT FLASH.
 *    "Occasionally the dock flashes, or changes quickly, just while your on one
 *     card, not touching anything."
 *    refreshHA() runs every 30s and called renderDock() unconditionally, which
 *    replaced dbody.innerHTML wholesale — rebuilding every tile/icon/SVG even
 *    when not one value had moved. The fix skips the write when the generated
 *    markup is identical. Proven here by identity of the live DOM NODES across a
 *    refresh: a rebuild replaces them, an in-place no-op does not.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Claim 1 is about which network call a tap makes and which element ends up
 * focusable; claim 2 is about DOM node IDENTITY surviving a timer. Neither is
 * observable in a fake DOM.
 *
 * Run:  node services/zoe-ui/dist/test_touch_orb_and_dock.js
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
// A real pinned dock: the operator's actual pin (Bedroom Light) — the tile whose
// rebuild was visible as the flash.
const PANEL_CFG = {
  device_id: 'zoe-touch-pi', location: 'bedroom', room_id: 'r-bed', room_name: 'Bedroom',
  default_player: null, default_player_source: 'none', pins_configured: true,
  pinned: [{
    name: 'Bedroom', kind: 'toggle', read_eid: 'switch.bedroom_1_switch_1',
    write_eid: 'switch.bedroom_1_switch_1', write_action: 'toggle', state: 'on',
    setpoint: null, friendly_name: 'Bedroom Light', icon: 'mdi:ceiling-light',
    available: true, min: null, max: null, step: null, unit: null,
  }],
  unresolved: [], ha_available: true, max_pins: 4,
};

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

/* daemon:true  -> the panel voice daemon answers /activate (on-panel)
   daemon:false -> nothing is listening on :7777 (desktop browser / dev) */
async function open(browser, ctx, base, daemon) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.route('**://localhost:7777/**', (route) => {
    ctx.activates.push(route.request().method());
    if (daemon) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    return route.abort('connectionrefused');
  });
  await page.route((url) => !String(url).startsWith(base) && !String(url).includes('localhost:7777'), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (route.request().method() === 'POST') return json({ ok: true });
    if (url.includes('/api/panels/') && url.includes('sleep-gate')) return json({ block: false, reason: 'room-dark', entities: [] });
    if (url.includes('/api/panels/')) return json(PANEL_CFG);
    if (url.includes('/api/ha/entities')) {
      return json([{ entity_id: 'switch.bedroom_1_switch_1', state: 'on', attributes: { friendly_name: 'Bedroom Light' } }]);
    }
    if (url.includes('/api/music/now-playing')) return json({ available: true, now_playing: { state: 'idle' } });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    return json({});
  });
  await page.goto(base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => {
    const b = document.getElementById('dbody');
    return b && !b.textContent.includes('…');
  }, { timeout: 8000 });
  await page.waitForTimeout(400);
  return page;
}

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\norb = voice, and the dock must not flash — 1280x720\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  await t('tapping the orb hands the mic to the panel daemon (no text box)', async () => {
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, true);
    await page.evaluate(() => document.getElementById('orb').click());
    await page.waitForTimeout(600);
    const s = await page.evaluate(() => ({
      cmdbarOpen: document.getElementById('cmdbar').classList.contains('on'),
      listening: document.getElementById('orb').classList.contains('listening'),
      toast: document.getElementById('saytoast').classList.contains('on'),
      toastText: document.getElementById('saytoast').textContent,
    }));
    assert.deepStrictEqual(ctx.activates, ['POST'], `expected one POST to the daemon, got ${JSON.stringify(ctx.activates)}`);
    assert.strictEqual(s.cmdbarOpen, false, 'the keyboard-only text box still opened on the panel');
    assert.strictEqual(s.listening, true, 'the orb did not show a listening state');
    assert.strictEqual(s.toast, true, 'no on-screen prompt telling the user to speak');
    assert.ok(/ask zoe/i.test(s.toastText), `prompt was ${JSON.stringify(s.toastText)}`);
    console.log(`      prompt: ${JSON.stringify(s.toastText.trim())}`);
    await page.close();
  });

  await t('off-panel (no daemon) it falls back to the typed bar', async () => {
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, false);
    await page.evaluate(() => document.getElementById('orb').click());
    await page.waitForTimeout(900);
    const s = await page.evaluate(() => ({
      cmdbarOpen: document.getElementById('cmdbar').classList.contains('on'),
      listening: document.getElementById('orb').classList.contains('listening'),
      focused: document.activeElement && document.activeElement.id,
    }));
    assert.strictEqual(s.cmdbarOpen, true, 'no fallback: the build would be unusable in a desktop browser');
    assert.strictEqual(s.listening, false, 'orb left pretending to listen when the daemon never answered');
    assert.strictEqual(s.focused, 'cmdin', `focus went to ${s.focused}, expected the text input`);
    await page.close();
  });

  await t('rapid double-tap sends ONE activate, not two beeps', async () => {
    // `_convLive` only guards a LiveKit session; a daemon-activated turn never
    // sets it, so without its own in-flight guard two taps inside the 800ms
    // window both reach the daemon and the wake beep fires twice.
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, true);
    await page.evaluate(() => {
      const o = document.getElementById('orb');
      o.click(); o.click(); o.click();
    });
    await page.waitForTimeout(900);
    assert.deepStrictEqual(ctx.activates, ['POST'],
      `three rapid taps produced ${ctx.activates.length} activate POSTs — the daemon would beep that many times`);
    await page.close();
  });

  await t('the guard clears after a FAILED activate (orb never wedges)', async () => {
    // If the in-flight flag only cleared on success, one unreachable daemon
    // would leave the orb permanently dead.
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, false);
    await page.evaluate(() => document.getElementById('orb').click());
    await page.waitForTimeout(1200);
    await page.evaluate(() => document.getElementById('orb').click());
    await page.waitForTimeout(1200);
    assert.strictEqual(ctx.activates.length, 2,
      `second tap after a failure produced ${ctx.activates.length} total attempts — the orb wedged`);
    await page.close();
  });

  // NOT TESTED HERE, deliberately: cancelling `_orbListenT` when convEvent takes
  // over the orb needs a live LiveKit `state` message, and convEvent is closure
  // -scoped with no seam to drive it. A test that faked it would assert nothing
  // (the first draft of this called a probe that did not exist and "passed").
  // The change is a one-line defensive clear; it is reviewed, not proven.

  await t('an unchanged refresh does NOT rebuild the dock (no flash)', async () => {
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, true);
    // Tag the live nodes, force the same refresh the 30s timer performs, and see
    // whether the very same element objects survived.
    // refreshHA() is closure-scoped, so drive the REAL 30s interval rather than
    // reaching for a test-only hook: that is the exact path that flashed.
    const tagged = await page.evaluate(() => {
      const t = Array.from(document.getElementById('dbody').querySelectorAll('.pc'));
      t.forEach((el, i) => { el.__mark = 'keep' + i; });
      return t.length;
    });
    assert.ok(tagged > 0, 'no dock tiles rendered to observe');
    await page.waitForTimeout(33000);          // one full refreshHA cycle
    const survived = await page.evaluate((n) => {
      const after = Array.from(document.getElementById('dbody').querySelectorAll('.pc'));
      return {
        beforeCount: n,
        afterCount: after.length,
        allSame: after.length === n && after.every((el, i) => el.__mark === 'keep' + i),
      };
    }, tagged);
    assert.ok(!survived.error, survived.error);
    assert.strictEqual(survived.afterCount, survived.beforeCount, 'tile count changed across an unchanged refresh');
    assert.strictEqual(survived.allSame, true,
      'the dock tiles were REPLACED on a refresh that changed nothing — that is the flash');
    console.log(`      ${survived.beforeCount} tiles survived refreshHA() intact`);
    await page.close();
  });

  await t('a refresh that DOES change state still repaints', async () => {
    // The guard must stay narrow: skipping writes must not freeze the dock.
    const ctx = { activates: [] }; const page = await open(browser, ctx, base, true);
    await page.evaluate(() => {
      Array.from(document.getElementById('dbody').querySelectorAll('.pc'))
        .forEach((el, i) => { el.__mark = 'old' + i; });
    });
    // Re-point the config endpoint at an OFF light, then let the timer fire.
    await page.route('**/api/panels/**', (route) => {
      if (route.request().url().includes('sleep-gate')) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ block: false }) });
      }
      const off = JSON.parse(JSON.stringify(PANEL_CFG));
      off.pinned[0].state = 'off';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(off) });
    });
    await page.waitForTimeout(33000);          // one full refreshHA cycle
    const res = await page.evaluate(() => {
      const tiles = Array.from(document.getElementById('dbody').querySelectorAll('.pc'));
      return { replaced: tiles.some((el) => !el.__mark), anyOn: tiles.some((el) => el.classList.contains('on')) };
    });
    assert.strictEqual(res.replaced, true, 'a real state change did NOT repaint the dock — the guard froze it');
    assert.strictEqual(res.anyOn, false, 'the light shows as on after being turned off');
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
