/*
 * Panel review cards (ZOE_PANEL_REVIEW_CARDS, flag-dark server side) — the touch estate only. Record:
 * docs/knowledge/panel-review-cards.md.
 *
 * Voice first: "what's on my plate?" / "review my day" pushes a private voice_card with a card_token; js/touch-voice-session.js
 * owns the card (member check, removed when the voice session closes) and hands its rows to ZoeReviewCards.render. Touch
 * second: each row has big buttons — Done / Remind me / Drop (an open loop), Keep / Skip (something from the day), and for a new
 * voice Play / That's me / Someone I know / TV / radio / Not now. Naming someone NEW needs typing, so it shows a QR to the
 * phone's Pendant page instead (never a keyboard on the panel).
 *
 * The card token goes in the X-Panel-Card HEADER (never a URL); the server honours it only while the member's voice session is
 * open or the request carries their own session. Every server string is set with textContent; a clip and the QR are fetched
 * with the headers and shown from blob URLs. Nothing is stored in the browser (the session id is only read).
 */
(function () {
    'use strict';
    if (window.ZoeReviewCards) return;

    var AGAIN = 'Say it again so I know it’s you.';
    var FAILED = 'That didn’t work. Try again.';

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = String(text);
        return n;
    }
    function clear(n) { while (n && n.children && n.children.length) n.removeChild(n.children[0]); if (n) n.textContent = ''; }

    function sid() {
        try {
            var s = JSON.parse(window.localStorage.getItem('zoe_session') || '{}');
            if (s.expires_at && Date.parse(s.expires_at) < Date.now()) return '';
            return s.session_id || '';
        } catch (_) { return ''; }
    }
    function headers(token, json) {
        var h = { 'X-Panel-Card': token };
        var s = sid();
        if (s) h['X-Session-ID'] = s;
        if (json) h['Content-Type'] = 'application/json';
        return h;
    }

    function ensureStyle() {
        if (document.getElementById('zrc-style')) return;
        var s = document.createElement('style');
        s.id = 'zrc-style';
        s.textContent =
            '#zvs-card .zrc-g{margin:14px 0 4px;font-size:14px;letter-spacing:.06em;text-transform:uppercase;' +
            'color:var(--ink-faint,rgba(244,246,250,.48))}' +
            '#zvs-card .zrc-r{flex-direction:column;gap:8px}' +
            '#zvs-card .zrc-m{display:block;font-size:14px;color:var(--ink-soft,rgba(244,246,250,.74))}' +
            '#zvs-card .zrc-acts,#zvs-card .zrc-sub{display:flex;flex-wrap:wrap;gap:8px}' +
            '#zvs-card .zrc-b{font:inherit;font-size:17px;min-height:48px;min-width:72px;padding:0 16px;border-radius:14px;' +
            'border:1px solid rgba(255,255,255,.16);background:rgba(255,255,255,.06);color:var(--ink,#F4F6FA);cursor:pointer}' +
            '#zvs-card .zrc-b[data-act=done],#zvs-card .zrc-b[data-act=keep],#zvs-card .zrc-b[data-act=me]{' +
            'background:var(--accent,#7b96ff);border-color:transparent;color:#fff}' +
            '#zvs-card .zrc-r[data-busy] .zrc-b{opacity:.5;pointer-events:none}' +
            '#zvs-card .zrc-qr{width:176px;height:176px;border-radius:12px;background:#fff;padding:6px}' +
            '#zvs-card .zrc-note{min-height:22px;margin-top:10px;font-size:16px;color:var(--ink-soft,rgba(244,246,250,.74))}';
        (document.head || document.documentElement).appendChild(s);
    }

    function render(card, d) {
        ensureStyle();
        var items = Array.isArray(d.items) ? d.items : [];
        var groups = d.groups || {};
        var note = el('div', 'zrc-note', '');
        note.setAttribute('role', 'status');
        var rows = [];
        var lastGroup = null;
        if (!items.length && d.empty) card.appendChild(el('div', 'zvs-b', d.empty));
        items.forEach(function (it) {
            if (!it || !it.id) return;
            if (it.group && it.group !== lastGroup) {
                lastGroup = it.group;
                if (groups[it.group]) card.appendChild(el('div', 'zrc-g', groups[it.group]));
            }
            var row = el('div', 'zvs-r zrc-r', null);
            row.setAttribute('data-item', String(it.id));
            var main = el('div', '', null);
            main.appendChild(el('span', 'zvs-rt', it.text || ''));
            if (it.meta) main.appendChild(el('span', 'zrc-m', it.meta));
            row.appendChild(main);
            var bar = el('div', 'zrc-acts', null);
            var sub = el('div', 'zrc-sub', null);
            (Array.isArray(it.actions) ? it.actions : []).forEach(function (a) {
                if (!a || !a.key) return;
                var b = el('button', 'zrc-b', a.label || a.key);
                b.setAttribute('data-act', String(a.key));
                b.addEventListener('click', function () { act(it, a, row, sub); });
                bar.appendChild(b);
            });
            row.appendChild(bar);
            row.appendChild(sub);
            card.appendChild(row);
            rows.push(row);
        });
        card.appendChild(note);

        function gone(row) {
            if (row.parentNode) row.parentNode.removeChild(row);
            rows = rows.filter(function (r) { return r !== row; });
            if (!rows.length) note.textContent = 'All done.';
        }
        function say(text) { note.textContent = text || ''; }

        function send(it, action, choice, row) {
            var body = { item: String(it.id), action: action };
            if (choice) body.choice = choice;
            row.setAttribute('data-busy', '1');
            return fetch('/api/panel/cards/act', { method: 'POST', headers: headers(d.card_token, true), body: JSON.stringify(body) })
                .then(function (r) {
                    row.removeAttribute('data-busy');
                    if (!r.ok) { say(r.status === 403 || r.status === 404 ? AGAIN : FAILED); return; }
                    return r.json().then(function (j) { gone(row); if (j && j.note) say(j.note); });
                })
                .catch(function () { row.removeAttribute('data-busy'); say(FAILED); });
        }
        function blob(path) {
            return fetch(path, { headers: headers(d.card_token) }).then(function (r) {
                if (!r.ok) { say(r.status === 403 ? AGAIN : 'Nothing to show for that one.'); return null; }
                return r.blob();
            }).catch(function () { say(FAILED); return null; });
        }
        function act(it, a, row, sub) {
            if (a.local && a.key === 'later') { gone(row); return; }
            if (a.key === 'play') {
                blob('/api/panel/cards/clip/' + encodeURIComponent(String(it.id))).then(function (b) {
                    if (!b) return;
                    var u = URL.createObjectURL(b);
                    var au = new Audio(u);
                    au.onended = function () { URL.revokeObjectURL(u); };
                    au.play();
                });
                return;
            }
            if (Array.isArray(a.choices) && a.choices.length) {
                clear(sub);
                a.choices.forEach(function (c) {
                    var b = el('button', 'zrc-b', c.label || c.key);
                    b.setAttribute('data-choice', String(c.key));
                    b.addEventListener('click', function () {
                        if (c.phone) { phone(sub); return; }
                        send(it, a.key, String(c.key), row);
                    });
                    sub.appendChild(b);
                });
                return;
            }
            send(it, a.key, null, row);
        }
        function phone(sub) {
            blob('/api/panel/cards/phone-qr').then(function (b) {
                if (!b) return;
                clear(sub);
                var img = document.createElement('img');
                img.className = 'zrc-qr';
                img.alt = 'Scan with your phone';
                img.onload = function () { try { img.scrollIntoView({ block: 'nearest' }); } catch (_) { /* old engine */ } };
                img.src = URL.createObjectURL(b);
                sub.appendChild(img);
                sub.appendChild(el('span', 'zrc-m', 'Scan with your phone to name them on the Pendant page.'));
            });
        }
    }

    window.ZoeReviewCards = { render: render };
})();
