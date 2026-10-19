/**
 * Pendant page (desktop / phone): sync status, "Review your day", "Voices to name", recordings with per-line tagging, voices
 * Zoe knows.
 *
 * Global `ZoePendant` ({ init, api, LABELS }). Loaded by pendant.html AFTER js/auth.js, so `fetch` is the wrapped one that
 * attaches the member's session. Every route is the member's OWN data (routers/omi_recordings.py + routers/omi_voices.py +
 * routers/omi_day_review.py): the page never names a member, never builds a credential and reads no storage.
 *
 * Everything the server supplies (transcript lines, names, labels) goes into the DOM through textContent only; this file
 * never assigns innerHTML. Pinned by dist/test_pendant_page.js (CI via services/zoe-data/tests/test_pendant_page_harness.py).
 */
(function (root) {
    'use strict';

    const LABELS = {
        you: 'You',
        someone_else: 'Someone else',
        not_sure: 'Not sure',
        unverified: 'Not checked',
        not_a_person: 'Not a person',
        person: 'Tagged',
    };
    const NOT_PERSON = [['tv', 'TV'], ['radio', 'Radio'], ['zoe', 'Zoe'], ['media', 'Other media']];
    const WARNINGS = {
        no_sync: 'has not synced for a while',
        ring_fill: 'storage is filling up - bring it near the panel',
        overwrite: 'overwrote older audio before it synced',
        battery_low: 'battery is low',
        bridge_silent: 'the panel bridge has gone quiet',
    };

    /* "Review your day": the groups the owner sees, in order; every other kind goes under "Other things Zoe noticed". */
    const REVIEW_GROUPS = [
        ['person_fact', 'About your people'],
        ['person_relation', 'Relationships'],
        ['follow_up', 'Follow-ups and open loops'],
        ['other', 'Other things Zoe noticed'],
    ];
    const KIND_LABEL = {
        calendar_event: 'Calendar', list_item: 'List', reminder: 'Reminder', follow_up: 'Follow-up', person: 'New person',
        owner_fact: 'About you', person_fact: 'About a person', person_relation: 'Relationship',
    };
    const FIELD_LABEL = { title: 'What', date: 'Date', time: 'Time', item: 'Item', list: 'List', name: 'Name',
        relation: 'Relationship to you', fact: 'Fact' };
    /* what the server's refusal codes mean to the owner (omi_review_apply.ApplyError); unknown codes get a plain fallback */
    const REVIEW_ERRORS = {
        held_conflict: 'This contradicts something you approved before.',
        edit_unsupported: 'Zoe can only keep what the quote supports. Keep your edit to what was said.',
        edit_vague: 'That is too vague to keep. Say what the fact is.',
        edit_not_durable: 'That sounds like a passing remark, not something lasting.',
        edit_identity: 'Zoe does not keep names, addresses or identity details from the pendant.',
        sensitive: 'Zoe does not keep that kind of detail from the pendant.',
        distress: 'Zoe will not keep this one.',
        child: 'Zoe does not keep details about children.',
        shared_person: 'That person is shared with the family, so Zoe will not add this from your pendant.',
        person_gone: 'That person is no longer in your people.',
        forgotten: 'You asked Zoe to forget this, so it is gone.',
        erased: 'This was erased while saving, so nothing was kept.',
        not_stored: 'Zoe could not keep it (it may already be known, or memory is switched off for you).',
        needs_date: 'Add a date first: tap Edit.',
        in_progress: 'Already saving this one.',
        already_dismissed: 'You already skipped this one.',
        forget_ledger_unavailable: 'Zoe could not check this right now. Try again in a minute.',
        bad_date: 'That date was not understood.',
        bad_time: 'That time was not understood.',
        bad_list: 'Pick the shopping or tasks list.',
    };
    const CONFLICT_ACTIONS = { keep_existing: 'Keep what I had', edit: 'Change this one', retry: 'Try again' };

    const state = { people: [], peopleById: {}, doc: null, open: {}, audio: null, audioUrl: null, voiceCards: new Map(), fieldSeq: 0 };

    function el(tag, cls, text) {
        const n = state.doc.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function button(text, cls, onClick) {
        const b = el('button', cls ? 'pb ' + cls : 'pb', text);
        b.setAttribute('type', 'button');
        b.addEventListener('click', onClick);
        return b;
    }

    function clear(n) {
        while (n.firstChild) n.removeChild(n.firstChild);
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
            const err = new Error('signed_out');
            err.status = 401;
            throw err;
        }
        let data = null;
        try { data = await res.json(); } catch (_e) { data = null; }
        if (!res.ok) {
            const err = new Error((data && (data.detail || data.error)) || ('HTTP ' + res.status));
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
    }

    function ago(iso) {
        if (!iso) return 'never';
        const s = (Date.now() - new Date(iso).getTime()) / 1000;
        if (!isFinite(s)) return 'unknown';
        if (s < 90) return 'just now';
        if (s < 5400) return Math.round(s / 60) + ' min ago';
        if (s < 129600) return Math.round(s / 3600) + ' h ago';
        return Math.round(s / 86400) + ' days ago';
    }

    function mmss(ms) {
        const s = Math.max(0, Math.round((ms || 0) / 1000));
        return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    }

    function toast(msg, bad) {
        const t = state.doc.getElementById('pendantToast');
        if (!t) return;
        t.textContent = msg;
        t.className = 'toast show' + (bad ? ' bad' : '');
        if (root.setTimeout) root.setTimeout(() => { t.className = 'toast'; }, 3500);
    }

    function failed(e) {
        if (e && e.status === 409) toast(e.message, true);
        else if (e && e.status === 404) toast('That changed since the page loaded - refreshing.', true);
        else toast('That did not save: ' + ((e && e.message) || 'error'), true);
    }

    async function loadPeople() {
        try {
            const d = await api('GET', '/api/omi/voices/people');
            state.people = (d && d.people) || [];
        } catch (_e) {
            state.people = [];
        }
        state.peopleById = {};
        state.people.forEach(p => { state.peopleById[p.id] = p.name; });
    }

    /* "Who is this?" - pick one of my people or type a name. onDone({person_id} | {name}). */
    function whoPicker(onDone, onCancel) {
        const box = el('div', 'who');
        const input = el('input', 'who-in');
        input.setAttribute('type', 'text');
        input.setAttribute('placeholder', 'Their name');
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('maxlength', '80');
        const chips = el('div', 'chips');
        const fill = () => {
            clear(chips);
            const q = String(input.value || '').trim().toLowerCase();
            state.people
                .filter(p => !q || String(p.name).toLowerCase().indexOf(q) !== -1)
                .slice(0, 12)
                .forEach(p => chips.appendChild(button(p.name, 'chip', () => onDone({ person_id: p.id }))));
        };
        input.addEventListener('input', fill);
        fill();
        const row = el('div', 'row');
        row.appendChild(button('Save', 'primary', () => {
            const name = String(input.value || '').trim();
            if (!name) { toast('Type a name or pick someone.', true); return; }
            const exact = state.people.filter(p => String(p.name).trim().toLowerCase() === name.toLowerCase());
            onDone(exact.length === 1 ? { person_id: exact[0].id } : { name });
        }));
        row.appendChild(button('Cancel', 'ghost', onCancel));
        box.appendChild(input);
        box.appendChild(chips);
        box.appendChild(row);
        return box;
    }

    /* A few seconds of that voice: fetched with the member's session (an <audio src> cannot carry it), played from a blob URL. */
    // a 0-sample WAV: played synchronously inside the tap so iOS Safari unlocks the element for the real clip after the fetch
    const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';

    function play(segmentId, host) {
        // SYNCHRONOUS part, still inside the click: iOS only lets media start from the gesture itself, and the fetch below
        // would lose it. Unlock (or reuse) the one element now; the clip is swapped in when it arrives.
        if (!state.audio) state.audio = new root.Audio();
        try {
            state.audio.src = SILENT_WAV;
            const p = state.audio.play();
            if (p && p.catch) p.catch(() => {});
        } catch (_e) { /* the fallback below still offers native controls */ }
        return fetchAndPlay(segmentId, host);
    }

    async function fetchAndPlay(segmentId, host) {
        let res;
        try {
            res = await root.fetch('/api/omi/segments/' + encodeURIComponent(segmentId) + '/clip', { method: 'GET', headers: {} });
        } catch (_e) {
            toast('Sign in again to play this.', true);
            return;
        }
        if (!res.ok) {
            toast(res.status === 404 ? 'The audio for this one is no longer kept.' : 'Could not play it (' + res.status + ').', true);
            return;
        }
        let url;
        try {
            if (state.audioUrl && root.URL && root.URL.revokeObjectURL) root.URL.revokeObjectURL(state.audioUrl);
            url = state.audioUrl = root.URL.createObjectURL(await res.blob());
            state.audio.pause();
            state.audio.src = url;
            await state.audio.play();
        } catch (_e) {
            // the browser still refused (gesture lost): give it native controls, which play on the next tap
            if (url && host) {
                const a = el('audio', 'clip');
                a.setAttribute('controls', '');
                a.setAttribute('src', url);
                host.appendChild(a);
                toast('Tap play on the clip below.');
            } else {
                toast('This browser would not play the clip.', true);
            }
        }
    }

    function notPersonRow(onKind) {
        const row = el('div', 'row');
        row.appendChild(el('span', 'hint', 'Not a person:'));
        NOT_PERSON.forEach(([kind, text]) => row.appendChild(button(text, 'small', () => onKind(kind))));
        return row;
    }

    /* ── status ───────────────────────────────────────────── */
    async function renderStatus() {
        const host = state.doc.getElementById('pendantStatus');
        clear(host);
        let health = null, recs = null, voices = null;
        try {
            [health, recs, voices] = await Promise.all([
                api('GET', '/api/omi/health'),
                api('GET', '/api/omi/recordings?limit=40'),
                api('GET', '/api/omi/voices'),
            ]);
        } catch (e) {
            if (e.status === 404) host.appendChild(el('p', 'empty', 'Pendant sync is switched off on Zoe.'));
            else if (e.status === 403) host.appendChild(el('p', 'empty', 'Only the pendant\'s owner can see this page. Sign in as them.'));
            else if (e.status === 401) host.appendChild(el('p', 'empty', 'Sign in to see your pendant.'));
            else host.appendChild(el('p', 'empty', 'Could not load your pendant: ' + e.message));
            return null;
        }
        const pendants = (health && health.pendants) || [];
        if (!pendants.length) host.appendChild(el('p', 'line', 'No pendant has reported yet. It syncs when it is near the panel.'));
        pendants.forEach(p => {
            const name = p.label || 'Pendant';
            const bits = ['last synced ' + ago(p.last_sync_at)];
            if (p.battery && p.battery.pct !== null && p.battery.pct !== undefined) bits.push('battery ' + p.battery.pct + '%');
            if (p.ring && p.ring.fill_pct !== null && p.ring.fill_pct !== undefined) bits.push('storage ' + p.ring.fill_pct + '% full');
            host.appendChild(el('p', 'line', name + ': ' + bits.join(' · ')));
            (p.warnings || []).forEach(w => host.appendChild(el('p', 'line warn', name + ' ' + (WARNINGS[w.kind] || w.kind))));
        });
        const t = (recs && recs.transcription) || {};
        if (t.backlog || t.transcribing) {
            let msg = (t.backlog || 0) + ' recording(s) waiting to be transcribed';
            if (t.deferred && t.reason) msg += ' (paused: ' + String(t.reason).replace(/_/g, ' ') + ')';
            host.appendChild(el('p', 'line', msg + '.'));
        }
        const mode = voices && voices.speaker_tag;
        if (mode === 'off') host.appendChild(el('p', 'line warn', 'Splitting recordings into voices is OFF, so new recordings are not tagged yet.'));
        else if (voices && !voices.feature_on) host.appendChild(el('p', 'line', 'Voices are split per line. Grouping unknown voices is off, so tag lines one at a time below.'));
        else if (voices && !voices.learning_enabled) host.appendChild(el('p', 'line', 'You switched off learning voices. Tags are labels only.'));
        return { recs, voices };
    }

    /* ── voices to name ───────────────────────────────────── */
    async function renderUnknown() {
        const host = state.doc.getElementById('pendantUnknown');
        clear(host);
        state.voiceCards = new Map();
        let list = [];
        try {
            list = ((await api('GET', '/api/omi/voices/unknown')) || {}).voices || [];
        } catch (e) {
            host.appendChild(el('p', 'empty', 'Could not load voices: ' + e.message));
            return;
        }
        if (!list.length) {
            host.appendChild(el('p', 'empty', 'No new voices to name right now.'));
            return;
        }
        list.forEach(v => host.appendChild(voiceCard(v)));
    }

    function voiceCard(v) {
        const card = el('div', 'card voice');
        const head = el('h3', null, v.name);
        head.setAttribute('tabindex', '-1');             // "Name this voice" in the day review moves focus here
        card.appendChild(head);
        state.voiceCards.set(v.id, card);
        card.appendChild(el('p', 'meta', 'Heard ' + v.segments + ' time(s) in ' + v.recordings + ' recording(s) over ' + v.days + ' day(s)'));
        (v.samples || []).forEach(s => {
            const q = el('blockquote', 'sample', s.text ? '"' + s.text + '"' : '(no words)');
            q.appendChild(el('span', 'at', ' ' + when(s.started_at)));
            if (s.has_audio) q.appendChild(button('▶ Play', 'small play', () => play(s.segment_id, q)));
            card.appendChild(q);
        });
        const actions = el('div', 'row');
        const done = async (p) => {
            try { await p; } catch (e) { failed(e); }
            await refresh();
        };
        actions.appendChild(button('That\'s me', '', () => done(api('POST', '/api/omi/voices/unknown/' + encodeURIComponent(v.id) + '/me'))));
        actions.appendChild(button('Name them', 'primary', () => {
            actions.hidden = true;
            const picker = whoPicker(
                (body) => done(api('POST', '/api/omi/voices/unknown/' + encodeURIComponent(v.id) + '/tag', body).then(r => {
                    if (r && r.learning === 'learning') toast('Got it. I\'ll learn their voice from these lines, only for your recordings.');
                    else toast('Tagged.');
                })),
                () => { card.removeChild(picker); actions.hidden = false; });
            card.appendChild(picker);
        }));
        card.appendChild(actions);
        card.appendChild(notPersonRow((kind) => done(api('POST', '/api/omi/voices/unknown/' + encodeURIComponent(v.id) + '/not-a-person', { kind }))));
        return card;
    }

    /* ── recordings + lines ───────────────────────────────── */
    function lineLabel(seg) {
        if (seg.label === 'person') return state.peopleById[seg.tagged_person_id] || 'Tagged person';
        if (seg.label === 'not_a_person') return 'Not a person (' + seg.not_person + ')';
        return LABELS[seg.label] || 'Not checked';
    }

    function segmentRow(seg, reload, hasAudio) {
        const row = el('div', 'seg');
        const head = el('div', 'seg-head');
        head.appendChild(el('span', 'chip lab ' + (seg.label || 'unverified'), lineLabel(seg)));
        if (seg.source === 'confirmed') head.appendChild(el('span', 'hint', 'you said'));
        head.appendChild(el('span', 'at', mmss(seg.start_ms)));
        row.appendChild(head);
        row.appendChild(el('p', 'seg-text', seg.text || '(no words)'));
        if (hasAudio) head.appendChild(button('▶ Play', 'small play', () => play(seg.id, row)));
        const done = async (p) => {
            try { await p; } catch (e) { failed(e); }
            await reload();
        };
        const sid = encodeURIComponent(seg.id);
        if (seg.suggested_person_id && state.peopleById[seg.suggested_person_id]) {
            const sug = el('div', 'row suggest');
            sug.appendChild(el('span', 'hint', 'Sounds like ' + state.peopleById[seg.suggested_person_id] + '?'));
            sug.appendChild(button('Yes', 'small primary', () => done(api('POST', '/api/omi/segments/' + sid + '/suggestion', { answer: 'yes' }))));
            sug.appendChild(button('No', 'small', () => done(api('POST', '/api/omi/segments/' + sid + '/suggestion', { answer: 'no' }))));
            row.appendChild(sug);
        }
        const acts = el('div', 'row');
        // a line marked TV / media is excluded until the mark is cleared, so a mistaken mark can always be undone. Me / Not me
        // clear it IN THE SAME request (two would let held audio be released between them); Who? clears it, then tags.
        const unmark = () => (seg.not_person ? api('PATCH', '/api/omi/segments/' + sid, { not_person: null }) : Promise.resolve());
        const label = (who) => api('PATCH', '/api/omi/segments/' + sid, seg.not_person ? { not_person: null, owner_label: who } : { owner_label: who });
        acts.appendChild(button('Me', 'small', () => done(label('you'))));
        acts.appendChild(button('Not me', 'small', () => done(label('other'))));
        acts.appendChild(button('Who?', 'small primary', () => {
            acts.hidden = true;
            const picker = whoPicker(
                (body) => done(unmark().then(() => api('POST', '/api/omi/segments/' + sid + '/tag', body))),
                () => { row.removeChild(picker); acts.hidden = false; });
            row.appendChild(picker);
        }));
        if (seg.not_person) acts.appendChild(button('Not media', 'small ghost', () => done(unmark())));
        else acts.appendChild(button('TV / media', 'small ghost', () => done(api('PATCH', '/api/omi/segments/' + sid, { not_person: 'tv' }))));
        row.appendChild(acts);
        return row;
    }

    async function renderDetail(rec, body) {
        clear(body);
        body.appendChild(el('p', 'meta', 'Loading…'));
        let d;
        try {
            d = await api('GET', '/api/omi/recordings/' + encodeURIComponent(rec.id));
        } catch (e) {
            clear(body);
            body.appendChild(el('p', 'empty', 'Could not load it: ' + e.message));
            return;
        }
        clear(body);
        const segs = (d && d.segments) || [];
        if (segs.length) {
            const reload = async () => { await loadPeople(); await renderDetail(rec, body); await renderUnknown(); };
            segs.forEach(s => body.appendChild(segmentRow(s, reload, !!d.has_audio)));
            return;
        }
        if (d && d.status === 'done') {
            body.appendChild(el('p', 'hint', 'Transcribed before voices were split, so its lines cannot be tagged.'));
            body.appendChild(el('p', 'seg-text', d.transcript || '(no words)'));
        } else {
            body.appendChild(el('p', 'hint', d && d.status === 'empty_transcript' ? 'No words were heard in this one.' : 'Not transcribed yet.'));
        }
    }

    function recordingRow(rec) {
        const item = el('div', 'card rec');
        const head = button('', 'rec-head', () => {
            state.open[rec.id] = !state.open[rec.id];
            body.hidden = !state.open[rec.id];
            if (state.open[rec.id]) renderDetail(rec, body);
        });
        head.appendChild(el('span', 'rec-when', when(rec.started_at) || 'Unknown time'));
        head.appendChild(el('span', 'rec-len', Math.max(1, Math.round((rec.duration_s || 0) / 60)) + ' min'));
        const st = rec.status === 'done' ? (rec.speaker_state === 'tagged' ? 'voices split' : 'text only')
            : rec.status === 'empty_transcript' ? 'no words' : rec.status;
        head.appendChild(el('span', 'chip st ' + String(rec.status), st));
        const body = el('div', 'rec-body');
        body.hidden = !state.open[rec.id];
        item.appendChild(head);
        item.appendChild(body);
        if (state.open[rec.id]) renderDetail(rec, body);
        return item;
    }

    function renderRecordings(recs) {
        const host = state.doc.getElementById('pendantRecordings');
        clear(host);
        const list = (recs && recs.recordings) || [];
        if (!list.length) {
            host.appendChild(el('p', 'empty', 'No recordings yet.'));
            return;
        }
        list.forEach(r => host.appendChild(recordingRow(r)));
    }

    /* ── voices Zoe knows ─────────────────────────────────── */
    function renderKnown(voices) {
        const host = state.doc.getElementById('pendantKnown');
        clear(host);
        const list = (voices && voices.voices) || [];
        if (!list.length) {
            host.appendChild(el('p', 'empty', 'Zoe has not learned anyone\'s voice yet.'));
            return;
        }
        list.forEach(v => {
            const row = el('div', 'card known');
            row.appendChild(el('h3', null, v.name));
            row.appendChild(el('p', 'meta', v.segments + ' line(s) from ' + v.recordings + ' recording(s) · ' + v.status));
            const acts = el('div', 'row');
            const forget = button('Forget this voice', 'small ghost', () => {
                acts.hidden = true;
                const ask = el('div', 'row');
                ask.appendChild(el('span', 'hint', 'Forget ' + v.name + '\'s voice? Your tags stay.'));
                ask.appendChild(button('Forget it', 'small danger', async () => {
                    try { await api('DELETE', '/api/omi/voices/' + encodeURIComponent(v.id)); } catch (e) { failed(e); }
                    await refresh();
                }));
                ask.appendChild(button('Keep', 'small', () => { row.removeChild(ask); acts.hidden = false; }));
                row.appendChild(ask);
            });
            acts.appendChild(forget);
            row.appendChild(acts);
            host.appendChild(row);
        });
    }

    /* ── review your day (routers/omi_day_review.py) ─────────
     * The owner's pending day-review candidates, grouped; each shows the proposed item, WHO said it, the exact quote with when and
     * which recording, and Play while the line's audio is kept. Approve / Skip / Edit call the day-review routes and nothing else;
     * what an edit may change comes from the server (`editable`), which also re-checks the edit against the quote. A held conflict
     * shows what it contradicts and only the server's `options`. Ids live in closures: never in a URL, the DOM or the text. */
    const REVIEW_BASE = '/api/omi/day-review/candidates';

    function via() {
        try {
            if (root.matchMedia && root.matchMedia('(pointer: coarse)').matches) return 'phone';
        } catch (_e) { /* no media queries: a desktop */ }
        return 'desktop';
    }

    function reviewError(e) {
        const code = String((e && e.message) || '');
        if (REVIEW_ERRORS[code]) return REVIEW_ERRORS[code];
        if (code.indexOf('edit_') === 0) return REVIEW_ERRORS.edit_unsupported;
        if (e && e.status === 404) return 'That one is gone (it changed since the page loaded).';
        return 'That did not save. Try again.';
    }

    function dayLabel(day) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day || ''))) return '';
        const d = new Date(day + 'T12:00:00');
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
    }

    function relWord(rel) {
        return String(rel || '').replace(/^(my|your)\s+/i, '');
    }

    function itemText(c) {
        const p = c.payload || {};
        const when = [p.date, p.time].filter(Boolean).join(' ');
        switch (c.kind) {
            case 'person_fact': return (p.name || 'Someone') + ': ' + (p.fact || '');
            case 'person_relation': return (p.name || 'Someone') + ' is your ' + relWord(p.relation);
            case 'owner_fact': return p.fact || '';
            case 'person': return (p.name || '') + (p.relation ? ' (your ' + relWord(p.relation) + ')' : '');
            case 'list_item': return (p.item || '') + ' (' + (p.list || 'shopping') + ' list)';
            case 'calendar_event':
            case 'reminder': return (p.title || '') + (when ? ' (' + when + ')' : '');
            default: return p.title || p.item || p.name || p.fact || '';
        }
    }

    /* WHO said the quoted line: Me / the person the owner tagged / someone. Never guessed beyond what the server recorded. */
    function whoSaid(c, ev) {
        const p = c.payload || {};
        const sp = (ev && ev.speaker) || c.speaker;
        if (sp === 'you') return 'Me';
        const pid = ev && ev.person_id;
        if (pid) return state.peopleById[pid] || (p.person_id === pid && p.name) || 'Someone you tagged';
        if (c.kind === 'person_fact' && p.stated_by === 'stated_by_subject' && p.name) return p.name;
        if (sp === 'someone_else') return 'Someone';
        return 'Not sure who';
    }

    function quoteBlock(c, ev) {
        const q = el('figure', 'rv-quote');
        q.appendChild(el('blockquote', 'sample', '"' + (ev.quote || '') + '"'));
        const bits = [dayLabel(c.day_local)];
        if (ev.at) bits.push('recording from ' + ev.at);
        if (typeof ev.offset_ms === 'number') bits.push(mmss(ev.offset_ms) + ' in');
        if (ev.clock === 'estimated') bits.push('time is a guess');
        const cap = el('figcaption', 'meta');
        cap.appendChild(el('strong', 'rv-who', whoSaid(c, ev)));
        cap.appendChild(el('span', null, ' · ' + bits.filter(Boolean).join(' · ')));
        q.appendChild(cap);
        if (ev.segment_id && ev.audio_kept) {
            const segId = ev.segment_id;
            const b = button('▶ Play the line', 'small play', () => play(segId, q));
            b.setAttribute('aria-label', 'Play what ' + whoSaid(c, ev) + ' said');
            q.appendChild(b);
        }
        return q;
    }

    function fieldInput(name, value) {
        const id = 'rv-f-' + (++state.fieldSeq);
        const wrap = el('div', 'rv-field');
        const lab = el('label', null, FIELD_LABEL[name] || name);
        lab.setAttribute('for', id);
        let input;
        if (name === 'list') {
            input = el('select', 'who-in');
            [['shopping', 'Shopping'], ['tasks', 'Tasks']].forEach(([v, t]) => {
                const o = el('option', null, t);
                o.setAttribute('value', v);
                if (v === value) o.setAttribute('selected', '');
                input.appendChild(o);
            });
        } else {
            input = el('input', 'who-in');
            input.setAttribute('type', name === 'date' ? 'date' : name === 'time' ? 'time' : 'text');
            input.setAttribute('maxlength', '160');
            input.setAttribute('autocomplete', 'off');
        }
        input.setAttribute('id', id);
        input.setAttribute('name', name);
        input.value = value === undefined || value === null ? '' : String(value);
        wrap.appendChild(lab);
        wrap.appendChild(input);
        return { wrap, input };
    }

    function reviewCard(c, ctx) {
        const card = el('article', 'card rv-item' + (c.conflict ? ' rv-conflict' : ''));
        const p = c.payload || {};
        card.appendChild(el('span', 'chip', KIND_LABEL[c.kind] || 'Other'));
        const title = el('h4', 'rv-text', itemText(c));
        title.setAttribute('tabindex', '-1');
        card.appendChild(title);
        (c.evidence || []).slice(0, 2).forEach(ev => card.appendChild(quoteBlock(c, ev)));

        const conflict = c.conflict;
        if (conflict) {
            const box = el('div', 'rv-warn');
            let msg;
            if (conflict.existing && c.kind === 'person_relation') {
                msg = 'You already have ' + (p.name || 'them') + ' as "' + conflict.existing + '". This says "' + relWord(p.relation) + '".';
            } else if (conflict.existing) {
                msg = 'This contradicts what you approved before: "' + conflict.existing + '"';
            } else if ((conflict.options || []).indexOf('retry') !== -1) {
                msg = 'This clashed with something you approved before, which is no longer there. You can try again.';
            } else {
                msg = 'This contradicts something you approved before.';
            }
            box.appendChild(el('p', 'line', msg));
            card.appendChild(box);
        } else if (c.write_error) {
            card.appendChild(el('p', 'line warn', 'Last time it did not save: ' + reviewError({ message: c.write_error })));
        }

        const acts = el('div', 'row rv-acts');
        const label = itemText(c);
        const path = REVIEW_BASE + '/' + encodeURIComponent(c.id);
        /* one decision: true when the server took it (the card goes), false when it refused (the card stays, says why) */
        const decide = async (what, body) => {
            acts.hidden = true;
            try {
                if (what === 'skip') await api('POST', path + '/dismiss');
                else await api('POST', path + '/approve', body);
            } catch (e) {
                toast(reviewError(e), true);
                // decided elsewhere, held, gone or forgotten: the server's current view replaces this card
                if (e && (e.status === 409 || e.status === 410 || e.status === 404)) await ctx.reload(c.id);
                else acts.hidden = false;
                return false;
            }
            toast(what === 'skip' ? 'Skipped.' : 'Kept.');
            ctx.removed(card);
            return true;
        };
        const editable = Array.isArray(c.editable) ? c.editable : [];
        const openEdit = () => {
            acts.hidden = true;
            const form = el('form', 'rv-edit');
            form.setAttribute('novalidate', '');
            if (c.kind === 'person_fact' || c.kind === 'owner_fact') {
                form.appendChild(el('p', 'hint', 'Keep it to what was said: Zoe checks your edit against the quote.'));
            }
            const inputs = editable.map(f => {
                const fi = fieldInput(f, p[f]);
                form.appendChild(fi.wrap);
                return [f, fi.input];
            });
            const row = el('div', 'row');
            row.appendChild(button('Save and keep', 'primary', async () => {
                const fields = {};
                inputs.forEach(([f, input]) => {
                    const v = String(input.value || '').trim();
                    if (v !== String(p[f] === undefined || p[f] === null ? '' : p[f])) fields[f] = v;
                });
                form.hidden = true;
                const ok = await decide('approve', Object.keys(fields).length ? { fields, via: via() } : { via: via() });
                if (!ok && form.parentNode === card) {        // a refused edit (422) keeps what the owner typed
                    form.hidden = false;
                    acts.hidden = true;
                }
            }));
            row.appendChild(button('Cancel', 'ghost', () => { card.removeChild(form); acts.hidden = false; }));
            form.appendChild(row);
            form.addEventListener('submit', (ev) => { if (ev && ev.preventDefault) ev.preventDefault(); });
            card.appendChild(form);
            if (inputs.length && inputs[0][1].focus) inputs[0][1].focus();
        };
        const add = (text, cls, aria, fn) => {
            const b = button(text, cls, fn);
            b.setAttribute('aria-label', aria + ': ' + label);
            acts.appendChild(b);
        };
        if (conflict) {
            (conflict.options || []).forEach(o => {
                if (o === 'keep_existing') add(CONFLICT_ACTIONS[o], 'primary', 'Keep what you had, skip', () => decide('skip'));
                else if (o === 'edit' && editable.length) add(CONFLICT_ACTIONS[o], '', 'Change', openEdit);
                else if (o === 'retry') add(CONFLICT_ACTIONS[o], '', 'Try again', () => decide('approve', { via: via() }));
            });
        } else {
            add('Approve', 'primary', 'Approve', () => decide('approve', { via: via() }));
            if (editable.length) add('Edit', '', 'Edit', openEdit);
            add('Skip', 'ghost', 'Skip', () => decide('skip'));
        }
        card.appendChild(acts);
        return card;
    }

    function voicesToNameBlock(voices) {
        const box = el('div', 'card rv-voices');
        box.appendChild(el('h3', null, 'New voices to name'));
        box.appendChild(el('p', 'hint', 'Name them so Zoe can tell who said what next time.'));
        voices.slice(0, 3).forEach(v => {
            const row = el('div', 'rv-voice');
            row.appendChild(el('p', 'line', v.name + ' · heard ' + v.segments + ' time(s) over ' + v.days + ' day(s)'));
            const s = v.sample || {};
            if (s.text) {
                const q = el('blockquote', 'sample', '"' + s.text + '"');
                if (s.segment_id && s.has_audio) {
                    const segId = s.segment_id;
                    q.appendChild(button('▶ Play', 'small play', () => play(segId, q)));
                }
                row.appendChild(q);
            }
            const vid = v.id;
            const go = button('Name this voice', 'primary', () => {
                const card = state.voiceCards.get(vid);
                const target = card ? card.firstChild : state.doc.getElementById('pendantUnknownHead');
                if (target && target.scrollIntoView) target.scrollIntoView({ block: 'start', behavior: 'smooth' });
                if (target && target.focus) target.focus();
                if (card) card.className = 'card voice rv-flash';
            });
            go.setAttribute('aria-label', 'Name ' + v.name + ' in Voices to name');
            row.appendChild(go);
            box.appendChild(row);
        });
        return box;
    }

    async function renderReview(focusCid) {
        const host = state.doc.getElementById('pendantReview');
        if (!host) return;
        clear(host);
        let d;
        try {
            d = await api('GET', REVIEW_BASE + '?status=pending&limit=100');
        } catch (e) {
            if (e.status === 404) host.appendChild(el('p', 'empty', 'Zoe is not reviewing your days yet (it is switched off).'));
            else if (e.status === 403) host.appendChild(el('p', 'empty', 'Only you can review your own day. Sign in as yourself.'));
            else if (e.status === 401) host.appendChild(el('p', 'empty', 'Sign in to review your day.'));
            else host.appendChild(el('p', 'empty', 'Could not load your day review: ' + e.message));
            return;
        }
        const cands = ((d && d.candidates) || []).filter(c => c && c.status === 'pending');
        const voices = (d && d.voices_to_name) || [];
        const summary = el('p', 'line rv-summary');
        summary.setAttribute('aria-live', 'polite');
        summary.setAttribute('tabindex', '-1');
        host.appendChild(summary);
        let left = cands.length;
        const say = () => {
            summary.textContent = left ? left + ' thing' + (left === 1 ? '' : 's') + ' to review. Nothing is kept until you approve it.'
                : 'All caught up. Nothing from your pendant is waiting.';
        };
        say();
        const cards = new Map();
        const ctx = {
            reload: (cid) => renderReview(cid),
            removed: (card) => {
                // the decided card goes; focus moves to the next item's text (or the summary), so a screen reader keeps its place
                const group = card.parentNode;
                let next = null;
                if (group) {
                    const kids = Array.prototype.slice.call(group.children);
                    next = kids[kids.indexOf(card) + 1] || null;
                    group.removeChild(card);
                    if (group.children.length <= 1 && group.parentNode) {          // only its heading left
                        group.parentNode.removeChild(group);
                        next = null;
                    }
                }
                left = Math.max(0, left - 1);
                say();
                const to = next ? next.children[1] : summary;
                if (to && to.focus) to.focus();
            },
        };
        REVIEW_GROUPS.forEach(([kind, title]) => {
            const mine = cands.filter(c => (kind === 'other'
                ? !REVIEW_GROUPS.some(([k]) => k !== 'other' && k === c.kind) : c.kind === kind));
            if (!mine.length) return;
            const group = el('section', 'rv-group');
            group.appendChild(el('h3', null, title + ' (' + mine.length + ')'));
            mine.forEach(c => {
                const card = reviewCard(c, ctx);
                cards.set(c.id, card);
                group.appendChild(card);
            });
            host.appendChild(group);
        });
        if (voices.length) host.appendChild(voicesToNameBlock(voices));
        if (focusCid && cards.has(focusCid)) {
            const t = cards.get(focusCid).children[1];
            if (t && t.focus) t.focus();
        }
    }

    async function refresh() {
        await loadPeople();
        const got = await renderStatus();
        if (!got) {
            ['pendantReview', 'pendantUnknown', 'pendantRecordings', 'pendantKnown'].forEach(id => {
                const n = state.doc.getElementById(id);
                if (n) clear(n);
            });
            return;
        }
        renderRecordings(got.recs);
        renderKnown(got.voices);
        await renderUnknown();
        await renderReview();
    }

    function init(doc) {
        state.doc = doc || root.document;
        state.open = {};
        return refresh();
    }

    root.ZoePendant = { init, api, refresh, LABELS };
})(typeof window !== 'undefined' ? window : globalThis);
