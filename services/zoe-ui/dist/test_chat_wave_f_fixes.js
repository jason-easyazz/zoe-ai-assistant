#!/usr/bin/env node
/**
 * Node harness for the Wave-F chat.html repairs.
 *
 * Four confirmed defects, each invisible for a different reason:
 *
 *  1. action_menu options were doubly broken — `onclick="sendMessage(${msg})"`
 *     where msg = JSON.stringify(...) opens with a double quote, terminating
 *     the double-quoted attribute (SyntaxError per option); and sendMessage()
 *     takes NO arguments (chat.html:4063), it reads #chatInput.
 *  2. add_to_list POSTed an ITEM body to the CREATE-LIST route (lists.py:92,
 *     body ListCreate{name}) → 422 on every click.
 *  3. claimPending() dispatched 'proactive_session' and rewrote ?session=, but
 *     nothing listened and nothing read the param → tapping a proactive
 *     notification landed on a blank chat.
 *  4. agent-activity.js guarded on `!this._container`, which does not catch a
 *     DETACHED node. chat.html's loadSessions() replaces sessionsList.innerHTML
 *     at three sites, orphaning the feed while the reference stayed truthy —
 *     so it rendered into a detached node forever.
 *
 * Run: node services/zoe-ui/dist/test_chat_wave_f_fixes.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, 'chat.html'), 'utf8');
const activitySrc = fs.readFileSync(path.join(__dirname, 'js', 'agent-activity.js'), 'utf8');

/** Drop // and block comments so assertions match CODE, not prose about code. */
function stripComments(src) {
    return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
}

let passed = 0;
function check(name, fn) {
    try { fn(); passed++; console.log('  ok  - ' + name); }
    catch (e) { console.error('  FAIL - ' + name + '\n        ' + e.message); process.exitCode = 1; }
}

console.log('chat.html Wave F repairs');

check('every inline <script> block parses', () => {
    const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
        .map(m => m[1]).filter(b => b.trim());
    blocks.forEach((b, i) => new vm.Script(b, { filename: `chat.html#inline-${i}` }));
    assert.ok(blocks.length >= 1);
});

