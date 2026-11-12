/* Settings > My pendant - ONE component shared by the desktop Settings page and the touch estate's Settings page (the member's OWN Omi
 * pendant: linked or not, last sync, Link / Unlink, and the per-member pendant settings). Record: docs/knowledge/omi-linking.md.
 *
 *   ZoeOmiPendant.mount(rootEl, { surface: 'desktop'|'touch', fetchJson(method, url, body) -> Promise<json>, panelId() -> string|null,
 *                                 onReady(), onUnavailable() })
 *
 * GET /api/omi/my-pendant is 404 while pendant sync is off (ZOE_OMI_SYNC_ENABLED): mount() calls onUnavailable() and renders nothing.
 * With linking off (ZOE_OMI_DEVICE_OWNERS) it says so in one plain sentence ("ask the household admin to turn on pendant linking").
 * Link is a TOUCH-only button (the panel the member is standing at; it needs their PIN sign-in there and the pendant in their hand); the
 * desktop says to link at the panel or by voice. Nothing is ever typed: keep-audio days are a row of buttons capped at the household
 * ceiling. A setting the household has switched off shows as off and cannot be turned on here (a member can only narrow).
 * Everything from the server goes through esc(). No backward-compat risk: a NEW file defining one global (ZoeOmiPendant).
 * Tested without a browser by dist/test_omi_pendant_settings.js (pure half + controller with an injected fetch / DOM).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ZoeOmiPendant = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var HANDLE = /^[0-9a-f]{24}$/;      // an opaque, member-scoped name for one of MY pendants (never a device id)
  var DAY_CHOICES = [1, 3, 7, 14, 30, 90];
  var POLL_MS = 2000;

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function ago(iso, now) {
    var t = Date.parse(iso || '');
    if (!isFinite(t)) return 'not yet';
    var mins = Math.max(0, Math.floor(((now || Date.now()) - t) / 60000));
    if (mins < 2) return 'just now';
    if (mins < 90) return mins + ' minutes ago';
    var h = Math.round(mins / 60);
    return h < 36 ? h + ' hours ago' : Math.round(h / 24) + ' days ago';
  }

  /* The switches, in the order the page lists them: [setting key, operator key, label, what "on" means]. `invert` = the stored
   * value is the opposite of the toggle (pause). */
  var SWITCHES = [
    { key: 'sync_paused', op: 'sync', label: 'Sync my recordings', note: 'Off holds them on the pendant until you turn it back on.', invert: true },
    { key: 'speaker_tag_enabled', op: 'speaker_tag', label: 'Tag voices on my recordings', note: 'Split who is speaking in each recording.' },
    { key: 'learning_enabled', op: 'learn_voices', label: 'Learn the voices I name', note: 'Zoe suggests a name next time she hears that voice.' },
    { key: 'day_review_enabled', op: 'day_review', label: 'Offer me a day review', note: 'Things from your day you might want to keep.' }
  ];

  function dayChoices(ceiling) {
    var max = Math.max(1, Math.floor(Number(ceiling) || 1));
    var out = DAY_CHOICES.filter(function (d) { return d < max; });
    out.push(max);
    return out;
  }

  /* ------------------------------------------------------------------ render (pure) */
  function renderSwitch(sw, view, surface) {
    var op = !!(view.operator || {})[sw.op];
    var stored = !!(view.settings || {})[sw.key];
    var on = sw.invert ? !stored : stored;
    var shown = op && on;
    var big = surface === 'touch' ? ' style="min-height:48px;min-width:96px"' : '';
    return '<div class="op-row" data-switch="' + esc(sw.key) + '">' +
      '<div class="op-row-text"><div class="op-row-label">' + esc(sw.label) + '</div><div class="op-row-sub">' +
      esc(op ? sw.note : 'Off for the household.') + '</div></div>' +
      '<button type="button" class="op-toggle' + (shown ? ' on' : '') + '" data-action="toggle" data-key="' + esc(sw.key) + '"' +
      (op ? '' : ' disabled') + ' aria-pressed="' + (shown ? 'true' : 'false') + '"' + big + '>' + (shown ? 'On' : 'Off') + '</button></div>';
  }

  function renderDays(view, surface) {
    var ceiling = (view.operator || {}).keep_audio_days_max;
    var mine = (view.settings || {}).keep_audio_days;
    var big = surface === 'touch' ? ' style="min-height:48px;min-width:64px"' : '';
    var btns = '<button type="button" class="op-day' + (mine === null || mine === undefined ? ' on' : '') +
      '" data-action="days" data-days=""' + big + '>Household (' + esc(ceiling) + ')</button>';
    dayChoices(ceiling).forEach(function (d) {
      btns += '<button type="button" class="op-day' + (mine === d ? ' on' : '') + '" data-action="days" data-days="' + d + '"' + big +
        '>' + d + (d === 1 ? ' day' : ' days') + '</button>';
    });
    return '<div class="op-row op-days"><div class="op-row-text"><div class="op-row-label">Keep audio</div><div class="op-row-sub">' +
      'How long the recording audio is kept. Never longer than the household allows.</div></div><div class="op-day-row">' + btns +
      '</div></div>';
  }

  function renderPendants(view, surface) {
    var pend = view.pendants || [];
    var big = surface === 'touch' ? ' style="min-height:48px"' : '';
    if (!pend.length) return '<div class="op-state" data-state="unlinked">No pendant linked to you yet.</div>';
    return pend.map(function (p) {
      var id = HANDLE.test(p.handle || '') ? p.handle : '';
      return '<div class="op-state" data-state="linked"><div class="op-row-label">' + esc(p.label || 'My pendant') + '</div>' +
        '<div class="op-row-sub">Linked. Last synced ' + esc(ago(p.last_sync_at)) + '.</div>' +
        (id ? '<button type="button" class="op-unlink" data-action="unlink" data-handle="' + id + '"' + big + '>Unlink</button>' : '') +
        '</div>';
    }).join('');
  }

  function renderLink(view, surface, panelId) {
    var w = view.window;
    if (w && w.state === 'searching') {
      return '<div class="op-window" data-state="searching">Hold your pendant right next to the panel...</div>';
    }
    var done = w && w.state !== 'searching' ? '<div class="op-window" data-state="' + esc(w.state) + '">' +
      esc(w.state === 'linked' || w.state === 'already' ? 'Your pendant is linked.' : 'That did not link. Try again with the pendant in your hand.') +
      '</div>' : '';
    if (surface !== 'touch' || !panelId) {
      return done + '<div class="op-hint">To link a pendant, stand at the wall panel with it in your hand and say "Zoe, link my pendant", ' +
        'or open My pendant on the panel.</div>';
    }
    return done + '<button type="button" class="op-link" data-action="link" style="min-height:48px">Link a pendant</button>';
  }

  function render(view, opts) {
    opts = opts || {};
    var surface = opts.surface === 'touch' ? 'touch' : 'desktop';
    if (!view) return '';
    var link = view.linking || {};
    var html = '';
    if (!link.enabled) {
      html += '<div class="op-off">' + esc(link.message || 'Pendant linking is not turned on here yet. Ask the household admin.') + '</div>';
    } else {
      html += renderPendants(view, surface) + renderLink(view, surface, opts.panelId);
    }
    if (opts.notice) {
      html += '<div class="op-notice" role="dialog"><div>' + esc(view.notice) + '</div>' +
        '<button type="button" data-action="notice-ok" style="min-height:48px">I understand, link it</button>' +
        '<button type="button" data-action="notice-cancel" style="min-height:48px">Not now</button></div>';
    }
    if (opts.confirmUnlink && opts.pendingWarning) {
      /* the server said unsynced audio is still on it: a SECOND, explicit tap unlinks anyway */
      html += '<div class="op-confirm" data-step="pending" role="dialog"><div>' + esc(opts.pendingWarning) + '</div>' +
        '<button type="button" data-action="unlink-force" style="min-height:48px">Unlink anyway</button>' +
        '<button type="button" data-action="unlink-cancel" style="min-height:48px">Keep it</button></div>';
    } else if (opts.confirmUnlink) {
      html += '<div class="op-confirm" role="dialog"><div>Anything still on the pendant will go to whoever links it next, so let it ' +
        'finish syncing first.</div><button type="button" data-action="unlink-ok" style="min-height:48px">Unlink</button>' +
        '<button type="button" data-action="unlink-cancel" style="min-height:48px">Keep it</button></div>';
    }
    if (opts.message) html += '<div class="op-msg">' + esc(opts.message) + '</div>';
    html += '<div class="op-settings">' + SWITCHES.map(function (sw) { return renderSwitch(sw, view, surface); }).join('') +
      renderDays(view, surface) + '</div>';
    return html;
  }

  /* What a toggle tap sends: the NEW stored value for that key (pause is inverted). Null = nothing (the household has it off). */
  function toggleBody(view, key) {
    var sw = SWITCHES.filter(function (s) { return s.key === key; })[0];
    if (!sw || !(view.operator || {})[sw.op]) return null;
    var body = {};
    body[key] = !(view.settings || {})[key];
    return body;
  }

  function daysBody(view, raw) {
    if (raw === '' || raw === null || raw === undefined) return { keep_audio_default: true };
    var d = Math.floor(Number(raw));
    var max = Math.floor(Number((view.operator || {}).keep_audio_days_max) || 1);
    if (!isFinite(d) || d < 1) return null;
    return { keep_audio_days: Math.min(d, max) };
  }

  /* ------------------------------------------------------------------ controller */
  function mount(rootEl, cfg) {
    cfg = cfg || {};
    var surface = cfg.surface === 'touch' ? 'touch' : 'desktop';
    var timers = cfg.timers || { set: function (f, ms) { return setTimeout(f, ms); }, clear: function (t) { clearTimeout(t); } };
    var state = { view: null, notice: false, confirmUnlink: '', pendingWarning: '', message: '', poll: null };

    function panelId() { try { return cfg.panelId ? cfg.panelId() : null; } catch (e) { return null; } }
    function paint() {
      rootEl.innerHTML = render(state.view, { surface: surface, panelId: panelId(), notice: state.notice,
                                             confirmUnlink: !!state.confirmUnlink, pendingWarning: state.pendingWarning,
                                             message: state.message });
    }
    function fail(e) { state.message = (e && e.message) || 'That did not work. Try again.'; paint(); }
    function load() {
      return cfg.fetchJson('GET', '/api/omi/my-pendant').then(function (v) {
        state.view = v;
        paint();
        if (cfg.onReady) cfg.onReady();
        if (v.window && v.window.state === 'searching') schedule();
        return v;
      }, function (e) {
        if (!state.view && e && (e.status === 404 || e.status === 403 || e.status === 401)) {
          rootEl.innerHTML = '';
          if (cfg.onUnavailable) cfg.onUnavailable();
          return null;
        }
        fail(e);
        return null;
      });
    }
    function schedule() {
      if (state.poll) timers.clear(state.poll);
      state.poll = timers.set(function () { state.poll = null; load(); }, POLL_MS);
    }
    function startLink(ack) {
      var pid = panelId();
      if (!pid) return Promise.resolve(null);
      state.notice = false;
      state.message = '';
      return cfg.fetchJson('POST', '/api/omi/my-pendant/link', { panel_id: pid, notice_ack: !!ack }).then(function () {
        return load();
      }, function (e) {
        if (e && e.status === 428) { state.notice = true; paint(); return null; }
        fail(e);
        return null;
      });
    }
    function put(body) {
      if (!body) return Promise.resolve(null);
      return cfg.fetchJson('PUT', '/api/omi/voices/settings', body).then(function () { return load(); }, fail);
    }
    function onClick(ev) {
      var t = ev && ev.target;
      while (t && t !== rootEl && !(t.getAttribute && t.getAttribute('data-action'))) t = t.parentNode;
      if (!t || t === rootEl || !t.getAttribute) return null;
      var act = t.getAttribute('data-action');
      var v = state.view || {};
      if (act === 'toggle') return put(toggleBody(v, t.getAttribute('data-key')));
      if (act === 'days') return put(daysBody(v, t.getAttribute('data-days')));
      if (act === 'link') return startLink(!!v.notice_ack_at);
      if (act === 'notice-ok') return startLink(true);
      if (act === 'notice-cancel') { state.notice = false; paint(); return null; }
      if (act === 'unlink') {
        var hd = t.getAttribute('data-handle') || '';
        if (!HANDLE.test(hd)) return null;
        state.confirmUnlink = hd;
        state.pendingWarning = '';
        paint();
        return null;
      }
      if (act === 'unlink-cancel') { state.confirmUnlink = ''; state.pendingWarning = ''; paint(); return null; }
      if (act === 'unlink-ok' || act === 'unlink-force') {
        var d = state.confirmUnlink;
        var force = act === 'unlink-force' && !!state.pendingWarning;     // only right after the server's warning
        if (!HANDLE.test(d) || (act === 'unlink-force' && !force)) return null;
        state.confirmUnlink = '';
        state.pendingWarning = '';
        return cfg.fetchJson('DELETE', '/api/omi/my-pendant/' + d + (force ? '?confirm=true' : '')).then(function (r) {
          state.message = (r && r.note) || '';
          return load();
        }, function (e) {
          if (e && e.status === 409 && !force) {             // unsynced audio is known to be on it: show the warning, ask again
            state.confirmUnlink = d;
            state.pendingWarning = (e && e.message) || 'Your pendant still has recordings that have not synced. If you unlink it now, ' +
              'they will go to whoever links it next.';
            paint();
            return null;
          }
          fail(e);
          return null;
        });
      }
      return null;
    }
    if (rootEl.addEventListener) rootEl.addEventListener('click', onClick);
    var ready = load();
    return { reload: load, click: onClick, state: function () { return state; }, ready: ready };
  }

  return { esc: esc, ago: ago, render: render, toggleBody: toggleBody, daysBody: daysBody, dayChoices: dayChoices, SWITCHES: SWITCHES,
           mount: mount };
});
