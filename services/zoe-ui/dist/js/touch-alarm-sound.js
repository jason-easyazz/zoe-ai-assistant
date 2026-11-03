/*
 * Alarm tones (ZOE_ALARMS) - synthesised in the page with WebAudio, so there is no audio asset, no licence question and nothing to
 * download: the panel can ring with the network and Music Assistant both down. Design: docs/architecture/alarms.md.
 *
 * Each tone is a short phrase of notes repeated every `period` seconds; every note is a few sine/square partials with a fast attack
 * and a decay. The master gain follows `rampGain(elapsed, ramp)` - a gentle climb from 5% to full over the alarm's ramp seconds -
 * measured from the SERVER's ring start, so a page reload mid-ring resumes at the right loudness instead of starting quiet again.
 * The ids here must match the server's `alarms.TONES` (pinned by tests/test_alarm_sounds.py).
 */
(function () {
    'use strict';
    if (window.ZoeAlarmSound) return;

    var FLOOR = 0.05;
    // notes: [offset s, frequency Hz, length s]; partials: [ratio, relative gain]
    var TONES = {
        sunrise: { name: 'Sunrise', period: 4.0, wave: 'sine', gain: 0.5, partials: [[1, 1], [2, 0.25]],
            notes: [[0, 523.25, 1.6], [0.5, 659.25, 1.5], [1.0, 783.99, 1.4], [1.5, 1046.5, 1.9]] },
        chime: { name: 'Chime', period: 3.0, wave: 'sine', gain: 0.5, partials: [[1, 1], [2.76, 0.3], [5.4, 0.1]],
            notes: [[0, 1318.5, 2.2], [0.7, 987.8, 2.4]] },
        bell: { name: 'Bell', period: 2.4, wave: 'sine', gain: 0.5, partials: [[1, 1], [2.0, 0.5], [2.76, 0.4], [5.4, 0.2]],
            notes: [[0, 660, 2.0], [1.0, 660, 2.0]] },
        beep: { name: 'Beep', period: 1.6, wave: 'square', gain: 0.18, partials: [[1, 1]],
            notes: [[0, 880, 0.18], [0.3, 880, 0.18], [0.6, 880, 0.18]] },
    };
    var st = { ctx: null, master: null, loopT: null, rampT: null, live: [] };

    function rampGain(elapsedS, rampS) {
        if (!(rampS > 0)) return 1;
        return Math.max(FLOOR, Math.min(1, FLOOR + (1 - FLOOR) * (Math.max(0, elapsedS) / rampS)));
    }
    // One period as plain data (also what the harness inspects): [{t, freq, dur, gain, wave}], one entry per partial of each note.
    function plan(id) {
        var tone = TONES[id] || TONES.sunrise, out = [];
        tone.notes.forEach(function (n) {
            tone.partials.forEach(function (p) {
                out.push({ t: n[0], freq: n[1] * p[0], dur: n[2], gain: tone.gain * p[1] / tone.partials.length, wave: tone.wave });
            });
        });
        return out;
    }
    function context() {
        if (!st.ctx) {
            var C = window.AudioContext || window.webkitAudioContext;
            if (!C) return null;
            st.ctx = new C();
        }
        wake();
        return st.ctx;
    }
    // A suspended context (autoplay policy) makes no sound; ask again every time we are called, and from the ramp timer below, so the
    // tone starts the moment the browser allows it. A rejected resume is retried, never fatal.
    function wake() {
        var c = st.ctx;
        if (c && c.state === 'suspended' && c.resume) {
            try { var p = c.resume(); if (p && p.catch) p.catch(function () { /* retried on the next tick */ }); } catch (e) { /* retried */ }
        }
    }
    function schedule(id, t0, dest) {
        var c = st.ctx;
        plan(id).forEach(function (n) {
            var o = c.createOscillator(), g = c.createGain();
            o.type = n.wave;
            o.frequency.value = n.freq;
            o.connect(g);
            g.connect(dest);
            g.gain.setValueAtTime(0, t0 + n.t);
            g.gain.linearRampToValueAtTime(n.gain, t0 + n.t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.001, t0 + n.t + n.dur);
            o.start(t0 + n.t);
            o.stop(t0 + n.t + n.dur + 0.05);
            st.live.push(o);
            if (st.live.length > 200) st.live.splice(0, st.live.length - 200);
        });
    }
    // Start looping `id`; `elapsed()` returns seconds since the ring began (server clock) and `rampS` the ramp length.
    function start(id, elapsed, rampS) {
        stop();
        var c = context();
        if (!c) return false;
        st.master = c.createGain();
        st.master.connect(c.destination);
        var tone = TONES[id] || TONES.sunrise;
        function level() { wake(); st.master.gain.setValueAtTime(rampGain(elapsed(), rampS), c.currentTime); }
        level();
        schedule(id, c.currentTime + 0.05, st.master);
        st.loopT = setInterval(function () { schedule(id, c.currentTime + 0.05, st.master); }, tone.period * 1000);
        st.rampT = setInterval(level, 500);
        return true;
    }
    function stop() {
        if (st.loopT) { clearInterval(st.loopT); st.loopT = null; }
        if (st.rampT) { clearInterval(st.rampT); st.rampT = null; }
        st.live.forEach(function (o) { try { o.stop(); } catch (e) { /* already stopped */ } });
        st.live = [];
        if (st.master) { try { st.master.disconnect(); } catch (e) { /* gone */ } st.master = null; }
    }
    // A one-shot taste of a tone for the picker (never overlaps a ring: a ringing alarm owns the speaker).
    function preview(id) {
        var c = context();
        if (!c || st.master) return;
        var g = c.createGain();
        g.gain.value = 0.6;
        g.connect(c.destination);
        schedule(id, c.currentTime + 0.02, g);
    }

    window.ZoeAlarmSound = { TONES: TONES, rampGain: rampGain, plan: plan, start: start, stop: stop, preview: preview, FLOOR: FLOOR };
})();
