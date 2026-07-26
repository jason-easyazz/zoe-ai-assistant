#!/usr/bin/env node
// Cover Flow motion harness — black box.
//
// Everything here drives REAL synthetic pointer events at the real page and
// reads back COMPUTED transforms. Nothing reaches into the page's closure and
// nothing calls an exported pure function: the point is to prove the thing the
// panel actually renders, not that some arithmetic is self-consistent.
//
// The API is mocked, but the fixture is asserted against the live endpoint by
// tests/e2e/music/test_coverflow_motion.py — a mock that invents its contract
// proves nothing (that is exactly how a previous carousel PR passed 50
// assertions on a feature that was dead on the panel).
import { chromium } from '/home/zoe/.openclaw/npm/node_modules/playwright-core/index.mjs';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Serve THIS checkout's docroot, resolved relative to this file. An absolute
// path to the live checkout would silently test whatever is deployed on main
// instead of the branch under test — which is exactly what it did once.
const ROOT = path.resolve(HERE, '..', '..', '..', 'services', 'zoe-ui', 'dist');
const FIXTURE = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'music_api.json'), 'utf8'));
const CF_K = 0.867;
const CF_SLOT = 238 * CF_K;          // px of finger travel per queue position
const EPS = 1e-6;

let failures = [], checks = 0;
const ok = (cond, msg) => { checks++; if (!cond) { failures.push(msg); console.log(`  ✗ ${msg}`); } else console.log(`  ✓ ${msg}`); };
const near = (a, b, tol, msg) => ok(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ${b}±${tol})`);

// The geometry as it shipped: discrete, integer-only. The continuous version
// must reproduce this EXACTLY at every integer slot — that's the approved look.
function OLD(d) {
  const ax = Math.min(Math.abs(d), 4), dir = Math.sign(d);
  if (d === 0) return { x: 0, z: 140 * CF_K, ry: 0, sc: 1.12, op: 1 };
  return {
    x: dir * (238 + (ax - 1) * 96) * CF_K,
    z: (-80 - (ax - 1) * 55) * CF_K,
    ry: dir * -54,
    sc: 1 - ax * 0.05,
    op: ax >= 4 ? 0 : 1 - (ax - 1) * 0.14,
  };
}

// ---- the calls the page must NOT make while you are only browsing ----------
const PLAYBACK_RE = /\/api\/music\/(control|queue\/play-index|queue\/move|queue\/remove)/;

// Distinct, legible covers. Without real art the fan renders as empty outlines
// and a screenshot can't tell a correct layout from a broken one.
const ART = ['#c77bff', '#5ac8fa', '#ff9f0a', '#30d158', '#ff375f', '#64d2ff', '#bf5af2',
  '#ffd60a', '#0a84ff', '#ff6482', '#32d74b', '#5e5ce6', '#ff9500', '#66d4cf'];
function artSvg(n) {
  const c = ART[n % ART.length];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">`
    + `<rect width="300" height="300" fill="${c}"/>`
    + `<text x="150" y="196" font-family="sans-serif" font-size="150" font-weight="700"`
    + ` fill="rgba(0,0,0,.55)" text-anchor="middle">${n}</text></svg>`;
}

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const url = new URL(req.url, 'http://x');
      const p = url.pathname;
      const port = srv.address().port;
      // The fixture is a static file, so the art host is patched in at serve
      // time — the panel only accepts an absolute http(s) url here.
      const withArt = (o) => JSON.parse(
        JSON.stringify(o).split('http://127.0.0.1/art/').join(`http://127.0.0.1:${port}/art/`));
      const json = (o) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(withArt(o))); };
      const m = /^\/art\/(\d+)\.png$/.exec(p);
      if (m) { res.writeHead(200, { 'content-type': 'image/svg+xml' }); return res.end(artSvg(+m[1])); }
      if (p === '/api/music/now-playing') return json(FIXTURE.now_playing_response);
      if (p.startsWith('/api/music/queue/')) return json(FIXTURE.queue_response);
      if (p.startsWith('/api/')) return json({ ok: true, available: false, items: [] });
      const f = path.join(ROOT, p === '/' ? 'touch/home.html' : p);
      if (!f.startsWith(ROOT) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end('no'); }
      const ext = path.extname(f);
      const ct = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' }[ext] || 'application/octet-stream';
      res.writeHead(200, { 'content-type': ct });
      res.end(fs.readFileSync(f));
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

