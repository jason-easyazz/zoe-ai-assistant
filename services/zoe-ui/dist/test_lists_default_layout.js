#!/usr/bin/env node
/**
 * Node harness: the lists page must default to actual LIST widgets.
 *
 * Reported symptom (Jason, 2026-07-20): "my lists aren't listed in list".
 * Reproduced in a real browser: the grid held exactly one widget — "📋 Project"
 * showing 0 — and the page made ZERO /api/lists calls.
 *
 * Root cause: createDefaultLayout() used
 *     WidgetManager.getAvailableWidgets('lists')
 * which filters on `w.lists === true`. In widget-manifest.json ONLY 'project'
 * carries that flag; shopping/personal/work/bucket/tasks have no `lists` key at
 * all. So it returned [project] — the dead wing whose backend is a stub
 * returning [] — and because that array was non-empty, the sensible
 * ['shopping','work','personal','reminders','bucket'] fallback never fired.
 *
 * Second half: createDefaultLayout() ends with saveLayout(), so the broken
 * default was PERSISTED. Fixing the default alone leaves existing users stuck,
 * because their saved layout wins on the next load.
 *
 * Run: node services/zoe-ui/dist/test_lists_default_layout.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const D = __dirname;
const src = fs.readFileSync(path.join(D, 'js', 'lists-dashboard.js'), 'utf8');
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
const code = stripComments(src);

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('lists page defaults to real list widgets');

check('the manifest genuinely lacks lists:true on the list widgets', () => {
    // This is the upstream cause. If someone later fixes the manifest, this
    // assertion tells them the client-side guard is now belt-and-braces rather
    // than load-bearing — it should NOT be removed either way.
    const mf = path.join(D, 'js', 'widgets', 'widget-manifest.json');
    if (!fs.existsSync(mf)) return;   // untracked on fresh clones; not a failure
    const m = JSON.parse(fs.readFileSync(mf, 'utf8'));
    const flagged = m.widgets.filter(w => w.lists === true).map(w => w.id);
    assert.ok(!flagged.includes('shopping'),
        'manifest now flags shopping for lists — good, but keep the client filter');
});

check('createDefaultLayout filters to real list widgets', () => {
    // Anchor on the DEFINITION (4-space indent + brace), not the call site —
    // `this.createDefaultLayout();` matched first and yielded a 38-char slice.
    const fn = code.match(/^ {4}createDefaultLayout\(\) \{[\s\S]*?\n {4}\}/m);
    assert.ok(fn && fn[0].length > 200, 'createDefaultLayout definition must be matched');
    assert.ok(/isListWidget\(w\.id\)/.test(fn[0]),
        'the manifest result must be filtered through isListWidget');
    assert.ok(/w\.id !== 'project'/.test(fn[0]),
        "'project' must be excluded — its backend is a stub returning []");
    assert.ok(/fallback = \['shopping', 'work', 'personal', 'reminders', 'bucket'\]/.test(fn[0]),
        'the fallback list must remain');
});

check('a project-only saved layout gains lists WITHOUT losing anything', () => {
    assert.ok(/!layout\.some\(it => it && isListWidget\(it\.type\) && it\.type !== 'project'\)/.test(code),
        'a saved layout with no real list widget must be detected');
    // Must APPEND, not replace: a project-only layout is indistinguishable from
    // a deliberate customisation, so discarding it would destroy a valid choice.
    assert.ok(/layout\.push\(\{ type: t \}\)/.test(code),
        'the migration must APPEND defaults, never replace the saved layout');
    // Scope to the migration block itself: `let layout = null;` at the top of
    // loadLayout() is a legitimate declaration, and a file-wide match flagged it.
    const mig = code.match(/!layout\.some\(it =>[\s\S]*?\n {8}\}/);
    assert.ok(mig, 'migration block must be matched');
    assert.ok(!/layout = null/.test(mig[0]),
        'the saved layout must not be nulled out inside the migration — that discards user data');
    assert.ok(/\.prelists\.bak/.test(code),
        'back the old layout up regardless, as a recovery route');
});

check('the lists-dashboard script URL is cache-busted', () => {
    // lists-dashboard.js is served from the `zoe-js` NetworkFirst runtime cache
    // (sw.js), which SW_VERSION does NOT invalidate. Without a fresh URL an
    // existing client on a slow network keeps the stale script and the migration
    // never runs at all.
    const html = fs.readFileSync(path.join(D, 'lists.html'), 'utf8');
    assert.ok(/js\/lists-dashboard\.js\?v=/.test(html),
        'lists-dashboard.js must carry a ?v= or the runtime cache can serve the old copy');
});

// Behavioural: simulate the exact manifest shape and prove the outcome flips.
check('SIMULATION: manifest with only project:lists=true still yields list widgets', () => {
    const manifestWidgets = [
        { id: 'project', lists: true },
        { id: 'shopping' }, { id: 'personal' }, { id: 'work' }, { id: 'bucket' }, { id: 'tasks' },
    ];
    const LIST_TYPES = ['shopping','work','personal','bucket','project','reminders','tasks'];
    const isListWidget = (t) => LIST_TYPES.includes(t);

    // OLD behaviour (what shipped): take whatever the manifest flags.
    const oldDefaults = manifestWidgets.filter(w => w.lists === true).slice(0,5).map(w => ({type:w.id}));
    assert.deepStrictEqual(oldDefaults, [{type:'project'}],
        'sanity: the old path really did produce a project-only default');

    // NEW behaviour: filter to real lists, fall back when empty.
    let newDefaults = manifestWidgets
        .filter(w => w.lists === true)
        .filter(w => w && isListWidget(w.id) && w.id !== 'project')
        .slice(0,5).map(w => ({type:w.id}));
    if (newDefaults.length === 0) {
        newDefaults = ['shopping','work','personal','reminders','bucket'].map(t => ({type:t}));
    }
    assert.ok(newDefaults.length >= 4, 'the fallback must supply real list widgets');
    assert.ok(newDefaults.every(d => d.type !== 'project'), 'project must not be a default');
    assert.ok(newDefaults.some(d => d.type === 'shopping'), 'shopping must be among the defaults');
});

console.log(`\n${passed} checks passed`);
