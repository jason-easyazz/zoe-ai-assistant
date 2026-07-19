/*
 * Browser test for the estate music card's polish pass (touch/home.html):
 * the favourite heart, the seekable scrub bar, "don't stop the music",
 * play-next, and the card's volume slider.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Same reason as test_touch_dock_pins.js: the failures this change can ship are
 * layout and gesture failures (a scrub that never arms, a heart that acts on
 * the wrong row, a 6th transport button that shoves play/pause off-centre).
 * So this drives the real page in headless Chromium at the panel's real
 * 1280x720 and reads real bounding boxes and real network writes.
 *
 * FIXTURE PROVENANCE — read before changing any fixture
 * -----------------------------------------------------
 * The shapes below are COPIED FROM THE LIVE API, not invented:
 *   curl -s localhost:8000/api/music/now-playing
 *   curl -s localhost:8000/api/music/queue/<player_id>
 * assertLiveContract() re-fetches both when the box is reachable and asserts
 * our fixture key sets ARE the live key sets. This matters here specifically:
 * the bug this change fixes is that the old heart read `now_playing.uri`, a
 * field the live API HAS NEVER RETURNED — a fixture that invented it would
 * have made the broken code pass. The live guard is what makes that
 * impossible to reintroduce.
 *
 * Run:  node services/zoe-ui/dist/test_touch_music_polish.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  MUSIC_SHOTS=<dir>
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
    try { return require(c).chromium; } catch (_) { /* next */ }
  }
  return null;
}
function findChrome(chromium) {
  const explicit = process.env.CHROME_PATH || process.env.PLAYWRIGHT_CHROMIUM;
  if (explicit && fs.existsSync(explicit)) return explicit;
  try { const p = chromium.executablePath(); if (p && fs.existsSync(p)) return p; } catch (_) {}
  return [
    '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
  ].find((p) => fs.existsSync(p)) || null;
}

const chromium = loadChromium();
if (!chromium) { console.error('playwright-core not found (PLAYWRIGHT_CORE=...)'); process.exit(2); }
const CHROME = findChrome(chromium);
if (!CHROME) { console.error('No Chromium binary (CHROME_PATH=...)'); process.exit(2); }

const DIST = __dirname;
const SHOTS = process.env.MUSIC_SHOTS || '/tmp/music-polish-shots';
const PLAYER = 'RINCON_TEST01400';

// ── fixtures (live-shaped; see provenance) ──────────────────────────────────
// now-playing: EXACTLY the live key set, plus `dont_stop` which this change
// adds to music_service.now_playing. Note there is deliberately NO `uri` here
// — the live endpoint does not return one, which is the whole bug.
function nowPlaying(over) {
  return Object.assign({
    player_id: PLAYER, player_name: 'Bedroom', state: 'playing',
    title: 'Mr. Bad News', artist: 'Thomas Newman', album: '',
    image: 'https://example.invalid/art.jpg', volume: 18,
    queue_id: PLAYER, queue_item_id: 'item-2', queue_index: 2,
    shuffle: false, repeat: 'off', elapsed: 30, duration: 100,
    dont_stop: false,
  }, over || {});
}
// queue items: live shape. `media_item` is where the real uri + favourite flag
// live; `index` is the resolved position (PR #1422), not MA's always-0 raw one.
function qitem(i, over) {
  return Object.assign({
    queue_id: PLAYER, queue_item_id: 'item-' + i, name: 'Artist - Track ' + i,
    title: 'Track ' + i, artist: 'Artist ' + i, duration: 100 + i, index: i,
    sort_index: i, image: 'https://example.invalid/a' + i + '.jpg',
    available: true, extra_attributes: {}, streamdetails: {},
    media_item: {
      uri: 'ytmusic--X://track/T' + i, favorite: false, media_type: 'track',
      item_id: 'T' + i, provider: 'ytmusic--X', name: 'Track ' + i,
    },
  }, over || {});
}
const QUEUE = [qitem(0), qitem(1), qitem(2), qitem(3), qitem(4), qitem(5)];

