/**
 * Caregiver curation on the desktop / phone Memories page (memories.html) - memories-space phase 7.
 *
 * Global `ZoeCuration` (init, open, close). Loaded AFTER js/auth.js, so `fetch` carries the member session. Flag-dark: nothing
 * appears unless `GET /api/memories/curation/me` answers 200 (`ZOE_MEMORIES_CURATORS` on). Two parts, each shown only when the
 * server offers it:
 *   - "Help with my memories" (an ADULT): which categories a household admin may look after for you (PUT /me/<category>).
 *     Turning one off gives those notes back to you: your own word wins again.
 *   - "Memories for my family" (an adult ADMIN, `GET /members` 200): pick a child (or an adult who opted in), see ONLY the notes
 *     added for them, add one (typed: text + category), fix or remove one (remove asks first; it is the audited forget), switch
 *     health / medication / money on for a child, and keep or accept the notes Zoe held when the child said something different.
 * The panel never shows this (the server refuses a panel). Every server string goes in through textContent (never innerHTML);
 * nothing is stored in the browser. Pinned by dist/test_memories_curation.js (CI: tests/test_memories_curation_harness.py).
 */
(function (root) {
    'use strict';

    const BASE = '/api/memories/curation';
    const state = { doc: null, section: null, trigger: null, me: null, members: null, member: null };

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
        const opts = { method: method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const res = await root.fetch(path, opts);
        let data = null;
        try { data = await res.json(); } catch (_e) { data = null; }
        if (!res.ok) {
            const err = new Error((data && data.detail) || ('Something went wrong (' + res.status + ').'));
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function say(box, text) {
        const m = el('p', 'am-msg', text);
        m.setAttribute('role', 'status');
        box.appendChild(m);
    }

    function select(options, value) {
        const s = el('select', 'am-input');
        options.forEach((o) => {
            const opt = el('option', null, o.label);
            opt.value = o.key;
            s.appendChild(opt);
        });
        if (value) s.value = value;
        return s;
    }

    function path(member, tail) {
        return BASE + '/members/' + encodeURIComponent(member) + (tail || '');
    }

    function labelOf(key) {
        const all = (state.me && state.me.categories) || [];
        const hit = all.find((c) => c.key === key);
        return hit ? hit.label : key;
    }

    async function reloadMe() {
        state.me = await api('GET', BASE + '/me');
    }

    function renderMine(box) {
        const me = state.me;
        if (!me) return;
        box.appendChild(el('h3', 'am-h', 'Help with my memories'));
        if (!me.editable) {
            box.appendChild(el('p', 'am-note', 'A grown-up in your household can add notes for you to ask Zoe about.'));
            return;
        }
        box.appendChild(el('p', 'am-note', 'Let a household admin look after these for you. Turn one off and those notes are yours again.'));
        me.categories.forEach((c) => {
            const on = me.on.indexOf(c.key) >= 0;
            const row = el('div', 'am-item');
            row.appendChild(el('p', 'am-text', c.label + (on ? ': on' : ': off')));
            row.appendChild(button(on ? 'Turn off' : 'Turn on', on ? '' : 'am-primary', async () => {
                try {
                    await api('PUT', BASE + '/me/' + encodeURIComponent(c.key), { on: !on });
                    await reloadMe();
                    render();
                } catch (err) { say(row, err.message); }
            }));
            box.appendChild(row);
        });
    }

    function renderMembers(box) {
        if (!state.members || !state.members.length) return;
        box.appendChild(el('h3', 'am-h', 'Memories for my family'));
        box.appendChild(el('p', 'am-note', 'Add things they can ask Zoe about. You only see the notes added for them.'));
        const pane = el('div', 'am-pane');
        state.members.forEach((m) => pane.appendChild(button(m.name, '', () => openMember(m))));
        box.appendChild(pane);
    }

    function noteRow(m, r) {
        const row = el('div', 'am-item');
        row.appendChild(el('p', 'am-text', r.text));
        row.appendChild(el('p', 'am-meta', labelOf(r.category) + (r.added_by ? ' · added by ' + r.added_by : '')));
        const acts = el('div', 'am-acts');
        acts.appendChild(button('Fix', '', () => {
            clear(acts);
            const input = el('input', 'am-input');
            input.value = r.text;
            acts.appendChild(input);
            acts.appendChild(button('Save', 'am-primary', async () => {
                try {
                    await api('POST', path(m.user_id, '/rows/' + encodeURIComponent(r.id) + '/edit'), { text: input.value });
                    await openMember(m);
                } catch (err) { say(acts, err.message); }
            }));
            acts.appendChild(button('Cancel', '', () => openMember(m)));
        }));
        acts.appendChild(button('Remove', 'am-danger', () => {
            clear(acts);
            acts.appendChild(el('span', 'am-label', 'Remove this note for good?'));
            acts.appendChild(button('Yes, remove', 'am-danger', async () => {
                try {
                    await api('POST', path(m.user_id, '/rows/' + encodeURIComponent(r.id) + '/forget'));
                    await openMember(m);
                } catch (err) { say(acts, err.message); }
            }));
            acts.appendChild(button('No', '', () => openMember(m)));
        }));
        row.appendChild(acts);
        return row;
    }

    function addForm(m, cats, box) {
        const form = el('div', 'am-pane');
        const input = el('input', 'am-input');
        input.setAttribute('placeholder', m.name + '’s swimming is on Tuesdays');
        input.setAttribute('aria-label', 'New note for ' + m.name);
        const kind = select(cats, 'routines');
        form.appendChild(input);
        form.appendChild(kind);
        form.appendChild(button('Add', 'am-primary', async () => {
            try {
                await api('POST', path(m.user_id, '/rows'), { text: input.value, category: kind.value });
                await openMember(m);
            } catch (err) { say(form, err.message); }
        }));
        box.appendChild(form);
    }

    function sensitiveSwitches(m, box) {
        if (!(m.sensitive_switchable || []).length) return;
        box.appendChild(el('p', 'am-note', 'Health, medication and money stay off until you turn them on. They are never shown on the wall panel.'));
        const pane = el('div', 'am-pane');
        m.sensitive_switchable.forEach((key) => {
            const on = (m.sensitive_on || []).indexOf(key) >= 0;
            pane.appendChild(button(labelOf(key) + (on ? ': on' : ': off'), on ? 'am-primary' : '', async () => {
                try {
                    await api('PUT', path(m.user_id, '/sensitive/' + encodeURIComponent(key)), { on: !on });
                    await loadMembers();
                    const fresh = (state.members || []).find((x) => x.user_id === m.user_id);
                    await openMember(fresh || m);
                } catch (err) { say(pane, err.message); }
            }));
        });
        box.appendChild(pane);
    }

    async function heldNotes(m, box) {
        const got = await api('GET', path(m.user_id, '/held'));
        if (!got || !(got.held || []).length) return;
        box.appendChild(el('h3', 'am-h', 'Things ' + m.name + ' said differently'));
        got.held.forEach((h) => {
            const row = el('div', 'am-item');
            row.appendChild(el('p', 'am-text', m.name + ' said: ' + h.text));
            row.appendChild(el('p', 'am-meta', 'Your note: ' + h.against.text));
            const acts = el('div', 'am-acts');
            ['keep', 'accept'].forEach((action) => {
                acts.appendChild(button(action === 'keep' ? 'Keep my note' : 'Use what they said', '', async () => {
                    try {
                        await api('POST', path(m.user_id, '/held/' + encodeURIComponent(h.id)), { action: action });
                        await openMember(m);
                    } catch (err) { say(acts, err.message); }
                }));
            });
            row.appendChild(acts);
            box.appendChild(row);
        });
    }

    async function openMember(m) {
        state.member = m;
        const list = state.section.__list;
        clear(list);
        list.appendChild(button('Back', '', () => { state.member = null; render(); }));
        list.appendChild(el('h3', 'am-h', 'Notes for ' + m.name));
        try {
            const data = await api('GET', path(m.user_id, '/rows'));
            sensitiveSwitches(m, list);
            addForm(m, data.categories || [], list);
            if (!(data.rows || []).length) list.appendChild(el('p', 'am-note', 'No notes yet.'));
            if (data.hidden > 0) list.appendChild(el('p', 'am-note', data.hidden + ' more in a category that is switched off. Turn it on to see or remove them.'));
            (data.rows || []).forEach((r) => list.appendChild(noteRow(m, r)));
            if (data.held > 0) await heldNotes(m, list);
        } catch (err) {
            say(list, err.message);
        }
    }

    async function loadMembers() {
        try {
            state.members = (await api('GET', BASE + '/members')).members || [];
        } catch (_e) {
            state.members = null;                        // not a curator (403): that part never appears
        }
    }

    function render() {
        const list = state.section.__list;
        clear(list);
        renderMine(list);
        renderMembers(list);
    }

    async function open() {
        if (!state.section || !state.me) return;
        clear(state.section);
        const head = el('div', 'am-head');
        head.appendChild(el('h2', 'am-title', 'Family memories'));
        head.appendChild(button('Close', 'am-close', close));
        state.section.appendChild(head);
        const list = el('div', 'am-list');
        state.section.appendChild(list);
        state.section.__list = list;
        state.section.hidden = false;
        await loadMembers();
        render();
    }

    function close() {
        if (!state.section) return;
        clear(state.section);
        state.section.__list = null;
        state.section.hidden = true;
        state.members = null;
        state.member = null;
    }

    async function init(doc) {
        state.doc = doc || root.document;
        state.section = state.doc.getElementById('curation');
        state.trigger = state.doc.getElementById('curationOpen');
        if (!state.section) return false;
        try {
            await reloadMe();
        } catch (_e) {
            state.me = null;                             // flag off (404) or signed out: nothing appears
            return false;
        }
        if (state.trigger) {
            state.trigger.hidden = false;
            state.trigger.addEventListener('click', open);
        }
        return true;
    }

    root.ZoeCuration = { init: init, open: open, close: close };
})(typeof window !== 'undefined' ? window : this);
