#!/usr/bin/env node
/**
 * Desktop UI "just needs to work" - phase 1 (2026-10-19). A live audit of every desktop page
 * (logged out against the real origin, plus every page with a member session against an
 * empty API mock) found: a fake "Zoe Developer" dashboard (hard-coded container/roadmap
 * numbers, three JS parse errors, served without the sign-in gate) linked from the More menu
 * on 10 pages; desktop Settings driving two retired runtimes (OpenClaw prefs + ChatGPT status,
 * Hermes model profiles) and opening /touch/pair.html to pair a panel; chat offering Hermes
 * and OpenClaw pills that force a turn into a runtime that no longer exists; calendar
 * throwing a TypeError on every load (a legacy notifications block writing into markup the
 * shared notifications panel had replaced); journal probing /api/journeys (no route, 404 on
 * every load) and flashing two fake entries with hot-linked third-party photos; and a stray
 * empty file `nice` in dist/. This harness keeps all of that fixed, mostly by behaviour.
 * Run: node services/zoe-ui/dist/test_desktop_ui_phase1.js
 */
const fs = require('fs'); const path = require('path'); const vm = require('vm'); const assert = require('assert');
const root = __dirname;
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const desktopPages = fs.readdirSync(root).filter(f => f.endsWith('.html'));
let n = 0; const ok = (m) => { n++; console.log('  ok  ' + m); };

function extractFunction(src, name) {
  const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(');
  const m = re.exec(src);
  assert(m, 'missing function ' + name);
  let i = src.indexOf('{', m.index), depth = 0;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return src.slice(m.index, i + 1); }
    else if (ch === '`' || ch === '"' || ch === "'") {
      const q = ch; i++;
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++; }
    } else if (ch === '/' && src[i + 1] === '/') { i = src.indexOf('\n', i); }
    else if (ch === '/' && src[i + 1] === '*') { i = src.indexOf('*/', i) + 1; }
    else if (ch === '/') {
      let j = i - 1; while (j >= 0 && /\s/.test(src[j])) j--;
      if (j < 0 || /[(,=:\[!&|?{};+\-*%<>~^]/.test(src[j]) || /\breturn$/.test(src.slice(Math.max(0, j - 6), j + 1))) {
        let k = i + 1, cls = false;
        while (k < src.length) { const c = src[k]; if (c === '\\') { k += 2; continue; } if (cls) { if (c === ']') cls = false; } else if (c === '[') cls = true; else if (c === '/') break; else if (c === '\n') break; k++; }
        i = k;
      }
    }
  }
  throw new Error('unterminated ' + name);
}

