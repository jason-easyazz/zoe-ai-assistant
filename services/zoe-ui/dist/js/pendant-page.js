/**
 * Pendant page (desktop / phone): sync status, "Voices to name", recordings with per-line tagging, voices Zoe knows.
 *
 * Global `ZoePendant` ({ init, api, LABELS }). Loaded by pendant.html AFTER js/auth.js, so `fetch` is the wrapped one that
 * attaches the member's session. Every route is the member's OWN data (routers/omi_recordings.py + routers/omi_voices.py):
 * the page never names a member, never builds a credential and reads no storage.
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

    const state = { people: [], peopleById: {}, doc: null, open: {}, audio: null, audioUrl: null };

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
    async function play(segmentId) {
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
        try {
            if (state.audio) state.audio.pause();
            if (state.audioUrl && root.URL && root.URL.revokeObjectURL) root.URL.revokeObjectURL(state.audioUrl);
            state.audioUrl = root.URL.createObjectURL(await res.blob());
            state.audio = new root.Audio(state.audioUrl);
            await state.audio.play();
        } catch (_e) {
            toast('This browser would not play the clip.', true);
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
        card.appendChild(el('h3', null, v.name));
        card.appendChild(el('p', 'meta', 'Heard ' + v.segments + ' time(s) in ' + v.recordings + ' recording(s) over ' + v.days + ' day(s)'));
        (v.samples || []).forEach(s => {
            const q = el('blockquote', 'sample', s.text ? '"' + s.text + '"' : '(no words)');
            q.appendChild(el('span', 'at', ' ' + when(s.started_at)));
            if (s.has_audio) q.appendChild(button('▶ Play', 'small play', () => play(s.segment_id)));
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
        if (hasAudio) head.appendChild(button('▶ Play', 'small play', () => play(seg.id)));
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
        acts.appendChild(button('Me', 'small', () => done(api('PATCH', '/api/omi/segments/' + sid, { owner_label: 'you' }))));
        acts.appendChild(button('Not me', 'small', () => done(api('PATCH', '/api/omi/segments/' + sid, { owner_label: 'other' }))));
        acts.appendChild(button('Who?', 'small primary', () => {
            acts.hidden = true;
            const picker = whoPicker(
                (body) => done(api('POST', '/api/omi/segments/' + sid + '/tag', body)),
                () => { row.removeChild(picker); acts.hidden = false; });
            row.appendChild(picker);
        }));
        acts.appendChild(button('TV / media', 'small ghost', () => done(api('PATCH', '/api/omi/segments/' + sid, { not_person: 'tv' }))));
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
            const reload = async () => { await renderDetail(rec, body); await renderUnknown(); };
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

    async function refresh() {
        await loadPeople();
        const got = await renderStatus();
        if (!got) {
            ['pendantUnknown', 'pendantRecordings', 'pendantKnown'].forEach(id => clear(state.doc.getElementById(id)));
            return;
        }
        renderRecordings(got.recs);
        renderKnown(got.voices);
        await renderUnknown();
    }

    function init(doc) {
        state.doc = doc || root.document;
        state.open = {};
        return refresh();
    }

    root.ZoePendant = { init, api, refresh, LABELS };
})(typeof window !== 'undefined' ? window : globalThis);
