#!/usr/bin/env node
/**
 * Node harness: desktop pages must NOT register themselves as touch panels.
 *
 * touch-ui-executor.js has no pathname guard. On every desktop page that loaded
 * it, init() minted a fake `panel_<random>` id into localStorage, POSTed
 * /api/ui/panel/bind, started a 2s action poll and a 5s state sync, opened a
 * second /ws/push socket, asked the service worker to poll every 5s (a poll that
 * OUTLIVES the page), and redirected auth failures to /touch/index.html --
 * bouncing a desktop user into the kiosk login. Logged out, its sync timer was
 * never cleared.
 *
 * It was loaded by 8 desktop pages INCLUDING auth.html, the login page.
 *
 * The estate legitimately needs it, so this asserts the split, not deletion.
 *
 * Run: node services/zoe-ui/dist/test_desktop_no_panel_executor.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const D = __dirname;
const stripComments = (s) => s.replace(/<!--[\s\S]*?-->/g, '');
const isScriptLoad = (s) => /<script[^>]*src="[^"]*touch-ui-executor\.js/.test(stripComments(s));

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('desktop pages do not register as touch panels');

check('NO desktop page loads touch-ui-executor.js', () => {
    const offenders = fs.readdirSync(D)
        .filter(f => f.endsWith('.html'))
        .filter(f => isScriptLoad(fs.readFileSync(path.join(D, f), 'utf8')));
    assert.deepStrictEqual(offenders, [],
        `these desktop pages still register as panels: ${offenders.join(', ')}`);
});

check('auth.html in particular does not (it is the login page)', () => {
    const s = fs.readFileSync(path.join(D, 'auth.html'), 'utf8');
    assert.ok(!isScriptLoad(s),
        'the login page must never bind itself as a physical panel');
});

check('the ESTATE still loads it — this is a split, not a deletion', () => {
    const home = fs.readFileSync(path.join(D, 'touch', 'home.html'), 'utf8');
    assert.ok(isScriptLoad(home),
        'touch/home.html depends on the executor for voice actions; it must keep it');
});

check('lists.html no longer swallows its inline JS behind a src attribute', () => {
    const s = fs.readFileSync(path.join(D, 'lists.html'), 'utf8');
    // A <script src=...> with content before its close silently discards that
    // content. That is how 114 lines here never ran -- and why activating them
    // would have shipped the unescaped ${notification.message} sink they carry.
    const bad = [...s.matchAll(/<script[^>]*\bsrc=[^>]*>([\s\S]*?)<\/script>/gi)]
        .filter(m => m[1].trim().length > 0);
    assert.strictEqual(bad.length, 0,
        `${bad.length} <script src> tag(s) still swallow inline code`);
    // stripComments: my own removal note quotes the sink, and a naive match
    // would flag the description of the fix as the bug.
    assert.ok(!/\$\{notification\.message\}/.test(stripComments(s)),
        'the unescaped notification sink must be gone from CODE, not merely dormant');
});

check('common.js tells the SW to stop any stray panel poll', () => {
    const s = fs.readFileSync(path.join(D, 'js', 'common.js'), 'utf8');
    assert.ok(/STOP_PANEL_POLL/.test(s),
        'browsers poisoned before this change keep polling; nothing else stops them');
    assert.ok(/startsWith\('\/touch\/'\)/.test(s),
        'the panel itself must be exempt — it legitimately polls');
});

check('the SW still understands STOP_PANEL_POLL', () => {
    const sw = fs.readFileSync(path.join(D, 'sw.js'), 'utf8');
    assert.ok(/STOP_PANEL_POLL/.test(sw), 'the message handler must exist for the cleanup to work');
});

console.log(`\n${passed} checks passed`);
