/*
 * The estate's Memories space (touch/home.html stage `memories`, js/touch-memories-space.js) - no browser, no network.
 *
 * Part A runs the REAL js/touch-memories-space.js against a DOM shim whose innerHTML setter throws (every row goes in
 *   through textContent) and fake apiGet/apiJson that play the server:
 *     guest   -> a refused read (Error('auth')) shows the sign-in prompt and NO rows, counts or names;
 *     member  -> groups, provenance badges, one large Forget target per item (people have none), private classes as counts;
 *     forget  -> confirm first (nothing is sent until "Forget it"), then POST /api/memories/mine/<id>/forget, then the row
 *                goes; a refused or failed forget keeps the row; a 401/403 mid-forget drops to the sign-in prompt;
 *     privacy -> after VIEW_TTL_MS with no touch the rows are removed from the DOM; unmount() drops a late response.
 * Part B pins home.html: the launcher swapped Ask Zoe for Memories while the Ask SURFACE (voice answers, the command bar)
 *   is untouched, the script loads before the estate script, and navigation unmounts.
 * Part C pins the module's discipline: no storage, no identity of its own, no innerHTML, one data read.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'js/touch-memories-space.js'), 'utf8');
const home = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

let checks = 0;
const ok = (c, m) => { assert(c, m); checks++; };
const eq = (a, b, m) => { assert.strictEqual(a, b, m); checks++; };

// ── DOM shim ───────────────────────────────────────────────────────────────────
function makeEl(tag) {
  const attrs = {};
  const el = {
    tagName: tag, className: '', children: [], parentNode: null, _text: '', listeners: {}, disabled: false, type: '',
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = el; el.children.push(c); return c; },
    removeChild(c) { el.children = el.children.filter((x) => x !== c); c.parentNode = null; return c; },
    replaceChildren() { el.children.forEach((c) => { c.parentNode = null; }); el.children = []; el._text = ''; },
    get firstChild() { return el.children[0] || null; },
    addEventListener(t, fn) { (el.listeners[t] = el.listeners[t] || []).push(fn); },
    click() { if (el.disabled) return; (el.listeners.click || []).forEach((fn) => fn({ target: el })); },
    get textContent() { return el._text + el.children.map((c) => c.textContent).join(' '); },
    set textContent(v) { el._text = String(v); el.children.forEach((c) => { c.parentNode = null; }); el.children = []; },
    set innerHTML(_v) { throw new Error('innerHTML used: rows must be set with textContent'); },
  };
  return el;
}
function walk(n, out = []) { out.push(n); n.children.forEach((c) => walk(c, out)); return out; }
const cls = (n, c) => (n.className || '').split(/\s+/).includes(c);
const all = (host, c) => walk(host).filter((n) => cls(n, c));
const byAct = (host, act) => walk(host).filter((n) => n.getAttribute && n.getAttribute('data-act') === act);

function makeClock() {
  let now = 0, seq = 0; const timers = new Map();
  return {
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]); now = next[1].at; next[1].fn();
      }
      now = end;
    },
    pending() { return timers.size; },
  };
}
const tick = () => new Promise((r) => setImmediate(r));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };

// Invented data only.
const SISTER = { key: 'people', label: 'People', kind: 'people', more: 0, entries: [
  { name: 'Marisol', relationship: 'sister', label: 'Marisol (sister)', facts: ['is flying in from Lisbon on Thursday'], when: '6 Oct' },
] };
const PREFS = { key: 'preferences', label: 'Preferences', kind: 'items', more: 0, items: [
  { id: 'p1', text: 'you like dark roast coffee', when: '6 Oct', epoch: 1, source: 'told' },
  { id: 'p2', text: 'you prefer <img src=x onerror=alert(1)> oat milk', when: '', epoch: 2, source: 'inferred' },
] };
const DATES = { key: 'dates', label: 'Dates', kind: 'items', more: 0, items: [
  { id: 'd1', text: 'your birthday is on 14 March', when: '5 Oct', epoch: 3, source: 'told' },
] };
const clone = (o) => JSON.parse(JSON.stringify(o));
const viewOf = (groups, extra) => Object.assign({ groups, private: {}, unsure: 0, total: groups.reduce((a, g) => a + (g.items || g.entries).length, 0) }, extra || {});

function boot(opts) {
  opts = opts || {};
  const clock = makeClock();
  const window = { document: { createElement: makeEl }, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout };
  const ctx = { window, String, Array, Object, Math, encodeURIComponent, Error, Promise, console };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const Z = window.ZoeMemoriesSpace;
  const host = makeEl('div');
  const sidRef = { v: 'sess-a' };
  const log = { gets: [], posts: [], toasts: [], signIns: 0, meta: [], confirms: [] };
  let getImpl = opts.get || (() => Promise.resolve(viewOf([clone(SISTER), clone(PREFS), clone(DATES)], { private: { health: 2, feelings: 1 } })));
  let postImpl = opts.post || (() => Promise.resolve({ forgotten: true }));
  const deps = {
    apiGet: (u) => { log.gets.push(u); return getImpl(u); },
    apiJson: (m, u) => { log.posts.push([m, u]); return postImpl(m, u); },
    setMeta: (t) => log.meta.push(t),
    toast: (t) => log.toasts.push(t),
    signIn: () => { log.signIns++; },
    confirm: (o, yes) => { log.confirms.push({ o, yes }); },
    sid: () => sidRef.v,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
  };
  return { Z, host, deps, log, clock, sidRef, setGet: (f) => { getImpl = f; }, setPost: (f) => { postImpl = f; } };
}

(async () => {
  // ── A1: a guest sees a sign-in prompt and nothing else ──────────────────────────────────────────
  {
    const t = boot({ get: () => Promise.reject(new Error('auth')) });
    t.Z.mount(t.host, t.deps);
    ok(t.host.textContent.includes('Looking through'), 'a loading state first');
    await settle();
    const txt = t.host.textContent;
    ok(txt.includes('Sign in to see what I know about you'), 'sign-in prompt for a refused read');
    ok(!/Marisol|coffee|birthday|private|migraine/i.test(txt), 'no rows, names or counts for a guest');
    eq(all(t.host, 'memitem').length, 0, 'no item rows');
    eq(byAct(t.host, 'forget').length, 0, 'no forget targets');
    byAct(t.host, 'signin')[0].click();
    eq(t.log.signIns, 1, 'the sign-in button raises the existing PIN card');
    eq(t.log.gets.length, 1);
    eq(t.log.gets[0], '/api/memories/mine', 'one read, of the member endpoint, with no identity in the URL');
    eq(t.log.posts.length, 0);
    eq(t.log.meta[t.log.meta.length - 1], '', 'no count in the header for a guest');
  }

  // ── A2: a member's view ──────────────────────────────────────────────────────────────────────
  {
    const t = boot();
    t.Z.mount(t.host, t.deps);
    await settle();
    const txt = t.host.textContent;
    ok(txt.includes('Marisol (sister)') && txt.includes('is flying in from Lisbon on Thursday'), 'people entry');
    ok(txt.includes('you like dark roast coffee') && txt.includes('your birthday is on 14 March'));
    ok(txt.includes('<img src=x onerror=alert(1)>'), 'markup in a row is text, never HTML (innerHTML throws in the shim)');
    eq(all(t.host, 'memgrp').length, 3, 'three groups');
    eq(all(t.host, 'memitem').length, 3, 'one row per item');
    eq(byAct(t.host, 'forget').length, 3, 'one Forget per item');
    eq(all(t.host, 'memperson').length, 1);
    ok(!walk(all(t.host, 'memperson')[0]).some((n) => n.tagName === 'button'), 'a person has no row to forget - voice does that');
    ok(txt.includes('You told me') && txt.includes('I picked this up'), 'provenance badges');
    ok(cls(all(t.host, 'membadge')[1], 'inferred'));
    ok(txt.includes('3 private notes (health, feelings) aren’t shown on this screen'), 'private classes as a count');
    ok(!/migraine|diagnos|insulin/i.test(txt));
    eq(t.log.meta[t.log.meta.length - 1], '4 things');
    ok(byAct(t.host, 'forget').every((b) => /Forget:/.test(b.getAttribute('aria-label'))), 'labelled for accessibility');
    // every action target is a real button (CSS makes it >= 56px; the harness pins the markup)
    ok(walk(t.host).filter((n) => n.tagName === 'button').every((b) => b.type === 'button'));
  }

  // ── A3: forget = confirm, then POST, then the row goes ───────────────────────────────────────────
  {
    const t = boot();
    t.Z.mount(t.host, t.deps);
    await settle();
    byAct(t.host, 'forget')[0].click();
    eq(t.log.confirms.length, 1, 'a confirm step comes first');
    eq(t.log.confirms[0].o.body, 'you like dark roast coffee', 'the confirm names what will be forgotten');
    eq(t.log.confirms[0].o.yes, 'Forget it');
    eq(t.log.posts.length, 0, 'nothing is sent before the confirm');
    // declined: the modal just closes (no callback) -> still nothing
    ok(t.host.textContent.includes('dark roast'), 'declined = kept');
    t.log.confirms[0].yes();
    eq(t.log.posts.length, 1);
    eq(t.log.posts[0][0], 'POST');
    eq(t.log.posts[0][1], '/api/memories/mine/p1/forget');
    await settle();
    ok(!t.host.textContent.includes('dark roast'), 'the forgotten row is gone');
    eq(all(t.host, 'memitem').length, 2);
    ok(t.log.toasts.includes('Forgotten'));
    eq(t.log.meta[t.log.meta.length - 1], '3 things');
    eq(all(all(t.host, 'memgrp').find((g) => g.getAttribute('data-group') === 'preferences'), 'memgc')[0].textContent, '1', 'group count follows');
    // forgetting the last row of a group removes the group
    byAct(t.host, 'forget')[0].click(); t.log.confirms[1].yes(); await settle();
    eq(all(t.host, 'memgrp').length, 2, 'the emptied group is removed');
    // ids with odd characters are encoded
    const t2 = boot({ get: () => Promise.resolve(viewOf([{ key: 'other', label: 'Other', kind: 'items', more: 0, items: [{ id: 'a/b c', text: 'x', when: '', epoch: 1, source: 'told' }] }])) });
    t2.Z.mount(t2.host, t2.deps); await settle();
    byAct(t2.host, 'forget')[0].click(); t2.log.confirms[0].yes();
    eq(t2.log.posts[0][1], '/api/memories/mine/a%2Fb%20c/forget');
  }

  // ── A4: forget failures keep the row; a dead session drops to sign-in ─────────────────────────────────
  {
    const t = boot();
    t.Z.mount(t.host, t.deps); await settle();
    t.setPost(() => Promise.reject(new Error('http 503')));
    const btn = byAct(t.host, 'forget')[0];
    btn.click(); t.log.confirms[0].yes(); await settle();
    ok(t.host.textContent.includes('dark roast'), 'a failed forget keeps the row');
    ok(t.log.toasts.some((m) => /try again/i.test(m)));
    eq(btn.disabled, false, 'and lets you try again');
    ok(!t.log.toasts.includes('Forgotten'), 'never "Forgotten" for a failure');
    // 404: already gone elsewhere
    t.setPost(() => Promise.reject(new Error('http 404')));
    const gets0 = t.log.gets.length;
    t.setGet(() => Promise.resolve(viewOf([clone(SISTER), { key: 'preferences', label: 'Preferences', kind: 'items', more: 0, items: [clone(PREFS.items[1])] }, clone(DATES)])));
    btn.click(); t.log.confirms[1].yes(); await settle();
    ok(t.log.toasts.includes('Already gone'));
    eq(t.log.gets.length, gets0 + 1, 'a 404 is answered by re-reading the server list, never by a local guess');
    ok(!t.host.textContent.includes('dark roast'), 'and the server list no longer has it');
    // the session died mid-way
    t.setPost(() => Promise.reject(new Error('auth')));
    byAct(t.host, 'forget')[0].click(); t.log.confirms[2].yes(); await settle();
    ok(t.host.textContent.includes('Sign in to see what I know about you'), '401/403 -> sign-in prompt');
    ok(!/Marisol|birthday|oat milk/.test(t.host.textContent), 'and the rows leave the screen');
  }

  // ── A5: everything forgotten -> the empty state, the private line stays ───────────────────────────────
  {
    const t = boot({ get: () => Promise.resolve(viewOf([{ key: 'other', label: 'Other', kind: 'items', more: 0, items: [{ id: 'o1', text: 'you keep a bike in the shed', when: '', epoch: 1, source: 'told' }] }], { private: { health: 1 } })) });
    t.Z.mount(t.host, t.deps); await settle();
    byAct(t.host, 'forget')[0].click(); t.log.confirms[0].yes(); await settle();
    ok(!t.host.textContent.includes('bike'));
    ok(t.host.textContent.includes('1 private note (health) isn’t shown'), 'the private line survives the last forget');
    ok(t.host.textContent.includes('I don’t know much about you yet'));
    const e = boot({ get: () => Promise.resolve(viewOf([])) });
    e.Z.mount(e.host, e.deps); await settle();
    ok(e.host.textContent.includes('I don’t know much about you yet') && byAct(e.host, 'forget').length === 0);
  }

  // ── A6: a wall display forgets what it showed ───────────────────────────────────────────────────
  {
    const t = boot();
    t.Z.mount(t.host, t.deps); await settle();
    ok(t.host.textContent.includes('Marisol'));
    t.clock.advance(t.Z.VIEW_TTL_MS - 1000);
    ok(t.host.textContent.includes('Marisol'), 'still there inside the window');
    // a touch inside the window restarts it
    t.host.listeners.pointerdown.forEach((f) => f());   // the host is the scroller; scroll does not bubble
    t.clock.advance(t.Z.VIEW_TTL_MS - 1000);
    ok(t.host.textContent.includes('Marisol'), 'a touch restarts the window');
    t.clock.advance(1500);
    ok(!/Marisol|coffee|birthday|private note/.test(t.host.textContent), 'rows leave the DOM after the privacy window');
    ok(t.host.textContent.includes('Hidden to keep your things private'));
    byAct(t.host, 'show')[0].click(); await settle();
    eq(t.log.gets.length, 2, 'showing again re-asks the server (which re-checks who is there)');
    ok(t.host.textContent.includes('Marisol'));
    // and when it is a guest at that point, it is a guest's view
    t.clock.advance(t.Z.VIEW_TTL_MS + 1);
    t.setGet(() => Promise.reject(new Error('auth')));
    byAct(t.host, 'show')[0].click(); await settle();
    ok(t.host.textContent.includes('Sign in to see') && !t.host.textContent.includes('Marisol'));
  }

  // ── A7: unmount (navigation) drops a late response and the timer ──────────────────────────────────
  {
    let release;
    const t = boot({ get: () => new Promise((r) => { release = r; }) });
    t.Z.mount(t.host, t.deps);
    t.Z.unmount();
    release(viewOf([clone(SISTER)])); await settle();
    ok(!t.host.textContent.includes('Marisol'), 'a response that lands after navigation never paints');
    const u = boot();
    u.Z.mount(u.host, u.deps); await settle();
    eq(u.clock.pending(), 2, 'the idle window and the who-is-here watch run while the rows are up');
    u.Z.unmount();
    eq(u.clock.pending(), 0, 'unmount cancels it');
    // a second mount supersedes the first (a guest result must not paint over a newer member view)
    let relA;
    const v = boot({ get: () => new Promise((r) => { relA = r; }) });
    v.Z.mount(v.host, v.deps);
    const firstRelease = relA;
    v.setGet(() => Promise.resolve(viewOf([clone(DATES)])));
    v.Z.mount(v.host, v.deps); await settle();
    firstRelease(viewOf([clone(SISTER)])); await settle();
    ok(v.host.textContent.includes('birthday') && !v.host.textContent.includes('Marisol'), 'the newest mount wins');
    // a failed read is not "nothing known"
    const f = boot({ get: () => Promise.reject(new Error('http 503')) });
    f.Z.mount(f.host, f.deps); await settle();
    ok(f.host.textContent.includes('couldn’t reach my memory') && !f.host.textContent.includes('don’t know much'), 'failed != empty');
    byAct(f.host, 'retry')[0].click();
    eq(f.log.gets.length, 2);
  }

  // ── A8: review findings - nothing private outlives its session, its navigation or its idle window ────────────────
  {
    // unmount empties the host: a screen that is hidden but live (a card painted over it) must not still hold rows
    const t = boot();
    t.Z.mount(t.host, t.deps); await settle();
    ok(t.host.textContent.includes('Marisol'));
    t.Z.unmount();
    ok(t.host.textContent === '' && t.host.children.length === 0, 'unmount removes the rows from the DOM');

    // the session changes under an untouched screen -> rows are re-asked (and a guest sees the prompt)
    const u = boot();
    u.Z.mount(u.host, u.deps); await settle();
    u.clock.advance(4900);
    ok(u.host.textContent.includes('Marisol'), 'same session: still shown');
    u.sidRef.v = 'sess-guest';
    u.setGet(() => Promise.reject(new Error('auth')));
    u.clock.advance(300); await settle();
    ok(!u.host.textContent.includes('Marisol') && u.host.textContent.includes('Sign in to see'), 'a changed session never keeps the old rows up');
    eq(u.log.gets.length, 2);

    // a touch cannot keep rows up past a session change either (the watch is independent of the idle window)
    const w = boot();
    w.Z.mount(w.host, w.deps); await settle();
    for (let i = 0; i < 3; i++) { w.clock.advance(4000); w.host.listeners.pointerdown.forEach((f) => f()); }
    w.sidRef.v = 'sess-other';
    w.setGet(() => Promise.reject(new Error('auth')));
    w.clock.advance(5100); await settle();
    ok(!w.host.textContent.includes('Marisol'));

    // scroll counts as activity
    const v = boot();
    v.Z.mount(v.host, v.deps); await settle();
    v.clock.advance(v.Z.VIEW_TTL_MS - 1000);
    v.host.listeners.scroll.forEach((f) => f());
    v.clock.advance(v.Z.VIEW_TTL_MS - 1000);
    ok(v.host.textContent.includes('Marisol'), 'scrolling restarts the idle window');

    // the idle window fires while the confirm modal is open: confirming does nothing and repaints nothing
    const x = boot();
    x.Z.mount(x.host, x.deps); await settle();
    byAct(x.host, 'forget')[0].click();
    x.clock.advance(x.Z.VIEW_TTL_MS + 1);
    ok(x.host.textContent.includes('Hidden to keep your things private'));
    x.log.confirms[0].yes(); await settle();
    eq(x.log.posts.length, 0, 'no POST once the screen was hidden');
    ok(!/Marisol|coffee/.test(x.host.textContent), 'and nothing repaints');

    // a different person is signed in by the time the modal is confirmed
    const y = boot();
    y.Z.mount(y.host, y.deps); await settle();
    byAct(y.host, 'forget')[0].click();
    y.sidRef.v = 'sess-b';
    y.log.confirms[0].yes(); await settle();
    eq(y.log.posts.length, 0, 'a forget asked under one session is not sent under another');

    // a forget that lands after the screen was hidden never repaints rows
    let releasePost;
    const z = boot({ post: () => new Promise((r) => { releasePost = r; }) });
    z.Z.mount(z.host, z.deps); await settle();
    byAct(z.host, 'forget')[2].click(); z.log.confirms[0].yes();   // the only item of its group: the removal would repaint
    z.clock.advance(z.Z.VIEW_TTL_MS + 1);
    releasePost({ forgotten: true }); await settle();
    ok(!/Marisol|coffee|birthday/.test(z.host.textContent) && z.host.textContent.includes('Hidden to keep your things private'), 'a late forget response never repaints over the hidden screen');

    // a hidden screen keeps no copy of the rows to re-render
    const q = boot();
    q.Z.mount(q.host, q.deps); await settle();
    q.clock.advance(q.Z.VIEW_TTL_MS + 1);
    q.setGet(() => Promise.reject(new Error('http 503')));
    byAct(q.host, 'show')[0].click(); await settle();
    ok(!/Marisol|coffee/.test(q.host.textContent), 'a failed re-read shows no stale rows');
  }

  // ── B: home.html ─────────────────────────────────────────────────────────────────────────────
  {
    const dests = home.slice(home.indexOf('var DESTS=['), home.indexOf('];', home.indexOf('var DESTS=[')));
    ok(/\{id:'memories',n:'Memories',ic:'memories'/.test(dests), 'the launcher has a Memories tile');
    ok(!/id:'ask'|Ask Zoe/.test(dests), 'and no Ask Zoe tile');
    ok(/LIC=\{[\s\S]*?memories:'<path/.test(home) || /\n\s+memories:'<path/.test(home), 'tile icon defined');
    ok(!/\n\s+ask:'<path d="M4 5\.5h16/.test(home), 'the retired Ask icon is removed');
    const order = home.slice(home.indexOf('var ORDER=['), home.indexOf('];', home.indexOf('var ORDER=[')));
    ok(order.includes("['memories','Memories']"), 'the (hidden) stage picker lists Memories');
    ok(order.includes("['ask','Ask Zoe']"), 'and keeps the ask surface (design: only the launcher shortcut goes)');
    // The Ask SURFACE is untouched: voice answers, the live conversation, the typed command bar.
    ok(/\n\s+ask:\{home:true, bg:GROUND/.test(home), 'FULL.ask (the answer surface) stays');
    ok(/placeholder="Ask Zoe…"/.test(home), 'the command bar still asks Zoe');
    ok(home.includes("function askShowAnswer(") && home.includes("chat:'ask'"), 'voice/chat answers still land on Ask');
    ok(/\n\s+memories:\{home:true, bg:GROUND/.test(home), 'FULL.memories stage');
    ok(home.includes("if(id==='memories')wireMemories();"), 'show() wires it');
    ok(home.includes('window.ZoeMemoriesSpace.unmount()'), 'show() unmounts it on every navigation');
    const scriptAt = home.indexOf('/js/touch-memories-space.js?v=mem-1');
    const estateAt = home.indexOf('<script>\n(function(){');
    ok(scriptAt > 0 && scriptAt < estateAt, 'loaded (cache-busted) before the estate script, so a ?domain=memories arrival finds it');
    // the wiring hands over the SESSION-carrying helpers and picks no identity
    const w = home.slice(home.indexOf('function wireMemories()'), home.indexOf('// ---- Settings: full-screen expanding sections'));
    ok(w.includes('apiGet:apiGet') && w.includes('apiJson:apiJson') && w.includes('sid:sid') && w.includes('__showAuthCard'));
    ok(!/user_id|localStorage|sessionStorage|zoe_session|<input|<textarea|prompt\(/.test(w), 'wireMemories names no member, reads no storage, adds no input');
    // a surface name opens it: the voice arrival path (?domain=memories) needs no new map entry
    ok(home.includes("(FULL[dom]?dom:'home')"));
    // desktop and touch stay separate
    ok(!/href=["']\/(memories|dashboard)\.html/.test(w));
  }

  // ── C: the module's discipline ─────────────────────────────────────────────────────────────────
  {
    ok(!/localStorage|sessionStorage|indexedDB|document\.cookie/.test(src), 'nothing is stored in the browser');
    ok(!/innerHTML|insertAdjacentHTML|outerHTML|document\.write/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), 'no HTML injection path');
    ok(!/user_id|X-Session-ID|member_id|wing/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), 'the page names no member and builds no credential');
    ok(!/<input|<textarea|createElement\('input'\)|createElement\('textarea'\)/.test(src), 'no text input: taps only');
    const reads = src.match(/deps\.apiGet\(/g) || [];
    eq(reads.length, 1, 'exactly one data read');
    eq((src.match(/MINE_URL = '\/api\/memories\/mine'/g) || []).length, 1);
    ok(!/\/api\/memories\/(?!mine)/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), 'only the member-scoped routes');
  }

  console.log('touch memories space: ' + checks + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
