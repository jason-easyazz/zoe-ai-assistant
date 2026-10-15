/*
 * The estate's Memories space - "What Zoe knows about you" (touch/home.html, stage `memories`).
 *
 * Renders ONLY what the server hands over. WHO is asking is never decided here: GET /api/memories/mine answers 401/403
 * to a guest, a downgraded session or a device-token-only caller (services/zoe-data/memory_space.py), and this file turns
 * that refusal into a sign-in prompt - it never shows, caches or infers rows on its own. No row is ever put anywhere but
 * textContent, so a note that contains markup is just text.
 *
 * Contracts (pinned by dist/test_touch_memories_space.js):
 *   - guest / refused / expired -> the sign-in prompt, no rows, no counts;
 *   - member -> groups (people, preferences, routines, dates, places, other), a provenance badge per item, a large
 *     "Forget" target per item behind a confirm step; people are forgotten by voice (no row id to forget);
 *   - private classes arrive as COUNTS and are shown as counts;
 *   - a wall display forgets what it showed: after VIEW_TTL_MS with no touch the rows are removed from the DOM;
 *   - unmount() (the estate calls it on every navigation) cancels the timer and drops any in-flight response.
 * No keyboard anywhere: taps only (docs/VISION.md principle 8).
 */
(function (root) {
  'use strict';

  var VIEW_TTL_MS = 120000;
  var MINE_URL = '/api/memories/mine';
  var BADGE = { told: 'You told me', inferred: 'I picked this up' };

  var gen = 0;       // bumped on every mount/unmount: a late response from an earlier screen is dropped
  var timer = null;       // the idle (privacy) window
  var watchT = null;      // the who-is-here check while rows are up
  var shown = null;       // the host currently holding this screen: unmount() empties it
  var timers = { set: null, clear: null };
  var WATCH_MS = 5000;

  function el(tag, cls, text) {
    var e = root.document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  function clear(host) {
    if (typeof host.replaceChildren === 'function') { host.replaceChildren(); return; }
    while (host.firstChild) host.removeChild(host.firstChild);
  }
  function button(cls, text, label) {
    var b = el('button', cls, text);
    b.type = 'button';
    if (label) b.setAttribute('aria-label', label);
    return b;
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function stopTimer() {
    if (timer != null) { timers.clear(timer); timer = null; }
    if (watchT != null) { timers.clear(watchT); watchT = null; }
  }
  // Called by the estate on EVERY navigation. Some screens (cards) are painted without resetting the stage's content, so a
  // member's rows must be removed here, not left in a hidden-but-live DOM.
  function unmount() {
    gen++;
    stopTimer();
    if (shown) { clear(shown); shown = null; }
  }

  function mount(host, deps) {
    if (!host) return;
    unmount();
    var my = gen;
    timers.set = deps.setTimeout || function (fn, ms) { return root.setTimeout(fn, ms); };
    timers.clear = deps.clearTimeout || function (id) { return root.clearTimeout(id); };
    var live = function () { return my === gen; };
    var lastData = null, groupState = {};
    var view = 'loading';       // loading | data | signin | failed | hidden | empty
    var loadSid = null;         // the session the rows on screen were read under
    shown = host;
    var activity = function () { if (live() && view === 'data') touch(); };
    // the scroller is the host itself (scroll does not bubble), so listen here, once per mount
    host.addEventListener('pointerdown', activity);
    host.addEventListener('scroll', activity);
    function sidNow() { return deps.sid ? deps.sid() : null; }

    function meta(text) { if (deps.setMeta) deps.setMeta(text || ''); }
    function paint(node) { clear(host); host.appendChild(node); }

    // ---- states -------------------------------------------------------------------------------------------------
    function note(title, body, actions) {
      var box = el('div', 'memstate');
      box.appendChild(el('div', 'memstate-t', title));
      if (body) box.appendChild(el('div', 'memstate-b', body));
      (actions || []).forEach(function (a) {
        var b = button('membtn' + (a.primary ? ' primary' : ''), a.text);
        b.setAttribute('data-act', a.act);
        b.addEventListener('click', a.fn);
        box.appendChild(b);
      });
      return box;
    }
    function loading() {
      view = 'loading';
      meta('');
      paint(note('Looking through what I know…', ''));
    }
    function signIn() {
      stopTimer();
      view = 'signin'; lastData = null;
      meta('');
      paint(note('Sign in to see what I know about you',
        'I only show this to you. Tap to enter your PIN.',
        [{ act: 'signin', text: 'Sign in', primary: true, fn: function () { if (deps.signIn) deps.signIn(); } }]));
    }
    function failed() {
      stopTimer();
      view = 'failed'; lastData = null;
      meta('');
      paint(note('I couldn’t reach my memory just now', 'Nothing has been changed.',
        [{ act: 'retry', text: 'Try again', primary: true, fn: load }]));
    }
    function hidden() {
      stopTimer();
      view = 'hidden'; lastData = null;
      meta('');
      paint(note('Hidden to keep your things private', 'Tap to show them again.',
        [{ act: 'show', text: 'Show again', primary: true, fn: load }]));
    }
    function empty() {
      view = 'empty'; lastData = null;
      meta('');
      paint(note('I don’t know much about you yet', 'Tell me something and say “remember that” — it will show up here.'));
    }

    // ---- the member's view ----------------------------------------------------------------------------------------
    function touch() {
      if (timer != null) { timers.clear(timer); timer = null; }
      timer = timers.set(function () { timer = null; if (live()) hidden(); }, VIEW_TTL_MS);
    }
    // The session can change under a screen that nobody is touching (the kiosk downgrades a PIN session to guest just before
    // it expires; a voice turn can sign someone else in). Rows read under one session never stay up under another: re-ask.
    function watch() {
      if (watchT != null) { timers.clear(watchT); watchT = null; }
      watchT = timers.set(function () {
        watchT = null;
        if (!live() || view !== 'data') return;
        if (sidNow() !== loadSid) { load(); return; }
        watch();
      }, WATCH_MS);
    }

    function renderItem(it, grp) {
      var row = el('div', 'memitem');
      row.setAttribute('data-id', it.id);
      var main = el('div', 'memmain');
      main.appendChild(el('div', 'memtx', it.text));
      var m = el('div', 'memmeta');
      m.appendChild(el('span', 'membadge ' + (it.source === 'inferred' ? 'inferred' : 'told'), BADGE[it.source] || BADGE.told));
      if (it.when) m.appendChild(el('span', 'memwhen', it.when));
      main.appendChild(m);
      row.appendChild(main);
      var f = button('memforget', 'Forget', 'Forget: ' + it.text);
      f.setAttribute('data-act', 'forget');
      f.addEventListener('click', function () { forgetFlow(it, row, grp, f); });
      row.appendChild(f);
      return row;
    }

    function renderPerson(p) {
      var card = el('div', 'memperson');
      if (p.label) card.appendChild(el('div', 'memname', p.label));
      (p.facts || []).forEach(function (f) { card.appendChild(el('div', 'memfact', f)); });
      return card;
    }

    function renderGroup(g) {
      var sec = el('section', 'memgrp');
      sec.setAttribute('data-group', g.key);
      var count = g.kind === 'people' ? (g.entries || []).length : (g.items || []).length;
      var h = el('h4', 'memgh', g.label);
      var c = el('span', 'memgc', String(count + (g.more || 0)));
      h.appendChild(c);
      sec.appendChild(h);
      var grp = { key: g.key, sec: sec, countEl: c, left: count, more: g.more || 0 };
      groupState[g.key] = grp;
      if (g.kind === 'people') {
        (g.entries || []).forEach(function (p) { sec.appendChild(renderPerson(p)); });
        sec.appendChild(el('div', 'memhint', 'To make me forget someone, ask me out loud.'));
      } else {
        (g.items || []).forEach(function (it) { sec.appendChild(renderItem(it, grp)); });
      }
      if (g.more) sec.appendChild(el('div', 'memhint', plural(g.more, 'more thing', 'more things') + ' not shown here.'));
      return sec;
    }

    function privateLine(priv) {
      var names = Object.keys(priv || {}).filter(function (k) { return priv[k] > 0; });
      if (!names.length) return null;
      var n = names.reduce(function (a, k) { return a + priv[k]; }, 0);
      return el('div', 'memprivate',
        plural(n, 'private note', 'private notes') + ' (' + names.join(', ') + ') ' + (n === 1 ? 'isn’t' : 'aren’t') +
        ' shown on this screen. Ask me about them by name.');
    }

    function renderView(data) {
      lastData = data;
      groupState = {};
      var groups = (data && data.groups) || [];
      var priv = (data && data.private) || {};
      var wrap = el('div', 'memwrap');
      var any = false;
      var cols = el('div', 'memcols');
      groups.forEach(function (g) { any = true; cols.appendChild(renderGroup(g)); });
      var pl = privateLine(priv);
      if (!any && !pl) { empty(); return; }
      if (any) wrap.appendChild(cols);
      else wrap.appendChild(note('I don’t know much about you yet', 'Tell me something and say “remember that” — it will show up here.'));
      if (pl) wrap.appendChild(pl);
      if (data.unsure > 0) {
        wrap.appendChild(el('div', 'memhint', plural(data.unsure, 'note I’m', 'notes I’m') + ' not sure about ' +
          (data.unsure === 1 ? 'isn’t' : 'aren’t') + ' listed.'));
      }
      meta(plural(data.total != null ? data.total : 0, 'thing', 'things'));
      paint(wrap);
      view = 'data';
      touch();
      watch();
    }

    // ---- forget (confirm -> POST -> remove the row) ---------------------------------------------------------------
    function forgetFlow(it, row, grp, btn) {
      if (!live()) return;
      touch();
      var askedSid = sidNow();
      deps.confirm({ title: 'Forget this?', body: it.text, yes: 'Forget it', no: 'Keep it' }, function () {
        // the screen was hidden, or someone else is signed in now, while the modal was open: do nothing, show nothing
        if (!live() || view !== 'data' || sidNow() !== askedSid) { if (live() && view === 'data') load(); return; }
        btn.disabled = true;
        deps.apiJson('POST', MINE_URL + '/' + encodeURIComponent(it.id) + '/forget').then(function () {
          if (!live()) return;
          if (deps.toast) deps.toast('Forgotten');
          removeRow(it, row, grp);
        }).catch(function (e) {
          if (!live()) return;
          var m = e && e.message;
          if (m === 'auth') { signIn(); return; }
          // 404 is also what a memory store that could not answer looks like: trust the server's list, not a local guess
          if (m === 'http 404') { if (deps.toast) deps.toast('Already gone'); load(); return; }
          btn.disabled = false;
          if (deps.toast) deps.toast('Couldn’t forget that — try again');
        });
      });
    }
    function removeRow(it, row, grp) {
      if (view !== 'data' || !lastData) return;   // hidden / signed out meanwhile: never repaint rows
      if (row.parentNode) row.parentNode.removeChild(row);
      // keep the data the screen was built from in step, so a re-render never resurrects a forgotten row
      (lastData.groups || []).forEach(function (g) {
        if (g.key === grp.key && g.items) g.items = g.items.filter(function (x) { return x.id !== it.id; });
      });
      lastData.total = Math.max(0, (lastData.total || 0) - 1);
      grp.left--;
      if (grp.left <= 0 && grp.more > 0) { load(); return; }   // more rows exist beyond the cap: fetch them
      if (grp.left <= 0) {
        if (grp.sec.parentNode) grp.sec.parentNode.removeChild(grp.sec);
        lastData.groups = (lastData.groups || []).filter(function (g) { return g.key !== grp.key; });
        delete groupState[grp.key];
        if (!lastData.groups.length) { renderView(lastData); return; }   // nothing left: the empty state (private line kept)
      } else {
        grp.countEl.textContent = String(grp.left + grp.more);
      }
      meta(plural(lastData.total, 'thing', 'things'));
    }

    // ---- load -----------------------------------------------------------------------------------------------------
    function load() {
      stopTimer();
      loading();
      loadSid = sidNow();
      deps.apiGet(MINE_URL).then(function (data) {
        if (!live()) return;
        renderView(data);
      }).catch(function (e) {
        if (!live()) return;
        if (e && e.message === 'auth') signIn(); else failed();
      });
    }
    load();
  }

  root.ZoeMemoriesSpace = { mount: mount, unmount: unmount, VIEW_TTL_MS: VIEW_TTL_MS };
})(window);
