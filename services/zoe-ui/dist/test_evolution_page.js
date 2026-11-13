#!/usr/bin/env node
/**
 * Behavioural harness for the decide page (evolution.html + js/evolution-page.js). Runs the REAL module (or $EVOLUTION_SRC, for
 * negative controls) against a DOM shim that refuses innerHTML and a fake server; clicks run the real handlers, so each check
 * proves the request the page sends. Invented data only. Run: node services/zoe-ui/dist/test_evolution_page.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIST = __dirname;
const SRC = process.env.EVOLUTION_SRC || path.join(DIST, 'js', 'evolution-page.js');
let checks = 0;
const failures = [];

class Node {
    constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.parentNode = null; this.attrs = {};
        this.listeners = {}; this._text = ''; this.className = ''; this.disabled = false; }
    get firstChild() { return this.children[0] || null; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k]; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    set textContent(v) { this.children = []; this._text = String(v); }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set innerHTML(v) { throw new Error('innerHTML assigned: ' + String(v).slice(0, 60)); }
    get innerHTML() { throw new Error('innerHTML read'); }
    async click() { for (const fn of this.listeners.click || []) await fn({ target: this }); }
    all(pred, out = []) { if (pred(this)) out.push(this); this.children.forEach(c => c.all(pred, out)); return out; }
}

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
function inbox() {
    return {
        cards: [
            { card: 'q-1', source: 'queue', ids: ['p-a', 'p-b'], count: 2, noticed: HOSTILE, first_seen: '2026-11-01', last_seen: '2026-11-10',
              evidence: { proposals: 2, signal_count: 7 }, proposes: 'A code change to intent_router (risk tier 3).', approved: false,
              next: 'Today a yes only records your decision.', actions: ['approve', 'reject', 'later'] },
            { card: 't-weather', source: 'failed_turns', ids: [], count: 3, noticed: '3 of your own requests went to the wrong place',
              evidence: { turns: 3 }, proposes: 'Learn from these turns.', next: 'not built yet', actions: [] },
        ],
        more_cards: 0, open: 9, stale: { count: 7, days: 30 }, later: 1,
        decided: [{ id: 'p-z', component: 'router', decision: 'approved', status: 'kept', measured: { result: 'better' }, at: '2026-11-02' }],
        approval_does: 'Today a yes only records your decision. Nothing is changed.',
    };
}

function setup() {
    const ids = {};
    for (const id of ['evoCards', 'evoStale', 'evoPast', 'evoFooter', 'evoToast']) ids[id] = new Node('div');
    const doc = { ids, createElement: (t) => new Node(t), getElementById: (id) => ids[id] || null };
    const sent = [];
    const win = {
        setTimeout: () => 0,
        fetch: async (url, opts) => {
            sent.push({ url, method: opts.method, body: opts.body ? JSON.parse(opts.body) : undefined });
            const out = url.endsWith('/inbox') ? inbox() : url.endsWith('/decide') ? { changed: 2, unchanged: 0 } : { dismissed: 7 };
            return { ok: true, status: 200, json: async () => out };
        },
    };
    vm.runInNewContext(fs.readFileSync(SRC, 'utf8'), { window: win, globalThis: win, JSON, Object, Number, String });
    return { doc, win, sent };
}

async function check(name, fn) {
    try { await fn(); checks++; } catch (e) { failures.push(name + ': ' + e.message); }
}

const buttons = (n) => n.all(x => x.tagName === 'BUTTON');

(async () => {
    await check('renders server text as text, never markup', async () => {
        const { doc, win } = setup();
        await win.ZoeEvolution.init(doc);
        assert(doc.ids.evoCards.textContent.includes(HOSTILE));
        assert(!win.__pwned);
        assert(doc.ids.evoFooter.textContent.includes('Nothing is changed'));
        assert(doc.ids.evoPast.textContent.includes('measured: better'));
    });
    for (const [action, label] of [['approve', 'Yes, do it'], ['reject', 'No'], ['later', 'Later']]) {
        await check('the ' + action + ' button posts one verdict for every id of its card', async () => {
            const { doc, win, sent } = setup();
            await win.ZoeEvolution.init(doc);
            const b = buttons(doc.ids.evoCards).find(x => x.getAttribute('data-action') === action);
            assert(b && b.textContent === label, 'button ' + action);
            await b.click();
            const post = sent.find(s => s.method === 'POST');
            assert.strictEqual(post.url, '/api/system/evolution/decide');
            assert.deepStrictEqual(post.body, { ids: ['p-a', 'p-b'], action });
        });
    }
    await check('a card without actions has no buttons (failed turns are labelled with the words, elsewhere)', async () => {
        const { doc, win } = setup();
        await win.ZoeEvolution.init(doc);
        assert.strictEqual(buttons(doc.ids.evoCards).length, 3);
    });
    await check('dismiss old posts the stale window it was shown', async () => {
        const { doc, win, sent } = setup();
        await win.ZoeEvolution.init(doc);
        assert(doc.ids.evoStale.textContent.includes('7 old proposals hidden'));
        await buttons(doc.ids.evoStale)[0].click();
        const post = sent.find(s => s.method === 'POST');
        assert.strictEqual(post.url, '/api/system/evolution/dismiss-stale');
        assert.deepStrictEqual(post.body, { stale_days: 30 });
    });
    await check('the page is desktop only: gated, no touch link, no inline handler; Settings links it', async () => {
        const html = fs.readFileSync(path.join(DIST, 'evolution.html'), 'utf8');
        assert(html.includes('<script src="/js/auth.js"></script>') && html.includes('/js/evolution-page.js?v='));
        assert(!/touch\//.test(html) && !/\son[a-z]+=/i.test(html));
        assert(!/\.innerHTML\s*=|\son[a-z]+=|touch\//.test(fs.readFileSync(SRC, 'utf8')));
        assert(fs.readFileSync(path.join(DIST, 'settings.html'), 'utf8').includes('href="evolution.html"'));
        for (const f of fs.readdirSync(path.join(DIST, 'touch')).filter(f => f.endsWith('.html'))) {
            assert(!fs.readFileSync(path.join(DIST, 'touch', f), 'utf8').includes('evolution.html'), 'touch/' + f + ' links the page');
        }
    });

    if (failures.length) {
        console.error(failures.join('\n'));
        console.error(failures.length + ' failed, ' + checks + ' checks passed');
        process.exit(1);
    }
    console.log(checks + ' checks passed');
})();