function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const file = path.join(DIST, rel);
    if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end('nope'); return;
    }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}

function newCtx() { return { posts: [], polls: 0 }; }

async function stub(page, ctx, opts) {
  const o = opts || {};
  const np = o.np === null ? null : (o.np || nowPlaying());
  const queue = o.queue || QUEUE;
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (req.method() === 'POST') {
      let body = {};
      try { body = JSON.parse(req.postData() || '{}'); } catch (_) {}
      const p = url.split('/api/')[1].split('?')[0];
      ctx.posts.push({ path: p, body });
      if (o.fail && o.fail(p)) return json({ ok: false });
      return json({ ok: true });
    }
    if (url.includes('/api/music/now-playing')) { ctx.polls++; return json(np ? { available: true, now_playing: np } : { available: true, now_playing: null }); }
    if (url.includes('/api/music/queue/')) return json({ available: true, items: queue });
    if (url.includes('/api/music/players')) return json({ available: true, players: [{ player_id: PLAYER, name: 'Bedroom' }] });
    if (url.includes('/api/music/recently-played')) return json({ items: o.recent || [] });
    if (url.includes('/api/music/playlists')) return json({ items: [] });
    if (url.includes('/api/panels/')) return json({ device_id: 'zoe-touch-pi', pins_configured: true, pinned: [], unresolved: [], ha_available: true, max_pins: 4 });
    if (url.includes('/api/ha/entities')) return json([]);
    if (url.includes('/api/system/status')) return json({});
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    return json({});
  });
}

// Drive the real launcher the way a finger does — no internal calls.
async function openMusic(browser, ctx, opts) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  await stub(page, ctx, opts);
  await page.goto((opts && opts.base) + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.click('#apps');
  await page.waitForTimeout(400);
  await page.click('.ltile[data-id="music"]');
  // NOT `.mfull` — its children are all absolutely positioned so it legitimately
  // measures 0px tall, and waiting for it to be "visible" hangs forever.
  await page.waitForSelector('#mTransport', { timeout: 8000 });
  await page.waitForFunction(() => document.querySelectorAll('.mfull .cfc').length > 0, { timeout: 8000 });
  await page.waitForTimeout(250);
  return page;
}

async function shoot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const f = path.join(SHOTS, name + '.png');
  await page.screenshot({ path: f });
  return f;
}

function getJSON(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (_) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(2500, () => { req.destroy(); resolve(null); });
  });
}

// ── the live-contract guard ─────────────────────────────────────────────────
async function assertLiveContract() {
  const live = await getJSON('http://localhost:8000/api/music/now-playing');
  if (!live || !live.now_playing) { console.log('  (live API unreachable / idle — contract guard skipped)'); return; }
  const lnp = live.now_playing;

  // The bug: the old heart read now_playing.uri. Prove the field does not exist,
  // so nobody "fixes" this by reaching for it again.
  assert.ok(!('uri' in lnp) && !('media_uri' in lnp),
    'LIVE now-playing unexpectedly has a uri field — the heart fix assumed it does not');

  // Our fixture must not invent keys. dont_stop is added by this change, so it
  // is allowed to be the one key present in the fixture and (until deploy) not live.
  const ours = new Set(Object.keys(nowPlaying()));
  const theirs = new Set(Object.keys(lnp));
  for (const k of theirs) assert.ok(ours.has(k), 'live now-playing key missing from fixture: ' + k);
  for (const k of ours) {
    if (k === 'dont_stop') continue;
    assert.ok(theirs.has(k), 'fixture invents a now-playing key the live API does not return: ' + k);
  }
  if (!theirs.has('dont_stop')) console.log('  (live zoe-data predates dont_stop read-back — expected until deploy)');

  const lq = await getJSON('http://localhost:8000/api/music/queue/' + encodeURIComponent(lnp.player_id));
  if (!lq || !Array.isArray(lq.items) || !lq.items.length) { console.log('  (live queue empty — item guard skipped)'); return; }
  const li = lq.items[0];
  for (const k of ['queue_item_id', 'index', 'media_item']) {
    assert.ok(k in li, 'live queue item lacks ' + k);
  }
  assert.ok(li.media_item && typeof li.media_item === 'object', 'live queue item media_item is not an object');
  // The two fields the heart depends on. If these ever vanish, the heart dies
  // silently — so assert them against the live API, not against our fixture.
  assert.ok('uri' in li.media_item, 'live media_item lacks uri — the heart has nothing to write');
  assert.ok('favorite' in li.media_item, 'live media_item lacks favorite — the heart has no read-back');
  console.log('  live contract OK (no now_playing.uri; media_item.uri + .favorite present)');
}

