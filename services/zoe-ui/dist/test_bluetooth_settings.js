#!/usr/bin/env node
/* Settings > Bluetooth devices - UI harness (no browser: node + a tiny fake DOM). Run: node dist/test_bluetooth_settings.js
 * CI runs it through services/zoe-data/tests/test_bluetooth_settings_ui_harness.py.
 *
 * Pins: every state the household sees (scanning, connecting, connected, updating with progress, failed WITH the reason); XSS
 * escaping of anything a panel supplies; the firmware update cannot be started without the safety acknowledgement and a ready
 * checklist; the "do not move or unplug for 2 minutes" and "cannot be undone" warnings; no cancel once the install is ordered;
 * finger targets >= 48 px on the touch surface; flag-off renders nothing; both Settings pages carry the section behind their gates;
 * the shared script never routes into /touch/; the Omi "Assigned to" control (members, assign / reassign / unassign, hidden when the registry
 * is off, only a listed household member can be chosen, escaped names, NO recording is ever fetched or rendered, 48 px on touch).
 * Source mutations worth re-running by Edit: drop esc() in renderDevice -> the XSS check goes red; drop `!ack` in canStartUpdate ->
 * the update-gating checks go red; drop the stepIsLocked branch -> the no-cancel-after-install check goes red; drop the `members.some` guard
 * in saveOwner -> the "only a listed member" check goes red; drop `o.available` from renderOwner -> the registry-off check goes red.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DIST = __dirname;
const BT = require(path.join(DIST, 'js', 'bluetooth-settings.js'));
let checks = 0;
function ok(cond, msg) { assert.ok(cond, msg); checks++; }
function eq(a, b, msg) { assert.deepStrictEqual(a, b, msg); checks++; }

const DEV = '0123456789abcdef';
const OTHER = 'fedcba9876543210';

/* ---------- pure helpers ---------- */
eq(BT.esc('<img src=x onerror="a()">&\''), '&lt;img src=x onerror=&quot;a()&quot;&gt;&amp;&#39;', 'esc covers < > & " \'');
eq(BT.esc(null), '', 'esc(null) is empty');
eq([-50, -60, -61, -75, -76, -85, -86, -95, null, undefined, 'x'].map(BT.rssiBars), [4, 4, 3, 3, 2, 2, 1, 1, 0, 0, 0], 'signal bars');
ok(BT.errorText({ code: 'connect_failed', message: 'Could not connect. Try later.' }) === 'Could not connect. Try later.', 'the panel sentence wins');
ok(/switched on/.test(BT.errorText({ code: 'ota_disabled' })) || /switched off/.test(BT.errorText({ code: 'ota_disabled' })), 'fallback sentence per code');
eq(BT.errorText(null), '', 'no error = no text');

/* ---------- device states ---------- */
const busy = (op, dev, extra) => Object.assign({ job_id: 'j1', op, device_id: dev, state: 'running', progress: {} }, extra || {});
eq(BT.deviceState({ id: DEV, connected: false }, null), 'idle');
eq(BT.deviceState({ id: DEV, connected: true }, null), 'connected');
eq(BT.deviceState({ id: DEV, connected: false }, busy('connect', DEV)), 'connecting');
eq(BT.deviceState({ id: DEV, connected: true }, busy('disconnect', DEV)), 'disconnecting');
eq(BT.deviceState({ id: DEV, connected: true }, busy('update_start', DEV)), 'updating');
eq(BT.deviceState({ id: DEV, connected: true }, busy('connect', OTHER)), 'connected', 'another device\'s job does not change this row');
eq(BT.deviceState({ id: DEV, connected: false }, busy('connect', DEV, { state: 'failed' })), 'idle', 'a finished job is not "connecting"');

function view(devs, over) {
  return Object.assign({ panel_id: 'p1', online: true, devices: devs, busy: null, scan: { at: 1, hidden_unnamed: 2 }, radio_holder: '' }, over || {});
}
const omi = (over) => Object.assign({ id: DEV, name: 'Omi', label: 'Omi pendant', driver: 'omi', remembered: true, connected: false, in_range: true, rssi: -62, supports_update: true }, over || {});
const baseUi = (v, over) => Object.assign({ panels: [{ panel_id: 'p1', name: 'Kitchen Panel', online: true, location: 'Kitchen' }], panelId: 'p1', view: v, infos: {}, checks: {}, failures: {}, expanded: null, ack: false, confirmForget: null, updateResult: null, error: '' }, over || {});
const btnRe = (action, dev) => new RegExp('<button[^>]*data-bt="' + action + '"' + (dev ? '[^>]*data-dev="' + dev + '"' : '') + '[^>]*>');
function button(html, action, dev) { const m = html.match(btnRe(action, dev)); return m ? { present: true, disabled: /\sdisabled/.test(m[0]) } : { present: false, disabled: false }; }

