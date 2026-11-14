#!/usr/bin/env node
/* Settings > Telegram > Reminders on my phone - UI harness (no browser: node + a fake root + a stubbed fetch).
 * Run: node dist/test_reminder_phone_settings.js. CI: services/zoe-data/tests/test_reminder_phone_settings_ui_harness.py.
 * Record: docs/knowledge/reminder-phone-delivery.md. Invented data only.
 *
 * Pins: state loads from the GET; hidden when Telegram is not linked, and unless the GET says eligible === true (a child, an older
 * server without the field); the toggle sends exactly {channels:["telegram"], full_text:false};
 * the full-text checkbox maps to full_text; another channel already chosen (web_push) is kept; nothing says "Saved" before the server
 * answered and the controls are disabled meanwhile; a failed PUT reverts the control and says so; a 403 (not an adult) reverts and
 * replaces the section with one plain sentence; no server message is rendered; desktop-only wiring (never /touch/).
 * Negative controls (run on scratch copies): drop the telegram_linked check in shown() -> "not linked" goes red; drop the eligible
 * check in shown() -> "eligible false" (a child) goes red; in the PUT failure branch, store the clicked body into state.view and skip
 * the re-read -> "reverts" goes red.
 * "Also send to this browser" (fake browser push adapter + push routes): hidden when unsupported / not eligible; no prompt on load; the
 * prompt is the click's first, synchronous act; prompt -> key -> subscribe -> POST -> PUT {channels:[...,"web_push"]}; denial, dismissal
 * and an already-blocked browser change nothing; a failed subscribe / store / PUT undoes the subscription; off removes both.
 * Negative controls (RP_SRC=<mutated copy>): prompt in load(); drop eligible from pushShown(); skip the undo on PUT failure; ignore
 * a denial; start the flow after an await; a PUT body that drops telegram -> each goes red.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DIST = __dirname;
const RP = require(process.env.RP_SRC || path.join(DIST, 'js', 'reminder-phone-settings.js'));   // RP_SRC: a mutated copy (negative controls)
const API = '/api/user/profile/reminder-delivery';
let checks = 0;
function ok(cond, msg) { assert.ok(cond, msg); checks++; }
function eq(a, b, msg) { assert.deepStrictEqual(a, b, msg); checks++; }

function fakeRoot() { return { innerHTML: '', addEventListener: function () {} }; }
function input(ctl, checked) { return { target: { checked: checked, getAttribute: function (k) { return k === 'data-ctl' ? ctl : null; } } }; }
function isOn(html, ctl) { const m = html.match(new RegExp('<input type="checkbox" data-ctl="' + ctl + '"([^>]*)>')); return m ? /checked/.test(m[1]) : null; }
function isDisabled(html, ctl) { const m = html.match(new RegExp('<input type="checkbox" data-ctl="' + ctl + '"([^>]*)>')); return m ? /disabled/.test(m[1]) : null; }
function err(status, msg) { const e = new Error(msg || ('HTTP ' + status)); e.status = status; return e; }

/* A stub server: GET answers `stored`; PUT answers per `next` (ok | a status) and stores on ok. */
function server(stored) {
  const s = { stored: Object.assign({ channels: [], full_text: false, telegram_linked: true, mode: 'on', eligible: true }, stored), calls: [], next: 'ok', hold: null };
  s.fetchJson = function (method, url, body) {
    s.calls.push([method, url, body]);
    if (method === 'GET') return Promise.resolve(JSON.parse(JSON.stringify(s.stored)));
    const outcome = s.next;
    const go = function () {
      if (outcome === 'ok') { s.stored.channels = body.channels; s.stored.full_text = body.full_text; return { channels: body.channels, full_text: body.full_text }; }
      throw err(outcome, outcome === 403 ? 'phone delivery of reminders is for adult members' : '<b>internal detail</b>');
    };
    if (s.hold) return new Promise(function (res, rej) { s.hold = function () { try { res(go()); } catch (e) { rej(e); } }; });
    return new Promise(function (res, rej) { try { res(go()); } catch (e) { rej(e); } });
  };
  s.puts = function () { return s.calls.filter(function (c) { return c[0] === 'PUT'; }); };
  return s;
}