// ── helpers ─────────────────────────────────────────────────────────────────
const favState = (page) => page.$eval('#mFav', (e) => ({
  on: e.classList.contains('on'),
  shown: getComputedStyle(e).display !== 'none',
}));
const focusTitle = (page) => page.$eval('#mTitle', (e) => e.textContent);
// Flick the Cover Flow to the next cover the way a finger does.
async function flick(page, n) {
  const cf = await page.$('#mCF');
  const b = await cf.boundingBox();
  for (let i = 0; i < n; i++) {
    await page.mouse.move(b.x + b.width / 2 + 160, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 - 60, b.y + b.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(500);
  }
  await page.waitForTimeout(400);
}
const lastPost = (ctx, p) => [...ctx.posts].reverse().find((x) => x.path === p);

// ── tests ───────────────────────────────────────────────────────────────────
const T = [];
const test = (n, f) => T.push([n, f]);

test('live contract: fixtures match the real API', async () => { await assertLiveContract(); });

test('heart acts on the FOCUSED cover, not what is playing', async (browser, base) => {
  const ctx = newCtx();
  // Playing is item-2; focus starts there, so flick to a DIFFERENT cover.
  const page = await openMusic(browser, ctx, { base });
  const before = await focusTitle(page);
  await flick(page, 2);
  const after = await focusTitle(page);
  assert.notStrictEqual(after, before, 'flick did not move the focus — test cannot prove anything');
  await page.click('#mFav');
  await page.waitForTimeout(300);
  const post = lastPost(ctx, 'music/favorite');
  assert.ok(post, 'no favourite POST — the heart did not reach the API');
  // The uri posted must be the FOCUSED item's, i.e. the one whose title is shown.
  const focused = QUEUE.find((q) => q.title === after);
  assert.ok(focused, 'could not map the shown title back to a queue row');
  assert.strictEqual(post.body.uri, focused.media_item.uri,
    'heart favourited a different track than the label above it shows');
  await page.close();
});

test('heart reads back favourite state from the queue', async (browser, base) => {
  const ctx = newCtx();
  // item-2 is playing AND already favourited -> the heart must start lit.
  const q = QUEUE.map((x) => (x.queue_item_id === 'item-2'
    ? Object.assign({}, x, { media_item: Object.assign({}, x.media_item, { favorite: true }) })
    : x));
  const page = await openMusic(browser, ctx, { base, queue: q });
  assert.deepStrictEqual(await favState(page), { on: true, shown: true },
    'heart did not read back the favourited state');
  await page.close();
});

test('heart un-favourites (and does not just add again)', async (browser, base) => {
  const ctx = newCtx();
  const q = QUEUE.map((x) => (x.queue_item_id === 'item-2'
    ? Object.assign({}, x, { media_item: Object.assign({}, x.media_item, { favorite: true }) })
    : x));
  const page = await openMusic(browser, ctx, { base, queue: q });
  await page.click('#mFav');
  await page.waitForTimeout(300);
  assert.ok(lastPost(ctx, 'music/unfavorite'), 'tapping a lit heart did not POST /unfavorite');
  assert.ok(!lastPost(ctx, 'music/favorite'), 'tapping a lit heart re-favourited instead of removing');
  assert.strictEqual((await favState(page)).on, false, 'heart stayed lit after un-favouriting');
  await page.close();
});

test('heart hides when there is no focused row to act on', async (browser, base) => {
  // An empty queue has nothing focusable, so the heart must not keep whatever
  // the last focused track set — that is the "stays lit across track changes"
  // lie in a different costume.
  const ctx = newCtx();
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', (e) => { throw new Error('page error: ' + e.message); });
  await stub(page, ctx, { queue: [] });
  await page.goto(base + '/touch/home.html', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.click('#apps');
  await page.waitForTimeout(400);
  await page.click('.ltile[data-id="music"]');
  await page.waitForSelector('#mTransport', { timeout: 8000 });
  await page.waitForTimeout(700);
  assert.strictEqual((await favState(page)).shown, false,
    'the heart is offered with no focused track behind it');
  await page.close();
});

test('heart reverts when the write fails', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base, fail: (p) => p === 'music/favorite' });
  await page.click('#mFav');
  await page.waitForTimeout(400);
  assert.strictEqual((await favState(page)).on, false,
    'heart stayed lit after the server refused — it is lying about persisted state');
  await page.close();
});

