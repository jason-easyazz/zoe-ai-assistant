/*
 * Browser test: tapping an UPCOMING cover in the Cover Flow must play it.
 *
 * "I cant seem to click on an upcoming track to get it to play now."
 *
 * THE DEAD ZONE
 * -------------
 * `end()` branches in this order:
 *     if (d.lifted)  -> cfCommitMove(d.i, d.to ?? d.i)      // a move to ITSELF
 *     ...
 *     if (dt<600 && !d.moved) -> cfPlay(d.i)                // never reached
 * and `d.lifted` is set by a CF_HOLD_MS (320ms) timer. So a STATIONARY press
 * lasting 320-600ms — an utterly ordinary deliberate tap, especially when
 * aiming at a smaller off-centre cover — lifts the card and then commits a
 * move-to-itself: it neither plays nor reorders. Under 320ms it plays, so the
 * bug is invisible to a fast flick and reproducible with a normal tap.
 *
 * WHY A REAL BROWSER
 * ------------------
 * The claim is about pointer-event TIMING against real setTimeout handlers and
 * the real hit-testing of a 3D-transformed card. A fake DOM proves nothing here.
 *
 * FIXTURE PROVENANCE: queue rows carry the flat shape the seam resolves
 * (`normalize_queue_items` in routers/music.py): title/artist/image/index from
 * `sort_index`, which is what #1422 fixed. `index` is the ABSOLUTE queue
 * position and is what cfPlay posts.
 *
 * Run:  node services/zoe-ui/dist/test_touch_cf_tap_play.js
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
const PLAYER = 'RINCON_347E5C9BEC8F01400';
const TRACKS = ['Meet Joe Black', 'Whisper of the Heart', 'Threnody', 'Gattaca', 'The Village', 'Road to Perdition'];
const QUEUE = TRACKS.map((t, i) => ({
  queue_id: PLAYER, queue_item_id: 'q' + i, name: 'Thomas Newman - ' + t,
  title: t, artist: 'Thomas Newman', image: '', index: i, sort_index: i,
  duration: 200, available: true, media_item: {}, streamdetails: {}, extra_attributes: {},
}));
const NOW = {
  player_id: PLAYER, player_name: 'Bedroom', state: 'playing', title: TRACKS[0],
  artist: 'Thomas Newman', album: '', image: '', volume: 18, queue_id: PLAYER,
  queue_item_id: 'q0', queue_index: 0, shuffle: false, repeat: 'off',
  elapsed: 10, duration: 200, dont_stop: false,
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

async function open(browser, ctx, base) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.route((url) => !String(url).startsWith(base), (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' }));
  await page.route('**/api/**', async (route) => {
    const req = route.request(); const url = req.url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (req.method() === 'POST') { ctx.posts.push({ url, body: JSON.parse(req.postData() || '{}') }); return json({ ok: true }); }
    if (url.includes('/api/music/now-playing')) return json({ available: true, now_playing: NOW });
    if (url.includes('/api/music/queue/')) return json({ available: true, items: QUEUE });
    if (url.includes('/api/music/players')) return json({ available: true, players: [{ player_id: PLAYER, name: 'Bedroom', display_name: 'Bedroom', available: true, kind: 'speaker', kind_label: 'Sonos Beam' }] });
    if (url.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', room_name: 'Bedroom', pins_configured: false, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    return json({});
  });
  await page.goto(base + '/touch/home.html?panel_id=zoe-touch-pi&kiosk=1&domain=music', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForSelector('.mfull .cfc.on', { timeout: 8000 });
  await page.waitForTimeout(500);
  return page;
}

/* The point where cover `i` is actually the TOPMOST element.
 *
 * Cover Flow cards are 3D-transformed and overlap, so a card's bounding-box
 * centre is often occluded by its neighbour. A finger lands on the VISIBLE face
 * of a cover, so the test must too — hit-testing with elementFromPoint keeps
 * this honest instead of quietly driving the wrong card. */
