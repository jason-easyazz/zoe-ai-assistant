#!/usr/bin/env node
/**
 * Guard: every function in chat.html's inline scripts is declared ONCE.
 *
 * The defect this pins down (desktop overhaul plan, Wave 1, "duplicate-function
 * cleanup in chat.html"): eight functions were declared twice.
 *
 *   - navigateToPage, openNotifications, closeNotifications, loadNotifications,
 *     displayNotifications, formatNotificationTime, handleNotificationClick lived
 *     in the main script AND, byte-identical, in the small script at the end of
 *     the page. The later script runs last, so ITS copies were the live ones and
 *     the main script's were dead. A fix made to the copy a person found first
 *     changed nothing; deleting "the dead one" silently swapped the live code.
 *     displayNotifications is a stored-XSS sink (a reminder's text), so that
 *     mattered.
 *   - escapeAttr was declared twice in the SAME script with different bodies.
 *     Function declarations hoist, so the later, weaker copy won: it wrote the
 *     words "null" / "undefined" into an attribute for a missing value.
 *
 * What this harness does, against the real shipped chat.html:
 *   1. finds every `function NAME(` declaration in the inline scripts (comments
 *      and string contents removed first) and fails on any name declared twice;
 *   2. checks the helpers the plan says to KEEP are still there, once;
 *   3. runs the surviving escapeAttr and the surviving displayNotifications (the
 *      real function text, against a small DOM stub) with a hostile reminder;
 *   4. negative controls: the detector is run on copies of the page with each
 *      old duplicate put back, and must go red.
 *
 * It is a source + function-level check. It does NOT load the page in a browser.
 *
 * Run: node services/zoe-ui/dist/test_chat_single_definitions.js
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

// Inline <script> bodies only (a `src=` script has no body of its own).
function inlineScripts(html) {
    const out = [];
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html))) {
        if (/\bsrc\s*=/.test(m[1])) continue;
        out.push(m[2]);
    }
    return out;
}

// Remove comments and blank out string / template TEXT (keeping `${...}` code),
// so a `function foo(` written inside a string or a comment is never counted.
function codeOnly(src) {
    let out = '';
    let i = 0;
    const n = src.length;
    const stack = []; // open template literals: brace depth of the current ${...}
    let depth = 0;
    while (i < n) {
        const c = src[i];
        const d = src[i + 1];
        if (c === '/' && d === '/') { while (i < n && src[i] !== '\n') i++; continue; }
        if (c === '/' && d === '*') {
            i += 2;
            while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
            i += 2;
            continue;
        }
        if (c === '/' && regexMayStart(out)) {
            // A regex literal: its quotes and braces are not code (`/"/g`).
            let inClass = false;
            i++;
            while (i < n && src[i] !== '\n' && (inClass || src[i] !== '/')) {
                if (src[i] === '\\') i++;
                else if (src[i] === '[') inClass = true;
                else if (src[i] === ']') inClass = false;
                i++;
            }
            i++;
            out += '/x/';
            continue;
        }
        if (c === '"' || c === "'") {
            i++;
            while (i < n && src[i] !== c && src[i] !== '\n') { if (src[i] === '\\') i++; i++; }
            i++;
            out += c + c;
            continue;
        }
        if (c === '`') { i = skipTemplate(i + 1); out += '``'; continue; }
        if (c === '{') depth++;
        if (c === '}') {
            if (stack.length && depth === stack[stack.length - 1]) {
                stack.pop();
                i = skipTemplate(i + 1);
                continue;
            }
            depth--;
        }
        out += c;
        i++;
    }
    return out;

    // `/` starts a regex (not a division) after an operator, an opening
    // bracket, or one of the keywords that take an expression.
    function regexMayStart(before) {
        const t = before.replace(/\s+$/, '');
        if (!t) return true;
        if (/[(,=:[!&|?{};+\-*%<>~^]$/.test(t)) return true;
        return /(?:^|[^\w$])(?:return|typeof|case|in|of|delete|void|throw)$/.test(t);
    }

    // Skip template text from `i`; stop after the closing backtick, or just
    // inside a `${` (whose code is then scanned by the main loop).
    function skipTemplate(j) {
        while (j < n) {
            if (src[j] === '\\') { j += 2; continue; }
            if (src[j] === '`') return j + 1;
            if (src[j] === '$' && src[j + 1] === '{') { stack.push(depth); out += ' '; return j + 2; }
            j++;
        }
        return j;
    }
}

function declaredNames(html) {
    const names = [];
    const re = /(?:^|[^\w$.])function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(/g;
    for (const script of inlineScripts(html)) {
        const code = codeOnly(script);
        let m;
        while ((m = re.exec(code))) names.push(m[1]);
    }
    return names;
}

function duplicateDeclarations(html) {
    const seen = new Map();
    for (const name of declaredNames(html)) seen.set(name, (seen.get(name) || 0) + 1);
    return [...seen].filter(([, count]) => count > 1).map(([name]) => name).sort();
}

function functionSource(html, name) {
    const sig = 'function ' + name + '(';
    const start = html.indexOf(sig);
    if (start === -1) return null;
    let depth = 0;
    for (let i = html.indexOf('{', start); i < html.length; i++) {
        if (html[i] === '{') depth++;
        else if (html[i] === '}' && --depth === 0) return html.slice(start, i + 1);
    }
    return null;
}

const chat = fs.readFileSync(path.join(__dirname, 'chat.html'), 'utf8');

// ---------------------------------------------------------------------------
// 1. One declaration per name.
// ---------------------------------------------------------------------------
const names = declaredNames(chat);
check('the scanner sees chat.html\'s functions (more than 100 declarations)', names.length > 100);
const dupes = duplicateDeclarations(chat);
check('no function is declared twice in chat.html' + (dupes.length ? ' - twice: ' + dupes.join(', ') : ''),
      dupes.length === 0);

// ---------------------------------------------------------------------------
// 2. What the plan says to keep is still there, exactly once.
// ---------------------------------------------------------------------------
const KEEP = [
    'openMoreOverlay', 'closeMoreOverlay', 'updateTimeDate', 'navigateToPage',
    'openNotifications', 'closeNotifications', 'loadNotifications', 'displayNotifications',
    'formatNotificationTime', 'handleNotificationClick', 'escapeAttr', 'escapeHtml',
];
for (const name of KEEP) {
    check(`${name} is declared exactly once`, names.filter((n) => n === name).length === 1);
}
// The page still calls them: an inline handler naming a deleted function only
// throws when clicked, so a load-time crawl would never see it.
check('the More menu still calls openMoreOverlay / closeMoreOverlay',
      /onclick="openMoreOverlay\(\)"/.test(chat) && /onclick="closeMoreOverlay\(\)"/.test(chat));
check('the page still calls updateTimeDate and loadNotifications at start',
      /updateTimeDate\(\);/.test(chat) && /loadNotifications\(\);/.test(chat));

// ---------------------------------------------------------------------------
// 3. The surviving copies behave.
// ---------------------------------------------------------------------------
const ctx = vm.createContext({});
vm.runInContext(functionSource(chat, 'escapeAttr') + '\n' + functionSource(chat, 'escapeHtml') + '\n'
    + functionSource(chat, 'formatNotificationTime') + '\n' + functionSource(chat, 'displayNotifications'), ctx);

const escapeAttr = ctx.escapeAttr;
check('escapeAttr writes nothing for a missing value (the weaker copy wrote "null")',
      escapeAttr(null) === '' && escapeAttr(undefined) === '');
check('escapeAttr escapes both quotes, angle brackets and &',
      escapeAttr(`a"b'c<d>e&f`) === 'a&quot;b&#39;c&lt;d&gt;e&amp;f');
check('escapeAttr turns a number into its text', escapeAttr(7) === '7');

const content = { innerHTML: '' };
const bell = { classes: new Set(), classList: null };
bell.classList = { add: (c) => bell.classes.add(c), remove: (c) => bell.classes.delete(c) };
ctx.document = {
    getElementById: (id) => (id === 'notificationsContent' ? content : null),
    querySelector: (sel) => (sel === '.notifications-btn' ? bell : null),
};
ctx.displayNotifications([{
    id: 5, is_delivered: false, notification_time: new Date().toISOString(),
    message: '<img src=x onerror=alert(1)> pick up Sam',
}]);
check('a hostile reminder is drawn as text, not markup',
      !content.innerHTML.includes('<img') && content.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt; pick up Sam'));
check('an unread reminder lights the bell', bell.classes.has('has-notifications'));
ctx.displayNotifications([]);
check('no reminders: the panel says so and the bell is cleared',
      content.innerHTML.includes('No notifications') && !bell.classes.has('has-notifications'));

// ---------------------------------------------------------------------------
// 4. Negative controls: put each old duplicate back and the detector goes red.
// ---------------------------------------------------------------------------
const lastScriptEnd = chat.lastIndexOf('</script>');
const withTailCopy = chat.slice(0, lastScriptEnd)
    + '\nfunction displayNotifications(notifications) { /* stale copy */ }\n'
    + chat.slice(lastScriptEnd);
check('control: a second displayNotifications in a later script is caught',
      duplicateDeclarations(withTailCopy).join() === 'displayNotifications');

const anchor = chat.indexOf('function renderAgentTrace(');
const withWeakEscape = chat.slice(0, anchor)
    + 'function escapeAttr(text) { return String(text); }\n        '
    + chat.slice(anchor);
check('control: a second escapeAttr in the same script is caught',
      anchor !== -1 && duplicateDeclarations(withWeakEscape).join() === 'escapeAttr');

const inString = chat.slice(0, lastScriptEnd)
    + '\nvar s = "function displayNotifications(x) {}"; // function escapeAttr(y) {}\n'
    + 'var t = `function loadNotifications(z) { ${1 + 1} }`;\n'
    + chat.slice(lastScriptEnd);
check('control: a declaration written in a string, a template or a comment is NOT counted',
      duplicateDeclarations(inString).length === 0);

console.log(failed
    ? `\n  ${failed} check(s) failed`
    : `\n  ${passed} checks passed - chat.html declares each function once`);
process.exit(failed ? 1 : 0);
