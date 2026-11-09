/*
 * The estate's Transcript check (touch/home.html stage `checks`, js/touch-transcript-check.js) - no browser, no network.
 *
 * Runs the REAL js/touch-transcript-check.js against a DOM shim whose innerHTML setter throws (every server string goes in through
 * textContent) and a fake server:
 *   guest    -> a refused read shows the sign-in prompt and NO sentence;
 *   item     -> the sentence is written as a live-caption line (.cvf .cvl), the uncertain word is the only highlighted button,
 *               "3 of 7" progress, no <input>/<textarea> anywhere (no keyboard);
 *   chips    -> touching the word shows one big chip per model's word + Play it / None of these / Skip; touching a chip sends
 *               exactly that alternative, shows the chosen word, and advances; the next item appears;
 *   none     -> None of these offers Say it and Fix on phone; Say it posts /listen then polls until the voice answer lands;
 *   resume   -> a second mount reads the server's remaining list and carries on from "4 of 7";
 *   privacy  -> after VIEW_TTL_MS with no touch the sentence leaves the DOM; unmount() drops a late response.
 * Part B pins home.html wiring: the stage, the script before the estate script, the unmount on navigation, the voice-card hand-off.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'js/touch-transcript-check.js'), 'utf8');
const home = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

let checks = 0;
const ok = (c, m) => { assert(c, m); checks++; };
const eq = (a, b, m) => { assert.strictEqual(a, b, m); checks++; };

function makeEl(tag) {
  const attrs = {};
  const el = {
    tagName: tag, className: '', children: [], parentNode: null, _text: '', listeners: {}, disabled: false, type: '', src: '', alt: '',
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = el; el.children.push(c); return c; },
    removeChild(c) { el.children = el.children.filter((x) => x !== c); c.parentNode = null; return c; },
    replaceChildren() { el.children.forEach((c) => { c.parentNode = null; }); el.children = []; el._text = ''; },
    get firstChild() { return el.children[0] || null; },
    addEventListener(t, fn) { (el.listeners[t] = el.listeners[t] || []).push(fn); },
    click() { (el.listeners.click || []).forEach((fn) => fn({ target: el })); },
    get textContent() { return el._text + el.children.map((c) => c.textContent).join(''); },
    set textContent(v) { el._text = String(v); el.children.forEach((c) => { c.parentNode = null; }); el.children = []; },
    set innerHTML(_v) { throw new Error('innerHTML used: strings must be set with textContent'); },
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
  };
}
const tick = () => new Promise((r) => setImmediate(r));
const settle = async () => { for (let i = 0; i < 8; i++) await tick(); };

// Invented data only.
const ITEMS = [
  { id: 'a', kind: 'slot', before: 'tell Marisol to meet me at', span: 'seven', after: 'on Thursday', pick: 'seven', alts: [{ i: 0, word: 'seven', votes: 2 }, { i: 1, word: 'eleven', votes: 1 }] },
  { id: 'b', kind: 'slot', before: 'we should ask', span: 'Delphine', after: 'about the garden', pick: 'Delphine', alts: [{ i: 0, word: 'Delphine', votes: 2 }, { i: 1, word: 'Delfina', votes: 1 }, { i: 2, word: 'Dolphin', votes: 1 }] },
];

function boot(opts) {
  opts = opts || {};
  const clock = makeClock();
  const window = { document: { createElement: makeEl }, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout };
  const ctx = { window, String, Array, Object, Math, encodeURIComponent, Error, Promise, console };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const Z = window.ZoeTranscriptCheck;
  const host = makeEl('div');
  const log = { calls: [], meta: [], toasts: [], signIns: 0, played: 0 };
  const server = { items: opts.items || ITEMS.map((x) => JSON.parse(JSON.stringify(x))), done: opts.done || 0, status: opts.status || 200, answered: null, listenState: 'open' };
  const deps = {
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    request: (m, p, body) => {
      log.calls.push([m, p, body]);
      if (server.status !== 200) return Promise.resolve({ status: server.status, json: {} });
      if (m === 'GET' && p === '/api/transcript-check') return Promise.resolve({ status: 200, json: { items: server.items, done: server.done, total: server.done + server.items.length } });
      if (/\/answer$/.test(p)) return Promise.resolve({ status: 200, json: { ok: true } });
      if (/\/skip$/.test(p)) return Promise.resolve({ status: 200, json: { ok: true } });
      if (/\/listen$/.test(p)) return Promise.resolve({ status: 200, json: { ok: true } });
      if (/\/state$/.test(p)) return Promise.resolve({ status: 200, json: server.listenState === 'answered' ? { status: 'answered', answer: 'eight' } : { status: 'open' } });
      return Promise.resolve({ status: 404, json: {} });
    },
    blob: () => Promise.resolve({ size: 1 }),
    play: () => { log.played++; },
    objectUrl: () => 'blob:x',
    setMeta: (t) => log.meta.push(t), toast: (t) => log.toasts.push(t), signIn: () => { log.signIns++; },
  };
  return { Z, host, deps, log, clock, server, mount: () => Z.mount(host, deps) };
}

(async () => {
  // guest: refused -> sign-in prompt, no sentence
  {
    const t = boot({ status: 403 });
    t.mount(); await settle();
    ok(!t.host.textContent.includes('Marisol'), 'a guest sees no sentence');
    eq(byAct(t.host, 'signin').length, 1, 'sign-in button');
    byAct(t.host, 'signin')[0].click(); eq(t.log.signIns, 1, 'sign-in handed to the estate');
  }
  // item: caption style, one highlighted word, progress, no input
  const t = boot({ done: 3 });
  t.mount(); await settle();
  ok(all(t.host, 'cvf').length === 1 && all(t.host, 'cvl').length === 1, 'sentence uses the live-caption components');
  eq(all(t.host, 'cvl')[0].textContent, 'tell Marisol to meet me at seven on Thursday', 'the sentence is written out');
  eq(all(t.host, 'tcw').length, 1, 'exactly one highlighted word');
  eq(t.log.meta[t.log.meta.length - 1], '4 of 5', 'progress counts the finished ones');
  ok(walk(t.host).every((n) => n.tagName !== 'input' && n.tagName !== 'textarea'), 'no keyboard input anywhere');
  eq(all(t.host, 'tcchip').length, 0, 'chips only after touching the word');
  // chips
  byAct(t.host, 'word')[0].click();
  const chips = all(t.host, 'tcchip');
  eq(chips.map((c) => c.textContent).join('|'), 'seven|eleven', 'one chip per model word');
  eq(['play', 'none', 'skip'].map((a) => byAct(t.host, a).length).join(','), '1,1,1', 'play / none / skip');
  byAct(t.host, 'play')[0].click(); await settle(); eq(t.log.played, 1, 'play it plays the snippet');
  chips[1].click(); await settle();
  const ans = t.log.calls.filter((c) => /\/answer$/.test(c[1]));
  eq(ans.length, 1, 'one answer'); eq(JSON.stringify(ans[0][2]), '{"alt":1}', 'exactly the touched alternative');
  eq(ans[0][1], '/api/transcript-check/a/answer', 'for that item');
  eq(all(t.host, 'cvl')[0].textContent.includes('Delphine'), true, 'the next item is up');
  eq(t.log.meta[t.log.meta.length - 1], '5 of 5', 'progress moved');
  // none -> say it -> poll
  byAct(t.host, 'word')[0].click(); byAct(t.host, 'none')[0].click();
  eq(byAct(t.host, 'sayit').length + byAct(t.host, 'phone').length, 2, 'say it + fix on phone');
  byAct(t.host, 'sayit')[0].click(); await settle();
  ok(t.log.calls.some((c) => /\/listen$/.test(c[1])), 'listen requested');
  t.server.listenState = 'answered'; t.clock.advance(1600); await settle();
  eq(t.host.textContent.includes('All done'), true, 'the voice answer finishes the run');
  // resume
  {
    const r = boot({ done: 6, items: [ITEMS[1]] });
    r.mount(); await settle();
    eq(r.log.meta[r.log.meta.length - 1], '7 of 7', 'resumes where it was left');
  }
  // skip
  {
    const s = boot(); s.mount(); await settle();
    byAct(s.host, 'word')[0].click(); byAct(s.host, 'skip')[0].click(); await settle();
    ok(s.log.calls.some((c) => /\/a\/skip$/.test(c[1])), 'skip posts');
  }
  // privacy window + unmount
  {
    const p = boot(); p.mount(); await settle();
    p.clock.advance(121000);
    ok(!p.host.textContent.includes('Marisol'), 'the sentence leaves the DOM after the privacy window');
    const u = boot(); u.mount(); u.Z.unmount(); await settle();
    eq(u.host.textContent, '', 'unmount empties the host and drops the late response');
  }
  // who said this?
  {
    const W = [{ id: 'w1', kind: 'who', before: '', span: 'put the garden things away on Sunday', after: '', pick: 'Quinn', alts: [{ i: 0, word: 'Quinn', votes: 61 }, { i: 2, word: 'Voice 1', votes: 40, cluster: true }] }];
    const w = boot({ items: JSON.parse(JSON.stringify(W)) });
    w.mount(); await settle();
    eq(all(w.host, 'cvl')[0].textContent, W[0].span, 'the turn is a caption line');
    eq(all(w.host, 'tcw').length, 0, 'no highlighted word on a who item');
    eq(all(w.host, 'tcchip').map((c) => c.textContent).join('|'), 'Quinn|Same as Voice 1|Someone new', 'candidates, the unnamed voice, someone new');
    ok(walk(w.host).every((n) => n.tagName !== 'input' && n.tagName !== 'textarea'), 'no keyboard');
    byAct(w.host, 'new')[0].click(); await settle();
    eq(JSON.stringify(w.log.calls.filter((c) => /\/answer$/.test(c[1]))[0][2]), '{"new":true}', 'someone new posts new:true');
    eq(byAct(w.host, 'sayname').length + byAct(w.host, 'later').length, 2, 'then offers to name them by voice, or later');
    byAct(w.host, 'later')[0].click(); await settle();
    ok(w.host.textContent.includes('All done'), 'later moves on');
    const w2 = boot({ items: JSON.parse(JSON.stringify(W)) });
    w2.mount(); await settle();
    all(w2.host, 'tcchip')[0].click(); await settle();
    eq(JSON.stringify(w2.log.calls.filter((c) => /\/answer$/.test(c[1]))[0][2]), '{"alt":0}', 'a person posts that candidate');
  }
  // Part B: home.html wiring
  ok(/id="tcBody"/.test(home) && /checks:\{home:true/.test(home), 'FULL.checks stage');
  ok(home.indexOf('touch-transcript-check.js') < home.indexOf('function wireChecks'), 'script loads before the estate script uses it');
  ok(/ZoeTranscriptCheck\.unmount\(\)/.test(home), 'navigation unmounts');
  ok(/kind==='transcript_check'&&d\.open==='checks'/.test(home), 'a voice yes opens the screen');
  ok(!/innerHTML/.test(src), 'module sets no innerHTML');
  console.log(checks + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
