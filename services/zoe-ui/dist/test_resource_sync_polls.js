#!/usr/bin/env node
/**
 * Node harness: ZoeWebSockets.init() must POLL, not open dead sockets.
 *
 * The six per-resource sockets
 * (/api/{lists,calendar,people,reminders,notes,journal}/ws/{user_id}) have never
 * connected for any user: connect() builds `${endpoint}/${userId}` with no query
 * string, while the server requires session_id from the query string or an
 * X-Session-ID header (main.py:2501) — and a browser cannot set headers on a
 * WebSocket. Every socket was closed 1008 immediately.
 *
 * The 'fallback' handlers meant to catch that were unreachable, because
 * maxReconnectAttempts defaults to 0 meaning "unlimited", so canRetry never
 * became false. Net: ~20 failed handshakes per 10s and NO data-change signal —
 * a voice-added event stayed invisible until a manual reload.
 *
 * Asserts init() polls (hidden-tab aware, refresh on focus) and opens nothing,
 * that initPush is untouched, and — the trap that bit me — that exactly ONE
 * init() definition exists. An earlier edit produced two; JS takes the second,
 * so the new code was dead while the broken code ran, and syntax checks passed.
 *
 * Run: node services/zoe-ui/dist/test_resource_sync_polls.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'js', 'websocket-sync.js'), 'utf8');

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('resource sync polls instead of opening dead sockets');

check('exactly ONE init(userId) definition', () => {
    const n = (src.match(/^    init\(userId\) \{/gm) || []).length;
    assert.strictEqual(n, 1,
        `${n} init() definitions — a duplicate makes the later one win and the other dead code`);
});

check('init() opens no WebSocket and schedules a poll', () => {
    const i = src.indexOf('    init(userId) {');
    const j = src.indexOf('    disconnect() {', i);   // the one AFTER init, not the class's
    assert.ok(i >= 0 && j > i, 'init/disconnect boundaries');
    const body = src.slice(i, j);
    assert.strictEqual((body.match(/new ZoeWebSocketSync/g) || []).length, 0,
        'init must not construct the sockets that never connect');
    assert.strictEqual((body.match(/\.connect\(\)/g) || []).length, 0,
        'init must not call connect()');
    assert.ok(/setInterval\(/.test(body), 'init must schedule a poll');
    assert.ok(/document\.hidden/.test(body), 'a hidden tab must not be polled');
    assert.ok(/visibilitychange/.test(body), 'returning to a tab must refresh immediately');
});

check('initPush is untouched — it is a DIFFERENT socket that works', () => {
    assert.ok(/params\.set\('session_id', sessionId\)/.test(src),
        'initPush sends session_id, which is why /ws/push authenticates');
    assert.ok(/initPush\(panelId, sessionId\)/.test(src), 'initPush must still exist');
});

check('disconnect() clears the poll timer', () => {
    const i = src.indexOf('    disconnect() {', src.indexOf('    init(userId) {'));
    const body = src.slice(i, i + 600);
    assert.ok(/clearInterval\(this\._pollTimer\)/.test(body),
        'disconnect must stop the poll or it survives teardown');
});

check('_refreshAll only calls page functions that exist', () => {
    const i = src.indexOf('    _refreshAll() {');
    const j = src.indexOf('    init(userId) {', i);
    const body = src.slice(i, j);
    for (const fn of ['loadEvents', 'loadPeople', 'loadReminders', 'loadNotes']) {
        assert.ok(new RegExp(`typeof ${fn} === 'function'`).test(body),
            `${fn} must be existence-guarded — this module loads on pages that lack it`);
    }
    assert.ok(/catch \(err\)/.test(body), 'a failing refresh must not kill the poll loop');

    // calendar.html's loadReminders takes (startDate,endDate) and loadEvents()
    // already re-runs it with the visible range. A bare call here re-fetches
    // with undefined bounds and wipes the ranged result.
    assert.ok(/typeof loadReminders === 'function' && typeof loadEvents !== 'function'/.test(body),
        'reminders must only refresh standalone where loadEvents is absent');

    // touch/journal.html stubs displayTimelineEntries to a no-op, so
    // loadJournalEntries fetches into nothing there; prefer the page-local loader.
    // Strip comments first: the explanation above names both functions, and a
    // raw indexOf matches the prose instead of the code. (Third time this trap
    // has appeared in this wave -- assertions must read CODE, not commentary.)
    const codeOnly = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    const li = codeOnly.indexOf("typeof loadEntries === 'function'");
    const lj = codeOnly.indexOf("typeof loadJournalEntries === 'function'");
    assert.ok(li !== -1 && lj !== -1, 'both journal loaders must be referenced');
    assert.ok(li < lj,
        'loadEntries (page-local) must be preferred: touch stubs the renderer loadJournalEntries uses');
});

// Behavioural: drive the real module against a stubbed DOM and prove a tick refreshes.
check('a poll tick calls the page refresh functions (live sim)', () => {
    let timerFn = null;
    let loadEventsCalls = 0;
    const listeners = {};
    const sandbox = {
        console: { log() {}, warn() {}, error() {} },
        document: {
            hidden: false,
            addEventListener: (evt, cb) => { listeners[evt] = cb; },
            getElementById: () => null, querySelector: () => null, querySelectorAll: () => []
        },
        location: { protocol: 'http:', host: 'localhost', pathname: '/calendar.html' },
        setInterval: (fn) => { timerFn = fn; return 1; },
        clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
        WebSocket: function () { throw new Error('init must not open a WebSocket'); },
        loadEvents: () => { loadEventsCalls++; },
        Promise, JSON, Date, Math, Array, Object, String, Number, Boolean, Error
    };
    sandbox.window = sandbox; sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox, { filename: 'websocket-sync.js' });

    sandbox.window.ZoeWebSockets.init('u1');
    assert.ok(timerFn, 'init must have scheduled a poll');
    assert.strictEqual(loadEventsCalls, 0, 'no refresh before the first tick');

    timerFn();
    assert.strictEqual(loadEventsCalls, 1, 'a tick must refresh the page');

    sandbox.document.hidden = true;
    timerFn();
    assert.strictEqual(loadEventsCalls, 1, 'a hidden tab must NOT be polled');

    sandbox.document.hidden = false;
    listeners['visibilitychange'] && listeners['visibilitychange']();
    assert.strictEqual(loadEventsCalls, 2, 'returning to the tab must refresh immediately');
});

console.log(`\n${passed} checks passed`);
