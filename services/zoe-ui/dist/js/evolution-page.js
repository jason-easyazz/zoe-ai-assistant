/**
 * "Zoe's proposals" decide page (desktop / phone, evolution.html). Owner only: the server refuses everyone else (403).
 *
 * Global `ZoeEvolution` ({ init }). Loaded AFTER js/auth.js, so `fetch` carries the session. Routes (routers/evolution_inbox.py):
 *   GET  /api/system/evolution/inbox            short ranked list of grouped cards, counts and dates only
 *   POST /api/system/evolution/decide           { key, action: approve | reject | later }  (the card's group key)
 *   POST /api/system/evolution/dismiss-stale    { stale_days }
 * Every server string goes into the DOM through textContent; this file never assigns innerHTML and uses no inline handlers.
 * Pinned by dist/test_evolution_page.js (CI: services/zoe-data/tests/test_evolution_inbox.py).
 */
(function (root) {
    'use strict';

    const BASE = '/api/system/evolution';
    const ACTION_LABEL = { approve: 'Yes, do it', reject: 'No', later: 'Later' };
    const MEASURED = { better: 'measured: better', no_change: 'measured: no change', worse: 'measured: worse',
        not_measurable: 'not measurable', not_yet: 'not yet measurable' };
    const state = { doc: null, staleDays: 30 };

    function el(tag, cls, text) {
        const n = state.doc.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

    async function api(method, path, body) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const r = await root.fetch(path, opts);
        if (!r.ok) {
            const e = new Error(r.status === 403 ? 'Only the household admin can decide on these.' : 'Zoe could not do that (' + r.status + ').');
            e.status = r.status;
            throw e;
        }
        return r.json();
    }

    function toast(msg, bad) {
        const t = state.doc.getElementById('evoToast');
        if (!t) return;
        t.textContent = msg;
        t.className = 'toast show' + (bad ? ' bad' : '');
        root.setTimeout(() => { t.className = 'toast'; }, 3500);
    }

    function when(c) {
        const span = c.first_seen && c.last_seen && c.first_seen !== c.last_seen ? c.first_seen + ' to ' + c.last_seen : (c.last_seen || '');
        const n = Number(c.count) || 0;
        return (n > 1 ? n + ' similar proposals' : '1 proposal') + (span ? ', ' + span : '');
    }

    function card(c) {
        const box = el('section', 'card evo-card');
        box.appendChild(el('h3', null, c.noticed));
        box.appendChild(el('p', 'meta', when(c)));
        const ev = c.evidence || {};
        const counts = Object.keys(ev).filter(k => typeof ev[k] === 'number' && ev[k] > 0).map(k => k.replace(/_/g, ' ') + ': ' + ev[k]);
        if (counts.length) box.appendChild(el('p', 'meta', counts.join(' · ')));
        box.appendChild(el('p', 'line', 'Proposes: ' + c.proposes));
        box.appendChild(el('p', 'hint', 'If you say yes: ' + c.next));
        if (c.approved) box.appendChild(el('p', 'chip', 'You already said yes'));
        const acts = (c.actions || []).filter(a => ACTION_LABEL[a]);
        if (acts.length && c.key) {
            const row = el('div', 'row evo-acts');
            for (const a of acts) {
                const b = el('button', 'pb' + (a === 'approve' ? ' primary' : a === 'reject' ? ' danger' : ''), ACTION_LABEL[a]);
                b.setAttribute('type', 'button');
                b.setAttribute('data-action', a);
                b.addEventListener('click', () => decide(c.key, a, b));
                row.appendChild(b);
            }
            box.appendChild(row);
        }
        return box;
    }

    async function decide(key, action, btn) {
        if (btn) btn.disabled = true;
        try {
            const out = await api('POST', BASE + '/decide', { key, action });
            toast(out.changed ? 'Recorded. Nothing has been changed in Zoe yet.' : 'Already recorded.');
            await load();
        } catch (e) {
            toast(e.message, true);
            if (btn) btn.disabled = false;
        }
    }

    async function dismissStale(btn) {
        btn.disabled = true;
        try {
            const out = await api('POST', BASE + '/dismiss-stale', { stale_days: state.staleDays });
            toast('Dismissed ' + (Number(out.dismissed) || 0) + ' old proposals.');
            await load();
        } catch (e) {
            toast(e.message, true);
            btn.disabled = false;
        }
    }

    function render(data) {
        const doc = state.doc;
        const list = doc.getElementById('evoCards');
        const stale = doc.getElementById('evoStale');
        const past = doc.getElementById('evoPast');
        const foot = doc.getElementById('evoFooter');
        [list, stale, past].forEach(clear);
        const cards = data.cards || [];
        if (!cards.length) list.appendChild(el('p', 'empty', 'Nothing waiting for you.'));
        cards.forEach(c => list.appendChild(card(c)));
        if (data.more_cards) list.appendChild(el('p', 'meta', data.more_cards + ' more after you decide these.'));
        const st = data.stale || {};
        state.staleDays = Number(st.days) || 30;
        if (st.count) {
            stale.appendChild(el('p', 'line', st.count + ' old proposals hidden (nothing new for ' + state.staleDays + ' days).'));
            const b = el('button', 'pb', 'Dismiss old proposals');
            b.setAttribute('type', 'button');
            b.setAttribute('data-action', 'dismiss-stale');
            b.addEventListener('click', () => dismissStale(b));
            stale.appendChild(b);
        }
        if (data.later) stale.appendChild(el('p', 'meta', data.later + ' put off for later.'));
        (data.decided || []).forEach(d => {
            const m = d.measured ? (MEASURED[d.measured.result] || 'not yet measurable') : '';
            const parts = [d.at || '', d.component || '', d.decision || d.status || '', m].filter(Boolean);
            past.appendChild(el('p', 'line', parts.join(' · ')));
        });
        if (!(data.decided || []).length) past.appendChild(el('p', 'empty', 'No decisions yet.'));
        foot.textContent = data.approval_does || 'Today a yes only records your decision.';
    }

    async function load() {
        try {
            render(await api('GET', BASE + '/inbox'));
        } catch (e) {
            const list = state.doc.getElementById('evoCards');
            clear(list);
            list.appendChild(el('p', 'empty', e.message));
        }
    }

    function init(doc) {
        state.doc = doc;
        return load();
    }

    root.ZoeEvolution = { init, MEASURED, ACTION_LABEL };
})(typeof window !== 'undefined' ? window : globalThis);
