/*
 * Voice enrolment on the estate's talk screen ("Zoe, learn my voice") - the touch estate only.
 *
 * Zoe asks for five short phrases and the person only HEARS each one, which is hard to remember. While the panel daemon runs the
 * enrolment this shows the SAME phrase as large readable text ("Say: ..."), a step counter (2 of 5), a "recording" state while the mic
 * is open, a tick when the clip is accepted, a gentle "that one sounded different" line on a re-ask, and a closing line. Zoe still
 * SPEAKS every phrase; the screen is in addition, never instead.
 *
 * Input: ONE window event relayed by js/touch-ui-executor.js from this panel's OWN push channel (only when the frame names this panel) -
 *   zoe:voice-enroll  {panel_id, state: phrase|recording|accepted|reask|saving|done|partial|failed|clear, line, phrase, index, total}
 * Every state is a complete picture (no memory of earlier frames is needed), so a page that was reloaded mid-run repaints on the next one.
 *
 * Safety: the wall must never keep a stale phrase. `clear` removes it at once (a stop, a timeout, a shutdown, no voice); a closing line
 * (done / partial / failed) removes itself after a few seconds; any other state removes itself if nothing newer arrives (the daemon died).
 * Privacy: only the phrase text, the counter and a short line are shown - no member name is ever in the frame. Everything is set with
 * textContent, and NOTHING is stored (no localStorage, no sessionStorage, no cookie): a reload shows nothing until the next frame.
 */
(function () {
    'use strict';
    if (window.ZoeVoiceEnroll) return;

    var CLOSING_MS = 8000;      // done / partial / failed: long enough to read while Zoe says it, then gone
    var WATCHDOG_MS = 75000;    // no newer frame this long (the daemon died mid-run): take the phrase down
    var MAX_DOTS = 12;

    var root = null;
    var timer = null;
    var current = { state: '', index: 0, total: 0 };

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = String(text);
        return n;
    }

    function ensureStyle() {
        if (document.getElementById('zve-style')) return;
        var s = document.createElement('style');
        s.id = 'zve-style';
        s.textContent =
            '#zve{position:fixed;inset:0;z-index:90;display:flex;flex-direction:column;align-items:center;justify-content:center;' +
            'gap:28px;padding:0 150px;text-align:center;color:var(--ink,#F4F6FA);' +
            'background:radial-gradient(ellipse at 50% 38%,rgba(34,40,84,.97),rgba(8,10,24,.98) 70%)}' +
            '#zve .zve-line{font-size:27px;min-height:36px;color:var(--ink-faint,rgba(244,246,250,.62))}' +
            '#zve[data-state="reask"] .zve-line{color:#ffd27a}' +
            '#zve[data-state="recording"] .zve-line{color:#a9d4ff}' +
            '#zve .zve-phrase{font-size:52px;line-height:1.35;font-weight:300;max-width:960px;min-height:70px}' +
            '#zve .zve-mark{min-height:64px;font-size:56px;line-height:64px;color:#8fe3a8}' +
            '#zve[data-state="recording"] .zve-mark::before{content:"";display:inline-block;width:22px;height:22px;border-radius:50%;' +
            'background:#ff6b6b;vertical-align:middle;animation:zve-pulse 1s ease-in-out infinite}' +
            '#zve .zve-dots{display:flex;gap:14px;justify-content:center;min-height:18px}' +
            '#zve .zve-dot{width:16px;height:16px;border-radius:50%;border:2px solid rgba(244,246,250,.45)}' +
            '#zve .zve-dot.done{background:#8fe3a8;border-color:#8fe3a8}' +
            '#zve .zve-dot.cur{border-color:#a9d4ff;box-shadow:0 0 14px rgba(123,150,255,.9)}' +
            '#zve .zve-count{font-size:24px;color:var(--ink-faint,rgba(244,246,250,.62));font-variant-numeric:tabular-nums;min-height:32px}' +
            '@keyframes zve-pulse{0%,100%{opacity:1}50%{opacity:.25}}';
        (document.head || document.documentElement).appendChild(s);
    }

    function stopTimer() {
        if (timer) { clearTimeout(timer); timer = null; }
    }

    function clear() {
        stopTimer();
        if (root && root.parentNode) root.parentNode.removeChild(root);
        root = null;
        current = { state: '', index: 0, total: 0 };
    }

    function ensureRoot() {
        ensureStyle();
        if (root && root.parentNode) return root;
        root = el('div', '', null);
        root.id = 'zve';
        root.setAttribute('role', 'status');
        root.setAttribute('aria-live', 'polite');
        // A tap hides it (the next frame brings it back): a stuck overlay must never lock the wall.
        root.addEventListener('click', clear);
        document.body.appendChild(root);
        return root;
    }

    function num(v, lo, hi) {
        var n = Math.floor(Number(v));
        if (!isFinite(n)) return 0;
        return Math.max(lo, Math.min(hi, n));
    }

    function paint(d) {
        var state = String(d.state);
        var total = num(d.total, 0, MAX_DOTS);
        var index = num(d.index, 0, MAX_DOTS);
        var closing = state === 'done' || state === 'partial' || state === 'failed';
        // saving / a closing line carry no counter of their own: keep the one already on screen (a failure shows none)
        if (state === 'saving' || state === 'done' || state === 'partial') { total = total || current.total; index = index || current.index; }
        if (state === 'failed') { total = 0; index = 0; }
        // completed clips: the ones before the current phrase; all of them once saving / done
        var completed = state === 'accepted' ? index
            : (state === 'saving' || state === 'done' || state === 'partial') ? total
            : Math.max(0, index - 1);
        var r = ensureRoot();
        r.setAttribute('data-state', state);
        while (r.firstChild) r.removeChild(r.firstChild);

        r.appendChild(el('div', 'zve-line', d.line || (state === 'phrase' ? 'Say:' : '')));
        var showPhrase = state === 'phrase' || state === 'recording' || state === 'accepted' || state === 'reask';
        r.appendChild(el('div', 'zve-phrase', showPhrase ? (d.phrase || '') : ''));
        r.appendChild(el('div', 'zve-mark', (state === 'accepted' || state === 'done' || state === 'partial') ? '\u2713' : ''));

        var dots = el('div', 'zve-dots', null);
        for (var i = 1; i <= total; i++) {
            var cls = 'zve-dot' + (i <= completed ? ' done' : (showPhrase && i === index && state !== 'accepted' ? ' cur' : ''));
            dots.appendChild(el('span', cls, null));
        }
        r.appendChild(dots);
        // numerals only: language-neutral, and it reads at a glance
        r.appendChild(el('div', 'zve-count', (total && showPhrase && index) ? (index + ' / ' + total) : ''));

        current = { state: state, index: index, total: total };
        stopTimer();
        timer = setTimeout(clear, closing ? CLOSING_MS : WATCHDOG_MS);
    }

    function onEnroll(d) {
        d = d || {};
        var state = String(d.state || '');
        if (state === 'clear') { clear(); return; }
        if (['phrase', 'recording', 'accepted', 'reask', 'saving', 'done', 'partial', 'failed'].indexOf(state) < 0) return;
        paint(d);
    }

    window.addEventListener('zoe:voice-enroll', function (e) { onEnroll(e && e.detail); });

    window.ZoeVoiceEnroll = {
        state: function () { return { visible: !!(root && root.parentNode), state: current.state, index: current.index, total: current.total }; },
        clear: clear,
    };
})();