/* ---------- "Also send to this browser" (web push) ---------- */
/* A fake browser push: records every call in `log` (shared with the server below, so the ORDER of acts is visible). */
function fakePush(opts) {
  const p = Object.assign({ supported: true, permission: 'default', answer: 'granted', sub: null, subscribeFails: false }, opts);
  p.log = opts.log;
  p.prompts = 0;
  let n = 0;
  function mkSub() {
    const s = { endpoint: 'https://push.example.invalid/ep-' + (++n), live: true };
    s.toJSON = function () { return { endpoint: s.endpoint, keys: { p256dh: 'pk-' + n, auth: 'au-' + n } }; };
    s.unsubscribe = function () { s.live = false; p.log.push('browser:unsubscribe'); if (p.sub === s) p.sub = null; return Promise.resolve(true); };
    return s;
  }
  p.adapter = {
    supported: function () { return p.supported; },
    permission: function () { return p.permission; },
    requestPermission: function () { p.prompts++; p.log.push('prompt'); p.permission = p.answer; return Promise.resolve(p.answer); },
    current: function () { p.log.push('browser:current'); return Promise.resolve(p.sub); },
    subscribe: function (key) {
      p.log.push('browser:subscribe:' + key);
      if (p.subscribeFails) return Promise.reject(new Error('push service down'));
      p.sub = mkSub();
      return Promise.resolve(p.sub);
    },
  };
  if (p.sub === true) p.sub = mkSub();
  return p;
}
/* A server with the push routes: `pushNext` / `putNext` = 'ok' | a status. */
function pushServer(stored, log) {
  const s = { stored: Object.assign({ channels: [], full_text: false, telegram_linked: false, mode: 'on', eligible: true }, stored),
    rows: [], log: log, pushNext: 'ok', putNext: 'ok', deleteNext: 'ok' };
  s.fetchJson = function (method, url, body) {
    log.push(method + ' ' + url);
    const fail = function (st) { return Promise.reject(err(st, '<b>internal detail</b>')); };
    if (method === 'GET' && url === API) return Promise.resolve(JSON.parse(JSON.stringify(s.stored)));
    if (method === 'GET' && url === '/api/push/vapid-public-key') return Promise.resolve({ public_key: 'VAPIDKEY' });
    if (method === 'POST' && url === '/api/push/subscribe') {
      if (s.pushNext !== 'ok') return fail(s.pushNext);
      s.rows.push(body.subscription); s.lastPost = body; return Promise.resolve({ status: 'subscribed' });
    }
    if (method === 'DELETE' && url === '/api/push/subscribe') {
      if (s.deleteNext !== 'ok') return fail(s.deleteNext);
      s.lastDelete = body; s.rows = s.rows.filter(function (r) { return r.endpoint !== body.endpoint; });
      return Promise.resolve({ status: 'unsubscribed' });
    }
    if (method === 'PUT' && url === API) {
      s.lastPut = body;
      if (s.putNext !== 'ok') return fail(s.putNext);
      s.stored.channels = body.channels; s.stored.full_text = body.full_text;
      return Promise.resolve({ channels: body.channels, full_text: body.full_text });
    }
    return fail(404);
  };
  s.count = function (prefix) { return log.filter(function (x) { return x.indexOf(prefix) === 0; }).length; };
  return s;
}
async function mountPush(stored, opts) {
  const log = [];
  const s = pushServer(stored, log);
  const p = fakePush(Object.assign({ log: log }, opts || {}));
  const root = fakeRoot();
  const m = RP.mount(root, { fetchJson: s.fetchJson, push: p.adapter });
  await m.ready;
  return { s: s, p: p, root: root, m: m, log: log };
}