// 1. The fake developer dashboard is gone, unlinked and unrouted.
assert(!fs.existsSync(path.join(root, 'developer')), 'dist/developer/ should be retired');
for (const page of desktopPages) assert(!read(page).includes('developer/index.html'), page + ' still links the retired developer page');
const locations = fs.readFileSync(path.join(root, '..', 'nginx.d', 'locations.inc'), 'utf8');
assert(!/location\s+\/developer\//.test(locations), 'nginx still routes /developer/');
ok('the fake developer dashboard is gone: no dist/developer/, no More-menu link on ' + desktopPages.length + ' pages, no nginx location');

// 2. No stray files in dist/ root (the empty `nice` - a mis-aimed `nice -n 10` - shipped in #2027).
const stray = fs.readdirSync(root).filter(f => fs.statSync(path.join(root, f)).isFile() && !/\.(html|js|json|png|ico|svg|webmanifest|txt)$/.test(f));
assert.deepStrictEqual(stray, [], 'stray files in dist/: ' + stray.join(', '));
ok('dist/ root holds only web assets (no stray extensionless files)');

// 3. Every page a desktop page navigates to exists (the class behind the developer link).
const missing = [];
for (const page of desktopPages) {
  const src = read(page);
  const targets = [...src.matchAll(/(?:navigateToPage\(\s*|<a\b[^>]*\bhref=|location(?:\.href)?\s*=\s*|location\.(?:assign|replace)\(\s*|window\.open\(\s*)['"`]\/?([A-Za-z0-9_\-\/]+\.html)/g)].map(m => m[1]);
  for (const t of targets) if (!fs.existsSync(path.join(root, t))) missing.push(page + ' -> ' + t);
}
assert.deepStrictEqual(missing, [], 'desktop pages navigate to missing pages: ' + missing.join(', '));
ok('every .html a desktop page navigates to (links, navigateToPage, location writes, window.open) exists');

// 4. Settings no longer drives the retired OpenClaw / Hermes runtimes.
const settings = read('settings.html');
for (const dead of ['/api/system/openclaw', '/api/system/hermes', '/api/voice/chatgpt-auth-status', 'sec-openclaw', 'sec-ai-models']) {
  assert(!settings.includes(dead), 'settings.html still carries ' + dead);
}
assert(!/id:\s*'(?:openclaw|ai-models)'/.test(settings), 'settings NAV still lists a retired section');
ok('desktop Settings has no OpenClaw/ChatGPT or Hermes model-profile section (both runtimes retired 2026-10-18)');

// 5. Settings pairs a panel in place - the same confirm the phone page makes - and never opens /touch/.
async function pairing() {
  const els = {};
  const el = (id) => (els[id] = els[id] || { id, value: '', textContent: '', disabled: false, style: {}, focus() {} });
  const calls = []; const opened = []; let reloaded = 0;
  const ctx = { document: { getElementById: el }, encodeURIComponent, String,
                window: { open: (u) => opened.push(u) }, loadTouchPanels: () => { reloaded++; } };
  ctx.apiCall = async (method, url, body) => {
    calls.push({ method, url, body });
    if (url.endsWith('/public')) return { status: 'pending' };
    return { message: 'Living Room is paired.' };
  };
  const letLine = /let _panelPairCode = '';/.exec(settings);
  assert(letLine, 'settings.html: _panelPairCode state missing');
  const code = letLine[0] + '\n' + ['confirmPanelCode', 'panelPairSlug', 'pairPanelHere'].map(f => extractFunction(settings, f)).join('\n') +
    '\n; ({ confirmPanelCode, panelPairSlug, pairPanelHere })';
  const api = vm.runInNewContext(code, ctx);
  el('panel-code-input').value = 'a3f7k2';
  await api.confirmPanelCode();
  assert.deepStrictEqual(opened, [], 'confirmPanelCode opened a window: ' + opened.join(','));
  assert.strictEqual(el('panel-pair-form').style.display, 'flex', 'the pairing form is not shown after a pending code');
  el('panel-pair-name').value = 'Living Room';
  api.panelPairSlug();
  assert.strictEqual(el('panel-pair-id').value, 'living-room-panel');
  el('panel-pair-location').value = 'Downstairs';
  await api.pairPanelHere();
  const post = calls.find(c => c.method === 'POST');
  assert(post, 'no confirm POST was made');
  assert.strictEqual(post.url, '/api/panels/provision/A3F7K2/confirm');
  assert.strictEqual(JSON.stringify(post.body), JSON.stringify({ name: 'Living Room', location: 'Downstairs', panel_id: 'living-room-panel' }));
  assert.strictEqual(reloaded, 1, 'the panel list is not refreshed after pairing');
  assert.strictEqual(el('panel-pair-form').style.display, 'none');
  assert(/paired/.test(el('panel-code-msg').textContent), 'no success message: ' + el('panel-code-msg').textContent);
  // SAME state: a pending lookup, then an expired one - the expired lookup must clear the earlier
  // code, so the next Pair click cannot submit the stale code (Codex, #2161).
  ctx.apiCall = async (method, url, body) => { calls.push({ method, url, body }); return { status: url.includes('OLDOK1') ? 'pending' : 'expired' }; };
  el('panel-code-input').value = 'OLDOK1';
  await api.confirmPanelCode();
  assert.strictEqual(el('panel-pair-form').style.display, 'flex');
  el('panel-code-input').value = 'NEWEX2';
  await api.confirmPanelCode();
  assert.strictEqual(el('panel-pair-form').style.display, 'none', 'an expired lookup left the earlier pairing form up');
  calls.length = 0;
  await api.pairPanelHere();
  assert(!calls.some(c => c.method === 'POST'), 'a stale code was paired after a newer lookup failed');
  // an expired code never shows the form, and nothing posts without a looked-up code
  calls.length = 0;
  const ctx2 = Object.assign({}, ctx, { apiCall: async (method, url) => { calls.push({ method, url }); return { status: 'expired' }; } });
  const api2 = vm.runInNewContext(code, ctx2);
  el('panel-code-input').value = 'ZZZZZZ';
  await api2.confirmPanelCode();
  assert.strictEqual(el('panel-pair-form').style.display, 'none', 'an expired code showed the pairing form');
  await api2.pairPanelHere();
  assert(!calls.some(c => c.method === 'POST'), 'pairPanelHere posted without a looked-up code');
  ok('Settings pairs a panel in place: lookup, name, POST /confirm, list refreshed, no /touch/ window; an expired code never posts');
}

function chatAgents() {
  // 6. Chat cannot force a turn into a retired runtime.
  const chat = read('chat.html');
  for (const id of ['agentPillHermes', 'agentPillOpenclaw']) assert(!chat.includes('id=' + JSON.stringify(id)), 'chat.html still renders ' + id);
  const pre = 'let selectedAgent = ' + JSON.stringify('auto') + '; let forceOpenClawMode = false;\n';
  const post = '\n; selectAgent(' + JSON.stringify('hermes') + '); const a = selectedAgent; selectAgent(' + JSON.stringify('openclaw') + '); ({ a, b: selectedAgent, f: forceOpenClawMode })';
  const sel = vm.runInNewContext(pre + extractFunction(chat, 'selectAgent') + post, { document: { getElementById: () => null } });
  assert.deepStrictEqual({ a: sel.a, b: sel.b, f: sel.f }, { a: 'auto', b: 'auto', f: false });
  ok('chat shows no Hermes/OpenClaw pill and selectAgent() maps any agent to Zoe (force_agent stays auto)');
}

function calendarAndJournal() {
  // 7. Calendar no longer runs the legacy notifications block that threw on every load.
  const cal = read('calendar.html');
  for (const dead of ['function loadNotifications', 'function displayNotifications', 'notificationsContent', 'handleNotificationClick']) {
    assert(!cal.includes(dead), 'calendar.html still carries ' + dead);
  }
  assert(cal.includes('/js/notifications-panel.js'), 'calendar.html must keep the shared notifications panel');
  ok('calendar leaves notifications to the shared panel (the TypeError on every load is gone)');
  // 8. Journal: no dead probe, no fake entries, no third-party asset on any desktop page.
  const japi = read('js/journal-api.js');
  assert(!/probe\(\s*.\/journeys/.test(japi), 'journal-api.js still probes /api/journeys (no such route)');
  const markup = read('journal.html').replace(/<script\b[\s\S]*?<\/script>/g, '');
  assert(!/class=.timeline-entry./.test(markup), 'journal.html ships placeholder timeline entries in its markup');
  // (third-party images: services/zoe-data/tests/test_ui_no_external_assets_scan.py, allowlist now empty)
  ok('journal makes no /api/journeys call and ships no fake entries');
}

(async () => {
  await pairing();
  chatAgents();
  calendarAndJournal();
  console.log('desktop UI phase 1: ' + n + ' checks passed');
})().catch(e => { console.error(e); process.exit(1); });
