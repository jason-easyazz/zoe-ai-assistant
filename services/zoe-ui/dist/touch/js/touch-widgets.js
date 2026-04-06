/**
 * touch-widgets.js — Self-contained widget renderers for the Zoe touch dashboard.
 * No dependency on WidgetManager or the manifest system.
 * Each widget fetches its own data and manages its own refresh cycle.
 */
'use strict';

const TouchWidgets = (() => {

    // Inject widget CSS once
    function injectCSS() {
        if (document.getElementById('tw-css')) return;
        const s = document.createElement('style');
        s.id = 'tw-css';
        s.textContent = `
/* ── Touch Widget Base ─────────────────────────────────── */
.widget {
    width: 100%; height: 100%;
    display: flex; flex-direction: column;
    background: #1e1b3a;
    border: 1.5px solid #5b41df;
    border-radius: 16px;
    overflow: hidden;
    box-sizing: border-box;
    padding: 0;
    color: rgba(255,255,255,0.93);
    font-family: inherit;
    box-shadow: 0 4px 20px rgba(0,0,0,0.50);
}
.widget-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px 6px;
    flex-shrink: 0;
}
.widget-title {
    font-size: 11px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase;
    color: var(--text-3, rgba(255,255,255,0.35));
}
.tw-more {
    font-size: 10px; color: var(--grad-start, #7B61FF);
    text-decoration: none; opacity: .8;
}
.tw-scrollable {
    overflow-y: auto; flex: 1;
    scrollbar-width: none;
}
.tw-scrollable::-webkit-scrollbar { display: none; }
.tw-loading, .tw-empty {
    padding: 12px 14px;
    font-size: 12px;
    color: var(--text-3, rgba(255,255,255,0.35));
    text-align: center;
}

/* ── Clock ─────────────────────────────────────────────── */
.tw-clock-body {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 4px 8px 12px;
}
.tw-time-row {
    display: flex; align-items: baseline; gap: 4px;
}
.tw-time-big {
    font-size: 42px; font-weight: 200;
    letter-spacing: -.02em; line-height: 1;
    color: var(--text-1, rgba(255,255,255,0.93));
}
.tw-ampm {
    font-size: 14px; font-weight: 400;
    color: var(--text-2, rgba(255,255,255,0.55));
    margin-bottom: 4px;
}
.tw-date {
    font-size: 14px; font-weight: 400;
    color: var(--text-2, rgba(255,255,255,0.55));
    margin-top: 2px;
}
.tw-day {
    font-size: 11px;
    color: var(--text-3, rgba(255,255,255,0.32));
    margin-top: 1px;
}

/* ── Weather ───────────────────────────────────────────── */
.tw-weather-body {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 4px 8px 12px; gap: 4px;
}
.tw-weather-main {
    display: flex; align-items: center; gap: 8px;
}
.tw-weather-icon { font-size: 32px; }
.tw-weather-temp {
    font-size: 36px; font-weight: 200;
    color: var(--text-1, rgba(255,255,255,0.93));
}
.tw-weather-cond {
    font-size: 13px;
    color: var(--text-2, rgba(255,255,255,0.55));
}
.tw-weather-meta {
    font-size: 11px;
    color: var(--text-3, rgba(255,255,255,0.32));
}

/* ── Events ────────────────────────────────────────────── */
.tw-events-list { padding: 0 6px 6px; }
.tw-ev-item {
    display: flex; gap: 8px; align-items: baseline;
    padding: 6px 8px; border-radius: 8px;
    margin-bottom: 2px;
    background: rgba(255,255,255,0.03);
}
.tw-ev-time {
    font-size: 10px; font-variant-numeric: tabular-nums;
    color: var(--text-3, rgba(255,255,255,0.32));
    min-width: 42px; flex-shrink: 0;
}
.tw-ev-title {
    font-size: 12px; line-height: 1.3;
    color: var(--text-1, rgba(255,255,255,0.90));
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ── Notes ─────────────────────────────────────────────── */
.tw-notes-list { padding: 0 6px 6px; }
.tw-note-item {
    padding: 7px 8px; border-radius: 8px;
    margin-bottom: 3px;
    background: rgba(255,255,255,0.04);
    border-left: 2px solid rgba(123,97,255,0.5);
}
.tw-note-text {
    font-size: 12px; line-height: 1.4;
    color: var(--text-1, rgba(255,255,255,0.90));
    display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden;
}

/* ── Lists / Reminders ─────────────────────────────────── */
.tw-list-items { padding: 0 6px 6px; }
.tw-list-item {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px; border-radius: 8px;
    margin-bottom: 2px;
}
.tw-check-circle {
    width: 16px; height: 16px; border-radius: 50%;
    border: 1.5px solid var(--text-3, rgba(255,255,255,0.32));
    flex-shrink: 0;
}
.tw-item-text {
    font-size: 12px;
    color: var(--text-1, rgba(255,255,255,0.90));
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ── Smart Home ─────────────────────────────────────────── */
.tw-home-grid {
    flex: 1; display: grid; grid-template-columns: 1fr 1fr;
    gap: 6px; padding: 4px 8px 10px;
}
.tw-device-tile {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 4px;
    padding: 8px; border-radius: 12px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    transition: background .2s;
    cursor: pointer;
}
.tw-device-tile.on {
    background: rgba(123,97,255,0.18);
    border-color: rgba(123,97,255,0.3);
}
.tw-device-icon { font-size: 20px; }
.tw-device-name {
    font-size: 10px; text-align: center;
    color: var(--text-2, rgba(255,255,255,0.55));
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    max-width: 100%;
}

/* ── System ─────────────────────────────────────────────── */
.tw-system-body {
    flex: 1; display: flex; flex-direction: column;
    justify-content: center; gap: 8px;
    padding: 4px 14px 12px;
}
.tw-sys-row {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 12px;
    color: var(--text-2, rgba(255,255,255,0.55));
}
.tw-sys-row span:last-child {
    font-weight: 600;
    color: var(--text-1, rgba(255,255,255,0.90));
}

/* ── Music ──────────────────────────────────────────────── */
.tw-music-body {
    flex: 1; display: flex; align-items: center; gap: 12px;
    padding: 4px 12px 12px;
}
.tw-music-art {
    width: 52px; height: 52px; border-radius: 10px;
    background: rgba(255,255,255,0.08);
    display: flex; align-items: center; justify-content: center;
    font-size: 24px; flex-shrink: 0; overflow: hidden;
}
.tw-music-info { flex: 1; overflow: hidden; }
.tw-music-title {
    font-size: 13px; font-weight: 500;
    color: var(--text-1, rgba(255,255,255,0.90));
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tw-music-artist {
    font-size: 11px; margin-top: 2px;
    color: var(--text-2, rgba(255,255,255,0.55));
}

/* ── Journal ─────────────────────────────────────────────── */
.tw-journal-body {
    flex: 1; padding: 4px 12px 12px;
    overflow: hidden;
}
.tw-journal-date {
    font-size: 10px; color: var(--text-3, rgba(255,255,255,0.32));
    margin-bottom: 4px;
}
.tw-journal-preview {
    font-size: 12px; line-height: 1.5;
    color: var(--text-1, rgba(255,255,255,0.88));
    display: -webkit-box; -webkit-line-clamp: 4;
    -webkit-box-orient: vertical; overflow: hidden;
}

/* ── Light mode overrides ──────────────────────────────── */
body.light-mode .widget {
    background: #ffffff;
    border: 2px solid #c4b5fd;
    color: #1a1a2e;
    box-shadow: 0 4px 24px rgba(100,80,200,0.14), 0 2px 8px rgba(0,0,0,0.10);
}
body.light-mode .tw-ev-time,
body.light-mode .tw-weather-meta,
body.light-mode .tw-day,
body.light-mode .widget-title { color: rgba(26,26,46,0.38); }
body.light-mode .tw-ev-title,
body.light-mode .tw-note-text,
body.light-mode .tw-item-text,
body.light-mode .tw-time-big,
body.light-mode .tw-weather-temp,
body.light-mode .tw-music-title,
body.light-mode .tw-journal-preview { color: #1a1a2e; }
body.light-mode .tw-weather-cond,
body.light-mode .tw-date,
body.light-mode .tw-ampm,
body.light-mode .tw-music-artist { color: rgba(26,26,46,0.60); }
body.light-mode .tw-device-tile { background: rgba(0,0,0,0.04); border-color: rgba(0,0,0,0.07); }
body.light-mode .tw-device-tile.on { background: rgba(123,97,255,0.10); border-color: rgba(123,97,255,0.25); }
body.light-mode .tw-note-item { background: rgba(0,0,0,0.03); }
body.light-mode .tw-ev-item { background: rgba(0,0,0,0.02); }
`;
        document.head.appendChild(s);
    }

    // ── Helpers ─────────────────────────────────────────────────────
    function getSession() {
        try { return JSON.parse(localStorage.getItem('zoe_session')) || {}; } catch (e) { return {}; }
    }

    async function api(url) {
        const sess = getSession();
        const headers = {};
        if (sess.session_id) headers['X-Session-ID'] = sess.session_id;
        const r = await fetch(url, { headers });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    }

    function formatTime(str) {
        if (!str) return '';
        try {
            const d = new Date(str);
            if (isNaN(d.getTime())) return str;
            return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        } catch (e) { return str; }
    }

    function formatDate(str) {
        if (!str) return 'Today';
        try {
            return new Date(str).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
        } catch (e) { return str; }
    }

    function formatUptime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function weatherEmoji(cond) {
        const c = (cond || '').toLowerCase();
        if (c.includes('sun') || c.includes('clear')) return '☀️';
        if (c.includes('partly') || c.includes('cloud')) return '⛅';
        if (c.includes('rain') || c.includes('shower')) return '🌧️';
        if (c.includes('storm') || c.includes('thunder')) return '⛈️';
        if (c.includes('snow') || c.includes('sleet')) return '❄️';
        if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return '🌫️';
        if (c.includes('wind')) return '💨';
        return '🌤️';
    }

    // ── Widget Definitions ─────────────────────────────────────────

    const WIDGETS = {

        // ── Clock ───────────────────────────────────────────────────
        time: {
            template: () => `
                <div class="widget-header"><div class="widget-title">Clock</div></div>
                <div class="tw-clock-body">
                    <div class="tw-time-row">
                        <div class="tw-time-big">12:00</div>
                        <div class="tw-ampm">AM</div>
                    </div>
                    <div class="tw-date"></div>
                    <div class="tw-day"></div>
                </div>`,
            init(el) {
                const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
                const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                const tick = () => {
                    const now = new Date();
                    const h = now.getHours() % 12 || 12;
                    const m = now.getMinutes().toString().padStart(2, '0');
                    const ampm = now.getHours() >= 12 ? 'PM' : 'AM';
                    const timeEl = el.querySelector('.tw-time-big');
                    const amEl = el.querySelector('.tw-ampm');
                    const dateEl = el.querySelector('.tw-date');
                    const dayEl = el.querySelector('.tw-day');
                    if (timeEl) timeEl.textContent = `${h}:${m}`;
                    if (amEl) amEl.textContent = ampm;
                    if (dateEl) dateEl.textContent = `${MONTHS[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`;
                    if (dayEl) dayEl.textContent = DAYS[now.getDay()];
                };
                tick();
                const timer = setInterval(tick, 1000);
                // Clean up if widget removed
                el._twTimer = timer;
            }
        },

        // ── Weather ─────────────────────────────────────────────────
        weather: {
            template: () => `
                <div class="widget-header"><div class="widget-title">Weather</div></div>
                <div class="tw-weather-body">
                    <div class="tw-weather-main">
                        <span class="tw-weather-icon">🌤️</span>
                        <span class="tw-weather-temp">--°</span>
                    </div>
                    <div class="tw-weather-cond">Loading...</div>
                    <div class="tw-weather-meta"></div>
                </div>`,
            async init(el) {
                try {
                    const data = await api('/api/weather/current');
                    const temp = Math.round(data.temperature ?? data.temp ?? 0);
                    const unit = data.unit || '°C';
                    const cond = data.condition || data.description || 'Clear';
                    const icon = data.icon_emoji || weatherEmoji(cond);
                    const feelsLike = data.feels_like != null ? `Feels ${Math.round(data.feels_like)}°` : '';
                    const humidity = data.humidity != null ? `💧${data.humidity}%` : '';
                    const iconEl = el.querySelector('.tw-weather-icon');
                    const tempEl = el.querySelector('.tw-weather-temp');
                    const condEl = el.querySelector('.tw-weather-cond');
                    const metaEl = el.querySelector('.tw-weather-meta');
                    if (iconEl) iconEl.textContent = icon;
                    if (tempEl) tempEl.textContent = `${temp}${unit}`;
                    if (condEl) condEl.textContent = cond;
                    if (metaEl) metaEl.textContent = [feelsLike, humidity].filter(Boolean).join('  ');
                } catch (e) {
                    const condEl = el.querySelector('.tw-weather-cond');
                    if (condEl) condEl.textContent = 'Weather unavailable';
                }
            },
            interval: 10 * 60 * 1000
        },

        // ── Events ──────────────────────────────────────────────────
        events: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Today</div>
                    <a href="/touch/calendar.html" class="tw-more">Calendar</a>
                </div>
                <div class="tw-events-list tw-scrollable">
                    <div class="tw-loading">Loading events…</div>
                </div>`,
            async init(el) {
                const list = el.querySelector('.tw-events-list');
                if (!list) return;
                try {
                    const today = new Date().toISOString().split('T')[0];
                    const data = await api(`/api/calendar/events?date=${today}`);
                    const items = data.events || data.items || (Array.isArray(data) ? data : []);
                    if (!items.length) {
                        list.innerHTML = '<div class="tw-empty">No events today ✓</div>';
                        return;
                    }
                    list.innerHTML = items.slice(0, 6).map(ev => {
                        const t = ev.start_time || ev.time || ev.start || '';
                        return `<div class="tw-ev-item">
                            <span class="tw-ev-time">${formatTime(t)}</span>
                            <span class="tw-ev-title">${ev.title || ev.summary || ev.name || 'Event'}</span>
                        </div>`;
                    }).join('');
                } catch (e) {
                    list.innerHTML = '<div class="tw-empty">No events</div>';
                }
            },
            interval: 5 * 60 * 1000
        },

        // ── Notes ───────────────────────────────────────────────────
        notes: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Notes</div>
                    <a href="/touch/notes.html" class="tw-more">See all</a>
                </div>
                <div class="tw-notes-list tw-scrollable">
                    <div class="tw-loading">Loading…</div>
                </div>`,
            async init(el) {
                const list = el.querySelector('.tw-notes-list');
                if (!list) return;
                try {
                    const data = await api('/api/notes/?limit=6');
                    const items = data.notes || data.items || (Array.isArray(data) ? data : []);
                    if (!items.length) {
                        list.innerHTML = '<div class="tw-empty">No notes yet</div>';
                        return;
                    }
                    list.innerHTML = items.slice(0, 5).map(n => `
                        <div class="tw-note-item">
                            <div class="tw-note-text">${n.title || n.content || n.text || 'Note'}</div>
                        </div>`).join('');
                } catch (e) {
                    list.innerHTML = '<div class="tw-empty">No notes</div>';
                }
            },
            interval: 5 * 60 * 1000
        },

        // ── Reminders ───────────────────────────────────────────────
        reminders: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Reminders</div>
                    <a href="/touch/lists.html" class="tw-more">See all</a>
                </div>
                <div class="tw-list-items tw-scrollable">
                    <div class="tw-loading">Loading…</div>
                </div>`,
            async init(el) {
                const list = el.querySelector('.tw-list-items');
                if (!list) return;
                try {
                    const data = await api('/api/reminders/');
                    const items = (data.reminders || data.items || (Array.isArray(data) ? data : []));
                    const pending = items.filter(i => !i.acknowledged && i.is_active !== false);
                    if (!pending.length) {
                        list.innerHTML = '<div class="tw-empty">All done! 🎉</div>';
                        return;
                    }
                    list.innerHTML = pending.slice(0, 6).map(i => `
                        <div class="tw-list-item">
                            <span class="tw-check-circle"></span>
                            <span class="tw-item-text">${i.text || i.title || i.name || 'Item'}</span>
                        </div>`).join('');
                } catch (e) {
                    list.innerHTML = '<div class="tw-empty">No reminders</div>';
                }
            },
            interval: 5 * 60 * 1000
        },

        // ── Smart Home ──────────────────────────────────────────────
        home: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Smart Home</div>
                    <a href="/touch/smart-home.html" class="tw-more">Control</a>
                </div>
                <div class="tw-home-grid">
                    <div class="tw-loading" style="grid-column:1/-1">Loading…</div>
                </div>`,
            async init(el) {
                const grid = el.querySelector('.tw-home-grid');
                if (!grid) return;
                try {
                    const data = await api('/api/ha/entities');
                    const states = data.entities || data.states || (Array.isArray(data) ? data : []);
                    const devices = states.filter(s =>
                        (s.entity_id?.startsWith('light.') || s.entity_id?.startsWith('switch.')) &&
                        s.state !== 'unavailable'
                    ).slice(0, 4);
                    if (!devices.length) {
                        grid.innerHTML = '<div class="tw-empty" style="grid-column:1/-1">No devices</div>';
                        return;
                    }
                    grid.innerHTML = devices.map(d => {
                        const on = d.state === 'on';
                        const name = (d.attributes?.friendly_name || d.entity_id.split('.')[1]).replace(/_/g, ' ');
                        const isLight = d.entity_id.startsWith('light.');
                        return `<div class="tw-device-tile ${on ? 'on' : 'off'}">
                            <span class="tw-device-icon">${isLight ? (on ? '💡' : '🔦') : (on ? '⚡' : '⭕')}</span>
                            <span class="tw-device-name">${name}</span>
                        </div>`;
                    }).join('');
                } catch (e) {
                    grid.innerHTML = '<div class="tw-empty" style="grid-column:1/-1">Unavailable</div>';
                }
            },
            interval: 30 * 1000
        },

        // ── Tasks ───────────────────────────────────────────────────
        tasks: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Tasks</div>
                    <a href="/touch/lists.html" class="tw-more">All</a>
                </div>
                <div class="tw-list-items tw-scrollable">
                    <div class="tw-loading">Loading…</div>
                </div>`,
            async init(el) {
                const list = el.querySelector('.tw-list-items');
                if (!list) return;
                try {
                    const data = await api('/api/lists/tasks');
                    const items = (data.items || data.tasks || (Array.isArray(data) ? data : []));
                    const pending = items.filter(i => !i.completed && !i.done).slice(0, 6);
                    if (!pending.length) {
                        list.innerHTML = '<div class="tw-empty">All done! 🎉</div>';
                        return;
                    }
                    list.innerHTML = pending.map(i => `
                        <div class="tw-list-item">
                            <span class="tw-check-circle"></span>
                            <span class="tw-item-text">${i.text || i.title || i.name || 'Task'}</span>
                        </div>`).join('');
                } catch (e) {
                    list.innerHTML = '<div class="tw-empty">No tasks</div>';
                }
            },
            interval: 5 * 60 * 1000
        },

        // ── Journal ─────────────────────────────────────────────────
        journal: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Journal</div>
                    <a href="/touch/journal.html" class="tw-more">Write</a>
                </div>
                <div class="tw-journal-body">
                    <div class="tw-loading">Loading…</div>
                </div>`,
            async init(el) {
                const body = el.querySelector('.tw-journal-body');
                if (!body) return;
                try {
                    const data = await api('/api/journal/entries?limit=1');
                    const entries = data.entries || data.items || (Array.isArray(data) ? data : []);
                    const entry = entries[0];
                    if (!entry) {
                        body.innerHTML = '<div class="tw-empty">Start writing today ✍️</div>';
                        return;
                    }
                    body.innerHTML = `
                        <div class="tw-journal-date">${formatDate(entry.date || entry.created_at)}</div>
                        <div class="tw-journal-preview">${(entry.content || entry.text || entry.body || '').slice(0, 150)}</div>`;
                } catch (e) {
                    body.innerHTML = '<div class="tw-empty">No entries yet</div>';
                }
            },
            interval: 10 * 60 * 1000
        },

        // ── System Status ───────────────────────────────────────────
        system: {
            template: () => `
                <div class="widget-header"><div class="widget-title">System</div></div>
                <div class="tw-system-body">
                    <div class="tw-sys-row"><span>CPU</span><span class="tw-cpu">--</span></div>
                    <div class="tw-sys-row"><span>Memory</span><span class="tw-mem">--</span></div>
                    <div class="tw-sys-row"><span>Temp</span><span class="tw-temp">--</span></div>
                    <div class="tw-sys-row"><span>Uptime</span><span class="tw-uptime">--</span></div>
                </div>`,
            async init(el) {
                try {
                    const data = await api('/api/system/status');
                    const q = sel => el.querySelector(sel);
                    if (data.cpu_percent != null) q('.tw-cpu').textContent = `${Math.round(data.cpu_percent)}%`;
                    if (data.memory_percent != null) q('.tw-mem').textContent = `${Math.round(data.memory_percent)}%`;
                    if (data.temperature != null) q('.tw-temp').textContent = `${Math.round(data.temperature)}°C`;
                    if (data.uptime != null) q('.tw-uptime').textContent = formatUptime(data.uptime);
                } catch (e) { /* show dashes */ }
            },
            interval: 15 * 1000
        },

        // ── Music ───────────────────────────────────────────────────
        music: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Music</div>
                    <a href="/touch/music.html" class="tw-more">Open</a>
                </div>
                <div class="tw-music-body">
                    <div class="tw-music-art">🎵</div>
                    <div class="tw-music-info">
                        <div class="tw-music-title">Nothing playing</div>
                        <div class="tw-music-artist"></div>
                    </div>
                </div>`,
            async init(el) {
                try {
                    const data = await api('/api/media/now-playing');
                    if (data && data.title) {
                        const titleEl = el.querySelector('.tw-music-title');
                        const artistEl = el.querySelector('.tw-music-artist');
                        const artEl = el.querySelector('.tw-music-art');
                        if (titleEl) titleEl.textContent = data.title;
                        if (artistEl) artistEl.textContent = data.artist || data.album || '';
                        if (artEl && data.album_art) {
                            artEl.innerHTML = `<img src="${data.album_art}" style="width:100%;height:100%;object-fit:cover;border-radius:8px;" alt="">`;
                        }
                    }
                } catch (e) { /* keep placeholder */ }
            },
            interval: 30 * 1000
        },

        // ── Shopping ────────────────────────────────────────────────
        shopping: {
            template: () => `
                <div class="widget-header">
                    <div class="widget-title">Shopping</div>
                    <a href="/touch/lists.html" class="tw-more">List</a>
                </div>
                <div class="tw-list-items tw-scrollable">
                    <div class="tw-loading">Loading…</div>
                </div>`,
            async init(el) {
                const list = el.querySelector('.tw-list-items');
                if (!list) return;
                try {
                    const data = await api('/api/lists/shopping');
                    const items = (data.items || data.tasks || (Array.isArray(data) ? data : []));
                    const pending = items.filter(i => !i.completed && !i.done).slice(0, 6);
                    if (!pending.length) {
                        list.innerHTML = '<div class="tw-empty">List is empty</div>';
                        return;
                    }
                    list.innerHTML = pending.map(i => `
                        <div class="tw-list-item">
                            <span class="tw-check-circle"></span>
                            <span class="tw-item-text">${i.text || i.title || i.name || 'Item'}</span>
                        </div>`).join('');
                } catch (e) {
                    list.innerHTML = '<div class="tw-empty">No items</div>';
                }
            },
            interval: 5 * 60 * 1000
        },
    };

    // ── Public API ────────────────────────────────────────────────────

    /**
     * Render a widget into the given element.
     * @param {string} type - Widget type key (e.g. 'time', 'weather')
     * @param {HTMLElement} el - Container element to render into
     */
    async function render(type, el) {
        if (!el) return;
        const w = WIDGETS[type];
        if (!w) {
            el.innerHTML = `<div class="widget-header"><div class="widget-title">${type}</div></div>
                <div class="tw-loading">Widget unavailable</div>`;
            return;
        }
        try {
            el.innerHTML = w.template();
            await w.init(el);
            if (w.interval) {
                const timer = setInterval(() => {
                    try { w.init(el); } catch (e) { /* ignore refresh errors */ }
                }, w.interval);
                el._twInterval = timer;
            }
        } catch (e) {
            console.warn(`[TouchWidgets:${type}]`, e);
            el.innerHTML = `<div class="widget-header"><div class="widget-title">${type}</div></div>
                <div class="tw-empty">Failed to load</div>`;
        }
    }

    function init() {
        injectCSS();
    }

    return { render, init, WIDGETS };

})();

window.TouchWidgets = TouchWidgets;