async function pushChecks() {
  /* hidden when unsupported, or not eligible (false / absent / not a boolean) */
  let t = await mountPush({ telegram_linked: true }, { supported: false });
  ok(isOn(t.root.innerHTML, 'push') === null && isOn(t.root.innerHTML, 'send') === false, 'no web push in this browser: no browser row (Telegram row still there)');
  await t.m.change(input('push', true));
  ok(t.p.prompts === 0 && t.s.count('PUT') === 0 && t.s.count('POST') === 0, 'unsupported: a stray change asks nothing, stores nothing');
  for (const [label, extra] of [['eligible false', { eligible: false }], ['eligible absent', { eligible: undefined }], ['eligible "yes"', { eligible: 'yes' }]]) {
    t = await mountPush(extra);
    if (extra.eligible === undefined) { delete t.s.stored.eligible; await t.m.reload(); }
    eq(t.root.innerHTML, '', label + ': nothing shown');
    await t.m.change(input('push', true));
    ok(t.p.prompts === 0 && t.s.count('PUT') === 0 && t.s.count('POST') === 0, label + ': a stray change asks nothing, stores nothing');
  }

  /* shown for an eligible member in a supporting browser, Telegram NOT linked; nothing asked on load */
  t = await mountPush({});
  ok(isOn(t.root.innerHTML, 'push') === false && isOn(t.root.innerHTML, 'send') === null, 'browser row shown without Telegram, off by default');
  ok(/Also send to this browser/.test(t.root.innerHTML), 'the browser row label');
  ok(t.p.prompts === 0 && t.log.indexOf('prompt') === -1 && t.s.count('GET /api/push') === 0 && t.s.count('POST') === 0,
     'page load: no permission prompt, no key fetch, no subscription');

  /* click on: the prompt is the FIRST act and happens inside the click; then key -> subscribe -> store -> PUT exactly */
  const before = t.log.length;
  const flow = t.m.change(input('push', true));
  ok(t.p.prompts === 1 && t.log[before] === 'prompt', 'the permission request is the first thing the click does, synchronously');
  ok(!/Saved/.test(t.root.innerHTML) && isDisabled(t.root.innerHTML, 'push'), 'no "Saved" yet; controls disabled while setting up');
  await flow;
  eq(t.log.slice(before), ['prompt', 'GET /api/push/vapid-public-key', 'browser:subscribe:VAPIDKEY', 'POST /api/push/subscribe', 'PUT ' + API],
     'order: prompt, key, subscribe, store, PUT');
  eq(t.s.lastPost, { subscription: { endpoint: 'https://push.example.invalid/ep-1', keys: { p256dh: 'pk-1', auth: 'au-1' } } },
     'the subscription is stored through the existing endpoint, endpoint + keys only');
  eq(t.s.lastPut, { channels: ['web_push'], full_text: false }, 'the PUT body is exactly {channels:["web_push"], full_text:false}');
  ok(isOn(t.root.innerHTML, 'push') === true && /Saved\./.test(t.root.innerHTML), 'on, and Saved only after the server said so');

  /* telegram + full_text already chosen: both kept */
  t = await mountPush({ telegram_linked: true, channels: ['telegram'], full_text: true });
  await t.m.change(input('push', true));
  eq(t.s.lastPut, { channels: ['telegram', 'web_push'], full_text: true }, 'turning the browser on keeps telegram and full_text');
  ok(isOn(t.root.innerHTML, 'send') === true && isOn(t.root.innerHTML, 'push') === true, 'both rows on');

  /* already granted: no prompt, still subscribes */
  t = await mountPush({}, { permission: 'granted' });
  await t.m.change(input('push', true));
  ok(t.p.prompts === 0 && t.s.lastPut && t.s.lastPut.channels.join() === 'web_push', 'permission already granted: no second prompt, subscribed');

  /* denial at the prompt: one honest line, nothing changed */
  t = await mountPush({}, { answer: 'denied' });
  await t.m.change(input('push', true));
  ok(t.p.prompts === 1 && t.s.count('GET /api/push') === 0 && t.s.count('POST') === 0 && t.s.count('PUT') === 0 && t.log.indexOf('browser:subscribe:VAPIDKEY') === -1,
     'denied: no key, no subscription, no PUT');
  ok(isOn(t.root.innerHTML, 'push') === false && t.root.innerHTML.indexOf(RP.DENIED) !== -1 && !/Saved/.test(t.root.innerHTML),
     'denied: the control is back off with one honest line');
  eq(t.s.stored.channels, [], 'denied: nothing stored');
  /* "default" dismissed (not granted) is a denial too */
  t = await mountPush({}, { answer: 'default' });
  await t.m.change(input('push', true));
  ok(t.s.count('PUT') === 0 && isOn(t.root.innerHTML, 'push') === false && t.root.innerHTML.indexOf(RP.DENIED) !== -1, 'prompt dismissed: nothing changed');
  /* blocked before: no prompt at all */
  t = await mountPush({}, { permission: 'denied' });
  await t.m.change(input('push', true));
  ok(t.p.prompts === 0 && t.s.count('PUT') === 0 && t.root.innerHTML.indexOf(RP.DENIED) !== -1, 'already blocked: no prompt, the honest line, nothing changed');

  /* failures revert and undo */
  t = await mountPush({}, { subscribeFails: true });
  await t.m.change(input('push', true));
  ok(t.s.count('POST') === 0 && t.s.count('PUT') === 0 && isOn(t.root.innerHTML, 'push') === false && t.root.innerHTML.indexOf(RP.PUSH_FAILED) !== -1,
     'the browser could not subscribe: nothing stored, control off, says so');
  t = await mountPush({});
  t.s.pushNext = 500;
  await t.m.change(input('push', true));
  ok(t.s.count('PUT') === 0 && t.p.sub === null && t.log.indexOf('browser:unsubscribe') !== -1, 'storing the subscription failed: the browser subscription is undone, no PUT');
  ok(isOn(t.root.innerHTML, 'push') === false && t.root.innerHTML.indexOf(RP.PUSH_FAILED) !== -1 && !/internal detail/.test(t.root.innerHTML), 'control off, fixed text only');
  t = await mountPush({});
  t.s.putNext = 500;
  await t.m.change(input('push', true));
  eq(t.s.lastDelete, { endpoint: 'https://push.example.invalid/ep-1' }, 'the PUT failed: the stored subscription is deleted again');
  ok(t.s.rows.length === 0 && t.p.sub === null, 'the PUT failed: no server row, no browser subscription left');
  ok(isOn(t.root.innerHTML, 'push') === false && t.root.innerHTML.indexOf(RP.FAILED) !== -1 && t.log[t.log.length - 1] === 'browser:current',
     'the PUT failed: control off, says so, re-reads');
  t = await mountPush({});
  t.s.putNext = 403;
  await t.m.change(input('push', true));
  ok(t.s.rows.length === 0 && t.p.sub === null && t.root.innerHTML.indexOf(RP.REFUSED) !== -1 && isOn(t.root.innerHTML, 'push') === null,
     '403 backstop: subscription undone, one plain sentence');

  /* web_push chosen but THIS browser has no subscription: shown off */
  t = await mountPush({ channels: ['web_push'] });
  ok(isOn(t.root.innerHTML, 'push') === false, 'web_push chosen elsewhere, no subscription here: this browser is off');
  /* subscription here but web_push not chosen: off too */
  t = await mountPush({}, { sub: true });
  ok(isOn(t.root.innerHTML, 'push') === false, 'a subscription without the channel: off');

  /* click off: removes both (server row, browser subscription, the channel), keeps the rest */
  t = await mountPush({ telegram_linked: true, channels: ['telegram', 'web_push'], full_text: true }, { sub: true, permission: 'granted' });
  ok(isOn(t.root.innerHTML, 'push') === true, 'chosen + subscribed here: on');
  const ep = t.p.sub.endpoint;
  const b2 = t.log.length;
  await t.m.change(input('push', false));
  ok(t.p.prompts === 0, 'turning off never prompts');
  eq(t.s.lastDelete, { endpoint: ep }, 'off: the server row for this browser is deleted');
  ok(t.p.sub === null && t.log.slice(b2).indexOf('browser:unsubscribe') !== -1, 'off: the browser subscription is gone');
  eq(t.s.lastPut, { channels: ['telegram'], full_text: true }, 'off: the PUT body is exactly the channels without web_push');
  ok(isOn(t.root.innerHTML, 'push') === false && isOn(t.root.innerHTML, 'send') === true && /Saved\./.test(t.root.innerHTML), 'off, Telegram untouched, Saved');
  /* off, but the server DELETE fails: nothing else changes */
  t = await mountPush({ channels: ['web_push'] }, { sub: true, permission: 'granted' });
  t.s.deleteNext = 500;
  const sub0 = t.p.sub;
  await t.m.change(input('push', false));
  ok(t.s.count('PUT') === 0 && t.p.sub === sub0 && sub0.live && isOn(t.root.innerHTML, 'push') === true && t.root.innerHTML.indexOf(RP.FAILED) !== -1,
     'off failed at the server: subscription and channel kept, control back on, says so');

  /* a click while setting up is not queued */
  t = await mountPush({});
  const f1 = t.m.change(input('push', true));
  await t.m.change(input('full', true));
  await f1;
  eq(t.s.count('PUT'), 1, 'a second click while the browser is being set up is not sent');
}

