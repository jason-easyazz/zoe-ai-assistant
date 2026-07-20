#!/usr/bin/env node
/**
 * Stored-XSS regression harness for notes.html + memories.html.
 *
 * Notes are family-visible across users and memory content comes from
 * conversations/ingest, so every rendered field on these pages is untrusted.
 * Both pages used to build HTML strings and hand them to innerHTML, embedding
 * note/memory data inside on* attributes. Escaping was not enough there:
 *   - notes.html's own escHtml() escaped & < > " but NOT ', and the handlers
 *     were single-quoted (onclick='selectNote(...)'), so a title containing
 *     `' onmouseover='...` broke straight out of the attribute;
 *   - JSON.stringify() does not escape ' either, so serialising the whole note
 *     into the attribute carried the payload verbatim;
 *   - memories.html defined no escape helper at all.
 *
 * This harness does two things:
 *   1. Source assertions (with comments stripped, so prose cannot satisfy them).
 *   2. Behavioural checks: the real renderer functions are extracted from the
 *      pages and executed against a minimal DOM shim with hostile data. The
 *      shim serialises DOM properties escaped (as a browser would) but emits
 *      anything assigned to .innerHTML verbatim -- so a string-built renderer
 *      shows its payload and fails, while a DOM-built renderer cannot.
 *
 * Run: node services/zoe-ui/dist/test_notes_memories_xss.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIST = __dirname;
let checks = 0;
const failures = [];
// Collect every failure rather than aborting on the first, so a red run shows
// the full blast radius (and so the behavioural checks are visibly exercised
// even when the source checks already fail).
function check(label, fn) {
    checks++;
    try {
        fn();
        if (process.env.XSS_HARNESS_VERBOSE) console.log('  ok   ' + label);
    } catch (err) {
        failures.push({ label, message: err && err.message ? err.message : String(err) });
        console.error('  FAIL ' + label);
    }
}

/* ── source helpers ─────────────────────────────────────────────── */

function read(name) {
    return fs.readFileSync(path.join(DIST, name), 'utf8');
}