// Read every rendered cover's geometry straight out of the DOM.
const readCards = (page) => page.evaluate(() => {
  const out = [];
  for (const c of document.querySelectorAll('#mCFT .cfc')) {
    const cs = getComputedStyle(c);
    const m = new DOMMatrix(cs.transform);
    // decompose: scale from the length of the x basis vector, ry from m13/m11
    const sc = Math.hypot(m.m11, m.m12, m.m13);
    out.push({
      i: +c.getAttribute('data-i'),
      x: m.m41, z: m.m43,
      ry: Math.round(Math.asin(Math.max(-1, Math.min(1, -m.m13 / (sc || 1)))) * 180 / Math.PI * 1000) / 1000,
      sc: Math.round(sc * 1e5) / 1e5,
      op: +cs.opacity,
      on: c.classList.contains('on'),
      z1: +cs.zIndex,
    });
  }
  return out.sort((a, b) => a.i - b.i);
});

const focusOf = (page) => page.evaluate(() => {
  const on = document.querySelector('#mCFT .cfc.on');
  return on ? +on.getAttribute('data-i') : null;
});

async function boot(page, base) {
  await page.goto(`${base}/touch/home.html?kiosk=1`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => {
    localStorage.setItem('zoe_session', JSON.stringify({ session_id: 'test-harness' }));
    localStorage.setItem('zoe_kiosk', '1');
  });
  await page.goto(`${base}/touch/home.html?kiosk=1`, { waitUntil: 'networkidle' });
  // The music card's only entry point is the dock now-playing chip, and that
  // only paints while something is playing — hence the fixture's state:'playing'.
  await page.waitForSelector('.pc.dnp .dnm', { timeout: 10000 });
  await page.click('.pc.dnp .dnm');
  await page.waitForSelector('#mCFT .cfc', { timeout: 8000 });
  await page.waitForTimeout(900);   // let the first layout + any settle finish
}

// A drag made of real driver-level pointer events.
async function drag(page, { from, dxTotal, steps = 12, stepMs = 16, hold = 0, release = true }) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  if (hold) await page.waitForTimeout(hold);
  for (let s = 1; s <= steps; s++) {
    await page.mouse.move(from.x + (dxTotal * s) / steps, from.y);
    if (stepMs) await page.waitForTimeout(stepMs);
  }
  if (release) await page.mouse.up();
}

// Velocity-controlled gesture, dispatched INSIDE the page.
//
// Why not page.mouse: every driver-level move is a CDP round trip (~20-50ms on
// this box), so the fastest flick it can produce is slower than a lazy human
// one, and the release velocity — the exact thing under test — is a property of
// the harness rather than the gesture. These are real PointerEvents on the real
// card with real performance.now() timestamps; only the cadence is ours.
async function pgesture(page, { dxCards, steps, stepMs }) {
  return page.evaluate(async ({ dxCards, steps, stepMs, SLOT }) => {
    const cf = document.getElementById('mCF');
    const r = cf.getBoundingClientRect();
    const x0 = r.left + r.width / 2, y = r.top + r.height / 2;
    const mk = (type, x) => new PointerEvent(type, {
      pointerId: 1, isPrimary: true, bubbles: true, cancelable: true,
      clientX: x, clientY: y, pointerType: 'touch',
    });
    const dx = dxCards * SLOT;
    const nap = (ms) => new Promise((r2) => setTimeout(r2, ms));
    // pointerdown must land on the CARD — that handler keys off
    // e.target.closest('.cfc'). Everything after goes to the CONTAINER: arming
    // browse re-windows the flow, which detaches the card the gesture started
    // on, and events fired at a detached node never reach the listener. (Real
    // input doesn't care — the container holds the pointer capture.)
    document.elementFromPoint(x0, y).dispatchEvent(mk('pointerdown', x0));
    for (let s = 1; s <= steps; s++) { await nap(stepMs); cf.dispatchEvent(mk('pointermove', x0 + (dx * s) / steps)); }
    cf.dispatchEvent(mk('pointerup', x0 + dx));
    await new Promise((r2) => requestAnimationFrame(() => r2()));
  }, { dxCards, steps, stepMs, SLOT: CF_SLOT });
}

