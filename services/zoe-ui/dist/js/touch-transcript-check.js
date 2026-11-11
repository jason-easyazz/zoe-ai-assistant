/*
 * The estate's Transcript check (touch/home.html stage `checks`, js/touch-transcript-check.js) - the words the night re-pass could not
 * settle, put to the owner in the morning. Record: docs/knowledge/transcript-check.md. Flag-dark server side (ZOE_TRANSCRIPT_CHECK).
 *
 * One item at a time, written out in the SAME style as Zoe's live speech captions (the `.cvf .cvl` caption: 30 px, light weight, the
 * estate ink tokens). The uncertain word is highlighted and is the only thing to touch. Touching it opens big touch chips - one per
 * model's word - plus "Play it", "None of these" and "Skip". "None of these" offers "Say it" (Zoe listens for the word) or "Fix on
 * phone" (a QR to the phone). NO keyboard anywhere (docs/VISION.md principle 8; panel: voice first, touch second).
 *
 * WHO is asking is never decided here: the server answers 401/403 to a guest / expired session / a card token that is not that member's,
 * and this file turns that into the sign-in prompt - no sentence, no count. Every server string goes in through textContent. Like the
 * Memories space a wall display forgets what it showed: VIEW_TTL_MS with no touch removes the sentence from the DOM, and unmount() (the
 * estate calls it on every navigation) empties the host and drops any late response. Progress ("3 of 7") is the server's count, so the
 * screen can be left and resumed. Nothing is stored in the browser.
 *
 * deps: request(method, path, body) -> Promise<{status, json}>; blob(path) -> Promise<Blob|null>; play(blob); setMeta(text);
 *       signIn(); toast(text); setTimeout / clearTimeout (tests). Pinned by dist/test_touch_transcript_check.js.
 */