/** Strip /* *​/ and // comments so explanatory prose cannot satisfy an assertion. */
function stripComments(src) {
    return src
        .replace(/\/\*[\s\S]*?\*\//g, ' ')
        .replace(/(^|[^:'"\\])\/\/[^\n]*/g, '$1 ')
        .replace(/<!--[\s\S]*?-->/g, ' ');
}

/** Extract a top-level `function name(...) { ... }` body by brace matching. */
function extractFunction(src, name) {
    const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(');
    const m = re.exec(src);
    if (!m) return null;
    const start = m.index;
    let i = src.indexOf('{', m.index);
    let depth = 0;
    for (; i < src.length; i++) {
        const c = src[i];
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error('unbalanced braces extracting ' + name);
}

function extractFunctions(src, names) {
    return names.map(n => extractFunction(src, n)).filter(Boolean).join('\n\n');
}

/* ── minimal DOM shim ───────────────────────────────────────────── */

function esc(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const SERIALIZED_PROPS = ['value', 'src', 'placeholder', 'type', 'alt'];

function makeNode(tag) {
    const node = {
        tagName: String(tag).toLowerCase(),
        className: '',
        children: [],
        attrs: {},
        style: {},
        listeners: {},
        _text: null,
        _rawHtml: null,
        appendChild(c) { this.children.push(c); this._rawHtml = null; return c; },
        append(...cs) { cs.forEach(c => this.children.push(c)); this._rawHtml = null; },
        replaceChildren(...cs) { this.children = cs.slice(); this._rawHtml = null; this._text = null; },
        addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
        setAttribute(k, v) { this.attrs[k] = v; },
        removeAttribute(k) { delete this.attrs[k]; },
        querySelectorAll() { return []; },
        focus() {},
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    };
    Object.defineProperty(node, 'textContent', {
        get() { return node._text; },
        set(v) { node._text = String(v); node.children = []; node._rawHtml = null; },
    });
    // The vulnerable path: whatever is assigned here is emitted verbatim.
    Object.defineProperty(node, 'innerHTML', {
        get() { return node._rawHtml; },
        set(v) { node._rawHtml = String(v); node.children = []; node._text = null; },
    });
    return node;
}

/**
 * Serialise like a browser: DOM *properties* are escaped, but anything that
 * went through .innerHTML is emitted raw. Event listeners are rendered as
 * data-listener-* so they never look like an injected on* attribute.
 */
function serialize(node) {
    if (node == null) return '';
    if (typeof node === 'string') return esc(node);
    let attrs = '';
    if (node.className) attrs += ` class="${esc(node.className)}"`;
    for (const [k, v] of Object.entries(node.attrs)) attrs += ` ${k}="${esc(v)}"`;
    for (const p of SERIALIZED_PROPS) {
        if (node[p] !== undefined && node[p] !== null && node[p] !== '') {
            attrs += ` ${p}="${esc(node[p])}"`;
        }
    }
    const style = Object.entries(node.style)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}:${esc(v)}`)
        .join(';');
    if (style) attrs += ` style="${style}"`;
    for (const ev of Object.keys(node.listeners)) attrs += ` data-listener-${ev}="fn"`;

    // Setting textContent replaces children with one text node; anything
    // appended afterwards follows it -- mirror that ordering.
    let inner;
    if (node._rawHtml != null) inner = node._rawHtml;
    else if (node._text != null) inner = esc(node._text) + node.children.map(serialize).join('');
    else inner = node.children.map(serialize).join('');
    return `<${node.tagName}${attrs}>${inner}</${node.tagName}>`;
}

function makeSandbox(extraGlobals) {
    const nodes = {};
    const document = {
        createElement: makeNode,
        getElementById(id) { return (nodes[id] = nodes[id] || makeNode('div')); },
        querySelector() { return makeNode('div'); },
        querySelectorAll() { return []; },
        addEventListener() {},
        body: makeNode('body'),
    };
    const sandbox = Object.assign({
        document,
        window: {},
        console,
        Math,
        Number,
        String,
        Date,
        Array,
        Object,
        JSON,
        isNaN,
        parseInt,
        parseFloat,
        __nodes: nodes,
    }, extraGlobals);
    sandbox.window = sandbox;
    return sandbox;
}

/**
 * Assertions applied to any serialised render of hostile data.
 *
 * Note the handler regex requires a RAW quote after the `=`. Escaped text
 * legitimately still contains the characters " onmouseover=" -- what makes it
 * inert is that the following quote came out as &#39; / &quot;. Matching on
 * ` on<word>=` alone would fire on safe, correctly-escaped output.
 */
const HANDLER_RE = /\son[a-z]+\s*=\s*['"]/i;
function assertNoInjection(label, html, opts) {
    const { rawPayloads = [], marker } = opts || {};
    assert.ok(!HANDLER_RE.test(html),
        `${label}: injected event-handler attribute in output:\n${html}`);
    assert.ok(!/<script/i.test(html),
        `${label}: injected <script> in output:\n${html}`);
    assert.ok(!/<img/i.test(html),
        `${label}: injected <img> in output:\n${html}`);
    assert.ok(!/javascript:/i.test(html),
        `${label}: javascript: URL survived in output:\n${html}`);
    // Any payload reaching output with its quotes/angle brackets intact escaped
    // its context -- correctly rendered data is always entity-escaped.
    for (const p of rawPayloads) {
        assert.ok(!html.includes(p),
            `${label}: payload survived verbatim (unescaped) in output:\n${html}`);
    }
    if (marker) {
        assert.ok(html.includes(marker),
            `${label}: payload was dropped entirely -- the renderer may not have run:\n${html}`);
    }
}

/* ── payloads ───────────────────────────────────────────────────── */

// Breaks out of a single-quoted on* attribute (the notes.html defect).
const SQ_PAYLOAD = `x' onmouseover='alert(1)`;
// Breaks out of a double-quoted attribute.
const DQ_PAYLOAD = `x" onmouseover="alert(1)`;
// Plain element injection for innerHTML sinks.
const TAG_PAYLOAD = `<img src=x onerror=alert(1)>`;

/* ── notes.html ─────────────────────────────────────────────────── */

const notesRaw = read('notes.html');
const notesSrc = stripComments(notesRaw);

check('notes.html: no escHtml helper remains (it never escaped the single quote)', () => {
    assert.ok(!/escHtml/.test(notesSrc),
        'notes.html still references escHtml; it escapes & < > " but not \', which is the defect');
});

check('notes.html: no note object is serialised into an inline handler', () => {
    assert.ok(!/JSON\.stringify\s*\(\s*note\s*\)/.test(notesSrc),
        'notes.html still serialises a note into markup via JSON.stringify');
    assert.ok(!/onclick\s*=\s*'[^']*\$\{/.test(notesSrc),
        'notes.html still interpolates data into a single-quoted onclick');
});

check('notes.html: no template interpolation inside any on* attribute', () => {
    const m = notesSrc.match(/\son[a-z]+\s*=\s*(['"])[^'"]*\$\{[^'"]*\1/i);
    assert.ok(!m, `notes.html interpolates into an on* attribute: ${m && m[0]}`);
});

check('notes.html: colour is not interpolated into a style attribute', () => {
    assert.ok(!/style\s*=\s*"[^"]*border-left-color\s*:\s*\$\{/.test(notesSrc),
        'notes.html still interpolates an unvalidated colour into a style attribute');
    assert.ok(/safeNoteColor/.test(notesSrc), 'notes.html lost its colour validator');
});

check('notes.html: still loads js/common.js (shared helpers stay reachable)', () => {
    assert.ok(/<script[^>]+src=["']js\/common\.js["']/.test(notesRaw),
        'notes.html no longer loads common.js');
});

// -- behavioural: renderSidebar with a hostile note ---------------
check('notes.html renderSidebar(): hostile note title cannot inject', () => {
    const sandbox = makeSandbox({
        NOTE_COLORS: ['#7B61FF'],
        currentNoteId: null,
        searchQuery: '',
        selectNote() {},
    });
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunctions(notesRaw, ['safeNoteColor', 'hashStr', 'makeDiv', 'escHtml', 'renderSidebar']),
        sandbox);

    const hostileNote = {
        id: 'n1',
        title: SQ_PAYLOAD,
        content: TAG_PAYLOAD,
        tags: [DQ_PAYLOAD],
        color: `#000" onload="alert(1)`,
        updated_at: null,
        created_at: null,
    };
    vm.runInContext('renderSidebar(__list)', Object.assign(sandbox, { __list: [hostileNote] }));
    const html = serialize(sandbox.__nodes.notesList);
    assertNoInjection('renderSidebar', html,
        { rawPayloads: [SQ_PAYLOAD, DQ_PAYLOAD, TAG_PAYLOAD], marker: 'alert(1)' });
    assert.ok(!/#000/.test(html), 'renderSidebar accepted a non-hex colour: ' + html);
});

check('notes.html renderSidebar(): benign note still renders its fields', () => {
    const sandbox = makeSandbox({
        NOTE_COLORS: ['#7B61FF'],
        currentNoteId: null,
        searchQuery: '',
        selectNote() {},
    });
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunctions(notesRaw, ['safeNoteColor', 'hashStr', 'makeDiv', 'escHtml', 'renderSidebar']),
        sandbox);
    const note = { id: 'n1', title: 'Groceries', content: 'milk', tags: ['home'], color: '#5AE0E0' };
    vm.runInContext('renderSidebar(__list)', Object.assign(sandbox, { __list: [note] }));
    const html = serialize(sandbox.__nodes.notesList);
    assert.ok(html.includes('Groceries'), 'title missing: ' + html);
    assert.ok(html.includes('milk'), 'content missing: ' + html);
    assert.ok(html.includes('home'), 'tag missing: ' + html);
    assert.ok(html.includes('#5AE0E0'), 'valid colour was rejected: ' + html);
    assert.ok(/data-listener-click/.test(html), 'note item has no click listener: ' + html);
});

// -- behavioural: updateTagsChips with a hostile tag ---------------
check('notes.html updateTagsChips(): hostile tag cannot inject', () => {
    const sandbox = makeSandbox({ currentTags: [SQ_PAYLOAD], removeTag() {}, isDirty: false });
    vm.createContext(sandbox);
    vm.runInContext(extractFunctions(notesRaw, ['escHtml', 'updateTagsChips']), sandbox);
    vm.runInContext('updateTagsChips()', sandbox);
    const html = serialize(sandbox.__nodes.tagsChips);
    assertNoInjection('updateTagsChips', html,
        { rawPayloads: [SQ_PAYLOAD], marker: 'alert(1)' });
    assert.ok(/data-listener-click/.test(html), 'remove control lost its listener: ' + html);
});

/* ── memories.html ──────────────────────────────────────────────── */

const memRaw = read('memories.html');
const memSrc = stripComments(memRaw);

check('memories.html: no template interpolation inside any on* attribute', () => {
    const m = memSrc.match(/\son[a-z]+\s*=\s*(['"])[^'"]*\$\{[^'"]*\1/i);
    assert.ok(!m, `memories.html interpolates into an on* attribute: ${m && m[0]}`);
});

check('memories.html: memory content is not interpolated into innerHTML', () => {
    assert.ok(!/innerHTML\s*=[^;]*item\.content/.test(memSrc),
        'memories.html still writes memory content through innerHTML');
    assert.ok(!/innerHTML\s*=[^;]*notification\.message/.test(memSrc),
        'memories.html still writes notification text through innerHTML');
    assert.ok(!/innerHTML\s*=[^;]*col\.name/.test(memSrc),
        'memories.html still writes a collection name through innerHTML');
});

check('memories.html: defines DOM-building + URL-sanitising helpers', () => {
    assert.ok(/function\s+memDiv\s*\(/.test(memSrc), 'memDiv helper missing');
    assert.ok(/function\s+sanitizeTileUrl\s*\(/.test(memSrc), 'sanitizeTileUrl helper missing');
});

check('memories.html: still loads js/common.js', () => {
    assert.ok(/<script[^>]+src=["']js\/common\.js["']/.test(memRaw),
        'memories.html no longer loads common.js');
});

// -- behavioural: updateSidebar review queue -----------------------
function memSandbox(extra) {
    const sandbox = makeSandbox(Object.assign({
        reviewMemory() {},
        handleNotificationClick() {},
        getPriorityColor() { return '#ef4444'; },
    }, extra));
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunctions(memRaw, ['memDiv', 'sanitizeTileUrl', 'updateSidebar', 'displayNotifications']),
        sandbox);
    return sandbox;
}

check('memories.html updateSidebar(): hostile memory content cannot inject', () => {
    const sandbox = memSandbox({
        reviewQueue: [{
            id: `x' onmouseover='alert(1)`,
            memory_type: TAG_PAYLOAD,
            confidence: 0.5,
            content: `${TAG_PAYLOAD} and ${DQ_PAYLOAD}`,
        }],
    });
    vm.runInContext('updateSidebar()', sandbox);
    const html = serialize(sandbox.__nodes.insightsList);
    assertNoInjection('updateSidebar', html,
        { rawPayloads: [DQ_PAYLOAD, TAG_PAYLOAD], marker: 'alert(1)' });
    assert.ok(/data-listener-click/.test(html), 'approve/reject lost their listeners: ' + html);
});

check('memories.html updateSidebar(): benign memory still renders', () => {
    const sandbox = memSandbox({
        reviewQueue: [{ id: 'm1', memory_type: 'fact', confidence: 0.9, content: 'Jason likes tea' }],
    });
    vm.runInContext('updateSidebar()', sandbox);
    const html = serialize(sandbox.__nodes.insightsList);
    assert.ok(html.includes('Jason likes tea'), 'memory content missing: ' + html);
    assert.ok(html.includes('confidence 90%'), 'confidence missing: ' + html);
});

check('memories.html displayNotifications(): hostile message cannot inject', () => {
    const sandbox = memSandbox({});
    vm.runInContext('__n = [{ id: 1, message: __payload, created_at: 0, priority: "high", is_delivered: false }]',
        Object.assign(sandbox, { __payload: `${TAG_PAYLOAD}${SQ_PAYLOAD}` }));
    vm.runInContext('displayNotifications(__n)', sandbox);
    const html = serialize(sandbox.__nodes.notificationsContent);
    assertNoInjection('displayNotifications', html,
        { rawPayloads: [SQ_PAYLOAD, TAG_PAYLOAD], marker: 'alert(1)' });
});

// -- sanitizeTileUrl ----------------------------------------------
check('memories.html sanitizeTileUrl(): rejects script-bearing URLs', () => {
    const sandbox = makeSandbox({});
    vm.createContext(sandbox);
    vm.runInContext(extractFunctions(memRaw, ['sanitizeTileUrl']), sandbox);
    const call = u => vm.runInContext('sanitizeTileUrl(__u)', Object.assign(sandbox, { __u: u }));
    assert.strictEqual(call('javascript:alert(1)'), '');
    assert.strictEqual(call('JaVaScRiPt:alert(1)'), '');
    assert.strictEqual(call('java\tscript:alert(1)'), '', 'control-char scheme bypass accepted');
    assert.strictEqual(call('  javascript:alert(1)'), '');
    assert.strictEqual(call('vbscript:msgbox(1)'), '');
    assert.strictEqual(call('data:text/html,<script>alert(1)</script>'), '');
    // SVG is an active document format, not an inert raster image.
    assert.strictEqual(call('data:image/svg+xml;base64,AAAA'), '',
        'data:image/svg+xml must be rejected -- SVG can carry script');
    assert.strictEqual(call('data:image/svg+xml,<svg onload=alert(1)>'), '');
    assert.strictEqual(call('https://example.com/a.png'), 'https://example.com/a.png');
    assert.strictEqual(call('data:image/png;base64,AAAA'), 'data:image/png;base64,AAAA');
    // A data URL may carry no parameters at all -- must not be rejected.
    assert.strictEqual(call('data:image/gif,AAAA'), 'data:image/gif,AAAA');
    assert.strictEqual(call('data:image/jpeg;base64,AAAA'), 'data:image/jpeg;base64,AAAA');
    assert.strictEqual(call(null), '');
});

/* ── done ───────────────────────────────────────────────────────── */

if (failures.length) {
    console.error(`\n${failures.length} of ${checks} checks FAILED:\n`);
    for (const f of failures) console.error(`  - ${f.label}\n      ${f.message}\n`);
    process.exit(1);
}
console.log(`${checks} checks passed`);