// ── 1 ────────────────────────────────────────────────────────────────────
check('action_menu no longer interpolates a message into an onclick', () => {
    assert.ok(!/onclick="sendMessage\(\$\{msg\}\)"/.test(html),
        'the broken onclick must be gone');
    const fn = html.match(/function renderActionMenu[\s\S]*?\n {8}\}/);
    assert.ok(fn, 'renderActionMenu must exist');
    assert.ok(/data-am-index="\$\{i\}"/.test(fn[0]),
        'options must carry an index, not embedded message text');
    assert.ok(/addEventListener\('click'/.test(fn[0]),
        'a delegated listener must replace the inline handler');
    assert.ok(/tryPrompt\(/.test(fn[0]),
        'must route through tryPrompt(text), which actually accepts an argument');
    assert.ok(!/sendMessage\(\s*[a-zA-Z_$]/.test(stripComments(fn[0])),
        'sendMessage takes no arguments — nothing may pass it one');
});

check('NO component anywhere still interpolates into a sendMessage onclick', () => {
    // This assertion found a second live instance in renderPriceTable that the
    // audit had not flagged. Keep it file-wide so a third cannot slip in.
    const code = stripComments(html);
    assert.ok(!/onclick="sendMessage\(\$\{/.test(code),
        'a component still builds onclick="sendMessage(${...})" — broken twice over');
});

// ── 2 ────────────────────────────────────────────────────────────────────
check('add_to_list posts an item to the items route, not the create-list route', () => {
    const seg = html.match(/actionType === 'add_to_list'[\s\S]{0,2600}/);
    assert.ok(seg, 'add_to_list branch must exist');
    assert.ok(/\/items`, \{[\s\S]{0,120}method: 'POST'/.test(seg[0]),
        'the item must POST to .../items');
    assert.ok(!/apiRequest\(`\/api\/lists\/tasks\?user_id=/.test(html),
        'the old create-list POST (which 422d) must be gone');
    assert.ok(/name: wantName/.test(seg[0]),
        'creating the list must send ListCreate{name}, the shape the route expects');
    // A named card must land in the NAMED list. Picking lists[0] (most recently
    // updated) would drop a "Work Tasks" item into whatever list was touched
    // last while still reporting success.
    assert.ok(/const wantName = data\.list_name \|\| 'Tasks'/.test(seg[0]),
        'the requested list name must be captured');
    assert.ok(/\.find\(l =>[\s\S]{0,160}toLowerCase\(\)/.test(seg[0]),
        'the existing lists must be searched BY NAME, not indexed at [0]');
    assert.ok(/if \(!listId && !data\.list_name\) listId = existing\[0\]\?\.id;/.test(seg[0]),
        'the unnamed fallback must apply only when the card named no list');
});

// ── 3 ────────────────────────────────────────────────────────────────────
check('proactive_session has a listener that opens the session', () => {
    assert.ok(/addEventListener\('proactive_session'/.test(html),
        "nothing listened for the event the claim dispatches");
    // Strip comments: the prose below the listener mentions loadSessionMessages,
    // so a naive match passed for the wrong reason.
    const seg = stripComments(html.match(/addEventListener\('proactive_session'[\s\S]{0,600}/)[0]);
    assert.ok(/loadSession\(sid\)/.test(seg),
        'must call loadSession(), which sets currentSessionId before rendering');
    assert.ok(!/loadSessionMessages\(sid\)/.test(seg),
        'loadSessionMessages alone leaves the session inactive — the next reply would use a stale id');
});

// ── 4 — behavioural, not textual ─────────────────────────────────────────
check('agent-activity re-attaches after its container is detached (live DOM sim)', () => {
    // Minimal DOM good enough to model prepend + innerHTML replacement.
    function mkNode(id) {
        return {
            id, children: [], isConnected: true,
            prepend(c) { c.isConnected = true; this.children.unshift(c); },
            set innerHTML(_v) { this.children.forEach(c => { c.isConnected = false; }); this.children = []; },
            get innerHTML() { return ''; },
            addEventListener() {}, querySelector() { return null; }
        };
    }
    const sessionsList = mkNode('sessionsList');
    const head = mkNode('head');
    head.appendChild = () => {};          // _injectStyles() uses document.head
    const sandbox = {
        console: { log() {}, warn() {}, error() {}, debug() {} },
        document: {
            getElementById: (id) => (id === 'sessionsList' ? sessionsList : null),
            createElement: () => mkNode('agentActivitySection'),
            addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
            head,
            // 'loading' so the module registers a DOMContentLoaded listener
            // instead of auto-running init() (which starts polling timers).
            readyState: 'loading'
        },
        setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
        fetch: async () => ({ ok: true, json: async () => ({ tasks: [] }) }),
        Promise, JSON, Date, Math, Array, Object, String, Number, Boolean, Error
    };
    sandbox.window = sandbox; sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(activitySrc, sandbox, { filename: 'agent-activity.js' });

    // The module self-boots: `window.agentActivity = new AgentActivity()`.
    const inst = sandbox.agentActivity;
    assert.ok(inst, 'agent-activity must expose window.agentActivity');
    assert.ok(typeof inst._ensureContainer === 'function' && typeof inst._render === 'function',
        'expected _ensureContainer/_render on the instance');

    inst._tasks = [];
    inst._ensureContainer();
    const first = inst._container;
    assert.ok(first && first.isConnected, 'container must be attached initially');

    // chat.html's loadSessions() does exactly this, milliseconds after boot.
    sessionsList.innerHTML = '<div>sessions</div>';
    assert.strictEqual(first.isConnected, false, 'the wipe must detach our node');

    inst._render();
    assert.ok(inst._container && inst._container.isConnected,
        'after a wipe, _render must re-attach — otherwise the feed renders into an orphan forever');
});

console.log(`\n${passed} checks passed`);
