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

    var POLL_MS = 2000, OFF_RECHECK_MS = 300000, HOLD_MS = 1000, ITEM_H = 64, MUSIC_DEADLINE_MS = 10000, STOP_GRACE_MS = 5000;
    var ACK_KEY = 'zoe_alarm_acks';
    var DAYS = [['M', 1], ['T', 2], ['W', 3], ['T', 4], ['F', 5], ['S', 6], ['S', 7]];
    var PRESETS = [['Once', 'once'], ['Every day', 'daily'], ['Weekdays', 'weekdays'], ['Weekends', 'weekends']];
    var DATE_STRIP_DAYS = 14;  // Today, Tomorrow and the next twelve days as chips; anything later comes from the month grid
    var WDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
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
    // dates are household-local 'YYYY-MM-DD' strings (the panel's clock IS the household's: same timezone as the server)
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    function ymd(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
    // calendar arithmetic on Y-M-D only, at local NOON so no DST edge can move a day; which day it IS comes from householdNow()
    function parseYmd(k) { var p = String(k).split('-'); return new Date(+p[0], +p[1] - 1, +p[2], 12); }
    function addDays(k, n) { var d = parseYmd(k); return ymd(new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, 12)); }
    function addYear(k) { var d = parseYmd(k); return ymd(new Date(d.getFullYear() + 1, d.getMonth(), d.getDate(), 12)); }
    // The HOUSEHOLD's today and clock (the server's zone, sent as `tz` with GET /api/alarms), never the browser's own zone: a panel
    // whose OS zone differs would otherwise send the wrong once_date. No zone yet (or no Intl) = the browser's clock.
    function householdNow(ms, tz) {
        if (tz && typeof Intl === 'object' && Intl.DateTimeFormat) {
            try {
                var parts = {};
                new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
                    .formatToParts(new Date(ms)).forEach(function (x) { parts[x.type] = x.value; });
                if (parts.year && parts.hour) return { ymd: parts.year + '-' + parts.month + '-' + parts.day, hm: parts.hour + ':' + parts.minute };
            } catch (e) { /* an unknown zone name: fall back below */ }
        }
        var d = new Date(ms);
        return { ymd: ymd(d), hm: pad2(d.getHours()) + ':' + pad2(d.getMinutes()) };
    }
    // "Today" / "Tomorrow" / "Fri 7" (same month) / "Mon 1 Dec"; `long` always names the month ("Fri 7 Nov")
    function dayLabel(k, today, long) {
        var d = parseYmd(k), t = parseYmd(today), diff = Math.round((d - t) / 864e5);
        if (diff === 0) return 'Today';
        if (diff === 1) return 'Tomorrow';
        return WDAYS[d.getDay()] + ' ' + d.getDate() + (long || d.getMonth() !== t.getMonth() ? ' ' + MONTHS[d.getMonth()] : '');
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
    // A ring never outlives the server's own limit (stops_at = ring start + RING_MAX_S), even when the server cannot be reached to
    // say so (zoe-data restarting, the DB down in the night window): the panel stops itself STOP_GRACE_MS after it.
    // `grace` is only for stopping a ring already on screen; a ring first seen past stops_at is never started (grace 0).
    function overdue(a, grace) { return !!(a && a.stops_at && nowMs() >= a.stops_at * 1000 + (grace || 0)); }
    function tickClock() {
        if (overdue(st.ring, STOP_GRACE_MS)) { stopRing(); return; }
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
            var live = ((d && d.ringing) || []).filter(function (a) { return !st.acks[ackKey(a)] && !overdue(a); });
            var listed = {};
            ((d && d.ringing) || []).forEach(function (a) { listed[ackKey(a)] = 1; });
            Object.keys(st.acks).forEach(function (k) { if (!listed[k] && !st.inflight[k]) delete st.acks[k]; });
            saveAcks();
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
            if (overdue(st.ring, STOP_GRACE_MS)) stopRing();  // the server is unreachable past the ring's own limit: never ring on and on
            if (/http 404/.test(String(e && e.message))) { st.enabled = false; hideTabs(); schedule(OFF_RECHECK_MS); } else schedule(POLL_MS * 2);
        });
    }
    function hideTabs() {  // the feature went away at runtime (flag flipped off): no stale Alarms tab, back to the Timers pane
        if (st.tabsCtl) { st.tabsCtl.tabsEl.hidden = true; st.tabsCtl.setTab('timers'); }
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
    // A month grid for "Pick a date": past days are drawn but cannot be tapped; ‹ › move a month (up to a year ahead). No keyboard.
    function openDatePicker(host_el, today, chosen, onPick) {
        var sheet = el('div', 'aldp'), box = el('div', 'aldpbox');
        sheet.appendChild(box);
        var t = parseYmd(today), base = parseYmd(chosen || today), view = new Date(base.getFullYear(), base.getMonth(), 1, 12);
        var first = new Date(t.getFullYear(), t.getMonth(), 1, 12), last = new Date(t.getFullYear() + 1, t.getMonth(), 1, 12);
        var cutoff = addYear(today);  // a year ahead to the DAY: the last month's later days are drawn but cannot be tapped
        function close() { if (sheet.parentNode) sheet.parentNode.removeChild(sheet); }
        function draw() {
            clear(box);
            var head = el('div', 'aldphead');
            var prev = el('button', 'alnav', '‹'), next = el('button', 'alnav', '›');
            prev.setAttribute('aria-label', 'Previous month'); next.setAttribute('aria-label', 'Next month');
            prev.disabled = view <= first; next.disabled = view >= last;
            prev.addEventListener('click', function () { if (!prev.disabled) { view = new Date(view.getFullYear(), view.getMonth() - 1, 1, 12); draw(); } });
            next.addEventListener('click', function () { if (!next.disabled) { view = new Date(view.getFullYear(), view.getMonth() + 1, 1, 12); draw(); } });
            head.appendChild(prev);
            head.appendChild(el('div', 'aldpttl', MONTHS_LONG[view.getMonth()] + ' ' + view.getFullYear()));
            head.appendChild(next);
            box.appendChild(head);
            var grid = el('div', 'aldpgrid');
            ['M', 'T', 'W', 'T', 'F', 'S', 'S'].forEach(function (w) { grid.appendChild(el('div', 'aldpwk', w)); });
            var lead = (view.getDay() + 6) % 7, n = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
            for (var i = 0; i < lead; i++) grid.appendChild(el('div', 'aldpblank'));
            for (var d = 1; d <= n; d++) {
                (function (key, past) {
                    var b = el('button', 'aldpday' + (key === chosen ? ' on' : '') + (key === today ? ' today' : ''), String(d));
                    if (past) b.disabled = true;
                    else b.addEventListener('click', function () { close(); onPick(key); });
                    grid.appendChild(b);
                })(ymd(new Date(view.getFullYear(), view.getMonth(), d, 12)), ymd(new Date(view.getFullYear(), view.getMonth(), d, 12)) < today
                   || ymd(new Date(view.getFullYear(), view.getMonth(), d, 12)) > cutoff);
            }
            box.appendChild(grid);
            box.appendChild(chip('Cancel', false, close, 'ghost'));
        }
        draw();
        host_el.appendChild(sheet);
    }
    function openEditor(root, alarm, done) {
        var isNew = !alarm;
        var t = from24h(alarm ? alarm.time_hhmm : '07:00');
        var chips = chipsOf(alarm ? alarm.repeat_rule : 'once');
        var s = { h: t.h12, mi: t.mi, mer: t.mer, preset: chips.preset, days: chips.days.slice(), custom: !chips.preset,
                  date: alarm && alarm.repeat_rule === 'once' && alarm.once_date ? alarm.once_date : '', dateAuto: !(alarm && alarm.once_date),
                  kind: alarm ? alarm.sound_kind : 'tone', tone: alarm && alarm.sound_kind === 'tone' ? alarm.sound_id : 'sunrise',
                  music: alarm && alarm.sound_kind === 'music' ? alarm.sound_id : '', musicName: alarm && alarm.sound_kind === 'music' ? alarm.sound_name : '',
                  snooze: alarm ? alarm.snooze_minutes : 9, more: false };
        var today = householdNow(nowMs(), st.tz).ymd;
        // A once-off with no day picked yet: today while the time is still ahead, else tomorrow (what the server would pick).
        function autoDate() {
            if (!s.dateAuto) return;
            var ahead = to24h(s.h, s.mi, s.mer) > householdNow(nowMs(), st.tz).hm;
            s.date = ahead ? today : addDays(today, 1);
        }
        autoDate();
        if (s.date && s.date < today) { s.date = today; s.dateAuto = true; autoDate(); }  // a spent one-off: offer the next one
        var ed = el('div', 'aled');
        var box = el('div', 'aledbox');
        ed.appendChild(box);
        var wheels = el('div', 'alwheels');
        var hours = [], mins = [];
        for (var h = 1; h <= 12; h++) hours.push(String(h));
        for (var m = 0; m < 60; m++) mins.push((m < 10 ? '0' : '') + m);
        function timeMoved() { if (s.dateAuto) { autoDate(); drawWhen(); } }
        wheels.appendChild(makeWheel(hours, s.h - 1, function (i) { s.h = i + 1; timeMoved(); }));
        wheels.appendChild(el('div', 'alcolon', ':'));
        wheels.appendChild(makeWheel(mins, s.mi, function (i) { s.mi = i; timeMoved(); }));
        wheels.appendChild(makeWheel(['am', 'pm'], s.mer === 'pm' ? 1 : 0, function (i) { s.mer = i ? 'pm' : 'am'; timeMoved(); }));
        box.appendChild(wheels);
        var side = el('div', 'alside');
        box.appendChild(side);
        function section(title, cls, parent) {
            var r = el('div', 'alrow'); r.appendChild(el('div', 'alk', title));
            var c = el('div', 'alchips' + (cls ? ' ' + cls : '')); r.appendChild(c); (parent || side).appendChild(r); return { row: r, chips: c };
        }
        // 1. Repeat: four calm presets plus "Days" (which alone reveals the weekday chips).
        var rep = section('Repeat').chips, dayRow = el('div', 'aldays');
        rep.parentNode.appendChild(dayRow);
        function drawRep() {
            clear(rep); clear(dayRow);
            PRESETS.forEach(function (p) { rep.appendChild(chip(p[0], !s.custom && s.preset === p[1], function () { s.preset = p[1]; s.days = []; s.custom = false; drawRep(); drawWhen(); })); });
            rep.appendChild(chip('Days', s.custom, function () { s.custom = true; s.preset = ''; drawRep(); drawWhen(); }));
            dayRow.hidden = !s.custom;
            if (!s.custom) return;
            DAYS.forEach(function (d) {
                dayRow.appendChild(chip(d[0], s.days.indexOf(d[1]) >= 0, function () {
                    var i = s.days.indexOf(d[1]);
                    if (i >= 0) s.days.splice(i, 1); else s.days.push(d[1]);
                    drawRep();
                }, 'day'));
            });
        }
        // 2. When (a once-off only): Today, Tomorrow, the next two weeks as one swipeable row, and "Pick a date" for anything later.
        // "Pick a date" sits OUTSIDE the swipeable strip so it is always on screen; the chosen day is scrolled into view.
        var when = section('Day', 'aldayline'), daystrip = el('div', 'alchips alscroll');
        when.chips.appendChild(daystrip);
        when.chips.appendChild(chip('Pick a date', false, function () {
            openDatePicker(ed, today, s.date, function (k) { s.date = k; s.dateAuto = false; err.textContent = ''; drawWhen(); });
        }, 'ghost'));
        function drawWhen() {
            var once = !s.custom && s.preset === 'once';
            when.row.hidden = !once;
            clear(daystrip);
            if (!once) return;
            var strip = [];
            for (var i = 0; i < DATE_STRIP_DAYS; i++) strip.push(addDays(today, i));
            if (s.date && strip.indexOf(s.date) < 0) strip.unshift(s.date);  // a date picked from the grid leads the row
            var sel = null;
            strip.forEach(function (k) {
                var c = chip(dayLabel(k, today), s.date === k, function () { s.date = k; s.dateAuto = false; err.textContent = ''; drawWhen(); });
                if (s.date === k) sel = c;
                daystrip.appendChild(c);
            });
            if (sel && sel.offsetLeft != null) {
                var left = sel.offsetLeft - daystrip.offsetLeft;
                if (left < daystrip.scrollLeft || left + sel.offsetWidth > daystrip.scrollLeft + daystrip.clientWidth) daystrip.scrollLeft = Math.max(0, left - 12);
            }
        }
        // 3. Everything else waits behind one row, closed every time the editor opens.
        var moreBtn = el('button', 'almore');
        var moreBox = el('div', 'almorebox');
        side.appendChild(moreBtn);
        side.appendChild(moreBox);
        function drawMore() {
            moreBtn.textContent = s.more ? 'Fewer options' : 'More options  ·  ' + soundName() + ', snooze ' + s.snooze + ' min';
            moreBtn.setAttribute('aria-expanded', s.more ? 'true' : 'false');
            moreBox.hidden = !s.more;
        }
        function soundName() { return s.kind === 'music' && s.music ? (s.musicName || 'Music') : (st.tones[s.tone] || 'Tone'); }
        moreBtn.addEventListener('click', function () { s.more = !s.more; drawMore(); });
        var snd = section('Sound', '', moreBox).chips;
        function drawSnd() {
            clear(snd);
            Object.keys(st.tones).forEach(function (id) {
                snd.appendChild(chip(st.tones[id], s.kind === 'tone' && s.tone === id, function () {
                    s.kind = 'tone'; s.tone = id; drawSnd(); drawMus(); drawMore(); if (host.sound) host.sound.preview(id);
                }));
            });
        }
        // Clock radio: the last thing played, or a playlist from Music Assistant. If it cannot start within 10 s the alarm rings a tone instead.
        var mus = section('Wake to music', 'alscroll', moreBox).chips, lists = [];  // one swipeable row: playlist names are long
        function drawMus() {
            clear(mus);
            var opts = [['Last played', 'last']].concat(lists.slice(0, 6).map(function (n) { return [n, 'playlist:' + n]; }));
            opts.forEach(function (o) {
                mus.appendChild(chip(o[0], s.kind === 'music' && s.music === o[1], function () {
                    s.kind = 'music'; s.music = o[1]; s.musicName = o[0]; drawSnd(); drawMus(); drawMore();
                }));
            });
        }
        host.apiGet('/api/music/playlists').then(function (d) {
            lists = ((d && d.playlists) || []).map(function (p) { return p && p.name; }).filter(Boolean);
            drawMus();
        }).catch(function () { /* no Music Assistant: Last played still works, and the tone is the safety net */ });
        var sz = section('Snooze', '', moreBox).chips;
        function drawSz() {
            clear(sz);
            [5, 9, 10, 15].forEach(function (n) { sz.appendChild(chip(n + ' min', s.snooze === n, function () { s.snooze = n; drawSz(); drawMore(); })); });
        }
        var acts = el('div', 'alacts');
        side.appendChild(acts);
        var err = el('div', 'alerr');
        side.appendChild(err);
        drawRep(); drawWhen(); drawSnd(); drawMus(); drawSz(); drawMore();
        var watch = null;
        function close() { if (watch) { watch.disconnect(); watch = null; } if (ed.parentNode) ed.parentNode.removeChild(ed); }
        acts.appendChild(chip('Cancel', false, close, 'ghost'));
        if (!isNew) {
            acts.appendChild(chip('Delete', false, function () {
                host.apiJson('DELETE', '/api/alarms/' + encodeURIComponent(alarm.id)).then(function () { close(); done(); })
                    .catch(function () { err.textContent = 'Couldn’t delete that.'; });
            }, 'danger'));
        }
        acts.appendChild(chip('Save', true, function () {
            if (s.custom && !s.days.length) { err.textContent = 'Pick at least one day.'; return; }
            var repeat = s.custom ? repeatOf('', s.days) : repeatOf(s.preset, []);
            var body = { time_hhmm: to24h(s.h, s.mi, s.mer), repeat: repeat, snooze_minutes: s.snooze };
            if (repeat === 'once') {
                // Never re-pick the day here: what is on screen is what is saved, and a Today whose time went by while the editor
                // stood open is refused in place (the user sees it and picks), not quietly moved to tomorrow.
                var hn = householdNow(nowMs(), st.tz);
                if (s.date < hn.ymd || (s.date === hn.ymd && body.time_hhmm <= hn.hm)) {
                    s.dateAuto = false; err.textContent = 'That time has already passed today. Pick another day.'; return;
                }
                body.once_date = s.date;
            }
            if (s.kind === 'music' && s.music) { body.sound_kind = 'music'; body.sound_id = s.music; body.sound_name = s.musicName; }
            else { body.sound_kind = 'tone'; body.sound_id = s.tone; }
            var req = isNew ? host.apiJson('POST', '/api/alarms', body) : host.apiJson('PATCH', '/api/alarms/' + encodeURIComponent(alarm.id), body);
            req.then(function () { close(); done(); }).catch(function () { err.textContent = 'Couldn’t save that. Try again.'; });
        }, 'save'));
        // The sheet covers the whole 1280x720 stage (above the dock and the orb), so it lives on #stage, not inside the Alarms
        // pane - and goes away with the pane: any surface change replaces the pane's parent's children (a voice turn, Home).
        var stage = document.getElementById('stage'), surface = root.parentNode;
        (stage || root).appendChild(ed);
        if (stage && surface && typeof MutationObserver === 'function') {
            watch = new MutationObserver(function () { if (!root.isConnected) close(); });
            watch.observe(surface, { childList: true });
        }
    }
    // The row's second line: a once-off names its DAY ("Once · Fri 7 Nov"), a repeat says when it next rings.
    function subline(a, today) {
        if (a.repeat_rule === 'once' && a.once_date) return 'Once · ' + (a.enabled ? dayLabel(a.once_date, today, true) : 'Off');
        return [a.repeat_phrase || 'Once', a.enabled && a.when ? a.when : 'Off'].filter(Boolean).join(' · ');
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
        info.appendChild(el('div', 'alsub', subline(a, householdNow(nowMs(), st.tz).ymd)));
        // One day's change ("an hour earlier next Friday", set by voice): shown on the row; tapping it puts that day back to normal.
        (a.overrides || []).slice(0, 2).forEach(function (o) {
            var txt = dayLabel(o.date, householdNow(nowMs(), st.tz).ymd, true) + ': ' + (o.action === 'skip' ? 'off' : clockText(o.time_hhmm));
            if (!a.editable) { info.appendChild(el('div', 'alov', txt)); return; }
            var b = el('button', 'alov', txt + '  ·  Undo');
            b.setAttribute('aria-label', 'Put ' + dayLabel(o.date, householdNow(nowMs(), st.tz).ymd, true) + ' back to ' + clockText(a.time_hhmm));
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                host.apiJson('DELETE', '/api/alarms/' + encodeURIComponent(a.id) + '/overrides/' + encodeURIComponent(o.date)).then(refresh).catch(function () {});
            });
            info.appendChild(b);
        });
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
                if (d && d.tz) st.tz = String(d.tz);
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
        st.tabsCtl = { tabsEl: o.tabsEl, setTab: setTab };
        return setTab;
    }

    function init(h) {
        if (host) return;
        host = h;
        loadAcks();
        schedule(300);
    }

    window.ZoeAlarms = { init: init, attachTabs: attachTabs, mount: mount, _poll: poll, _state: st,
                         pure: { to24h: to24h, from24h: from24h, clockText: clockText, wheelIndex: wheelIndex, repeatOf: repeatOf, chipsOf: chipsOf,
                                 addDays: addDays, dayLabel: dayLabel, subline: subline, householdNow: householdNow },
                         HOLD_MS: HOLD_MS };
})();
