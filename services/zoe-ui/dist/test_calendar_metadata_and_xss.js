#!/usr/bin/env node
/**
 * Node harness for two defects in the desktop calendar (calendar.html).
 *
 * DEFECT 1 -- silent DATA LOSS on drag / resize / task-link.
 * PUT /calendar/events/{id} REPLACES the metadata column wholesale: in
 * services/zoe-data/routers/calendar.py the update loop does
 *     if key == "metadata": updates.append("metadata = ?")
 * with json.dumps(value) -- no merge with the stored row. Four client call
 * sites (rescheduleEvent, linkTasksToEvent, saveEventDuration,
 * saveEventLinkedTasks) sent `metadata: { linked_tasks: ... }` and nothing
 * else, so dragging or resizing an event destroyed description, prep_items,
 * get_ready_time, travel_time, attendees and reminders.
 *
 * The read half of the same wipe lived in openEventPanel(), which loaded
 * attendees and reminders from GET /calendar/events/{id}/attendees and
 * .../reminders. Neither route exists on zoe-data, so both 404'd and reset the
 * in-memory lists to empty -- and the next saveEvent() persisted the emptied
 * lists over the real ones.
 *
 * DEFECT 2 -- zero HTML escaping. calendar.html interpolated task text,
 * reminder title/category/description, event titles, linked task text, person
 * name/email and notification messages straight into innerHTML, plus reminder
 * message and prep item text into value="..." attributes.
 *
 * The behavioural checks below EXECUTE the real renderers, extracted from the
 * live file by brace matching, against a DOM shim -- they are not regex
 * theatre. Validated against the true pre-fix file from origin/main, where the
 * harness fails.
 *
 * Run: node services/zoe-ui/dist/test_calendar_metadata_and_xss.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const distDir = __dirname;
const calendarPath = path.join(distDir, 'calendar.html');
const html = fs.readFileSync(calendarPath, 'utf8');
const swSource = fs.readFileSync(path.join(distDir, 'sw.js'), 'utf8');
const commonSource = fs.readFileSync(path.join(distDir, 'js', 'common.js'), 'utf8');

// The server file is two levels up from zoe-ui/dist.
const routerPath = path.join(
    distDir, '..', '..', 'zoe-data', 'routers', 'calendar.py'
);
const routerSource = fs.readFileSync(routerPath, 'utf8');

/* --------------------------------------------------------------- utilities */

/** Concatenate every inline <script> body (i.e. skip src= includes). */
function inlineScript(source) {
    const out = [];
    const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
    let m;
    while ((m = re.exec(source)) !== null) out.push(m[1]);
    return out.join('\n;\n');
}

/**
 * Strip block comments and whole-line // comments so the explanatory prose in
 * this fix cannot satisfy a source-level assertion. TEXT matching only -- the
 * vm below executes the original, unmodified source.
 */