test('scrub drag seeks to where the finger let go', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const tr = await (await page.$('.mfull .mtrack')).boundingBox();
  await page.mouse.move(tr.x + tr.width * 0.3, tr.y + tr.height / 2);
  await page.mouse.down();
  await page.mouse.move(tr.x + tr.width * 0.75, tr.y + tr.height / 2, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  const post = lastPost(ctx, 'music/seek');
  assert.ok(post, 'dragging the scrub bar did not POST /seek');
  // duration 100 -> 75% is 75s. Allow a couple of px of hit-target slop.
  assert.ok(Math.abs(post.body.position_seconds - 75) <= 3,
    'seek landed at ' + post.body.position_seconds + 's, expected ~75s');
  await page.close();
});

test('scrub tap seeks too (not drag-only)', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const tr = await (await page.$('.mfull .mtrack')).boundingBox();
  await page.mouse.click(tr.x + tr.width * 0.5, tr.y + tr.height / 2);
  await page.waitForTimeout(300);
  const post = lastPost(ctx, 'music/seek');
  assert.ok(post, 'tapping the scrub bar did not seek');
  assert.ok(Math.abs(post.body.position_seconds - 50) <= 3,
    'tap seek landed at ' + post.body.position_seconds + 's, expected ~50s');
  await page.close();
});

test('a poll mid-drag does not yank the bar back', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const tr = await (await page.$('.mfull .mtrack')).boundingBox();
  await page.mouse.move(tr.x + tr.width * 0.2, tr.y + tr.height / 2);
  await page.mouse.down();
  await page.mouse.move(tr.x + tr.width * 0.8, tr.y + tr.height / 2, { steps: 8 });
  const during = await page.$eval('#mFill', (e) => e.style.width);
  // Hold the finger down and let the REAL 5s loadMusic interval fire. Calling
  // an internal here would prove nothing — loadMusic is a closure, not on
  // window, so `window.loadMusic&&...` would no-op and the test would pass
  // vacuously. Waiting on the production timer is the honest instrument.
  const before = ctx.polls;
  await page.waitForTimeout(6200);
  assert.ok(ctx.polls > before,
    'no poll happened during the drag — this test proves nothing (polls=' + ctx.polls + ')');
  const after = await page.$eval('#mFill', (e) => e.style.width);
  assert.strictEqual(after, during,
    'the poll repainted the scrub mid-drag (' + during + ' -> ' + after + ')');
  await page.mouse.up();
  await page.close();
});

test('the knob tracks the fill between polls, not just on them', async (browser, base) => {
  // tickMusic advances the bar every 1s; loadMusic repaints both every 5s. If
  // the tick moves only the fill, the knob lags it by up to 5s. (Greptile P2.)
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const read = () => page.evaluate(() => ({
    fill: parseFloat(document.getElementById('mFill').style.width),
    knob: parseFloat(document.getElementById('mKnob').style.left),
  }));
  const a = await read();
  await page.waitForTimeout(2600);   // ticks, but no 5s poll boundary crossed
  const b = await read();
  assert.ok(b.fill > a.fill, 'the fill did not advance — the tick never ran');
  assert.ok(Math.abs(b.knob - b.fill) < 0.5,
    'knob at ' + b.knob + '% but fill at ' + b.fill + '% — they drifted apart between polls');
  await page.close();
});

