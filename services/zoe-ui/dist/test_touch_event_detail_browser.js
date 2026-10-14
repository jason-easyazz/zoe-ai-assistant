/*
 * Browser gate for the calendar EVENT SHEET (touch/home.html), real headless Chromium at the
 * panel's 1280x720. LOCAL gate, not CI (no JS browser lane) — the no-browser logic suite is
 * test_touch_event_detail.js (CI). This one proves what only a layout engine can:
 *   - tapping an event on the real calendar opens a NEAR-FULL-SCREEN sheet that fits the panel,
 *   - no <select>/<input>/<textarea> exists in the sheet (detail, edit, delete-confirm),
 *   - every chip's COMPUTED text/background meet WCAG AA, every control is >= 48px,
 *   - tapping a chip + Save sends ONLY the category (captured off the wire),
 *   - close by X, tap-outside, swipe-down (synthetic touch), and the spoken word "close",
 *   - delete asks first ("Keep it" returns to the sheet), read-only events hide edit/delete.
 *
 * Run:  node services/zoe-ui/dist/test_touch_event_detail_browser.js
 * Env:  PLAYWRIGHT_CORE=<dir> CHROME_PATH=<binary> EVD_SHOTS=<dir for PNGs>
 * Fixtures are INVENTED (public repo); shape per routers/calendar.py::list_events.
 * Run it under `flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10` — it starts Chromium.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PW = [process.env.PLAYWRIGHT_CORE, 'playwright-core', 'playwright', '/home/zoe/.openclaw/npm/node_modules/playwright-core'].filter(Boolean);
function loadChromium() { for (const c of PW) { try { return require(c).chromium; } catch (_) { /* next */ } } return null; }
const chromium = loadChromium();
if (!chromium) { console.error('playwright-core not found. Set PLAYWRIGHT_CORE.'); process.exit(2); }
function findChrome() {
  const e = process.env.CHROME_PATH || process.env.PLAYWRIGHT_CHROMIUM;
  if (e && fs.existsSync(e)) return e;
  try { const p = chromium.executablePath(); if (p && fs.existsSync(p)) return p; } catch (_) { /* ignore */ }
  return ['/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome'].find((p) => fs.existsSync(p)) || null;
}
const CHROME = findChrome();
if (!CHROME) { console.error('No Chromium binary found. Set CHROME_PATH.'); process.exit(2); }

const DIST = __dirname;
const SHOTS = process.env.EVD_SHOTS || '/tmp/event-detail-shots';
const VIEW = { width: 1280, height: 720 };

const base = (o) => Object.assign({ user_id: 'u1', end_date: null, end_time: null, duration: 60, location: null, all_day: false, recurring: null,
  metadata: null, visibility: 'family', deleted: false, created_at: '2026-10-01T00:00:00', updated_at: '2026-10-01T00:00:00' }, o);
const EVENTS = [
  base({ id: 'ev-min', title: 'Dentist', start_date: '2026-10-14', start_time: '09:00:00', duration: 45, category: 'health' }),
  base({ id: 'ev-full', title: 'Sam’s school concert', start_date: '2026-10-15', start_time: '18:30:00', end_time: '20:00:00', category: 'family',
    location: 'Example Primary School hall', recurring: 'weekly',
    metadata: { description: 'Bring the blue folder.\nDoors open at 6.', attendees: [{ person_id: 'p1', name: 'Sam', role: 'participant' }, { person_id: 'p2', name: 'Riley', role: 'participant' }],
      reminders: [{ offset_minutes: 30, message: 'leave soon' }] } }),
  base({ id: 'ev-sync', title: 'Quarterly planning', start_date: '2026-10-16', start_time: '10:00:00', end_time: '11:30:00', category: 'work',
    location: 'Room 4, Example Tower', status: 'tentative', source: 'google', timezone: 'Europe/London', read_only: true,
    meeting_url: 'https://meet.example.com/abc-defg-hij', description: 'Agenda in the shared doc.', organizer: { name: 'Alex Example' },
    attendees: [{ name: 'Alex Example', status: 'accepted' }, { name: 'Jo Sample', status: 'needsAction' }, { name: 'Kit Demo', status: 'declined' }] }),
];

