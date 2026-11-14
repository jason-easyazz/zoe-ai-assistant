/* Settings > Telegram > "Reminders on my phone" - the member's OWN opt-in for phone delivery of due reminders (desktop Settings only;
 * the panel never shows it). Record: docs/knowledge/reminder-phone-delivery.md (server: services/zoe-data/reminder_delivery.py).
 *
 *   ZoeReminderPhone.mount(rootEl, { fetchJson(method, url, body) -> Promise<json> (rejects with err.status), push?: adapter })
 *
 * GET  /api/user/profile/reminder-delivery -> { channels, full_text, telegram_linked, mode, eligible }   (own profile)
 * PUT  /api/user/profile/reminder-delivery    { channels, full_text }  -> the stored document;  403 for anyone not an adult member.
 * GET  /api/push/vapid-public-key -> { public_key };  POST /api/push/subscribe { subscription };  DELETE /api/push/subscribe { endpoint }
 *
 * Telegram row: shown only when telegram_linked AND eligible === true (the server's own adult check, the same one the PUT uses); it
 * adds / removes "telegram" only. "Also send to this browser" row: shown only when this browser supports web push AND eligible ===
 * true (Telegram need not be linked); it adds / removes "web_push" only. The browser's permission prompt is asked ONLY from the
 * member's click on that control, and it is the first thing the click does (iOS needs it inside the gesture) - never on load.
 * A denied permission shows one plain line and changes nothing. On: permission -> VAPID key -> subscribe this browser -> store the
 * subscription -> PUT with web_push; any failure undoes what was done and reverts the control. Off: remove this browser's
 * subscription (server row, then the browser) -> PUT without web_push. A 403 on the PUT (the backstop) reverts the control and
 * replaces the section with one plain sentence. Nothing says "Saved" until the server answered; a failed save reverts to the last
 * stored state. Only fixed strings are rendered (never a server message). One global: ZoeReminderPhone.
 * Tested without a browser by dist/test_reminder_phone_settings.js.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ZoeReminderPhone = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var API = '/api/user/profile/reminder-delivery';
  var PUSH_API = '/api/push';
  var REFUSED = 'Sending reminders to a phone is for adult members of the household.';
  var FAILED = 'That change did not save. The setting shown is the one Zoe has.';
  var DENIED = 'This browser is not allowed to show notifications from Zoe, so nothing was changed. You can allow them in the browser\'s site settings.';
  var PUSH_FAILED = 'This browser could not be set up for notifications. Nothing was changed.';

  function channels(view) { return (view && Array.isArray(view.channels)) ? view.channels.slice() : []; }
  function sends(view) { return channels(view).indexOf('telegram') !== -1; }

  /* The PUT body for one control change, from the last STORED view. Each control owns exactly one channel and keeps the rest. */
  function putBody(view, ctl, checked) {
    var full = !!(view && view.full_text === true);
    var own = ctl === 'send' ? 'telegram' : (ctl === 'push' ? 'web_push' : null);
    if (own) {
      var ch = channels(view).filter(function (c) { return c !== own; });
      if (checked) ch.push(own);
      return { channels: ch, full_text: full };
    }
    if (ctl === 'full') return { channels: channels(view), full_text: !!checked };
    return null;
  }

  /* Controls only for a member the server POSITIVELY calls eligible (an adult); a missing field (an older server), false, or
   * anything else hides them. The 403 on the PUT stays as the backstop. */
  function eligible(st) { return !!(st.view && st.view.eligible === true && !st.refused); }
  function shown(st) { return eligible(st) && st.view.telegram_linked === true; }
  function pushShown(st) { return eligible(st) && st.pushSupported === true; }
  /* "This browser" is on only when the member chose web_push AND this browser holds a subscription. */
  function pushOn(st) { return channels(st.view).indexOf('web_push') !== -1 && !!st.subscribed; }

  /* ------------------------------------------------------------------ the browser's push (injectable; absent outside a browser) */
  function b64ToBytes(s) {
    var b64 = (s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(b64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function browserPush() {
    var g = typeof window !== 'undefined' ? window : null;
    var nav = typeof navigator !== 'undefined' ? navigator : null;
    var ok = !!(g && nav && 'serviceWorker' in nav && 'PushManager' in g && 'Notification' in g && g.isSecureContext !== false);
    function existingReg() { return nav.serviceWorker.getRegistration('/'); }
    function reg() {          // only from a click: settings.html does not register the worker itself (other pages do)
      return existingReg().then(function (r) { return r || nav.serviceWorker.register('/sw.js', { scope: '/' }); })
        .then(function () { return nav.serviceWorker.ready; });
    }
    return {
      supported: function () { return ok; },
      permission: function () { return ok ? g.Notification.permission : 'denied'; },
      requestPermission: function () {
        return new Promise(function (res) {
          var p = g.Notification.requestPermission(res);        // the old callback form and the promise form
          if (p && typeof p.then === 'function') p.then(res, function () { res('denied'); });
        });
      },
      current: function () {    // reading never prompts and never registers
        return existingReg().then(function (r) { return r && r.pushManager ? r.pushManager.getSubscription() : null; });
      },
      subscribe: function (publicKey) {
        return reg().then(function (r) {
          return r.pushManager.getSubscription().then(function (old) { return old ? old.unsubscribe() : null; })
            .then(function () { return r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(publicKey) }); });
        });
      },
    };
  }
  function subJson(sub) {
    var j = sub && typeof sub.toJSON === 'function' ? sub.toJSON() : (sub || {});
    return { endpoint: String(j.endpoint || ''), keys: { p256dh: String((j.keys || {}).p256dh || ''), auth: String((j.keys || {}).auth || '') } };
  }

  /* ------------------------------------------------------------------ render (pure) */
  function toggle(ctl, on, dis) {
    return '<label class="toggle"><input type="checkbox" data-ctl="' + ctl + '"' + (on ? ' checked' : '') + dis + '>' +
      '<div class="toggle-track"></div></label>';
  }
  function render(st) {
    if (st.refused) return '<div class="card"><div class="row"><div class="row-info"><div class="row-sub">' + REFUSED + '</div></div></div></div>';
    var v = st.view;
    var tg = shown(st), wp = pushShown(st);
    if (!tg && !wp) return '';
    var dis = st.busy ? ' disabled' : '';
    var h = '<div class="card"><div class="card-header">Reminders on my phone</div>';
    if (tg) {
      h += '<div class="row"><div class="row-info"><div class="row-label">Send my reminders to my phone</div>' +
        '<div class="row-sub">Your reminders also arrive in Telegram when they are due (not in your quiet hours).</div></div>' +
        toggle('send', sends(v), dis) + '</div>';
    }
    if (wp) {
      h += '<div class="row"><div class="row-info"><div class="row-label">Also send to this browser</div>' +
        '<div class="row-sub">Your reminders also show as a notification from this browser. The browser asks you to allow it first.</div></div>' +
        toggle('push', pushOn(st), dis) + '</div>';
    }
    h += '<div class="row"><div class="row-info"><div class="row-label">Show the full text of private reminders on my phone</div>' +
      '<div class="row-sub">Otherwise a private reminder arrives as "You have a reminder."</div></div>' +
      toggle('full', v.full_text === true, dis) + '</div>';
    if (v.mode !== 'on') {
      h += '<div class="row"><div class="row-info"><div class="row-sub">Phone reminders are not switched on for this household yet. ' +
        'Your choice is kept for when they are.</div></div></div>';
    }
    if (st.message) h += '<div class="row"><div class="row-info"><div class="row-sub" data-msg="1">' + st.message + '</div></div></div>';
    return h + '</div>';
  }

  /* ------------------------------------------------------------------ controller */
  function mount(rootEl, cfg) {
    var push = cfg.push || browserPush();
    var state = { view: null, busy: false, refused: false, message: '', pushSupported: false, subscribed: false };
    try { state.pushSupported = !!push.supported(); } catch (e) { state.pushSupported = false; }

    function paint() { rootEl.innerHTML = render(state); }
    function current() { return Promise.resolve().then(function () { return push.current(); }).catch(function () { return null; }); }
    function load() {
      return cfg.fetchJson('GET', API).then(function (v) {
        state.view = v || null;
        if (!state.pushSupported) { paint(); return v; }
        return current().then(function (sub) { state.subscribed = !!sub; paint(); return v; });
      }, function () {                // cannot read it: keep the last stored state, or show nothing rather than an unbacked control
        paint();
        return null;
      });
    }
    function saved(doc) {
      state.busy = false;
      state.view = Object.assign({}, state.view, { channels: channels(doc), full_text: !!(doc && doc.full_text === true) });
      state.message = 'Saved.';
      paint();
      return doc;
    }
    function putFailed(e) {
      state.busy = false;
      if (e && e.status === 403) { state.refused = true; state.message = ''; paint(); return null; }
      state.message = FAILED;
      paint();                                                            // back to the last stored state,
      return load();                                                      // then re-read it (a timeout may still have saved)
    }
    function stop(message) { state.busy = false; state.message = message; paint(); return null; }
    function undo(sub, stored) {      // best effort; never throws
      var steps = Promise.resolve();
      if (stored) steps = steps.then(function () { return cfg.fetchJson('DELETE', PUSH_API + '/subscribe', { endpoint: subJson(sub).endpoint }); });
      return steps.catch(function () {}).then(function () { return sub && sub.unsubscribe ? sub.unsubscribe() : null; }).catch(function () {});
    }

    /* Turning "this browser" on. The permission request is the FIRST call (nothing awaited before it): it must stay in the click. */
    function pushOnFlow(body) {
      var perm;
      try { perm = push.permission(); } catch (e) { perm = 'denied'; }
      if (perm === 'denied') return Promise.resolve(stop(DENIED));
      var asked;
      try { asked = Promise.resolve(perm === 'granted' ? 'granted' : push.requestPermission()); } catch (e) { asked = Promise.resolve('denied'); }
      var sub = null, stored = false;
      return asked.catch(function () { return 'denied'; }).then(function (p) {
        if (p !== 'granted') return stop(DENIED);
        return cfg.fetchJson('GET', PUSH_API + '/vapid-public-key').then(function (k) {
          var key = k && typeof k.public_key === 'string' ? k.public_key : '';
          if (!key) throw new Error('no key');
          return push.subscribe(key);
        }).then(function (s) {
          sub = s;
          return cfg.fetchJson('POST', PUSH_API + '/subscribe', { subscription: subJson(s) });
        }).then(function (r) {
          if (!r || r.error) throw new Error('not stored');      // the route answers 200 {"error"} for a missing subscription
          stored = true;
          return cfg.fetchJson('PUT', API, body).then(function (doc) { state.subscribed = true; return saved(doc); }, function (e) {
            return undo(sub, stored).then(function () { state.subscribed = false; return putFailed(e); });
          });
        }, function () {
          return undo(sub, stored).then(function () { state.subscribed = false; return stop(PUSH_FAILED); });
        });
      });
    }
    /* Turning it off: this browser's subscription goes first (server row, then the browser), then web_push leaves the channels. */
    function pushOffFlow(body) {
      return current().then(function (sub) {
        var gone = sub ? cfg.fetchJson('DELETE', PUSH_API + '/subscribe', { endpoint: subJson(sub).endpoint })
          .then(function () { return sub.unsubscribe ? sub.unsubscribe() : null; }) : Promise.resolve();
        return gone.then(function () {
          state.subscribed = false;
          return cfg.fetchJson('PUT', API, body).then(saved, putFailed);
        }, function () { return stop(FAILED); });
      });
    }

    function change(ev) {
      var t = ev && ev.target;
      var ctl = t && t.getAttribute ? t.getAttribute('data-ctl') : null;
      if (!ctl) return Promise.resolve(null);
      var drawn = ctl === 'push' ? pushShown(state) : (ctl === 'send' ? shown(state) : (shown(state) || pushShown(state)));
      var body = drawn ? putBody(state.view, ctl, !!t.checked) : null;      // only a control the page drew
      if (!body || state.busy) { paint(); return Promise.resolve(null); }   // a click while saving is undone, not queued
      state.busy = true;
      state.message = '';
      if (ctl === 'push') {
        var flow = t.checked ? pushOnFlow(body) : pushOffFlow(body);       // started BEFORE the repaint: the prompt is the gesture's first act
        if (state.busy) paint();
        return flow;
      }
      paint();                                                             // disabled; the clicked state is not called saved
      return cfg.fetchJson('PUT', API, body).then(saved, putFailed);
    }
    if (rootEl.addEventListener) rootEl.addEventListener('change', change);
    var ready = load();
    return { reload: load, change: change, state: function () { return state; }, ready: ready };
  }

  return { putBody: putBody, shown: shown, pushShown: pushShown, render: render, mount: mount, API: API, REFUSED: REFUSED,
    FAILED: FAILED, DENIED: DENIED, PUSH_FAILED: PUSH_FAILED };
});