async function hitPoint(page, i) {
  return page.evaluate((idx) => {
    const el = document.querySelector('.cfc[data-i="' + idx + '"]');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    for (let fx = 0.5; fx <= 0.96; fx += 0.04) {
      for (const fy of [0.5, 0.35, 0.65]) {
        const x = Math.round(r.left + r.width * fx);
        const y = Math.round(r.top + r.height * fy);
        const hit = document.elementFromPoint(x, y);
        if (hit && hit.closest('.cfc') === el) return { x, y };
      }
    }
    return null;
  }, i);
}

/* A stationary press of `ms` on the visible face of the cover at data-i=`i`. */
async function press(page, i, ms) {
  const box = await hitPoint(page, i);
  assert.ok(box, 'cover data-i=' + i + ' is not reachable by a finger (fully occluded)');
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.waitForTimeout(ms);
  await page.mouse.up();
  await page.waitForTimeout(400);
}
const playIndexPosts = (ctx) => ctx.posts.filter((p) => p.url.includes('queue/play-index'));
const movePosts = (ctx) => ctx.posts.filter((p) => p.url.includes('queue/move'));

let failures = 0;
async function t(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); }
  catch (e) { failures++; console.log('  ✗ ' + name + '\n      ' + String(e.message).split('\n').join('\n      ')); }
}

(async () => {
  console.log('\ncover flow — tap an upcoming cover to play it\n');
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--force-device-scale-factor=1'] });

  // Control: a fast tap already worked, so it must keep working.
  await t('a FAST tap (150ms) on an upcoming cover plays it', async () => {
    const ctx = { posts: [] }; const page = await open(browser, ctx, base);
    await press(page, 2, 150);
    const posts = playIndexPosts(ctx);
    assert.strictEqual(posts.length, 1, `expected one play-index POST, got ${posts.length}`);
    assert.strictEqual(posts[0].body.index, 2, `played index ${posts[0].body.index}, expected 2`);
    await page.close();
  });

  // THE BUG: a normal deliberate tap sits in the 320-600ms dead zone.
  await t('a NORMAL tap (420ms) on an upcoming cover plays it', async () => {
    const ctx = { posts: [] }; const page = await open(browser, ctx, base);
    await press(page, 2, 420);
    const posts = playIndexPosts(ctx);
    assert.strictEqual(posts.length, 1,
      `expected one play-index POST, got ${posts.length}. Moves posted: ${JSON.stringify(movePosts(ctx).map((m) => m.body))}`);
    assert.strictEqual(posts[0].body.index, 2, `played index ${posts[0].body.index}, expected 2`);
    await page.close();
  });

  await t('a LONG stationary press (900ms) still plays rather than doing nothing', async () => {
    const ctx = { posts: [] }; const page = await open(browser, ctx, base);
    await press(page, 3, 900);
    assert.strictEqual(playIndexPosts(ctx).length, 1, 'a stationary press that never moved did not play');
    await page.close();
  });

  // The guard must stay narrow: a real reorder must NOT be turned into a play.
  await t('a real drag-to-reorder still MOVES and does not play', async () => {
    const ctx = { posts: [] }; const page = await open(browser, ctx, base);
    const box = await hitPoint(page, 2);
    assert.ok(box, 'cover data-i=2 is not reachable by a finger');
    await page.mouse.move(box.x, box.y);
    await page.mouse.down();
    await page.waitForTimeout(450);              // pass CF_HOLD_MS -> lift
    for (let s = 1; s <= 12; s++) { await page.mouse.move(box.x - s * 40, box.y); await page.waitForTimeout(16); }
    await page.mouse.up();
    await page.waitForTimeout(500);
    assert.strictEqual(playIndexPosts(ctx).length, 0, 'a deliberate reorder wrongly started playback');
    assert.ok(movePosts(ctx).length >= 1, 'the reorder did not post a queue/move');
    await page.close();
  });

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILED\n` : '\nall passed\n');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
