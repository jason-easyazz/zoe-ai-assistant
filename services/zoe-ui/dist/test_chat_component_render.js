#!/usr/bin/env node
/**
 * Node harness for the chat.html generic-component + activity-timeline helpers.
 *
 * These helpers are written as pure string-builders in chat.html so they can be
 * unit-tested without a DOM. This harness extracts their source straight out of
 * chat.html (so we test the *real* code, not a copy), evaluates them with a few
 * browser globals stubbed, and asserts:
 *   - zoe.component with actions renders buttons with escaped label text
 *   - <script>/<b> injection in props + labels is escaped (no raw markup)
 *   - action payloads are referenced by data-index, never inlined as HTML
 *   - the activity-summary text builder reflects tool counts / done state
 *
 * Run: node services/zoe-ui/dist/test_chat_component_render.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'chat.html'), 'utf8');

// Pull a top-level `function NAME(...) { ... }` block out of the source by
// brace-matching from its opening brace.
function extractFunction(src, name) {
    const sig = 'function ' + name + '(';
    const start = src.indexOf(sig);
    if (start === -1) throw new Error('function not found: ' + name);
    const braceStart = src.indexOf('{', start);
    let depth = 0;
    for (let i = braceStart; i < src.length; i++) {
        const c = src[i];
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error('unbalanced braces for: ' + name);
}

const NAMES = [
    'escapeHtml', 'escapeAttr', 'zoeComponentScalar',
    'buildZoeComponentHtml', 'buildActivitySummaryText', 'activityToolVerb',
];

// Stub the browser-side globals these helpers reach for.
const sandbox = `
    // renderMarkdown in the browser uses marked+DOMPurify; here we keep it
    // simple-but-faithful: escape angle brackets so injection can't survive.
    function renderMarkdown(text) {
        const esc = String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        return esc.replace(/\\n/g, '<br>');
    }
`;

const bundle = sandbox + '\n' + NAMES.map((n) => extractFunction(html, n)).join('\n\n') +
    '\n; ({ escapeHtml, escapeAttr, zoeComponentScalar, buildZoeComponentHtml, buildActivitySummaryText, activityToolVerb });';

// eslint-disable-next-line no-eval
const api = eval(bundle);

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('chat.html component / activity helpers');

// 1. Actions render as buttons with the right kinds and escaped labels.
check('renders action buttons with kinds', () => {
    const { html: out, actions } = api.buildZoeComponentHtml({
        component: 'shopping_list',
        props: { title: 'Groceries', items: ['Milk', 'Eggs'] },
        actions: [
            { label: 'Add item', kind: 'primary', query: 'add bread to my list' },
            { label: 'Clear', kind: 'warn', intent: 'clear_list' },
            { label: 'Refresh', query: 'show my list' },
        ],
    });
    assert.strictEqual(actions.length, 3);
    assert.ok(out.includes('class="zc-action primary" data-zc-action="0"'), 'primary btn');
    assert.ok(out.includes('class="zc-action warn" data-zc-action="1"'), 'warn btn');
    assert.ok(out.includes('class="zc-action normal" data-zc-action="2"'), 'normal btn default');
    assert.ok(out.includes('>Add item</button>'), 'label text present');
    assert.ok(out.includes('>Groceries</'), 'title rendered');
    assert.ok(out.includes('Milk') && out.includes('Eggs'), 'list items rendered');
});

// 2. Injection in labels AND props is escaped — no raw markup survives.
check('escapes script/markup injection in labels and props', () => {
    const evil = '<script>alert(1)</script><b>x</b>';
    const { html: out } = api.buildZoeComponentHtml({
        component: '<img src=x onerror=1>',
        props: {
            title: evil,
            body: 'hi <script>steal()</script>',
            status: '<b>danger</b>',
            items: [{ label: '<i>item</i>', detail: '<u>d</u>' }],
            events: [{ title: '<svg/onload=1>', when: '<b>now</b>', location: '<x>' }],
        },
        actions: [{ label: '<script>boom()</script>', query: 'q' }],
    });
    // No raw, unescaped dangerous tags anywhere in the output: every '<' that
    // came from user data must be entity-escaped, so no real element/attribute
    // can form. We assert there is NO raw '<tag' opening other than the card's
    // own known-safe structural tags.
    const SAFE_TAGS = ['div','span','ul','li','button','br','p'];
    const rawTagRe = /<\/?([a-zA-Z][a-zA-Z0-9]*)/g;
    let m;
    while ((m = rawTagRe.exec(out)) !== null) {
        assert.ok(SAFE_TAGS.includes(m[1].toLowerCase()),
            'unexpected raw tag <' + m[1] + '> — injection not escaped');
    }
    assert.ok(!/<script/i.test(out), 'no raw <script');
    // Event-handler attributes must never appear inside a real (unescaped) tag.
    // (Inert, entity-escaped text like "&lt;img onerror=1&gt;" is fine.)
    const realTags = out.match(/<[^>]*>/g) || [];
    for (const tag of realTags) {
        assert.ok(!/\son\w+=/i.test(tag), 'no event-handler attr in real tag: ' + tag);
    }
    // The escaped form must be present (proves it was rendered-but-neutralised).
    assert.ok(out.includes('&lt;script&gt;'), 'script tag entity-escaped');
});

// 3. Action payloads are NEVER serialized into the HTML (only an index is).
check('action payloads stay out of the DOM (index only)', () => {
    const { html: out } = api.buildZoeComponentHtml({
        component: 'card',
        props: { title: 'X' },
        actions: [{ label: 'Go', query: 'SECRET_QUERY_PAYLOAD', intent: 'SECRET_INTENT' }],
    });
    assert.ok(!out.includes('SECRET_QUERY_PAYLOAD'), 'query not inlined');
    assert.ok(!out.includes('SECRET_INTENT'), 'intent not inlined');
    assert.ok(out.includes('data-zc-action="0"'), 'index reference present');
});

// 4. Generic prop rendering: events + key/value rows for unknown scalars.
check('renders calendar events and generic kv rows', () => {
    const { html: out } = api.buildZoeComponentHtml({
        component: 'calendar',
        props: {
            title: 'Today',
            events: [{ title: 'Standup', start: '09:00', location: 'Zoom' }],
            owner: 'Jason',
            count: 3,
        },
    });
    assert.ok(out.includes('Standup') && out.includes('09:00') && out.includes('Zoom'), 'event fields');
    assert.ok(out.includes('Jason') && out.includes('>3<'), 'kv rows for unknown scalars');
});

// 5. Activity summary text builder (timeline collapse summary).
check('activity summary text reflects count + done state', () => {
    assert.strictEqual(api.buildActivitySummaryText(0, false), 'Working…');
    assert.strictEqual(api.buildActivitySummaryText(0, true), '');
    assert.strictEqual(api.buildActivitySummaryText(1, false), '1 step running…');
    assert.strictEqual(api.buildActivitySummaryText(2, true), '2 steps');
});

// 6. Tool verb mapping for the live "Zoe is doing X…" line.
check('tool verb mapping', () => {
    assert.strictEqual(api.activityToolVerb('web_search'), 'Searching the web');
    assert.strictEqual(api.activityToolVerb('mystery_tool'), 'Using mystery_tool');
});

console.log('\n' + passed + ' checks passed' + (process.exitCode ? ' (with failures)' : ''));