function stripComments(js) {
    return js
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .split('\n')
        .filter(line => !/^\s*\/\//.test(line))
        .join('\n');
}

const script = inlineScript(html);
const code = stripComments(script);

/** Extract one top-level `function NAME(...) { ... }` by brace matching. */
function extractFunction(source, name) {
    let start = source.indexOf(`function ${name}(`);
    assert.notStrictEqual(start, -1, `function ${name} not found in calendar.html`);
    // Keep a leading `async ` -- dropping it makes the extracted body a
    // non-async function and every `await` inside it a SyntaxError.
    const prefix = source.slice(Math.max(0, start - 6), start);
    if (prefix === 'async ') start -= 6;
    let i = source.indexOf('{', start);
    let depth = 0;
    for (; i < source.length; i++) {
        const ch = source[i];
        if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error(`unbalanced braces extracting ${name}`);
}

let passed = 0;
// Checks are ENQUEUED, not run inline: several of them exercise async renderers
// and return a promise. Running them inline would let an assertion failure
// inside a .then() escape as an unhandled rejection -- the check would print
// "ok" while proving nothing. The runner at the bottom awaits each one.
const queue = [];
function check(name, fn) { queue.push([name, fn]); }

async function run() {
    for (const [name, fn] of queue) {
        try { await fn(); passed++; console.log('  ok  - ' + name); }
        catch (e) {
            console.error('  FAIL - ' + name + '\n        ' + e.message);
            process.exitCode = 1;
        }
    }
    console.log(`\n${passed} checks passed`);
}

// A stray unhandled rejection must never be silent either.
process.on('unhandledRejection', err => {
    console.error('  FAIL - unhandled rejection\n        ' + err);
    process.exitCode = 1;
});

/* --------------------------------------------------------------- DOM shim */

function makeEl() {
    const el = {
        className: '', id: '', innerHTML: '', value: '', textContent: '',
        style: { cssText: '', display: '' },
        dataset: {},
        classList: { add() {}, remove() {}, contains: () => false },
        appendChild() {}, remove() {}, insertBefore() {},
        addEventListener() {},
        querySelector: () => null,
        // Parse just enough of innerHTML to find tagged elements, so both the
        // DOM-assigned .value path and the data-attribute handler binding can be
        // exercised. Located elements are recorded per attribute in `found`.
        querySelectorAll(sel) {
            const attr = (sel.match(/^\[([\w-]+)\]$/) || [])[1];
            if (!attr) return [];
            // Match the attribute as a whole token, valued (attr="x") or bare (attr).
            const re = new RegExp('\\b' + attr + '(?=[\\s=>])', 'g');
            const count = (el.innerHTML.match(re) || []).length;
            const hits = Array.from({ length: count }, () => makeEl());
            el.found[attr] = (el.found[attr] || []).concat(hits);
            return hits;
        },
        found: {}
    };
    return el;
}

/**
 * Evaluate a set of extracted functions in a sandbox with supplied globals.
 * Returns the sandbox so the functions can be called.
 */
function sandboxWith(fnNames, globals) {
    const sandbox = Object.assign({
        console: { log() {}, warn() {}, error() {}, info() {} },
        JSON, String, Number, Array, Object, Date, Math, RegExp, parseInt,
        document: { getElementById: () => null, querySelector: () => null,
                    querySelectorAll: () => [], createElement: () => makeEl() },
        window: {}
    }, globals);
    sandbox.globalThis = sandbox;
    const ctx = vm.createContext(sandbox);
    // The renderers need the page-level escapeHtml. If the file under test does
    // not define one (the pre-fix state), inject a pass-through stub so the
    // renderers still RUN -- otherwise every behavioural XSS check would fail
    // with "escapeHtml not found", which proves only that the harness could not
    // be built, not that the payload escapes. With the stub, the pre-fix file
    // fails these checks by actually emitting a live <img>, which is the real
    // negative control.
    const helper = /function\s+escapeHtml\s*\(/.test(script)
        ? extractFunction(script, 'escapeHtml')
        : 'function escapeHtml(v) { return String(v == null ? \'\' : v); }';
    const src = [helper]
        .concat(fnNames.map(n => extractFunction(script, n)))
        .join('\n');
    new vm.Script(src, { filename: 'calendar-inline.js' }).runInContext(ctx);
    return ctx;
}

const XSS = '<img src=x onerror=alert(1)>';
const QUOTE_BREAK = `" onmouseover="alert(1)`;

console.log('calendar.html metadata preservation + XSS escaping');

/* ============================================================ DEFECT 1 ==== */

check('SERVER still replaces the metadata column wholesale', () => {
    // If zoe-data ever starts MERGING metadata server-side, the client-side
    // spread becomes redundant and this pin should be revisited deliberately
    // rather than left as cargo cult. Fail loudly on that change.
    const py = routerSource
        .replace(/"""[\s\S]*?"""/g, '')
        .split('\n')
        .filter(line => !/^\s*#/.test(line))
        .join('\n');
    assert.ok(/if key == "metadata":/.test(py),
        'the metadata branch in the update loop moved -- re-verify the wipe');
    const branch = py.slice(py.indexOf('if key == "metadata":'));
    const body = branch.slice(0, branch.indexOf('elif'));
    assert.ok(/updates\.append\("metadata = \?"\)/.test(body),
        'server must still assign the metadata column outright');
    assert.ok(!/json\.loads|\{\*\*|dict\(row\)\.get\("metadata"\)/.test(body),
        'server appears to MERGE metadata now -- the client spread may be redundant');
});

check('all four PUT sites spread the existing event metadata', () => {
    const sites = [
        'rescheduleEvent',      // drag to a new slot
        'linkTasksToEvent',     // drop a task onto an event
        'saveEventDuration',    // resize handle
        'saveEventLinkedTasks'  // tick a linked task
    ];
    for (const name of sites) {
        const body = stripComments(extractFunction(script, name));
        assert.ok(/metadata:\s*\{/.test(body), `${name} sends a metadata object`);
        const meta = body.slice(body.indexOf('metadata: {'));
        assert.ok(/\.\.\.\(?\s*event\.metadata/.test(meta),
            `${name} must spread event.metadata into the PUT or it wipes the column`);
    }
});

check('rescheduleEvent preserves description/prep/attendees through a drag', () => {
    // End-to-end: build the real payload the way a drag does and confirm the
    // fields the wipe destroyed survive.
    let sent = null;
    const ctx = sandboxWith(['rescheduleEvent'], {
        formatDateISO: () => '2026-07-21',
        loadEvents: async () => {},
        showNotification: () => {},
        authedApiRequest: async (url, opts) => { sent = JSON.parse(opts.body); }
    });
    const event = {
        id: 7, title: 'Dentist', duration: 60, category: 'personal',
        linkedTasks: [],
        metadata: {
            description: 'bring insurance card',
            get_ready_time: 15,
            travel_time: 25,
            prep_items: [{ id: 1, text: 'find card' }],
            attendees: [{ person_id: 3, name: 'Jason', role: 'participant' }],
            reminders: [{ offset_minutes: 30, message: 'leave now' }]
        }
    };
    return ctx.rescheduleEvent(event, new Date('2026-07-21'), '10:00').then(() => {
        assert.ok(sent, 'a PUT payload must have been sent');
        const m = sent.metadata;
        assert.strictEqual(m.description, 'bring insurance card',
            'description must survive a drag-reschedule');
        assert.strictEqual(m.get_ready_time, 15, 'get_ready_time must survive');
        assert.strictEqual(m.travel_time, 25, 'travel_time must survive');
        assert.strictEqual(m.prep_items.length, 1, 'prep_items must survive');
        assert.strictEqual(m.attendees.length, 1, 'attendees must survive');
        assert.strictEqual(m.reminders.length, 1, 'reminders must survive');
        assert.ok('linked_tasks' in m, 'linked_tasks must still be written');
    });
});

check('saveEventDuration preserves the same fields through a resize', () => {
    let sent = null;
    const ctx = sandboxWith(['saveEventDuration'], {
        formatDateISO: () => '2026-07-21',
        authedApiRequest: async (url, opts) => { sent = JSON.parse(opts.body); }
    });
    const event = {
        id: 8, title: 'Standup', date: new Date('2026-07-21'), time: '09:00',
        duration: 90, category: 'work', linkedTasks: [],
        metadata: { description: 'keep it short', prep_items: [{ id: 2, text: 'notes' }] }
    };
    return ctx.saveEventDuration(event).then(() => {
        assert.ok(sent, 'a PUT payload must have been sent');
        assert.strictEqual(sent.metadata.description, 'keep it short',
            'description must survive a resize');
        assert.strictEqual(sent.metadata.prep_items.length, 1,
            'prep_items must survive a resize');
    });
});

check('the metadata spread precedes linked_tasks so the fresh value wins', () => {
    let sent = null;
    const ctx = sandboxWith(['saveEventLinkedTasks'], {
        formatDateISO: () => '2026-07-21',
        authedApiRequest: async (url, opts) => { sent = JSON.parse(opts.body); }
    });
    const event = {
        id: 9, title: 'x', date: new Date('2026-07-21'), time: '09:00',
        duration: 60, category: 'work',
        linkedTasks: [{ id: 'a', text: 'new', completed: true }],
        metadata: { linked_tasks: JSON.stringify([{ id: 'a', text: 'stale' }]) }
    };
    return ctx.saveEventLinkedTasks(event).then(() => {
        const written = JSON.parse(sent.metadata.linked_tasks);
        assert.strictEqual(written[0].text, 'new',
            'the spread must not resurrect the stale linked_tasks value');
        assert.strictEqual(written[0].completed, true);
    });
});

check('openEventPanel no longer calls the non-existent attendees/reminders routes', () => {
    assert.ok(!/\/attendees['"`]/.test(code) && !/\$\{event\.id\}\/attendees/.test(code),
        'GET .../attendees does not exist on zoe-data; it 404s and empties the list');
    assert.ok(!/\$\{event\.id\}\/reminders/.test(code),
        'GET .../reminders does not exist on zoe-data; it 404s and empties the list');
});

check('the server really has no attendees/reminders sub-route (why they 404)', () => {
    const py = routerSource
        .replace(/"""[\s\S]*?"""/g, '')
        .split('\n')
        .filter(line => !/^\s*#/.test(line))
        .join('\n');
    assert.ok(!/attendees/.test(py),
        'routers/calendar.py gained an attendees route -- revisit the metadata read path');
    assert.ok(!/@router\.(get|post)\([^)]*reminders/.test(py),
        'routers/calendar.py gained an event-reminders route -- revisit the read path');
});

check('openEventPanel reads attendees and reminders from event.metadata', () => {
    const body = stripComments(extractFunction(script, 'openEventPanel'));
    assert.ok(/currentAttendees\s*=\s*\(?\s*metadata\.attendees/.test(body),
        'attendees must be read from the event metadata');
    assert.ok(/currentReminders\s*=\s*\(?\s*metadata\.reminders/.test(body),
        'reminders must be read from the event metadata');
});

/* ============================================================ DEFECT 2 ==== */

check('a page-level escapeHtml helper exists and escapes the single quote', () => {
    assert.ok(/function\s+escapeHtml\s*\(/.test(code),
        'calendar.html must define an escape helper (the stub in sandboxWith is '
        + 'a negative-control aid, not a substitute)');
    const ctx = sandboxWith([], { window: {} });
    assert.strictEqual(ctx.escapeHtml(`&<>"'`), '&amp;&lt;&gt;&quot;&#39;');
    assert.strictEqual(ctx.escapeHtml(null), '');
    assert.strictEqual(ctx.escapeHtml(undefined), '');
    assert.strictEqual(ctx.escapeHtml(0), '0');
});

check('escapeHtml delegates to window.zoeEscapeHtml when common.js is present', () => {
    assert.ok(/window\.zoeEscapeHtml\s*=/.test(commonSource),
        'js/common.js must still export zoeEscapeHtml');
    assert.ok(/common\.js/.test(html), 'calendar.html must load js/common.js');
    // Load order matters: common.js must come before the inline script.
    assert.ok(html.indexOf('js/common.js') < html.indexOf('function escapeHtml'),
        'js/common.js must be loaded before the inline script that uses it');
    let delegated = false;
    const ctx = sandboxWith([], {
        window: { zoeEscapeHtml: v => { delegated = true; return String(v); } }
    });
    ctx.escapeHtml('x');
    assert.ok(delegated, 'escapeHtml must prefer the shared common.js helper');
});

check('escapeHtml still works if common.js failed to load (inline fallback)', () => {
    assert.ok(/function\s+escapeHtml\s*\(/.test(code),
        'calendar.html must define its own escape helper for this to mean anything');
    const ctx = sandboxWith([], { window: {} });
    assert.strictEqual(ctx.escapeHtml('<b>'), '&lt;b&gt;',
        'the fallback must escape, not pass through raw');
});

check('renderTasks escapes task text and keeps the task id out of JS source', () => {
    const container = makeEl();
    const ctx = sandboxWith(['renderTasks', 'bindDataHandlers'], {
        document: { getElementById: () => container, querySelectorAll: () => [] },
        tasks: [{ id: `'),alert(1)//`, text: XSS, category: 'personal' }],
        selectedList: 'all',
        selectedTasks: [],
        tasksUnavailableLists: [],
        renderRemindersInSidebar: () => {},
        addTaskFromInput: () => {}, toggleTaskSelection: () => {},
        handleTaskDragStart: () => {}, handleTaskDragEnd: () => {},
        window: {}
    });
    ctx.renderTasks();
    const out = container.innerHTML;
    assert.ok(out.length > 0, 'tasks must have rendered');
    assert.ok(!out.includes(XSS), 'the verbatim payload must not survive');
    assert.ok(!/<img\s+src=x/.test(out), 'no real <img> element may be constructed');
    assert.ok(out.includes('&lt;img src=x'), 'the payload must appear escaped');
    assert.ok(!out.includes(`'),alert(1)`),
        'a crafted task id must not close the onclick string');
});

check('renderLinkedTasks escapes task text and keeps both ids out of JS source', () => {
    const list = makeEl();
    const summary = makeEl();
    const btn = makeEl();
    const byId = { linkedTasksList: list, completionSummary: summary, completeBtn: btn };
    const ctx = sandboxWith(['renderLinkedTasks', 'bindDataHandlers'], {
        document: { getElementById: id => byId[id] || makeEl() },
        window: {}
    });
    ctx.renderLinkedTasks({
        id: `'),alert(1)//`,
        linkedTasks: [{ id: 'a', text: XSS, completed: false }]
    });
    const out = list.innerHTML;
    assert.ok(!out.includes(XSS), 'linked task text must be escaped');
    assert.ok(out.includes('&lt;img src=x'), 'the payload must appear escaped');
    assert.ok(!out.includes(`'),alert(1)`),
        'a crafted event id must not close the onchange string');
});

check('createEventElement escapes the event title', () => {
    const el = makeEl();
    const ctx = sandboxWith(['createEventElement'], {
        document: { createElement: () => el },
        getPriorityColor: () => '#000',
        getCategoryColor: () => '#000',
        events: [],
        openEventPanel: () => {},
        openReminderPanel: () => {},
        handleEventDragStart: () => {},
        handleEventDragEnd: () => {},
        draggedTask: null,
        window: {}
    });
    ctx.createEventElement({
        id: 1, title: XSS, time: '09:00', duration: 60,
        category: 'personal', type: 'event', metadata: {}, linkedTasks: []
    }, 0);
    assert.ok(!el.innerHTML.includes(XSS), 'event title must be escaped');
    assert.ok(!/<img\s+src=x/.test(el.innerHTML), 'no real <img> may be constructed');
    assert.ok(el.innerHTML.includes('&lt;img src=x'), 'the payload must appear escaped');
});

check('showAttendeeSuggestions escapes person name and email', () => {
    const suggestions = makeEl();
    const input = makeEl();
    input.value = 'a';
    const byId = { attendeeSuggestions: suggestions, addAttendeeInput: input };
    const ctx = sandboxWith(['showAttendeeSuggestions', 'bindDataHandlers'], {
        document: { getElementById: id => byId[id] || makeEl() },
        filterPeople: () => [{ id: 1, name: XSS, email: `evil${QUOTE_BREAK}` }],
        filteredSuggestions: [],
        selectedSuggestionIndex: -1,
        window: {}
    });
    ctx.showAttendeeSuggestions();
    const out = suggestions.innerHTML;
    assert.ok(!out.includes(XSS), 'person name must be escaped');
    assert.ok(!out.includes(QUOTE_BREAK),
        'a quote-bearing email must not break out of its element');
    assert.ok(out.includes('&lt;img src=x'), 'the payload must appear escaped');
});

check('renderRemindersList sets the message via the DOM, not a value= attribute', () => {
    const container = makeEl();
    const ctx = sandboxWith(['renderRemindersList', 'bindDataHandlers'], {
        document: { getElementById: () => container },
        currentReminders: [{ id: 1, offset_minutes: 15, message: `x${QUOTE_BREAK}` }],
        window: {}
    });
    ctx.renderRemindersList();
    assert.ok(!/value="/.test(container.innerHTML.replace(/value="\d+"/g, '')),
        'the user-controlled message must not be interpolated into a value= attribute');
    assert.ok(!container.innerHTML.includes(QUOTE_BREAK),
        'a quote-bearing reminder message must never reach the markup');
    const inputs = container.found['data-reminder-message'] || [];
    assert.strictEqual(inputs.length, 1, 'the message input must be located');
    assert.strictEqual(inputs[0].value, `x${QUOTE_BREAK}`,
        'the message must still be displayed -- set through the DOM');
});

check('renderPrepItemsList sets the item text via the DOM, not a value= attribute', () => {
    const container = makeEl();
    const ctx = sandboxWith(['renderPrepItemsList', 'bindDataHandlers'], {
        document: { getElementById: () => container },
        currentPrepItems: [{
            id: 1, text: `x${QUOTE_BREAK}`, deadline_offset: 1,
            deadline_unit: 'days', list_type: 'personal',
            auto_add_to_list: false, completed: false
        }],
        window: {}
    });
    ctx.renderPrepItemsList();
    assert.ok(!container.innerHTML.includes(QUOTE_BREAK),
        'a quote-bearing prep item must never reach the markup');
    const inputs = container.found['data-prep-text'] || [];
    assert.strictEqual(inputs.length, 1, 'the text input must be located');
    assert.strictEqual(inputs[0].value, `x${QUOTE_BREAK}`,
        'the prep text must still be displayed -- set through the DOM');
});

check('openReminderPanel escapes title, category and description', () => {
    const panel = makeEl();
    const title = makeEl();
    const content = makeEl();
    const byId = { eventPanel: panel, eventTitle: title, eventContent: content };
    const ctx = sandboxWith(['openReminderPanel', 'bindDataHandlers'], {
        document: { getElementById: id => byId[id] || makeEl() },
        window: {}
    });
    ctx.openReminderPanel({
        reminderId: 1, title: XSS, time: '09:00', priority: 'high',
        category: XSS, description: XSS
    });
    const out = content.innerHTML;
    assert.ok(!out.includes(XSS), 'reminder fields must be escaped');
    assert.ok(!/<img\s+src=x/.test(out), 'no real <img> may be constructed');
    // The title goes through textContent, which is inherently safe.
    assert.strictEqual(title.textContent, `🔔 ${XSS}`,
        'the panel title uses textContent, so it stays raw but inert');
});

check('NO render template interpolates into an inline on* handler attribute', () => {
    // Greptile P1 on #1488, and it is correct: the browser HTML-DECODES an
    // event-handler attribute before compiling it as JavaScript, so an
    // HTML-escaped quote (&#39;) becomes a real quote at compile time and breaks
    // out of the JS string argument. HTML escaping CANNOT secure an inline
    // handler argument -- the value has to stay out of the JS source entirely.
    // Ids therefore travel in data-* attributes and are read back via dataset.
    const offenders = code
        .split('\n')
        .filter(l => /\bon[a-z]+\s*=\s*"[^"]*\$\{/.test(l))
        .map(l => l.trim().slice(0, 80));
    assert.deepStrictEqual(offenders, [],
        'inline handler attribute with an interpolated value: ' + offenders.join(' | '));
});

check('bindDataHandlers exists and every renderer calls it', () => {
    assert.ok(/function\s+bindDataHandlers\s*\(/.test(code),
        'the data-attribute handler binder must exist');
    const renderers = [
        'renderTasks', 'renderLinkedTasks', 'showAttendeeSuggestions',
        'renderAttendeesList', 'renderRemindersList', 'renderPrepItemsList',
        'displayNotifications', 'openReminderPanel'
    ];
    const missing = renderers.filter(
        n => !/bindDataHandlers\(/.test(stripComments(extractFunction(script, n))));
    assert.deepStrictEqual(missing, [],
        'renderer(s) emit data-* handlers but never bind them: ' + missing.join(', '));
});

check('a quote-bearing id survives as inert data, not as JS source', () => {
    // The exact payload that defeats HTML escaping inside an inline handler.
    const HOSTILE = `x'),alert(1)//`;
    const container = makeEl();
    const ctx = sandboxWith(['renderTasks', 'bindDataHandlers'], {
        document: { getElementById: () => container, querySelectorAll: () => [] },
        tasks: [{ id: HOSTILE, text: 'ok', category: 'personal' }],
        selectedList: 'all',
        selectedTasks: [],
        tasksUnavailableLists: [],
        renderRemindersInSidebar: () => {},
        addTaskFromInput: () => {},
        toggleTaskSelection: () => {},
        handleTaskDragStart: () => {},
        handleTaskDragEnd: () => {},
        window: {}
    });
    ctx.renderTasks();
    const out = container.innerHTML;
    assert.ok(!/\bon[a-z]+\s*=/.test(out),
        'no inline event-handler attribute may be emitted for a task row');
    assert.ok(!out.includes(HOSTILE),
        'the raw quote must not reach the markup');
    assert.ok(/data-toggle-task-id=/.test(out),
        'the id must travel in a data attribute instead');
    assert.ok(container.found['data-toggle-task-id'],
        'bindDataHandlers must locate and wire the tagged element');
});

check('the empty-list render path binds its handlers too', () => {
    // That branch returns early and still emits a data-add-to-list input; an
    // unbound one leaves "Add a new item" silently dead on an empty list.
    const container = makeEl();
    const ctx = sandboxWith(['renderTasks', 'bindDataHandlers'], {
        document: { getElementById: () => container, querySelectorAll: () => [] },
        tasks: [],
        selectedList: 'shopping',
        selectedTasks: [],
        tasksUnavailableLists: [],
        renderRemindersInSidebar: () => {},
        addTaskFromInput: () => {}, toggleTaskSelection: () => {},
        handleTaskDragStart: () => {}, handleTaskDragEnd: () => {},
        window: {}
    });
    ctx.renderTasks();
    assert.ok(/data-add-to-list=/.test(container.innerHTML),
        'the empty-list branch must render the add-item input');
    assert.ok(container.found['data-add-to-list'],
        'the empty-list branch must bind it as well as render it');
});

check('the notifications list escapes the notification message', () => {
    // Not in the original defect list, found while sweeping the file.
    const body = stripComments(extractFunction(script, 'displayNotifications'));
    assert.ok(/escapeHtml\(notification\.message\)/.test(body),
        'notification.message is user content going into innerHTML');
});

check('no unescaped user-content interpolation remains in the render templates', () => {
    // Belt and braces over the specific fields this fix covers, so a future
    // edit that reintroduces a raw interpolation is caught at source level.
    const raw = [
        /\$\{task\.text\}/, /\$\{reminder\.title\}/,
        /\$\{reminder\.description\}/, /\$\{reminder\.category\}/,
        /\$\{event\.title\}/, /\$\{person\.name\}/, /\$\{person\.email/,
        /\$\{attendee\.name\}/, /\$\{notification\.message\}/,
        /value="\$\{reminder\.message\}"/, /value="\$\{item\.text\}"/
    ];
    const offenders = [];
    for (const re of raw) {
        // console.log lines are not a render sink.
        // textContent assignment is an inherently safe sink, as is console.
        const hit = code.split('\n').find(
            l => re.test(l) && !/console\./.test(l) && !/\.textContent\s*=/.test(l));
        if (hit) offenders.push(hit.trim().slice(0, 70));
    }
    assert.deepStrictEqual(offenders, [],
        'raw user-content interpolation found: ' + offenders.join(' | '));
});

/* ============================================================ SW_VERSION == */

check('calendar.html is precached by the service worker', () => {
    assert.ok(/['"]\/calendar\.html['"]/.test(swSource),
        'calendar.html must be in the sw.js precache list for the bump to matter');
});

check('SW_VERSION was bumped past the pre-fix 4.68.3', () => {
    const m = swSource.match(/const SW_VERSION\s*=\s*'([\d.]+)'/);
    assert.ok(m, 'SW_VERSION must be present in sw.js');
    const cmp = (a, b) => {
        const pa = a.split('.').map(Number), pb = b.split('.').map(Number);
        for (let i = 0; i < 3; i++) {
            if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
        }
        return 0;
    };
    assert.ok(cmp(m[1], '4.68.3') > 0,
        `SW_VERSION ${m[1]} must exceed the pre-fix 4.68.3 or clients keep the cached calendar`);
});

run();
