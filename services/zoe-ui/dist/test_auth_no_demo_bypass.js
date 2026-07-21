#!/usr/bin/env node
/**
 * Node harness guarding the two sign-in pages (index.html, auth.html) against
 * the client-side authentication bypass and the reflected DOM XSS.
 *
 * Background: js/auth.js isAuthenticated() trusts whatever sits in
 * localStorage under `zoe_session` -- it only checks session_id + expiry. So a
 * session fabricated in the browser is a *complete* auth bypass. Four catch
 * blocks used to do exactly that when zoe-auth was unreachable, the worst
 * being the guest path, which minted a role:'guest' session with no credential
 * check whatsoever.
 *
 * This harness asserts, against the real shipped HTML:
 *   - no fabricated-session scaffolding (demoSession / demo_mode) survives
 *   - no hardcoded credential pair (admin/admin, user/user, Admin/demo, ...)
 *   - NO catch block anywhere in either file calls handleSuccessfulLogin
 *   - the ?setup= parameter is never interpolated into innerHTML
 *   - login greeting + stored-session gate use the real AuthResponse shape
 *
 * Every regex assertion runs against COMMENT-STRIPPED source, so the prose in
 * the files (including the comments describing these very fixes) can never
 * satisfy or defeat a check.
 *
 * Run: node services/zoe-ui/dist/test_auth_no_demo_bypass.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

let passed = 0;
function check(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok  - ' + name);
    } catch (err) {
        process.exitCode = 1;
        console.error('  FAIL- ' + name + '\n        ' + (err && err.message));
    }
}

/**
 * Remove JS line/block comments without corrupting string or template-literal
 * contents. A naive stripper eats the "//" in "https://..." and silently
 * changes what the assertions below see, so track quoting state and escapes.
 *
 * Only ever run this over <script> contents (see scriptSource): prose
 * apostrophes in HTML body text ("Zoe's") would otherwise flip the quote state
 * and desynchronize the whole scan.
 */
function stripComments(src) {
    let out = '';
    let i = 0;
    const n = src.length;
    let quote = null; // "'", '"', '`'
    while (i < n) {
        const c = src[i];
        const next = src[i + 1];
        if (quote) {
            if (c === '\\') { out += c + (next || ''); i += 2; continue; }
            if (c === quote) quote = null;
            out += c; i++; continue;
        }
        // Escaped char outside a string, e.g. the \/ inside a regex literal
        // like /^https?:\/\//i -- must not be read as a comment opener.
        if (c === '\\') { out += c + (next || ''); i += 2; continue; }
        if (c === '"' || c === "'" || c === '`') { quote = c; out += c; i++; continue; }
        if (c === '/' && next === '/') {
            while (i < n && src[i] !== '\n') i++;
            continue;
        }
        if (c === '/' && next === '*') {
            i += 2;
            while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
            i += 2;
            continue;
        }
        out += c; i++;
    }
    return out;
}

/** All <script> bodies in an HTML file, comment-stripped and concatenated. */
function scriptSource(html) {
    const blocks = [];
    const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html)) !== null) blocks.push(stripComments(m[1]));
    if (!blocks.length) throw new Error('no <script> blocks found');
    return blocks.join('\n;\n');
}

