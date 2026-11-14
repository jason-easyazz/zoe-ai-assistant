/* Settings > Telegram > "Reminders on my phone" - the member's OWN opt-in for phone delivery of due reminders (desktop Settings only;
 * the panel never shows it). Record: docs/knowledge/reminder-phone-delivery.md (server: services/zoe-data/reminder_delivery.py).
 *
 *   ZoeReminderPhone.mount(rootEl, { fetchJson(method, url, body) -> Promise<json> (rejects with err.status) })
 *
 * GET  /api/user/profile/reminder-delivery -> { channels, full_text, telegram_linked, mode }   (own profile)
 * PUT  /api/user/profile/reminder-delivery    { channels, full_text }  -> the stored document;  403 for anyone not an adult member.
 *
 * Shown only when telegram_linked. The GET does not say whether the member is an adult, so a 403 on the PUT reverts the control and
 * replaces the section with one plain sentence. The toggle adds / removes "telegram" only; any other channel the member already has
 * (web_push, set by the phone's subscribe prompt) is kept as it is. Nothing says "Saved" until the server answered; a failed save
 * reverts to the last stored state. Only fixed strings are rendered (never a server message). One global: ZoeReminderPhone.
 * Tested without a browser by dist/test_reminder_phone_settings.js.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ZoeReminderPhone = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var API = '/api/user/profile/reminder-delivery';
  var REFUSED = 'Sending reminders to a phone is for adult members of the household.';
  var FAILED = 'That change did not save. The setting shown is the one Zoe has.';

  function channels(view) { return (view && Array.isArray(view.channels)) ? view.channels.slice() : []; }
  function sends(view) { return channels(view).indexOf('telegram') !== -1; }

  /* The PUT body for one control change, from the last STORED view. */
  function putBody(view, ctl, checked) {
    var ch = channels(view).filter(function (c) { return c !== 'telegram'; });
    if (ctl === 'send') {
      if (checked) ch.push('telegram');
      return { channels: ch, full_text: !!(view && view.full_text === true) };
    }
    if (ctl === 'full') return { channels: channels(view), full_text: !!checked };
    return null;
  }

  /* ------------------------------------------------------------------ render (pure) */
  function render(st) {
    if (st.refused) return '<div class="card"><div class="row"><div class="row-info"><div class="row-sub">' + REFUSED + '</div></div></div></div>';
    var v = st.view;
    if (!v || v.telegram_linked !== true) return '';
    var dis = st.busy ? ' disabled' : '';
    var h = '<div class="card"><div class="card-header">Reminders on my phone</div>' +
      '<div class="row"><div class="row-info"><div class="row-label">Send my reminders to my phone</div>' +
      '<div class="row-sub">Your reminders also arrive in Telegram when they are due (not in your quiet hours).</div></div>' +
      '<label class="toggle"><input type="checkbox" data-ctl="send"' + (sends(v) ? ' checked' : '') + dis + '>' +
      '<div class="toggle-track"></div></label></div>' +
      '<div class="row"><div class="row-info"><div class="row-label">Show the full text of private reminders on my phone</div>' +
      '<div class="row-sub">Otherwise a private reminder arrives as "You have a reminder."</div></div>' +
      '<label class="toggle"><input type="checkbox" data-ctl="full"' + (v.full_text === true ? ' checked' : '') + dis + '>' +
      '<div class="toggle-track"></div></label></div>';
    if (v.mode !== 'on') {
      h += '<div class="row"><div class="row-info"><div class="row-sub">Phone reminders are not switched on for this household yet. ' +
        'Your choice is kept for when they are.</div></div></div>';
    }
    if (st.message) h += '<div class="row"><div class="row-info"><div class="row-sub" data-msg="1">' + st.message + '</div></div></div>';
    return h + '</div>';
  }

  /* ------------------------------------------------------------------ controller */
  function mount(rootEl, cfg) {
    var state = { view: null, busy: false, refused: false, message: '' };

    function paint() { rootEl.innerHTML = render(state); }
    function load() {
      return cfg.fetchJson('GET', API).then(function (v) {
        state.view = v || null;
        paint();
        return v;
      }, function () {                // cannot read it: keep the last stored state, or show nothing rather than an unbacked control
        paint();
        return null;
      });
    }
    function change(ev) {
      var t = ev && ev.target;
      var ctl = t && t.getAttribute ? t.getAttribute('data-ctl') : null;
      if (!ctl) return Promise.resolve(null);
      var shown = !!(state.view && state.view.telegram_linked === true && !state.refused);   // only a control the page drew
      var body = shown ? putBody(state.view, ctl, !!t.checked) : null;
      if (!body || state.busy) { paint(); return Promise.resolve(null); }   // a click while saving is undone, not queued
      state.busy = true;
      state.message = '';
      paint();                                                             // disabled; the clicked state is not called saved
      return cfg.fetchJson('PUT', API, body).then(function (doc) {
        state.busy = false;
        state.view = Object.assign({}, state.view, { channels: channels(doc), full_text: !!(doc && doc.full_text === true) });
        state.message = 'Saved.';
        paint();
        return doc;
      }, function (e) {
        state.busy = false;
        if (e && e.status === 403) { state.refused = true; state.message = ''; paint(); return null; }
        state.message = FAILED;
        paint();                                                            // back to the last stored state,
        return load();                                                      // then re-read it (a timeout may still have saved)
      });
    }
    if (rootEl.addEventListener) rootEl.addEventListener('change', change);
    var ready = load();
    return { reload: load, change: change, state: function () { return state; }, ready: ready };
  }

  return { putBody: putBody, render: render, mount: mount, API: API, REFUSED: REFUSED, FAILED: FAILED };
});
