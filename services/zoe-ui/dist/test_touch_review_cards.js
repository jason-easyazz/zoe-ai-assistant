/*
 * Panel review cards on the estate (ZOE_PANEL_REVIEW_CARDS) — no browser, no network.
 *
 * Runs the REAL js/touch-voice-session.js + js/touch-review-cards.js against a DOM shim whose innerHTML setter throws (every
 * server string is textContent) and a fake fetch. Pins: the card token travels in the X-Panel-Card HEADER (never a URL), a
 * row goes only after the server said yes, a refusal keeps the row and asks for the voice again, Remind / Someone I know open
 * their choices, "Someone new" shows the phone QR (never a text input), Play fetches the clip with the header, Not now sends
 * nothing, another member's card is never drawn and the session closing removes it all.
 * Negative controls: RC_SRC=<mutated copy> (e.g. put the token in the URL, or remove the row before the response) -> red.
 *
 * The notifications LIST (the orb press, no voice; docs/knowledge/notifications.md): the panel's own card is drawn with no voice
 * session, a tap on a row's words opens its screen (or the row's full words), Clear and Clear all go only after the server's yes
 * and re-read the dot, and the REAL orb block of touch/home.html opens the list on a press when the dot is lit by notifications,
 * keeps the mic one press away and never opens it on that first press. Each has a planted-mutation control inside this file.
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

function boot(responses, src) {
  const { document, find } = makeDom();
  const listeners = {};
  const calls = [];
  const emitted = [];
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
    CustomEvent: function (type, init) { this.type = type; this.detail = init && init.detail; },
    dispatchEvent: (ev) => { emitted.push(ev); (listeners[ev.type] || []).forEach((fn) => fn(ev)); },
  };
  function Audio(u) { this.src = u; } Audio.prototype.play = function () { played.push(this.src); };
  const ctx = { window, document, fetch, setTimeout: () => 1, clearTimeout() {}, String, Array, JSON, Date, Promise, Audio,
    encodeURIComponent, URL: { createObjectURL: (b) => 'blob:' + b.blob, revokeObjectURL() {} } };
  vm.createContext(ctx);
  vm.runInContext((src && src.vs) || vsSrc, ctx);
  vm.runInContext((src && src.rc) || rcSrc, ctx);
  const fire = (type, detail) => (listeners[type] || []).forEach((fn) => fn({ detail }));
  const btn = (act, item) => find((e) => e.tagName === 'BUTTON' && e.getAttribute('data-act') === act
    && (!item || (e.parentNode && e.parentNode.parentNode && e.parentNode.parentNode.getAttribute('data-item') === item)))[0];
  const choice = (key) => find((e) => e.tagName === 'BUTTON' && e.getAttribute('data-choice') === key)[0];
  const card = () => document.getElementById('zvs-card');
  return { fire, btn, choice, card, calls, played, find, emitted, window };
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
  // ── the notifications LIST: the panel's own card (an orb press, nobody identified) ───────────────────────────────────────
  const NOTE = '/api/panel/notifications';
  const done = [{ key: 'done', label: 'Clear' }];
  const LIST = { panel_id: P, member_id: '', kind: 'notifications', private: false, touch: true, title: 'Notifications',
    subtitle: 'Press the orb again to talk to me.', card_token: TOKEN, endpoint: NOTE, clear_all: 'Clear all', more: 4, more_label: 'And 4 more.',
    items: [
      { id: 'n1', text: 'Pendant battery is low', meta: 'Charge it tonight', open: 'settings', actions: done },
      { id: 'n2', text: 'Phone alerts are not getting through', meta: 'Zoe cannot warn...', detail: 'Zoe cannot warn your phone until the bot is fixed.', actions: done },
      { id: 'locked', text: 'Some are private', meta: 'Tap, then say what is up.', open: 'voice', actions: [] },
    ] };
  const words = (s, item) => s.find((e) => e.getAttribute('role') === 'button' && e.parentNode && e.parentNode.getAttribute('data-item') === item)[0];
  const opened = (s) => s.emitted.filter((e) => e.type === 'zoe:card-open').map((e) => e.detail.target);
  const acted = (s) => s.emitted.filter((e) => e.type === 'zoe:card-acted').length;
  async function listDraws(src) {                       // no voice session at all: the list is still drawn, with its buttons
    const s = boot(null, src);
    s.fire('zoe:voice-card', LIST);
    assert(s.card() && s.card().textContent.includes('Pendant battery is low') && s.btn('done', 'n1'), 'the list is drawn with no voice');
    s.fire('zoe:voice-session', { panel_id: P, state: 'closed', member_id: 'casey', reason: 'timeout' });
    assert(s.card() && s.btn('done', 'n1'), 'a voice session ending does not take the pressed list away');
    assert.strictEqual(s.window.ZoeVoiceSession.cardKind(), 'notifications');
  }
  async function listTaps(src) {                        // a tap on the words does something
    const s = boot(null, src);
    s.fire('zoe:voice-card', LIST);
    words(s, 'n1').click();
    assert.deepStrictEqual(opened(s), ['settings'], 'a row with a screen opens it');
    words(s, 'n2').click();
    assert(s.card().textContent.includes('until the bot is fixed.'), 'a row with no screen opens out to its full words');
    words(s, 'locked').click();
    assert.deepStrictEqual(opened(s), ['settings', 'voice'], 'the locked row asks for a voice');
    assert.strictEqual(s.calls.length, 0, 'opening a row sends nothing');
  }
  async function listClears(src) {                      // Clear, then Clear all: rows go after the yes, the dot is re-read
    const s = boot(null, src);
    s.fire('zoe:voice-card', LIST);
    s.btn('done', 'n1').click();
    await flush(); await flush();
    assert.strictEqual(s.calls[0].url, NOTE + '/act');
    assert.strictEqual(s.calls[0].opts.headers['X-Panel-Card'], TOKEN);
    assert.deepStrictEqual(JSON.parse(s.calls[0].opts.body), { item: 'n1', action: 'done' });
    assert(!s.card().textContent.includes('Pendant battery is low'), 'cleared row gone');
    assert.strictEqual(acted(s), 1, 'a cleared row re-reads the dot at once');
    s.btn('clear_all').click();
    await flush(); await flush();
    assert.deepStrictEqual(JSON.parse(s.calls[1].opts.body), { item: '*', action: 'clear_all' });
    const txt = s.card().textContent;
    assert(!txt.includes('Phone alerts') && !txt.includes('And 4 more.'), 'clear all takes every clearable row and the hidden ones');
    assert(txt.includes('Some are private') && txt.includes('Cleared.'), 'the locked row is not cleared by a tap');
    assert.strictEqual(s.btn('clear_all'), undefined, 'nothing left to clear: the button goes');
    assert.strictEqual(acted(s), 2);
  }
  async function listRefusal(src) {                     // a refused clear keeps everything and says how to get the list back
    const s = boot(() => ({ status: 403, json: { detail: 'not_your_card' } }), src);
    s.fire('zoe:voice-card', LIST);
    s.btn('clear_all').click();
    await flush(); await flush(); await flush();
    assert(s.card().textContent.includes('Pendant battery is low') && s.card().textContent.includes('Phone alerts'), 'a refusal clears nothing');
    assert(s.card().textContent.includes('Press the orb to open the list again.'));
    assert.strictEqual(acted(s), 0);
  }
  for (const fn of [listDraws, listTaps, listClears, listRefusal]) await fn();
  const red = async (fn, src, what) => {
    let failed = false;
    try { await fn(src); } catch (_) { failed = true; }
    assert(failed, 'negative control did not go red: ' + what);
  };
  const plant = (text, a, b) => { assert(text.includes(a), 'control anchor missing: ' + a); return text.replace(a, b); };
  await red(listDraws, { vs: plant(vsSrc, '!d.private && d.touch && d.card_token', 'false && d.card_token') }, 'a list that needs a voice session');
  await red(listTaps, { rc: plant(rcSrc, "if (it.open) { emit('zoe:card-open'", "if (false) { emit('zoe:card-open'") }, 'a row tap that does nothing');
  await red(listClears, { rc: plant(rcSrc, "rows.filter(clearable).forEach(function (row) { if (row.parentNode) row.parentNode.removeChild(row); });", '') }, 'clear all that leaves the rows');
  await red(listClears, { rc: plant(rcSrc, "if (j && j.note) say(j.note);\n                        emit('zoe:card-acted'", "if (j && j.note) say(j.note);\n                        (function () {})('zoe:card-acted'") }, 'a cleared row that leaves the dot lit');
  await red(listRefusal, { rc: plant(rcSrc, 'if (!r.ok) return refused(r);\n                    rows.filter(clearable)', 'rows.filter(clearable)') }, 'rows removed before the yes');

  // ── the orb press itself: the REAL block of touch/home.html in a vm ──────────────────────────────────────────────────────
  function orbBoot(html, inbox, openReply, listIsOpen) {
    const a = html.indexOf('  var _pullT=null,_dotVoiceOnly=false,_dotNotes=false'), b = html.indexOf('  if(cmdin){', a);
    assert(a > 0 && b > a, 'orb block not found in home.html');
    const cls = new Set(); const log = { voice: [], shown: [], fetched: [], zf: [], cards: [], closed: 0 };
    const listeners = {}; const orbL = {};
    const classList = { contains: (c) => cls.has(c), add: (c) => cls.add(c), remove: (c) => cls.delete(c),
      toggle: (c, on) => { if (on) cls.add(c); else cls.delete(c); } };
    const ctx = {
      orb: { classList, addEventListener: (t, fn) => { orbL[t] = fn; } },
      cmdbar: { classList: { add() {}, remove() {} } }, cmdin: null, _convLive: false, _orbBusy: false,
      FULL: { reminder: {}, settings: {} }, CARD: {}, show: (id) => log.shown.push(id), toast() {}, sid: () => 'sess-guest',
      _panelDevId: () => 'p1', reqSig: () => undefined, AbortSignal: { timeout: () => undefined },
      panelVoice: (m) => log.voice.push(m || ''), setInterval: () => 1, setTimeout: () => 1, clearTimeout() {}, JSON, String,
      document: { hidden: false, addEventListener() {} },
      CustomEvent: function (type, init) { this.type = type; this.detail = init && init.detail; },
      fetch: (url) => { log.fetched.push(url); return Promise.resolve({ type: 'opaque' }); },
      zfetch: (url, opts) => {
        log.zf.push({ url, opts: opts || {} });
        const r = url === '/api/proactive/inbox' ? { status: 200, json: inbox } : openReply;
        return Promise.resolve({ ok: r.status === 200, status: r.status, json: () => Promise.resolve(r.json) });
      },
    };
    ctx.window = { __kiosk: true, addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
      dispatchEvent: (ev) => { if (ev.type === 'zoe:voice-card') log.cards.push(ev.detail); (listeners[ev.type] || []).forEach((fn) => fn(ev)); },
      ZoeVoiceSession: { cardKind: () => (listIsOpen() ? 'notifications' : ''), closeCard: () => { log.closed += 1; } } };
    vm.createContext(ctx);
    vm.runInContext(html.slice(a, b), ctx);
    const emit = (type, detail) => (listeners[type] || []).forEach((fn) => fn({ detail }));
    return { log, press: () => orbL.click(), emit, cls };
  }
  const FRAME = { status: 200, json: LIST };
  const opens = (o) => o.log.zf.filter((c) => c.url === NOTE + '/open');
  async function pressShowsTheList(html) {              // dot lit by notifications: the press shows the list, the mic stays shut
    for (const inbox of [{ notify_any: true }, { enabled: false, count: 0, notify: 2 }]) {
      let open = false;
      const o = orbBoot(html, inbox, FRAME, () => open);
      await flush(); await flush();
      assert(o.cls.has('has'), 'the dot is lit');
      o.press();
      await flush(); await flush(); await flush();
      assert.strictEqual(opens(o).length, 1, 'the press asks for the list');
      assert.strictEqual(opens(o)[0].opts.method, 'POST');
      assert.deepStrictEqual(o.log.cards, [LIST], 'and the list is shown as the card');
      assert.deepStrictEqual(o.log.voice, [], 'the mic did not open: nothing has to be said');
      assert.deepStrictEqual(o.log.fetched, [], 'no spoken pull either');
      open = true;                                      // the list is on screen: the next press is the mic
      o.press();
      await flush();
      assert.strictEqual(o.log.voice.length, 1, 'the mic is one more press away');
      assert.strictEqual(opens(o).length, 1);
    }
  }
  async function pressFallsBack(html) {                 // nothing to list / refused / no dot / a selector item: as before
    let o = orbBoot(html, { notify_any: true }, { status: 200, json: { empty: true } }, () => false);
    await flush(); await flush(); o.press(); await flush(); await flush(); await flush();
    assert(o.log.cards.length === 0 && o.log.voice.length === 1, 'an empty list listens');
    o = orbBoot(html, { notify_any: true }, { status: 403, json: {} }, () => false);
    await flush(); await flush(); o.press(); await flush(); await flush(); await flush();
    assert(o.log.voice.length === 1 && /what's up/.test(o.log.voice[0]), 'a refused list asks for a voice');
    o = orbBoot(html, { enabled: false, count: 0 }, FRAME, () => false);
    await flush(); await flush(); o.press(); await flush();
    assert(opens(o).length === 0 && o.log.voice.length === 1, 'no dot: the press listens, as ever');
    o = orbBoot(html, { enabled: true, count: 1 }, FRAME, () => false);
    await flush(); await flush(); o.press(); await flush();
    assert(opens(o).length === 0 && o.log.fetched[0] === 'http://localhost:7777/pull', 'a selector item still pulls aloud');
  }
  async function listEventsReachTheEstate(html) {       // a cleared row re-reads the dot; a row tap opens its screen
    const o = orbBoot(html, { notify_any: true }, FRAME, () => true);
    await flush(); await flush();
    const before = o.log.zf.filter((c) => c.url === '/api/proactive/inbox').length;
    o.emit('zoe:card-acted', { kind: 'notifications' });
    assert.strictEqual(o.log.zf.filter((c) => c.url === '/api/proactive/inbox').length, before + 1, 'the dot is re-read at once');
    o.emit('zoe:card-open', { target: 'reminder' });
    assert.deepStrictEqual(o.log.shown, ['reminder']); assert.strictEqual(o.log.closed, 1, 'the list closes so the screen is seen');
    o.emit('zoe:card-open', { target: 'voice' });
    assert.strictEqual(o.log.voice.length, 1);
    o.emit('zoe:card-open', { target: '/people.html' });
    assert.deepStrictEqual(o.log.shown, ['reminder'], 'only an estate screen can be opened');
  }
  for (const fn of [pressShowsTheList, pressFallsBack, listEventsReachTheEstate]) await fn(homeHtml);
  await red(pressShowsTheList, plant(homeHtml, "if(free&&!listOpen()&&(idlePrompt||(ready&&_dotNotes)))orbList(idlePrompt);",
    "if(ready&&_dotVoiceOnly)panelVoice('Say \"what\\'s up?\" - I\\'ll check it\\'s you');"), 'the old press (a dot lit by notifications opens the mic)');
  await red(pressShowsTheList, plant(homeHtml, "else if(ready&&!_dotVoiceOnly&&!listOpen())orbPull();", "else if(ready&&!_dotVoiceOnly)orbPull();")
    .replace("if(free&&!listOpen()&&(", "if(free&&("), 'a list that swallows the mic for good');
  await red(listEventsReachTheEstate, plant(homeHtml, "window.addEventListener('zoe:card-acted',function(){_orbPoll();});", ''), 'a dot that stays lit after a clear');
  await red(listEventsReachTheEstate, plant(homeHtml, "if(!FULL[t]&&!CARD[t])return;", ''), 'a row that can open anything');

  const vsAt = homeHtml.indexOf('/js/touch-voice-session.js');
  const rcAt = homeHtml.indexOf('/js/touch-review-cards.js');
  const exAt = homeHtml.indexOf('/js/touch-ui-executor.js');
  assert(vsAt > 0 && rcAt > 0 && exAt > rcAt, 'home.html loads touch-review-cards.js before the executor');
  console.log('review cards: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