/* ---------- every state the household sees ---------- */
let h = BT.renderBody(baseUi(view([], { scan: null })));
ok(/Press Scan/.test(h) && button(h, 'scan').present && !button(h, 'scan').disabled, 'empty: invites a scan');
h = BT.renderBody(baseUi(view([], { busy: busy('scan', null, { progress: { step: 'scan' } }) })));
ok(/Looking for nearby devices/.test(h) && /Scanning\.\.\./.test(h) && button(h, 'scan').disabled, 'scanning: spinner text, scan button locked');
h = BT.renderBody(baseUi(view([], { scan: { at: 1, hidden_unnamed: 0 } })));
ok(/Nothing found\. Move the device closer/.test(h), 'scanned and found nothing');
h = BT.renderBody(baseUi(view([omi()])));
ok(/Not connected/.test(h) && button(h, 'connect', DEV).present && !button(h, 'connect', DEV).disabled && !button(h, 'disconnect', DEV).present, 'idle row: Connect');
ok(/2 unnamed devices nearby are hidden/.test(h) && /Kitchen Panel/.test(h) && /aria-label="Signal 3 of 4"/.test(h) && /-62 dBm/.test(h), 'panel picker, hidden-unnamed note, signal bars');
h = BT.renderBody(baseUi(view([omi()], { busy: busy('connect', DEV) })));
ok(/Connecting\.\.\./.test(h) && button(h, 'connect', DEV).disabled, 'connecting: label + button locked');
h = BT.renderBody(baseUi(view([omi({ connected: true })]), { infos: { [DEV]: { model: 'Omi CV 1', firmware: '3.0.18', battery: 95, charging: true, sync: { configured: true, waiting_to_upload: 4, confirmed_ranges: 12 } } } }));
ok(/Connected/.test(h) && button(h, 'disconnect', DEV).present && button(h, 'update', DEV).present && !button(h, 'connect', DEV).present, 'connected row: Disconnect + Update');
ok(/95% \(charging\)/.test(h) && /3\.0\.18/.test(h) && /4 waiting to upload, 12 stored/.test(h) && /Omi CV 1/.test(h), 'omi extras: battery, charging, firmware, sync');
h = BT.renderBody(baseUi(view([omi({ connected: true, name: 'HR', driver: 'generic', supports_update: false })])));
ok(!button(h, 'update', DEV).present, 'a generic device has no Update button');
h = BT.renderBody(baseUi(view([omi()]), { failures: { [DEV]: { code: 'connect_failed', message: 'Could not connect. Make sure it is switched on.' } } }));
ok(/Failed/.test(h) && /Could not connect\. Make sure it is switched on\./.test(h) && button(h, 'connect', DEV).present, 'failed: the reason is shown and Connect stays available');
h = BT.renderBody(baseUi(view([omi({ in_range: false, remembered: false, rssi: null })])));
ok(button(h, 'connect', DEV).disabled && /not in range/.test(h), 'not in range and not remembered: Connect disabled');
h = BT.renderBody(baseUi(view([omi({ in_range: false })])));
ok(!button(h, 'connect', DEV).disabled, 'remembered but out of range can still be tried');
h = BT.renderBody(baseUi({ panelId: null }, { panels: [], view: null }));
ok(/No panel has a Bluetooth agent yet/.test(h), 'no panels: says why');
h = BT.renderBody(baseUi(view([]), { panels: [{ panel_id: 'p1', name: 'Kitchen Panel', online: true }, { panel_id: 'p2', name: 'Hall', online: false }] }));
ok(button(h, 'panel').present && /Hall<small>offline/.test(h) && /<button[^>]*data-panel="p2"[^>]*disabled/.test(h), 'an offline panel is shown but cannot be picked');
h = BT.renderBody(baseUi(view([omi({ connected: true })]), { confirmForget: DEV }));
ok(/Forget Omi\?/.test(h) && button(h, 'forget-yes', DEV).present && button(h, 'forget-no', DEV).present && !button(h, 'disconnect', DEV).present, 'forget asks first (no browser confirm on the kiosk)');
h = BT.renderBody(baseUi(view([omi({ remembered: true })])));
ok(button(h, 'forget', DEV).present && !button(h, 'forget-yes', DEV).present, 'forget alone does not forget');
h = BT.renderBody(baseUi(view([]), { error: 'Sign in as the household admin to manage Bluetooth devices.' }));
ok(/Sign in as the household admin/.test(h), 'an auth error is shown in words');

