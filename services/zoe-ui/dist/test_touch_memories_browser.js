/*
 * Real-browser check of the estate's Memories space (touch/home.html) - headless Chromium at the panel's 1280x720.
 *
 * WHY A REAL BROWSER: test_touch_memories_space.js proves the logic against a DOM shim; only a browser sees layout
 * (hit-target size, overflow behind the dock) and the real launcher -> stage -> modal wiring in home.html.
 *
 * FIXTURE PROVENANCE: VIEW is NOT hand-written. It is the output of the REAL backend builder over invented rows:
 *   cd services/zoe-data && PYTHONPATH=.:tests python3 -c "...put rows with MemoryService + _Col (tests/test_memory_space.py)...
 *   print(json.dumps(asyncio.run(memory_space.read_view(uid, svc=svc))))"
 * so its shape cannot drift from GET /api/memories/mine. (Private rows are in the store; only their COUNT is here.)
 *
 * The stub plays the server's identity rule: the member's session id gets the view, anything else gets 403.
 *
 * Run:  node services/zoe-ui/dist/test_touch_memories_browser.js
 * Overrides: PLAYWRIGHT_CORE=<dir>  CHROME_PATH=<binary>  MEM_SHOTS=<dir>
 * (Not in CI - the repo has no JS browser lane. A local verification gate, like test_touch_dock_pins.js.)
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PW = [process.env.PLAYWRIGHT_CORE, 'playwright-core', 'playwright', '/home/zoe/.openclaw/npm/node_modules/playwright-core'].filter(Boolean);
let chromium = null;
for (const c of PW) { try { chromium = require(c).chromium; break; } catch (_) { /* next */ } }
if (!chromium) { console.error('playwright-core not found (set PLAYWRIGHT_CORE).'); process.exit(2); }
const CHROME = [process.env.CHROME_PATH, '/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser']
  .filter(Boolean).find((p) => fs.existsSync(p));
if (!CHROME) { console.error('No Chromium binary found (set CHROME_PATH).'); process.exit(2); }
const DIST = __dirname;
const SHOTS = process.env.MEM_SHOTS || '/tmp/memories-shots';

const VIEW = {
  "groups": [
    {
      "key": "people",
      "label": "People",
      "kind": "people",
      "entries": [
        {
          "name": "Marisol",
          "relationship": "sister",
          "label": "Marisol (sister)",
          "facts": [
            "is flying in from Lisbon on Thursday"
          ],
          "when": "6 Oct"
        },
        {
          "name": "Teodor",
          "relationship": "dad",
          "label": "Teodor (dad)",
          "facts": [
            "was a lighthouse keeper on the northern coast for thirty years"
          ],
          "when": "6 Oct"
        },
        {
          "name": "Quillan",
          "relationship": "friend",
          "label": "Quillan (friend)",
          "facts": [
            "lives in Eastvale"
          ],
          "when": "6 Oct"
        }
      ],
      "more": 0
    },
    {
      "key": "preferences",
      "label": "Preferences",
      "kind": "items",
      "items": [
        {
          "id": "zoe_demo_bar_00000001_dfaedadc68f7f51616466d88",
          "text": "I like dark roast coffee with oat milk",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        },
        {
          "id": "zoe_demo_bar_00000001_1ef9472faced4e69c7f27ee7",
          "text": "I love spicy noodles on a rainy night",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        }
      ],
      "more": 0
    },
    {
      "key": "routines",
      "label": "Routines",
      "kind": "items",
      "items": [
        {
          "id": "zoe_demo_bar_00000001_5d117d5ec509d4fa8d83be56",
          "text": "I walk the dog every morning before work",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        },
        {
          "id": "zoe_demo_bar_00000001_ce2b1a76dea1803d56feb99e",
          "text": "I go to the gym on Tuesdays and Thursdays",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        }
      ],
      "more": 0
    },
    {
      "key": "dates",
      "label": "Dates",
      "kind": "items",
      "items": [
        {
          "id": "zoe_demo_bar_00000001_2203ce388c418a76760607f4",
          "text": "My birthday is on 14 March",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        },
        {
          "id": "zoe_demo_bar_00000001_167e35eddf01c507bfecf86c",
          "text": "My wedding anniversary is on 2 June",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        }
      ],
      "more": 0
    },
    {
      "key": "places",
      "label": "Places",
      "kind": "items",
      "items": [
        {
          "id": "zoe_demo_bar_00000001_755702e951cb5842230d9964",
          "text": "I live in Eastvale near the old mill",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        }
      ],
      "more": 0
    },
    {
      "key": "other",
      "label": "Other",
      "kind": "items",
      "items": [
        {
          "id": "zoe_demo_bar_00000001_0551fd261b1025d72b0af55d",
          "text": "I keep my bike in the shed behind the garage",
          "when": "6 Oct",
          "epoch": 1791252000.0,
          "source": "told"
        }
      ],
      "more": 0
    }
  ],
  "private": {
    "health": 1,
    "money": 1
  },
  "unsure": 0,
  "total": 11
};

