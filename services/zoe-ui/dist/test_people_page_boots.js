#!/usr/bin/env node
/**
 * Node harness for people.html — proves the page's main script BOOTS.
 *
 * WHY: people.html has been 100% dead since 2026-05-18. Its main <script>
 * opened with
 *     const canvas = document.getElementById('peopleCanvas');
 *     const ctx = canvas.getContext('2d');
 * and no <canvas> element exists anywhere in the file, so `.getContext` threw
 * on statement two and killed the entire ~1500-line block — including the
 * DOMContentLoaded handler. The page rendered "Loading people…" forever
 * against a perfectly healthy /api/people backend.
 *
 * Removing the crash is necessary but NOT sufficient: the canvas render layer
 * was also the home of showPersonDetail() and updateLegend(), which six LIVE
 * functions still called. Reviving the page without repointing them would just
 * trade a load-time crash for a ReferenceError on every click — the same shape
 * as tasks.js's `toggleTask`, which was invisible only while its widget never
 * rendered.
 *
 * Asserts:
 *   a) every inline block parses
 *   b) the page has no canvas bootstrap left, and no <canvas> element
 *   c) NO call site references a removed function (the ReferenceError guard)
 *   d) the live detail path (dpOpenCard) and grid refresh (renderCardGrid) are
 *      the repoint targets, and every DOM id they touch exists in the markup
 *   e) executing the main block against a DOM stub does not throw
 *
 * Run: node services/zoe-ui/dist/test_people_page_boots.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'people.html'), 'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
    .map(m => m[1]).filter(b => b.trim());
// the main block is the biggest one
const mainBlock = blocks.slice().sort((a, b) => b.length - a.length)[0];

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('people.html boots (canvas relic removed)');

check('every inline <script> block parses', () => {
    blocks.forEach((b, i) => new vm.Script(b, { filename: `people.html#inline-${i}` }));
    assert.ok(blocks.length >= 1);
});

check('no canvas bootstrap and no <canvas> element remain', () => {
    assert.ok(!/getElementById\(['"]peopleCanvas['"]\)/.test(html),
        'the peopleCanvas lookup must be gone');
    assert.ok(!/\.getContext\(['"]2d['"]\)/.test(html),
        'the getContext call that killed the page must be gone');
    assert.strictEqual((html.match(/<canvas/gi) || []).length, 0,
        'no <canvas> element exists, which is why the relic could never work');
});

check('no call site references a removed function', () => {
    // Comments may mention them; executable references must not exist.
    // Strip BOTH full-line and trailing `//` comments — the repoint sites carry
    // trailing "// was updateLegend()" notes, and a naive full-line filter keeps
    // those and matches the comment text instead of real code.
    const code = blocks.join('\n')
        .split('\n')
        .map(l => l.replace(/\/\/.*$/, ''))
        .join('\n');
    for (const dead of ['showPersonDetail', 'updateLegend', 'getPolarPosition',
                        'resizeCanvas', 'drawPeopleView', 'drawYouNode', 'getItemAtPosition']) {
        assert.ok(!new RegExp('\\b' + dead + '\\s*\\(').test(code),
            `${dead}() is removed but still called — that is a ReferenceError at click time`);
    }
});

check('repoint targets exist and their DOM ids are present', () => {
    assert.ok(/function\s+dpOpenCard\s*\(/.test(mainBlock), 'dpOpenCard must survive');
    assert.ok(/function\s+renderCardGrid\s*\(/.test(mainBlock), 'renderCardGrid must survive');
    // ids the live detail path writes to — verified present in the markup
    for (const id of ['detailPanel', 'detailIcon', 'detailName', 'detailSubtitle',
                      'dp-tab-timeline', 'dp-timeline-content', 'people-grid']) {
        assert.ok(html.includes(`id="${id}"`), `#${id} must exist for the live path to render`);
    }
});

check('filter chips refresh the card grid, not just the sidebar', () => {
    const fn = mainBlock.match(/function toggleFilter[\s\S]*?\n {8}\}/);
    assert.ok(fn, 'toggleFilter must exist');
    assert.ok(/renderCardGrid\(\)/.test(fn[0]),
        'a filter change must repaint the grid or it shows the previous selection');
});

check('the main block executes against a DOM stub without throwing', () => {
    const noop = () => {};
    const mkEl = () => ({
        addEventListener: noop, removeEventListener: noop,
        querySelector: () => null, querySelectorAll: () => [],
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        setAttribute: noop, getAttribute: () => null, appendChild: noop, remove: noop,
        style: {}, dataset: {}, textContent: '', innerHTML: '', value: '', focus: noop
    });
    const sandbox = {
        console: { log: noop, warn: noop, error: noop, info: noop },
        document: {
            addEventListener: noop, removeEventListener: noop,
            querySelector: () => mkEl(), querySelectorAll: () => [],
            getElementById: () => mkEl(), createElement: () => mkEl(),
            body: mkEl(), documentElement: mkEl(), head: mkEl(), readyState: 'loading'
        },
        localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        location: { href: 'http://localhost/people.html', pathname: '/people.html',
                    protocol: 'http:', host: 'localhost', search: '' },
        navigator: { onLine: true, userAgent: 'node' },
        fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                              clone() { return this; }, json: async () => ({}), text: async () => '{}' }),
        setTimeout: () => 0, clearTimeout: noop, setInterval: () => 0, clearInterval: noop,
        requestAnimationFrame: () => 0, cancelAnimationFrame: noop,
        alert: noop, confirm: () => false, prompt: () => null,
        apiRequest: async () => ({}), showNotification: noop,
        Promise, JSON, Date, Math, Array, Object, String, Number, Boolean, Error,
        URL, URLSearchParams, encodeURIComponent, decodeURIComponent, isNaN, parseInt, parseFloat
    };
    sandbox.addEventListener = noop;      // the page calls window.addEventListener
    sandbox.removeEventListener = noop;
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    // This is the assertion that matters: before the fix this threw
    // "TypeError: Cannot read properties of null (reading 'getContext')".
    vm.runInContext(mainBlock, sandbox, { filename: 'people.html#main' });
    assert.strictEqual(typeof sandbox.dpOpenCard, 'function',
        'dpOpenCard must be defined after the block runs — proof the block completed');
    assert.strictEqual(typeof sandbox.renderCardGrid, 'function');
    assert.strictEqual(typeof sandbox.loadPeopleAndRender, 'function');
});

console.log(`\n${passed} checks passed`);
