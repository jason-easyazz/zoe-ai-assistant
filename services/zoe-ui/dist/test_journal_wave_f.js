#!/usr/bin/env node
/**
 * Node harness for the Wave-F journal repairs.
 *
 *  1. Edit was an alert('coming soon') stub while PUT /api/journal/{entry_id}
 *     has existed all along (journal.py:360). The comment claiming otherwise
 *     named the path WITHOUT its /api/journal prefix — delete, fifteen lines
 *     below, uses the same prefix and always worked.
 *  2. The Journeys tab rendered hardcoded Tokyo/Paris/Bali cards with Unsplash
 *     photos as if they were the user's own entries. No /api/journeys route
 *     exists anywhere in zoe-data.
 *  3. Location autocomplete called /api/location/search, which does not exist;
 *     the route is under the weather router (weather.py:638, prefix
 *     /api/weather). Mis-prefixed, not missing.
 *
 * Run: node services/zoe-ui/dist/test_journal_wave_f.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'journal.html'), 'utf8');
const enh = fs.readFileSync(path.join(__dirname, 'js', 'journal-ui-enhancements.js'), 'utf8');

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('journal.html Wave F repairs');

check('every inline <script> block parses', () => {
    const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
        .map(m => m[1]).filter(b => b.trim());
    blocks.forEach((b, i) => new vm.Script(b, { filename: `journal.html#inline-${i}` }));
});

check('markup stays balanced after the Journeys removal', () => {
    const open = (html.match(/<div\b/g) || []).length;
    const close = (html.match(/<\/div>/g) || []).length;
    assert.strictEqual(open, close, `div imbalance: ${open} open vs ${close} close`);
});

check('Edit is wired to the real PUT route and is no longer hidden', () => {
    assert.ok(!/Edit functionality coming soon/.test(html), 'the alert() stub must be gone');
    const fn = html.match(/function editCurrentEntry[\s\S]*?\n {8}\}/);
    assert.ok(fn, 'editCurrentEntry must exist');
    assert.ok(/method: 'PUT'/.test(fn[0]) && /\/api\/journal\/\$\{entry\.id\}/.test(fn[0]),
        'must PUT to /api/journal/{entry_id}');
    assert.ok(!/onclick="editCurrentEntry\(\)" style="display:none;"/.test(html),
        'the Edit button must not stay hidden now that the route is wired');
});

check('the fabricated Journeys content is gone', () => {
    for (const word of ['Tokyo', 'Paris', 'Bali']) {
        assert.ok(!new RegExp('journey-location[\\s\\S]{0,80}' + word).test(html),
            `${word} journey card must be gone — it was never the user's data`);
    }
    assert.ok(!/class="journeys-view"/.test(html), 'the journeys view must be removed');
    assert.ok(!/data-view="journeys"/.test(html), 'its tab button must be removed too');
    assert.ok(!/journey-image/.test(html), 'no journey imagery may remain');
});

check('location autocomplete uses the real weather-router path', () => {
    assert.ok(!/fetch\(`\/api\/location\/search/.test(enh),
        'the nonexistent /api/location/search must be gone');
    assert.ok(/\/api\/weather\/location\/search/.test(enh),
        'must call the real route under the weather prefix');
});

check('journal-ui-enhancements keeps its LIVE exports', () => {
    // Its autocomplete half is dead by script order, but showEntryModal and
    // publishEntryEnhanced own the live view and publish paths — deleting the
    // file wholesale (as an earlier draft of the plan proposed) would break
    // entry viewing and publishing.
    assert.ok(/showEntryModal/.test(enh), 'showEntryModal is called from journal-api.js:563');
    assert.ok(/publishEntryEnhanced/.test(enh), 'publishEntryEnhanced owns the publish path');
});

console.log(`\n${passed} checks passed`);
