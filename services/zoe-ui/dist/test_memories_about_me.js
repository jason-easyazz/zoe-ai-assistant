#!/usr/bin/env node
/**
 * Behavioural harness for "What Zoe knows about you" on the desktop / phone Memories page (js/memories-about-me.js).
 *
 * Runs the REAL module (or $ABOUT_ME_SRC, for negative controls) against a minimal DOM shim that refuses innerHTML and a fake
 * server. Clicks run the real handlers, so each check proves the request the page actually sends: method, path and body.
 * Pins: flag off (actions 404) = nothing shown and nothing else asked; the list is the member's own rows with provenance; Fix
 * sends the typed words to the correct route and re-reads; Forget asks first and sends nothing until the yes; a 401 shows the
 * sign-in line and no rows; private classes are counts only.
 *
 * Synthetic data only. Run: node services/zoe-ui/dist/test_memories_about_me.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = process.env.ABOUT_ME_SRC || path.join(__dirname, 'js', 'memories-about-me.js');
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
    removeChild(c) {
        const i = this.children.indexOf(c);
        if (i < 0) throw new Error('removeChild: not a child');
        this.children.splice(i, 1);
        c.parentNode = null;
        return c;
    }
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
const VIEW = {
    groups: [
        { key: 'people', label: 'People', kind: 'people', more: 0,
          entries: [{ name: 'Robin', relationship: 'sister', label: 'Robin (sister)', facts: ['lives in Eastvale'] }] },
        { key: 'preferences', label: 'Preferences', kind: 'items', more: 0, items: [
            { id: 'row/1', text: 'You like dark roast coffee', source: 'told', provenance_label: 'You told me on 3 Oct' },
            { id: 'row-2', text: HOSTILE, source: 'inferred', provenance_label: 'I worked this out' }] },
    ],
    private: { health: 2 }, unsure: 1, total: 3,
};

function makeEnv(opts = {}) {
    const ids = { aboutMe: new Node('section'), aboutMeOpen: new Node('button'), aboutMeNav: new Node('a') };
    ids.aboutMe.hidden = true;
    ids.aboutMeNav.hidden = true;
    ids.aboutMeOpen.hidden = true;
    const doc = { createElement: (t) => new Node(t), getElementById: (id) => ids[id] || null };
    const calls = [];
    let view = JSON.parse(JSON.stringify(VIEW));
    const fetch = async (url, o = {}) => {
        const method = (o.method || 'GET').toUpperCase();
        calls.push({ method, url, body: o.body === undefined ? undefined : JSON.parse(o.body) });
        const reply = (status, json) => ({ ok: status >= 200 && status < 300, status, json: async () => json });
        if (url === '/api/memories/mine/actions') return opts.actions === 404 ? reply(404, { detail: 'Not Found' }) : reply(200, { forget: opts.forget !== false, correct: opts.correct !== false });
        if (url === '/api/memories/mine') return opts.mine ? reply(opts.mine, { detail: 'Sign in' }) : reply(200, view);
        if (/\/correct$/.test(url)) {
            view.groups[1].items[0].text = 'You like oat milk';
            return opts.correctStatus ? reply(opts.correctStatus, { detail: 'I couldn’t change that one.' }) : reply(200, { corrected: true });
        }
        if (/\/forget$/.test(url)) return reply(200, { forgotten: true });
        return reply(404, { detail: 'not found' });
    };
    const listeners = [];
    const location = { hash: opts.hash || '', pathname: '/memories.html', search: '' };
    const replaced = [];
    const history = { replaceState: (_s, _t, url) => { replaced.push(url); location.hash = ''; } };   // a real replaceState drops the fragment
    const addEventListener = (type, fn) => listeners.push({ type, fn });
    const win = { fetch, location, history, addEventListener, encodeURIComponent, JSON, Promise, String, Number, Object, Array };
    win.window = win;
    vm.runInContext(fs.readFileSync(SRC, 'utf8'), vm.createContext(win), { filename: 'memories-about-me.js' });
    // the phone menu's link: sets the fragment, and the browser fires hashchange only if the fragment actually changed
    const tapNav = async (hash) => {
        if (location.hash === hash) return false;
        location.hash = hash;
        listeners.filter(l => l.type === 'hashchange').forEach(l => l.fn());
        await new Promise(r => setTimeout(r, 0));
        return true;
    };
    return { ids, doc, calls, replaced, location, tapNav, Z: win.ZoeAboutMe };
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

(async () => {
    await check('flag off: nothing shown, nothing else asked', async () => {
        const e = makeEnv({ actions: 404, hash: '#about-me' });
        assert.strictEqual(await e.Z.init(e.doc), false);
        assert.strictEqual(e.ids.aboutMeOpen.hidden, true);
        assert.strictEqual(e.ids.aboutMeNav.hidden, true, 'the phone menu link stays hidden while the flag is off');
        assert.strictEqual(e.ids.aboutMe.hidden, true);
        assert.deepStrictEqual(e.calls.map(c => c.url), ['/api/memories/mine/actions']);
    });

    await check('the QR link (#about-me) opens the list with provenance, text only', async () => {
        const e = makeEnv({ hash: '#about-me' });
        assert.strictEqual(await e.Z.init(e.doc), true);
        assert.strictEqual(e.ids.aboutMeOpen.hidden, false);
        assert.strictEqual(e.ids.aboutMeNav.hidden, false, 'the phone menu link appears once the server offers the list');
        assert.strictEqual(e.ids.aboutMe.hidden, false);
        const txt = e.ids.aboutMe.textContent;
        assert(txt.includes('Robin (sister)') && txt.includes('lives in Eastvale'), 'people');
        assert(txt.includes('You like dark roast coffee') && txt.includes('You told me on 3 Oct'), 'row + provenance');
        assert(txt.includes(HOSTILE), 'hostile text shown as text');
        assert(txt.includes('I worked this out'), 'inferred is marked');
        assert(txt.includes('2 private notes (health)') && !/migraine/i.test(txt), 'private = counts only');
        assert(e.ids.aboutMe.all(n => n.className && n.className.includes('am-soft')).length === 1, 'inferred row styled soft');
        assert.strictEqual(byText(e.ids.aboutMe, 'Forget').length, 2, 'a person has no Forget button here');
    });

    await check('Fix sends the typed words to the correct route, then re-reads', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Fix this')[0].click();
        const input = e.ids.aboutMe.all(n => n.tagName === 'TEXTAREA')[0];
        assert(input, 'typing is fine on the phone');
        input.value = '  I like oat milk  ';
        await byText(e.ids.aboutMe, 'Save')[0].click();
        const p = posts(e.calls);
        assert.strictEqual(p.length, 1);
        assert.strictEqual(p[0].url, '/api/memories/mine/row%2F1/correct', 'the row id is path-encoded');
        assert.deepStrictEqual(p[0].body, { text: 'I like oat milk' });
        assert(e.ids.aboutMe.textContent.includes('You like oat milk'), 'the list is re-read from the server');
    });

    await check('an empty fix sends nothing', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Fix this')[0].click();
        await byText(e.ids.aboutMe, 'Save')[0].click();
        assert.strictEqual(posts(e.calls).length, 0);
    });

    await check('a refused fix says why and keeps the row', async () => {
        const e = makeEnv({ hash: '#about-me', correctStatus: 409 });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Fix this')[0].click();
        e.ids.aboutMe.all(n => n.tagName === 'TEXTAREA')[0].value = 'I like tea';
        await byText(e.ids.aboutMe, 'Save')[0].click();
        assert(e.ids.aboutMe.textContent.includes('I couldn’t change that one.'));
    });

    await check('Forget asks first, then removes the row after the server said yes', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Forget')[0].click();
        assert.strictEqual(posts(e.calls).length, 0, 'nothing sent before the yes');
        await byText(e.ids.aboutMe, 'Yes, forget it')[0].click();
        const p = posts(e.calls);
        assert.strictEqual(p.length, 1);
        assert.strictEqual(p[0].url, '/api/memories/mine/row%2F1/forget');
        assert(!e.ids.aboutMe.textContent.includes('dark roast'), 'row removed');
    });

    await check('no Fix button when corrections are off', async () => {
        const e = makeEnv({ hash: '#about-me', correct: false });
        await e.Z.init(e.doc);
        assert.strictEqual(byText(e.ids.aboutMe, 'Fix this').length, 0);
    });

    await check('no Forget button when the role may not review', async () => {
        const e = makeEnv({ hash: '#about-me', forget: false, correct: false });
        await e.Z.init(e.doc);
        assert.strictEqual(byText(e.ids.aboutMe, 'Forget').length, 0);
        assert(e.ids.aboutMe.textContent.includes('dark roast'), 'the rows still show');
    });

    await check('signed out: the sign-in line and no rows', async () => {
        const e = makeEnv({ hash: '#about-me', mine: 401 });
        await e.Z.init(e.doc);
        const txt = e.ids.aboutMe.textContent;
        assert(txt.includes('Sign in to see what Zoe knows about you.'));
        assert(!txt.includes('dark roast') && !txt.includes('Robin'));
    });

    await check('close empties the section', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Close')[0].click();
        assert.strictEqual(e.ids.aboutMe.hidden, true);
        assert.strictEqual(e.ids.aboutMe.textContent, '');
    });

    await check('close drops the #about-me fragment so the menu link can reopen the section', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        assert.strictEqual(e.ids.aboutMe.hidden, false);
        await byText(e.ids.aboutMe, 'Close')[0].click();
        assert.strictEqual(e.location.hash, '', 'the fragment must be gone after close');
        assert.deepStrictEqual(e.replaced, ['/memories.html'], 'replaceState keeps the path, drops the fragment');
        assert.strictEqual(await e.tapNav('#about-me'), true, 'the second tap now changes the hash');
        assert.strictEqual(e.ids.aboutMe.hidden, false, 'and reopens the section');
    });

    await check('the page loads the module after auth.js and is gated', async () => {
        const html = fs.readFileSync(path.join(__dirname, 'memories.html'), 'utf8');
        const auth = html.indexOf('src="js/auth.js"');
        const mod = html.indexOf('/js/memories-about-me.js?v=');
        assert(auth > 0 && mod > auth, 'module after auth.js');
        assert(html.includes('id="aboutMe"') && html.includes('id="aboutMeOpen"'));
        assert(/id="aboutMeNav"[^>]*\shidden/.test(html), 'the phone menu deep link ships hidden');
        assert(html.includes('.mobile-nav-item[hidden]'), 'display:flex must not override hidden on the menu link');
    });

    if (failures.length) {
        console.error(failures.length + ' of ' + checks + ' checks FAILED');
        process.exit(1);
    }
    console.log('about me: all ' + checks + ' checks passed');
})();