/* ---------- XSS: nothing a panel supplies becomes markup ---------- */
const evil = '<img src=x onerror=alert(1)>"\'';
h = BT.renderBody(baseUi(view([omi({ name: evil, label: evil })]), { infos: {}, failures: { [DEV]: { code: 'x', message: evil } },
  panels: [{ panel_id: 'p1', name: evil, online: true, location: evil }] }));
ok(!/<img/i.test(h) && !/onerror=alert/.test(h.replace(/&lt;img src=x onerror=alert\(1\)&gt;/g, '')), 'device/panel names and error text are escaped');
h = BT.renderBody(baseUi(view([omi({ connected: true })]), { infos: { [DEV]: { model: evil, firmware: evil, manufacturer: evil } } }));
ok(!/<img/i.test(h), 'device info fields are escaped');
h = BT.renderBody(baseUi(view([omi({ id: '"><script>x</script>' })])));
ok(!/<script>x/.test(h), 'even a hostile device id is escaped in attributes');

/* ---------- the firmware update panel ---------- */
const gates = (over) => [
  { id: 'ota_enabled', ok: true, label: 'Updates are switched on for this panel' },
  { id: 'firmware', ok: true, label: 'Update available: 3.0.18 to 3.0.21' },
  { id: 'charging', ok: true, label: 'On its charger' },
  { id: 'battery', ok: true, label: 'Battery at least 50%' },
  { id: 'signal', ok: false, label: 'Strong, steady signal (better than -75 dBm)', detail: 'Signal is measured when you press Update.', hint: 'Move the pendant closer - right next to the panel - or use a USB Bluetooth adapter on the panel.' },
].map((g) => Object.assign({}, g, (over || {})[g.id] || {}));
const check = (over) => Object.assign({ supported: true, available: true, ready: true, current: '3.0.18', latest: '3.0.21', changelog: 'fix audio sync issue',
  min_battery: 50, min_rssi: -75, needs_ack: true, gates: gates(), blocked_by: [],
  warning: 'This install cannot be undone: the pendant has no fallback copy. Keep it on its charger, close to the panel, and do not move or unplug it for 2 minutes after it restarts.' }, over || {});
const upd = (c, ui) => BT.renderBody(baseUi(view([omi({ connected: true })]), Object.assign({ expanded: DEV, checks: { [DEV]: c } }, ui || {})));

h = upd(null);
ok(/Checking for updates/.test(h), 'update panel: checking');
h = upd(check());
ok(/3\.0\.18<\/b> to <b>3\.0\.21/.test(h) && /This cannot be undone/.test(h) && /2 minutes/.test(h) && /charger/.test(h), 'update panel: versions + the plain no-undo warning');
ok(/zbt-gates/.test(h) && /On its charger/.test(h) && /Move the pendant closer/.test(h) && /USB Bluetooth adapter/.test(h), 'the checklist shows each safety check and the way out of a weak signal');
ok(button(h, 'start-update', DEV).present && button(h, 'start-update', DEV).disabled, 'Update is DISABLED until the acknowledgement is ticked');
h = upd(check(), { ack: true });
ok(!button(h, 'start-update', DEV).disabled, 'acknowledged + ready: Update is enabled');
h = upd(check({ ready: false, blocked_by: ['charging'], gates: gates({ charging: { ok: false, detail: 'The pendant is not charging.', hint: 'Put the pendant on its charger and leave it there.' } }) }), { ack: true });
ok(button(h, 'start-update', DEV).disabled && /Put the pendant on its charger/.test(h) && /Fix the items marked above first/.test(h), 'a failed gate keeps Update disabled even when acknowledged');
ok(BT.canStartUpdate(check(), true) === true && BT.canStartUpdate(check(), false) === false && BT.canStartUpdate(check({ ready: false }), true) === false &&
   BT.canStartUpdate(check({ available: false }), true) === false && BT.canStartUpdate(null, true) === false, 'canStartUpdate truth table');
h = upd(check({ available: false, reason: 'Already on 3.0.21; the pinned release is 3.0.21.' }));
ok(/Already on 3\.0\.21/.test(h) && !button(h, 'start-update', DEV).present, 'up to date: no Update button');
h = upd({ supported: false, reason: 'This device type has no update support.' });
ok(/no update support/.test(h) && !button(h, 'start-update', DEV).present, 'unsupported device');

