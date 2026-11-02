/*
 * Panel alarms (ZOE_ALARMS, flag-dark server side) - the touch estate only. Design: docs/architecture/alarms.md.
 *
 * Two surfaces, both drawn with createElement/textContent only (a server string - a label - is never parsed as HTML):
 *   1. THE RING SCREEN. Polls GET /api/alarms/ringing every 2 s. A ringing alarm takes the sleep clock page and a full-screen
 *      animated overlay: TAP ANYWHERE snoozes; the one deliberate control is "Hold to turn off" (press and hold 1 s, a fill
 *      shows progress, releasing early does nothing - and never snoozes). The state lives on the server, so a reload, a voice
 *      turn that reloads the page or a zoe-data restart mid-ring all re-show it. A snooze/off that could not reach the server is
 *      remembered (acks) and retried on every poll, so a dismissed alarm does not ring again from this panel.
 *   2. THE ALARMS TAB on the Timer screen: a list (time, repeat, on/off switch) and a touch editor - time wheels, repeat chips,
 *      tone chips, snooze chips. No keyboard: a label is voice-set ("called gym") or phone-set.
 * With the flag off the server answers 404; the tab stays hidden and the poller checks again only every few minutes.
 */
(function () {
    'use strict';
    if (window.ZoeAlarms) return;

    var POLL_MS = 2000, OFF_RECHECK_MS = 300000, HOLD_MS = 1000, ITEM_H = 64, MUSIC_DEADLINE_MS = 10000;
    var ACK_KEY = 'zoe_alarm_acks';
    var DAYS = [['M', 1], ['T', 2], ['W', 3], ['T', 4], ['F', 5], ['S', 6], ['S', 7]];
    var PRESETS = [['Once', 'once'], ['Every day', 'daily'], ['Weekdays', 'weekdays'], ['Weekends', 'weekends']];
    var host = null;
    var st = { enabled: null, ring: null, skew: 0, pollT: null, holdT: null, musicT: null, toneOn: false, clockT: null, acks: {}, inflight: {}, tones: {} };

    // ---- pure helpers (exported for the node harness) -------------------------------------------------------------------
    function to24h(h12, mi, mer) {
        var h = h12 % 12 + (mer === 'pm' ? 12 : 0);
        return (h < 10 ? '0' : '') + h + ':' + (mi < 10 ? '0' : '') + mi;
    }
    function from24h(hhmm) {
        var h = parseInt(hhmm.slice(0, 2), 10), mi = parseInt(hhmm.slice(3), 10);
        return { h12: h % 12 || 12, mi: mi, mer: h < 12 ? 'am' : 'pm' };
    }
    function clockText(hhmm) {
        var t = from24h(hhmm);
        return t.h12 + ':' + (t.mi < 10 ? '0' : '') + t.mi + ' ' + t.mer;
    }
    function wheelIndex(scrollTop, itemH, n) {
        return Math.max(0, Math.min(n - 1, Math.round(scrollTop / itemH)));
    }
    // chips -> repeat rule: a preset wins; otherwise the chosen weekdays (all seven = daily, none = once).
    function repeatOf(preset, days) {
        if (preset) return preset;
        var d = (days || []).slice().sort(function (a, b) { return a - b; });
        if (!d.length) return 'once';
        return d.length === 7 ? 'daily' : 'days:' + d.join(',');
    }
    function chipsOf(rule) {
        if (rule && rule.indexOf('days:') === 0) {
            return { preset: '', days: rule.slice(5).split(',').map(function (x) { return parseInt(x, 10); }) };
        }
        return { preset: rule || 'once', days: [] };
    }

    // ---- small DOM + api helpers ----------------------------------------------------------------------------------------
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = String(text);
        return n;
    }
    function clear(n) { while (n.children && n.children.length) n.removeChild(n.children[0]); n.textContent = ''; }
    function nowMs() { return Date.now() + st.skew; }
    function loadAcks() { try { st.acks = JSON.parse(window.localStorage.getItem(ACK_KEY) || '{}') || {}; } catch (e) { st.acks = {}; } }
    function saveAcks() { try { window.localStorage.setItem(ACK_KEY, JSON.stringify(st.acks)); } catch (e) { /* private mode */ } }
    function ackKey(a) { return a.id + ':' + a.ring_started_at; }

    // ---- the ring screen ------------------------------------------------------------------------------------------------
    function overlay() {
        var ov = document.getElementById('alarmRing');
        if (ov) return ov;
        ov = el('div', 'alring');
        ov.id = 'alarmRing';
        for (var i = 0; i < 3; i++) ov.appendChild(el('div', 'alpulse p' + i));
        ov.appendChild(el('div', 'alclock'));
        ov.appendChild(el('div', 'allabel'));
        ov.appendChild(el('div', 'alhint'));
        var off = el('button', 'aloff');
        off.appendChild(el('span', 'alfill'));
        off.appendChild(el('span', 'altxt', 'Hold to turn off'));
        ov.appendChild(off);
        // Tap ANYWHERE snoozes - the whole screen is the target (it is hit half-asleep). Turn off is never a tap.
        ov.addEventListener('click', function (e) { if (!e.target.closest || !e.target.closest('.aloff')) snooze(); });
        off.addEventListener('click', function (e) { e.stopPropagation(); });
        off.addEventListener('pointerdown', function (e) { e.stopPropagation(); startHold(); });
        ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (t) { off.addEventListener(t, cancelHold); });
        (document.getElementById('stage') || document.body).appendChild(ov);
        return ov;
    }
    function paintRing() {
        var a = st.ring, ov = overlay();
        if (!a) return;
        ov.querySelector('.allabel').textContent = a.label || '';
        ov.querySelector('.alhint').textContent = a.can_snooze
            ? 'Tap anywhere to snooze ' + a.snooze_minutes + ' min' : 'Hold the button to turn off';
        ov.querySelector('.aloff').classList.toggle('only', !a.can_snooze);
        tickClock();
    }
    function tickClock() {
        var c = document.querySelector('#alarmRing .alclock');
        if (!c) return;
        var d = new Date(nowMs()), h = d.getHours() % 12 || 12, m = d.getMinutes();
        c.textContent = h + ':' + (m < 10 ? '0' : '') + m;
    }
    function startRing(a) {
        st.ring = a;
        if (host.cur && host.cur() !== 'sleep') host.show('sleep');
        paintRing();
        overlay().classList.add('on');
        if (!st.clockT) st.clockT = setInterval(tickClock, 1000);
        st.toneOn = false;
        applySound();
        // A music alarm gets MUSIC_DEADLINE_MS from the ring start to be playing; the check below fires when it is up.
        if (a.sound_kind === 'music') {
            var left = Math.max(0, a.ring_started_at * 1000 + MUSIC_DEADLINE_MS - nowMs());
            st.musicT = setTimeout(applySound, left + 50);
        }
    }
    // AN ALARM IS NEVER SILENT. A tone alarm plays its tone. A music alarm plays music (the server starts it on the panel's speaker and
    // reports music_state) - but if music is not `playing` once the deadline has passed, or the server says `failed` (Music Assistant down,
    // provider needs re-auth, no panel player, unknown playlist), the tone plays. Unknown state (zoe-data restarted mid-ring) counts as not playing.
    function applySound() {
        var a = st.ring, snd = host && host.sound;
        if (!a || !snd) return;
        var music = a.sound_kind === 'music';
        var late = nowMs() >= a.ring_started_at * 1000 + MUSIC_DEADLINE_MS;
        var wantTone = !music || a.music_state === 'failed' || (a.music_state !== 'playing' && late);
        if (music && a.music_state === 'playing' && st.toneOn) { snd.stop(); st.toneOn = false; return; }  // music made it after all
        if (wantTone && !st.toneOn) {
            st.toneOn = true;
            snd.start(music ? 'sunrise' : a.sound_id, function () { return nowMs() / 1000 - a.ring_started_at; }, a.ramp_seconds);
        }
    }
    function stopRing() {
        st.ring = null;
        cancelHold();
        var ov = document.getElementById('alarmRing');
        if (ov) ov.classList.remove('on');
        if (st.musicT) { clearTimeout(st.musicT); st.musicT = null; }
        if (st.clockT) { clearInterval(st.clockT); st.clockT = null; }
        st.toneOn = false;
        if (host && host.sound) host.sound.stop();
    }
    function startHold() {
        cancelHold();
        var f = document.querySelector('#alarmRing .alfill');
        if (f) f.style.transitionDuration = HOLD_MS + 'ms';
        var b = document.querySelector('#alarmRing .aloff');
        if (b) b.classList.add('holding');
        st.holdT = setTimeout(function () { st.holdT = null; turnOff(); }, HOLD_MS);
    }
    function cancelHold() {
        if (st.holdT) { clearTimeout(st.holdT); st.holdT = null; }
        var b = document.querySelector('#alarmRing .aloff');
        if (b) b.classList.remove('holding');
        var f = document.querySelector('#alarmRing .alfill');
        if (f) f.style.transitionDuration = '0ms';
    }
    function answer(kind) {
        var a = st.ring;
        if (!a) return;
        stopRing();  // silence at once; the server confirms (or the ack is retried) after
        st.acks[ackKey(a)] = kind;
        saveAcks();
        sendAck(a.id, a.ring_started_at, kind);
    }
    function sendAck(id, started, kind) {
        var key = id + ':' + started;
        if (st.inflight[key]) return st.inflight[key];  // a poll that lands mid-request must not send the same answer twice
        function settle() { delete st.inflight[key]; }
        st.inflight[key] = host.apiJson('POST', '/api/alarms/' + encodeURIComponent(id) + '/' + kind).then(function () {
            delete st.acks[key];
            saveAcks();
            settle();
        }).catch(function (e) {
            if (/http 409/.test(String(e && e.message))) { delete st.acks[key]; saveAcks(); }  // not ringing any more / no snoozes left
            settle();
        });
        return st.inflight[key];
    }
    function snooze() {
        var a = st.ring;
        if (!a || !a.can_snooze) return;  // out of snoozes: only the hold ends it
        answer('snooze');
    }
    function turnOff() { answer('dismiss'); }

    function poll() {
        host.apiGet('/api/alarms/ringing').then(function (d) {
            st.enabled = true;
            if (d && d.now) st.skew = d.now * 1000 - Date.now();
            var live = ((d && d.ringing) || []).filter(function (a) { return !st.acks[ackKey(a)]; });
            // retry anything we answered locally that the server still lists as ringing
            ((d && d.ringing) || []).forEach(function (a) {
                var k = st.acks[ackKey(a)];
                if (k) sendAck(a.id, a.ring_started_at, k);
            });
            if (live.length) {
                if (!st.ring || st.ring.id !== live[0].id || st.ring.ring_started_at !== live[0].ring_started_at) startRing(live[0]);
                else { st.ring = live[0]; paintRing(); applySound(); }
            } else if (st.ring) stopRing();  // answered elsewhere, auto-stopped, or edited off
            schedule(POLL_MS);
        }).catch(function (e) {
            if (/http 404/.test(String(e && e.message))) { st.enabled = false; schedule(OFF_RECHECK_MS); } else schedule(POLL_MS * 2);
        });
    }
    function schedule(ms) {
        if (st.pollT) clearTimeout(st.pollT);
        st.pollT = setTimeout(poll, ms);
    }

    // ---- the Alarms tab -------------------------------------------------------------------------------------------------
    function chip(text, on, fn, cls) {
        var b = el('button', 'alchip' + (on ? ' on' : '') + (cls ? ' ' + cls : ''), text);
        b.addEventListener('click', fn);
        return b;
    }
    function makeWheel(labels, index, onChange) {
        var w = el('div', 'alwheel'), t = null, cur = index;
        w.appendChild(el('div', 'alpad'));
        labels.forEach(function (l, i) {
            var it = el('div', 'alitem', l);
            it.addEventListener('click', function () { if (w.scrollTo) w.scrollTo({ top: i * ITEM_H, behavior: 'smooth' }); cur = i; onChange(i); markSel(); });
            w.appendChild(it);
        });
        w.appendChild(el('div', 'alpad'));
        function markSel() { [].forEach.call(w.querySelectorAll('.alitem'), function (n, i) { n.classList.toggle('sel', i === cur); }); }
        w.addEventListener('scroll', function () {
            clearTimeout(t);
            t = setTimeout(function () { cur = wheelIndex(w.scrollTop, ITEM_H, labels.length); onChange(cur); markSel(); }, 90);
        });
        setTimeout(function () { w.scrollTop = index * ITEM_H; markSel(); }, 0);
        return w;
    }
    function openEditor(root, alarm, done) {
        var isNew = !alarm;
        var t = from24h(alarm ? alarm.time_hhmm : '07:00');
        var chips = chipsOf(alarm ? alarm.repeat_rule : 'once');
        var s = { h: t.h12, mi: t.mi, mer: t.mer, preset: chips.preset, days: chips.days.slice(),
                  kind: alarm ? alarm.sound_kind : 'tone', tone: alarm && alarm.sound_kind === 'tone' ? alarm.sound_id : 'sunrise',
                  music: alarm && alarm.sound_kind === 'music' ? alarm.sound_id : '', musicName: alarm && alarm.sound_kind === 'music' ? alarm.sound_name : '',
                  snooze: alarm ? alarm.snooze_minutes : 9 };
        var ed = el('div', 'aled');
        var box = el('div', 'aledbox');
        ed.appendChild(box);
        var wheels = el('div', 'alwheels');
        var hours = [], mins = [];
        for (var h = 1; h <= 12; h++) hours.push(String(h));
        for (var m = 0; m < 60; m++) mins.push((m < 10 ? '0' : '') + m);
        wheels.appendChild(makeWheel(hours, s.h - 1, function (i) { s.h = i + 1; }));
        wheels.appendChild(el('div', 'alcolon', ':'));
        wheels.appendChild(makeWheel(mins, s.mi, function (i) { s.mi = i; }));
        wheels.appendChild(makeWheel(['am', 'pm'], s.mer === 'pm' ? 1 : 0, function (i) { s.mer = i ? 'pm' : 'am'; }));
        box.appendChild(wheels);
        var side = el('div', 'alside');
        box.appendChild(side);
        function section(title) { var r = el('div', 'alrow'); r.appendChild(el('div', 'alk', title)); var c = el('div', 'alchips'); r.appendChild(c); side.appendChild(r); return c; }
        var rep = section('Repeat');
        function drawRep() {
            clear(rep);
            PRESETS.forEach(function (p) { rep.appendChild(chip(p[0], s.preset === p[1], function () { s.preset = p[1]; s.days = []; drawRep(); })); });
            var dr = el('div', 'aldays');
            DAYS.forEach(function (d) {
                dr.appendChild(chip(d[0], s.days.indexOf(d[1]) >= 0, function () {
                    s.preset = '';
                    var i = s.days.indexOf(d[1]);
                    if (i >= 0) s.days.splice(i, 1); else s.days.push(d[1]);
                    if (!s.days.length) s.preset = 'once';
                    drawRep();
                }, 'day'));
            });
            rep.appendChild(dr);
        }
        drawRep();
        var snd = section('Sound');
        function drawSnd() {
            clear(snd);
            Object.keys(st.tones).forEach(function (id) {
                snd.appendChild(chip(st.tones[id], s.kind === 'tone' && s.tone === id, function () {
                    s.kind = 'tone'; s.tone = id; drawSnd(); drawMus(); if (host.sound) host.sound.preview(id);
                }));
            });
        }
        drawSnd();
        // Clock radio: the last thing played, or a playlist from Music Assistant. If it cannot start within 10 s the alarm rings a tone instead.
        var mus = section('Wake to music'), lists = [];
        function drawMus() {
            clear(mus);
            var opts = [['Last played', 'last']].concat(lists.slice(0, 6).map(function (n) { return [n, 'playlist:' + n]; }));
            opts.forEach(function (o) {
                mus.appendChild(chip(o[0], s.kind === 'music' && s.music === o[1], function () {
                    s.kind = 'music'; s.music = o[1]; s.musicName = o[0]; drawSnd(); drawMus();
                }));
            });
        }
        drawMus();
        host.apiGet('/api/music/playlists').then(function (d) {
            lists = ((d && d.playlists) || []).map(function (p) { return p && p.name; }).filter(Boolean);
            drawMus();
        }).catch(function () { /* no Music Assistant: Last played still works, and the tone is the safety net */ });
        var sz = section('Snooze');
        function drawSz() {
            clear(sz);
            [5, 9, 10, 15].forEach(function (n) { sz.appendChild(chip(n + ' min', s.snooze === n, function () { s.snooze = n; drawSz(); })); });
        }
        drawSz();
        var acts = el('div', 'alacts');
        side.appendChild(acts);
        var err = el('div', 'alerr');
        side.appendChild(err);
        function close() { if (ed.parentNode) ed.parentNode.removeChild(ed); }
        acts.appendChild(chip('Cancel', false, close, 'ghost'));
        if (!isNew) {
            acts.appendChild(chip('Delete', false, function () {
                host.apiJson('DELETE', '/api/alarms/' + encodeURIComponent(alarm.id)).then(function () { close(); done(); })
                    .catch(function () { err.textContent = 'Couldn’t delete that.'; });
            }, 'danger'));
        }
        acts.appendChild(chip('Save', true, function () {
            var body = { time_hhmm: to24h(s.h, s.mi, s.mer), repeat: repeatOf(s.preset, s.days), snooze_minutes: s.snooze };
            if (s.kind === 'music' && s.music) { body.sound_kind = 'music'; body.sound_id = s.music; body.sound_name = s.musicName; }
            else { body.sound_kind = 'tone'; body.sound_id = s.tone; }
            var req = isNew ? host.apiJson('POST', '/api/alarms', body) : host.apiJson('PATCH', '/api/alarms/' + encodeURIComponent(alarm.id), body);
            req.then(function () { close(); done(); }).catch(function () { err.textContent = 'Couldn’t save that. Try again.'; });
        }, 'save'));
        root.appendChild(ed);
    }
    function row(a, root, refresh) {
        var r = el('div', 'alrowitem' + (a.enabled ? '' : ' off'));
        var tm = el('div', 'altime');
        var t = from24h(a.time_hhmm);
        tm.appendChild(document.createTextNode(t.h12 + ':' + (t.mi < 10 ? '0' : '') + t.mi));
        tm.appendChild(el('span', 'alm', t.mer));
        r.appendChild(tm);
        var info = el('div', 'alinfo');
        info.appendChild(el('div', 'alname', a.masked ? 'Private alarm' : (a.label || (a.household ? 'Household alarm' : 'Alarm'))));
        info.appendChild(el('div', 'alsub', [a.repeat_phrase || 'Once', a.enabled && a.when ? a.when : 'Off'].filter(Boolean).join(' · ')));
        r.appendChild(info);
        if (a.editable) {
            var sw = el('button', 'alsw' + (a.enabled ? ' on' : ''));
            sw.setAttribute('aria-label', a.enabled ? 'Turn off' : 'Turn on');
            sw.appendChild(el('span', 'alknob'));
            sw.addEventListener('click', function (e) {
                e.stopPropagation();
                host.apiJson('PATCH', '/api/alarms/' + encodeURIComponent(a.id), { enabled: !a.enabled }).then(refresh).catch(function () {});
            });
            r.appendChild(sw);
            r.addEventListener('click', function () { openEditor(root, a, refresh); });
        }
        return r;
    }
    function mount(root) {
        function refresh() {
            host.apiGet('/api/alarms').then(function (d) {
                st.tones = (d && d.tones) || st.tones;
                clear(root);
                var head = el('div', 'alhead');
                head.appendChild(el('div', 'alhelp', 'Say “wake me at 7”, or tap New alarm'));
                head.appendChild(chip('+ New alarm', true, function () { openEditor(root, null, refresh); }, 'save'));
                root.appendChild(head);
                var list = el('div', 'allist');
                var rows = (d && d.alarms) || [];
                if (!rows.length) list.appendChild(el('div', 'alempty', 'No alarms yet'));
                rows.forEach(function (a) { list.appendChild(row(a, root, refresh)); });
                root.appendChild(list);
            }).catch(function () { clear(root); root.appendChild(el('div', 'alempty', 'Alarms aren’t available right now')); });
        }
        refresh();
    }
    // Hand the host the tab strip: shown only when the server has the feature (no 404 on /api/alarms/ringing).
    function attachTabs(o) {
        function setTab(which) {
            var al = which === 'alarms';
            o.timersEl.hidden = al;
            o.alarmsEl.hidden = !al;
            [].forEach.call(o.tabsEl.children, function (b) { b.classList.toggle('on', b.getAttribute('data-t') === which); });
            if (al) mount(o.alarmsEl);
        }
        function ready() {
            if (!st.enabled) { o.tabsEl.hidden = true; return false; }
            o.tabsEl.hidden = false;
            return true;
        }
        clear(o.tabsEl);
        [['timers', 'Timers'], ['alarms', 'Alarms']].forEach(function (t) {
            var b = el('button', 'altab' + (t[0] === 'timers' ? ' on' : ''), t[1]);
            b.setAttribute('data-t', t[0]);
            b.addEventListener('click', function () { setTab(t[0]); });
            o.tabsEl.appendChild(b);
        });
        o.timersEl.hidden = false;
        o.alarmsEl.hidden = true;
        if (st.enabled === null) {
            o.tabsEl.hidden = true;
            host.apiGet('/api/alarms/ringing').then(function () { st.enabled = true; ready(); }).catch(function () { st.enabled = false; ready(); });
        } else ready();
        return setTab;
    }

    function init(h) {
        if (host) return;
        host = h;
        loadAcks();
        schedule(300);
    }

    window.ZoeAlarms = { init: init, attachTabs: attachTabs, mount: mount, _poll: poll, _state: st,
                         pure: { to24h: to24h, from24h: from24h, clockText: clockText, wheelIndex: wheelIndex, repeatOf: repeatOf, chipsOf: chipsOf },
                         HOLD_MS: HOLD_MS };
})();
