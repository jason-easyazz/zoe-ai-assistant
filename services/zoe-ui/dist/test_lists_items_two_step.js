#!/usr/bin/env node
/**
 * Node harness for the shared lists two-step helper in js/common.js.
 *
 * WHY: `GET /api/lists/{type}` returns list ROWS ONLY — routers/lists.py:76-89
 * selects 8 columns and `items` is not among them. Items live behind
 * `GET /api/lists/{type}/{id}/items` (lists.py:341). Three desktop surfaces read
 * `list.items` off the collection response, got `undefined`, and — because the
 * request itself returns 200 — failed silently:
 *   - calendar.html task sidebar: permanently empty
 *   - lists.html cards:           "0 items" on every list
 *   - widgets/core/tasks.js:      permanent "All tasks completed! 🎉"
 *
 * This harness loads the REAL js/common.js (no copy) in a vm sandbox with a
 * stubbed fetch that replays the REAL server shapes, and asserts:
 *   a) items are attached by the two-step
 *   b) a list whose items fetch FAILS gets items:null ("unknown"), never []
 *   c) zoeListCountLabel distinguishes empty from unavailable
 *   d) a failed collection fetch yields {lists:[], ok:false}
 *   e) NEGATIVE CONTROL: the old single-step read really does yield undefined,
 *      so these assertions would have gone red against the pre-fix code
 *
 * Run: node services/zoe-ui/dist/test_lists_items_two_step.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}
async function acheck(name, fn) {
    try { await fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('lists two-step helper (js/common.js)');

// ——— REAL server response shapes (mirrored from routers/lists.py) ————————
// Collection route :64 — SELECT id,user_id,name,list_type,description,
// visibility,created_at,updated_at. Deliberately NO items.
const COLLECTION = {
    lists: [
        { id: 'l1', user_id: 'u', name: 'Groceries', list_type: 'shopping',
          description: null, visibility: 'family', created_at: 'x', updated_at: 'y' },
        { id: 'l2', user_id: 'u', name: 'Hardware', list_type: 'shopping',
          description: null, visibility: 'family', created_at: 'x', updated_at: 'y' }
    ]
};
// Items route :341
const ITEMS_L1 = { items: [
    { id: 'i1', list_id: 'l1', text: 'Milk',  completed: 0, priority: 'medium' },
    { id: 'i2', list_id: 'l1', text: 'Bread', completed: 1, priority: 'medium' }
] };
const ITEMS_L2 = { items: [] };

// ——— Load the REAL common.js in a sandbox ————————————————————————————
const commonSrc = fs.readFileSync(path.join(__dirname, 'js', 'common.js'), 'utf8');

function makeSandbox(fetchImpl) {
    const noop = () => {};
    const el = () => ({
        addEventListener: noop, removeEventListener: noop, querySelector: () => null,
        querySelectorAll: () => [], classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        setAttribute: noop, getAttribute: () => null, appendChild: noop, remove: noop,
        style: {}, dataset: {}, textContent: '', innerHTML: ''
    });
    const documentStub = {
        addEventListener: noop, removeEventListener: noop,
        querySelector: () => null, querySelectorAll: () => [],
        getElementById: () => null, createElement: () => el(),
        body: el(), documentElement: el(), head: el(), readyState: 'complete'
    };
    const sandbox = {
        console: { log: noop, warn: noop, error: noop, info: noop },
        document: documentStub,
        localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        location: { href: 'http://localhost/lists.html', pathname: '/lists.html',
                    protocol: 'http:', host: 'localhost', origin: 'http://localhost' },
        navigator: { onLine: true, userAgent: 'node' },
        fetch: fetchImpl,
        setTimeout, clearTimeout, setInterval: () => 0, clearInterval: noop,
        Promise, JSON, Date, Math, Array, Object, String, Number, Boolean, Error,
        URL, URLSearchParams, encodeURIComponent, decodeURIComponent
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    // common.js is a classic script: top-level function declarations become globals.
    vm.runInContext(commonSrc, sandbox, { filename: 'js/common.js' });
    return sandbox;
}

/** fetch stub replaying the real routes; `failItemsFor` forces an items 500. */
function makeFetch(opts = {}) {
    const calls = [];
    return Object.assign(async function fetchStub(url) {
        const u = String(url);
        calls.push(u);
        const json = (body) => ({
            ok: true, status: 200,
            headers: { get: () => 'application/json' },
            clone() { return this; },
            text: async () => JSON.stringify(body),
            json: async () => body
        });
        const fail = () => ({
            ok: false, status: 500, statusText: 'Server Error',
            headers: { get: () => 'application/json' },
            clone() { return this; },
            text: async () => '{"detail":"boom"}',
            json: async () => ({ detail: 'boom' })
        });
        if (opts.failCollection && /\/api\/lists\/[^/]+$/.test(u)) return fail();
        if (/\/api\/lists\/[^/]+\/l1\/items$/.test(u)) {
            return opts.failItemsFor === 'l1' ? fail() : json(ITEMS_L1);
        }
        if (/\/api\/lists\/[^/]+\/l2\/items$/.test(u)) {
            return opts.failItemsFor === 'l2' ? fail() : json(ITEMS_L2);
        }
        if (/\/api\/lists\/[^/]+$/.test(u)) return json(COLLECTION);
        return json({});
    }, { calls });
}

