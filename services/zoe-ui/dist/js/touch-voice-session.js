/*
 * Panel voice session UI (ZOE_VOICE_SESSION, flag-dark server side) — the touch estate only.
 *
 * "An identified voice is a password": while a member's voice session is open on THIS panel the estate shows an
 * unobtrusive "<name> · voice" badge, and a spoken calendar / list / reminders read shows its real rows in a card.
 * When the session closes (another voice, timeout, "lock", consent revoked) the badge goes and every private card
 * goes with it. A turn that is not authorised gets a privacy-safe card with no member and no rows.
 *
 * Input: window events relayed by js/touch-ui-executor.js from the panel's OWN push channel —
 *   zoe:voice-session  {panel_id, state: open|renewed|closed, member_id, display_name, badge, reason}
 *   zoe:voice-card     {panel_id, member_id, kind, private, title, subtitle, items:[{text,meta,done}], more, empty, body}
 * Every string is set with textContent (never innerHTML): rows are household data.
 * Nothing is stored (no localStorage, no sessionStorage): a reload shows nothing until the next verified turn.
 */
(function () {
    'use strict';
    if (window.ZoeVoiceSession) return;

    var PIN_CARD_MS = 90000;     // a private card shown on a PIN sign-in (no voice session) fades after this
    var LOCKED_CARD_MS = 20000;  // the privacy-safe card is a hint, not a fixture
    var state = { open: false, member_id: '', display_name: '', badge: 'voice' };
    var badgeEl = null;
    var cardEl = null;
    var cardTimer = null;

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = String(text);
        return n;
    }

    function ensureStyle() {
        if (document.getElementById('zvs-style')) return;
        var s = document.createElement('style');
        s.id = 'zvs-style';
        s.textContent =
            '#zvs-badge{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:60;padding:6px 14px;' +
            'border-radius:999px;background:rgba(123,150,255,.18);border:1px solid rgba(123,150,255,.45);' +
            'color:var(--ink,#F4F6FA);font-size:15px;letter-spacing:.02em;pointer-events:none}' +
            '#zvs-card{position:fixed;top:64px;right:24px;width:min(420px,calc(100vw - 48px));max-height:70vh;overflow:auto;' +
            'z-index:59;padding:18px 20px;border-radius:20px;background:rgba(14,16,26,.88);backdrop-filter:blur(18px);' +
            'border:1px solid rgba(255,255,255,.14);color:var(--ink,#F4F6FA)}' +
            '#zvs-card .zvs-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px}' +
            '#zvs-card .zvs-t{font-size:21px;font-weight:600}' +
            '#zvs-card .zvs-x{font:inherit;font-size:22px;min-width:48px;min-height:48px;background:transparent;' +
            'border:0;color:var(--ink-soft,rgba(244,246,250,.74));cursor:pointer}' +
            '#zvs-card .zvs-s{font-size:15px;color:var(--ink-faint,rgba(244,246,250,.48));margin:2px 0 10px}' +
            '#zvs-card .zvs-r{display:flex;gap:12px;padding:8px 0;border-top:1px solid rgba(255,255,255,.08);font-size:18px}' +
            '#zvs-card .zvs-r.done .zvs-rt{text-decoration:line-through;color:var(--ink-faint,rgba(244,246,250,.48))}' +
            '#zvs-card .zvs-m{flex:none;min-width:84px;color:var(--ink-soft,rgba(244,246,250,.74));font-variant-numeric:tabular-nums}' +
            '#zvs-card .zvs-b{font-size:18px;color:var(--ink-soft,rgba(244,246,250,.74))}';
        (document.head || document.documentElement).appendChild(s);
    }

    function renderBadge() {
        if (!state.open) {
            if (badgeEl && badgeEl.parentNode) badgeEl.parentNode.removeChild(badgeEl);
            badgeEl = null;
            return;
        }
        ensureStyle();
        if (!badgeEl) {
            badgeEl = el('div', '', '');
            badgeEl.id = 'zvs-badge';
            badgeEl.setAttribute('role', 'status');
            document.body.appendChild(badgeEl);
        }
        badgeEl.textContent = (state.display_name || state.member_id) + ' · ' + (state.badge || 'voice');
    }

    function clearCard() {
        if (cardTimer) { clearTimeout(cardTimer); cardTimer = null; }
        if (cardEl && cardEl.parentNode) cardEl.parentNode.removeChild(cardEl);
        cardEl = null;
    }

    function showCard(d) {
        clearCard();
        ensureStyle();
        cardEl = el('section', '', null);
        cardEl.id = 'zvs-card';
        cardEl.setAttribute('data-kind', String(d.kind || ''));
        cardEl.setAttribute('data-private', d.private ? '1' : '0');
        if (d.private) cardEl.setAttribute('data-member', String(d.member_id || ''));
        var head = el('div', 'zvs-h', null);
        head.appendChild(el('div', 'zvs-t', d.title || ''));
        var close = el('button', 'zvs-x', '×');
        close.setAttribute('aria-label', 'Close');
        close.addEventListener('click', clearCard);
        head.appendChild(close);
        cardEl.appendChild(head);
        if (d.subtitle) cardEl.appendChild(el('div', 'zvs-s', d.subtitle));
        if (!d.private) {
            cardEl.appendChild(el('div', 'zvs-b', d.body || ''));
        } else if (d.card_token && window.ZoeReviewCards) {
            // A review card (open loops, the day review): rows with Done / Remind / Keep / "That's me" buttons, drawn by
            // js/touch-review-cards.js inside THIS card, so it lives and dies with the session exactly like every other row.
            window.ZoeReviewCards.render(cardEl, d);
        } else {
            var items = Array.isArray(d.items) ? d.items : [];
            if (!items.length && d.empty) cardEl.appendChild(el('div', 'zvs-b', d.empty));
            items.forEach(function (it) {
                var row = el('div', 'zvs-r' + (it && it.done ? ' done' : ''), null);
                if (it && it.meta) row.appendChild(el('span', 'zvs-m', it.meta));
                row.appendChild(el('span', 'zvs-rt', (it && it.text) || ''));
                cardEl.appendChild(row);
            });
            if (d.more > 0) cardEl.appendChild(el('div', 'zvs-s', '+' + d.more));
        }
        document.body.appendChild(cardEl);
        // A private card under a voice session lives exactly as long as the session; otherwise it fades.
        if (!d.private) cardTimer = setTimeout(clearCard, LOCKED_CARD_MS);
        else if (!state.open) cardTimer = setTimeout(clearCard, PIN_CARD_MS);
    }

    function onSession(d) {
        d = d || {};
        if (d.state === 'open' || d.state === 'renewed') {
            if (state.open && state.member_id && state.member_id !== d.member_id) clearCard();
            state.open = true;
            state.member_id = String(d.member_id || '');
            state.display_name = String(d.display_name || '');
            state.badge = String(d.badge || 'voice');
            if (cardEl && cardEl.getAttribute('data-private') === '0') clearCard();
            // A card that arrived a moment before its session frame now lives as long as the session.
            if (cardEl && cardEl.getAttribute('data-member') === state.member_id && cardTimer) {
                clearTimeout(cardTimer); cardTimer = null;
            }
        } else if (d.state === 'closed') {
            state.open = false;
            state.member_id = '';
            state.display_name = '';
            // Every private card goes when the session goes — whoever it was for.
            if (cardEl && cardEl.getAttribute('data-private') === '1') clearCard();
        }
        renderBadge();
    }

    function onCard(d) {
        d = d || {};
        if (d.private) {
            // A private card for someone OTHER than the open session's member is never shown.
            if (state.open && String(d.member_id || '') !== state.member_id) return;
            if (!d.member_id) return;
        }
        showCard(d);
    }

    window.addEventListener('zoe:voice-session', function (e) { onSession(e && e.detail); });
    window.addEventListener('zoe:voice-card', function (e) { onCard(e && e.detail); });

    window.ZoeVoiceSession = {
        state: function () { return { open: state.open, member_id: state.member_id, display_name: state.display_name }; },
        clear: function () { state.open = false; state.member_id = ''; clearCard(); renderBadge(); },
    };
})();
