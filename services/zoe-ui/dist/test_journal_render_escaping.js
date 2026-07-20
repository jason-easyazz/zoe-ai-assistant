#!/usr/bin/env node
/**
 * Node harness for the stored-XSS fix in js/journal-api.js.
 *
 * Background: PR #895 added escaping to an inline renderer inside journal.html,
 * but that renderer (loadJournalEntriesInline) has ZERO callers — it is dead
 * code. The LIVE renderers are createTimelineEntry / displayOnThisDay /
 * displayPrompts / the journey card builders, all of which live in
 * js/journal-api.js and interpolated entry titles, content, tags, people names
 * and photo URLs straight into innerHTML. That is the path this harness covers.
 *
 * The escape helper must be defined INSIDE js/journal-api.js. The file is
 * loaded by both journal.html and touch/journal.html, and only the former
 * defines a page-level escapeHtml(); a cross-file reference would throw
 * ReferenceError on the touch panel and break journal rendering on the kiosk.
 * That is asserted explicitly below.
 *
 * Run: node services/zoe-ui/dist/test_journal_render_escaping.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const apiPath = path.join(__dirname, 'js', 'journal-api.js');
const source = fs.readFileSync(apiPath, 'utf8');
const desktopHtml = fs.readFileSync(path.join(__dirname, 'journal.html'), 'utf8');
const touchHtml = fs.readFileSync(path.join(__dirname, 'touch', 'journal.html'), 'utf8');

/**
 * Strip block comments and whole-line // comments so that the explanatory prose
 * in this fix cannot satisfy a source-level assertion. Only used for TEXT
 * matching; the vm below executes the original, unmodified source.
 */
function stripComments(js) {
    return js
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .split('\n')
        .filter(line => !/^\s*\/\//.test(line))
        .join('\n');
}
const code = stripComments(source);

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

/* ---------------------------------------------------------------- DOM shim */

function makeEl() {
    const el = {
        className: '', id: '', title: '', innerHTML: '',
        style: { cssText: '' },
        dataset: {},
        children: [],
        firstChild: null,
        appendChild() {}, remove() {}, insertBefore() {},
        insertAdjacentHTML(pos, html) { this.innerHTML += html; },
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; }
    };
    return el;
}

function runModule() {
    const timelineView = makeEl();
    const journeysView = makeEl();
    const navRight = makeEl();
    const byId = { timelineView, journeysView, navRight };

    const documentStub = {
        createElement: () => makeEl(),
        getElementById: id => byId[id] || null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {}
    };
    const windowStub = {};
    const sandbox = {
        window: windowStub,
        document: documentStub,
        console: { log() {}, warn() {}, error() {} },
        fetch: () => Promise.reject(new Error('no network in harness')),
        setTimeout() {}, clearTimeout() {},
        localStorage: { getItem: () => null, setItem() {} }
    };
    sandbox.globalThis = sandbox;
    const ctx = vm.createContext(sandbox);
    new vm.Script(source, { filename: 'journal-api.js' }).runInContext(ctx);
    return { ctx, timelineView, journeysView, navRight };
}

const XSS = '<img src=x onerror=alert(1)>';
const QUOTED = `" onmouseover="alert(1)`;

console.log('journal-api.js live-render escaping');

/* ------------------------------------------- the ReferenceError guard rail */

