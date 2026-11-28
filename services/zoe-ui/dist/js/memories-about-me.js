/**
 * "What Zoe knows about you" on the desktop / phone Memories page (memories.html) - memories-space phase 3.
 *
 * Global `ZoeAboutMe` ({ init, open, close, api }). Loaded by memories.html AFTER js/auth.js, so `fetch` is the wrapped one that
 * attaches the member's session. Flag-dark: the section exists only when `GET /api/memories/mine/actions` answers 200
 * (`ZOE_MEMORIES_VOICE` on); a 404 leaves the trigger hidden and nothing else happens.
 *
 * The list is `GET /api/memories/mine` - the member's OWN rows, grouped; private classes arrive as COUNTS only. Per row, all
 * decided by the server: its STAGE (`stage_label`: You told me / Confirmed / I noticed / Added for you, from the stored authority
 * class), WHERE IT CAME FROM (`came_from`: the owner's own quoted words, channel and day; day + channel only when the source is not
 * theirs to quote; "Source not kept" when there is none) and `muted` (a spoken "don't bring that up" that restraint enforces).
 * Actions: "Not right" (typing is fine on the phone: POST /api/memories/mine/{id}/correct - the existing correction path, the old
 * row kept as history) and "Forget" behind a confirm whose SAFE button has the focus (POST /api/memories/mine/{id}/forget - the
 * audited row forget). There is no mute / unmute / bring-back button: no route serves one (a spoken mute is the only way in).
 * "Let go" lists the retired rows the server returns (`let_go`, with a closed-list reason when the data has one), read-only.
 * A person is not forgotten here: that is the voice "forget <name>" with the PIN, or the People page.
 * The panel's memories card points its phone QR at memories.html#about-me, which opens this section.
 *
 * Every server string goes in through textContent (this file never assigns innerHTML); nothing is stored in the browser.
 * Pinned by dist/test_memories_about_me.js (CI via services/zoe-data/tests/test_memories_about_me_harness.py).
 */