test('navigating away mid-drag does not freeze the bar forever', async (browser, base) => {
  // _seek gates BOTH repainters. If a drag is abandoned by a navigation with it
  // still set, the scrub never repaints again for the rest of the session.
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const tr = await (await page.$('.mfull .mtrack')).boundingBox();
  await page.mouse.move(tr.x + tr.width * 0.8, tr.y + tr.height / 2);
  await page.mouse.down();
  await page.mouse.move(tr.x + tr.width * 0.9, tr.y + tr.height / 2, { steps: 4 });
  // Leave WITHOUT ever releasing, and never release — a pointerup (even on the
  // detached element) would clear _seek by itself and hide the bug.
  await page.evaluate(() => document.getElementById('home').click());
  await page.waitForTimeout(500);
  await page.click('#apps');
  await page.waitForTimeout(400);
  await page.click('.ltile[data-id="music"]');
  await page.waitForSelector('#mTransport', { timeout: 8000 });
  const polls = ctx.polls;
  await page.waitForTimeout(6200);
  assert.ok(ctx.polls > polls, 'no poll during the window — test proves nothing');
  // elapsed is 30/100 -> the poll must have repainted the bar to ~30%.
  const w = await page.$eval('#mFill', (e) => parseFloat(e.style.width));
  assert.ok(Math.abs(w - 30) <= 3,
    'the bar is stuck at ' + w + '% — a stranded _seek is still blocking repaints');
  await page.close();
});

test('dont-stop toggles, reads back, and reverts on refusal', async (browser, base) => {
  // read-back
  let ctx = newCtx();
  let page = await openMusic(browser, ctx, { base, np: nowPlaying({ dont_stop: true }) });
  assert.ok(await page.$eval('#mDS', (e) => e.classList.contains('on')),
    'dont-stop did not read back as enabled');
  await page.close();

  // toggle off -> posts enabled:false
  ctx = newCtx();
  page = await openMusic(browser, ctx, { base, np: nowPlaying({ dont_stop: true }) });
  await page.click('#mDS');
  await page.waitForTimeout(300);
  const post = lastPost(ctx, 'music/dont-stop');
  assert.ok(post, 'dont-stop did not POST');
  assert.strictEqual(post.body.enabled, false, 'dont-stop posted the wrong target state');
  await page.close();

  // MA refuses (no similar_tracks provider) -> the button must not lie
  ctx = newCtx();
  page = await openMusic(browser, ctx, { base, fail: (p) => p === 'music/dont-stop' });
  await page.click('#mDS');
  await page.waitForTimeout(400);
  assert.strictEqual(await page.$eval('#mDS', (e) => e.classList.contains('on')), false,
    'dont-stop stayed lit after MA refused it');
  await page.close();
});

test('play next sends option:next; add still sends add', async (browser, base) => {
  const recent = [{ uri: 'ytmusic--X://track/R1', name: 'Recent One', artist: 'A', image: '' }];
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base, recent });
  await page.click('#mQBtn');
  await page.waitForSelector('.rtile', { timeout: 6000 });
  console.log('    shot: ' + await shoot(page, 'browse-card'));
  await page.click('.rtile .qnext');
  await page.waitForTimeout(300);
  let post = lastPost(ctx, 'music/play_media');
  assert.ok(post, 'play-next did not POST play_media');
  assert.strictEqual(post.body.option, 'next', 'play-next sent the wrong option');
  await page.click('.rtile .qadd');
  await page.waitForTimeout(300);
  post = lastPost(ctx, 'music/play_media');
  assert.strictEqual(post.body.option, 'add', 'the ＋ button stopped meaning "add to queue"');
  await page.close();
});

