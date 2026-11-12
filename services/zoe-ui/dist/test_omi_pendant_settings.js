#!/usr/bin/env node
/* Settings > My pendant - UI harness (no browser: node + a tiny fake root). Run: node dist/test_omi_pendant_settings.js
 * CI runs it through services/zoe-data/tests/test_omi_pendant_settings_ui_harness.py. Record: docs/knowledge/omi-linking.md.
 *
 * Pins: linked / not linked / last sync; linking off says "ask the household admin"; sync off (404) renders nothing; a setting the
 * household has off shows Off, is disabled and a tap sends nothing (a member can only narrow); keep-audio days never offer more than
 * the ceiling; Link is touch-only and the first link shows the one-sentence notice; unlink asks first and warns about unsynced audio;
 * escaping; >= 48 px touch targets; no text input anywhere; desktop Settings carries the section and the shared script never routes
 * into /touch/.
 * Source mutations worth re-running by Edit: drop `op &&` in renderSwitch's `shown` -> the household-off check goes red; drop the
 * operator check in toggleBody -> "a tap sends nothing" goes red; drop `Math.min(d, max)` in daysBody -> the ceiling check goes red;
 * drop the `surface !== 'touch'` branch in renderLink -> the desktop-never-links check goes red; drop esc() in renderPendants -> XSS.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DIST = __dirname;
const OP = require(path.join(DIST, 'js', 'omi-pendant-settings.js'));
let checks = 0;
function ok(cond, msg) { assert.ok(cond, msg); checks++; }
function eq(a, b, msg) { assert.deepStrictEqual(a, b, msg); checks++; }

const HND = '0123456789abcdef01234567';     // an opaque member-scoped handle (24 hex), never a device id
const DEVICE_ID_LIKE = /\b[0-9a-f]{16}\b/;
const ALL_ON = { sync: true, speaker_tag: true, learn_voices: true, keep_audio_days_max: 7, day_review: true };
function view(extra) {
  return Object.assign({
    linking: { enabled: true, reason: null, message: '' },
    pendants: [], window: null, notice: 'Just so you know, your pendant records the people around you.', notice_ack_at: null,
    settings: { learning_enabled: true, sync_paused: false, speaker_tag_enabled: true, keep_audio_days: null, day_review_enabled: true },
    operator: Object.assign({}, ALL_ON)
  }, extra || {});
}

/* ---------- pure ---------- */
eq(OP.esc('<b a="x">&\''), '&lt;b a=&quot;x&quot;&gt;&amp;&#39;', 'esc');
eq(OP.ago(null), 'not yet', 'no sync yet');
eq(OP.ago(new Date(Date.now() - 30 * 60000).toISOString()), '30 minutes ago', 'minutes');
eq(OP.dayChoices(7), [1, 3, 7], 'never more than the ceiling');
eq(OP.dayChoices(10), [1, 3, 7, 10], 'the ceiling itself is offered');
eq(OP.daysBody(view(), '30'), { keep_audio_days: 7 }, 'a longer choice is capped at the ceiling');
eq(OP.daysBody(view(), ''), { keep_audio_default: true }, 'household default');
eq(OP.daysBody(view(), '0'), null, 'nonsense sends nothing');

/* ---------- states ---------- */
let h = OP.render(view(), { surface: 'touch', panelId: 'p1' });
ok(/No pendant linked to you yet/.test(h), 'not linked');
ok(/data-action="link"/.test(h) && /min-height:48px/.test(h), 'touch shows a big Link button');
ok(!/<input|<textarea|contenteditable/i.test(h), 'nothing is typed on the panel');
h = OP.render(view(), { surface: 'desktop', panelId: null });
ok(!/data-action="link"/.test(h) && /link my pendant/.test(h), 'desktop never links; it says how to at the panel');
h = OP.render(view({ pendants: [{ handle: HND, label: '<img onerror=x>', last_sync_at: new Date(Date.now() - 3 * 3600000).toISOString() }] }),
  { surface: 'touch', panelId: 'p1' });
ok(/Linked\. Last synced 3 hours ago/.test(h) && /data-action="unlink"/.test(h), 'linked + last sync + unlink');
ok(!/<img onerror/.test(h) && /&lt;img onerror=x&gt;/.test(h), 'label escaped');
h = OP.render(view({ linking: { enabled: false, reason: 'linking_off',
  message: "Pendant linking isn't turned on here yet. Ask the household admin to turn on pendant linking." } }), { surface: 'touch', panelId: 'p1' });
ok(/ask the household admin to turn on pendant linking/i.test(h) && !/data-action="link"/.test(h), 'linking off: honest sentence, no Link');
ok(/data-switch="sync_paused"/.test(h), 'settings still listed with linking off');

/* ---------- narrow, never widen ---------- */
const offTag = view({ operator: Object.assign({}, ALL_ON, { speaker_tag: false }) });
h = OP.render(offTag, { surface: 'desktop' });
ok(/Off for the household/.test(h), 'the household-off setting says so');
ok(/data-key="speaker_tag_enabled" disabled/.test(h), 'and its toggle is disabled');
eq(OP.toggleBody(offTag, 'speaker_tag_enabled'), null, 'a tap on a household-off setting sends nothing');
eq(OP.toggleBody(view(), 'speaker_tag_enabled'), { speaker_tag_enabled: false }, 'a member can turn their own off');
eq(OP.toggleBody(view(), 'sync_paused'), { sync_paused: true }, 'turning sync off = pause');