// Aim, pause, then flick — how people actually throw things. The pause fires no
// pointermove at all (a still finger generates none), so a velocity window that
// keeps a pre-pause sample as its baseline measures the flick across the whole
// hesitation and reports a stop.
async function aimPauseFlick(page, { aimCards, pauseMs, flickPx, flickSteps = 4, flickStepMs = 3, holdAfterMs = 0 }) {
  return page.evaluate(async (a) => {
    const cf = document.getElementById('mCF');
    const r = cf.getBoundingClientRect();
    const x0 = r.left + r.width / 2, y = r.top + r.height / 2;
    const mk = (type, x) => new PointerEvent(type, {
      pointerId: 3, isPrimary: true, bubbles: true, cancelable: true,
      clientX: x, clientY: y, pointerType: 'touch',
    });
    const nap = (ms) => new Promise((r2) => setTimeout(r2, ms));
    const aim = -a.aimCards * a.SLOT;
    document.elementFromPoint(x0, y).dispatchEvent(mk('pointerdown', x0));
    for (let s = 1; s <= 6; s++) { await nap(25); cf.dispatchEvent(mk('pointermove', x0 + (aim * s) / 6)); }
    await nap(a.pauseMs);                        // hold still: no pointermove fires
    for (let s = 1; s <= a.flickSteps; s++) {
      await nap(a.flickStepMs);
      cf.dispatchEvent(mk('pointermove', x0 + aim - (a.flickPx * s) / a.flickSteps));
    }
    if (a.holdAfterMs) await nap(a.holdAfterMs); // rest the finger, then lift
    cf.dispatchEvent(mk('pointerup', x0 + aim - a.flickPx));
    await new Promise((r2) => requestAnimationFrame(() => r2()));
  }, { aimCards, pauseMs, flickPx, flickSteps, flickStepMs, holdAfterMs, SLOT: CF_SLOT });
}