(function (root) {
    'use strict';

    const MSG = {
        signin: 'Sign in to see what Zoe knows about you.',
        down: 'Memory is unavailable right now. Try again in a moment.',
        empty: 'Zoe doesn’t know much about you yet.',
        people: 'To forget a person, tell Zoe “forget” and their name, or use the People page.',
        saved: 'Fixed.',
        forgotten: 'Forgotten.',
        forgetAsk: 'Forget this? This can’t be undone.',
        noSource: 'Source not kept',
        muted: 'Muted: Zoe won’t bring this up unless you ask.',
        letGo: 'Let go',
        letGoSub: 'Replaced or retired. Zoe no longer uses these.',
    };
    const PRIVATE_WORDS = { health: 'health', money: 'money', personal: 'personal', feelings: 'feelings' };
    const state = { doc: null, section: null, trigger: null, actions: null, busy: false, listening: false };

    function el(tag, cls, text) {
        const n = state.doc.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function button(text, cls, onClick) {
        const b = el('button', cls ? 'am-b ' + cls : 'am-b', text);
        b.setAttribute('type', 'button');
        b.addEventListener('click', onClick);
        return b;
    }

    function clear(n) {
        while (n && n.firstChild) n.removeChild(n.firstChild);
    }

    async function api(method, path, body) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let res;
        try {
            res = await root.fetch(path, opts);
        } catch (e) {
            const err = new Error('offline');
            err.status = 0;
            throw err;
        }
        let data = null;
        try { data = await res.json(); } catch (_e) { data = null; }
        if (!res.ok) {
            const err = new Error((data && data.detail) || ('HTTP ' + res.status));
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function message(text) {
        const m = el('p', 'am-msg', text);
        m.setAttribute('role', 'status');
        return m;
    }

    function named(b, act, what) {
        b.setAttribute('data-act', act);
        b.setAttribute('aria-label', b.textContent + ': ' + String(what || '').slice(0, 80));
        return b;
    }

    // Where the row came from. ONLY `came_from.quote` is ever drawn as words: the server puts the owner's own words there and
    // nothing else, so another speaker's words (a pendant recording, a curator's note) arrive as day + channel only.
    function sourceLine(it) {
        const src = it && it.came_from;
        const box = el('div', 'am-src');
        if (!src || typeof src !== 'object') {
            box.appendChild(el('span', 'am-meta', it.provenance_label || (it.source === 'inferred' ? 'I worked this out' : 'You told me')));
            return box;
        }
        if (src.quote) box.appendChild(el('q', 'am-quote', String(src.quote)));
        const where = [src.channel_label, src.day].filter(Boolean).join(' · ');
        if (src.kept || src.quote) {
            if (where) box.appendChild(el('span', 'am-meta', where));
        } else {
            box.appendChild(el('span', 'am-meta', MSG.noSource + (src.day ? ' · ' + src.day : '')));
        }
        return box;
    }

    function itemRow(it) {
        const soft = it.stage ? it.stage === 'noticed' : it.source === 'inferred';
        const row = el('div', 'am-item' + (soft ? ' am-soft' : '') + (it.muted === true ? ' am-muted' : ''));
        row.setAttribute('data-id', String(it.id));
        row.appendChild(el('p', 'am-text', it.text || ''));
        const tags = el('div', 'am-tags');
        if (it.stage_label) tags.appendChild(el('span', 'am-stage am-stage-' + String(it.stage || 'noticed').replace(/[^a-z]/g, ''), it.stage_label));
        if (it.muted === true) tags.appendChild(el('span', 'am-stage am-mute', 'Muted'));
        if (tags.firstChild) row.appendChild(tags);
        row.appendChild(sourceLine(it));
        if (it.muted === true) row.appendChild(el('p', 'am-note', MSG.muted));
        const bar = el('div', 'am-acts');
        const pane = el('div', 'am-pane');
        const note = el('p', 'am-note', '');
        note.setAttribute('role', 'status');
        if (state.actions && state.actions.correct) bar.appendChild(named(button('Not right', '', () => fixPane(it, row, pane, note)), 'not-right', it.text));
        if (state.actions && state.actions.forget) bar.appendChild(named(button('Forget', 'am-danger', () => forgetPane(it, row, pane, note)), 'forget', it.text));
        row.appendChild(bar);
        row.appendChild(pane);
        row.appendChild(note);
        return row;
    }

    function fixPane(it, row, pane, note) {
        clear(pane);
        note.textContent = '';
        const label = el('label', 'am-label', 'What’s right? Say it the way you would tell Zoe.');
        const input = el('textarea', 'am-input');
        input.setAttribute('rows', '2');
        input.setAttribute('maxlength', '280');
        input.setAttribute('placeholder', 'For example: I like oat milk in my coffee');
        pane.appendChild(label);
        pane.appendChild(input);
        pane.appendChild(button('Save', 'am-primary', async () => {
            const text = String(input.value || '').trim();
            if (!text) { note.textContent = 'Type what’s right first.'; return; }
            try {
                await api('POST', '/api/memories/mine/' + encodeURIComponent(it.id) + '/correct', { text });
                note.textContent = MSG.saved;
                await load();
            } catch (err) {
                note.textContent = err.status === 404 ? 'That note is already gone.' : String(err.message || MSG.down);
            }
        }));
        pane.appendChild(button('Cancel', '', () => { clear(pane); }));
    }

    function forgetPane(it, row, pane, note) {
        clear(pane);
        note.textContent = '';
        pane.appendChild(el('span', 'am-label', MSG.forgetAsk));
        // the SAFE choice comes first and holds the focus: Enter / a stray tap keeps the note
        const keep = named(button('Keep it', 'am-primary', () => { clear(pane); }), 'keep', it.text);
        pane.appendChild(keep);
        pane.appendChild(named(button('Yes, forget it', 'am-danger', async () => {
            try {
                await api('POST', '/api/memories/mine/' + encodeURIComponent(it.id) + '/forget');
                if (row.parentNode) row.parentNode.removeChild(row);
            } catch (err) {
                if (err.status === 404) { await load(); return; }      // already gone: re-read the server, never guess
                note.textContent = String(err.message || MSG.down);
            }
        }), 'forget-confirm', it.text));
        if (typeof keep.focus === 'function') keep.focus();
    }

    function letGoBox(rows) {
        const box = el('section', 'am-group am-letgo');
        box.appendChild(el('h3', 'am-h', MSG.letGo));
        box.appendChild(el('p', 'am-note', MSG.letGoSub));
        rows.forEach((r) => {
            if (!r || !r.text) return;
            const row = el('div', 'am-item am-soft');
            row.appendChild(el('p', 'am-text', r.text));
            const why = [r.reason_label, r.when].filter(Boolean).join(' · ');
            if (why) row.appendChild(el('span', 'am-meta', why));
            box.appendChild(row);
        });
        return box;
    }

    function render(data) {
        const list = state.section.__list;
        clear(list);
        const groups = Array.isArray(data && data.groups) ? data.groups : [];
        if (!groups.length) list.appendChild(message(MSG.empty));
        let people = false;
        groups.forEach((g) => {
            const box = el('section', 'am-group');
            box.appendChild(el('h3', 'am-h', g.label || ''));
            if (g.kind === 'people') {
                people = true;
                (g.entries || []).forEach((e) => {
                    const row = el('div', 'am-item am-person');
                    row.appendChild(el('p', 'am-text', e.label || e.name || ''));
                    if (Array.isArray(e.facts) && e.facts.length) row.appendChild(el('span', 'am-meta', e.facts.join('; ')));
                    box.appendChild(row);
                });
            } else {
                (g.items || []).forEach((it) => { if (it && it.id) box.appendChild(itemRow(it)); });
            }
            if (g.more > 0) box.appendChild(el('p', 'am-note', '+' + g.more + ' more'));
            list.appendChild(box);
        });
        if (people) list.appendChild(el('p', 'am-note', MSG.people));
        const priv = Object.keys((data && data.private) || {}).filter((k) => data.private[k] > 0);
        if (priv.length) {
            const n = priv.reduce((a, k) => a + Number(data.private[k] || 0), 0);
            const words = priv.map((k) => PRIVATE_WORDS[k] || 'personal').join(', ');
            list.appendChild(el('p', 'am-note', n + ' private note' + (n === 1 ? '' : 's') + ' (' + words +
                ') ' + (n === 1 ? 'is' : 'are') + ' kept off this list. Ask Zoe about them by name.'));
        }
        if (data && Array.isArray(data.let_go) && data.let_go.length) list.appendChild(letGoBox(data.let_go));
        if (data && data.unsure > 0) {
            list.appendChild(el('p', 'am-note', data.unsure + ' thing' + (data.unsure === 1 ? '' : 's') +
                ' I picked up but couldn’t tie to anything you said ' + (data.unsure === 1 ? 'is' : 'are') + ' not listed.'));
        }
    }

    async function load() {
        const list = state.section.__list;
        try {
            render(await api('GET', '/api/memories/mine'));
        } catch (err) {
            clear(list);
            list.appendChild(message(err.status === 401 || err.status === 403 ? MSG.signin : MSG.down));
        }
    }

    function build() {
        const s = state.section;
        clear(s);
        const head = el('div', 'am-head');
        head.appendChild(el('h2', 'am-title', 'What Zoe knows about you'));
        head.appendChild(button('Close', 'am-close', close));
        s.appendChild(head);
        s.appendChild(el('p', 'am-sub', 'Each note says how sure Zoe is and where it came from. Tap “Not right” to fix it, or forget it.'));
        const list = el('div', 'am-list');
        s.appendChild(list);
        s.__list = list;
    }

    async function open() {
        if (!state.section || !state.actions) return;
        build();
        state.section.hidden = false;
        await load();
    }

    function clearHash() {
        // Leave no '#about-me' behind: with it still in the URL, tapping the menu link again changes nothing, so no hashchange fires
        // and the section would never reopen.
        try {
            const loc = root.location;
            if (loc && loc.hash === '#about-me' && root.history && root.history.replaceState)
                root.history.replaceState(null, '', (loc.pathname || '') + (loc.search || ''));
        } catch (_e) { /* the URL is a convenience: closing still works */ }
    }

    function close() {
        if (!state.section) return;
        clear(state.section);
        state.section.__list = null;
        state.section.hidden = true;
        clearHash();
    }

    async function init(doc, opts) {
        state.doc = doc || root.document;
        opts = opts || {};
        state.section = state.doc.getElementById('aboutMe');
        state.trigger = state.doc.getElementById('aboutMeOpen');
        if (!state.section) return false;
        try {
            state.actions = await api('GET', '/api/memories/mine/actions');
        } catch (_e) {
            state.actions = null;                       // flag off (404) or signed out: the section never appears
            return false;
        }
        if (state.trigger) {
            state.trigger.hidden = false;
            state.trigger.addEventListener('click', open);
        }
        const nav = state.doc.getElementById('aboutMeNav');   // the phone menu's deep link: only once the server has offered the list
        if (nav) nav.hidden = false;
        const hash = opts.hash !== undefined ? opts.hash : (root.location && root.location.hash) || '';
        if (!state.listening && typeof root.addEventListener === 'function') {
            state.listening = true;
            root.addEventListener('hashchange', () => { if (root.location && root.location.hash === '#about-me') open(); });
        }
        if (hash === '#about-me') await open();
        return true;
    }

    root.ZoeAboutMe = { init, open, close, api, MSG };
})(typeof window !== 'undefined' ? window : this);
