#!/usr/bin/env node
/**
 * Behavioural harness for "What Zoe knows about you" on the desktop / phone Memories page (js/memories-about-me.js).
 *
 * Runs the REAL module (or $ABOUT_ME_SRC, for negative controls) against a minimal DOM shim that refuses innerHTML and a fake
 * server. Clicks run the real handlers, so each check proves the request the page actually sends: method, path and body.
 * Pins: flag off (actions 404) = nothing shown and nothing else asked; the list is the member's own rows with their STAGE and
 * WHERE THEY CAME FROM (only `came_from.quote` is ever drawn as words: another speaker's words that reach the page in any other
 * field are never rendered); "Not right" sends the typed words to the correct route and re-reads; Forget asks first, its SAFE
 * button holds the focus, and nothing is sent until the yes; an enforced mute is shown as muted with no dead mute/unmute button;
 * "Let go" lists retired rows with their reason, no actions; a 401 shows the sign-in line and no rows; private classes are
 * counts only. Every control is named (data-act + aria-label).
 *
 * Negative controls (services/zoe-data/tests/test_memories_about_me_harness.py runs each against a mutated copy and requires red):
 * stage chip removed; source line drawing a non-quote field; the safe-button focus removed; the Let go list dropped.
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

let focused = null;

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
    focus() { focused = this; }
    async click() { for (const fn of this.listeners.click || []) await fn({ target: this }); }
    all(pred, out = []) { if (pred(this)) out.push(this); this.children.forEach(c => c.all(pred, out)); return out; }
}

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const OTHERS_WORDS = 'Quillfeather said the ferry leaves at nine';
const VIEW = {
    groups: [
        { key: 'people', label: 'People', kind: 'people', more: 0,
          entries: [{ name: 'Robin', relationship: 'sister', label: 'Robin (sister)', facts: ['lives in Eastvale'] }] },
        { key: 'preferences', label: 'Preferences', kind: 'items', more: 0, items: [
            { id: 'row/1', text: 'You like dark roast coffee', source: 'told', provenance_label: 'You told me on 3 Oct',
              stage: 'told', stage_label: 'You told me',
              came_from: { quote: 'I like dark roast', channel: 'chat', channel_label: 'In chat', day: '3 Oct', kept: true } },
            { id: 'row-2', text: HOSTILE, source: 'inferred', provenance_label: 'I worked this out',
              stage: 'noticed', stage_label: 'I noticed',
              came_from: { quote: '', channel: '', channel_label: '', day: '4 Oct', kept: false } },
            // a pendant row: the source is another speaker's recording, so the server sends day + channel only. The legacy
            // field below is what a careless renderer could pick up: it must never reach the screen.
            { id: 'row-3', text: 'You keep the bike in the shed', source: 'told', provenance_label: 'You confirmed this',
              stage: 'confirmed', stage_label: 'Confirmed', muted: true, source_excerpt: OTHERS_WORDS,
              came_from: { quote: '', channel: 'pendant', channel_label: 'From your pendant', day: '5 Oct', kept: true } }] },
    ],
    private: { health: 2 }, unsure: 1, total: 4,
    let_go: [{ id: 'old-1', text: 'You like milky tea', when: '1 Oct', reason: 'replaced', reason_label: 'Replaced' },
             { id: 'old-2', text: 'You walk at six', when: '2 Oct', reason: '', reason_label: '' }],
};

function makeEnv(opts = {}) {
    focused = null;
    const ids = { aboutMe: new Node('section'), aboutMeOpen: new Node('button'), aboutMeNav: new Node('a') };
    ids.aboutMe.hidden = true;
    ids.aboutMeNav.hidden = true;
    ids.aboutMeOpen.hidden = true;
    const doc = { createElement: (t) => new Node(t), getElementById: (id) => ids[id] || null };
    const calls = [];
    let view = JSON.parse(JSON.stringify(VIEW));
    if (opts.legacy) view.groups[1].items.forEach((it) => { delete it.stage; delete it.stage_label; delete it.came_from; });
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
        assert(txt.includes('You like dark roast coffee') && txt.includes('You told me') && txt.includes('3 Oct'), 'row + provenance');
        assert(txt.includes(HOSTILE), 'hostile text shown as text');
        assert(txt.includes('I noticed'), 'inferred is marked');
        assert(txt.includes('2 private notes (health)') && !/migraine/i.test(txt), 'private = counts only');
        assert(e.ids.aboutMe.all(n => n.className && n.className.includes('am-soft')).length === 1, 'inferred row styled soft');
        assert.strictEqual(byText(e.ids.aboutMe, 'Forget').length, 3, 'a person has no Forget button here');
    });

    await check('Fix sends the typed words to the correct route, then re-reads', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Not right')[0].click();
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
        await byText(e.ids.aboutMe, 'Not right')[0].click();
        await byText(e.ids.aboutMe, 'Save')[0].click();
        assert.strictEqual(posts(e.calls).length, 0);
    });

    await check('a refused fix says why and keeps the row', async () => {
        const e = makeEnv({ hash: '#about-me', correctStatus: 409 });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Not right')[0].click();
        e.ids.aboutMe.all(n => n.tagName === 'TEXTAREA')[0].value = 'I like tea';
        await byText(e.ids.aboutMe, 'Save')[0].click();
        assert(e.ids.aboutMe.textContent.includes('I couldn’t change that one.'));
    });

    await check('each row shows its stage in plain words', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        const stages = e.ids.aboutMe.all(n => /(^| )am-stage( |$)/.test(n.className) && n.className.includes('am-stage-')).map(n => n._text);
        assert.deepStrictEqual(stages, ['You told me', 'I noticed', 'Confirmed']);
        assert.strictEqual(e.ids.aboutMe.all(n => n.className && n.className.includes('am-soft') && n.attrs['data-id']).length, 1,
            'only the "I noticed" row is drawn soft');
    });

    await check('where it came from: own words quoted, channel and day; no source says so', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        const quotes = e.ids.aboutMe.all(n => n.tagName === 'Q').map(n => n._text);
        assert.deepStrictEqual(quotes, ['I like dark roast'], 'only the owner quote is drawn as words');
        const txt = e.ids.aboutMe.textContent;
        assert(txt.includes('In chat · 3 Oct'), 'channel + day');
        assert(txt.includes('Source not kept · 4 Oct'), 'no source says so');
        assert(txt.includes('From your pendant · 5 Oct'), 'a source that is not theirs: day + channel only');
    });

    await check('another person\'s words are never rendered', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        assert(!e.ids.aboutMe.textContent.includes('Quillfeather'), 'a non-quote field reached the screen');
    });

    await check('an enforced mute shows as muted, with no dead mute or unmute button', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        const row = e.ids.aboutMe.all(n => n.attrs['data-id'] === 'row-3')[0];
        assert(row.className.includes('am-muted') && row.textContent.includes('Muted'));
        assert(row.textContent.includes('won’t bring this up'));
        for (const label of ['Don’t bring this up', "Don't bring this up", 'Unmute', 'Undo', 'Bring back'])
            assert.strictEqual(byText(e.ids.aboutMe, label).length, 0, label + ' has no route to serve it');
        const other = e.ids.aboutMe.all(n => n.attrs['data-id'] === 'row/1')[0];
        assert(!other.textContent.includes('Muted'));
    });

    await check('Let go lists retired rows with their reason and no actions', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        const box = e.ids.aboutMe.all(n => n.className && n.className.includes('am-letgo'))[0];
        assert(box, 'the Let go list is drawn');
        const t = box.textContent;
        assert(t.includes('Let go') && t.includes('You like milky tea') && t.includes('Replaced · 1 Oct'));
        assert(t.includes('You walk at six') && t.includes('2 Oct'), 'a row with no reason shows just its day');
        assert.strictEqual(box.all(n => n.tagName === 'BUTTON').length, 0, 'no bring-back or forget route serves these');
    });

    await check('every action button is named', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        const acts = e.ids.aboutMe.all(n => n.tagName === 'BUTTON' && n.attrs['data-act']);
        assert.deepStrictEqual([...new Set(acts.map(b => b.attrs['data-act']))].sort(), ['forget', 'not-right']);
        assert(acts.every(b => /^(Not right|Forget): ./.test(b.attrs['aria-label'] || '')), 'aria-label names the action and the row');
        assert(acts.every(b => b.attrs.type === 'button'));
    });

    await check('Forget asks first with the safe button focused, then removes the row after the yes', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Forget')[0].click();
        assert.strictEqual(posts(e.calls).length, 0, 'nothing sent before the yes');
        assert(e.ids.aboutMe.textContent.includes('Forget this? This can’t be undone.'));
        assert(focused && focused._text === 'Keep it' && focused.attrs['data-act'] === 'keep', 'the safe button holds the focus');
        await byText(e.ids.aboutMe, 'Yes, forget it')[0].click();
        const p = posts(e.calls);
        assert.strictEqual(p.length, 1);
        assert.strictEqual(p[0].url, '/api/memories/mine/row%2F1/forget');
        assert(!e.ids.aboutMe.textContent.includes('dark roast'), 'row removed');
    });

    await check('Keep it sends nothing and closes the question', async () => {
        const e = makeEnv({ hash: '#about-me' });
        await e.Z.init(e.doc);
        await byText(e.ids.aboutMe, 'Forget')[0].click();
        await byText(e.ids.aboutMe, 'Keep it')[0].click();
        assert.strictEqual(posts(e.calls).length, 0);
        assert.strictEqual(byText(e.ids.aboutMe, 'Yes, forget it').length, 0);
        assert(e.ids.aboutMe.textContent.includes('dark roast'));
    });

    await check('an older server with no stage or source still shows its provenance line', async () => {
        const e = makeEnv({ hash: '#about-me', legacy: true });
        await e.Z.init(e.doc);
        assert(e.ids.aboutMe.textContent.includes('You told me on 3 Oct'));
    });

    await check('no Not right button when corrections are off', async () => {
        const e = makeEnv({ hash: '#about-me', correct: false });
        await e.Z.init(e.doc);
        assert.strictEqual(byText(e.ids.aboutMe, 'Not right').length, 0);
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