const running = (step, pct, msg) => baseUi(view([omi({ connected: true })], { busy: busy('update_start', DEV, { progress: { step, pct, message: msg } }) }), { expanded: DEV });
h = BT.renderBody(running('upload', 42, 'Uploading firmware part 1 of 2'));
ok(/Updating/.test(h) && /aria-valuenow="42"/.test(h) && /Uploading firmware part 1 of 2/.test(h) && /<b>42%<\/b>/.test(h), 'updating: progress bar + message + percent');
ok(button(h, 'cancel-update', DEV).present && !/Do not move or unplug/.test(h), 'during upload: can cancel, no 2-minute warning yet');
h = BT.renderBody(running('install', 86, 'Telling the pendant to install'));
ok(!button(h, 'cancel-update', DEV).present && /cannot be stopped/.test(h), 'once the install is ordered there is no cancel');
h = BT.renderBody(running('reset', 90, 'Restarting the pendant.'));
ok(/Do not move or unplug the pendant for 2 minutes/.test(h) && !button(h, 'cancel-update', DEV).present, 'after the restart command: the 2-minute warning, no cancel');
h = BT.renderBody(running('waiting', 94, 'Waiting for the pendant to come back.'));
ok(/Do not move or unplug the pendant for 2 minutes/.test(h), 'while waiting for it to return: the warning stays');

const done = (u) => BT.renderBody(baseUi(view([omi()]), { expanded: DEV, updateResult: Object.assign({ device_id: DEV }, u) }));
h = done({ state: 'succeeded', result: { from: '3.0.18', to: '3.0.21' } });
ok(/Updated to 3\.0\.21 \(from 3\.0\.18\)/.test(h) && button(h, 'dismiss-update', DEV).present, 'result: success');
h = done({ state: 'failed', error: { code: 'gate_failed', message: 'Strong, steady signal: Median signal is -91 dBm.', detail: { gates: gates({ signal: { ok: false, detail: 'Median signal is -91 dBm.' } }) } } });
ok(/Update failed/.test(h) && /Median signal is -91 dBm/.test(h) && /Move the pendant closer/.test(h), 'result: failure shows the reason and the failed checks');
h = done({ state: 'failed', error: { code: 'update_failed', message: 'The pendant did not come back within 5 minutes. Leave it on its charger.' } });
ok(/did not come back within 5 minutes/.test(h) && /Leave it on its charger/.test(h), 'result: failure after the restart tells the household what to do');
h = done({ state: 'cancelled', error: { code: 'cancelled', message: 'Update cancelled before anything was installed. The pendant is unchanged.' } });
ok(/Cancelled/.test(h) && /unchanged/.test(h), 'result: cancelled');

