#!/usr/bin/env node
/**
 * Behavioural harness for caregiver curation on the Memories page (js/memories-curation.js, memories-space P7).
 *
 * Runs the REAL module (or $CURATION_SRC, for negative controls) against a DOM shim that REFUSES innerHTML and a fake server.
 * Clicks run the real handlers. Pins: flag off (/me 404) = nothing shown, nothing else asked; a non-curator never sees the family
 * part; the notes are text only (a hostile note is shown as text); Add sends text + category; Remove asks first and sends nothing
 * until the yes; a refusal (sensitive off) is said, not swallowed. Synthetic data only.
 * Run: node services/zoe-ui/dist/test_memories_curation.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = process.env.CURATION_SRC || path.join(__dirname, 'js', 'memories-curation.js');
let checks = 0;
const failures = [];

class Node {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attrs = {};
        this.listeners = {};
        this._text = '';
        this.className = '';
        this.hidden = false;
        this.value = '';
    }
    get firstChild() { return this.children[0] || null; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    set textContent(v) { this.children = []; this._text = String(v); }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set innerHTML(v) { throw new Error('innerHTML assigned: ' + String(v).slice(0, 60)); }
    get innerHTML() { throw new Error('innerHTML read'); }
    async click() { for (const fn of this.listeners.click || []) await fn({ target: this }); }
    all(pred, out = []) { if (pred(this)) out.push(this); this.children.forEach(c => c.all(pred, out)); return out; }
}

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const CATS = [{ key: 'routines', label: 'Routines', sensitive: false }, { key: 'school', label: 'School', sensitive: false },
              { key: 'health', label: 'Health', sensitive: true }];

function makeEnv(opts = {}) {
    const ids = { curation: new Node('section'), curationOpen: new Node('button') };
    ids.curation.hidden = true;
    ids.curationOpen.hidden = true;
    const doc = { createElement: (t) => new Node(t), getElementById: (id) => ids[id] || null };
    const calls = [];
    const rows = [{ id: 'row/1', text: 'Mia’s teacher is Ms Lee', category: 'school', added_by: 'Sam' },
                  { id: 'row-2', text: HOSTILE, category: 'routines', added_by: 'Sam' }];
    const fetch = async (url, o = {}) => {
        const method = (o.method || 'GET').toUpperCase();
        calls.push({ method, url, body: o.body === undefined ? undefined : JSON.parse(o.body) });
        const reply = (status, json) => ({ ok: status >= 200 && status < 300, status, json: async () => json });
        const base = '/api/memories/curation';
        if (url === base + '/me') return opts.me === 404 ? reply(404, { detail: 'Not Found' })
            : reply(200, { kind: 'adult', categories: CATS, on: [], editable: true });
        if (url === base + '/members') return opts.members === 403 ? reply(403, { detail: 'Only a grown-up household admin' })
            : reply(200, { members: [{ user_id: 'kid/1', name: 'Mia', kind: 'child', categories: CATS, sensitive_on: [],
                                          sensitive_switchable: ['health', 'medication', 'money'] }] });
        if (url === base + '/members/kid%2F1/rows' && method === 'GET') return reply(200, { rows, categories: CATS, held: 0 });
        if (url === base + '/members/kid%2F1/rows') return opts.addStatus ? reply(opts.addStatus, { detail: 'Health notes for Mia are off. Turn them on first.' })
            : reply(200, { id: 'row-3' });
        if (/\/forget$/.test(url)) { rows.splice(0, 1); return reply(200, { forgotten: true }); }
        return reply(404, { detail: 'not found' });
    };
    const win = { fetch, location: { hash: '' }, encodeURIComponent, JSON, Promise, String, Number, Object, Array };
    win.window = win;
    vm.runInContext(fs.readFileSync(SRC, 'utf8'), vm.createContext(win), { filename: 'memories-curation.js' });
    return { ids, doc, calls, Z: win.ZoeCuration };
}

const byText = (root, text) => root.all(n => n.tagName === 'BUTTON' && n._text === text);
const posts = (calls) => calls.filter(c => c.method !== 'GET');

async function check(label, fn) {
    checks++;
    try { await fn(); } catch (err) {
        failures.push(label + ': ' + (err && err.message ? err.message : String(err)));
        console.error('  FAIL ' + label + ' - ' + (err && err.message));
    }
}

async function openMia(e) {
    await e.Z.init(e.doc);
    await e.ids.curationOpen.click();
    await byText(e.ids.curation, 'Mia')[0].click();
}

(async () => {
    await check('flag off: nothing shown, nothing else asked', async () => {
        const e = makeEnv({ me: 404 });
        assert.strictEqual(await e.Z.init(e.doc), false);
        assert.strictEqual(e.ids.curationOpen.hidden, true);
        assert.strictEqual(e.ids.curation.hidden, true);
        assert.deepStrictEqual(e.calls.map(c => c.url), ['/api/memories/curation/me']);
    });

    await check('a non-curator sees only their own settings, never the family part', async () => {
        const e = makeEnv({ members: 403 });
        await e.Z.init(e.doc);
        await e.ids.curationOpen.click();
        const txt = e.ids.curation.textContent;
        assert(txt.includes('Help with my memories'), 'own settings');
        assert(!txt.includes('Memories for my family') && byText(e.ids.curation, 'Mia').length === 0, 'no family part');
    });

    await check('the notes for a child are text only, with who added them', async () => {
        const e = makeEnv();
        await openMia(e);
        const txt = e.ids.curation.textContent;
        assert(txt.includes('Mia’s teacher is Ms Lee') && txt.includes('added by Sam'), 'row + provenance');
        assert(txt.includes(HOSTILE), 'a hostile note is shown as text');
        assert.strictEqual(byText(e.ids.curation, 'Health: off').length, 1, 'sensitive switches for a child');
    });

    await check('Add sends the typed text and the category', async () => {
        const e = makeEnv();
        await openMia(e);
        const input = e.ids.curation.all(n => n.tagName === 'INPUT')[0];
        input.value = 'Mia’s swimming is on Tuesdays';
        e.ids.curation.all(n => n.tagName === 'SELECT')[0].value = 'routines';
        await byText(e.ids.curation, 'Add')[0].click();
        const p = posts(e.calls);
        assert.strictEqual(p.length, 1);
        assert.strictEqual(p[0].url, '/api/memories/curation/members/kid%2F1/rows', 'the member id is path-encoded');
        assert.deepStrictEqual(p[0].body, { text: 'Mia’s swimming is on Tuesdays', category: 'routines' });
    });

    await check('a refusal is said, not swallowed', async () => {
        const e = makeEnv({ addStatus: 403 });
        await openMia(e);
        e.ids.curation.all(n => n.tagName === 'INPUT')[0].value = 'Mia is allergic to peanuts';
        await byText(e.ids.curation, 'Add')[0].click();
        assert(e.ids.curation.textContent.includes('Health notes for Mia are off'));
    });

    await check('Remove asks first, then sends the audited forget', async () => {
        const e = makeEnv();
        await openMia(e);
        await byText(e.ids.curation, 'Remove')[0].click();
        assert.strictEqual(posts(e.calls).length, 0, 'nothing sent before the yes');
        await byText(e.ids.curation, 'Yes, remove')[0].click();
        const p = posts(e.calls);
        assert.strictEqual(p.length, 1);
        assert.strictEqual(p[0].url, '/api/memories/curation/members/kid%2F1/rows/row%2F1/forget');
        assert(!e.ids.curation.textContent.includes('Ms Lee'), 'the list is re-read');
    });

    await check('close empties the section', async () => {
        const e = makeEnv();
        await openMia(e);
        await byText(e.ids.curation, 'Close')[0].click();
        assert.strictEqual(e.ids.curation.hidden, true);
        assert.strictEqual(e.ids.curation.textContent, '');
    });

    await check('the page loads the module after auth.js', async () => {
        const html = fs.readFileSync(path.join(__dirname, 'memories.html'), 'utf8');
        const auth = html.indexOf('src="js/auth.js"');
        const mod = html.indexOf('/js/memories-curation.js?v=');
        assert(auth > 0 && mod > auth, 'module after auth.js');
        assert(html.includes('id="curation"') && html.includes('id="curationOpen"'));
    });

    if (failures.length) {
        console.error(failures.length + ' of ' + checks + ' checks FAILED');
        process.exit(1);
    }
    console.log('curation: all ' + checks + ' checks passed');
})();
