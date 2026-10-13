/* Settings > Bluetooth devices - ONE component shared by the desktop Settings page and the touch estate's Settings page.
 *
 *   ZoeBluetooth.mount(rootEl, { surface: 'desktop'|'touch', fetchJson(method, url, body) -> Promise<json>, onUnavailable() })
 *
 * Flag-dark: the server answers 404 on /api/bluetooth/* while ZOE_BLUETOOTH_SETTINGS is off; mount() then calls onUnavailable() and
 * renders nothing, so the host page can hide the whole section. Household members never run a command: every step (pick a panel, scan,
 * connect, disconnect, forget, check for and run a firmware update) is a button here.
 *
 * Split so it can be tested without a browser (dist/test_bluetooth_settings.js): the PURE half (esc, rssiBars, deviceState, errorText,
 * gateRows, render*) has no DOM access; the controller takes its root, fetch and timers by injection.
 * Everything that came from the server (device names, messages) goes through esc(): a panel is trusted to be a panel, not to author HTML.
 *
 * Omi pendants carry an "Assigned to" control (GET /api/omi/members + /api/omi/devices, PUT/DELETE /api/omi/devices/{id}): which household
 * member the pendant's recordings sync to. It appears only when the server has the device registry on (ZOE_OMI_DEVICE_OWNERS; a 404/403
 * hides it) and it never fetches or shows a recording - an admin chooses WHO owns a pendant, not what it heard.
 * No backward-compat risk to touch consumers: this is a NEW file; it defines one global (ZoeBluetooth) and touches nothing else.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ZoeBluetooth = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DEVICE_ID = /^[0-9a-f]{16}$/;
  var PANEL_ID = /^[A-Za-z0-9._:-]{1,64}$/;
  var POLL_IDLE_MS = 5000;
  var POLL_JOB_MS = 1000;

  /* ------------------------------------------------------------------ pure helpers */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* 0-4 bars. The panel's own radio is weak (about -91 dBm even beside it), so the scale is stretched to what it can actually hear. */
  function rssiBars(rssi) {
    if (typeof rssi !== 'number' || !isFinite(rssi)) return 0;
    if (rssi >= -60) return 4;
    if (rssi >= -75) return 3;
    if (rssi >= -85) return 2;
    return 1;
  }

  var ERROR_TEXT = {
    radio_busy: 'Bluetooth is busy on that panel right now. Try again in a minute.',
    not_found: 'That device is not in the list any more. Scan again.',
    connect_failed: 'Could not connect. Make sure it is switched on, close to the panel and not connected to a phone.',
    not_connected: 'Not connected. Connect it first.',
    gate_failed: 'A safety check stopped this.',
    ota_disabled: 'Firmware updates are switched off on this panel.',
    update_failed: 'The update did not finish.',
    cancelled: 'Cancelled. Nothing was changed.',
    unsupported: 'That is not possible for this kind of device.',
    panel_unreachable: 'The panel did not respond. Is it on and online?',
    bad_request: 'That request was not accepted.',
    internal: 'Something went wrong on the panel.'
  };

  function errorText(err) {
    if (!err) return '';
    var base = ERROR_TEXT[err.code] || 'Something went wrong.';
    // the panel's own sentence is the most specific one; the generic line is the fallback
    return err.message ? String(err.message) : base;
  }

  /* one device row's state, from the panel view + the active job */
  function deviceState(dev, busy) {
    if (busy && busy.device_id === dev.id && (busy.state === 'queued' || busy.state === 'sent' || busy.state === 'running')) {
      var m = { connect: 'connecting', disconnect: 'disconnecting', forget: 'forgetting', update_start: 'updating', info: 'reading', update_check: 'checking' };
      if (m[busy.op]) return m[busy.op];
    }
    return dev.connected ? 'connected' : 'idle';
  }

  var STATE_LABEL = { connecting: 'Connecting...', disconnecting: 'Disconnecting...', forgetting: 'Forgetting...', updating: 'Updating',
    reading: 'Reading...', checking: 'Checking for updates...', connected: 'Connected', idle: 'Not connected' };

  /* the update checklist, as rows: ok / blocked, with the reason and the way out */
  function gateRows(gates) {
    return (gates || []).map(function (g) {
      return { id: g.id, ok: !!g.ok, label: g.label || g.id, detail: g.detail || '', hint: g.hint || '' };
    });
  }

  function canStartUpdate(check, ack) {
    if (!check || !check.available || !check.ready || !ack) return false;
    return true;
  }

  function stepIsLocked(step) { return step === 'install' || step === 'reset' || step === 'waiting' || step === 'confirm'; }

  /* ------------------------------------------------------------------ pure renderers */
  function bars(n) {
    var out = '<span class="zbt-bars" role="img" aria-label="Signal ' + n + ' of 4">';
    for (var i = 1; i <= 4; i++) out += '<i class="' + (i <= n ? 'on' : '') + '" style="height:' + (4 + i * 4) + 'px"></i>';
    return out + '</span>';
  }

  function btn(action, label, extra) {
    extra = extra || {};
    return '<button type="button" class="zbt-btn' + (extra.primary ? ' zbt-primary' : '') + (extra.danger ? ' zbt-danger' : '') + '" data-bt="' + esc(action) + '"' +
      (extra.dev ? ' data-dev="' + esc(extra.dev) + '"' : '') + (extra.panel ? ' data-panel="' + esc(extra.panel) + '"' : '') +
      (extra.disabled ? ' disabled' : '') + '>' + esc(label) + '</button>';
  }

  function renderPanelPicker(panels, selected) {
    if (!panels || !panels.length) {
      return '<div class="zbt-empty">No panel has a Bluetooth agent yet. Once it is switched on for a panel, that panel appears here.</div>';
    }
    var h = '<div class="zbt-panels" role="tablist" aria-label="Choose a panel">';
    panels.forEach(function (p) {
      var on = p.panel_id === selected;
      h += '<button type="button" role="tab" class="zbt-panelbtn' + (on ? ' on' : '') + (p.online ? '' : ' off') + '" data-bt="panel" data-panel="' + esc(p.panel_id) +
        '" aria-selected="' + on + '"' + (p.online ? '' : ' disabled') + '>' + esc(p.name || p.panel_id) +
        '<small>' + (p.online ? esc(p.location || 'online') : 'offline') + '</small></button>';
    });
    return h + '</div>';
  }

  function renderInfo(info) {
    if (!info) return '';
    var rows = [];
    if (info.model) rows.push(['Model', info.model]);
    if (info.firmware) rows.push(['Firmware', info.firmware]);
    if (info.hardware) rows.push(['Hardware', info.hardware]);
    if (typeof info.battery === 'number') rows.push(['Battery', info.battery + '%' + (info.charging === true ? ' (charging)' : '')]);
    else if (info.charging === true) rows.push(['Power', 'Charging']);
    if (info.manufacturer) rows.push(['Maker', info.manufacturer]);
    var sync = info.sync;
    if (sync && sync.configured) {
      if (sync.unreadable) rows.push(['Recording sync', 'status unavailable']);
      else rows.push(['Recording sync', sync.waiting_to_upload + ' waiting to upload, ' + sync.confirmed_ranges + ' stored']);
    }
    if (!rows.length) return '';
    return '<dl class="zbt-info">' + rows.map(function (r) { return '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>'; }).join('') + '</dl>';
  }

  function renderUpdate(dev, check, ui, busy) {
    var h = '<div class="zbt-update" data-for="' + esc(dev.id) + '">';
    var updating = busy && busy.device_id === dev.id && busy.op === 'update_start' && (busy.state === 'queued' || busy.state === 'sent' || busy.state === 'running');
    if (updating) {
      var p = busy.progress || {};
      var pct = Math.max(0, Math.min(100, p.pct || 0));
      h += '<div class="zbt-progress" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100"><div style="width:' + pct + '%"></div></div>';
      h += '<div class="zbt-step">' + esc(p.message || 'Starting...') + ' <b>' + pct + '%</b></div>';
      if (p.step === 'reset' || p.step === 'waiting' || p.step === 'confirm') {
        h += '<div class="zbt-callout zbt-warn">Do not move or unplug the pendant for 2 minutes.</div>';
      }
      if (!stepIsLocked(p.step)) h += btn('cancel-update', 'Cancel update', { dev: dev.id });
      else h += '<div class="zbt-note">It is installing now and cannot be stopped.</div>';
      return h + '</div>';
    }
    if (ui.updateResult && ui.updateResult.device_id === dev.id) {
      var r = ui.updateResult;
      if (r.state === 'succeeded') {
        h += '<div class="zbt-callout zbt-ok">Updated' + (r.result && r.result.to ? ' to ' + esc(r.result.to) : '') + (r.result && r.result.from ? ' (from ' + esc(r.result.from) + ')' : '') + '.</div>';
      } else {
        h += '<div class="zbt-callout zbt-bad"><b>' + (r.state === 'cancelled' ? 'Cancelled' : 'Update failed') + '</b><br>' + esc(errorText(r.error)) + '</div>';
        h += gateList(r.error && r.error.detail && r.error.detail.gates);
      }
      h += btn('dismiss-update', 'OK', { dev: dev.id });
      return h + '</div>';
    }
    if (!check) return h + '<div class="zbt-note">Checking for updates...</div></div>';
    if (check.supported === false) return h + '<div class="zbt-note">' + esc(check.reason || 'No update support for this device.') + '</div></div>';
    if (!check.available) {
      return h + '<div class="zbt-note">' + esc(check.reason || 'This device is up to date.') + (check.current ? ' (firmware ' + esc(check.current) + ')' : '') + '</div></div>';
    }
    h += '<div class="zbt-callout">Update available: <b>' + esc(check.current) + '</b> to <b>' + esc(check.latest) + '</b>' +
      (check.changelog ? '<br><small>' + esc(check.changelog) + '</small>' : '') + '</div>';
    h += '<div class="zbt-callout zbt-warn"><b>This cannot be undone.</b> ' + esc(check.warning || '') + '</div>';
    h += gateList(check.gates);
    h += '<div class="zbt-note">The signal is measured when you press Update (it needs to be better than ' + esc(check.min_rssi) +
      ' dBm and steady). Battery must be at least ' + esc(check.min_battery) + '%.</div>';
    h += '<label class="zbt-ack"><input type="checkbox" data-bt="ack" ' + (ui.ack ? 'checked' : '') + '> I understand it cannot be undone and the pendant stays on its charger</label>';
    h += btn('start-update', 'Update firmware', { dev: dev.id, primary: true, disabled: !canStartUpdate(check, ui.ack) });
    if (!check.ready) h += '<div class="zbt-note">Fix the items marked above first.</div>';
    return h + '</div>';
  }

  function gateList(gates) {
    var rows = gateRows(gates);
    if (!rows.length) return '';
    return '<ul class="zbt-gates">' + rows.map(function (g) {
      return '<li class="' + (g.ok ? 'ok' : 'no') + '"><span class="zbt-mark" aria-hidden="true">' + (g.ok ? '&#10003;' : '&#10007;') + '</span> ' + esc(g.label) +
        (g.ok ? '' : (g.detail ? '<small>' + esc(g.detail) + '</small>' : '') + (g.hint ? '<small class="hint">' + esc(g.hint) + '</small>' : '')) + '</li>';
    }).join('') + '</ul>';
  }

  /* the "Assigned to" control for an Omi pendant. Pure: ui.omi = { available, members:[{user_id,name}], devices:{id:{user_id,label,active}},
   * saving: id|null, error: '' }. No recordings, no counts - ownership only. */
  function renderOwner(dev, ui) {
    var o = ui.omi;
    if (!o || !o.available || dev.driver !== 'omi') return '';
    var reg = (o.devices && o.devices[dev.id]) || null;
    var cur = reg && reg.active && reg.user_id ? reg.user_id : '';
    var members = o.members || [];
    var known = members.some(function (m) { return m.user_id === cur; });
    var saving = o.saving === dev.id;
    var h = '<div class="zbt-owner" data-for="' + esc(dev.id) + '"><label class="zbt-ownerlabel" for="zbt-owner-' + esc(dev.id) + '">Assigned to</label>';
    h += '<select id="zbt-owner-' + esc(dev.id) + '" class="zbt-select" data-bt="assign" data-dev="' + esc(dev.id) + '"' + (saving ? ' disabled' : '') + '>';
    h += '<option value=""' + (cur ? '' : ' selected') + '>Nobody yet</option>';
    if (cur && !known) h += '<option value="' + esc(cur) + '" selected>Unknown member</option>';
    members.forEach(function (m) {
      h += '<option value="' + esc(m.user_id) + '"' + (m.user_id === cur ? ' selected' : '') + '>' + esc(m.name || m.user_id) + '</option>';
    });
    h += '</select>';
    if (saving) h += '<div class="zbt-note" role="status">Saving...</div>';
    if (o.error) h += '<div class="zbt-callout zbt-bad">' + esc(o.error) + '</div>';
    if (!cur) {
      h += '<div class="zbt-note">Nobody owns this pendant yet. It keeps recording, and the panel holds the audio until you choose someone.</div>';
    } else {
      h += '<div class="zbt-note">Its recordings sync to this person only, whoever is signed in. Changing this does not move recordings already saved; ' +
        'audio still waiting to sync goes to the new person, so let it finish syncing first.</div>';
    }
    return h + '</div>';
  }

  function renderDevice(dev, view, ui) {
    var busy = view.busy;
    var st = deviceState(dev, busy);
    var working = ['connecting', 'disconnecting', 'forgetting', 'updating', 'reading', 'checking'].indexOf(st) >= 0;
    var h = '<div class="zbt-row" data-state="' + esc(st) + '" data-dev="' + esc(dev.id) + '">';
    h += '<div class="zbt-head"><div class="zbt-title"><span class="zbt-name">' + esc(dev.name) + '</span><small>' + esc(dev.label || '') +
      (dev.remembered ? ' - remembered' : '') + '</small></div>';
    h += '<div class="zbt-side">' + (dev.in_range ? bars(rssiBars(dev.rssi)) + (typeof dev.rssi === 'number' ? '<small>' + esc(dev.rssi) + ' dBm</small>' : '') : '<small>not in range</small>') +
      '<span class="zbt-pill zbt-' + esc(st) + '">' + esc(STATE_LABEL[st] || st) + '</span></div></div>';
    h += renderOwner(dev, ui);
    var fail = ui.failures && ui.failures[dev.id];
    if (fail && !working) h += '<div class="zbt-callout zbt-bad"><b>Failed</b><br>' + esc(errorText(fail)) + '</div>';
    if (st === 'connected' || (st === 'updating')) h += renderInfo(ui.infos && ui.infos[dev.id]);
    if (ui.confirmForget === dev.id && !working) {
      h += '<div class="zbt-callout zbt-warn">Forget ' + esc(dev.name) + '? This panel will stop remembering it.</div><div class="zbt-actions">' +
        btn('forget-yes', 'Yes, forget', { dev: dev.id, danger: true }) + btn('forget-no', 'Keep', { dev: dev.id }) + '</div>';
    } else {
      h += '<div class="zbt-actions">';
      if (st === 'connected') {
        h += btn('disconnect', 'Disconnect', { dev: dev.id, disabled: working });
        if (dev.supports_update) h += btn('update', ui.expanded === dev.id ? 'Hide update' : 'Update', { dev: dev.id, disabled: working });
      } else {
        h += btn('connect', 'Connect', { dev: dev.id, primary: true, disabled: working || (!dev.in_range && !dev.remembered) });
      }
      if (dev.remembered) h += btn('forget', 'Forget', { dev: dev.id, disabled: working });
      h += '</div>';
    }
    if (dev.supports_update && ui.expanded === dev.id && (st === 'connected' || st === 'updating' || st === 'checking' || ui.updateResult)) {
      h += renderUpdate(dev, ui.checks && ui.checks[dev.id], ui, busy);
    }
    return h + '</div>';
  }

  function renderBody(ui) {
    var h = '';
    if (ui.error) h += '<div class="zbt-callout zbt-bad">' + esc(ui.error) + '</div>';
    h += renderPanelPicker(ui.panels, ui.panelId);
    var v = ui.view;
    if (!ui.panelId || !v) return h;
    var scanning = v.busy && v.busy.op === 'scan' && (v.busy.state === 'queued' || v.busy.state === 'sent' || v.busy.state === 'running');
    var anyBusy = !!v.busy;
    h += '<div class="zbt-toolbar">' + btn('scan', scanning ? 'Scanning...' : 'Scan for devices', { primary: true, disabled: anyBusy }) +
      (v.radio_holder ? '<span class="zbt-note">' + (v.radio_holder === 'omi-sync' ? 'The pendant is syncing recordings.' : 'Bluetooth is in use.') + '</span>' : '') + '</div>';
    if (scanning) h += '<div class="zbt-scanning" role="status"><span class="zbt-spin"></span> Looking for nearby devices... about 10 seconds</div>';
    if (!v.devices.length && !scanning) {
      h += '<div class="zbt-empty">' + (v.scan ? 'Nothing found. Move the device closer and scan again.' : 'Press Scan to look for devices near this panel.') + '</div>';
    }
    v.devices.forEach(function (d) { h += renderDevice(d, v, ui); });
    if (v.scan && v.scan.hidden_unnamed) h += '<div class="zbt-note">' + esc(v.scan.hidden_unnamed) + ' unnamed devices nearby are hidden.</div>';
    return h;
  }

  var CSS = '.zbt{font:inherit;color:inherit}.zbt *{box-sizing:border-box}' +
    '.zbt-panels{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}' +
    '.zbt-panelbtn,.zbt-btn{font:inherit;border:1px solid var(--border,#c9ced6);background:var(--surface,#fff);color:inherit;border-radius:10px;padding:8px 14px;cursor:pointer;min-height:40px}' +
    '.zbt-panelbtn small{display:block;font-size:.75em;opacity:.7}.zbt-panelbtn.on{border-color:var(--accent,#7b61ff);box-shadow:0 0 0 2px rgba(123,97,255,.25)}.zbt-panelbtn.off{opacity:.5;cursor:default}' +
    '.zbt-btn.zbt-primary{background:var(--accent,#7b61ff);border-color:var(--accent,#7b61ff);color:#fff}.zbt-btn.zbt-danger{background:#c0392b;border-color:#c0392b;color:#fff}.zbt-btn[disabled]{opacity:.45;cursor:default}' +
    '.zbt-toolbar{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}' +
    '.zbt-row{border:1px solid var(--border,#d7dbe2);border-radius:12px;padding:12px;margin-bottom:10px;background:var(--surface,#fff)}' +
    '.zbt-row[data-state=connected]{border-color:#2e9e5b}.zbt-row[data-state=updating]{border-color:var(--accent,#7b61ff)}' +
    '.zbt-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.zbt-title{min-width:0}.zbt-name{font-weight:600;display:block;overflow-wrap:anywhere}' +
    '.zbt small{opacity:.7;display:block}.zbt-side{display:flex;align-items:center;gap:8px;flex-wrap:wrap}' +
    '.zbt-bars{display:inline-flex;align-items:flex-end;gap:2px;height:20px}.zbt-bars i{display:block;width:5px;background:currentColor;opacity:.2;border-radius:1px}.zbt-bars i.on{opacity:.9}' +
    '.zbt-pill{font-size:.8em;border-radius:999px;padding:3px 10px;background:rgba(127,127,127,.15)}.zbt-connected{background:rgba(46,158,91,.18)}.zbt-connecting,.zbt-updating,.zbt-disconnecting,.zbt-forgetting,.zbt-reading,.zbt-checking{background:rgba(123,97,255,.2)}' +
    '.zbt-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.zbt-info{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px 14px;margin:10px 0 0}.zbt-info div{min-width:0}.zbt-info dt{font-size:.75em;opacity:.65}.zbt-info dd{margin:0;overflow-wrap:anywhere}' +
    '.zbt-callout{margin:10px 0 0;padding:10px 12px;border-radius:10px;background:rgba(127,127,127,.12);overflow-wrap:anywhere}.zbt-warn{background:rgba(230,160,30,.18)}.zbt-bad{background:rgba(192,57,43,.14)}.zbt-ok{background:rgba(46,158,91,.16)}' +
    '.zbt-note,.zbt-empty,.zbt-scanning{margin:10px 0;opacity:.8;font-size:.92em}.zbt-empty{padding:16px;text-align:center;border:1px dashed var(--border,#c9ced6);border-radius:12px}' +
    '.zbt-gates{list-style:none;margin:10px 0 0;padding:0}.zbt-gates li{padding:4px 0}.zbt-gates .ok .zbt-mark{color:#2e9e5b}.zbt-gates .no .zbt-mark{color:#c0392b}.zbt-gates small.hint{font-weight:500}' +
    '.zbt-owner{margin:10px 0 0}.zbt-ownerlabel{display:block;font-size:.8em;opacity:.75;margin-bottom:4px}' +
    '.zbt-select{font:inherit;color:inherit;background:var(--surface,#fff);border:1px solid var(--border,#c9ced6);border-radius:10px;padding:8px 12px;min-height:40px;max-width:100%}' +
    '.zbt-ack{display:flex;gap:10px;align-items:flex-start;margin:12px 0;cursor:pointer}.zbt-ack input{width:24px;height:24px;flex:none;margin-top:2px}' +
    '.zbt-progress{height:14px;border-radius:7px;background:rgba(127,127,127,.2);overflow:hidden;margin:10px 0 6px}.zbt-progress div{height:100%;background:var(--accent,#7b61ff);transition:width .4s}' +
    '.zbt-spin{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;vertical-align:-2px;animation:zbtspin .8s linear infinite}@keyframes zbtspin{to{transform:rotate(360deg)}}' +
    '@media (prefers-reduced-motion:reduce){.zbt-spin{animation:none}.zbt-progress div{transition:none}}' +
    /* touch: finger targets are 48 px or more, text is larger, rows breathe */
    '.zbt[data-surface=touch] .zbt-btn,.zbt[data-surface=touch] .zbt-panelbtn{min-height:48px;min-width:48px;font-size:16px;padding:10px 18px}' +
    '.zbt[data-surface=touch] .zbt-select{min-height:48px;font-size:16px;padding:10px 14px}' +
    '.zbt[data-surface=touch] .zbt-ack{min-height:48px;align-items:center;font-size:15px}.zbt[data-surface=touch] .zbt-ack input{width:32px;height:32px}' +
    '.zbt[data-surface=touch] .zbt-row{padding:14px}.zbt[data-surface=touch] .zbt-actions{gap:12px}';

  /* ------------------------------------------------------------------ controller */
  function mount(rootEl, opts) {
    opts = opts || {};
    var surface = opts.surface === 'touch' ? 'touch' : 'desktop';
    var call = opts.fetchJson;
    var setT = opts.setTimeout || (typeof setTimeout !== 'undefined' ? setTimeout.bind(null) : null);
    var clearT = opts.clearTimeout || (typeof clearTimeout !== 'undefined' ? clearTimeout.bind(null) : null);
    var storage = opts.storage || null;
    var ui = { panels: [], panelId: null, view: null, error: '', infos: {}, checks: {}, failures: {}, expanded: null, ack: false,
      confirmForget: null, updateResult: null, omi: { available: null, members: [], devices: {}, saving: null, error: '', loadedFor: null } };
    var timer = null, dead = false, jobId = null, available = null;

    var lastHtml = null;
    function paint() {
      if (dead) return;
      var html = '<div class="zbt" data-surface="' + surface + '"><style>' + CSS + '</style>' + renderBody(ui) + '</div>';
      if (html === lastHtml) return;                 // a poll that changed nothing must not rebuild the DOM (it would drop focus and scroll)
      lastHtml = html;
      rootEl.innerHTML = html;
    }
    function fail(msg) { ui.error = msg; paint(); }
    function schedule(ms) {
      if (dead) return;
      if (timer !== null) clearT(timer);
      timer = setT(tick, ms);
    }
    function savedPanel() { try { return storage && storage.getItem('zoe_bt_panel'); } catch (e) { return null; } }
    function savePanel(id) { try { if (storage) storage.setItem('zoe_bt_panel', id); } catch (e) { /* private mode: fine */ } }

    function api(method, url, body) {
      return Promise.resolve(call(method, url, body));
    }

    function pickDefaultPanel() {
      var want = ui.panelId || savedPanel();
      var online = ui.panels.filter(function (p) { return p.online; });
      var hit = ui.panels.filter(function (p) { return p.panel_id === want && p.online; })[0];
      ui.panelId = hit ? hit.panel_id : (online[0] ? online[0].panel_id : null);
    }

    function loadPanels() {
      return api('GET', '/api/bluetooth/panels').then(function (r) {
        ui.panels = (r && r.panels) || [];
        pickDefaultPanel();
      });
    }

    function hasOmi() {
      return !!(ui.view && (ui.view.devices || []).some(function (d) { return d.driver === 'omi'; }));
    }

    /* ownership of Omi pendants: loaded once an Omi device is on screen, reloaded after a change. A 404 (registry flag off) or 403 means
     * "no such control here": available=false and nothing is rendered. Never touches a recording. */
    function loadOmi(force) {
      var o = ui.omi;
      if (o.available === false || (!force && o.loadedFor !== null) || !hasOmi()) return Promise.resolve();
      o.loadedFor = ui.panelId;
      return Promise.all([api('GET', '/api/omi/members'), api('GET', '/api/omi/devices')]).then(function (rs) {
        o.members = (rs[0] && rs[0].members) || [];
        o.devices = {};
        ((rs[1] && rs[1].devices) || []).forEach(function (d) { o.devices[d.device_id] = d; });
        o.available = true;
      }, function (e) {
        if (e && (e.status === 404 || e.status === 403 || e.status === 401)) { o.available = false; return; }
        o.loadedFor = null;             // transient: try again on the next poll
      });
    }

    function saveOwner(dev, uid) {
      var o = ui.omi;
      if (uid && !o.members.some(function (m) { return m.user_id === uid; })) return Promise.resolve();   // only a listed household member
      o.saving = dev; o.error = ''; paint();
      var req = uid ? api('PUT', '/api/omi/devices/' + dev, { user_id: uid }) : api('DELETE', '/api/omi/devices/' + dev);
      return req.then(function () { o.saving = null; return loadOmi(true); }, function (e) {
        o.saving = null; o.error = (e && e.message) || 'Could not save that change.';
        return loadOmi(true);
      }).then(paint);
    }

    function loadView() {
      if (!ui.panelId) { ui.view = null; return Promise.resolve(); }
      return api('GET', '/api/bluetooth/panels/' + encodeURIComponent(ui.panelId)).then(function (v) {
        ui.view = v;
        var busy = v && v.busy;
        jobId = busy ? busy.job_id : null;
      }).then(function () { return loadOmi(false); });
    }

    function finishJob(job) {
      /* a job just ended: fold its result into the UI state */
      var dev = job.device_id;
      if (job.state === 'failed' || job.state === 'cancelled') {
        if (job.op === 'update_start' && dev) { ui.updateResult = { device_id: dev, state: job.state, error: job.error, result: null }; ui.expanded = dev; }
        else if (dev) ui.failures[dev] = job.error || { code: 'internal' };
        else if (job.op === 'scan') ui.error = errorText(job.error);
        return;
      }
      if (dev) delete ui.failures[dev];
      if (job.op === 'connect' || job.op === 'info') { if (job.result && job.result.info) ui.infos[dev] = job.result.info; }
      if (job.op === 'disconnect') { delete ui.infos[dev]; delete ui.checks[dev]; if (ui.expanded === dev) ui.expanded = null; }
      if (job.op === 'forget') { delete ui.infos[dev]; delete ui.checks[dev]; if (ui.expanded === dev) ui.expanded = null; ui.confirmForget = null; }
      if (job.op === 'update_check') { ui.checks[dev] = job.result || null; }
      if (job.op === 'update_start') { ui.updateResult = { device_id: dev, state: 'succeeded', result: job.result }; ui.expanded = dev; delete ui.checks[dev]; }
      if (job.op === 'scan') ui.error = '';
    }

    function pollJob(id) {
      return api('GET', '/api/bluetooth/panels/' + encodeURIComponent(ui.panelId) + '/jobs/' + encodeURIComponent(id)).then(function (job) {
        if (ui.view && ui.view.busy && ui.view.busy.job_id === id) ui.view.busy = job;
        if (job.state === 'succeeded' || job.state === 'failed' || job.state === 'cancelled') {
          jobId = null;
          finishJob(job);
          return loadView();
        }
      });
    }

    function tick() {
      timer = null;
      if (dead) return;
      var step = jobId ? pollJob(jobId) : loadPanels().then(loadView);
      step.then(function () { ui.error = ''; paint(); schedule(jobId ? POLL_JOB_MS : POLL_IDLE_MS); },
        function (e) { handleErr(e); schedule(POLL_IDLE_MS * 2); });
    }

    function handleErr(e) {
      var s = e && e.status;
      if (s === 401 || s === 403) fail('Sign in as the household admin to manage Bluetooth devices.');
      else if (s === 404 && available === false) { /* flag off */ }
      else fail((e && e.message) || 'Could not reach Zoe.');
    }

    function enqueue(path, body) {
      ui.error = '';
      return api('POST', '/api/bluetooth/panels/' + encodeURIComponent(ui.panelId) + path, body || {}).then(function (job) {
        jobId = job.job_id;
        ui.view = ui.view || { devices: [] };
        ui.view.busy = job;
        paint();
        schedule(300);
        return job;
      }, function (e) { handleErr(e); throw e; });
    }

    function onClick(ev) {
      var el = ev.target && ev.target.closest ? ev.target.closest('[data-bt]') : null;
      if (!el || el.disabled) return;
      var act = el.dataset.bt;
      if ((act === 'ack' || act === 'assign') !== (ev.type === 'change')) return;          // checkbox + select act on 'change', every button on 'click'
      var dev = el.dataset.dev;
      if (dev && !DEVICE_ID.test(dev)) return;
      var safe = function (p) { return p && p.catch ? p.catch(function () {}) : p; };
      if (act === 'panel') {
        if (!PANEL_ID.test(el.dataset.panel || '')) return;
        ui.panelId = el.dataset.panel; savePanel(ui.panelId); ui.view = null; ui.infos = {}; ui.checks = {}; ui.failures = {}; ui.expanded = null;
        if (ui.omi.available !== false) ui.omi.loadedFor = null;
        paint(); safe(loadView().then(paint, handleErr)); return;
      }
      if (!ui.panelId) return;
      if (act === 'scan') { ui.failures = {}; safe(enqueue('/scan')); }
      else if (act === 'connect') { delete ui.failures[dev]; ui.updateResult = null; safe(enqueue('/devices/' + dev + '/connect')); }
      else if (act === 'disconnect') safe(enqueue('/devices/' + dev + '/disconnect'));
      else if (act === 'forget') { ui.confirmForget = dev; paint(); }
      else if (act === 'forget-no') { ui.confirmForget = null; paint(); }
      else if (act === 'forget-yes') { ui.confirmForget = null; safe(enqueue('/devices/' + dev + '/forget')); }
      else if (act === 'update') {
        if (ui.expanded === dev) { ui.expanded = null; paint(); return; }
        ui.expanded = dev; ui.ack = false; ui.updateResult = null; ui.checks[dev] = null; paint();
        safe(enqueue('/devices/' + dev + '/update_check'));
      }
      else if (act === 'ack') { ui.ack = !!(el.checked); paint(); }
      else if (act === 'assign') safe(saveOwner(dev, String(el.value || '')));
      else if (act === 'start-update') {
        if (!canStartUpdate(ui.checks[dev], ui.ack)) return;
        ui.updateResult = null;
        safe(enqueue('/devices/' + dev + '/update_start', { confirm: true, ack_no_undo: true }));
        ui.ack = false;
      }
      else if (act === 'cancel-update') safe(enqueue('/devices/' + dev + '/update_cancel'));
      else if (act === 'dismiss-update') { ui.updateResult = null; paint(); }
    }

    rootEl.addEventListener('click', onClick);
    rootEl.addEventListener('change', onClick);

    /* first load: a 404 on the very first call means the flag is off - tell the host page and render nothing */
    var readyCalled = false;
    function ready() { if (!readyCalled) { readyCalled = true; if (opts.onReady) opts.onReady(); } }
    api('GET', '/api/bluetooth/config').then(function () {
      available = true;
      return loadPanels().then(loadView);
    }).then(function () { ready(); paint(); schedule(POLL_IDLE_MS); }, function (e) {
      if (e && e.status === 404) { available = false; dead = true; if (opts.onUnavailable) opts.onUnavailable(); return; }
      available = true;
      ready();                                     // 401/403/network: the section is real, it just says why it cannot load yet
      handleErr(e);
      schedule(POLL_IDLE_MS * 2);
    });

    return {
      destroy: function () { dead = true; if (timer !== null) clearT(timer); rootEl.removeEventListener('click', onClick); rootEl.removeEventListener('change', onClick); },
      state: function () { return ui; },
      refresh: function () { if (timer !== null) clearT(timer); tick(); }
    };
  }

  return { esc: esc, rssiBars: rssiBars, errorText: errorText, deviceState: deviceState, gateRows: gateRows, canStartUpdate: canStartUpdate,
    renderBody: renderBody, renderDevice: renderDevice, renderOwner: renderOwner, renderUpdate: renderUpdate, renderPanelPicker: renderPanelPicker, renderInfo: renderInfo,
    mount: mount, CSS: CSS };
});
