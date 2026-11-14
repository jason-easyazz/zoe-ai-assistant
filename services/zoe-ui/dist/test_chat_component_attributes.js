#!/usr/bin/env node
/**
 * Guard: a value from a chat component's props can never leave the attribute or
 * the text it was put in (chat.html, the generative-UI renderers).
 *
 * The defect: chat.html's escapeHtml() escaped & < > but NOT " or ', and the
 * renderers used it for attribute values and inside inline handlers:
 *
 *     <input name="title" value="${escapeHtml(props.title)}">
 *     <button onclick="handleConfirmation('id', true, '${escapeHtml(props.yes_action)}')">
 *     <a href="${escapeHtml(props.url)}">
 *
 * A title containing `"` closed the attribute and the rest became new attributes
 * (`onfocus=`); a yes_action containing `'` closed the script string (the browser
 * decodes entities BEFORE it parses an inline handler, so escaping cannot fix
 * that one); a `javascript:` url ran on click. Component props carry text the
 * person typed and text the model wrote after reading the web.
 *
 * What this harness does, with the real renderer text from chat.html:
 *   1. escapeHtml / escapeAttr / safeComponentUrl unit checks;
 *   2. renders every component that has an attribute or handler sink twice - once
 *      with plain props, once with hostile ones - into a container stub, tokenises
 *      the HTML the way a browser would, and requires
 *        - the SAME tags and attribute names in both (nothing was added),
 *        - no on*="" attribute containing any prop text,
 *        - every href to be http(s) / a path;
 *   3. clicks the listeners the renderers bind and checks the action text arrives
 *      unchanged;
 *   4. negative controls: the same suite against copies of the page with each old
 *      defect put back must fail.
 *
 * It is a function-level check with an HTML tokeniser written for it. It does NOT
 * run a browser's parser.
 *
 * Run: node services/zoe-ui/dist/test_chat_component_attributes.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failed = 0;
let passed = 0;
const check = (name, cond) => {
    console.log(`  ${cond ? 'PASS' : 'FAIL'} ${name}`);
    if (cond) passed++; else failed++;
};

function extractFunction(src, name) {
    const sig = 'function ' + name + '(';
    const start = src.indexOf(sig);
    if (start === -1) throw new Error('function not found: ' + name);
    let depth = 0;
    for (let i = src.indexOf('{', start); i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}' && --depth === 0) return src.slice(start, i + 1);
    }
    throw new Error('unbalanced braces for: ' + name);
}

// --- a small HTML tokeniser: tags, attribute names, decoded attribute values ---
function decodeEntities(s) {
    return s.replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[e]));
}
function tokenise(html) {
    const tags = [];
    let i = 0;
    while (i < html.length) {
        const lt = html.indexOf('<', i);
        if (lt === -1) break;
        const m = /^<([a-zA-Z][a-zA-Z0-9-]*)/.exec(html.slice(lt, lt + 40));
        if (!m) { i = lt + 1; continue; }
        const tag = { name: m[1].toLowerCase(), attrs: [] };
        let j = lt + m[0].length;
        for (;;) {
            while (j < html.length && /[\s/]/.test(html[j])) j++;
            if (j >= html.length || html[j] === '>') { j++; break; }
            let k = j;
            while (k < html.length && !/[\s/>=]/.test(html[k])) k++;
            const attr = { name: html.slice(j, k).toLowerCase(), value: '' };
            j = k;
            while (j < html.length && /\s/.test(html[j])) j++;
            if (html[j] === '=') {
                j++;
                while (j < html.length && /\s/.test(html[j])) j++;
                const q = html[j];
                if (q === '"' || q === "'") {
                    const end = html.indexOf(q, j + 1);
                    attr.value = html.slice(j + 1, end === -1 ? html.length : end);
                    j = end === -1 ? html.length : end + 1;
                } else {
                    k = j;
                    while (k < html.length && !/[\s>]/.test(html[k])) k++;
                    attr.value = html.slice(j, k);
                    j = k;
                }
            }
            attr.value = decodeEntities(attr.value);
            tag.attrs.push(attr);
        }
        tags.push(tag);
        i = j;
    }
    return tags;
}
const shape = (tags) => tags.map((t) => t.name + '[' + t.attrs.map((a) => a.name).join(',') + ']').join(' ');

// --- a container stub: keeps innerHTML, records the listeners a renderer binds ---
function makeContainer() {
    const listeners = [];
    const el = (sel) => ({
        sel, dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {} },
        addEventListener(type, fn) { listeners.push({ sel, type, fn, el: this }); },
        appendChild() {}, focus() {}, querySelector: () => el('?'), querySelectorAll: () => [],
    });
    const c = el('container');
    c.className = 'ui-component';
    c.innerHTML = '';
    c.querySelector = (sel) => el(sel);
    c.querySelectorAll = (sel) => {
        const m = /^\[data-([a-z-]+)\]$/.exec(sel);
        if (!m) return [];
        const camel = m[1].replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
        return tokenise(c.innerHTML)
            .filter((t) => t.attrs.some((a) => a.name === 'data-' + m[1]))
            .map((t) => { const e = el(sel); e.dataset[camel] = t.attrs.find((a) => a.name === 'data-' + m[1]).value; return e; });
    };
    c.listeners = listeners;
    return c;
}

const RENDERERS = [
    'renderInfoCard', 'renderConfirmation', 'renderForm', 'renderResearchEvidence',
    'renderCalendarEventForm', 'renderNoteCreateForm', 'renderJournalCreateForm',
    'renderListAddForm', 'renderReminderCreateForm', 'renderTimerCreateForm',
    'renderLinkPreview', 'renderImage', 'renderPriceTable', 'renderAgentTrace',
];

// Every string prop any of those renderers reads, filled with one value.
const propsWith = (v, url) => ({
    icon: v, title: v, description: v, yes_action: v, yes_label: v, no_label: v,
    action: v, submit_label: v, category: v, date: v, time: v, end_time: v, location: v,
    content: v, mood: v, list_type: v, item: v, label: v, minutes: 5, priority: v,
    url, image: url, alt: v, caption: v, query: v,
    actions: [{ type: v, data: v, label: v }],
    fields: [{ label: v, name: v, placeholder: v, type: v, required: true }],
    rows: [{ store: v, product: v, price: v, url }, { store: v, product: v, price: v, action: v, action_label: v }],
    steps: [{ icon: v, name: v, detail: v }],
    results: [{ rank: 1, name: v, value: v, location: v }],
    screenshots: [{ image_base64: v }], sources: [v],
});

const PLAIN = 'plain';
const HOSTILE = `x" autofocus onfocus="PWNED(1)" y='z' ');PWNED(2);// ><img src=x onerror=PWNED(3)>`;
const HOSTILE_URLS = ['javascript:PWNED(4)', 'JaVa\tScRiPt:PWNED(5)', ' javascript:PWNED(6)', 'data:text/html,<script>PWNED(7)</script>', 'vbscript:PWNED(8)'];

function loadPage(html) {
    const ctx = vm.createContext({
        URL, window: { location: { origin: 'https://zoe.test' } }, console,
        document: { getElementById: () => null, dispatchEvent() {}, querySelector: () => null },
        setTimeout: () => 0, CustomEvent: function () {},
        renderMarkdown: (t) => String(t).replace(/</g, '&lt;'),
        calls: [],
    });
    const consts = (/const FORM_INPUT_TYPES = [^\n]+/.exec(html) || [''])[0];
    const helpers = ['escapeHtml', 'escapeAttr'].concat(html.includes('function safeComponentUrl(') ? ['safeComponentUrl'] : []);
    vm.runInContext([
        consts.replace(/^const /, 'var '),
        ...helpers.concat(RENDERERS).map((n) => extractFunction(html, n)),
        'function handleComponentAction(type, data) { calls.push(["action", type, data]); }',
        'function handleConfirmation(id, yes, action) { calls.push(["confirm", yes, action]); }',
        'function handleFormSubmit(event, action) { calls.push(["form", action]); }',
        'function tryPrompt(text) { calls.push(["prompt", text]); }',
    ].join('\n\n'), ctx);
    return ctx;
}

// Runs the render suite against one copy of the page; returns the failures.
function renderSuite(html) {
    const problems = [];
    const ctx = loadPage(html);
    for (const name of RENDERERS) {
        const plain = makeContainer();
        ctx[name](plain, propsWith(PLAIN, 'https://example.test/a'));
        const hostile = makeContainer();
        try {
            ctx[name](hostile, propsWith(HOSTILE, HOSTILE_URLS[0]));
        } catch (e) {
            problems.push(`${name}: threw on hostile props (${e.message})`);
            continue;
        }
        const a = tokenise(plain.innerHTML);
        const b = tokenise(hostile.innerHTML);
        if (a.length < 2) problems.push(`${name}: rendered nothing to check`);
        if (shape(a) !== shape(b)) problems.push(`${name}: hostile props changed the tags / attributes`);
        for (const tag of b) {
            for (const attr of tag.attrs) {
                if (/^on/.test(attr.name) && /PWNED|plain|x"|autofocus/.test(attr.value)) {
                    problems.push(`${name}: prop text inside ${tag.name} ${attr.name}=""`);
                }
            }
        }
        for (const url of HOSTILE_URLS) {
            const c = makeContainer();
            ctx[name](c, propsWith(PLAIN, url));
            for (const tag of tokenise(c.innerHTML)) {
                const href = tag.attrs.find((x) => x.name === 'href');
                if (href && !/^(https?:\/\/|\/|#)/.test(href.value)) {
                    problems.push(`${name}: href carries ${JSON.stringify(url)}`);
                }
            }
        }
    }
    return problems;
}

const chat = fs.readFileSync(path.join(__dirname, 'chat.html'), 'utf8');
const ctx = loadPage(chat);

// ---------------------------------------------------------------------------
// 1. The helpers.
// ---------------------------------------------------------------------------
check('escapeHtml escapes both quotes as well as & < >',
      ctx.escapeHtml(`a"b'c<d>e&f`) === 'a&quot;b&#39;c&lt;d&gt;e&amp;f');
check('escapeHtml still turns a newline into <br>', ctx.escapeHtml('a\nb') === 'a<br>b');
check('escapeHtml takes a missing value and a number without throwing',
      ctx.escapeHtml(null) === '' && ctx.escapeHtml(undefined) === '' && ctx.escapeHtml(7) === '7');
check('escapeAttr writes no <br> and escapes both quotes',
      ctx.escapeAttr(`a\n"b'`) === 'a\n&quot;b&#39;');
check('safeComponentUrl keeps http, https and same-origin paths',
      ctx.safeComponentUrl('https://example.test/a?b=1') === 'https://example.test/a?b=1'
      && ctx.safeComponentUrl('http://example.test/') === 'http://example.test/'
      && ctx.safeComponentUrl('/calendar.html') === '/calendar.html');
check('safeComponentUrl refuses javascript:, data:, vbscript: (any case, tabs, leading space) and a missing url',
      HOSTILE_URLS.every((u) => ctx.safeComponentUrl(u) === '#')
      && ctx.safeComponentUrl('') === '#' && ctx.safeComponentUrl(null) === '#');

// ---------------------------------------------------------------------------
// 2. Every renderer, plain vs hostile.
// ---------------------------------------------------------------------------
const problems = renderSuite(chat);
problems.forEach((p) => console.log('    ' + p));
check(`${RENDERERS.length} renderers: hostile props add no tag, no attribute, no handler text, no script href`,
      problems.length === 0);

// No renderer writes escapeHtml into an attribute value or a handler any more.
check('no ="${escapeHtml(...)}" attribute value is left in chat.html', !/="\$\{escapeHtml\(/.test(chat));
check('no inline handler takes an escapeHtml(...) argument', !/on[a-z]+="[^"\n]*\$\{escapeHtml\(/.test(chat));

// ---------------------------------------------------------------------------
// 3. The bound listeners deliver the text unchanged.
// ---------------------------------------------------------------------------
{
    const card = makeContainer();
    ctx.renderInfoCard(card, propsWith(HOSTILE, 'https://example.test/a'));
    card.listeners.filter((l) => l.type === 'click').forEach((l) => l.fn.call(l.el));
    check('info card: a click passes the action type and data as plain strings',
          JSON.stringify(ctx.calls.pop()) === JSON.stringify(['action', HOSTILE, HOSTILE]));

    const confirm = makeContainer();
    ctx.renderConfirmation(confirm, propsWith(HOSTILE));
    const yes = confirm.listeners.find((l) => l.sel === '.confirm-btn.yes');
    const no = confirm.listeners.find((l) => l.sel === '.confirm-btn.no');
    yes.fn();
    check('confirmation: Yes passes yes_action as a plain string',
          JSON.stringify(ctx.calls.pop()) === JSON.stringify(['confirm', true, HOSTILE]));
    no.fn();
    check('confirmation: No sends no action', JSON.stringify(ctx.calls.pop()) === JSON.stringify(['confirm', false, '']));

    const form = makeContainer();
    ctx.renderForm(form, propsWith(HOSTILE));
    form.listeners.find((l) => l.sel === 'form' && l.type === 'submit').fn({ preventDefault() {} });
    check('form: submit passes the action as a plain string',
          JSON.stringify(ctx.calls.pop()) === JSON.stringify(['form', HOSTILE]));
    const input = tokenise(form.innerHTML).find((t) => t.name === 'input');
    check('form: an unknown input type falls back to text; the name round-trips exactly',
          input.attrs.find((a) => a.name === 'type').value === 'text'
          && input.attrs.find((a) => a.name === 'name').value === HOSTILE);
    const dated = makeContainer();
    ctx.renderForm(dated, { fields: [{ name: 'when', type: 'date' }] });
    check('form: a known input type is kept',
          tokenise(dated.innerHTML).find((t) => t.name === 'input').attrs.find((a) => a.name === 'type').value === 'date');
}

// ---------------------------------------------------------------------------
// 4. Negative controls: each old defect, put back, must be reported.
// ---------------------------------------------------------------------------
const mutate = (from, to) => { if (!chat.includes(from)) throw new Error('control anchor missing: ' + from); return chat.split(from).join(to); };

const oldEscaper = mutate(`.replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/\\n/g, '<br>');`, `.replace(/\\n/g, '<br>');`)
    .split('="${escapeAttr(').join('="${escapeHtml(');
check('control: escapeHtml without quotes, used in attributes, is caught',
      renderSuite(oldEscaper).some((p) => /changed the tags \/ attributes/.test(p)));

const oldHandler = mutate('<button class="confirm-btn yes">',
    `<button class="confirm-btn yes" onclick="handleConfirmation('\${confirmId}', true, '\${escapeHtml(props.yes_action || '')}')">`);
check('control: yes_action written into an onclick is caught',
      renderSuite(oldHandler).some((p) => /renderConfirmation: prop text inside button onclick/.test(p)));

const oldHref = mutate(`if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return value;`, 'return value;');
check('control: a link that keeps any scheme is caught',
      renderSuite(oldHref).some((p) => /renderLinkPreview: href carries/.test(p))
      && renderSuite(oldHref).some((p) => /renderPriceTable: href carries/.test(p)));

console.log(failed
    ? `\n  ${failed} check(s) failed`
    : `\n  ${passed} checks passed - chat component props stay inside their attribute`);
process.exit(failed ? 1 : 0);
