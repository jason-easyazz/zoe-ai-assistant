/*
 * Browser test for the today-anchored calendar (touch/home.html):
 *   1. WEEK shows exactly 4 day columns, today leftmost, and scrolls sideways,
 *   2. MONTH shows exactly 3 rolling week rows, today's week first, scrolls down,
 *   3. the past is reachable by scrolling BACK, not amputated at today,
 *   4. the header is honest about the days actually on screen,
 *   5. nothing collides with #dock / #orb / #home.
 *
 * WHY A REAL BROWSER
 * ------------------
 * Every claim here is a LAYOUT claim — "four columns fit", "today's row is not
 * hidden under the sticky weekday strip". A fake DOM sees none of that. It would
 * have passed the two defects this suite actually caught during development:
 *   - month rows sized with minmax(0,...) collapsed to 39px and the view did not
 *     scroll at all, and
 *   - scroll-snap dragged today's row back UNDER the sticky header, hiding every
 *     date number, while the row still measured ">=50% visible".
 * So this drives real headless Chromium at the panel's real resolution
 * (1280x720) and reads real bounding boxes.
 *
 * FIXTURE PROVENANCE — read this before changing any fixture below
 * ---------------------------------------------------------------
 * DERIVED FROM SOURCE, not from a live capture, and that is deliberate:
 *
 *   curl "localhost:8000/api/calendar/events?start_date=...&end_date=..."
 *
 * answers 200 {"events":[]} for an unauthenticated caller, so it CANNOT confirm
 * the field set — and inventing a shape from an empty response is precisely the
 * trap that shipped a Cover Flow passing 50 assertions while dead on the panel.
 * The shape below is therefore read off the code that builds it:
 *
 *   routers/calendar.py::list_events  ->  {"events": [ row_to_event(row), ... ]}
 *   calendar_utils.row_to_event       ->  dict(row), i.e. the raw `events`
 *                                         columns, with all_day/deleted coerced
 *                                         to bool and metadata JSON-parsed
 *   alembic/versions/0001_initial_schema.py -> the column list itself
 *
 * assertLiveContract() still re-fetches and, IF the endpoint ever returns a row,
 * asserts our key set matches it exactly. An empty or unreachable response SKIPS
 * LOUDLY — it never counts as agreement.
 *
 * Run:  node services/zoe-ui/dist/test_touch_calendar_anchor.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  CAL_SHOTS=<dir>
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
const SHOTS = process.env.CAL_SHOTS || '/tmp/calendar-anchor-shots';
const API = process.env.ZOE_API || 'http://127.0.0.1:8000';

// The panel's real geometry, and the numbers this change is justified by.
const VIEW = { width: 1280, height: 720 };
const EXPECT = {
  bodyW: 1208, bodyH: 476,   // #calBody content box at 1280x720
  weekCols: 4, weekColW: 294, weekColTol: 3,
  monthRows: 3, monthCellW: 167, monthCellH: 144, monthTol: 3,
  minTap: 48,                // finger target floor for an event chip
  monthChips: 4,             // chips per day cell before "+N more"
};

// ── fixture: one `events` row, shape derived from source (see header) ────────
const EVENT_KEYS = [
  'id', 'user_id', 'title', 'start_date', 'start_time', 'end_date', 'end_time',
  'duration', 'category', 'location', 'all_day', 'recurring', 'metadata',
  'visibility', 'deleted', 'created_at', 'updated_at',
];
function mkEvent(id, isoDate, hour, category, extra) {
  return Object.assign({
    id: String(id), user_id: 'jason', title: 'Event ' + id,
    start_date: isoDate, start_time: String(hour).padStart(2, '0') + ':00:00',
    end_date: null, end_time: null, duration: 60, category: category || 'general',
    location: '', all_day: false, recurring: null, metadata: null,
    visibility: 'family', deleted: false,
    created_at: '2026-07-01T00:00:00', updated_at: '2026-07-01T00:00:00',
  }, extra || {});
}

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
// If the API ever hands back a real row, our derived key set must match it
// exactly — in BOTH directions. Empty/unreachable SKIPS LOUDLY.
async function assertLiveContract() {
  const d = new Date(); const iso = (x) => x.toISOString().slice(0, 10);
  const a = new Date(d); a.setDate(d.getDate() - 7);
  const b = new Date(d); b.setDate(d.getDate() + 56);
  const r = await getJson(`${API}/api/calendar/events?start_date=${iso(a)}&end_date=${iso(b)}`);
  if (!r) { console.log(`  ~ live API unreachable at ${API} — contract check SKIPPED`); return; }
  if (!Array.isArray(r.events)) {
    assert.fail(`/api/calendar/events did not return an events array: ${JSON.stringify(r).slice(0, 200)}`);
  }
  if (!r.events.length) {
    console.log('  ~ live API returned 0 events (unauthenticated) — key set UNVERIFIED against');
    console.log('    the wire; it is derived from routers/calendar.py + 0001_initial_schema.py.');
    return;
  }
  const live = Object.keys(r.events[0]).sort();
  const ours = EVENT_KEYS.slice().sort();
  assert.deepStrictEqual(
    { invented: ours.filter((k) => !live.includes(k)), missed: live.filter((k) => !ours.includes(k)) },
    { invented: [], missed: [] },
    `event fixture keys drifted from the LIVE API\n  fixture=${ours.join(',')}\n  live   =${live.join(',')}`);
  console.log(`  ✓ event fixture matches the LIVE API key-for-key (${API})`);
}

// ── plumbing ────────────────────────────────────────────────────────────────
function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(DIST, '.' + path.sep + rel);
    // Anchor the containment check at a separator. A bare startsWith(DIST) also
    // accepts a SIBLING directory whose name merely begins with it — "dist2",
    // "dist-legacy" — because the prefix matches with no boundary.
    if ((file !== DIST && !file.startsWith(DIST + path.sep)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end('nope'); return;
    }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}

const isoOf = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

// Freeze the page's clock. Month-boundary and six-week-month behaviour is only
// reachable by controlling "today", and a calendar tested only on the day it was
// written is a calendar tested once.
function freezeClock(page, isoInstant) {
  return page.addInitScript((stamp) => {
    const Real = Date; const fixed = new Real(stamp).getTime();
    function F(...a) {
      if (!(this instanceof F)) return new Real(fixed).toString();
      return a.length ? new Real(...a) : new Real(fixed);
    }
    F.prototype = Real.prototype; F.now = () => fixed;
    F.parse = Real.parse; F.UTC = Real.UTC;
    // eslint-disable-next-line no-global-assign
    Date = F;
  }, isoInstant);
}

// Events spread across the whole scrollable window so every day cell has
// something to draw and the "+N more" path is exercised.
function eventsAround(nowIso) {
  const base = new Date(nowIso + 'T12:00:00');
  const out = [];
  for (let i = -10; i <= 60; i++) {
    const d = new Date(base); d.setDate(base.getDate() + i);
    const n = i % 5 === 0 ? 6 : (i % 3 === 0 ? 2 : 1);   // some days overflow
    for (let j = 0; j < n; j++) {
      out.push(mkEvent(`${i}_${j}`, isoOf(d), 8 + j, ['work', 'personal', 'health', 'family'][j % 4]));
    }
  }
  return out;
}

async function openCalendar(page, view) {
  // Drive the real launcher, the way a finger would.
  await page.click('#apps');
  await page.waitForTimeout(450);
  await page.click('.ltile[data-id="calendar"]');
  await page.waitForTimeout(900);
  if (view && view !== 'week') {
    await page.click(`.calviews button[data-v="${view}"]`);
    await page.waitForTimeout(900);
  }
}

function overlaps(a, b) {
  return !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
}

async function boot(browser, port, { nowIso, events }) {
  const page = await browser.newPage({ viewport: VIEW });
  await freezeClock(page, nowIso + 'T09:30:00');
  // Generic stub FIRST: Playwright matches routes in REVERSE registration
  // order, so the specific calendar route registered after it wins. (Getting
  // this backwards silently served {} to the calendar and every day rendered
  // empty while the layout assertions still passed.)
  await page.route('**/api/**', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  // The stub must be no more generous than the real endpoint. Returning every
  // event regardless of the query string made the fetch window untestable — a
  // deliberately narrowed fetch still passed, because the mock kept handing back
  // days the server would never have sent. So filter exactly as
  // routers/calendar.py does: start_date >= ? AND start_date <= ?, on
  // start_date ALONE (which is also why an event that merely OVERLAPS the
  // window, having started before it, is not returned — see the PR).
  await page.route('**/api/calendar/events**', (r) => {
    const q = new URL(r.request().url()).searchParams;
    const a = q.get('start_date'); const b = q.get('end_date');
    const hit = events.filter((e) => (!a || e.start_date >= a) && (!b || e.start_date <= b));
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: hit }) });
  });
  await page.goto(`http://127.0.0.1:${port}/touch/home.html`, { waitUntil: 'domcontentloaded' });
  // #authov swallows clicks; hide it rather than authenticating.
  await page.addStyleTag({ content: '#authov{display:none!important}' });
  await page.waitForTimeout(800);
  return page;
}