function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(DIST, '.' + path.sep + rel);
    if ((file !== DIST && !file.startsWith(DIST + path.sep)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end('nope'); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}
function freezeClock(page, iso) {
  return page.addInitScript((stamp) => {
    const Real = Date; const fixed = new Real(stamp).getTime();
    function F(...a) { if (!(this instanceof F)) return new Real(fixed).toString(); return a.length ? new Real(...a) : new Real(fixed); }
    F.prototype = Real.prototype; F.now = () => fixed; F.parse = Real.parse; F.UTC = Real.UTC;
    // eslint-disable-next-line no-global-assign
    Date = F;
  }, iso);
}

const results = [];
async function t(name, fn) { try { await fn(); results.push(true); console.log('  ✓ ' + name); } catch (e) { results.push(false); console.log('  ✗ ' + name + '\n      ' + String(e && e.message || e).split('\n')[0]); } }

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const srv = await serve(); const port = srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: VIEW, hasTouch: true });
  await freezeClock(page, '2026-10-14T09:30:00');
  const wire = [], pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e.message || e)));
  const events = EVENTS.map((e) => Object.assign({}, e));
  await page.route('**/api/**', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/calendar/events**', (r) => {
    const req = r.request(), url = new URL(req.url());
    if (req.method() === 'GET' && /\/events$/.test(url.pathname)) {
      const a = url.searchParams.get('start_date'), b = url.searchParams.get('end_date');
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: events.filter((e) => !e.deleted && (!a || e.start_date >= a) && (!b || e.start_date <= b)) }) });
    }
    const id = decodeURIComponent(url.pathname.split('/').pop());
    const body = req.postData() ? JSON.parse(req.postData()) : null;
    wire.push({ method: req.method(), id, body });
    const ev = events.find((e) => e.id === id);
    if (req.method() === 'PUT' && ev) Object.assign(ev, body);
    if (req.method() === 'DELETE' && ev) ev.deleted = true;
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(req.method() === 'DELETE' ? { ok: true, id } : ev) });
  });
  await page.goto(`http://127.0.0.1:${port}/touch/home.html`, { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none!important}' });
  await page.waitForTimeout(800);
  await page.click('#apps'); await page.waitForTimeout(450);
  await page.click('.ltile[data-id="calendar"]'); await page.waitForTimeout(1000);

  const open = async (id) => { await page.click(`#calBody [data-eid="${id}"]`); await page.waitForSelector('#estModal .evd', { timeout: 3000 }); await page.waitForTimeout(250); };
  const closed = () => page.evaluate(() => !document.getElementById('estModal').classList.contains('on'));
  const shot = (n) => page.screenshot({ path: path.join(SHOTS, n + '.png') });
  const rect = () => page.evaluate(() => { const r = document.querySelector('#estModal .evd').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const fields = () => page.evaluate(() => document.querySelectorAll('#estModal select, #estModal input, #estModal textarea, #estModal [contenteditable]').length);

  console.log('\ncalendar event sheet (1280x720, real Chromium)\n');
  await t('tapping an event opens a near-full-screen sheet that fits the panel', async () => {
    await open('ev-min');
    const r = await rect();
    assert(r.w >= 1000 && r.h >= 500, `sheet ${Math.round(r.w)}x${Math.round(r.h)} is not near-full-screen`);
    assert(r.x >= 0 && r.y >= 0 && r.x + r.w <= VIEW.width && r.y + r.h <= VIEW.height, 'sheet overflows the panel');
    await shot('01-detail-minimal');
  });
  await t('minimal event shows title/date/time/category and omits empty rows', async () => {
    const txt = await page.evaluate(() => document.querySelector('#estModal .evd').innerText);
    ['Dentist', 'Health', '9:00 am', 'October'].forEach((s) => assert(txt.includes(s), 'missing ' + s));
    ['WITH', 'WHERE', 'NOTES', 'MEETING', 'REMINDER'].forEach((s) => assert(!txt.includes(s), 'should be omitted: ' + s));
  });
  await t('no select / input / textarea in the detail view', async () => { assert.strictEqual(await fields(), 0); });
  await t('every control in the sheet is >= 48px', async () => {
    const small = await page.evaluate(() => [...document.querySelectorAll('#estModal button')].map((b) => { const r = b.getBoundingClientRect(); return { t: b.textContent.trim().slice(0, 20), w: r.width, h: r.height }; }).filter((b) => b.w < 48 || b.h < 48));
    assert.deepStrictEqual(small, []);
  });
  await t('"Edit on your phone" shows a QR for the phone', async () => {
    await page.click('#estModal [data-x="phone"]'); await page.waitForTimeout(500);
    const r = await page.evaluate(() => { const c = document.querySelector('#evqr canvas'); return { hidden: document.getElementById('evqr').hidden, w: c && c.width, drawn: c && (() => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 0; i < d.length; i += 4) if (d[i] < 100) return true; return false; })() }; });
    assert(!r.hidden && r.w > 0, 'QR slot visible'); assert(r.drawn, 'the QR was actually drawn (dark modules present)');
    await shot('02-detail-phone-qr');
  });

  await t('edit view: category chips are readable (computed AA), selected state obvious, no native select', async () => {
    await page.click('#estModal [data-x="edit"]'); await page.waitForTimeout(300);
    assert.strictEqual(await fields(), 0, 'no form controls');
    const chips = await page.evaluate(() => {
      const lum = (c) => { const v = c.match(/[\d.]+/g).slice(0, 3).map(Number).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
      return [...document.querySelectorAll('#estModal .evchip')].map((b) => { const s = getComputedStyle(b); const a = lum(s.color), c = lum(s.backgroundColor); const r = b.getBoundingClientRect();
        return { cat: b.dataset.cat, on: b.getAttribute('aria-checked'), ratio: (Math.max(a, c) + 0.05) / (Math.min(a, c) + 0.05), h: r.height, visible: r.width > 0 && r.right <= 1280 && r.bottom <= 720 }; });
    });
    assert(chips.length >= 8, 'all categories shown (' + chips.length + ')');
    chips.forEach((c) => { assert(c.ratio >= 4.5, `${c.cat} contrast ${c.ratio.toFixed(2)}`); assert(c.h >= 48, c.cat + ' height ' + c.h); });
    assert.strictEqual(chips.filter((c) => c.on === 'true').length, 1, 'exactly one selected');
    assert.strictEqual(chips.find((c) => c.on === 'true').cat, 'health');
    await shot('03-edit-chips');
  });
  await t('chip + Save PUTs ONLY the category, then returns to the updated sheet', async () => {
    wire.length = 0;
    await page.click('#estModal .evchip[data-cat="work"]'); await page.waitForTimeout(150);
    const sel = await page.evaluate(() => [...document.querySelectorAll('#estModal .evchip.on')].map((b) => b.dataset.cat));
    assert.deepStrictEqual(sel, ['work'], 'tap moves the selection');
    await page.click('#estModal [data-x="save"]'); await page.waitForTimeout(500);
    assert.strictEqual(wire.length, 1, 'one request'); assert.strictEqual(wire[0].method, 'PUT'); assert.strictEqual(wire[0].id, 'ev-min');
    assert.deepStrictEqual(wire[0].body, { category: 'work' });
    assert((await page.evaluate(() => document.querySelector('#estModal .evcat').textContent)).includes('Work'), 'sheet shows the new category');
  });
  await t('a date/time stepper tap + Save moves the start and keeps the length', async () => {
    await page.click('#estModal [data-x="close"]'); await page.waitForTimeout(150);
    await open('ev-full'); await page.click('#estModal [data-x="edit"]'); await page.waitForTimeout(300);
    wire.length = 0;
    await page.click('#estModal .dtp button[data-s="h,1"]'); await page.waitForTimeout(100);
    await page.click('#estModal [data-x="save"]'); await page.waitForTimeout(500);
    assert.deepStrictEqual(wire[0].body, { start_time: '19:30', end_time: '21:00' });
  });
  await t('full event shows notes, who, where, repeat, reminder', async () => {
    const txt = await page.evaluate(() => document.querySelector('#estModal .evd').innerText);
    ['Bring the blue folder.', 'Sam', 'Riley', 'Example Primary School hall', 'Repeats every week', '30 min before', '7:30 pm – 9:00 pm'].forEach((s) => assert(txt.includes(s), 'missing ' + s));
    await shot('04-detail-full');
  });
  await t('delete asks first; Keep it returns; Delete sends DELETE and closes', async () => {
    wire.length = 0;
    await page.click('#estModal [data-x="del"]'); await page.waitForTimeout(150);
    assert((await page.evaluate(() => document.querySelector('#estModal .evd').innerText)).includes('Delete “Sam’s school concert”?'));
    assert.strictEqual(wire.length, 0, 'nothing sent before confirming');
    await shot('05-delete-confirm');
    await page.click('#estModal [data-x="keep"]'); await page.waitForTimeout(150);
    assert(!(await closed()) && wire.length === 0, 'Keep it keeps the sheet');
    await page.click('#estModal [data-x="del"]'); await page.click('#estModal [data-x="delyes"]'); await page.waitForTimeout(500);
    assert.strictEqual(wire[0].method, 'DELETE'); assert(await closed());
  });
  await t('synced-shaped event: read-only, tentative, source, tz, meeting QR, RSVPs', async () => {
    await open('ev-sync');
    const h = await page.evaluate(() => ({ txt: document.querySelector('#estModal .evd').innerText, btns: [...document.querySelectorAll('#estModal button')].map((b) => b.dataset.x) }));
    ['Tentative', 'From Google Calendar', 'Europe/London', 'Managed in Google Calendar', 'Organizer', 'Going', 'No reply', 'Declined', 'Agenda in the shared doc.'].forEach((s) => assert(h.txt.includes(s), 'missing ' + s));
    assert(!h.btns.includes('edit') && !h.btns.includes('del') && !h.btns.includes('phone'), 'no edit/delete on a read-only event: ' + h.btns);
    await page.click('#estModal [data-x="meeturl"]'); await page.waitForTimeout(500);
    assert(await page.evaluate(() => !document.getElementById('evqr').hidden), 'meeting QR shown');
    await shot('06-detail-synced');
  });
  await t('close: the X', async () => { await page.click('#estModal [data-x="close"]'); await page.waitForTimeout(150); assert(await closed()); });
  await t('close: tap outside the sheet', async () => { await open('ev-min'); await page.mouse.click(4, 4); await page.waitForTimeout(150); assert(await closed()); });
  await t('close: swipe down from the top', async () => {
    await open('ev-min');
    await page.evaluate(() => {
      const sh = document.querySelector('#estModal .evd');
      const mk = (type, y) => new TouchEvent(type, { bubbles: true, cancelable: true, touches: type === 'touchend' ? [] : [new Touch({ identifier: 1, target: sh, clientX: 600, clientY: y })], changedTouches: [new Touch({ identifier: 1, target: sh, clientX: 600, clientY: y })] });
      sh.dispatchEvent(mk('touchstart', 200)); sh.dispatchEvent(mk('touchend', 420));
    });
    await page.waitForTimeout(150); assert(await closed());
  });
  await t('close: a short drag does NOT close', async () => {
    await open('ev-min');
    await page.evaluate(() => {
      const sh = document.querySelector('#estModal .evd');
      const mk = (type, y) => new TouchEvent(type, { bubbles: true, cancelable: true, touches: type === 'touchend' ? [] : [new Touch({ identifier: 1, target: sh, clientX: 600, clientY: y })], changedTouches: [new Touch({ identifier: 1, target: sh, clientX: 600, clientY: y })] });
      sh.dispatchEvent(mk('touchstart', 200)); sh.dispatchEvent(mk('touchend', 260));
    });
    await page.waitForTimeout(150); assert(!(await closed()));
  });
  await t('close: the spoken word "close" (and not an unrelated sentence)', async () => {
    await page.evaluate(() => window.ZoeEstateVoice.transcript('turn on the kitchen light')); await page.waitForTimeout(100);
    assert(!(await closed()), 'unrelated speech must not close the sheet');
    await page.evaluate(() => window.ZoeEstateVoice.transcript('Zoe, close that')); await page.waitForTimeout(150);
    assert(await closed());
  });
  await t('no uncaught page errors', async () => { assert.deepStrictEqual(pageErrors, []); });

  await browser.close(); srv.close();
  const failed = results.filter((x) => !x).length;
  console.log(`\n${results.length - failed}/${results.length} passed; screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