async function main() {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({
    executablePath: '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome',
    // vsync off: headless otherwise pins rAF to ~30fps (33.3ms) regardless of
    // how cheap the frame is — the OLD code measured 33.3ms too. With the clock
    // ungated, a frame interval is the actual cost of producing that frame,
    // which is the number that says whether 16.7ms is affordable.
    args: ['--no-sandbox', '--enable-gpu-rasterization', '--ignore-gpu-blocklist',
      '--disable-gpu-vsync', '--disable-frame-rate-limit'],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });

  const calls = [];
  page.on('request', (r) => { if (PLAYBACK_RE.test(r.url())) calls.push(`${r.method()} ${new URL(r.url()).pathname}`); });

  await boot(page, base);
  const box = await page.locator('#mCF').boundingBox();
  const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };

  // ---------------------------------------------------------------- 1. rest
  console.log('\n[1] rest geometry == the approved discrete look at every integer slot');
  {
    const cards = await readCards(page);
    const f = await focusOf(page);
    ok(f !== null, 'a cover is focused at rest');
    let compared = 0;
    for (const c of cards) {
      const d = c.i - f;
      if (Math.abs(d) > 4) continue;
      const w = OLD(d);
      // epsilon, not equality: sc(2) lands on 0.8999999999999999 in float. The
      // float is harmless in a transform — only the assertion needs tolerance.
      near(c.x, w.x, 0.6, `d=${d} x`);
      near(c.z, w.z, 0.6, `d=${d} z`);
      near(c.ry, w.ry, 0.15, `d=${d} rotateY`);
      near(c.sc, w.sc, 2e-3, `d=${d} scale`);
      near(c.op, w.op, 5e-3, `d=${d} opacity`);
      compared++;
    }
    ok(compared >= 5, `compared ${compared} slots against the old geometry (want >=5)`);
  }

  // -------------------------------------------------- 2. continuity (the point)
  console.log('\n[2] a half-card drag puts covers at genuinely intermediate transforms');
  {
    const f0 = await focusOf(page);
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down();
    // half a card to the LEFT = focus + 0.5
    for (let s = 1; s <= 8; s++) { await page.mouse.move(centre.x - (CF_SLOT / 2) * s / 8, centre.y); await page.waitForTimeout(16); }
    await page.waitForTimeout(40);
    const mid = await readCards(page);
    await page.screenshot({ path: path.join(HERE, 'coverflow-middrag.png') });
    let strictly = 0;
    for (const c of mid) {
      const d = c.i - (f0 + 0.5);
      if (Math.abs(d) > 3.4) continue;
      const lo = OLD(Math.floor(d)), hi = OLD(Math.ceil(d));
      const between = (v, a, b, tol) => v > Math.min(a, b) - tol && v < Math.max(a, b) + tol
        && Math.abs(v - a) > tol && Math.abs(v - b) > tol;
      // the .5 card must sit strictly BETWEEN its two integer slots — a test
      // that only checked integer rest states would pass on the OLD code.
      if (between(c.x, lo.x, hi.x, 1.5) && between(c.sc, lo.sc, hi.sc, 5e-3)) strictly++;
    }
    ok(strictly >= 2, `${strictly} covers sit strictly between their integer slots (want >=2)`);
    const onCount = mid.filter((c) => c.on).length;
    ok(onCount <= 1, `at most one .on cover mid-drag (got ${onCount})`);
    await page.mouse.up();
    await page.waitForTimeout(700);
  }

  // ------------------------------------------------------- 3. browse != play
  console.log('\n[3] browsing never triggers playback');
  {
    calls.length = 0;
    await drag(page, { from: centre, dxTotal: -CF_SLOT * 1.2, steps: 10, stepMs: 16 });
    await page.waitForTimeout(900);
    ok(calls.length === 0, `no playback/mutation call during browse (saw: ${JSON.stringify(calls)})`);
  }

  // ------------------------------------------------------ 4. flick vs slow drag
  console.log('\n[4] a flick travels further than a slow drag of the SAME distance');
  let slowTravel, flickTravel;
  {
    // identical distance (0.6 of a cover), different speed — so the only thing
    // that can separate them is the release velocity.
    const a = await focusOf(page);
    await pgesture(page, { dxCards: -0.6, steps: 12, stepMs: 40 });   // ~480ms: a slow drag
    await page.waitForTimeout(900);
    slowTravel = (await focusOf(page)) - a;
    const b = await focusOf(page);
    await pgesture(page, { dxCards: -0.6, steps: 5, stepMs: 3 });     // ~15ms: a flick
    await page.waitForTimeout(900);
    flickTravel = (await focusOf(page)) - b;
    ok(slowTravel === 1, `a slow 0.6-cover drag snaps to the nearest (travelled ${slowTravel})`);
    ok(flickTravel > slowTravel, `flick travelled ${flickTravel} vs slow ${slowTravel} over the same distance`);
  }

  // ------------------------------------------------------------ 5. rubber band
  // ------------------------------------------------- 4b. aim, pause, flick
  console.log('\n[4b] aim → pause → flick, and its mirror');
  {
    // Aim, hesitate, THEN throw — how people actually throw things. These are
    // REGRESSION GUARDS, not proof of a fix: both already passed before the
    // strict-pruning change, because a flick of 2+ pointermoves evicts the
    // stale baseline on its second move. The case that does NOT self-heal is a
    // flick short enough to fire a single pointermove; see the PR for why that
    // one can't be fixed from the coalesced event stream.
    const a = await focusOf(page);
    await aimPauseFlick(page, { aimCards: 0.45, pauseMs: 150, flickPx: 40, flickSteps: 4, flickStepMs: 3 });
    await page.waitForTimeout(1000);
    const travel = (await focusOf(page)) - a;
    ok(travel > 1, `a 40px flick after a 150ms pause still flings (travelled ${travel}; a snap would be 1)`);

    // The mirror of the same mistake: flick, then rest your finger, then lift.
    // The flick is over, so it is history rather than a throw — this must settle
    // where it is. It used to pass only by accident (the long gap diluted the
    // velocity below the threshold); cfRelease now says so explicitly.
    const b = await focusOf(page);
    await aimPauseFlick(page, { aimCards: 0.45, pauseMs: 20, flickPx: 40, flickSteps: 4, flickStepMs: 3, holdAfterMs: 260 });
    await page.waitForTimeout(1000);
    const held = (await focusOf(page)) - b;
    ok(held <= 1, `flick → rest 260ms → lift does NOT fling (travelled ${held}, want <=1)`);
  }

  console.log('\n[5] the ends resist, and spring back');
  {
    // walk to the very front of the queue
    for (let n = 0; n < 12; n++) { await drag(page, { from: centre, dxTotal: CF_SLOT * 0.9, steps: 4, stepMs: 6 }); await page.waitForTimeout(260); }
    await page.waitForTimeout(1000);
    const atEnd = await focusOf(page);
    ok(atEnd === 0, `reached the first cover (focus=${atEnd})`);
    // now pull a FULL card further past the end and hold
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down();
    for (let s = 1; s <= 8; s++) { await page.mouse.move(centre.x + CF_SLOT * s / 8, centre.y); await page.waitForTimeout(16); }
    await page.waitForTimeout(40);
    const held = await readCards(page);
    const first = held.find((c) => c.i === 0);
    // unresisted this would be a full slot (238*CF_K ≈ 206px). Rubber-banded it
    // must be a fraction of that — visibly resisting, not running off.
    ok(first && first.x > 8 && first.x < 238 * CF_K * 0.75,
      `overshoot resisted: first cover x=${first ? first.x.toFixed(1) : '?'}px, want 8 < x < ${(238 * CF_K * 0.75).toFixed(0)}`);
    await page.mouse.up();
    await page.waitForTimeout(900);
    const back = await readCards(page);
    const f2 = back.find((c) => c.i === 0);
    near(f2.x, 0, 1.0, 'springs back to rest at the end');
  }

  // -------------------------------------------------- 6. catch a moving flow
  console.log('\n[6] grabbing mid-settle catches it (does not queue behind it)');
  {
    const drift = (a, b) => a.reduce((acc, c) => { const o = b.find((q) => q.i === c.i); return acc + (o ? Math.abs(o.x - c.x) : 0); }, 0);
    calls.length = 0;
    // (a) establish the control: a fling really is still moving at +80ms. Without
    // this the freeze assertion below passes on a flow that never moved at all.
    await pgesture(page, { dxCards: -1.6, steps: 5, stepMs: 3 });
    await page.waitForTimeout(80);
    const m1 = await readCards(page);
    await page.waitForTimeout(120);
    const m2 = await readCards(page);
    const flying = drift(m1, m2);
    ok(flying > 5, `a settle really is in flight at +80ms (${flying.toFixed(1)}px of travel in 120ms)`);
    await page.waitForTimeout(900);

    // (b) same fling, but grab it mid-flight — it must stop dead under the finger
    await pgesture(page, { dxCards: -1.6, steps: 5, stepMs: 3 });
    await page.waitForTimeout(80);
    await page.evaluate(() => {
      const cf = document.getElementById('mCF');
      const r = cf.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      document.elementFromPoint(x, y).dispatchEvent(new PointerEvent('pointerdown', {
        pointerId: 2, isPrimary: true, bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: 'touch',
      }));
    });
    await page.waitForTimeout(30);
    const s1 = await readCards(page);
    await page.waitForTimeout(140);
    const s2 = await readCards(page);
    const caught = drift(s1, s2);
    ok(caught < 1.5, `flow froze under the finger (drift ${caught.toFixed(2)}px over 140ms, vs ${flying.toFixed(1)}px uncaught)`);
    // Release with a nudge, not on the spot: a still press released inside 600ms
    // is a TAP, and a tap plays. (The trace caught this doing exactly that.)
    await page.evaluate(() => {
      const cf = document.getElementById('mCF');
      const r = cf.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      for (const [type, dx] of [['pointermove', 30], ['pointerup', 30]]) {
        cf.dispatchEvent(new PointerEvent(type, {
          pointerId: 2, isPrimary: true, bubbles: true, cancelable: true,
          clientX: x + dx, clientY: y, pointerType: 'touch',
        }));
      }
    });
    await page.waitForTimeout(900);
    ok(!calls.some((c) => /play-index|control/.test(c)),
      `catching the flow never triggered playback (saw: ${JSON.stringify(calls)})`);
  }

  // ------------------------------------------------------------ 7. long-press
  console.log('\n[7] long-press still lifts a cover for reorder');
  {
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down();
    await page.waitForTimeout(480);
    const lifted = await page.evaluate(() => !!document.querySelector('#mCFT .cfc.lift'));
    ok(lifted, 'a cover gained .lift after a still long-press');
    await page.mouse.move(centre.x + 10, centre.y);
    await page.mouse.up();
    await page.waitForTimeout(600);
  }

  // ------------------------------------------------------------ 8. flick up
  console.log('\n[8] flick up still removes');
  {
    calls.length = 0;
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down();
    for (let s = 1; s <= 5; s++) { await page.mouse.move(centre.x, centre.y - 22 * s); await page.waitForTimeout(8); }
    await page.mouse.up();
    await page.waitForTimeout(400);
    const toasted = await page.evaluate(() => /removed/i.test(document.body.innerText));
    ok(toasted, 'flick-up produced the Removed affordance');
  }

  // ------------------------------------------------------- 9. frame timing
  console.log('\n[9] frame timing during a scripted drag');
  let worst = 0, p95 = 0, n = 0;
  {
    await page.evaluate(() => {
      window.__f = []; let last = performance.now();
      const tick = (t) => { window.__f.push(t - last); last = t; requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    });
    await page.waitForTimeout(120);
    await page.evaluate(() => { window.__f.length = 0; });
    // a long, dense drag: one pointermove per frame for ~1.3s
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down();
    for (let s = 0; s < 80; s++) { await page.mouse.move(centre.x - Math.sin(s / 12) * CF_SLOT * 1.5, centre.y); await page.waitForTimeout(16); }
    await page.mouse.up();
    await page.waitForTimeout(700);
    const fr = (await page.evaluate(() => window.__f)).filter((x) => x > 0.01);
    fr.sort((a, b) => a - b);
    n = fr.length;
    worst = fr[fr.length - 1] || 0;
    p95 = fr[Math.floor(fr.length * 0.95)] || 0;
    const med = fr[Math.floor(fr.length / 2)] || 0;
    console.log(`  frames=${n} median=${med.toFixed(2)}ms p95=${p95.toFixed(2)}ms worst=${worst.toFixed(2)}ms`);
    // With vsync off this is the cost of a frame, not the interval between two.
    // 16.7ms is the 60fps budget; a worst frame under it means the flow has
    // headroom on hardware strictly faster than this measurement.
    ok(p95 < 16.7, `p95 frame inside the 60fps budget (16.7ms) — got ${p95.toFixed(2)}ms`);
    ok(worst < 33, `worst frame never drops below 30fps (33ms) — got ${worst.toFixed(2)}ms`);
  }

  await page.screenshot({ path: path.join(HERE, 'coverflow-rest.png') });
  await browser.close(); srv.close();

  console.log(`\n${checks - failures.length}/${checks} checks passed`);
  console.log(`FRAME_REPORT worst=${worst.toFixed(1)}ms p95=${p95.toFixed(1)}ms samples=${n}`);
  if (failures.length) { console.log('\nFAILURES:'); failures.forEach((f) => console.log(' - ' + f)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(2); });