/* ---------- the touch surface ---------- */
ok(/\.zbt\[data-surface=touch\] \.zbt-btn[^{]*\{[^}]*min-height:48px/.test(BT.CSS), 'touch buttons are >= 48 px tall');
ok(/\.zbt\[data-surface=touch\][^{]*\.zbt-panelbtn[^{]*\{[^}]*min-height:48px/.test(BT.CSS) || /\.zbt-panelbtn\{[^}]*min-height:48px/.test(BT.CSS) || /zbt-panelbtn\{min-height:48px/.test(BT.CSS), 'touch panel picker buttons are >= 48 px');
ok(/\.zbt\[data-surface=touch\] \.zbt-ack input\{width:32px;height:32px\}/.test(BT.CSS), 'the acknowledgement checkbox is large on touch');
ok(/prefers-reduced-motion/.test(BT.CSS), 'motion is gated');
ok(/\.zbt\[data-surface=touch\] \.zbt-select\{[^}]*min-height:48px/.test(BT.CSS), 'the "Assigned to" select is >= 48 px on touch');

/* ---------- Omi ownership: the "Assigned to" control ---------- */
const MEMBERS = [{ user_id: 'ann', name: 'Ann' }, { user_id: 'ben', name: 'Ben <img src=x onerror=a()>' }];
const omiUi = (devices, over) => Object.assign({ available: true, members: MEMBERS, devices: devices || {}, saving: null, error: '' }, over || {});
{
  const dev = omi();
  ok(BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi() })).length > 0 && /Assigned to/.test(BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi() }))), 'an Omi pendant has an Assigned to control');
  eq(BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({}, { available: false }) })), '', 'registry off (404/403): no control at all');
  eq(BT.renderOwner(dev, baseUi(view([dev]), { omi: undefined })), '', 'not loaded yet: no control');
  eq(BT.renderOwner(omi({ driver: 'generic' }), baseUi(view([]), { omi: omiUi() })), '', 'only Omi pendants are assignable');
  let h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi() }));
  ok(/<select[^>]*data-bt="assign"[^>]*data-dev="0123456789abcdef"/.test(h) && /<option value="" selected>Nobody yet/.test(h), 'unassigned: Nobody yet is selected');
  ok(/<option value="ann">Ann<\/option>/.test(h) && /Nobody owns this pendant yet/.test(h), 'members are listed; the unassigned note says what happens');
  ok(!/<img/.test(h) && /Ben &lt;img src=x onerror=a\(\)&gt;/.test(h), 'a member name is escaped');
  h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({ [DEV]: { device_id: DEV, user_id: 'ben', active: true } }) }));
  ok(/<option value="ben" selected>/.test(h) && !/value="" selected/.test(h), 'assigned: that member is selected');
  ok(/sync to this person only, whoever is signed in/.test(h) && /does not move recordings already saved/.test(h) && /let it finish syncing first/.test(h), 'the reassignment warning says what moves and what does not');
  h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({ [DEV]: { device_id: DEV, user_id: 'ghost', active: true } }) }));
  ok(/<option value="ghost" selected>Unknown member/.test(h), 'an owner who is not in the member list is still shown, not silently replaced');
  h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({ [DEV]: { device_id: DEV, user_id: 'ann', active: false } }) }));
  ok(/value="" selected/.test(h), 'an inactive registration reads as unassigned');
  h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({}, { saving: DEV }) }));
  ok(/<select[^>]*disabled/.test(h) && /Saving\.\.\./.test(h), 'saving: the select is locked');
  h = BT.renderOwner(dev, baseUi(view([dev]), { omi: omiUi({}, { error: 'Pick a household member.' }) }));
  ok(/Pick a household member\./.test(h), 'an error is shown');
  const whole = BT.renderBody(baseUi(view([dev]), { omi: omiUi({ [DEV]: { device_id: DEV, user_id: 'ann', active: true } }) }));
  ok(/Assigned to/.test(whole) && !/\/api\/omi\/recordings|transcript|recorded for/i.test(whole), 'the device row never shows or links a recording (the admin manages who owns it, not what it heard)');
}

/* ---------- the controller, against a fake DOM and a fake server ---------- */
function fakeRoot() {
  const r = { innerHTML: '', handlers: {}, addEventListener(t, f) { (r.handlers[t] = r.handlers[t] || []).push(f); }, removeEventListener(t, f) { r.handlers[t] = (r.handlers[t] || []).filter((x) => x !== f); } };
  r.click = (action, dev, panel, value) => {
    const b = button(r.innerHTML, action, dev);
    const m = r.innerHTML.match(new RegExp('<(?:button|input|select)[^>]*data-bt="' + action + '"[^>]*' + (dev ? 'data-dev="' + dev + '"' : '') + '[^>]*>'));
    if (!m) throw new Error('no ' + action + ' control rendered');
    const el = { dataset: { bt: action, dev: dev, panel: panel }, disabled: /\sdisabled/.test(m[0]), checked: true, value: value };
    const type = (action === 'ack' || action === 'assign') ? 'change' : 'click';
    (r.handlers[type] || []).forEach((f) => f({ type: type, target: { closest: () => el } }));
  };
  return r;
}
function fakeTimers() { const q = []; return { q, setTimeout: (f, ms) => { q.push({ f, ms }); return q.length; }, clearTimeout: () => {}, async run(n) { for (let i = 0; i < (n || 1); i++) { const t = q.shift(); if (t) await t.f(); await new Promise((r) => setImmediate(r)); } } }; }
function fakeServer(script) {
  const log = [];
  const state = { jobs: {}, n: 0, view: view([]), status: {} };
  const s = {
    log, state,
    async fetchJson(method, url, body) {
      log.push({ method, url, body });
      if (script && script.fail && script.fail(method, url)) { const e = new Error('nope'); e.status = script.fail(method, url); throw e; }
      if (url.indexOf('/api/omi/') === 0) {
        const o = state.omi;
        if (!o) { const e = new Error('Not Found'); e.status = 404; throw e; }
        if (url === '/api/omi/members') return { members: o.members };
        if (url === '/api/omi/devices') return { devices: Object.keys(o.devices).map((k) => o.devices[k]) };
        const dm = url.match(/^\/api\/omi\/devices\/([0-9a-f]{16})$/);
        if (dm && method === 'PUT') {
          if (o.refuse) { const e = new Error('Pick a household member (guests cannot own a pendant).'); e.status = 400; throw e; }
          o.devices[dm[1]] = { device_id: dm[1], user_id: body.user_id, active: true }; return o.devices[dm[1]];
        }
        if (dm && method === 'DELETE') { o.devices[dm[1]] = { device_id: dm[1], user_id: '', active: false }; return { ok: true }; }
      }
      if (url === '/api/bluetooth/config') return { enabled: true };
      if (url === '/api/bluetooth/panels') return { panels: [{ panel_id: 'p1', name: 'Kitchen Panel', online: true, location: 'Kitchen' }] };
      if (url === '/api/bluetooth/panels/p1') return state.view;
      let m = url.match(/^\/api\/bluetooth\/panels\/p1\/jobs\/(\w+)$/);
      if (m) return state.jobs[m[1]];
      m = url.match(/^\/api\/bluetooth\/panels\/p1\/(scan|devices\/([0-9a-f]{16})\/(\w+))$/);
      if (m && method === 'POST') {
        const id = 'job' + (++state.n);
        const job = { job_id: id, panel_id: 'p1', op: m[1] === 'scan' ? 'scan' : m[3], device_id: m[2] || null, state: 'queued', progress: {}, result: null, error: null };
        state.jobs[id] = job; state.view = Object.assign({}, state.view, { busy: job });
        return job;
      }
      throw new Error('unexpected ' + method + ' ' + url);
    },
    finish(id, patch) { Object.assign(state.jobs[id], patch); if (state.view.busy && state.view.busy.job_id === id && ['succeeded', 'failed', 'cancelled'].indexOf(patch.state) >= 0) state.view = Object.assign({}, state.view, { busy: null }); }
  };
  return s;
}
const flush = () => new Promise((r) => setImmediate(r));