const MEMBER_SID = 'sess-member';
function serve() {
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(DIST, '.' + path.sep + rel);
    if ((file !== DIST && !file.startsWith(DIST + path.sep)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end('nope'); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv)));
}

async function stub(page, ctx) {
  await page.route('**/api/**', async (route) => {
    const req = route.request(); const url = req.url();
    const sid = req.headers()['x-session-id'] || '';
    const json = (body, status) => route.fulfill({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (/\/api\/memories\/mine$/.test(url) && req.method() === 'GET') {
      ctx.reads.push(sid);
      return sid === MEMBER_SID ? json(ctx.view) : json({ detail: 'Sign in to see what Zoe knows about you.' }, 403);
    }
    const f = url.match(/\/api\/memories\/mine\/([^/]+)\/forget$/);
    if (f && req.method() === 'POST') {
      ctx.forgets.push({ id: decodeURIComponent(f[1]), sid });
      if (sid !== MEMBER_SID) return json({ detail: 'x' }, 403);
      return json({ forgotten: true });
    }
    if (url.includes('/api/system/display/preferences')) return json({ preferences: {} });
    if (url.includes('/api/skybridge/timers')) return json({ timers: [] });
    if (url.includes('/api/music/now-playing')) return json({ now_playing: null });
    return json({});
  });
}

async function open(browser, ctx, session) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  await context.addInitScript((s) => {
    try {
      localStorage.setItem('zoe_kiosk', '1'); sessionStorage.setItem('zoe_kiosk', '1');
      localStorage.setItem('zoe_session', JSON.stringify(s));
    } catch (e) { /* ignore */ }
  }, session);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await stub(page, ctx);
  await page.goto(ctx.base + '/touch/home.html?kiosk=1', { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '#authov{display:none !important}' });
  await page.waitForFunction(() => { const b = document.getElementById('dbody'); return b && !b.textContent.includes('…'); }, { timeout: 8000 });
  return { page, errors, context };
}
async function shoot(page, name) { fs.mkdirSync(SHOTS, { recursive: true }); const f = path.join(SHOTS, name + '.png'); await page.screenshot({ path: f }); return f; }
async function openMemories(page) {
  await page.click('#apps'); await page.waitForTimeout(500);
  const tiles = await page.$$eval('.ltile .lcap', (els) => els.map((e) => e.textContent));
  await shoot(page, 'launcher');
  await page.click('.ltile[data-id="memories"]'); await page.waitForTimeout(700);
  return tiles;
}

(async () => {
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  let n = 0; const ok = (c, m) => { assert(c, m); n++; };
  try {
    // 1. a GUEST kiosk: the tile is there, the screen is a sign-in prompt, no row anywhere
    {
      const ctx = { base, view: VIEW, reads: [], forgets: [] };
      const { page, errors, context } = await open(browser, ctx, { session_id: 'sess-guest', user_id: 'guest', role: 'guest', expires_at: new Date(Date.now() + 1800000).toISOString() });
      const tiles = await openMemories(page);
      ok(tiles.includes('Memories') && !tiles.includes('Ask Zoe'), 'launcher: Memories in, Ask Zoe out: ' + tiles.join(','));
      const txt = await page.$eval('#full', (e) => e.innerText);
      ok(/Sign in to see what I know about you/.test(txt), 'guest sees the sign-in prompt');
      ok(!/Marisol|dark roast|birthday|private note/.test(txt), 'a guest sees no row');
      ok(ctx.reads.length === 1 && ctx.reads[0] === 'sess-guest', 'one read, with the guest session');
      const btn = await page.$eval('[data-act="signin"]', (b) => { const r = b.getBoundingClientRect(); return { w: r.width, h: r.height }; });
      ok(btn.h >= 56 && btn.w >= 120, 'sign-in target is touch-sized: ' + JSON.stringify(btn));
      await shoot(page, 'memories-guest');
      ok(errors.length === 0, 'page errors: ' + errors.join('; '));
      await context.close();
    }
    // 2. the signed-in member: grouped rows, large targets, confirm-then-forget
    {
      const ctx = { base, view: JSON.parse(JSON.stringify(VIEW)), reads: [], forgets: [] };
      const { page, errors, context } = await open(browser, ctx, { session_id: MEMBER_SID, user_id: 'demo_member', role: 'user', expires_at: new Date(Date.now() + 3600000).toISOString() });
      await openMemories(page);
      const txt = await page.$eval('#full', (e) => e.innerText);
      ok(/What Zoe knows about you/.test(txt) && /Marisol/.test(txt) && /dark roast/.test(txt), 'member sees their rows');
      ok(/private notes \(health, money\) aren.t shown on this screen/.test(txt), 'private classes are a count');
      ok(!/doctor|salary|migraine/i.test(txt), 'no private words');
      ok(ctx.reads.every((s) => s === MEMBER_SID), 'every read carried the member session');
      const geo = await page.$$eval('.memforget', (bs) => bs.map((b) => { const r = b.getBoundingClientRect(); return { w: r.width, h: r.height }; }));
      ok(geo.length >= 5 && geo.every((g) => g.h >= 56 && g.w >= 80), 'every Forget is a >=56px target: ' + JSON.stringify(geo.slice(0, 2)));
      const over = await page.evaluate(() => { const e = document.getElementById('memBody'); return { sw: e.scrollWidth, cw: e.clientWidth }; });
      ok(over.sw <= over.cw + 1, 'no sideways scroll');
      await shoot(page, 'memories-member');
      // forget: tap -> modal -> Keep it (nothing sent) -> tap -> Forget it
      const before = await page.$$eval('.memitem', (e) => e.length);
      await page.click('.memitem .memforget'); await page.waitForTimeout(300);
      ok(await page.$eval('#estModal', (e) => e.classList.contains('on')), 'confirm modal opens');
      await shoot(page, 'memories-confirm');
      await page.click('#estModal [data-x="no"]'); await page.waitForTimeout(200);
      ok(ctx.forgets.length === 0 && (await page.$$eval('.memitem', (e) => e.length)) === before, 'Keep it sends nothing');
      await page.click('.memitem .memforget'); await page.waitForTimeout(300);
      const yes = await page.$eval('#estModal [data-x="yes"]', (b) => { const r = b.getBoundingClientRect(); return r.height; });
      ok(yes >= 48, 'modal confirm is touch-sized: ' + yes);
      await page.click('#estModal [data-x="yes"]'); await page.waitForTimeout(500);
      ok(ctx.forgets.length === 1 && ctx.forgets[0].sid === MEMBER_SID, 'one forget, with the member session');
      ok((await page.$$eval('.memitem', (e) => e.length)) === before - 1, 'row removed after the server said yes');
      ok(errors.length === 0, 'page errors: ' + errors.join('; '));
      // leaving the screen leaves nothing behind
      await page.click('#home'); await page.waitForTimeout(500);
      ok(!/Marisol|dark roast/.test(await page.evaluate(() => document.body.innerText)), 'private rows do not outlive the screen');
      await context.close();
    }
  } finally { await browser.close(); srv.close(); }
  console.log('touch memories (browser): ' + n + ' checks passed; screenshots in ' + SHOTS);
})().catch((e) => { console.error(e); process.exit(1); });
