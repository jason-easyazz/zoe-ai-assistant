#!/usr/bin/env node
/**
 * Node harness for the chat.html ↔ zoe-compose bridge (shared tree renderer).
 *
 * The bridge helpers are written as pure functions in chat.html so they can be
 * unit-tested without a DOM (same pattern as test_chat_component_render.js).
 * This harness:
 *   - vm.Script parse-checks EVERY inline <script> block in chat.html
 *   - extracts the bridge helpers straight out of chat.html (real code, no copy)
 *   - loads the REAL shared renderer from touch/js/zoe-compose.js
 * and asserts:
 *   a) a compose zoe.component payload renders zx- classes + the tree content
 *   b) markup injection in tree text is entity-escaped (no raw tags survive)
 *   c) ActionButtons carry the delegated-click contract:
 *      data-sky-action="query" + data-query (plain text, never code)
 *   d) non-compose components still route to buildZoeComponentHtml output
 *   e) a missing ZoeCompose renderer falls back to the generic path, no throw
 *
 * Run: node services/zoe-ui/dist/test_chat_compose_render.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'chat.html'), 'utf8');
const composeSrc = fs.readFileSync(path.join(__dirname, 'touch', 'js', 'zoe-compose.js'), 'utf8');

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('chat.html compose bridge (shared zoe-compose renderer)');

// ——— 0. Parse-check every inline <script> block in chat.html ————————————
check('all inline <script> blocks parse (vm.Script)', () => {
    const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m, count = 0;
    while ((m = scriptRe.exec(html)) !== null) {
        if (/\bsrc\s*=/i.test(m[1])) continue; // external script, nothing inline
        const code = m[2];
        if (!code.trim()) continue;
        count++;
        // Throws SyntaxError (failing this check) if the block doesn't parse.
        new vm.Script(code, { filename: 'chat.html#inline-' + count });
    }
    assert.ok(count >= 1, 'expected at least one inline script block, saw ' + count);
});

// ——— Extract the real helpers out of chat.html ——————————————————————————
// Pull a top-level `function NAME(...) { ... }` block out of the source by
// brace-matching from its opening brace (same as test_chat_component_render.js).
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
    'escapeHtml', 'escapeAttr', 'zoeComponentScalar', 'buildZoeComponentHtml',
    'zoeComposeTreeFromComponentValue', 'zoeComposeTreeFromUiComponent',
    'buildZoeComposeCardHtml', 'buildZoeComponentCard',
];

// Stub the browser-side globals the generic builder reaches for.
const sandbox = `
    // renderMarkdown in the browser uses marked+DOMPurify; here we keep it
    // simple-but-faithful: escape angle brackets so injection can't survive.
    function renderMarkdown(text) {
        const esc = String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        return esc.replace(/\\n/g, '<br>');
    }
`;

const bundle = sandbox + '\n' + NAMES.map((n) => extractFunction(html, n)).join('\n\n') +
    '\n; ({ ' + NAMES.join(', ') + ' });';
// eslint-disable-next-line no-eval
const api = eval(bundle);

// ——— Load the REAL shared renderer (touch/js/zoe-compose.js) ————————————
const fakeWindow = {};
vm.runInNewContext(composeSrc, { window: fakeWindow }, { filename: 'zoe-compose.js' });
const ZoeCompose = fakeWindow.ZoeCompose;
assert.ok(ZoeCompose && typeof ZoeCompose.render === 'function', 'zoe-compose.js exposes window.ZoeCompose.render');

const COMPOSE_TREE = {
    component: 'Stack',
    gap: 'md',
    children: [
        { component: 'Text', role: 'title', text: 'Tomorrow at a glance' },
        { component: 'ListRow', variant: 'check', checked: true, title: 'Milk', detail: '2L' },
        { component: 'ActionButton', action: { label: 'Show more', kind: 'primary', query: 'show me the full list' } },
    ],
};
const COMPOSE_COMPONENT_VALUE = { component: 'compose', props: { tree: COMPOSE_TREE } };
const COMPOSE_UI_PAYLOAD = { // exactly what PR-B emits under ZOE_COMPOSE_UI
    type: 'compose',
    data: { action: 'Composed view' },
    card: { component: 'compose', props: { tree: COMPOSE_TREE } },
};

// a) compose zoe.component payload → zx- classes + tree content
check('compose zoe.component renders through ZoeCompose (zx- classes + content)', () => {
    const card = api.buildZoeComponentCard(COMPOSE_COMPONENT_VALUE, ZoeCompose);
    assert.strictEqual(card.mode, 'compose');
    assert.ok(card.html.includes('zx-root'), 'zx-root wrapper');
    assert.ok(card.html.includes('zx-stack'), 'zx-stack container');
    assert.ok(card.html.includes('Tomorrow at a glance'), 'tree text rendered');
    assert.ok(card.html.includes('zx-listrow'), 'list row rendered');
    assert.ok(!card.html.includes('zc-header'), 'generic zc- card NOT used for compose');
    // The ui_component (PR-B) shape extracts the very same tree.
    assert.strictEqual(api.zoeComposeTreeFromUiComponent(COMPOSE_UI_PAYLOAD), COMPOSE_TREE);
    assert.strictEqual(api.zoeComposeTreeFromUiComponent({ type: 'calendar', card: {} }), null, 'non-compose type ignored');
    assert.strictEqual(api.zoeComposeTreeFromUiComponent({ type: 'compose' }), null, 'missing card ignored');
});

// b) injection in tree text is escaped
check('markup injection in tree text is entity-escaped', () => {
    const evilTree = {
        component: 'Stack',
        children: [
            { component: 'Text', role: 'body', text: '<b>x</b>' },
            { component: 'ActionButton', action: { label: '<script>boom()</script>', query: 'a "quoted" <q>' } },
        ],
    };
    const card = api.buildZoeComponentCard({ component: 'compose', props: { tree: evilTree } }, ZoeCompose);
    assert.strictEqual(card.mode, 'compose');
    assert.ok(!card.html.includes('<b>x</b>'), 'raw <b> must not survive');
    assert.ok(card.html.includes('&lt;b&gt;x&lt;/b&gt;'), 'escaped form present');
    assert.ok(!/<script/i.test(card.html), 'no raw <script');
    // Every real tag in the output is a known-safe structural tag.
    const SAFE_TAGS = ['div', 'span', 'p', 'button', 'strong', 'em', 'i', 'hr', 'ul', 'li', 'img', 'figure', 'figcaption', 'svg', 'rect', 'path', 'circle'];
    const rawTagRe = /<\/?([a-zA-Z][a-zA-Z0-9]*)/g;
    let m;
    while ((m = rawTagRe.exec(card.html)) !== null) {
        assert.ok(SAFE_TAGS.includes(m[1].toLowerCase()), 'unexpected raw tag <' + m[1] + '>');
    }
    // Quotes in the query are attribute-escaped so the data attribute can't break out.
    assert.ok(card.html.includes('data-query="a &quot;quoted&quot; &lt;q&gt;"'), 'data-query attribute-escaped');
});

// c) the delegated click contract: data-sky-action="query" + data-query
check('ActionButton carries data-sky-action="query" + data-query (no inline code)', () => {
    const card = api.buildZoeComponentCard(COMPOSE_COMPONENT_VALUE, ZoeCompose);
    assert.ok(card.html.includes('data-sky-action="query"'), 'delegation attribute present');
    assert.ok(card.html.includes('data-query="show me the full list"'), 'query payload as plain text attr');
    assert.ok(card.html.includes('>Show more</button>'), 'label rendered as text');
    assert.ok(!/\son\w+=/i.test(card.html), 'no inline event-handler attributes anywhere');
});

// d) non-compose components still route to buildZoeComponentHtml output
check('non-compose components still use the generic zc- builder', () => {
    const value = {
        component: 'shopping_list',
        props: { title: 'Groceries', items: ['Milk', 'Eggs'] },
        actions: [{ label: 'Add item', kind: 'primary', query: 'add bread' }],
    };
    const card = api.buildZoeComponentCard(value, ZoeCompose);
    const direct = api.buildZoeComponentHtml(value);
    assert.strictEqual(card.mode, 'generic');
    assert.strictEqual(card.html, direct.html, 'byte-identical to buildZoeComponentHtml');
    assert.deepStrictEqual(card.actions, direct.actions, 'action registry passed through');
    assert.ok(card.html.includes('zc-header') && card.html.includes('data-zc-action="0"'));
    assert.ok(!card.html.includes('zx-root'), 'no zx- markup on generic path');
});

// e) missing ZoeCompose falls back to the generic path without throwing
check('missing ZoeCompose renderer falls back without throwing', () => {
    const card = api.buildZoeComponentCard(COMPOSE_COMPONENT_VALUE, null);
    assert.strictEqual(card.mode, 'generic', 'falls back to generic zc- card');
    assert.ok(card.html.includes('zc-header'), 'generic card produced');
    // Malformed inputs are also inert:
    assert.strictEqual(api.zoeComposeTreeFromComponentValue({ component: 'compose' }), null);
    assert.strictEqual(api.zoeComposeTreeFromComponentValue({ component: 'compose', props: { tree: 'nope' } }), null);
    assert.strictEqual(api.buildZoeComposeCardHtml(COMPOSE_TREE, { render: () => { throw new Error('boom'); } }), null,
        'renderer throw is swallowed into fallback');
    const broken = api.buildZoeComponentCard({ component: 'compose', props: { tree: null } }, ZoeCompose);
    assert.strictEqual(broken.mode, 'generic', 'tree-less compose value falls back');
});

console.log('\n' + passed + ' checks passed' + (process.exitCode ? ' (with failures)' : ''));