/* ---------- controller: link with the notice, unlink with the warning ---------- */
function fakeRoot() { return { innerHTML: '', addEventListener: function () {} }; }
function btn(action, attrs) {
  const a = Object.assign({ 'data-action': action }, attrs || {});
  return { target: { getAttribute: function (k) { return a[k] === undefined ? null : a[k]; }, parentNode: null } };
}
const calls = [];
let current = view({ pendants: [{ handle: HND, label: '', last_sync_at: null }] });
let pendingOnce = false;
function fetchJson(method, url, body) {
  calls.push([method, url, body]);
  if (method === 'GET') return Promise.resolve(current);
  if (url === '/api/omi/my-pendant/link' && !body.notice_ack) { const e = new Error('notice'); e.status = 428; return Promise.reject(e); }
  if (method === 'DELETE' && pendingOnce && !/confirm=true/.test(url)) {
    pendingOnce = false;
    const e = new Error('Your pendant still has recordings that have not synced. If you unlink it now, they will go to whoever links it next.');
    e.status = 409;
    return Promise.reject(e);
  }
  if (method === 'DELETE') return Promise.resolve({ ok: true, note: 'Done. Your pendant is unlinked.' });
  return Promise.resolve({ ok: true });
}
const timers = { set: function () { return 1; }, clear: function () {} };

(async function () {
  const root = fakeRoot();
  const m = OP.mount(root, { surface: 'touch', fetchJson: fetchJson, panelId: function () { return 'p1'; }, timers: timers });
  await m.ready;
  await m.click(btn('link'));
  ok(m.state().notice && /records the people around you/.test(root.innerHTML), 'the first link shows the notice');
  await m.click(btn('notice-ok'));
  const linkCalls = calls.filter(c => c[1] === '/api/omi/my-pendant/link');
  eq(linkCalls[linkCalls.length - 1][2], { panel_id: 'p1', notice_ack: true }, 'link names only the panel; never another member');
  ok(!DEVICE_ID_LIKE.test(root.innerHTML.replace(HND, '')), 'the page never shows a device id');
  await m.click(btn('unlink-force'));
  ok(!calls.some(c => c[0] === 'DELETE'), 'an "unlink anyway" with no warning before it does nothing');
  await m.click(btn('unlink', { 'data-handle': HND }));
  ok(/go to whoever links it next/.test(root.innerHTML) && !calls.some(c => c[0] === 'DELETE'), 'unlink asks first, with the warning');
  await m.click(btn('unlink-ok'));
  ok(calls.some(c => c[0] === 'DELETE' && c[1] === '/api/omi/my-pendant/' + HND), 'then unlinks my pendant, by its handle');
  await m.click(btn('unlink', { 'data-handle': '../devices/x' }));
  await m.click(btn('unlink-ok'));
  ok(calls.filter(c => c[0] === 'DELETE').length === 1, 'a malformed handle is never sent');
  /* unsynced audio known: the first unlink is refused with the warning, a second explicit tap unlinks anyway */
  pendingOnce = true;
  await m.click(btn('unlink', { 'data-handle': HND }));
  await m.click(btn('unlink-ok'));
  ok(/Unlink anyway/.test(root.innerHTML) && /go to whoever links it next/.test(root.innerHTML), 'refused: the warning says where the audio goes');
  await m.click(btn('unlink-force'));
  const dels = calls.filter(c => c[0] === 'DELETE');
  eq(dels[dels.length - 1][1], '/api/omi/my-pendant/' + HND + '?confirm=true', 'the second, explicit tap confirms');
  current = view({ operator: Object.assign({}, ALL_ON, { day_review: false }) });
  await m.reload();
  const before = calls.length;
  await m.click(btn('toggle', { 'data-key': 'day_review_enabled' }));
  ok(calls.length === before, 'a household-off toggle makes no request');

  /* sync off = 404 = nothing rendered */
  let gone = false;
  const r2 = fakeRoot();
  const m2 = OP.mount(r2, { surface: 'touch', timers: timers, fetchJson: function () { const e = new Error('nf'); e.status = 404; return Promise.reject(e); },
    onUnavailable: function () { gone = true; } });
  await m2.ready;
  ok(gone && r2.innerHTML === '', 'pendant sync off: the section stays hidden');

  /* ---------- the pages ---------- */
  const desk = fs.readFileSync(path.join(DIST, 'settings.html'), 'utf8');
  const src = fs.readFileSync(path.join(DIST, 'js', 'omi-pendant-settings.js'), 'utf8');
  ok(/js\/omi-pendant-settings\.js/.test(desk) && /id="sec-pendant"/.test(desk) && /ZoeOmiPendant\.mount/.test(desk), 'desktop carries the section');
  ok(!/\/touch\//.test(src), 'the shared script never routes into /touch/');
  ok(!/\/touch\//.test(desk.split('sec-pendant')[1].split('</div>\n  </div>')[0]), 'the desktop section links nothing in /touch/');

  console.log(`omi pendant settings UI: ${checks} checks passed`);
})().catch(function (e) { console.error(e); process.exit(1); });