/** Brace-match a block starting at the first '{' at or after `from`. */
function blockAt(src, from) {
    const start = src.indexOf('{', from);
    if (start === -1) return '';
    let depth = 0;
    for (let i = start; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    return src.slice(start);
}

/** Every `catch (...) { ... }` body in the source. */
function catchBodies(src) {
    const bodies = [];
    const re = /\bcatch\s*(\([^)]*\))?\s*\{/g;
    let m;
    while ((m = re.exec(src)) !== null) {
        bodies.push(blockAt(src, m.index + m[0].length - 1));
    }
    return bodies;
}

/**
 * Body of a function/method DEFINITION. Matching on a bare name is unsafe:
 * `this.showPasswordSetup(username)` is called ~300 lines before the method is
 * defined, and indexOf would happily brace-match the call site's enclosing
 * block instead. So anchor on a definition-shaped match at line start.
 */
function namedBlock(src, needle) {
    const esc = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const def = new RegExp('(?:^|\\n)\\s*' + esc + '\\s*\\{');
    const m = def.exec(src);
    if (!m) throw new Error('definition not found in source: ' + needle);
    return blockAt(src, m.index + m[0].length - 1);
}

const DIST = __dirname;
const raw = {
    index: fs.readFileSync(path.join(DIST, 'index.html'), 'utf8'),
    auth: fs.readFileSync(path.join(DIST, 'auth.html'), 'utf8'),
};
const src = {
    index: scriptSource(raw.index),
    auth: scriptSource(raw.auth),
};

// Guard the guard: the stripper must actually remove comments, must not mangle
// URLs or regex literals, and must leave no comment text in the real sources.
// Without this, prose in the files could quietly satisfy the checks below.
check('comment stripper works and preserves strings', () => {
    const sample = 'const a = "https://example.com"; // trailing\n/* block */ const b = 1;\n' +
        'const re = /^https?:\\/\\//i; const c = 2;';
    const got = stripComments(sample);
    assert.ok(got.includes('https://example.com'), 'URL inside string was damaged');
    assert.ok(!got.includes('trailing'), 'line comment survived');
    assert.ok(!got.includes('block'), 'block comment survived');
    assert.ok(/const c = 2;/.test(got), 'regex literal ate the rest of the line');
    assert.ok(!/AUTH BYPASS SENTINEL/.test(stripComments('// AUTH BYPASS SENTINEL')),
        'prose in a comment is still visible to assertions');
    // The real files: no comment markers may remain outside strings.
    for (const [name, s] of Object.entries(src)) {
        assert.ok(!/Fail closed/.test(s),
            name + '.html comments survived stripping - assertions are unreliable');
    }
});

// 1. No fabricated-session scaffolding anywhere in either page.
check('no demoSession / demo_mode scaffolding remains', () => {
    for (const [name, s] of Object.entries(src)) {
        assert.ok(!/demoSession/.test(s), name + '.html still builds a demoSession');
        assert.ok(!/demo_mode/.test(s), name + '.html still sets demo_mode');
        assert.ok(!/guestSession\s*=/.test(s), name + '.html still builds a guestSession');
    }
});

// 2. No hardcoded credential pairs. These were the demo unlocks on the
//    password and PIN paths (admin/admin, user/user, Admin/demo, User/demo).
check('no hardcoded credential pairs remain', () => {
    for (const [name, s] of Object.entries(src)) {
        assert.ok(!/===\s*['"]admin['"]/i.test(s),
            name + '.html still compares a credential against "admin"');
        assert.ok(!/===\s*['"]demo['"]/i.test(s),
            name + '.html still compares a credential against "demo"');
        assert.ok(!/admin\/admin|user\/user/i.test(s),
            name + '.html still advertises demo credentials');
    }
});

// 3. THE core invariant: no error path may create a session. If zoe-auth is
//    unreachable, sign-in fails closed. This covers all four original sites
//    (index password / index PIN / index guest / auth.html password) plus any
//    future catch block that tries the same trick.
check('no catch block calls handleSuccessfulLogin', () => {
    for (const [name, s] of Object.entries(src)) {
        const bodies = catchBodies(s);
        assert.ok(bodies.length > 0, 'expected catch blocks in ' + name + '.html');
        bodies.forEach((body, idx) => {
            assert.ok(!/handleSuccessfulLogin/.test(body),
                name + '.html catch block #' + (idx + 1) + ' creates a session on failure');
            assert.ok(!/localStorage\.setItem\(\s*['"]zoe_session['"]/.test(body),
                name + '.html catch block #' + (idx + 1) + ' writes zoe_session directly');
        });
    }
});

// 4. Each of the four handlers must surface the honest unavailable message.
check('login handlers report the service as unavailable', () => {
    for (const [name, s] of Object.entries(src)) {
        assert.ok(/AUTH_UNAVAILABLE_MESSAGE\s*=/.test(s),
            name + '.html does not define AUTH_UNAVAILABLE_MESSAGE');
        assert.ok(/sign-in service is unavailable/.test(s),
            name + '.html lost the honest failure message');
    }
    for (const fn of ['async function handlePasswordLogin(event)', 'async function handlePinLogin(event)', 'async function handleGuestLogin(event)']) {
        const body = namedBlock(src.index, fn);
        assert.ok(/AUTH_UNAVAILABLE_MESSAGE/.test(body), fn + ' does not report unavailability');
    }
    const authLogin = namedBlock(src.auth, 'async handleUserLogin(username, password, btn)');
    assert.ok(/AUTH_UNAVAILABLE_MESSAGE/.test(authLogin),
        'auth.html handleUserLogin does not report unavailability');
});

// 5. Guest path specifically: it never had a credential check, so any network
//    failure minted a role:'guest' session. Nothing may hand out a guest role
//    outside a real server response.
check('guest path never fabricates a guest role', () => {
    const guest = namedBlock(src.index, 'async function handleGuestLogin(event)');
    assert.ok(!/role\s*:\s*['"]guest['"]/.test(guest),
        'handleGuestLogin still assigns a guest role client-side');
    assert.ok(!/user_id\s*:\s*['"]guest['"]/.test(guest),
        'handleGuestLogin still assigns a guest user_id client-side');
});

// 6. Reflected DOM XSS: ?setup= flows into showPasswordSetup(). CSP here
//    allows unsafe-inline, so an innerHTML sink is directly exploitable.
check('showPasswordSetup does not interpolate username into innerHTML', () => {
    const body = namedBlock(src.auth, 'showPasswordSetup(username)');
    assert.ok(!/innerHTML/.test(body),
        'showPasswordSetup still assigns innerHTML');
    assert.ok(!/\$\{\s*username\s*\}/.test(body),
        'showPasswordSetup still interpolates username into a template literal');
    assert.ok(/createElement/.test(body),
        'showPasswordSetup should build the form with createElement');
    assert.ok(/\.value\s*=\s*username/.test(body),
        'showPasswordSetup should assign the username via the .value DOM API');
});

// 7. No innerHTML sink anywhere is fed a raw URL parameter.
check('URL parameters are not piped into innerHTML', () => {
    for (const [name, s] of Object.entries(src)) {
        const re = /innerHTML\s*=\s*[`'"][^`'"]*\$\{[^}]*(?:urlParams|searchParams|params)\.get/g;
        assert.ok(!re.test(s), name + '.html interpolates a URL parameter into innerHTML');
    }
    // ...and the redirect that produces ?setup= still encodes its value.
    assert.ok(/setup=['"]\s*\+\s*encodeURIComponent\(/.test(src.index),
        'index.html no longer encodes the ?setup= value it sends');
});

// 8. Real AuthResponse shape: identity lives under user_info, not top level.
//    Reading data.username rendered "Welcome, undefined!" on every real login.
check('login greeting reads the real AuthResponse shape', () => {
    const body = namedBlock(src.index, 'function handleSuccessfulLogin(data)');
    assert.ok(/user_info\s*&&\s*data\.user_info\.username|data\.user_info\?\.username/.test(body),
        'handleSuccessfulLogin does not read data.user_info.username');
    assert.ok(!/'Welcome, '\s*\+\s*data\.username/.test(body),
        'handleSuccessfulLogin still greets with the non-existent top-level username');
});

// 9. Same mistake in the stored-session gate: real sessions carry no top-level
//    user_id, so gating on it admitted only the fabricated demo sessions.
check('stored-session gate does not require a top-level user_id', () => {
    assert.ok(!/sessionData\.user_id\s*&&\s*sessionData\.expires_at/.test(src.index),
        'index.html still gates stored sessions on sessionData.user_id');
    assert.ok(/sessionData\.session_id\s*&&\s*sessionData\.expires_at/.test(src.index),
        'index.html should gate stored sessions on session_id + expires_at');
});

console.log('\n' + passed + ' checks passed' + (process.exitCode ? ' (with failures)' : ''));
if (process.exitCode) {
    console.error('FAILED');
}