/* ---------- pure ---------- */
eq(RP.putBody({ channels: ['telegram'], full_text: true }, 'push', true), { channels: ['telegram', 'web_push'], full_text: true }, 'push on adds web_push, keeps the rest');
eq(RP.putBody({ channels: ['web_push', 'telegram'], full_text: false }, 'push', false), { channels: ['telegram'], full_text: false }, 'push off removes web_push only');
eq(RP.putBody({ channels: [], full_text: false }, 'send', true), { channels: ['telegram'], full_text: false }, 'the documented PUT body');
eq(RP.putBody({ channels: ['telegram'], full_text: true }, 'send', false), { channels: [], full_text: true }, 'off removes telegram, keeps full_text');
eq(RP.putBody({ channels: ['web_push'], full_text: false }, 'send', true), { channels: ['web_push', 'telegram'], full_text: false }, 'web_push is kept');
eq(RP.putBody({ channels: ['web_push', 'telegram'] }, 'send', false), { channels: ['web_push'], full_text: false }, 'web_push survives off');
eq(RP.putBody({ channels: ['telegram'], full_text: false }, 'full', true), { channels: ['telegram'], full_text: true }, 'checkbox maps to full_text');
eq(RP.putBody({ channels: [] }, 'other', true), null, 'an unknown control sends nothing');