// ── the suite ───────────────────────────────────────────────────────────────
const results = [];
function ok(name) { results.push([true, name]); console.log('  ✓ ' + name); }
function bad(name, e) { results.push([false, name]); console.log('  ✗ ' + name + '\n      ' + (e && e.message ? e.message.split('\n')[0] : e)); }
async function t(name, fn) { try { await fn(); ok(name); } catch (e) { bad(name, e); } }

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  console.log('\ncalendar: today-anchored week + month\n');
  console.log('live contract');
  try { await assertLiveContract(); } catch (e) { bad('live contract', e); }

  const srv = await serve();
  const port = srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

  // ── A. week view, on a Sunday (worst case for the OLD Monday-start layout:
  //       six of seven columns were spent past) ────────────────────────────
  const NOW = '2026-07-19';   // a Sunday
  console.log('\nweek view (today = Sun 2026-07-19)');
  {
    const page = await boot(browser, port, { nowIso: NOW, events: eventsAround(NOW) });
    await openCalendar(page, 'week');

    await t('#calBody is the measured 1208x476 content box', async () => {
      const b = await page.evaluate(() => { const r = document.getElementById('calBody').getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; });
      assert.deepStrictEqual(b, { w: EXPECT.bodyW, h: EXPECT.bodyH });
    });

    await t(`exactly ${EXPECT.weekCols} day columns are visible`, async () => {
      const n = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calweek')).length);
      assert.strictEqual(n, EXPECT.weekCols);
    });

    await t(`each column is ~${EXPECT.weekColW}px (was 164px)`, async () => {
      const w = await page.evaluate(() => Math.round(document.querySelector('.cwc').getBoundingClientRect().width));
      assert.ok(Math.abs(w - EXPECT.weekColW) <= EXPECT.weekColTol, `column width ${w}px, expected ~${EXPECT.weekColW}px`);
    });

    await t('today is the LEFTMOST visible column', async () => {
      const r = await page.evaluate(() => {
        const vis = window._calTest.visibleDays(document.querySelector('.calweek'));
        const el = document.querySelector(`.cwc[data-d="${vis[0]}"]`);
        return { first: vis[0], today: el.classList.contains('today') };
      });
      assert.strictEqual(r.first, NOW, 'leftmost visible day');
      assert.ok(r.today, 'leftmost column carries .today');
    });

    await t('today is visually marked, not merely positioned', async () => {
      const badge = await page.evaluate(() => {
        const el = document.querySelector('.cwc.today h5');
        return getComputedStyle(el, '::after').content;
      });
      assert.ok(/TODAY/.test(badge), `expected a TODAY badge, got ${badge}`);
    });

    await t(`event chips clear the ${EXPECT.minTap}px finger target`, async () => {
      const r = await page.evaluate(() => {
        const el = document.querySelector('.cwe');
        return { h: Math.round(el.getBoundingClientRect().height), floor: parseFloat(getComputedStyle(el).minHeight) || 0 };
      });
      assert.ok(r.h >= EXPECT.minTap, `chip renders ${r.h}px, need >=${EXPECT.minTap}px`);
      // Assert the CSS floor too. A chip with a short title and no time line
      // sits right on the boundary, so the rendered height alone passes even
      // with the floor deleted — which is how an untested guard quietly rots.
      assert.ok(r.floor >= EXPECT.minTap, `min-height floor is ${r.floor}px, need >=${EXPECT.minTap}px`);
    });

    await t('scrolling RIGHT reveals later days WITH their events', async () => {
      const before = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calweek')));
      await page.evaluate(() => { const s = document.querySelector('.calweek'); s.scrollLeft += 4 * 304; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const after = await page.evaluate(() => {
        const sc = document.querySelector('.calweek');
        const vis = window._calTest.visibleDays(sc);
        return { vis, chips: vis.reduce((n, d) => n + document.querySelector(`.cwc[data-d="${d}"]`).querySelectorAll('.cwe').length, 0) };
      });
      assert.ok(after.vis[0] > before[before.length - 1], `expected later days, got ${after.vis[0]} after ${before[before.length - 1]}`);
      // The events matter, not just the columns. Scrolling used to be able to
      // reveal empty scaffolding — days rendered from the local window while the
      // fetch only covered the opening view. Narrowing the fetch must fail HERE.
      assert.ok(after.chips > 0, 'scrolled-to days rendered no events: the fetch window is too narrow');
    });

    await t('ONE NUDGE back from the opening position shows YESTERDAY', async () => {
      // The operator's requirement is "nudge back and yesterday is still
      // there" — not "scroll to the beginning", which lands on the far edge of
      // the history window instead.
      await page.click('.calviews button[data-v="week"]');   // re-render, re-anchor
      await page.waitForTimeout(900);
      const colStep = await page.evaluate(() => {
        const a = document.querySelectorAll('.cwc');
        return a[1].getBoundingClientRect().left - a[0].getBoundingClientRect().left;
      });
      await page.evaluate((step) => { const s = document.querySelector('.calweek'); s.scrollLeft -= step; s.dispatchEvent(new Event('scroll')); }, colStep);
      await page.waitForTimeout(250);
      const vis = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calweek')));
      const y = new Date(NOW + 'T12:00:00'); y.setDate(y.getDate() - 1);
      assert.strictEqual(vis[0], isoOf(y), `one nudge back should lead with yesterday; saw ${vis.join(',')}`);
      assert.ok(vis.includes(NOW), 'today should still be on screen after a single nudge');
    });

    await t('the history window bottoms out at CAL_BACK_DAYS, not at today', async () => {
      await page.evaluate(() => { const s = document.querySelector('.calweek'); s.scrollLeft = 0; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const vis = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calweek')));
      const back = await page.evaluate(() => window._calTest.K.back);
      const oldest = new Date(NOW + 'T12:00:00'); oldest.setDate(oldest.getDate() - back);
      assert.strictEqual(vis[0], isoOf(oldest), `expected to reach ${isoOf(oldest)}, saw ${vis[0]}`);
      assert.ok(vis[0] < NOW, 'the past must be reachable, not hard-stopped at today');
    });

    await t('the header follows what is on screen', async () => {
      await page.evaluate(() => { const s = document.querySelector('.calweek'); s.scrollLeft = 0; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const back = await page.evaluate(() => document.getElementById('calTtl').textContent);
      await page.evaluate(() => { const s = document.querySelector('.calweek'); s.scrollLeft += 20 * 304; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const fwd = await page.evaluate(() => document.getElementById('calTtl').textContent);
      assert.notStrictEqual(back, fwd, 'header did not change when the window scrolled');
      assert.ok(!/This week/.test(back + fwd), '"This week" cannot describe a rolling window');
    });

    await t('nothing collides with #dock / #orb / #home', async () => {
      const boxes = await page.evaluate(() => {
        const g = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { sel: s, x: r.x, y: r.y, width: r.width, height: r.height }; };
        return { cells: [...document.querySelectorAll('.cwc')].map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, width: r.width, height: r.height })),
          chrome: ['#dock', '#orb', '#home'].map(g).filter(Boolean) };
      });
      const vis = boxes.cells.filter((c) => c.x < VIEW.width && c.x + c.width > 0);
      for (const c of vis) {
        for (const ch of boxes.chrome) {
          assert.ok(!overlaps(c, ch), `a day cell overlaps ${ch.sel}`);
        }
      }
      assert.ok(boxes.chrome.length >= 2, 'expected to actually find the dock/orb chrome');
    });

    await page.evaluate(() => { const s = document.querySelector('.calweek'); s.scrollLeft = 0; s.dispatchEvent(new Event('scroll')); });
    await page.evaluate(() => { document.querySelector('.calviews button[data-v="week"]').click(); });
    await page.waitForTimeout(900);
    await page.screenshot({ path: path.join(SHOTS, 'week.png') });
    await page.close();
  }

  // ── B. month view ─────────────────────────────────────────────────────────
  console.log('\nmonth view (today = Sun 2026-07-19)');
  {
    const page = await boot(browser, port, { nowIso: NOW, events: eventsAround(NOW) });
    await openCalendar(page, 'month');

    await t(`exactly ${EXPECT.monthRows} week rows are visible`, async () => {
      const n = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calmonth')).length);
      assert.strictEqual(n, EXPECT.monthRows * 7, `${n / 7} rows visible`);
    });

    await t(`cells are ~${EXPECT.monthCellW}x${EXPECT.monthCellH} (was ~70px tall)`, async () => {
      const b = await page.evaluate(() => { const r = document.querySelector('.cmd').getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; });
      assert.ok(Math.abs(b.w - EXPECT.monthCellW) <= EXPECT.monthTol, `cell width ${b.w}`);
      assert.ok(Math.abs(b.h - EXPECT.monthCellH) <= EXPECT.monthTol, `cell height ${b.h}`);
    });

    await t('it opens on the CURRENT week, not the 1st of the month', async () => {
      const vis = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calmonth')));
      assert.ok(vis.includes(NOW), `today ${NOW} is not in the opening rows (${vis[0]}..${vis[vis.length - 1]})`);
      const monday = await page.evaluate((n) => window._calTest.iso(window._calTest.monday(window._calTest.fromIso(n))), NOW);
      assert.strictEqual(vis[0], monday, 'first visible row is not the week containing today');
    });

    await t("today's row is NOT hidden under the sticky weekday strip", async () => {
      const r = await page.evaluate(() => {
        const c = document.querySelector('.cmd.today').getBoundingClientRect();
        const h = document.querySelector('.cmh').getBoundingClientRect();
        return { cellTop: c.top, headerBottom: h.bottom };
      });
      assert.ok(r.cellTop >= r.headerBottom - 1, `today's row top ${r.cellTop} is above the strip bottom ${r.headerBottom}`);
    });

    await t('the weekday strip stays put while the weeks scroll', async () => {
      const before = await page.evaluate(() => document.querySelector('.cmh').getBoundingClientRect().top);
      await page.evaluate(() => { const s = document.querySelector('.calmonth'); s.scrollTop += 300; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const after = await page.evaluate(() => document.querySelector('.cmh').getBoundingClientRect().top);
      assert.ok(Math.abs(after - before) <= 1, `weekday strip moved ${Math.abs(after - before)}px`);
    });

    await t('scrolling DOWN reveals later weeks; scrolling back reaches the past', async () => {
      await page.evaluate(() => { const s = document.querySelector('.calmonth'); s.scrollTop += 600; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const fwd = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calmonth')));
      assert.ok(fwd[0] > NOW, `expected later weeks, first visible ${fwd[0]}`);
      await page.evaluate(() => { const s = document.querySelector('.calmonth'); s.scrollTop = 0; s.dispatchEvent(new Event('scroll')); });
      await page.waitForTimeout(250);
      const back = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calmonth')));
      assert.ok(back[0] < NOW, `expected reachable past, first visible ${back[0]}`);
    });

    await t(`a day shows ${EXPECT.monthChips} chips before "+N more"`, async () => {
      // Read the CONSTANT the panel actually runs on and the cells it actually
      // rendered. An earlier cut of this test passed the limit in as a
      // parameter, so dropping CAL_MONTH_CHIPS back to the old squeezed 2 left
      // it happily green — the exact "test exercises a copy, not production"
      // failure this suite is supposed to prevent.
      const r = await page.evaluate(() => {
        const cells = [...document.querySelectorAll('.cmd')];
        const over = cells.find((c) => /\+\d+ more/.test(c.textContent));
        return {
          K: window._calTest.K.chips,
          rowsInOverflowCell: over ? over.querySelectorAll('.cev').length : null,
          maxRowsAnyCell: Math.max(...cells.map((c) => c.querySelectorAll('.cev').length)),
          counterLast: over ? /more$/.test(over.textContent.trim()) : false,
        };
      });
      assert.strictEqual(r.K, EXPECT.monthChips, 'CAL_MONTH_CHIPS is not the expected limit');
      assert.strictEqual(r.rowsInOverflowCell, EXPECT.monthChips, 'an overflowing day should fill exactly the limit');
      assert.strictEqual(r.maxRowsAnyCell, EXPECT.monthChips, 'no cell may exceed the limit');
      assert.ok(r.counterLast, 'the "+N more" counter should take the LAST slot');
    });

    await t('the header names the visible span, not a calendar month', async () => {
      const txt = await page.evaluate(() => document.getElementById('calTtl').textContent);
      assert.ok(!/^July 2026$/.test(txt), 'header still claims a whole calendar month');
      assert.ok(/\d/.test(txt), `header looks empty: "${txt}"`);
    });

    await t('nothing collides with #dock / #orb / #home', async () => {
      const boxes = await page.evaluate(() => {
        const g = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { sel: s, x: r.x, y: r.y, width: r.width, height: r.height }; };
        const sc = document.querySelector('.calmonth').getBoundingClientRect();
        return { sc: { x: sc.x, y: sc.y, width: sc.width, height: sc.height }, chrome: ['#dock', '#orb', '#home'].map(g).filter(Boolean) };
      });
      for (const ch of boxes.chrome) assert.ok(!overlaps(boxes.sc, ch), `the month grid overlaps ${ch.sel}`);
    });

    await page.evaluate(() => { document.querySelector('.calviews button[data-v="month"]').click(); });
    await page.waitForTimeout(900);
    await page.screenshot({ path: path.join(SHOTS, 'month.png') });
    await page.close();
  }

  // ── C. the cases the OLD layout broke on ─────────────────────────────────
  // 2026-08-31 is a Monday whose calendar month (August) spans six Mon..Sun
  // rows — the shape that forced maxChips down to 1 — and whose rolling window
  // immediately crosses into September.
  console.log('\nmonth boundary + six-week month (today = Mon 2026-08-31)');
  {
    const EDGE = '2026-08-31';
    const page = await boot(browser, port, { nowIso: EDGE, events: eventsAround(EDGE) });
    await openCalendar(page, 'month');

    await t('rows stay uniform across a month boundary (no six-week squeeze)', async () => {
      const hs = await page.evaluate(() => [...document.querySelectorAll('.cmd')].map((e) => Math.round(e.getBoundingClientRect().height)));
      assert.strictEqual(new Set(hs).size, 1, `cell heights are not uniform: ${[...new Set(hs)].join(',')}`);
      assert.ok(Math.abs(hs[0] - EXPECT.monthCellH) <= EXPECT.monthTol, `cell height ${hs[0]}`);
    });

    await t('still exactly 3 visible rows, opening on today', async () => {
      const vis = await page.evaluate(() => window._calTest.visibleDays(document.querySelector('.calmonth')));
      assert.strictEqual(vis.length, EXPECT.monthRows * 7);
      assert.ok(vis.includes(EDGE), `today ${EDGE} missing from ${vis[0]}..${vis[vis.length - 1]}`);
    });

    await t('the header spans both months honestly', async () => {
      const txt = await page.evaluate(() => document.getElementById('calTtl').textContent);
      assert.ok(/Aug/.test(txt) && /Sep/.test(txt), `expected both months in "${txt}"`);
    });

    await t('the window still crosses a YEAR boundary correctly', async () => {
      const txt = await page.evaluate(() => {
        const T = window._calTest;
        return T.headerText('month', T.fromIso('2026-12-28'), T.fromIso('2027-01-17'), new Date());
      });
      assert.ok(/2026/.test(txt) && /2027/.test(txt), `year-crossing header lost a year: "${txt}"`);
    });

    await page.screenshot({ path: path.join(SHOTS, 'month-boundary.png') });
    await page.close();
  }

  // ── D. preserved behaviour ───────────────────────────────────────────────
  console.log('\npreserved');
  {
    const page = await boot(browser, port, { nowIso: NOW, events: eventsAround(NOW) });
    await openCalendar(page, 'day');

    await t('day view still lists today, unchanged', async () => {
      const n = await page.evaluate(() => document.querySelectorAll('.caldaylist .dev').length);
      assert.ok(n > 0, 'day view drew nothing');
      const wk = await page.evaluate(() => !!document.querySelector('.calweek,.calmonth'));
      assert.ok(!wk, 'day view should not render a scroller');
    });

    await t('day view header is still the long weekday form', async () => {
      const txt = await page.evaluate(() => document.getElementById('calTtl').textContent);
      assert.ok(/Sunday/.test(txt), `expected the weekday form, got "${txt}"`);
    });

    await t('tapping an event still opens the editor', async () => {
      await page.click('.calviews button[data-v="week"]');
      await page.waitForTimeout(900);
      await page.click('.cwc.today .cwe');
      await page.waitForTimeout(500);
      const open = await page.evaluate(() => !!document.querySelector('#estModal'));
      assert.ok(open, 'event tap did not open the edit modal');
    });

    await page.close();
  }

  await browser.close(); srv.close();

  const failed = results.filter(([p]) => !p);
  console.log(`\n${results.length - failed.length}/${results.length} passed   shots: ${SHOTS}\n`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
