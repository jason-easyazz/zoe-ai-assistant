#!/usr/bin/env node
/**
 * Behavioural harness for the Pendant page (pendant.html + js/pendant-page.js).
 *
 * Runs the REAL js/pendant-page.js (or $PENDANT_SRC, for negative controls) against a minimal DOM shim and a fake server.
 * The shim refuses innerHTML (the page must build every node with textContent), and clicks run the real handlers, so each
 * check proves the request the page actually sends: method, path and body.
 *
 * Synthetic data only (no household names or transcripts).
 * Run: node services/zoe-ui/dist/test_pendant_page.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIST = __dirname;
const SRC = process.env.PENDANT_SRC || path.join(DIST, 'js', 'pendant-page.js');
let checks = 0;
const failures = [];

/* ── DOM shim ───────────────────────────────────────────── */
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
    fire(type) { for (const fn of this.listeners[type] || []) fn({ target: this }); }
    all(pred, out = []) { if (pred(this)) out.push(this); this.children.forEach(c => c.all(pred, out)); return out; }
    visible() { let n = this; while (n) { if (n.hidden) return false; n = n.parentNode; } return true; }
}

function makeDoc() {
    const ids = {};
    for (const id of ['pendantStatus', 'pendantUnknown', 'pendantRecordings', 'pendantKnown', 'pendantToast']) {
        ids[id] = new Node('div');
    }
    return { ids, createElement: (t) => new Node(t), getElementById: (id) => ids[id] || null };
}

/* ── fake server ────────────────────────────────────────── */
const HOSTILE = '<img src=x onerror="window.__pwned=1">';
function fixtures() {
    return {
        '/api/omi/health': { pendants: [{ label: 'Pendant', last_sync_at: new Date().toISOString(), battery: { pct: 80 },
            ring: { fill_pct: 10 }, warnings: [{ kind: 'battery_low' }] }] },
        '/api/omi/recordings?limit=40': { recordings: [
            { id: 'rec-new', started_at: '2026-10-18T10:00:00Z', duration_s: 120, status: 'done', speaker_state: 'tagged' },
            { id: 'rec-old', started_at: '2026-10-15T10:00:00Z', duration_s: 60, status: 'done', speaker_state: 'unverified' },
        ], transcription: { backlog: 2, transcribing: 0, deferred: true, reason: 'voice_busy' } },
        '/api/omi/voices': { voices: [{ id: 'vp-1', person_id: 'p-alex', name: 'Alex Example', status: 'provisional', segments: 3,
            recordings: 2 }], learning_enabled: true, feature_on: true, speaker_tag: 'shadow' },
        '/api/omi/voices/people': { people: [{ id: 'p-alex', name: 'Alex Example' }, { id: 'p-robin', name: 'Robin Sample' }] },
        '/api/omi/voices/unknown': { voices: [{ id: 'cl/1', name: 'Voice 3', segments: 4, recordings: 2, days: 1,
            samples: [{ segment_id: 's-9', recording_id: 'rec-new', start_ms: 0, end_ms: 3000, started_at: '2026-10-18T10:00:00Z',
                text: HOSTILE, has_audio: true }] }] },
        '/api/omi/recordings/rec-new': { id: 'rec-new', status: 'done', transcript: 'x', has_audio: true, segments: [
            { id: 'seg-1', start_ms: 0, end_ms: 2000, text: 'synthetic line one', label: 'someone_else', speaker: 'other',
              source: 'model', suggested_person_id: 'p-alex', tagged_person_id: null, not_person: null },
            { id: 'seg-2', start_ms: 2000, end_ms: 4000, text: HOSTILE, label: 'person', speaker: 'other', source: 'confirmed',
              tagged_person_id: 'p-robin', suggested_person_id: null, not_person: null },
        ] },
        '/api/omi/recordings/rec-old': { id: 'rec-old', status: 'done', transcript: 'synthetic joined transcript', has_audio: false,
            segments: [] },
    };
}

function makeEnv(opts = {}) {
    const doc = makeDoc();
    const calls = [];
    const fx = fixtures();
    const fetch = async (url, o = {}) => {
        const method = (o.method || 'GET').toUpperCase();
        calls.push({ method, url, body: o.body === undefined ? undefined : JSON.parse(o.body) });
        if (opts.status) return { ok: false, status: opts.status, json: async () => ({ detail: 'nope' }) };
        if (method === 'GET' && /\/clip$/.test(url)) {
            if (opts.clipStatus) return { ok: false, status: opts.clipStatus, json: async () => ({ detail: 'gone' }) };
            return { ok: true, status: 200, blob: async () => ({ type: 'audio/wav' }), json: async () => { throw new Error('binary'); } };
        }
        if (method === 'GET') {
            if (!(url in fx)) return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) };
            return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(fx[url])) };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true, learning: 'learning' }) };
    };
    const played = [];
    function Audio(src) { this.src = src; }
    Audio.prototype.play = async function () { played.push(this.src); };
    Audio.prototype.pause = function () {};
    const URL = { createObjectURL: (b) => 'blob:' + b.type, revokeObjectURL: () => {} };
    const win = { fetch, setTimeout: () => 0, Date, encodeURIComponent, JSON, Promise, String, Math, isNaN, isFinite, console, Audio, URL };
    win.window = win;
    const ctx = vm.createContext(win);
    vm.runInContext(fs.readFileSync(SRC, 'utf8'), ctx, { filename: 'pendant-page.js' });
    return { doc, calls, Z: win.ZoePendant, win, played };
}

