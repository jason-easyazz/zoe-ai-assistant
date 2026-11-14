#!/usr/bin/env node
/* Settings > Telegram > Reminders on my phone - UI harness (no browser: node + a fake root + a stubbed fetch).
 * Run: node dist/test_reminder_phone_settings.js. CI: services/zoe-data/tests/test_reminder_phone_settings_ui_harness.py.
 * Record: docs/knowledge/reminder-phone-delivery.md. Invented data only.
 *
 * Pins: state loads from the GET; hidden when Telegram is not linked, and unless the GET says eligible === true (a child, an older
 * server without the field); the toggle sends exactly {channels:["telegram"], full_text:false};
 * the full-text checkbox maps to full_text; another channel already chosen (web_push) is kept; nothing says "Saved" before the server
 * answered and the controls are disabled meanwhile; a failed PUT reverts the control and says so, staying disabled until the
 * recovery GET settles (answers or fails); a 403 (not an adult) reverts and
 * replaces the section with one plain sentence; no server message is rendered; desktop-only wiring (never /touch/).
 * Negative controls (run on scratch copies): drop the telegram_linked check in shown() -> "not linked" goes red; drop the eligible
 * check in shown() -> "eligible false" (a child) goes red; in the PUT failure branch, store the clicked body into state.view and skip
 * the re-read -> "reverts" goes red; clear busy before the recovery GET settles -> "stay disabled during the recovery GET" goes red.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DIST = __dirname;
const RP = require(path.join(DIST, 'js', 'reminder-phone-settings.js'));
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

/* ---------- pure ---------- */
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

  /* ---------- a failed PUT keeps the controls disabled until the recovery GET settles ---------- */
  for (const getFails of [false, true]) {
    const tag = getFails ? 'recovery GET fails' : 'recovery GET answers';
    const calls = [];
    let gets = 0, releaseGet = null;
    const truth = { channels: ['telegram'], full_text: false, telegram_linked: true, mode: 'on', eligible: true };  // a timeout that DID save
    const fj = function (method, url, body) {
      calls.push([method, url, body]);
      if (method === 'PUT') return Promise.reject(err(500));
      if (++gets === 1) return Promise.resolve({ channels: [], full_text: false, telegram_linked: true, mode: 'on', eligible: true });
      return new Promise(function (res, rej) { releaseGet = function () { if (getFails) rej(err(502)); else res(JSON.parse(JSON.stringify(truth))); }; });
    };
    root = fakeRoot();
    m = RP.mount(root, { fetchJson: fj });
    await m.ready;
    const pending = m.change(input('send', true));
    for (let i = 0; i < 5 && !releaseGet; i++) await Promise.resolve();
    ok(typeof releaseGet === 'function', tag + ': the recovery GET is in flight');
    ok(isDisabled(root.innerHTML, 'send') && isDisabled(root.innerHTML, 'full'), tag + ': controls stay disabled during the recovery GET');
    await m.change(input('full', true));
    eq(calls.filter(function (c) { return c[0] === 'PUT'; }).length, 1, tag + ': a click during the recovery GET sends nothing');
    releaseGet();
    await pending;
    ok(isDisabled(root.innerHTML, 'send') === false && isDisabled(root.innerHTML, 'full') === false, tag + ': re-enabled once it settled');
    ok(root.innerHTML.indexOf(RP.FAILED) !== -1, tag + ': the stored-state message stays');
    if (getFails) {
      ok(isOn(root.innerHTML, 'send') === false && isOn(root.innerHTML, 'full') === false, tag + ': the last known server state');
    } else {
      ok(isOn(root.innerHTML, 'send') === true && isOn(root.innerHTML, 'full') === false, tag + ': the final state is what the GET returned');
    }
  }

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

  /* ---------- the pages: desktop only ---------- */
  const desk = fs.readFileSync(path.join(DIST, 'settings.html'), 'utf8');
  const src = fs.readFileSync(path.join(DIST, 'js', 'reminder-phone-settings.js'), 'utf8');
  ok(/<script src="js\/reminder-phone-settings\.js\?v=\d+"><\/script>/.test(desk), 'desktop Settings loads the component');
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