test('volume slider reads back and writes volume_set', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  assert.strictEqual(await page.$eval('#mVol', (e) => e.value), '18',
    'volume did not read back from now-playing');
  await page.$eval('#mVol', (e) => { e.value = '40'; e.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.waitForTimeout(500);
  const post = lastPost(ctx, 'music/control');
  assert.ok(post, 'volume did not POST');
  assert.strictEqual(post.body.action, 'volume_set');
  assert.strictEqual(post.body.value, 40, 'volume posted the wrong level');
  await page.close();
});

test('layout: play/pause stays centred and nothing collides', async (browser, base) => {
  const ctx = newCtx();
  const page = await openMusic(browser, ctx, { base });
  const box = (s) => page.$eval(s, (e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const pp = await box('#mPP');
  assert.ok(Math.abs((pp.x + pp.w / 2) - 640) <= 2,
    'play/pause is off-centre at ' + (pp.x + pp.w / 2) + 'px (the 6th transport button unbalanced the row)');
  // The transport must not run under the launcher or the QR.
  const ds = await box('#mDS');
  const qr = await box('#mQR');
  assert.ok(ds.x + ds.w <= qr.x, 'dont-stop overlaps the jukebox QR');
  const apps = await box('#apps');
  const shuf = await box('#mShuf');
  assert.ok(shuf.x >= apps.x + apps.w, 'the transport runs into the launcher button');
  // Everything on screen.
  for (const s of ['#mVolWrap', '#mDS', '#mScrub', '#mFav']) {
    const b = await box(s);
    assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= 1280 && b.y + b.h <= 720, s + ' is off-screen');
  }
  // The volume pill must not sit ON the artwork. This assertion exists because
  // the first placement (in .mtop, beside the speaker chip) did exactly that,
  // and every other test still passed — only the screenshot showed it.
  const vol = await box('#mVolWrap');
  const covers = await page.$$eval('.mfull .cfc', (es) => es.map((e) => {
    const r = e.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  }));
  const hits = (a, b2) => !(a.x + a.w <= b2.x || b2.x + b2.w <= a.x || a.y + a.h <= b2.y || b2.y + b2.h <= a.y);
  const clash = covers.find((c) => hits(vol, c));
  assert.ok(!clash, 'the volume pill overlaps cover art at x' + (clash && Math.round(clash.x)));
  // ...nor the scrub it shares a row with, nor the transport below it. (It also
  // must not cover the focused cover's ✕ remove affordance — that is what the
  // cover-overlap check above really protects.)
  for (const [sel, what] of [['#mScrub', 'the scrub bar'], ['#mTransport', 'the transport'], ['#orb', 'the orb']]) {
    const b = await box(sel);
    assert.ok(!hits(vol, b), 'the volume pill overlaps ' + what);
  }
  // Finger-target floor for the kiosk.
  assert.ok(vol.h >= 48, 'volume pill is only ' + vol.h + 'px tall (kiosk floor is 48)');
  // The tight spot the breathe PR flagged: the centre cover must still clear
  // .cfmeta. Our scrub padding must not have eaten that gap.
  const cover = await page.$eval('.mfull .cfc.mid, .mfull .cfc', (e) => { const r = e.getBoundingClientRect(); return r.bottom; });
  const meta = await box('.mfull .cfmeta');
  assert.ok(meta.y >= cover - 1, 'the meta block now overlaps the centre cover');
  console.log('    shot: ' + await shoot(page, 'music-card'));
  await page.close();
});

(async () => {
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  let fail = 0;
  for (const [name, fn] of T) {
    try { await fn(browser, base); console.log('  PASS  ' + name); }
    catch (e) { fail++; console.log('  FAIL  ' + name + '\n        ' + e.message); }
  }
  await browser.close(); srv.close();
  console.log(fail ? '\n' + fail + ' failing' : '\nall ' + T.length + ' passing');
  process.exit(fail ? 1 : 0);
})();
