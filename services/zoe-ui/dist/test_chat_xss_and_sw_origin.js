#!/usr/bin/env node
/**
 * Guard: chat.html must escape server-stored user text, and sw.js must leave
 * cross-origin scripts/styles UNROUTED.
 *
 * Two defects this pins down:
 *
 * 1. STORED XSS x2 in chat.html. Session titles are derived SERVER-SIDE from raw
 *    user message text (chat_session_title.py strips markdown but not HTML), so a
 *    message containing `<img src=x onerror=...>` becomes the stored title and was
 *    injected unescaped into the sidebar. Reminder `notification.message` is
 *    user-authored via POST /api/reminders and was injected unescaped into the
 *    notifications panel. chat.html carries TWO copies of displayNotifications
 *    (the later declaration wins by hoisting) — both must escape, or deleting one
 *    copy later silently re-exposes the sink.
 *
 * 2. sw.js routed cross-origin no-cors scripts and styles through NetworkFirst.
 *    On every SW-controlled reload chat.html lost all 9 of its cross-origin assets
 *    with net::ERR_FAILED (marked, DOMPurify, Prism + autoloader + theme CSS,
 *    Chart.js, QRCode, Leaflet + CSS) — no markdown, no sanitization, no
 *    highlighting, no charts, no maps. Reproduced in a real browser: first load 0
 *    failures, reload 9/9.
 *
 * This is a STATIC check on source text plus a live check of escapeHtml itself.
 * It cannot prove the page renders; it proves the escaping wrapper is present on
 * every sink and the origin guards have not been dropped again.
 *
 * Comments are stripped from both files before any regex assertion — otherwise
 * the prose above would satisfy the very patterns it describes.
 *
 * Run: node services/zoe-ui/dist/test_chat_xss_and_sw_origin.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

let failed = 0;
let passed = 0;
const check = (name, cond) => {
    console.log(`  ${cond ? 'PASS' : 'FAIL'} ${name}`);
    if (cond) passed++; else failed++;
};

// ---------------------------------------------------------------------------
// Comment stripping. Tracks string state so that `https://` inside a string and
// apostrophes inside comments don't desync the scanner. Also strips HTML
// comments so <!-- ... --> prose can't satisfy an assertion either.
// ---------------------------------------------------------------------------
function stripComments(src) {
    let out = '';
    let i = 0;
    const n = src.length;
    let quote = null; // ' " or `
    while (i < n) {
        const c = src[i];
        const d = src[i + 1];
        if (quote) {
            if (c === '\\') { out += c + (d === undefined ? '' : d); i += 2; continue; }
            if (c === quote) quote = null;
            out += c; i++; continue;
        }
        if (c === '"' || c === "'" || c === '`') { quote = c; out += c; i++; continue; }
        if (c === '/' && d === '/') {
            while (i < n && src[i] !== '\n') i++;
            continue;
        }
        if (c === '/' && d === '*') {
            i += 2;
            while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
            i += 2;
            continue;
        }
        if (c === '<' && src.startsWith('<!--', i)) {
            const end = src.indexOf('-->', i);
            i = end === -1 ? n : end + 3;
            continue;
        }
        out += c; i++;
    }
    return out;
}

// Brace-match a `function NAME(...) { ... }` block starting at `from`.
function functionBodyAt(src, start) {
    const braceStart = src.indexOf('{', start);
    let depth = 0;
    for (let i = braceStart; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error('unbalanced braces at offset ' + start);
}

function allFunctionBodies(src, name) {
    const sig = 'function ' + name + '(';
    const bodies = [];
    let idx = src.indexOf(sig);
    while (idx !== -1) {
        bodies.push(functionBodyAt(src, idx));
        idx = src.indexOf(sig, idx + sig.length);
    }
    return bodies;
}

const distDir = __dirname;
const chatRaw = fs.readFileSync(path.join(distDir, 'chat.html'), 'utf8');
const swRaw = fs.readFileSync(path.join(distDir, 'sw.js'), 'utf8');
const chat = stripComments(chatRaw);
const sw = stripComments(swRaw);

// Sanity: the stripper must not have eaten the code we are about to assert on.
check('stripComments preserved chat.html code', chat.includes('function escapeHtml('));
check('stripComments preserved sw.js code',
      (sw.match(/registerRoute\(/g) || []).length >= 6);
check('stripComments removed prose', !chat.includes('Comments are stripped'));

// ---------------------------------------------------------------------------
// 1a. escapeHtml really escapes. The static assertions below are only meaningful
//     if the wrapper they look for is a genuine escaper.
// ---------------------------------------------------------------------------
const escapeSrc = allFunctionBodies(chat, 'escapeHtml');
check('chat.html defines exactly one escapeHtml', escapeSrc.length === 1);
if (escapeSrc.length) {
    // eslint-disable-next-line no-eval
    const escapeHtml = eval('(' + escapeSrc[0] + ')');
    const payload = '<img src=x onerror=alert(1)>';
    const escaped = escapeHtml(payload);
    check('escapeHtml neutralises <img onerror> payload',
          !escaped.includes('<img') && escaped.includes('&lt;img'));
    check('escapeHtml escapes & first (no double-unescape)',
          escapeHtml('&lt;b&gt;') === '&amp;lt;b&amp;gt;');
    check('escapeHtml leaves no raw angle brackets',
          !/[<>]/.test(escapeHtml('<script>alert(1)</script>')));
}

// ---------------------------------------------------------------------------
// 1b. BOTH displayNotifications copies must escape notification.message.
// ---------------------------------------------------------------------------
const dnBodies = allFunctionBodies(chat, 'displayNotifications');
check('chat.html still carries both displayNotifications copies', dnBodies.length === 2);
check('at least one displayNotifications exists', dnBodies.length >= 1);
dnBodies.forEach((body, i) => {
    const n = i + 1;
    check(`displayNotifications #${n} does NOT inject raw \${notification.message}`,
          !/\$\{\s*notification\.message\s*\}/.test(body));
    check(`displayNotifications #${n} routes notification.message through escapeHtml`,
          /escapeHtml\([^)]*notification\.message/.test(body));
});

// ---------------------------------------------------------------------------
// 1c. Session titles must escape.
// ---------------------------------------------------------------------------
const titleLines = chat.split('\n').filter((l) => l.includes('class="session-title"') && l.includes('${'));
check('session-title render line found', titleLines.length >= 1);
titleLines.forEach((line, i) => {
    check(`session-title line #${i + 1} does NOT inject raw \${s.title}`,
          !/\$\{\s*s\.title\s*\}/.test(line));
    check(`session-title line #${i + 1} routes the title through escapeHtml`,
          /escapeHtml\([^)]*\.title/.test(line));
});

// ---------------------------------------------------------------------------
// 2. sw.js: script + widget-CSS + other-CSS routes must be same-origin only.
// ---------------------------------------------------------------------------
// Pull each registerRoute's predicate: text from just after `registerRoute(` up
// to the first comma at paren/brace depth 0.
function routePredicates(src) {
    const marker = 'registerRoute(';
    const preds = [];
    let idx = src.indexOf(marker);
    while (idx !== -1) {
        let i = idx + marker.length;
        let paren = 0, brace = 0;
        let start = i;
        for (; i < src.length; i++) {
            const c = src[i];
            if (c === '(') paren++;
            else if (c === ')') { if (paren === 0) break; paren--; }
            else if (c === '{') brace++;
            else if (c === '}') brace--;
            else if (c === ',' && paren === 0 && brace === 0) break;
        }
        preds.push(src.slice(start, i));
        idx = src.indexOf(marker, idx + marker.length);
    }
    return preds;
}

const preds = routePredicates(sw);
const GUARD = /url\.origin\s*===\s*self\.location\.origin/;
const isWidgetScoped = (p) => /\/widgets\//.test(p);

const scriptRoute = preds.find((p) => /destination\s*===\s*'script'/.test(p) && !isWidgetScoped(p));
const widgetCssRoute = preds.find((p) => /destination\s*===\s*'style'/.test(p) && isWidgetScoped(p));
const otherCssRoute = preds.find((p) => /destination\s*===\s*'style'/.test(p) && !isWidgetScoped(p));

check('sw.js: generic script route found', !!scriptRoute);
check('sw.js: generic script route is same-origin only', !!scriptRoute && GUARD.test(scriptRoute));
check('sw.js: widget CSS route found', !!widgetCssRoute);
check('sw.js: widget CSS route is same-origin only', !!widgetCssRoute && GUARD.test(widgetCssRoute));
check('sw.js: other CSS route found', !!otherCssRoute);
check('sw.js: other CSS route is same-origin only', !!otherCssRoute && GUARD.test(otherCssRoute));

// The image route's guard (PR #1405) must not regress while we are in here.
const imageRoute = preds.find((p) => /destination\s*===\s*'image'/.test(p));
check('sw.js: image route still same-origin only', !!imageRoute && GUARD.test(imageRoute));

// No blanket route may re-capture every script or style regardless of origin.
const blanketScript = preds.some((p) => /destination\s*===\s*'script'/.test(p)
                                        && !isWidgetScoped(p) && !GUARD.test(p));
const blanketStyle = preds.some((p) => /destination\s*===\s*'style'/.test(p)
                                       && !isWidgetScoped(p) && !GUARD.test(p));
check('sw.js: no unguarded blanket script route', !blanketScript);
check('sw.js: no unguarded blanket style route', !blanketStyle);

// Same-origin caching must be unaffected: the routes still exist and still use
// NetworkFirst with the cacheable-response plugin.
check('sw.js: script route still NetworkFirst-cached for same origin',
      /cacheName:\s*'zoe-js'/.test(sw) && /cacheName:\s*'zoe-css'/.test(sw));

assert.strictEqual(typeof failed, 'number');
console.log(failed
    ? `\n  ${failed} check(s) failed`
    : `\n  ${passed} checks passed — chat.html sinks escaped, sw.js cross-origin left unrouted`);
process.exit(failed ? 1 : 0);