(async function controller() {
  /* flag off: 404 on the first call renders nothing and tells the host */
  {
    const root = fakeRoot(), t = fakeTimers(); let gone = 0, ready = 0;
    BT.mount(root, { surface: 'touch', fetchJson: async () => { const e = new Error('Not Found'); e.status = 404; throw e; }, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
      onUnavailable: () => gone++, onReady: () => ready++ });
    await flush(); await flush();
    ok(gone === 1 && ready === 0 && root.innerHTML === '' && t.q.length === 0, 'flag off (404): nothing rendered, host told, no polling scheduled');
  }
  /* admin without a session: shown, with the reason, no crash */
  {
    const root = fakeRoot(), t = fakeTimers(); let ready = 0;
    BT.mount(root, { fetchJson: async () => { const e = new Error('Sign in'); e.status = 403; throw e; }, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, onReady: () => ready++ });
    await flush(); await flush();
    ok(ready === 1 && /Sign in as the household admin/.test(root.innerHTML), '403: the section appears and says to sign in as the admin');
  }
  /* the full flow */
  {
    const srv = fakeServer(), root = fakeRoot(), t = fakeTimers(); const store = {};
    srv.state.view = view([omi()]);
    const c = BT.mount(root, { surface: 'touch', fetchJson: srv.fetchJson, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
      storage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } } });
    await flush(); await flush();
    ok(/data-surface="touch"/.test(root.innerHTML) && /Kitchen Panel/.test(root.innerHTML) && button(root.innerHTML, 'connect', DEV).present, 'mounted: picked the online panel, listed the device');
    ok(c.state().panelId === 'p1', 'default panel chosen');

    root.click('scan'); await flush(); await flush();
    const scanPost = srv.log.filter((l) => l.method === 'POST').pop();
    ok(scanPost.url === '/api/bluetooth/panels/p1/scan', 'Scan posts to the scan route');
    ok(/Looking for nearby devices/.test(root.innerHTML) && button(root.innerHTML, 'scan').disabled, 'UI shows scanning while the job runs');
    srv.finish('job1', { state: 'succeeded', result: {} });
    await t.run(3); await flush();
    ok(!/Looking for nearby devices/.test(root.innerHTML), 'scan finished: spinner gone');

    root.click('connect', DEV); await flush(); await flush();
    ok(srv.log.filter((l) => l.method === 'POST').pop().url === '/api/bluetooth/panels/p1/devices/' + DEV + '/connect', 'Connect posts to the device route');
    ok(/Connecting\.\.\./.test(root.innerHTML), 'UI shows connecting');
    srv.state.view = view([omi({ connected: true })]);
    srv.finish('job2', { state: 'succeeded', result: { connected: true, info: { model: 'Omi CV 1', firmware: '3.0.18', battery: 88, charging: true } } });
    await t.run(3); await flush();
    ok(/Connected/.test(root.innerHTML) && /88% \(charging\)/.test(root.innerHTML), 'connected: info from the connect job is shown');

    root.click('update', DEV); await flush(); await flush();
    ok(srv.log.filter((l) => l.method === 'POST').pop().url.endsWith('/update_check') && /Checking for updates/.test(root.innerHTML), 'Update opens the panel and asks for a check');
    srv.finish('job3', { state: 'succeeded', result: check() });
    await t.run(3); await flush();
    ok(/This cannot be undone/.test(root.innerHTML) && button(root.innerHTML, 'start-update', DEV).disabled, 'check result shown; Update locked until acknowledged');

    const before = srv.log.length;
    try { root.click('start-update', DEV); } catch (e) { /* control present but disabled */ }
    await flush();
    ok(srv.log.length === before, 'pressing a DISABLED Update posts nothing');
    root.click('ack'); await flush();
    ok(!button(root.innerHTML, 'start-update', DEV).disabled, 'ticking the acknowledgement enables Update');
    root.click('start-update', DEV); await flush(); await flush();
    const up = srv.log.filter((l) => l.method === 'POST').pop();
    eq([up.url, up.body], ['/api/bluetooth/panels/p1/devices/' + DEV + '/update_start', { confirm: true, ack_no_undo: true }], 'Update posts the explicit confirmation and no-undo acknowledgement');
    ok(c.state().ack === false, 'the acknowledgement is consumed (a second update needs a fresh one)');

    srv.state.jobs.job4.state = 'running'; srv.state.jobs.job4.progress = { step: 'upload', pct: 37, message: 'Uploading firmware part 1 of 2' };
    await t.run(1); await flush();
    ok(/aria-valuenow="37"/.test(root.innerHTML), 'progress is polled and drawn');
    srv.finish('job4', { state: 'failed', error: { code: 'gate_failed', message: 'Strong, steady signal: Median signal is -91 dBm. Move the pendant closer.', detail: { gates: gates() } } });
    await t.run(2); await flush();
    ok(/Update failed/.test(root.innerHTML) && /Median signal is -91 dBm/.test(root.innerHTML), 'a refused update ends in a readable failure');
    root.click('dismiss-update', DEV);
    ok(!/Update failed/.test(root.innerHTML), 'dismissed');

    root.click('forget', DEV);
    const n = srv.log.length;
    ok(/Forget Omi\?/.test(root.innerHTML) && srv.log.length === n, 'Forget only asks');
    root.click('forget-no', DEV);
    ok(!/Forget Omi\?/.test(root.innerHTML), 'Keep cancels the question');
    root.click('forget', DEV); root.click('forget-yes', DEV); await flush(); await flush();
    ok(srv.log.filter((l) => l.method === 'POST').pop().url.endsWith('/devices/' + DEV + '/forget'), 'only the confirmed Forget posts');

    c.destroy();
    ok((root.handlers.click || []).length === 0, 'destroy removes the listeners');
  }
  /* Omi ownership through the controller */
  {
    const srv = fakeServer(), root = fakeRoot(), t = fakeTimers();
    srv.state.view = view([omi(), omi({ id: OTHER, name: 'HR Strap', driver: 'generic', supports_update: false })]);
    srv.state.omi = { members: MEMBERS, devices: {} };
    const c = BT.mount(root, { surface: 'touch', fetchJson: srv.fetchJson, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
    await flush(); await flush(); await flush();
    ok(/Assigned to/.test(root.innerHTML) && /<option value="ann">Ann/.test(root.innerHTML), 'mounted: members and the registry were loaded, the control is drawn');
    ok((root.innerHTML.match(/Assigned to/g) || []).length === 1, 'only the Omi pendant gets the control, not the generic device');
    root.click('assign', DEV, null, 'ben'); await flush(); await flush(); await flush();
    const put = srv.log.filter((l) => l.method === 'PUT').pop();
    eq([put.url, put.body], ['/api/omi/devices/' + DEV, { user_id: 'ben' }], 'choosing a member PUTs exactly that user id');
    ok(/<option value="ben" selected>/.test(root.innerHTML), 'the new owner is shown after the reload');
    root.click('assign', DEV, null, 'ann'); await flush(); await flush(); await flush();
    ok(srv.log.filter((l) => l.method === 'PUT').pop().body.user_id === 'ann' && /<option value="ann" selected>/.test(root.innerHTML), 'reassigning is the same call');
    root.click('assign', DEV, null, ''); await flush(); await flush(); await flush();
    ok(srv.log.filter((l) => l.method === 'DELETE').pop().url === '/api/omi/devices/' + DEV && /value="" selected/.test(root.innerHTML), 'Nobody yet unassigns');
    const n = srv.log.length;
    root.click('assign', DEV, null, 'someone-not-listed'); await flush(); await flush();
    ok(srv.log.length === n, 'only a listed household member can be chosen; anything else sends nothing');
    srv.state.omi.refuse = true;
    root.click('assign', DEV, null, 'ben'); await flush(); await flush(); await flush();
    ok(/Pick a household member/.test(root.innerHTML) && !/<option value="ben" selected>/.test(root.innerHTML), 'a refused change shows the server sentence and the old owner stays');
    ok(!srv.log.some((l) => /\/api\/omi\/recordings/.test(l.url)), 'the Bluetooth settings never fetch a recording');
    c.destroy();
  }
  /* registry off: the whole control is absent and the section still works */
  {
    const srv = fakeServer(), root = fakeRoot(), t = fakeTimers();
    srv.state.view = view([omi()]);
    BT.mount(root, { fetchJson: srv.fetchJson, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
    await flush(); await flush(); await flush();
    ok(!/Assigned to/.test(root.innerHTML) && button(root.innerHTML, 'connect', DEV).present, 'registry flag off (404): no Assigned to control, Connect still there');
    const asks = srv.log.filter((l) => /^\/api\/omi\//.test(l.url)).length;
    await t.run(3); await flush();
    ok(srv.log.filter((l) => /^\/api\/omi\//.test(l.url)).length === asks, 'a 404 is remembered: the page does not keep asking');
  }

  /* a failed connect surfaces the panel's reason on the row */
  {
    const srv = fakeServer(), root = fakeRoot(), t = fakeTimers();
    srv.state.view = view([omi()]);
    BT.mount(root, { fetchJson: srv.fetchJson, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
    await flush(); await flush();
    root.click('connect', DEV); await flush(); await flush();
    srv.finish('job1', { state: 'failed', error: { code: 'connect_failed', message: 'Could not connect. Make sure it is switched on, in range and not connected to a phone.' } });
    await t.run(3); await flush();
    ok(/Failed/.test(root.innerHTML) && /not connected to a phone/.test(root.innerHTML) && button(root.innerHTML, 'connect', DEV).present, 'failed connect: reason on the row, Connect available to try again');
  }
  /* a hostile device id in the DOM is never turned into a URL */
  {
    const srv = fakeServer(), root = fakeRoot(), t = fakeTimers();
    BT.mount(root, { fetchJson: srv.fetchJson, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
    await flush(); await flush();
    const el = { dataset: { bt: 'connect', dev: '../../x' }, disabled: false };
    const n = srv.log.length;
    root.handlers.click.forEach((f) => f({ type: 'click', target: { closest: () => el } }));
    await flush();
    ok(srv.log.length === n, 'a malformed device id never reaches fetch');
  }

  /* ---------- the pages that host it ---------- */
  const desktop = fs.readFileSync(path.join(DIST, 'settings.html'), 'utf8');
  const touch = fs.readFileSync(path.join(DIST, 'touch', 'settings.html'), 'utf8');
  const shared = fs.readFileSync(path.join(DIST, 'js', 'bluetooth-settings.js'), 'utf8');
  ok(/<script src="js\/bluetooth-settings\.js\?v=\d+"><\/script>/.test(desktop) && /id="sec-bluetooth"/.test(desktop) && /bluetooth:\s+loadBluetooth/.test(desktop), 'desktop Settings hosts the section');
  ok(/feature: 'bluetooth'/.test(desktop) && /if \(item\.feature && !FEATURES\[item\.feature\]\) continue;/.test(desktop) && /'\/api\/bluetooth\/config'/.test(desktop), 'desktop nav item is hidden unless the server confirms the feature');
  ok(/id="bluetoothSection"[^>]*display: none/.test(touch) && /toggleSection\('bluetooth'\)/.test(touch) && /id="btRoot"/.test(touch) && /bluetooth-settings\.js\?v=\d+/.test(touch), 'touch Settings hosts the section, hidden by default');
  ok(/surface: 'touch'/.test(touch) && /zoeResolveIsAdmin/.test(touch) && /onUnavailable[^}]*display = 'none'/.test(touch), 'touch section is admin-gated and hides itself when the feature is off');
  ok(!/\/touch\//.test(shared), 'the shared component never routes into /touch/ (desktop is desktop, touch is touch)');
  ok(!/confirm\(|alert\(|prompt\(/.test(shared), 'no browser dialogs (the kiosk suppresses them)');
  console.log('bluetooth settings UI: ' + checks + ' checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