const byText = (root, text) => root.all(n => n.tagName === 'BUTTON' && n._text === text && n.visible());
const posts = (calls) => calls.filter(c => c.method !== 'GET');

async function check(label, fn) {
    checks++;
    try {
        await fn();
    } catch (err) {
        failures.push(label + ': ' + (err && err.message ? err.message : String(err)));
        console.error('  FAIL ' + label + ' - ' + (err && err.message));
    }
}

(async () => {
    await check('renders status, voices to name, recordings and known voices without innerHTML', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        const s = e.doc.ids.pendantStatus.textContent;
        assert(/last synced just now/.test(s) && /battery 80%/.test(s) && /battery is low/.test(s), s);
        assert(/2 recording\(s\) waiting/.test(s) && /voice busy/.test(s), s);
        const u = e.doc.ids.pendantUnknown.textContent;
        assert(u.includes('Voice 3') && u.includes('Heard 4 time(s)'), u);
        assert(u.includes(HOSTILE), 'the hostile sample is shown as TEXT');
        assert(e.doc.ids.pendantRecordings.children.length === 2);
        assert(e.doc.ids.pendantKnown.textContent.includes('Alex Example'));
        assert(!e.win.__pwned);
    });

    await check('a voice: "That\'s me" posts /me for that voice (id encoded)', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, 'That\'s me')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'POST', url: '/api/omi/voices/unknown/cl%2F1/me', body: undefined });
    });

    await check('a voice: Name them -> typed NEW name posts {name}', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        await byText(e.doc.ids.pendantUnknown, 'Name them')[0].click();
        const input = e.doc.ids.pendantUnknown.all(n => n.tagName === 'INPUT')[0];
        input.value = '  Casey Synthetic ';
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, 'Save')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'POST', url: '/api/omi/voices/unknown/cl%2F1/tag', body: { name: 'Casey Synthetic' } });
    });

    await check('a voice: typing an existing person\'s name links them (person_id), never a duplicate', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        await byText(e.doc.ids.pendantUnknown, 'Name them')[0].click();
        e.doc.ids.pendantUnknown.all(n => n.tagName === 'INPUT')[0].value = 'robin sample';
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, 'Save')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0].body, { person_id: 'p-robin' });
    });

    await check('a voice: picking a person chip posts {person_id}; the filter narrows the chips', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        await byText(e.doc.ids.pendantUnknown, 'Name them')[0].click();
        const input = e.doc.ids.pendantUnknown.all(n => n.tagName === 'INPUT')[0];
        input.value = 'ale';
        input.fire('input');
        assert.strictEqual(byText(e.doc.ids.pendantUnknown, 'Robin Sample').length, 0);
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, 'Alex Example')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'POST', url: '/api/omi/voices/unknown/cl%2F1/tag', body: { person_id: 'p-alex' } });
    });

    await check('a voice: TV posts not-a-person {kind: tv}', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, 'TV')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'POST', url: '/api/omi/voices/unknown/cl%2F1/not-a-person', body: { kind: 'tv' } });
    });

    await check('a recording opens to its lines: labels, tagged name, suggestion, hostile text as text', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        const recs = e.doc.ids.pendantRecordings;
        await recs.children[0].children[0].click();
        await new Promise(r => setImmediate(r));
        const t = recs.children[0].textContent;
        assert(t.includes('synthetic line one') && t.includes('Someone else'), t);
        assert(t.includes('Robin Sample') && t.includes(HOSTILE), 'a tagged line shows the person\'s name and the text verbatim');
        assert(t.includes('Sounds like Alex Example?'), t);
    });

    await check('a line: Me / Not me / TV PATCH the segment; Yes answers the suggestion; Who? tags the line', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        const rec = e.doc.ids.pendantRecordings.children[0];
        await rec.children[0].click();
        await new Promise(r => setImmediate(r));
        const seg = () => rec.all(n => n.className === 'seg')[0];
        const press = async (text) => {
            e.calls.length = 0;
            await byText(seg(), text)[0].click();
            await new Promise(r => setImmediate(r));
            return posts(e.calls)[0];
        };
        assert.deepStrictEqual(await press('Me'), { method: 'PATCH', url: '/api/omi/segments/seg-1', body: { owner_label: 'you' } });
        assert.deepStrictEqual(await press('Not me'), { method: 'PATCH', url: '/api/omi/segments/seg-1', body: { owner_label: 'other' } });
        assert.deepStrictEqual(await press('TV / media'), { method: 'PATCH', url: '/api/omi/segments/seg-1', body: { not_person: 'tv' } });
        assert.deepStrictEqual(await press('Yes'), { method: 'POST', url: '/api/omi/segments/seg-1/suggestion', body: { answer: 'yes' } });
        assert.deepStrictEqual(await press('No'), { method: 'POST', url: '/api/omi/segments/seg-1/suggestion', body: { answer: 'no' } });
        await byText(seg(), 'Who?')[0].click();
        e.calls.length = 0;
        await byText(seg(), 'Alex Example')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'POST', url: '/api/omi/segments/seg-1/tag', body: { person_id: 'p-alex' } });
    });

    await check('Play fetches a clip of THAT line with the session (fetch, not <audio src>) and plays it', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        e.calls.length = 0;
        await byText(e.doc.ids.pendantUnknown, '▶ Play')[0].click();
        assert.deepStrictEqual(e.calls.map(c => c.method + ' ' + c.url), ['GET /api/omi/segments/s-9/clip']);
        assert.deepStrictEqual(e.played, ['blob:audio/wav']);
        const rec = e.doc.ids.pendantRecordings.children[0];
        await rec.children[0].click();
        await new Promise(r => setImmediate(r));
        e.calls.length = 0;
        await byText(rec.all(n => n.className === 'seg')[1], '▶ Play')[0].click();
        assert.deepStrictEqual(e.calls.map(c => c.url), ['/api/omi/segments/seg-2/clip']);
        const old = e.doc.ids.pendantRecordings.children[1];
        await old.children[0].click();
        await new Promise(r => setImmediate(r));
        assert.strictEqual(byText(old, '▶ Play').length, 0, 'no audio held -> no play button');
    });

    await check('a clip that is no longer kept says so and plays nothing', async () => {
        const e = makeEnv({ clipStatus: 404 });
        await e.Z.init(e.doc);
        await byText(e.doc.ids.pendantUnknown, '▶ Play')[0].click();
        assert.deepStrictEqual(e.played, []);
        assert(/no longer kept/.test(e.doc.ids.pendantToast.textContent), e.doc.ids.pendantToast.textContent);
    });

    await check('an old recording (no segments) says plainly it cannot be tagged and shows its transcript', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        const rec = e.doc.ids.pendantRecordings.children[1];
        await rec.children[0].click();
        await new Promise(r => setImmediate(r));
        const t = rec.textContent;
        assert(t.includes('cannot be tagged') && t.includes('synthetic joined transcript'), t);
        assert.strictEqual(byText(rec, 'Who?').length, 0);
    });

    await check('forget a learned voice asks first, then DELETEs it', async () => {
        const e = makeEnv();
        await e.Z.init(e.doc);
        e.calls.length = 0;
        await byText(e.doc.ids.pendantKnown, 'Forget this voice')[0].click();
        assert.strictEqual(posts(e.calls).length, 0, 'nothing is sent before the confirm');
        await byText(e.doc.ids.pendantKnown, 'Forget it')[0].click();
        assert.deepStrictEqual(posts(e.calls)[0], { method: 'DELETE', url: '/api/omi/voices/vp-1', body: undefined });
    });

    await check('403 (not the owner) and 404 (sync off) say so and render no data', async () => {
        for (const [status, text] of [[403, 'Only the pendant\'s owner'], [404, 'switched off']]) {
            const e = makeEnv({ status });
            await e.Z.init(e.doc);
            assert(e.doc.ids.pendantStatus.textContent.includes(text), status + ': ' + e.doc.ids.pendantStatus.textContent);
            assert.strictEqual(e.doc.ids.pendantRecordings.children.length, 0);
            assert.strictEqual(e.doc.ids.pendantUnknown.children.length, 0);
        }
    });

    await check('the page never routes into /touch/ and loads auth.js before the module', async () => {
        const html = fs.readFileSync(path.join(DIST, 'pendant.html'), 'utf8');
        const src = fs.readFileSync(SRC, 'utf8');
        assert(!/\/touch\//.test(html) && !/\/touch\//.test(src));
        const a = html.indexOf('/js/auth.js'), m = html.indexOf('/js/pendant-page.js');
        assert(a > 0 && m > a, 'auth.js must load before pendant-page.js');
        assert(!/innerHTML/.test(src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')), 'no innerHTML in the module');
    });

    if (failures.length) {
        console.error(failures.length + ' of ' + checks + ' checks FAILED');
        process.exit(1);
    }
    console.log(checks + ' checks passed');
})();