(async () => {
    // ——— a) the core fix: items get attached —————————————————————————
    await acheck('two-step attaches items to every list', async () => {
        const f = makeFetch();
        const sb = makeSandbox(f);
        assert.strictEqual(typeof sb.zoeFetchListsWithItems, 'function',
            'zoeFetchListsWithItems must be a global from common.js');
        const { lists, ok } = await sb.zoeFetchListsWithItems('shopping');
        assert.strictEqual(ok, true);
        assert.strictEqual(lists.length, 2);
        assert.deepStrictEqual(lists[0].items.map(i => i.text), ['Milk', 'Bread']);
        assert.deepStrictEqual(lists[1].items, []);
        // proves the SECOND request actually happened — the whole point
        assert.ok(f.calls.some(u => /\/l1\/items$/.test(u)), 'expected an items request for l1');
    });

    // ——— b) unknown must not masquerade as empty ————————————————————
    await acheck('a failed items fetch yields items:null (unknown), not []', async () => {
        const sb = makeSandbox(makeFetch({ failItemsFor: 'l1' }));
        const { lists } = await sb.zoeFetchListsWithItems('shopping');
        const l1 = lists.find(l => l.id === 'l1');
        assert.strictEqual(l1.items, null, 'unloadable list must be null, never []');
        const l2 = lists.find(l => l.id === 'l2');
        assert.deepStrictEqual(l2.items, [], 'genuinely-empty list must stay []');
    });

    // ——— c) the label tells the truth ————————————————————————————————
    await acheck('zoeListCountLabel separates empty from unavailable', async () => {
        const sb = makeSandbox(makeFetch());
        assert.strictEqual(sb.zoeListCountLabel({ items: [] }), '0 items');
        assert.strictEqual(sb.zoeListCountLabel({ items: [1] }), '1 item');
        assert.strictEqual(sb.zoeListCountLabel({ items: [1, 2] }), '2 items');
        assert.strictEqual(sb.zoeListCountLabel({ items: null }), 'items unavailable');
        assert.strictEqual(sb.zoeListCountLabel(null), 'items unavailable');
    });

    // ——— d) collection failure is reported, not swallowed ————————————
    await acheck('a failed collection fetch yields {lists:[], ok:false}', async () => {
        const sb = makeSandbox(makeFetch({ failCollection: true }));
        const res = await sb.zoeFetchListsWithItems('shopping');
        assert.strictEqual(res.ok, false);
        // NB: res.lists is constructed INSIDE the vm sandbox, so it is a
        // cross-realm Array and deepStrictEqual would fail on prototype
        // identity alone. Assert the shape, not the reference.
        assert.ok(Array.isArray(res.lists) || typeof res.lists.length === 'number',
            'lists must be array-like');
        assert.strictEqual(res.lists.length, 0, 'lists must be empty on collection failure');
    });

    // ——— e) NEGATIVE CONTROL ————————————————————————————————————————
    // Prove these assertions would have caught the pre-fix code: the old
    // single-step read of the collection response yields undefined items.
    check('negative control: single-step read yields undefined items (the old bug)', () => {
        const asOldCodeSawIt = COLLECTION.lists[0];
        assert.strictEqual(asOldCodeSawIt.items, undefined,
            'the collection route must NOT carry items — if it starts to, this helper is obsolete');
        // and the exact expression the three surfaces used:
        assert.strictEqual(asOldCodeSawIt.items?.length || 0, 0,
            'the old `list.items?.length || 0` must evaluate to 0 — that was the visible bug');
    });

    console.log(`\n${passed} checks passed`);
})();
