#!/usr/bin/env node
/**
 * Desktop is desktop, touch is touch (standing rule, 2026-07-20): no desktop page
 * may link, redirect or deep-link into /touch/*. Wave 5 (2026-10-04) severed the
 * last ones — the cooking/smart-home meta-refresh stubs (+20 nav items on 10 pages)
 * and notifications-panel.js's two deep links — and removed the files nothing loads.
 * This harness keeps them gone. Run: node services/zoe-ui/dist/test_desktop_no_touch_links.js
 */
const fs = require('fs'); const path = require('path'); const assert = require('assert');
const root = __dirname;
const desktopPages = fs.readdirSync(root).filter(f => f.endsWith('.html'));
let n = 0; const ok = (m) => { n++; console.log('  ok  ' + m); };
for (const page of desktopPages) {
  const src = fs.readFileSync(path.join(root, page), 'utf8');
  // hrefs / redirects / assignments into /touch/ — attribute values and JS string literals
  // navigation only: anchors, meta refresh, location writes. A <link>/<script> to a shared
  // asset under /touch/css or /touch/js is a dependency, not a route (chat.html's compose.css).
  const hits = [...src.matchAll(/(?:<a\b[^>]*\bhref=|location(?:\.href)?\s*=|location\.(?:assign|replace)\(|content="0; url=)\s*['"]?\/?touch\//g)];
  assert.strictEqual(hits.length, 0, page + ' still routes into /touch/: ' + hits.length + ' site(s)');
  for (const dead of ['cooking.html', 'smart-home.html', 'games.html', 'week_planner_widget.html', 'js/navigation.js', 'widget-registry.js', 'module-widget-loader.js', 'widgets-enhanced.css', 'memories-enhanced.css']) {
    assert(!src.includes(dead), page + ' references the removed ' + dead);
  }
}
ok(desktopPages.length + ' desktop pages carry no route into /touch/ and no reference to the removed files');
for (const dead of ['cooking.html', 'smart-home.html', 'games.html', 'week_planner_widget.html', 'js/navigation.js', 'js/lib/widget-registry.js', 'js/lib/module-widget-loader.js', 'css/widgets-enhanced.css', 'css/memories-enhanced.css']) {
  assert(!fs.existsSync(path.join(root, dead)), dead + ' should be gone');
}
ok('the desktop stubs and the dead tier are gone from dist/');
const panel = fs.readFileSync(path.join(root, 'js/notifications-panel.js'), 'utf8');
const links = [...panel.matchAll(/location\.href = '\/touch\/updates\.html[^']*'/g)];
assert(links.length === 2, 'expected the two touch deep links to still exist for touch surfaces');
for (const m of links) {
  const before = panel.slice(Math.max(0, m.index - 60), m.index);
  assert(/if \(IS_TOUCH_SURFACE\) $/.test(before), 'deep link is not gated to touch surfaces: ' + before.trim());
}
assert(/const IS_TOUCH_SURFACE = /.test(panel));
ok('notifications-panel.js deep-links into touch/updates.html only from a touch surface');
const orb = fs.readFileSync(path.join(root, 'js/orb-loader.js'), 'utf8');
assert(/document\.addEventListener\('zoe:logout', _purgeOrbOnLogout\)/.test(orb), 'orb purge must listen on document (auth.js dispatches there)');
ok('orb-loader purges transcripts on the document-level zoe:logout');
for (const f of ['js/dashboard.js', 'js/lists-dashboard.js']) {
  const s = fs.readFileSync(path.join(root, f), 'utf8');
  assert(!/if \(event\.persisted \|\| !window\.dashboard\)/.test(s), f + ' pageshow still re-inits on a plain first load');
  assert(/if \(event\.persisted\) \{/.test(s));
}
ok('dashboard pageshow handlers act only on a BFCache restore');
const off = fs.readFileSync(path.join(root, 'offline.html'), 'utf8');
assert(/fetch\('\/health'/.test(off) && !/\/api\/health/.test(off) && /if \(!res\.ok\) throw/.test(off));
ok('offline.html probes /health and checks res.ok');
for (const [f, bad] of [['notes.html', "fetch('/api/notes'"], ['chat.html', "apiRequest('/api/notes',"], ['chat.html', "apiRequest('/api/reminders',"], ['music.html', "fetch('/api/chat',"]]) {
  assert(!fs.readFileSync(path.join(root, f), 'utf8').includes(bad), f + ' still calls ' + bad + ' (307 → http Location)');
}
ok('no desktop call hits a FastAPI trailing-slash 307');
console.log('desktop no-touch-links: ' + n + ' checks passed');