check('escape helper is DEFINED inside journal-api.js (not borrowed cross-file)', () => {
    assert.ok(/function\s+escapeJournalHtml\s*\(/.test(code),
        'journal-api.js must define its own escape helper in its own scope');
});

check('journal-api.js never calls the journal.html-only global escapeHtml()', () => {
    // touch/journal.html defines no escapeHtml -- calling it would throw
    // ReferenceError on the live kiosk panel and kill journal rendering.
    const bare = code.match(/(^|[^.\w])escapeHtml\s*\(/g) || [];
    assert.strictEqual(bare.length, 0,
        `journal-api.js must not reference the page-level escapeHtml(); found ${bare.length}`);
});

check('both consuming pages load journal-api.js (why the helper must be local)', () => {
    assert.ok(/journal-api\.js/.test(desktopHtml), 'journal.html loads journal-api.js');
    assert.ok(/journal-api\.js/.test(touchHtml), 'touch/journal.html loads journal-api.js');
    assert.ok(!/function\s+escapeHtml\s*\(/.test(touchHtml),
        'touch/journal.html defines no escapeHtml -- this is the ReferenceError risk');
});

check('the live renderers really are the journal-api.js ones', () => {
    assert.ok(/function\s+createTimelineEntry\s*\(/.test(code),
        'createTimelineEntry lives in journal-api.js');
    assert.ok(!/function\s+createTimelineEntry\s*\(/.test(desktopHtml),
        'journal.html must not shadow the renderer');
});

/* -------------------------------------------------- helper behaviour */

check('escapeJournalHtml escapes & < > " and the single quote', () => {
    const { ctx } = runModule();
    assert.strictEqual(ctx.escapeJournalHtml(`&<>"'`), '&amp;&lt;&gt;&quot;&#39;');
    assert.strictEqual(ctx.escapeJournalHtml(null), '');
    assert.strictEqual(ctx.escapeJournalHtml(undefined), '');
    assert.strictEqual(ctx.escapeJournalHtml(0), '0');
});

check('sanitizeJournalUrl neutralises script-bearing URL schemes', () => {
    const { ctx } = runModule();
    assert.strictEqual(ctx.sanitizeJournalUrl('javascript:alert(1)'), '');
    assert.strictEqual(ctx.sanitizeJournalUrl('  JaVaScRiPt:alert(1)'), '');
    assert.strictEqual(ctx.sanitizeJournalUrl('java\tscript:alert(1)'), '');
    assert.strictEqual(ctx.sanitizeJournalUrl('data:text/html,<script>'), '');
    assert.ok(ctx.sanitizeJournalUrl('/media/photo.jpg').startsWith('/media/'));
    assert.ok(ctx.sanitizeJournalUrl('https://x/y.png').startsWith('https://'));
    assert.strictEqual(ctx.sanitizeJournalUrl('data:image/png;base64,AAA'),
        'data:image/png;base64,AAA');
});

/* --------------------------------------------- end-to-end render behaviour */

check('createTimelineEntry escapes title, content, tags, people and places', () => {
    const { ctx } = runModule();
    const el = ctx.createTimelineEntry({
        id: 1,
        created_at: '2026-07-20T10:00:00Z',
        title: XSS,
        content: XSS,
        tags: [XSS],
        people: [{ name: XSS }],
        place_tags: [{ name: XSS }],
        privacy_level: XSS,
        photos: ['https://example.test/p.png']
    }, 0);
    const html = el.innerHTML;
    // The breakout condition is a RAW '<' or '"' surviving from the payload.
    // The literal text "onerror=" legitimately remains as inert escaped text,
    // so asserting on that substring alone would be wrong.
    assert.ok(!html.includes(XSS), 'the verbatim payload must not survive into innerHTML');
    assert.ok(!/<img\s+src=x/.test(html), 'no real <img> element may be constructed');
    assert.ok(!/<script/i.test(html), 'no <script> element may be constructed');
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'),
        'payload must appear fully escaped');
    // Every field fed the payload; each must have been escaped, not dropped.
    const escapedCount = (html.match(/&lt;img src=x/g) || []).length;
    assert.ok(escapedCount >= 5,
        `title/content/tag/person/place must each be escaped, saw ${escapedCount}`);
});

check('createTimelineEntry emits no inline onclick= for the entry id', () => {
    const { ctx } = runModule();
    const el = ctx.createTimelineEntry({
        id: `'),alert(1)//`,
        created_at: '2026-07-20T10:00:00Z',
        title: 'x', content: 'x'
    }, 0);
    assert.ok(!/onclick=/i.test(el.innerHTML),
        'entry id must be carried by a data attribute + bound listener, not inline JS');
    // The raw single quote is what would have closed the old onclick="openEntry('..')"
    // string; escaped to &#39; it is inert.
    assert.ok(!el.innerHTML.includes(`'),alert(1)`),
        'a crafted entry id must not reach an attribute with its quote intact');
    assert.ok(el.innerHTML.includes('&#39;),alert(1)//'),
        'the id must be present but escaped');
});

check('createTimelineEntry rejects a javascript: photo URL', () => {
    const { ctx } = runModule();
    const el = ctx.createTimelineEntry({
        id: 1, created_at: '2026-07-20T10:00:00Z', title: 't', content: 'c',
        photos: ['javascript:alert(1)']
    }, 0);
    assert.ok(!/javascript:/i.test(el.innerHTML), 'javascript: photo URL must be dropped');
});

check('createTimelineEntry survives an attribute-breakout photo URL', () => {
    const { ctx } = runModule();
    const el = ctx.createTimelineEntry({
        id: 1, created_at: '2026-07-20T10:00:00Z', title: 't', content: 'c',
        photos: [`x${QUOTED}`]
    }, 0);
    // Breakout requires the raw double quote to survive; escaped it is inert.
    assert.ok(!el.innerHTML.includes(QUOTED),
        'must not break out of the src attribute with a raw quote');
    assert.ok(el.innerHTML.includes('&quot; onmouseover=&quot;'),
        'the hostile URL must be present but quote-escaped');
});

check('displayOnThisDay escapes title, label, content and date', () => {
    const { ctx, timelineView } = runModule();
    let captured = '';
    timelineView.insertBefore = node => { captured = node.innerHTML; };
    ctx.displayOnThisDay({
        date: XSS,
        entries: [{ id: 1, title: XSS, label: XSS, content: XSS }]
    });
    assert.ok(captured.length > 0, 'section must have rendered');
    assert.ok(!captured.includes('<img src=x'), 'raw payload must not survive');
    assert.ok(!/onclick=/i.test(captured), 'no inline onclick= for the entry id');
    assert.ok(captured.includes('&lt;img src=x'), 'payload must appear escaped');
});

check('displayPrompts escapes the prompt text', () => {
    const { ctx, timelineView } = runModule();
    let captured = '';
    timelineView.insertBefore = node => { captured = node.innerHTML; };
    ctx.displayPrompts([XSS]);
    assert.ok(captured.length > 0, 'prompt block must have rendered');
    assert.ok(!captured.includes('<img src=x'), 'raw payload must not survive');
    assert.ok(captured.includes('&lt;img src=x'), 'payload must appear escaped');
});

check('journey cards escape title, description and cover photo', () => {
    const { ctx } = runModule();
    const current = ctx.createCurrentJourneyHtml({
        id: 1, title: XSS, description: XSS, start_date: '2026-07-20'
    });
    assert.ok(!current.includes('<img src=x'), 'current journey must escape');
    assert.ok(!/onclick=/i.test(current), 'no inline onclick= on the check-in button');

    const past = ctx.createPastJourneyCard({
        id: 1, title: XSS, cover_photo: 'javascript:alert(1)',
        start_date: '2026-07-20', entry_count: XSS, stop_count: 1, progress_percentage: 5
    });
    assert.ok(!past.includes('<img src=x'), 'past journey must escape');
    assert.ok(!/javascript:/i.test(past), 'javascript: cover photo must be dropped');
});

console.log(`\n${passed} checks passed`);
