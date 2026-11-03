/*
 * Panel review cards on the estate (ZOE_PANEL_REVIEW_CARDS) — no browser, no network.
 *
 * Runs the REAL js/touch-voice-session.js + js/touch-review-cards.js against a DOM shim whose innerHTML setter throws (every
 * server string is textContent) and a fake fetch. Pins: the card token travels in the X-Panel-Card HEADER (never a URL), a
 * row goes only after the server said yes, a refusal keeps the row and asks for the voice again, Remind / Someone I know open
 * their choices, "Someone new" shows the phone QR (never a text input), Play fetches the clip with the header, Not now sends
 * nothing, another member's card is never drawn and the session closing removes it all.
 * Negative controls: RC_SRC=<mutated copy> (e.g. put the token in the URL, or remove the row before the response) -> red.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const vsSrc = fs.readFileSync(path.join(__dirname, 'js/touch-voice-session.js'), 'utf8');
const rcSrc = fs.readFileSync(process.env.RC_SRC || path.join(__dirname, 'js/touch-review-cards.js'), 'utf8');
const homeHtml = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

function makeDom() {
  const all = new Set();
  function makeEl(tag) {
    const attrs = {};
    const el = {
      tagName: String(tag).toUpperCase(), id: '', className: '', children: [], parentNode: null, _text: '', listeners: {},
      setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
      removeAttribute(k) { delete attrs[k]; }, hasAttribute(k) { return k in attrs; },
      appendChild(c) { c.parentNode = el; el.children.push(c); all.add(c); return c; },
      removeChild(c) { el.children = el.children.filter((x) => x !== c); c.parentNode = null; all.delete(c); return c; },
      addEventListener(t, fn) { (el.listeners[t] = el.listeners[t] || []).push(fn); },
      click() { (el.listeners.click || []).forEach((fn) => fn({})); },
      get textContent() { return el._text + el.children.map((c) => c.textContent).join(''); },
      set textContent(v) { el._text = String(v); },
      set innerHTML(_v) { throw new Error('innerHTML used: server strings must be set with textContent'); },
    };
    return el;
  }
  const head = makeEl('head'); const body = makeEl('body');
  const document = { head, body, documentElement: head, createElement: makeEl,
    getElementById: (id) => [...all].find((e) => e.id === id) || null };
  const find = (pred) => [...all].filter((e) => e.parentNode && attached(e) && pred(e));
  function attached(e) { let n = e; while (n.parentNode) n = n.parentNode; return n === body || n === head; }
  return { document, find };
}

function boot(responses) {
  const { document, find } = makeDom();
  const listeners = {};
  const calls = [];
  const played = [];
  const fetch = (url, opts) => {
    calls.push({ url, opts: opts || {} });
    const r = (responses && responses(url, opts || {})) || { status: 200, json: { ok: true } };
    return Promise.resolve({ ok: r.status >= 200 && r.status < 300, status: r.status,
      json: () => Promise.resolve(r.json || {}), blob: () => Promise.resolve({ blob: url }) });
  };
  const window = {
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    localStorage: { getItem: () => JSON.stringify({ session_id: 'sess-guest' }) },
  };
  function Audio(u) { this.src = u; } Audio.prototype.play = function () { played.push(this.src); };
  const ctx = { window, document, fetch, setTimeout: () => 1, clearTimeout() {}, String, Array, JSON, Date, Promise, Audio,
    encodeURIComponent, URL: { createObjectURL: (b) => 'blob:' + b.blob, revokeObjectURL() {} } };
  vm.createContext(ctx);
  vm.runInContext(vsSrc, ctx);
  vm.runInContext(rcSrc, ctx);
  const fire = (type, detail) => (listeners[type] || []).forEach((fn) => fn({ detail }));
  const btn = (act, item) => find((e) => e.tagName === 'BUTTON' && e.getAttribute('data-act') === act
    && (!item || (e.parentNode && e.parentNode.parentNode && e.parentNode.parentNode.getAttribute('data-item') === item)))[0];
  const choice = (key) => find((e) => e.tagName === 'BUTTON' && e.getAttribute('data-choice') === key)[0];
  const card = () => document.getElementById('zvs-card');
  return { fire, btn, choice, card, calls, played, find };
}

const flush = () => new Promise((r) => setImmediate(r));
const P = 'zoe-touch-pi';
const TOKEN = 'tok-AbC123';
const remind = { key: 'remind', label: 'Remind me', choices: [{ key: 'tomorrow', label: 'Tomorrow, 9 AM' }] };
const LOOPS = { panel_id: P, member_id: 'casey', kind: 'open_loops', private: true, title: 'On your plate', card_token: TOKEN,
  items: [
    { id: '1', text: 'Call the plumber <b>now</b>', meta: 'today', actions: [{ key: 'done', label: 'Done' }, remind, { key: 'drop', label: 'Drop' }] },
    { id: '2', text: 'Book the gutter clean', meta: '2 days ago', actions: [{ key: 'done', label: 'Done' }, remind, { key: 'drop', label: 'Drop' }] },
  ] };

(async () => {
  // ── open loops: draw, tap Done, header-only token, row goes after the yes ──────────────────────────────────────────────
  {
    const s = boot();
    s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
    s.fire('zoe:voice-card', LOOPS);
    assert(s.card() && s.card().textContent.includes('Call the plumber <b>now</b>'), 'rows drawn as text');
    s.btn('done', '1').click();
    await flush(); await flush();
    const c = s.calls[0];
    assert.strictEqual(c.url, '/api/panel/cards/act');
    assert(!c.url.includes(TOKEN), 'the card token never rides in a URL');
    assert.strictEqual(c.opts.headers['X-Panel-Card'], TOKEN);
    assert.strictEqual(c.opts.headers['X-Session-ID'], 'sess-guest');
    assert.deepStrictEqual(JSON.parse(c.opts.body), { item: '1', action: 'done' });
    assert(!s.card().textContent.includes('Call the plumber'), 'done row removed after the server said yes');
    assert(s.card().textContent.includes('Book the gutter clean'));
    // Remind opens its choices; a choice sends its KEY.
    s.btn('remind', '2').click();
    s.choice('tomorrow').click();
    await flush(); await flush();
    assert.deepStrictEqual(JSON.parse(s.calls[1].opts.body), { item: '2', action: 'remind', choice: 'tomorrow' });
    assert(s.card().textContent.includes('All done.'));
    assert.strictEqual(s.find((e) => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA').length, 0, 'no keyboard on the panel');
    // The session closing takes the card, buttons and all.
    s.fire('zoe:voice-session', { panel_id: P, state: 'closed', member_id: 'casey', reason: 'timeout' });
    assert.strictEqual(s.card(), null);
  }
  // ── a refusal keeps the row and asks for the voice again ──────────────────────────────────────────────────────────────
  {
    const s = boot(() => ({ status: 403 }));
    s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
    s.fire('zoe:voice-card', LOOPS);
    s.btn('drop', '1').click();
    await flush(); await flush();
    assert(s.card().textContent.includes('Call the plumber'), 'a refused tap never removes the row');
    assert(s.card().textContent.includes('Say it again so I know it'));
    // Another member's card is never drawn while casey's session is open.
    s.fire('zoe:voice-card', Object.assign({}, LOOPS, { member_id: 'dana', items: [{ id: '9', text: 'dana secret', actions: [] }] }));
    assert(!s.card().textContent.includes('dana secret'));
  }
  // ── the day review card: voices, play, someone new -> phone QR, not now ────────────────────────────────────────────────
  {
    const s = boot((url) => (url === '/api/panel/cards/phone-qr' || url.startsWith('/api/panel/cards/clip/') ? { status: 200 } : null));
    s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
    s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'day_review', private: true, title: 'Your day', card_token: TOKEN,
      groups: { day: 'From your day', voices: 'New voices' },
      items: [
        { id: 'c1', group: 'day', text: 'Milk', meta: 'List', actions: [{ key: 'keep', label: 'Keep' }, { key: 'skip', label: 'Skip' }] },
        { id: 'v1', group: 'voices', text: 'Voice 1', meta: 'heard in 2 recordings', actions: [
          { key: 'play', label: 'Play' }, { key: 'me', label: "That's me" },
          { key: 'tag', label: 'Someone I know', choices: [{ key: 'p1', label: 'Sam' }, { key: 'new', label: 'Someone new', phone: true }] },
          { key: 'media', label: 'TV / radio' }, { key: 'later', label: 'Not now', local: true }] },
        { id: 'v2', group: 'voices', text: 'Voice 2', actions: [{ key: 'later', label: 'Not now', local: true }] },
      ] });
    const txt = s.card().textContent;
    assert(txt.includes('From your day') && txt.includes('New voices') && txt.includes('Voice 1'));
    s.btn('play', 'v1').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[0].url, '/api/panel/cards/clip/v1');
    assert.strictEqual(s.calls[0].opts.headers['X-Panel-Card'], TOKEN);
    assert.strictEqual(s.played.length, 1, 'the clip plays from a blob URL');
    s.btn('tag', 'v1').click();
    s.choice('new').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[1].url, '/api/panel/cards/phone-qr');
    assert(s.find((e) => e.tagName === 'IMG' && String(e.src).startsWith('blob:')).length === 1, 'the phone QR is shown');
    assert(s.card().textContent.includes('Scan with your phone'));
    s.btn('later', 'v2').click();
    assert(!s.card().textContent.includes('Voice 2'), 'Not now hides the voice');
    assert.strictEqual(s.calls.length, 2, 'Not now sends nothing');
    s.choice('p1') || s.btn('tag', 'v1').click();
    s.choice('p1').click();
    await flush(); await flush();
    assert.deepStrictEqual(JSON.parse(s.calls[2].opts.body), { item: 'v1', action: 'tag', choice: 'p1' });
    assert.strictEqual(s.find((e) => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA').length, 0, 'no keyboard on the panel');
  }
  // ── a card without a token is drawn the old way (no buttons) ──────────────────────────────────────────────────────────────
  {
    const s = boot();
    s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'calendar', private: true, title: 'c', items: [{ text: 'Dentist' }] });
    assert(s.card().textContent.includes('Dentist') && !s.btn('done'));
  }
  // ── the memories card (ZOE_MEMORIES_VOICE): its own routes, rows kept on "keep", the phone hint, soft rows, PIN first ──────
  {
    const MEM = '/api/panel/memories';
    const s = boot((url, opts) => {
      if (url === MEM + '/phone-qr') return { status: 200 };
      if (url !== MEM + '/act') return { status: 500 };
      const b = JSON.parse(opts.body);
      if (b.action === 'source') return { status: 200, json: { ok: true, keep: true, detail: 'You told me on Tuesday.' } };
      if (b.action === 'wrong') return { status: 200, json: { ok: true, keep: true, note: 'Okay - tell me what is right.' } };
      if (b.action === 'forget_person') return { status: 403, json: { detail: 'pin_needed' } };
      return { status: 200, json: { ok: true, note: 'Forgotten.' } };
    });
    const rowActs = [
      { key: 'wrong', label: "That's wrong", choices: [{ key: 'voice', label: "I'll say it" }, { key: 'phone', label: 'Fix on my phone', phone: true }] },
      { key: 'forget', label: 'Forget', choices: [{ key: 'yes', label: 'Yes, forget it' }] },
      { key: 'source', label: "Where's this from?" }];
    s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
    s.fire('zoe:voice-card', { panel_id: P, member_id: 'casey', kind: 'memories', private: true, title: 'What I know about you',
      card_token: TOKEN, endpoint: MEM, phone_hint: 'Scan with your phone to fix it there.', more: 3,
      more_label: '3 more on your phone', groups: { people: 'People', preferences: 'Preferences', about: 'About you' },
      items: [
        { id: 'p1', group: 'people', text: 'Robin (sister)', meta: 'lives in Eastvale',
          actions: [{ key: 'forget_person', label: 'Forget', choices: [{ key: 'yes', label: 'Yes, forget Robin' }] }] },
        { id: 'm1', group: 'preferences', text: 'You like dark roast <i>coffee</i>', meta: 'You told me on 3 Oct', actions: rowActs },
        { id: 'm2', group: 'about', text: 'You enjoy long hikes', meta: 'I worked this out', soft: true, actions: rowActs },
      ] });
    const txt = s.card().textContent;
    assert(txt.includes('People') && txt.includes('Preferences') && txt.includes('About you'), 'grouped');
    assert(txt.includes('You like dark roast <i>coffee</i>') && txt.includes('You told me on 3 Oct'), 'text + provenance as text');
    assert(txt.includes('3 more on your phone'), 'what only the phone shows is said');
    const soft = s.find((e) => e.getAttribute && e.getAttribute('data-item') === 'm2')[0];
    assert(soft && soft.className.includes('zrc-soft'), 'what Zoe worked out herself is marked');
    s.btn('source', 'm1').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[0].url, MEM + '/act', 'the card uses its own routes');
    assert.strictEqual(s.calls[0].opts.headers['X-Panel-Card'], TOKEN);
    assert.deepStrictEqual(JSON.parse(s.calls[0].opts.body), { item: 'm1', action: 'source' });
    assert(s.card().textContent.includes('You told me on Tuesday.') && s.card().textContent.includes('dark roast'), 'kept + answered');
    s.btn('wrong', 'm1').click();
    s.choice('voice').click();
    await flush(); await flush();
    assert.deepStrictEqual(JSON.parse(s.calls[1].opts.body), { item: 'm1', action: 'wrong', choice: 'voice' });
    assert(s.card().textContent.includes('dark roast') && s.card().textContent.includes('tell me what is right'));
    s.btn('wrong', 'm1').click();
    s.choice('phone').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[2].url, MEM + '/phone-qr');
    assert(s.card().textContent.includes('fix it there'), "the phone hint is the card's own");
    s.btn('forget', 'm1').click();
    assert.strictEqual(s.calls.length, 3, 'Forget asks first: nothing sent yet');
    s.choice('yes').click();
    await flush(); await flush();
    assert.deepStrictEqual(JSON.parse(s.calls[3].opts.body), { item: 'm1', action: 'forget', choice: 'yes' });
    assert(!s.card().textContent.includes('dark roast'), 'forgotten row goes after the yes');
    s.btn('forget_person', 'p1').click();
    s.choice('yes').click();
    await flush(); await flush();
    assert(s.card().textContent.includes('Robin') && s.card().textContent.includes('Unlock with your PIN first'), 'PIN first');
    assert.strictEqual(s.find((e) => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA').length, 0, 'no keyboard on the panel');
  }
  // ── a card cannot point its taps off the panel routes ────────────────────────────────────────────────────────────────────
  {
    const s = boot();
    s.fire('zoe:voice-session', { panel_id: P, state: 'open', member_id: 'casey', display_name: 'Casey' });
    s.fire('zoe:voice-card', Object.assign({}, LOOPS, { endpoint: 'https://evil.example/x' }));
    s.btn('done', '1').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[0].url, '/api/panel/cards/act');
  }
  const vsAt = homeHtml.indexOf('/js/touch-voice-session.js');
  const rcAt = homeHtml.indexOf('/js/touch-review-cards.js');
  const exAt = homeHtml.indexOf('/js/touch-ui-executor.js');
  assert(vsAt > 0 && rcAt > 0 && exAt > rcAt, 'home.html loads touch-review-cards.js before the executor');
  console.log('review cards: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