(function (root) {
  'use strict';

  var VIEW_TTL_MS = 120000;
  var LISTEN_POLL_MS = 1500;
  var LISTEN_MAX_MS = 48000;
  var BASE = '/api/transcript-check';

  var gen = 0;
  var timer = null;
  var pollT = null;
  var shown = null;
  var timers = { set: null, clear: null };

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
  function stopTimers() {
    if (timer != null) { timers.clear(timer); timer = null; }
    if (pollT != null) { timers.clear(pollT); pollT = null; }
  }
  function unmount() {
    gen++;
    stopTimers();
    if (shown) { clear(shown); shown = null; }
  }

  function mount(host, deps) {
    if (!host) return;
    unmount();
    var my = gen;
    timers.set = deps.setTimeout || function (fn, ms) { return root.setTimeout(fn, ms); };
    timers.clear = deps.clearTimeout || function (id) { return root.clearTimeout(id); };
    var live = function () { return my === gen; };
    var queue = [], done = 0, view = 'loading', busy = false;
    shown = host;
    host.addEventListener('pointerdown', function () { if (live() && view === 'item') touch(); });

    function meta(t) { if (deps.setMeta) deps.setMeta(t || ''); }
    function paint(node) { clear(host); host.appendChild(node); }
    function say(t) { if (deps.toast) deps.toast(t); }

    function note(title, body, actions) {
      var box = el('div', 'tcstate');
      box.appendChild(el('div', 'tcstate-t', title));
      if (body) box.appendChild(el('div', 'tcstate-b', body));
      (actions || []).forEach(function (a) {
        var b = button('tcbtn' + (a.primary ? ' primary' : ''), a.text);
        b.setAttribute('data-act', a.act);
        b.addEventListener('click', a.fn);
        box.appendChild(b);
      });
      return box;
    }
    function touch() {
      if (timer != null) timers.clear(timer);
      timer = timers.set(function () { timer = null; if (live()) hidden(); }, VIEW_TTL_MS);
    }
    function signIn() {
      stopTimers(); view = 'signin'; queue = []; meta('');
      paint(note('Sign in to check your transcripts', 'I only show these to you. Tap to enter your PIN.',
        [{ act: 'signin', text: 'Sign in', primary: true, fn: function () { if (deps.signIn) deps.signIn(); } }]));
    }
    function failed() {
      stopTimers(); view = 'failed'; queue = []; meta('');
      paint(note('I couldn’t load those just now', 'Nothing has been changed.', [{ act: 'retry', text: 'Try again', primary: true, fn: load }]));
    }
    function hidden() {
      stopTimers(); view = 'hidden'; queue = []; meta('');
      paint(note('Hidden to keep your things private', 'Tap to carry on where you were.', [{ act: 'show', text: 'Carry on', primary: true, fn: load }]));
    }
    function finished() {
      stopTimers(); view = 'empty'; queue = []; meta('');
      paint(note(done ? 'All done, thank you.' : 'Nothing to check right now', done ? 'I’ve fixed those words.' : 'I’ll tell you when something needs you.'));
    }

    function request(method, path, body) {
      return deps.request(method, path, body).then(function (r) {
        if (!live()) return null;
        if (r.status === 401 || r.status === 403) { signIn(); return null; }
        return r;
      }, function () { if (live()) failed(); return null; });
    }

    function load() {
      view = 'loading'; meta('');
      paint(note('Getting those ready…', ''));
      request('GET', BASE).then(function (r) {
        if (!r) return;
        if (r.status !== 200) { failed(); return; }
        queue = (r.json && r.json.items) || [];
        done = (r.json && r.json.done) || 0;
        if (!queue.length) { finished(); return; }
        showItem();
      });
    }

    function progress() { meta((done + 1) + ' of ' + (done + queue.length)); }

    function next() {
      busy = false;
      queue.shift();
      done += 1;
      if (!queue.length) { finished(); return; }
      showItem();
    }

    // ---- "Who said this?" -------------------------------------------------------------------------------------------
    // The turn is written out as a caption line; the candidate people (best first) are big targets, then "Someone new" (a persistent
    // unnamed voice the owner can name later - by voice, "that's Quinn", or on the phone), "Play it" and "Skip". A candidate with no
    // person (an unnamed voice) reads "Same as <voice>" and merges. No keyboard.
    function showWho(it) {
      var box = el('div', 'tcwrap');
      box.appendChild(el('div', 'tcq', 'Who said this?'));
      var cvf = el('div', 'cvf');
      var line = el('div', 'cvl', it.span);
      line.setAttribute('data-id', it.id);
      cvf.appendChild(line);
      var chips = el('div', 'tcchips'), acts = el('div', 'tcacts'), sub = el('div', 'tcsub');
      (it.alts || []).forEach(function (a) {
        var c = button('tcchip' + (a.cluster ? ' unnamed' : ''), a.cluster ? 'Same as ' + a.word : a.word, (a.cluster ? 'Same voice as ' : 'It was ') + a.word);
        c.setAttribute('data-alt', String(a.i));
        c.addEventListener('click', function () { send({ alt: a.i }); });
        chips.appendChild(c);
      });
      var nw = button('tcchip new', 'Someone new', 'Someone I have not named yet');
      nw.setAttribute('data-act', 'new');
      nw.addEventListener('click', function () { send({ new: true }, true); });
      chips.appendChild(nw);
      [['play', 'Play it'], ['skip', 'Skip']].forEach(function (p) {
        var b = button('tcbtn', p[1]);
        b.setAttribute('data-act', p[0]);
        b.addEventListener('click', function () { act(p[0]); });
        acts.appendChild(b);
      });
      function send(body, offerName) {
        if (busy) return;
        busy = true; touch();
        request('POST', BASE + '/' + encodeURIComponent(it.id) + '/answer', body).then(function (r) {
          if (!r) return;
          if (r.status === 200 || r.status === 409) {
            if (offerName) { naming(); return; }
            next(); return;
          }
          busy = false;
          say(r.status === 410 ? 'That person isn’t there any more.' : 'That didn’t save. Try again.');
        });
      }
      // "Someone new" is saved at once (the voice stays as an unnamed voice); naming is optional and never needs a keyboard.
      function naming() {
        clear(chips); clear(acts); clear(sub);
        sub.appendChild(el('div', 'tcnote', 'Saved as a new voice. Want to name them now?'));
        var s = button('tcbtn primary', 'Say the name'); s.setAttribute('data-act', 'sayname');
        s.addEventListener('click', function () { sayName(); });
        var l = button('tcbtn', 'Later'); l.setAttribute('data-act', 'later');
        l.addEventListener('click', function () { next(); });
        sub.appendChild(s); sub.appendChild(l);
      }
      function sayName() {
        clear(sub);
        sub.appendChild(el('div', 'tcnote', 'Listening - say, “that’s Quinn”.'));
        request('POST', BASE + '/' + encodeURIComponent(it.id) + '/listen').then(function (r) {
          if (!r || r.status !== 200) { clear(sub); sub.appendChild(el('div', 'tcnote', 'I can’t listen from here. Skip this one for now.')); return; }
          pollName(0);
        });
      }
      function pollName(elapsed) {
        pollT = timers.set(function () {
          pollT = null;
          if (!live() || view !== 'item' || queue[0] !== it) return;
          request('GET', BASE + '/' + encodeURIComponent(it.id) + '/state').then(function (r) {
            if (!r || !live() || queue[0] !== it) return;
            if (r.json && r.json.named) { next(); return; }
            if (elapsed + LISTEN_POLL_MS >= LISTEN_MAX_MS) { clear(sub); sub.appendChild(el('div', 'tcnote', 'I didn’t catch it. You can name them later.')); return; }
            pollName(elapsed + LISTEN_POLL_MS);
          });
        }, LISTEN_POLL_MS);
      }
      function act(what) {
        if (busy) return;
        touch();
        if (what === 'play') {
          deps.blob(BASE + '/' + encodeURIComponent(it.id) + '/clip').then(function (b) {
            if (!live()) return;
            if (!b) { say('I don’t have that audio any more.'); return; }
            if (deps.play) deps.play(b);
          });
        } else {
          busy = true;
          request('POST', BASE + '/' + encodeURIComponent(it.id) + '/skip').then(function (r) {
            if (!r) return;
            if (r.status === 200 || r.status === 409) { next(); return; }
            busy = false; say('That didn’t save. Try again.');
          });
        }
      }
      box.appendChild(cvf); box.appendChild(chips); box.appendChild(acts); box.appendChild(sub);
      paint(box);
    }

    // ---- one item ------------------------------------------------------------------------------------------------
    function showItem() {
      view = 'item'; busy = false;
      touch(); progress();
      var it = queue[0];
      if (it.kind === 'who') { showWho(it); return; }
      var box = el('div', 'tcwrap');
      var cvf = el('div', 'cvf');            // the live-caption component: `.cvf .cvl` is Zoe's speech as written on the Ask surface
      var sentence = el('div', 'cvl');
      cvf.appendChild(sentence);
      sentence.setAttribute('data-id', it.id);
      var chips = el('div', 'tcchips');
      var acts = el('div', 'tcacts');
      var sub = el('div', 'tcsub');

      function line(spanText, chosen) {
        clear(sentence);
        if (it.before) sentence.appendChild(el('span', 'tcb', it.before + ' '));
        var w = button('tcw' + (chosen ? ' chosen' : ''), spanText || '…', 'Uncertain word: ' + (spanText || 'missing'));
        w.setAttribute('data-act', 'word');
        w.addEventListener('click', function () { openChips(); });
        sentence.appendChild(w);
        if (it.after) sentence.appendChild(el('span', 'tca', ' ' + it.after));
      }
      line(it.span, false);

      function openChips() {
        if (busy) return;
        clear(chips); clear(acts); clear(sub);
        (it.alts || []).forEach(function (a) {
          var c = button('tcchip', a.word, 'Use: ' + a.word);
          c.setAttribute('data-alt', String(a.i));
          if (a.word === it.pick) c.className += ' pick';
          c.addEventListener('click', function () { choose(a); });
          chips.appendChild(c);
        });
        [['play', 'Play it'], ['none', 'None of these'], ['skip', 'Skip']].forEach(function (p) {
          var b = button('tcbtn', p[1]);
          b.setAttribute('data-act', p[0]);
          b.addEventListener('click', function () { act(p[0]); });
          acts.appendChild(b);
        });
        touch();
      }

      function choose(a) {
        if (busy) return;
        busy = true;
        line(a.word, true);
        request('POST', BASE + '/' + encodeURIComponent(it.id) + '/answer', { alt: a.i }).then(function (r) {
          if (!r) return;
          if (r.status === 200 || r.status === 409) { next(); return; }
          if (r.status === 410 || r.status === 404) { say('That one has gone.'); next(); return; }
          busy = false; line(it.span, false); say('That didn’t save. Try again.');
        });
      }

      function act(what) {
        if (busy) return;
        touch();
        if (what === 'play') {
          deps.blob(BASE + '/' + encodeURIComponent(it.id) + '/clip').then(function (b) {
            if (!live()) return;
            if (!b) { say('I don’t have that audio any more.'); return; }
            if (deps.play) deps.play(b);
          });
        } else if (what === 'skip') {
          busy = true;
          request('POST', BASE + '/' + encodeURIComponent(it.id) + '/skip').then(function (r) {
            if (!r) return;
            if (r.status === 200 || r.status === 409) { next(); return; }
            busy = false; say('That didn’t save. Try again.');
          });
        } else if (what === 'none') {
          clear(sub);
          var s = button('tcbtn primary', 'Say it');
          s.setAttribute('data-act', 'sayit');
          s.addEventListener('click', function () { sayIt(); });
          sub.appendChild(s);
        }
      }

      function sayIt() {
        clear(sub);
        sub.appendChild(el('div', 'tcnote', 'Listening - just say the word.'));
        request('POST', BASE + '/' + encodeURIComponent(it.id) + '/listen').then(function (r) {
          if (!r) return;
          if (r.status !== 200) { clear(sub); sub.appendChild(el('div', 'tcnote', 'I can’t listen from here. Skip this one for now.')); return; }
          poll(0);
        });
        function poll(elapsed) {
          pollT = timers.set(function () {
            pollT = null;
            if (!live() || view !== 'item' || queue[0] !== it) return;
            request('GET', BASE + '/' + encodeURIComponent(it.id) + '/state').then(function (r) {
              if (!r || !live() || queue[0] !== it) return;
              if (r.json && r.json.status === 'answered') { line(r.json.answer || '…', true); next(); return; }
              if (elapsed + LISTEN_POLL_MS >= LISTEN_MAX_MS) { clear(sub); sub.appendChild(el('div', 'tcnote', 'I didn’t catch it. Tap None of these to try again.')); return; }
              poll(elapsed + LISTEN_POLL_MS);
            });
          }, LISTEN_POLL_MS);
        }
      }

      box.appendChild(cvf); box.appendChild(chips); box.appendChild(acts); box.appendChild(sub);
      paint(box);
    }

    load();
  }

  // The voice session this screen's private rows belong to. A close, or a DIFFERENT member, drops everything at once: the DOM, the cached alternatives (they live in
  // the mount's closure and die with it), the privacy timer and any in-flight read. A touch cannot extend a timer that no longer exists; seeing items again needs a
  // fresh authorised read (``mount`` -> ``load``).
  var sessionMember = null;
  function onVoiceSession(d) {
    d = d || {};
    var state = d.state, member = d.member_id || null;
    if (state === 'closed') { sessionMember = null; unmount(); return true; }
    if (state === 'open' || state === 'renewed') {
      var changed = sessionMember !== null && member !== sessionMember;
      sessionMember = member;
      if (changed) { unmount(); return true; }
    }
    return false;
  }

  root.ZoeTranscriptCheck = { mount: mount, unmount: unmount, onVoiceSession: onVoiceSession };
})(typeof window !== 'undefined' ? window : globalThis);