(async function () {
  /* ---------- not linked: hidden, nothing to click ---------- */
  let s = server({ telegram_linked: false, channels: ['telegram'] });
  let root = fakeRoot();
  let m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  eq(s.calls, [['GET', API, undefined]], 'loads from the GET');
  eq(root.innerHTML, '', 'Telegram not linked: both controls hidden');
  await m.change(input('send', true));
  ok(s.puts().length === 0, 'not linked: a stray change event sends nothing');

  /* ---------- not eligible (a child), or an older server without the field: hidden ---------- */
  for (const [label, extra] of [['eligible false', { eligible: false }], ['eligible absent', { eligible: undefined }],
                                ['eligible not a boolean', { eligible: 'yes' }]]) {
    s = server(Object.assign({ channels: ['telegram'] }, extra));
    if (extra.eligible === undefined) delete s.stored.eligible;
    root = fakeRoot();
    m = RP.mount(root, { fetchJson: s.fetchJson });
    await m.ready;
    eq(root.innerHTML, '', label + ': both controls hidden');
    await m.change(input('send', false));
    ok(s.puts().length === 0, label + ': a stray change event sends nothing');
  }
  s = server({});
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  ok(isOn(root.innerHTML, 'send') === false && isOn(root.innerHTML, 'full') === false, 'eligible true + linked: both controls shown');

  /* ---------- GET failure: hidden ---------- */
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: function () { return Promise.reject(err(500)); } });
  await m.ready;
  eq(root.innerHTML, '', 'an unreadable setting shows no control');

  /* ---------- linked: state from the GET ---------- */
  s = server({ channels: ['telegram'], full_text: true });
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  ok(isOn(root.innerHTML, 'send') === true && isOn(root.innerHTML, 'full') === true, 'both controls reflect the stored state');
  ok(/Send my reminders to my phone/.test(root.innerHTML), 'the toggle label');
  ok(/Show the full text of private reminders on my phone/.test(root.innerHTML), 'the checkbox label');
  ok(/arrives as &quot;You have a reminder\.&quot;|arrives as "You have a reminder\."/.test(root.innerHTML), 'the neutral-text line');
  ok(!/not switched on for this household/.test(root.innerHTML), 'mode on: no "not switched on" line');

  /* ---------- toggle on: exactly the documented body, no early "Saved" ---------- */
  s = server({});
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  ok(isOn(root.innerHTML, 'send') === false && isOn(root.innerHTML, 'full') === false, 'default off');
  s.hold = true;
  const p = m.change(input('send', true));
  ok(!/Saved/.test(root.innerHTML), 'no "Saved" before the server answered');
  ok(isDisabled(root.innerHTML, 'send') && isDisabled(root.innerHTML, 'full'), 'controls disabled while saving');
  await m.change(input('full', true));
  eq(s.puts().length, 1, 'a click while saving is not sent');
  s.hold();
  await p;
  eq(s.puts(), [['PUT', API, { channels: ['telegram'], full_text: false }]], 'toggle on PUTs exactly {channels:["telegram"], full_text:false}');
  ok(isOn(root.innerHTML, 'send') === true && /Saved\./.test(root.innerHTML), 'on, and Saved only after the server said so');
  s.hold = null;

  /* ---------- full-text checkbox ---------- */
  await m.change(input('full', true));
  eq(s.puts()[1], ['PUT', API, { channels: ['telegram'], full_text: true }], 'the checkbox PUTs full_text: true');
  ok(isOn(root.innerHTML, 'full') === true, 'checkbox on after the save');

  /* ---------- a failed PUT reverts the control ---------- */
  s.next = 500;
  await m.change(input('send', false));
  eq(s.puts()[2], ['PUT', API, { channels: [], full_text: true }], 'toggle off sends the off body');
  ok(isOn(root.innerHTML, 'send') === true, 'failed PUT: the toggle is back on (the stored state)');
  ok(new RegExp(RP.FAILED.replace(/\./g, '\\.')).test(root.innerHTML), 'failed PUT: a short honest error');
  ok(!/Saved/.test(root.innerHTML) && !/internal detail/.test(root.innerHTML), 'never "Saved", never the server text');
  ok(s.calls[s.calls.length - 1][0] === 'GET', 'then re-reads the stored state');
  await m.change(input('full', false));
  ok(isOn(root.innerHTML, 'full') === true, 'failed PUT: the checkbox is back on too');

  /* ---------- web_push already chosen is kept ---------- */
  s = server({ channels: ['web_push'] });
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  ok(isOn(root.innerHTML, 'send') === false, 'web_push alone does not show the Telegram toggle as on');
  await m.change(input('send', true));
  eq(s.puts()[0][2], { channels: ['web_push', 'telegram'], full_text: false }, 'turning it on keeps web_push');

  /* ---------- the backstop: the GET said eligible but the PUT says 403 -> reverts and says so plainly ---------- */
  s = server({});
  s.next = 403;
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  await m.change(input('send', true));
  ok(isOn(root.innerHTML, 'send') === null && isOn(root.innerHTML, 'full') === null, 'non-adult: both controls gone');
  ok(root.innerHTML.indexOf(RP.REFUSED) !== -1, 'non-adult: one plain sentence');
  ok(!/Saved/.test(root.innerHTML) && root.innerHTML.indexOf('phone delivery of reminders') === -1, 'not "Saved"; not the server text');
  eq(s.stored.channels, [], 'nothing stored');

  /* ---------- mode not on: says the choice is kept ---------- */
  s = server({ mode: 'shadow' });
  root = fakeRoot();
  m = RP.mount(root, { fetchJson: s.fetchJson });
  await m.ready;
  ok(/not switched on for this household yet/.test(root.innerHTML), 'flag not on: the page says nothing is sent yet');

  await pushChecks();

  /* ---------- the pages: desktop only ---------- */
  const desk = fs.readFileSync(path.join(DIST, 'settings.html'), 'utf8');
  const src = fs.readFileSync(path.join(DIST, 'js', 'reminder-phone-settings.js'), 'utf8');
  ok(/<script src="js\/reminder-phone-settings\.js\?v=2"><\/script>/.test(desk), 'desktop Settings loads the component (cache-busted v=2)');
  ok(!/push-notifications\.js/.test(desk), 'Settings never loads push-notifications.js (it asks for permission on page load)');
  const tg = desk.split('id="sec-telegram"')[1].split('<!-- Bluetooth devices')[0];
  ok(/id="tg-reminders-root"/.test(tg), 'mounted inside the Telegram section');
  ok(/ZoeReminderPhone\.mount\(document\.getElementById\('tg-reminders-root'\), \{ fetchJson: btFetch \}\)/.test(desk),
     'mounted with btFetch (which carries err.status for the 403)');
  ok(/function renderTgStatus\(tid\) \{\n  loadReminderPhone\(\);/.test(desk), 're-read whenever the Telegram link status is drawn');
  ok(!/\/touch\//.test(src) && !/\/touch\//.test(tg), 'nothing routes into /touch/');
  const touchDir = path.join(DIST, 'touch');
  const touchRefs = fs.readdirSync(touchDir).filter(function (f) { return /\.html$/.test(f); })
    .filter(function (f) { return /reminder-phone-settings/.test(fs.readFileSync(path.join(touchDir, f), 'utf8')); });
  eq(touchRefs, [], 'no touch page loads the desktop component');

  console.log(`reminder phone settings UI: ${checks} checks passed`);
})().catch(function (e) { console.error(e); process.exit(1); });
