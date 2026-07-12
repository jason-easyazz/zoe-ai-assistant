/*
 * Data-integrity test for the TouchMenu launcher (the full-screen "Where to?"
 * grid picker in touch-menu.js). Extracts the launcher's data structures from
 * source and pins the contract so a bad edit fails loudly:
 *   - every page has a glyph AND an accent (no blank / emoji-fallback tiles),
 *   - every page path is a real file on disk,
 *   - ids/paths are unique, PRIMARY and GUEST sets reference real pages,
 *   - the injected CSS has balanced () and {}.
 * Run:  node services/zoe-ui/dist/touch/test_touch_menu_launcher.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'js', 'touch-menu.js'), 'utf8');

// Grab a balanced literal (array or object) following a declaration.
function grab(decl, open, close) {
  const at = src.indexOf(decl);
  assert(at >= 0, 'missing declaration: ' + decl);
  const start = src.indexOf(open, at);
  let depth = 0;
  for (let j = start; j < src.length; j++) {
    if (src[j] === open) depth++;
    else if (src[j] === close && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error('unbalanced literal for ' + decl);
}

// eslint-disable-next-line no-eval
const ALL_PAGES = eval(grab('const ALL_PAGES', '[', ']'));
// eslint-disable-next-line no-eval
const PRIMARY = eval(grab('const PRIMARY', '[', ']'));
// eslint-disable-next-line no-eval
const PG_GLYPH = eval('(' + grab('const PG_GLYPH', '{', '}') + ')');
// eslint-disable-next-line no-eval
const PG_ACCENT = eval('(' + grab('const PG_ACCENT', '{', '}') + ')');
// eslint-disable-next-line no-eval
const GUEST = eval(grab('GUEST_ALLOWED_PAGE_IDS = new Set(', '[', ']'));

const ids = ALL_PAGES.map(p => p.id);
const idSet = new Set(ids);

assert(ALL_PAGES.length >= 12, 'at least 12 pages, got ' + ALL_PAGES.length);
assert.strictEqual(idSet.size, ids.length, 'page ids are unique');
assert.strictEqual(new Set(ALL_PAGES.map(p => p.path)).size, ids.length, 'page paths are unique');

ALL_PAGES.forEach(p => {
  // every tile gets a real icon (glyph) + accent — no blank/emoji-fallback tiles
  assert(typeof PG_GLYPH[p.id] === 'string' && PG_GLYPH[p.id].length > 0, 'glyph for ' + p.id);
  assert(/^#[0-9a-f]{6}$/i.test(PG_ACCENT[p.id]), 'hex accent for ' + p.id);
  // path is well-formed AND the target file actually exists
  assert(/^\/touch\/[a-z-]+\.html$/.test(p.path), 'well-formed path: ' + p.path);
  const file = path.join(__dirname, p.path.replace('/touch/', ''));
  assert(fs.existsSync(file), 'page file exists on disk: ' + p.path);
});

// no stray glyph/accent keys that do not map to a real page
Object.keys(PG_GLYPH).forEach(k => assert(idSet.has(k), 'PG_GLYPH has unknown id: ' + k));
Object.keys(PG_ACCENT).forEach(k => assert(idSet.has(k), 'PG_ACCENT has unknown id: ' + k));

// PRIMARY tabs and the guest allow-list must reference real pages
PRIMARY.forEach(p => assert(idSet.has(p.id), 'PRIMARY references real page: ' + p.id));
GUEST.forEach(id => assert(idSet.has(id), 'GUEST_ALLOWED references real page: ' + id));

// injected CSS is balanced (guards paren/brace typos in the style template)
const cssMatch = src.match(/s\.textContent = `([\s\S]*?)`;/);
assert(cssMatch, 'found the injected CSS template');
const css = cssMatch[1];
const count = (s, ch) => s.split(ch).length - 1;
assert.strictEqual(count(css, '('), count(css, ')'), 'CSS parentheses balanced');
assert.strictEqual(count(css, '{'), count(css, '}'), 'CSS braces balanced');

console.log('touch-menu launcher: all assertions passed (' + ALL_PAGES.length +
            ' pages, all have glyph+accent, all files exist)');
