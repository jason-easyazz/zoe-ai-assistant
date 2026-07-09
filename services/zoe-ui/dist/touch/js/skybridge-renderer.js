/*
 * Skybridge renderer registry.
 * Renderers consume Zoe card contracts, AG-UI custom payloads, and local
 * capability cards through a small common shape: { component, props }.
 */
(function () {
    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function buttonHtml(action, index) {
        const label = escapeHtml(action.label || action.title || 'Open');
        const query = escapeHtml(action.query || '');
        const route = escapeHtml(action.route || '');
        const challengeId = escapeHtml(action.challenge_id || '');
        const actionContext = escapeHtml(action.action_context || action.reason || '');
        const userId = escapeHtml(action.user_id || '');
        const userName = escapeHtml(action.username || action.name || '');
        const userAvatar = escapeHtml(action.avatar || '');
        const kind = action.kind === 'warn' ? ' warn' : (index === 0 ? ' primary' : '');
        return '<button type="button" class="' + kind.trim() + '" data-sky-action="' + escapeHtml(action.type || 'query') + '" data-query="' + query + '" data-route="' + route + '" data-challenge-id="' + challengeId + '" data-action-context="' + actionContext + '" data-user-id="' + userId + '" data-user-name="' + userName + '" data-user-avatar="' + userAvatar + '">' + label + '</button>';
    }

    function safeClassTokens(value) {
        return String(value || '')
            .split(/\s+/)
            .filter(token => /^[a-z0-9-]+$/i.test(token))
            .join(' ');
    }

    function rendererAccepts(schemaVersion) {
        const match = String(schemaVersion || '').match(/^(\d+)\.(\d+)\.(\d+)$/);
        return !!match && Number(match[1]) <= 1;
    }

    function glyphFor(props, fallback) {
        const source = props.icon || props.title || fallback || 'Z';
        return escapeHtml(String(source).trim().charAt(0).toUpperCase() || 'Z');
    }

    function cardFrame(props, body, options) {
        const wide = options && options.wide ? ' wide' : '';
        const compact = options && options.compact ? ' compact' : '';
        const safeTone = options && options.tone ? safeClassTokens(options.tone) : '';
        const tone = safeTone ? ' ' + safeTone : '';
        const showHeader = !(options && options.hideHeader);
        const showStatus = !(options && options.hideStatus);
        const actions = !(options && options.hideActions) && Array.isArray(props.actions) && props.actions.length
            ? '<div class="sky-actions">' + props.actions.map(buttonHtml).join('') + '</div>'
            : '';
        return [
            '<article class="sky-card sky-premium-card' + wide + compact + tone + '" data-card-id="' + escapeHtml(props.id || '') + '">',
            showHeader ? [
            '<div class="sky-widget-top">',
            '<div class="sky-widget-title">',
            props.kicker ? '<p>' + escapeHtml(props.kicker) + '</p>' : '',
            '<h3 class="sky-card-title">' + escapeHtml(props.title || 'Zoe') + '</h3>',
            '</div>',
            '<div class="sky-widget-glyph" aria-hidden="true">' + glyphFor(props) + '</div>',
            '</div>'
            ].join('') : '',
            showStatus && props.status ? '<span class="sky-badge">' + escapeHtml(props.status) + '</span>' : '',
            body,
            actions,
            '</article>'
        ].join('');
    }

    function renderAuthChallenge(props) {
        const title = escapeHtml(props.title || "Who's here?");
        const sub = escapeHtml(props.body || props.summary || 'Tap your profile to continue.');
        const bodyHtml = [
            '<div class="sky-auth-scene sky-auth-people-only">',
            '<div class="sky-authx-head">',
            '<span class="sky-authx-kicker">' + escapeHtml(props.kicker || 'Sign in') + '</span>',
            '<h2 class="sky-authx-title">' + title + '</h2>',
            '<p class="sky-authx-sub">' + sub + '</p>',
            '</div>',
            '<div class="sky-auth-profile-grid" data-auth-profiles aria-label="Choose your profile">',
            '<div class="sky-auth-loading"><i></i><span>Finding people for this panel…</span></div>',
            '</div>',
            '</div>'
        ].join('');
        return cardFrame(props, bodyHtml, { wide: true, tone: 'auth-challenge sky-authx', hideHeader: true, hideStatus: true, hideActions: true });
    }

    function renderTimer(props) {
        const dur = Math.max(1, parseInt(props.duration_seconds) || 0);
        const expires = parseInt(props.expires_at_ms) || (Date.now() + dur * 1000);
        const remaining = Math.max(0, Math.round((expires - Date.now()) / 1000));
        const frac = Math.max(0, Math.min(1, remaining / dur));
        const mm = String(Math.floor(remaining / 60)).padStart(2, '0');
        const ss = String(remaining % 60).padStart(2, '0');
        const label = props.label || props.title || 'Timer';
        const id = escapeHtml(props.timer_id || props.id || '');
        const expired = props.status === 'expired' || remaining <= 0;
        const lowClass = (!expired && frac <= 0.15) ? ' is-low' : '';
        // The fill is the card's border: a rounded-rect stroke whose visible length
        // tracks the time left (pathLength=100 → offset is just the spent percent).
        const offset = (100 * (1 - frac)).toFixed(2);
        const ring = [
            '<svg class="sky-timer-ring" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">',
            '<rect class="sky-timer-ring-track" x="2" y="2" width="96" height="96" rx="9" ry="9" pathLength="100"></rect>',
            '<rect class="sky-timer-ring-fill" x="2" y="2" width="96" height="96" rx="9" ry="9" pathLength="100" stroke-dasharray="100" stroke-dashoffset="' + offset + '"></rect>',
            '</svg>'
        ].join('');
        const body = [
            '<div class="sky-timer' + (expired ? ' is-expired' : '') + lowClass + '" data-timer-id="' + id + '"',
                ' data-timer-expires="' + expires + '" data-timer-duration="' + dur + '"',
                ' data-timer-status="' + (expired ? 'expired' : 'running') + '">',
            ring,
            '<button type="button" class="sky-timer-x" data-timer-cancel="' + id + '" aria-label="' + (expired ? 'Dismiss timer' : 'Cancel timer') + '">✕</button>',
            '<div class="sky-timer-center">',
            '<div class="sky-timer-digits">' + (expired ? "Time's up" : mm + ':' + ss) + '</div>',
            '<div class="sky-timer-label">' + escapeHtml(label) + '</div>',
            '</div>',
            '</div>'
        ].join('');
        return cardFrame(props, body, { tone: 'timer' + (expired ? ' sky-timer-ringing' : ''), hideHeader: true, hideStatus: true });
    }

    // ── zoe-compose adapters ────────────────────────────────────────────────
    // The generic renderers below are thin props→tree adapters over the shared
    // primitive catalog (window.ZoeCompose, loaded before this file). Producers
    // are untouched — servers keep emitting the same card shapes; only the BODY
    // markup is composed from catalog primitives. Actions stay on the cardFrame
    // path (props.actions → buttonHtml) so open/route semantics are unchanged.
    function composedBody(tree, fallbackText) {
        if (window.ZoeCompose && typeof window.ZoeCompose.render === 'function') {
            return '<div class="zx-card-body">' + window.ZoeCompose.render(tree) + '</div>';
        }
        // zoe-compose.js failed to load: degrade to a minimal escaped-text body
        // so the panel can never blank.
        return '<div class="zx-card-body"><p>' + escapeHtml(fallbackText || '') + '</p></div>';
    }
    function textNode(text, role) {
        return { component: 'Text', text: text, role: role || 'body' };
    }
    function listRowNode(title, detail) {
        const node = { component: 'ListRow', title: title };
        if (detail) node.detail = detail;
        return node;
    }

    function renderStatus(props) {
        if (props.source === 'calendar_show') return renderCalendar(props);
        if (props.source === 'weather_current' || props.source === 'weather_forecast') return renderWeather(props);
        if (props.source === 'list_show') return renderZoeList(props);
        if (props.source === 'people_directory') return renderPeopleDirectory(props);
        if (props.source === 'person_profile') return renderPersonProfile(props);
        if (props.source === 'clock_show') return renderClock(props);
        // Generic tail: kicker/title/status render on the cardFrame header as
        // before; the body becomes a composed tree (optional metric Stat + text).
        const message = props.body || props.message || '';
        const children = [];
        if (props.metric) children.push({ component: 'Stat', value: props.metric, label: props.metric_label || '' });
        children.push(textNode(message, 'body'));
        const tree = { component: 'Stack', gap: 'md', children: children };
        return cardFrame(props, composedBody(tree, message), { wide: !!props.wide, tone: props.tone || '' });
    }

    function formatCalendarDate(value) {
        const raw = String(value || '');
        const datePart = raw.slice(0, 10);
        if (/^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
            const date = new Date(datePart + 'T12:00:00');
            if (!Number.isNaN(date.getTime())) {
                return {
                    weekday: date.toLocaleDateString(undefined, { weekday: 'long' }),
                    monthDay: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                };
            }
        }
        return { weekday: 'Agenda', monthDay: raw || 'Today' };
    }

    function calendarEventSortKey(item) {
        const datePart = String(item.start_date || item.date || '').slice(0, 10);
        const timePart = item.all_day ? '00:00' : String(item.start_time || '00:00').slice(0, 5);
        const sortable = datePart ? datePart + 'T' + timePart : '9999-12-31T' + timePart;
        const timestamp = Date.parse(sortable);
        return Number.isNaN(timestamp) ? Number.MAX_SAFE_INTEGER : timestamp;
    }

    function accentClass(value, fallback) {
        const token = safeClassTokens(String(value || fallback || 'general').toLowerCase()) || 'general';
        const aliases = {
            task: 'tasks',
            todo: 'tasks',
            todos: 'tasks',
            grocery: 'shopping',
            groceries: 'shopping',
            friend: 'social',
            friends: 'social',
            contact: 'personal',
            contacts: 'personal'
        };
        const normalized = aliases[token] || token;
        const known = ['work', 'personal', 'bucket', 'shopping', 'health', 'routine', 'social', 'family', 'medical', 'household', 'tasks', 'all', 'general'];
        return known.indexOf(normalized) >= 0 ? normalized : (fallback || 'general');
    }

    function calendarCategoryClass(value) {
        const category = accentClass(value, 'general');
        return ['tasks', 'all'].indexOf(category) >= 0 ? 'general' : category;
    }

    // Best-effort epoch (ms) for an event start, so we can compute a live
    // "in 2h" / "in 25 min" countdown from now. Mirrors calendarEventSortKey's
    // date/time parsing but returns the actual timestamp (or NaN).
    function calendarEventStartMs(item) {
        const datePart = String(item.start_date || item.date || '').slice(0, 10);
        const timePart = item.all_day ? '00:00' : String(item.start_time || '00:00').slice(0, 5);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return NaN;
        return Date.parse(datePart + 'T' + timePart);
    }

    // A calm, human relative phrase from now → the event start. Returns '' when
    // we can't compute one (no parseable date) so the hero can fall back cleanly.
    function calendarCountdown(item, nowMs) {
        if (item.all_day) {
            const startDay = String(item.start_date || item.date || '').slice(0, 10);
            if (/^\d{4}-\d{2}-\d{2}$/.test(startDay)) {
                const today = new Date(nowMs);
                const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
                const startMs = Date.parse(startDay + 'T00:00:00');
                if (Number.isFinite(startMs)) {
                    const dDays = Math.round((startMs - todayStart) / 86400000);
                    if (dDays <= 0) return 'All day';
                    if (dDays === 1) return 'All day · tomorrow';
                    if (dDays < 7) return 'All day · ' + new Date(startMs).toLocaleDateString(undefined, { weekday: 'long' });
                }
            }
            return 'All day';
        }
        const startMs = calendarEventStartMs(item);
        if (!Number.isFinite(startMs)) return '';
        const diffMin = Math.round((startMs - nowMs) / 60000);
        if (diffMin <= -60) {
            const h = Math.round(-diffMin / 60);
            return h + 'h ago';
        }
        if (diffMin < 0) return Math.abs(diffMin) + ' min ago';
        if (diffMin === 0) return 'Now';
        if (diffMin < 60) return 'in ' + diffMin + ' min';
        const hours = diffMin / 60;
        if (hours < 24) {
            const h = Math.floor(hours);
            const m = diffMin - h * 60;
            return 'in ' + h + 'h' + (m ? ' ' + m + 'm' : '');
        }
        const days = Math.round(hours / 24);
        if (days === 1) return 'Tomorrow';
        return 'in ' + days + ' days';
    }

    // Custom filled calendar glyph (design system §5: filled/dimensional, never line
    // icons). Self-contained <svg> with a unique gradient id so it can live inside a
    // single rendered card without depending on the shared sprite.
    var calGlyphSeq = 0;
    function calendarGlyphSvg() {
        // Unique gradient id per render so multiple calendar cards don't collide on
        // a shared element id (invalid HTML).
        var gid = 'calCardG' + (++calGlyphSeq);
        return [
            '<svg class="calendar-glyph" viewBox="0 0 32 32" aria-hidden="true">',
            '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">',
            '<stop offset="0" stop-color="rgba(255,255,255,.95)"/>',
            '<stop offset="1" stop-color="rgba(228,236,250,.86)"/>',
            '</linearGradient></defs>',
            '<rect x="3" y="6" width="26" height="23" rx="5" fill="url(#' + gid + ')"/>',
            '<rect x="3" y="6" width="26" height="7" rx="5" fill="rgba(var(--calendar-glyph-accent,90,224,224),.92)"/>',
            '<rect x="9" y="2.5" width="2.8" height="7" rx="1.4" fill="rgba(var(--calendar-glyph-accent,90,224,224),.92)"/>',
            '<rect x="20.2" y="2.5" width="2.8" height="7" rx="1.4" fill="rgba(var(--calendar-glyph-accent,90,224,224),.92)"/>',
            '<g fill="rgba(22,50,77,.34)"><rect x="8" y="17" width="4" height="4" rx="1.2"/><rect x="14" y="17" width="4" height="4" rx="1.2"/><rect x="20" y="17" width="4" height="4" rx="1.2"/><rect x="8" y="23" width="4" height="4" rx="1.2"/><rect x="14" y="23" width="4" height="4" rx="1.2"/></g>',
            '</svg>'
        ].join('');
    }

    // Build a tap-to-edit query for an event row (opens the calendar editor card
    // via /api/skybridge/resolve). Including the time disambiguates same-title events.
    function calendarEditQuery(item, title) {
        const startTime = String(item.start_time || '').slice(0, 5);
        return 'edit ' + title + (startTime ? ' at ' + startTime : '');
    }

    // The calendar scene takes a living time-of-day gradient (like the clock card),
    // so the card reads as "your day" rather than a flat agenda. Phase by hour.
    function calendarDaypart(nowMs) {
        const h = new Date(nowMs != null ? nowMs : Date.now()).getHours();
        if (h >= 5 && h < 8) return 'dawn';
        if (h >= 8 && h < 17) return 'day';
        if (h >= 17 && h < 20) return 'dusk';
        return 'night';
    }

    // Minutes-since-midnight for an "HH:MM[:SS]" clock string, or NaN.
    function calMinutes(t) {
        const m = /^(\d{1,2}):(\d{2})/.exec(String(t || ''));
        if (!m) return NaN;
        return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    }

    // Event length in minutes (end − start), defaulting to 60 when the end is
    // missing/invalid so a point event still paints a legible ribbon block.
    function calDuration(item) {
        const s = calMinutes(item.start_time);
        const e = calMinutes(item.end_time);
        return (Number.isFinite(s) && Number.isFinite(e) && e > s) ? (e - s) : 60;
    }

    // Compact hour label for the ribbon axis: 6 → "6a", 12 → "12p", 18 → "6p".
    function calHourLabel(h) {
        h = ((h % 24) + 24) % 24;
        let hr = h % 12;
        if (hr === 0) hr = 12;
        return hr + (h < 12 ? 'a' : 'p');
    }

    // The time gutter for an agenda row: a big start + small end (or "All / day").
    function calGutter(item) {
        if (item.all_day) return { start: 'All', end: 'day' };
        const s = String(item.start_time || '').slice(0, 5);
        const e = String(item.end_time || '').slice(0, 5);
        if (!s) return { start: '—', end: '' };
        return { start: s, end: (e && e !== s) ? e : '' };
    }

    // The day RIBBON (Fantastical "DayTicker" / Apple day-timeline idea): a
    // full-width rail spanning the day's active window with each timed event
    // plotted as a category-coloured block, hour ticks for orientation, and a live
    // "now" marker. It makes the day's SHAPE glanceable and uses the wide panel.
    function calendarRibbon(timed, isToday, nowMs) {
        if (!timed.length) return '';
        let winStart = 6 * 60, winEnd = 22 * 60;
        timed.forEach(function (e) {
            const s = calMinutes(e.start_time);
            if (!Number.isFinite(s)) return;
            winStart = Math.min(winStart, Math.floor(s / 60) * 60);
            winEnd = Math.max(winEnd, Math.ceil((s + calDuration(e)) / 60) * 60);
        });
        // Keep the live "now" marker inside the window on today's view.
        let nowMin = NaN;
        if (isToday) {
            const d = new Date(nowMs);
            nowMin = d.getHours() * 60 + d.getMinutes();
            winStart = Math.min(winStart, Math.floor(nowMin / 60) * 60);
            // Round UP to the next hour (exclusive) so the now-line always has room
            // to its right — on an exact hour boundary Math.ceil returns nowMin
            // unchanged, leaving the marker at the far edge (100%) where it clips.
            winEnd = Math.max(winEnd, (Math.floor(nowMin / 60) + 1) * 60);
        }
        winStart = Math.max(0, winStart);
        winEnd = Math.min(24 * 60, Math.max(winEnd, winStart + 60));
        const span = winEnd - winStart;
        const pct = function (min) { return ((min - winStart) / span) * 100; };

        // ~7 evenly-spaced hour labels across the window.
        const stepH = Math.max(1, Math.round((span / 60) / 7));
        let ticks = '';
        for (let h = Math.ceil(winStart / 60); h * 60 <= winEnd; h += stepH) {
            ticks += '<span class="cal-tick" style="left:' + pct(h * 60).toFixed(2) + '%">' +
                escapeHtml(calHourLabel(h)) + '</span>';
        }

        const blocks = timed.map(function (e) {
            const s = calMinutes(e.start_time);
            if (!Number.isFinite(s)) return '';
            const left = Math.max(0, pct(s));
            const width = Math.max(2.4, Math.min(100 - left, (calDuration(e) / span) * 100));
            const cat = calendarCategoryClass(e.category);
            const label = (e.title || e.name || 'Event') + ' · ' + String(e.start_time || '').slice(0, 5);
            return '<span class="cal-block sky-accent-' + escapeHtml(cat) + '" ' +
                'style="left:' + left.toFixed(2) + '%;width:' + width.toFixed(2) + '%" ' +
                'title="' + escapeHtml(label) + '"></span>';
        }).join('');

        let nowLine = '';
        if (Number.isFinite(nowMin) && nowMin >= winStart && nowMin <= winEnd) {
            nowLine = '<span class="cal-ribbon-now" style="left:' + pct(nowMin).toFixed(2) + '%"></span>';
        }

        return [
            '<div class="cal-ribbon" aria-hidden="true">',
            '<div class="cal-ribbon-rail">', blocks, nowLine, '</div>',
            '<div class="cal-ribbon-axis">', ticks, '</div>',
            '</div>'
        ].join('');
    }

    // Card header: weekday + date on the left; a live "now" chip (today only) and
    // the event count on the right.
    function calendarHead(dateMeta, countLabel, isToday, nowMs) {
        const clock = isToday
            ? '<span class="cal-now-chip"><span class="cal-now-dot" aria-hidden="true"></span>' +
                escapeHtml(new Date(nowMs).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })) + '</span>'
            : '';
        return [
            '<header class="cal-head">',
            '<div class="cal-head-day">',
            '<span class="cal-head-weekday">' + escapeHtml(dateMeta.weekday) + '</span>',
            '<span class="cal-head-date">' + escapeHtml(dateMeta.monthDay) + '</span>',
            '</div>',
            '<div class="cal-head-meta">' + clock +
            '<span class="cal-head-count">' + escapeHtml(countLabel) + '</span>',
            '</div>',
            '</header>'
        ].join('');
    }

    function renderCalendar(props) {
        const events = Array.isArray(props.events) ? props.events : [];
        const sorted = events.slice().sort((a, b) => calendarEventSortKey(a) - calendarEventSortKey(b)).slice(0, 12);
        const dateMeta = formatCalendarDate(props.date || props.start_date || (sorted[0] && sorted[0].start_date));
        const qualifier = String(props.qualifier || 'today').trim();
        const countLabel = events.length + ' ' + (events.length === 1 ? 'event' : 'events');
        const nowMs = Date.now();

        // Local (not UTC) "today", so the ribbon's now-line + live chip only appear
        // when the shown day really is today.
        const nowDate = new Date(nowMs);
        const pad = n => String(n).padStart(2, '0');
        const todayStr = nowDate.getFullYear() + '-' + pad(nowDate.getMonth() + 1) + '-' + pad(nowDate.getDate());
        const dates = [];
        sorted.forEach(function (e) {
            const d = String(e.start_date || e.date || '').slice(0, 10);
            if (d && dates.indexOf(d) < 0) dates.push(d);
        });
        const singleDay = dates.length <= 1;
        const shownDay = dates.length === 1 ? dates[0] : (dates.length === 0 && qualifier === 'today' ? todayStr : '');
        const isToday = singleDay && shownDay === todayStr;

        // Empty state — calm "Nothing scheduled".
        if (!sorted.length) {
            const emptyBody = [
                '<div class="cal-scene" data-daypart="' + calendarDaypart(nowMs) + '">',
                calendarHead(dateMeta, countLabel, isToday, nowMs),
                '<div class="cal-empty">',
                '<span class="cal-empty-mark" aria-hidden="true">' + calendarGlyphSvg() + '</span>',
                '<strong>Nothing scheduled</strong>',
                '<span>Your day is clear ' + escapeHtml(qualifier || 'for now') + '.</span>',
                '</div>',
                '</div>'
            ].join('');
            return cardFrame(Object.assign({ status: 'Calendar', icon: 'C' }, props), emptyBody, { wide: true, tone: 'calendar-card', hideHeader: true, hideStatus: true });
        }

        // All-day events live in a pinned chip band; the agenda below is the timed
        // (and untimed) events. Keeping them separate stops an all-day event from
        // being shown twice AND from hijacking the next-up highlight (it sorts 00:00).
        const allDay = sorted.filter(e => e.all_day);
        const agendaEvents = sorted.filter(e => !e.all_day);
        const timed = singleDay ? agendaEvents.filter(e => Number.isFinite(calMinutes(e.start_time))) : [];
        const ribbon = calendarRibbon(timed, isToday, nowMs);

        // next-up = soonest UPCOMING event; fall back to the first agenda event when
        // all are already past, so the highlight never lands on nothing.
        let heroIndex = agendaEvents.findIndex(item => {
            const ms = calendarEventStartMs(item);
            return Number.isFinite(ms) && ms >= nowMs;
        });
        if (heroIndex < 0) heroIndex = 0;

        const alldayBand = allDay.length ? (
            '<div class="cal-allday">' + allDay.map(function (e) {
                const cat = calendarCategoryClass(e.category);
                const label = e.title || e.name || 'All-day';
                return '<button type="button" class="cal-chip sky-accent-' + escapeHtml(cat) + '" data-sky-action="query" data-query="' + escapeHtml(calendarEditQuery(e, label)) + '">' +
                    '<span class="cal-chip-dot" aria-hidden="true"></span>' + escapeHtml(label) + '</button>';
            }).join('') + '</div>'
        ) : '';

        // Agenda: every timed event as a tap-to-edit row, next-up emphasised as
        // .cal-hero. Grouped by day when the range spans more than one date.
        let lastDate = null;
        const rows = agendaEvents.map(function (item, i) {
            const isHero = i === heroIndex;
            const cat = calendarCategoryClass(item.category);
            const title = item.title || item.name || 'Calendar event';
            const detail = [item.location].filter(Boolean).join(' · ');
            const gutter = calGutter(item);
            const when = isHero ? calendarCountdown(item, nowMs) : '';
            const isPast = (function () {
                const ms = calendarEventStartMs(item);
                return Number.isFinite(ms) && ms < nowMs && !item.all_day;
            })();

            let dayHead = '';
            if (!singleDay) {
                const d = String(item.start_date || item.date || '').slice(0, 10);
                if (d && d !== lastDate) {
                    lastDate = d;
                    const dm = formatCalendarDate(d);
                    dayHead = '<div class="cal-daygroup">' + escapeHtml(dm.weekday + ' · ' + dm.monthDay) + '</div>';
                }
            }

            const cls = 'cal-row' + (isHero ? ' cal-hero is-next' : '') + (isPast ? ' is-past' : '') + ' sky-accent-' + cat;
            const btn = [
                '<button type="button" class="' + cls + '" data-sky-action="query" data-query="' + escapeHtml(calendarEditQuery(item, title)) + '">',
                (isHero ? '<span class="cal-kicker">' + escapeHtml(isPast ? 'Earlier' : 'Up next') + '</span>' : ''),
                '<span class="cal-time tnum"><b>' + escapeHtml(gutter.start) + '</b>' +
                    (gutter.end ? '<i>' + escapeHtml(gutter.end) + '</i>' : '') + '</span>',
                '<span class="cal-node" aria-hidden="true"></span>',
                '<span class="cal-body"><strong>' + escapeHtml(title) + '</strong>' +
                    (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</span>',
                (isHero && when ? '<span class="cal-when">' + escapeHtml(when) + '</span>' : ''),
                '</button>'
            ].join('');
            return dayHead + btn;
        }).join('');

        // Flow the agenda into two columns on the wide panel once the day is busy,
        // so a long list fills the width instead of scrolling off the bottom.
        const cols = (singleDay && agendaEvents.length >= 6) ? '2' : '1';

        // A calm free-time note on light days keeps the wide card from reading empty.
        const freeNote = (singleDay && isToday && agendaEvents.length && agendaEvents.length <= 2)
            ? '<div class="cal-freenote">The rest of your day is clear.</div>'
            : '';

        const body = [
            '<div class="cal-scene" data-daypart="' + calendarDaypart(nowMs) + '">',
            calendarHead(dateMeta, countLabel, isToday, nowMs),
            ribbon,
            alldayBand,
            '<div class="cal-agenda" data-cols="' + cols + '">' + rows + '</div>',
            freeNote,
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Calendar', icon: 'C' }, props), body, { wide: true, tone: 'calendar-card', hideHeader: true, hideStatus: true });
    }

    function formatTemp(value) {
        if (value == null || value === '') return '--';
        const number = Number(value);
        return Number.isFinite(number) ? Math.round(number) + '°' : String(value);
    }

    function weatherClass(props, current) {
        const text = [
            current && current.description,
            current && current.condition,
            props.description,
            props.condition,
            current && current.icon
        ].filter(Boolean).join(' ').toLowerCase();
        if (/thunder|storm/.test(text)) return 'weather-stormy';
        if (/rain|drizzle|shower/.test(text)) return 'weather-rainy';
        if (/snow|sleet|ice/.test(text)) return 'weather-snowy';
        if (/fog|mist|haze|smoke/.test(text)) return 'weather-foggy';
        if (/cloud|overcast/.test(text)) return /part|02/.test(text) ? 'weather-partly-cloudy' : 'weather-cloudy';
        if (/night|\dn$/.test(text)) return 'weather-clear-night';
        return 'weather-sunny';
    }

    function weatherEmoji(current) {
        if (current && current.icon_emoji) return current.icon_emoji;
        const text = [
            current && current.description,
            current && current.condition,
            current && current.icon
        ].filter(Boolean).join(' ').toLowerCase();
        const icon = String(current && current.icon || '').toLowerCase();
        if (/thunder|storm|11/.test(text)) return '⛈️';
        if (/rain|drizzle|shower|09|10/.test(text)) return '🌧️';
        if (/snow|sleet|ice|13/.test(text)) return '❄️';
        if (/fog|mist|haze|smoke|50/.test(text)) return '🌫️';
        if (/part|02/.test(text)) return /n$/.test(icon) ? '🌙' : '🌤️';
        if (/cloud|overcast|03|04/.test(text)) return '☁️';
        if (/night|\dn$/.test(text)) return '🌙';
        return '☀️';
    }

    function formatForecastLabel(value) {
        const raw = String(value || '');
        const datePart = raw.slice(0, 10);
        if (/^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
            const date = new Date(datePart + 'T12:00:00');
            if (!Number.isNaN(date.getTime())) {
                return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
            }
        }
        return raw;
    }

    function formatForecastShort(value) {
        const raw = String(value || '');
        const datePart = raw.slice(0, 10);
        if (/^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
            const date = new Date(datePart + 'T12:00:00');
            if (!Number.isNaN(date.getTime())) {
                return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }).replace(',', '');
            }
        }
        const label = formatForecastLabel(value).replace(',', '');
        return label
            .replace(/^([A-Za-z]{3})\s+([A-Za-z]{3})\s+(\d+)$/, '$1 $3')
            .replace(/^([A-Za-z]{3})\s+(\d+)\s+([A-Za-z]{3})$/, '$1 $2');
    }

    function formatHourLabel(value) {
        const raw = String(value || '');
        const match = raw.match(/T?(\d{1,2}):(\d{2})/);
        if (!match) return raw || 'Now';
        const hour = Number(match[1]);
        if (!Number.isFinite(hour)) return raw;
        if (hour === 0) return '12am';
        if (hour === 12) return '12pm';
        return (hour > 12 ? hour - 12 : hour) + (hour > 11 ? 'pm' : 'am');
    }

    function formatWind(value) {
        // Zoe weather APIs expose wind_speed in metres per second; Skybridge displays km/h.
        if (value == null || value === '') return '--';
        const number = Number(value);
        if (!Number.isFinite(number)) return String(value);
        return Math.round(number * 3.6) + ' km/h';
    }

    function forecastTempBand(item) {
        const low = Number(item.low != null ? item.low : item.temp);
        const high = Number(item.high != null ? item.high : item.temp);
        const min = Number.isFinite(low) ? low : high;
        const max = Number.isFinite(high) ? high : low;
        if (!Number.isFinite(min) || !Number.isFinite(max)) {
            return { left: 28, width: 36 };
        }
        const rangeMin = -5;
        const rangeMax = 45;
        const clamp = value => Math.max(0, Math.min(100, ((value - rangeMin) / (rangeMax - rangeMin)) * 100));
        const left = Math.min(clamp(min), clamp(max));
        const right = Math.max(clamp(min), clamp(max));
        return {
            left: Math.round(left),
            width: Math.max(12, Math.round(right - left))
        };
    }

    function weatherValue(source, keys) {
        for (const key of keys) {
            if (source && source[key] != null && source[key] !== '') return source[key];
        }
        return null;
    }

    // Design system: condition → theme class + crafted glyph (sun/moon by panel
    // theme for "clear"). See docs/architecture/skybridge-design-system.md §4/§5.
    function weatherCondClass(text) {
        const t = String(text || '').toLowerCase();
        if (/storm|thunder|lightning/.test(t)) return 'storm';
        if (/rain|drizzle|shower|sleet/.test(t)) return 'rain';
        if (/cloud|overcast|fog|mist|haze/.test(t)) return 'cloudy';
        return 'clear';
    }
    function panelIsDark() {
        return !(typeof document !== 'undefined' && document.documentElement &&
                 document.documentElement.getAttribute('data-theme') === 'light');
    }
    function weatherGlyphId(text, dark) {
        const c = weatherCondClass(text);
        if (c === 'storm') return 'i-storm';
        if (c === 'rain') return 'i-rain';
        if (c === 'cloudy') return 'i-cloud';
        return dark ? 'i-moon' : 'i-sun';
    }
    function glyphSvg(id, size) {
        return '<svg class="sky-glyph" width="' + size + '" height="' + size + '" aria-hidden="true"><use href="#' + id + '"></use></svg>';
    }

    function renderWeather(props) {
        const current = props.current || {};
        const forecast = props.forecast || {};
        const daily = Array.isArray(forecast.daily) ? forecast.daily : [];
        const hourly = Array.isArray(forecast.hourly) ? forecast.hourly : [];
        const location = props.location || {};
        const place = [location.city || current.city || props.city].filter(Boolean).join(', ') || 'Current location';
        const description = current.description || current.condition || props.description || '';
        const dark = panelIsDark();
        const cond = weatherCondClass(description);
        const temp = formatTemp(weatherValue(current, ['temp', 'temperature', 'temperature_c', 'temp_c', 'current_temp']));
        const feels = formatTemp(weatherValue(current, ['feels_like', 'feels_like_c', 'apparent_temperature']));
        const hi = daily[0] && daily[0].high != null ? formatTemp(daily[0].high) : '';
        const lo = daily[0] && daily[0].low != null ? formatTemp(daily[0].low) : '';
        const humidity = current.humidity == null ? '' : current.humidity + '%';
        const wind = formatWind(current.wind_speed);

        const hourTiles = hourly.slice(0, 6).map((item, i) => {
            const label = i === 0 ? 'Now' : formatHourLabel(item.time || item.day || '');
            const g = weatherGlyphId(item.description || item.condition || description, dark);
            const t = formatTemp(weatherValue(item, ['temp', 'temperature', 'temperature_c', 'temp_c', 'high']));
            return '<div class="wx-hc"><span class="wx-ht">' + escapeHtml(label) + '</span>' + glyphSvg(g, 44) + '<span class="wx-hv tnum">' + escapeHtml(t) + '</span></div>';
        }).join('');

        const dayRows = daily.slice(1, 5).map(item => {
            const label = formatForecastShort(item.day || item.time || '');
            const g = weatherGlyphId(item.description || item.condition || '', dark);
            const high = item.high != null ? formatTemp(item.high) : formatTemp(item.temp);
            const low = item.low != null ? formatTemp(item.low) : '';
            return '<div class="wx-drow"><span class="wx-sld">' + escapeHtml(label) + '</span>' + glyphSvg(g, 46) + '<span class="wx-dt tnum"><b>' + escapeHtml(high) + '</b> <span class="wx-lo">' + escapeHtml(low) + '</span></span></div>';
        }).join('');

        const detail = [
            (hi || lo) ? 'H ' + hi + ' L ' + lo : '',
            humidity ? 'Humidity ' + humidity : '',
            wind || ''
        ].filter(Boolean).map(escapeHtml).join('<br>');

        const body = [
            '<div class="wx-card">',
            '<div class="wx-main">',
            '<div class="wx-head"><div><div class="wx-place">' + escapeHtml(place) + '</div><div class="wx-cond">' + escapeHtml(description) + (feels ? ' · feels ' + escapeHtml(feels) : '') + '</div></div>' + glyphSvg(weatherGlyphId(description, dark), 84) + '</div>',
            '<div class="wx-hero"><div class="wx-temp tnum">' + escapeHtml(temp) + '</div><div class="wx-detail tnum">' + detail + '</div></div>',
            hourTiles ? '<div class="wx-hr">' + hourTiles + '</div>' : '',
            '</div>',
            dayRows ? '<div class="wx-div"></div><div class="wx-side"><div class="wx-nd">Next days</div>' + dayRows + '</div>' : '',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Weather', icon: 'W' }, props), body,
            { wide: true, tone: 'weather-card wx-' + cond, hideHeader: true, hideStatus: true, hideActions: true });
    }

    function clockParts(timezone) {
        const options = timezone ? { timeZone: timezone } : {};
        const now = new Date();
        const time = new Intl.DateTimeFormat(undefined, Object.assign({
            hour: 'numeric',
            minute: '2-digit',
            hour12: true
        }, options)).formatToParts(now);
        const date = new Intl.DateTimeFormat(undefined, Object.assign({
            weekday: 'long',
            day: 'numeric',
            month: 'long'
        }, options)).format(now);
        const hour = (time.find(part => part.type === 'hour') || {}).value || '';
        const minute = (time.find(part => part.type === 'minute') || {}).value || '';
        const dayPeriod = (time.find(part => part.type === 'dayPeriod') || {}).value || '';
        return { hour, minute, dayPeriod, date };
    }

    function clockGreeting(timezone) {
        var h;
        try {
            h = parseInt(new Intl.DateTimeFormat('en-US', { hour: 'numeric', hour12: false, timeZone: timezone || undefined }).format(new Date()), 10);
        } catch (e) { h = NaN; }
        if (!isFinite(h)) h = new Date().getHours();
        if (h < 5) return 'Good night';
        if (h < 12) return 'Good morning';
        if (h < 17) return 'Good afternoon';
        if (h < 21) return 'Good evening';
        return 'Good night';
    }
    function clockUserName() {
        try {
            var c = JSON.parse(sessionStorage.getItem('zoe_panel_auth_challenge') || '{}');
            var n = c.selected_username || '';
            return n && n.toLowerCase() !== 'guest' ? n : '';
        } catch (e) { return ''; }
    }
    function fmtClockTime(d) {
        try { return d ? new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', hour12: true }).format(d) : ''; } catch (e) { return ''; }
    }
    // Small filled sun-on-horizon glyph for the sunrise/sunset rows (up = sunrise).
    function horizonGlyph(up, color) {
        var arrow = up
            ? '<path d="M12 12.5V7M9.4 9.6 12 7l2.6 2.6" stroke="' + color + '" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            : '<path d="M12 7v5.5M9.4 9.9 12 12.5l2.6-2.6" stroke="' + color + '" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
        return '<svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">'
            + '<path d="M7.5 18a4.5 4.5 0 0 1 9 0z" fill="' + color + '" opacity=".95"/>'
            + '<line x1="3.5" y1="18" x2="20.5" y2="18" stroke="' + color + '" stroke-width="1.7" stroke-linecap="round"/>'
            + arrow + '</svg>';
    }

    function renderClock(props) {
        const timezone = props.timezone || '';
        const parts = clockParts(timezone);
        const dateText = parts.date || [props.weekday, props.date_label].filter(Boolean).join(', ');
        const name = clockUserName();
        const greet = clockGreeting(timezone) + (name ? ', ' + name : '');
        let sun = null;
        try { sun = (window.SkybridgeTheme && window.SkybridgeTheme.sunTimes) ? window.SkybridgeTheme.sunTimes() : null; } catch (e) {}
        const sunRows = sun ? [
            '<div class="clock-sun-row"><span class="clock-sun-lbl">' + horizonGlyph(false, '#ff9a6b') + 'Sunset</span><span class="tnum clock-sun-t">' + escapeHtml(fmtClockTime(sun.set)) + '</span></div>',
            '<div class="clock-sun-row"><span class="clock-sun-lbl">' + horizonGlyph(true, '#ffce70') + 'Sunrise</span><span class="tnum clock-sun-t">' + escapeHtml(fmtClockTime(sun.rise)) + '</span></div>'
        ].join('') : '';
        // Stars + both glyphs are always in the DOM; CSS shows the right ones per
        // [data-theme], so a live sunrise/sunset theme flip (60s tick) updates them
        // without a re-render.
        const stars = '<span class="clock-star" style="top:18%;left:9%"></span><span class="clock-star" style="top:30%;left:24%"></span><span class="clock-star" style="top:14%;left:40%"></span><span class="clock-star" style="top:40%;left:15%"></span>';
        // Keep .sky-clock-scene/.sky-live-clock + the data-clock-* spans so
        // updateAllClocks() keeps the numerals ticking live.
        // New class names (clock-*) sidestep the legacy clock CSS entirely; only
        // .sky-live-clock + the data-clock-* attributes are kept (all the live
        // updater needs).
        const body = [
            '<div class="clock-scene sky-live-clock" data-timezone="' + escapeHtml(timezone) + '">',
            stars,
            '<div class="clock-main">',
            '<div class="clock-greet">' + escapeHtml(greet) + '</div>',
            '<div class="clock-time"><span class="clock-h" data-clock-hour>' + escapeHtml(parts.hour || props.hour || '') + '</span><i class="clock-colon">:</i><span class="clock-m" data-clock-minute>' + escapeHtml(parts.minute || props.minute || '') + '</span><b class="clock-mer" data-clock-meridiem>' + escapeHtml(parts.dayPeriod || props.meridiem || '') + '</b></div>',
            '<div class="clock-date" data-clock-date>' + escapeHtml(dateText) + '</div>',
            '</div>',
            '<div class="clock-div"></div>',
            '<div class="clock-side">',
            '<span class="clock-glyph clock-glyph-night">' + glyphSvg('i-moon', 86) + '</span>',
            '<span class="clock-glyph clock-glyph-day">' + glyphSvg('i-sun', 86) + '</span>',
            sunRows ? '<div class="clock-sun">' + sunRows + '</div>' : '',
            '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Clock', icon: 'C' }, props), body, { wide: true, tone: 'clock-card', hideHeader: true, hideStatus: true, hideActions: true });
    }

    function normalizeListItems(items) {
        return Array.isArray(items) ? items : [];
    }

    function listAccentClass(value) {
        return accentClass(value, 'general');
    }

    function listLabel(list) {
        return list.name || list.list_name || list.title || list.list_type || 'List';
    }

    function listQuery(list) {
        return 'show my ' + listLabel(list) + ' list';
    }

    // Top tab-row switcher: the ACTIVE tab IS the list's title (no separate big
    // heading — that only wasted vertical space on the 7" kiosk). Each tab carries
    // its open-item count so the shopper scans lists at a glance. The row is
    // indented past the top-left Home pill (see .lst-switcher padding in CSS).
    function renderListSwitcher(lists, selectedId) {
        const tabs = (Array.isArray(lists) ? lists : []).slice(0, 6).map(list => {
            const accent = listAccentClass(list.list_type);
            const selected = selectedId && String(list.id || '') === String(selectedId) ? ' is-active' : '';
            const items = Array.isArray(list.items) ? list.items : null;
            // Prefer the authoritative open_count from the payload; else derive from
            // the items slice. Shows items still to do (0 reads as "all done").
            let count = null;
            if (typeof list.open_count === 'number') count = list.open_count;
            else if (items) count = items.filter(it => !(typeof it === 'object' && it && it.completed)).length;
            const countTag = count != null ? '<i class="lst-tab-count" aria-hidden="true">' + escapeHtml(count) + '</i>' : '';
            return '<button type="button" class="lst-tab lst-a-' + escapeHtml(accent) + selected + '" role="tab" aria-selected="' + (selected ? 'true' : 'false') + '" data-sky-action="query" data-query="' + escapeHtml(listQuery(list)) + '"><span class="lst-tab-dot" aria-hidden="true"></span><span class="lst-tab-name">' + escapeHtml(listLabel(list)) + '</span>' + countTag + '</button>';
        }).join('');
        // Only real list tabs live in the tablist; "+ New" is an action, not a tab,
        // so it sits outside the tablist (a11y). .lst-tablist is display:contents so
        // the tabs still flex within .lst-switcher.
        return '<div class="lst-switcher"><div class="lst-tablist" role="tablist">' + tabs + '</div>' +
            '<button type="button" class="lst-tab lst-tab-new" data-sky-action="query" data-query="new list"><span class="lst-tab-name">+ New</span></button></div>';
    }

    // The tap query for a row. Open item → tick off; done item → restore. Direction
    // is explicit so the backend complete_item resolver never has to guess.
    function listCheckQuery(title, listType, completed) {
        const type = String(listType || 'shopping').trim() || 'shopping';
        return (completed ? 'uncheck ' : 'check off ') + title + ' on the ' + type + ' list';
    }

    // Custom filled check glyph (NOT a thin line icon — see design-system §5/§12).
    // Card-local so it does not depend on the shared sprite shipping a list glyph.
    const LIST_CHECK_SVG = '<svg class="lst-check-mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.6 16.2 5.4 12l-1.5 1.5 5.7 5.7L21 7.5 19.5 6z"/></svg>';

    // Category → design-system accent token (colour with intent, §4). Falls back to
    // the list's own accent so an uncategorised row still reads as part of the list.
    function listCategoryAccent(category, fallbackAccent) {
        const cat = String(category || '').trim();
        if (!cat) return fallbackAccent || 'general';
        return listAccentClass(cat);
    }

    function renderListItemRow(item, index, listType, fallbackAccent) {
        const isObject = typeof item === 'object' && item;
        const title = isObject ? (item.text || item.title || item.label || 'List item') : String(item || 'List item');
        const completed = !!(isObject && item.completed);
        const quantity = isObject && item.quantity != null && String(item.quantity).trim() !== '' ? String(item.quantity).trim() : '';
        const category = isObject && item.category ? String(item.category).trim() : '';
        const catAccent = listCategoryAccent(category, fallbackAccent);
        const priorityValue = isObject && item.priority ? String(item.priority).trim().toLowerCase() : '';
        const flagged = !completed && priorityValue && priorityValue !== 'normal' && priorityValue !== 'low';
        const qtyTag = quantity
            ? '<span class="lst-qty" aria-label="Quantity ' + escapeHtml(quantity) + '">' + escapeHtml(quantity) + '</span>'
            : '';
        // Tap = tick it off (or restore a done item). The single most common list
        // gesture on a kiosk — routed through the real complete_item action loop
        // (mutate -> authoritative re-read -> refreshed card). Whole row is the
        // target so it's a comfortable fingertip hit on the 7" panel.
        return [
            '<button type="button" class="lst-row lst-a-' + escapeHtml(catAccent) + (completed ? ' is-done' : '') + (flagged ? ' is-flagged' : '') + '" data-sky-action="query" data-query="' + escapeHtml(listCheckQuery(title, listType, completed)) + '" aria-pressed="' + (completed ? 'true' : 'false') + '" aria-label="' + escapeHtml((completed ? 'Done: ' : '') + title) + '">',
            '<span class="lst-box" aria-hidden="true">' + LIST_CHECK_SVG + '</span>',
            '<span class="lst-text">' + escapeHtml(title) + '</span>',
            qtyTag,
            '</button>'
        ].join('');
    }

    function renderListColumn(list) {
        const items = Array.isArray(list.items) ? list.items : [];
        const accent = listAccentClass(list.list_type);
        const done = items.filter(it => typeof it === 'object' && it && it.completed).length;
        const pct = items.length ? Math.round((done / items.length) * 100) : 0;
        const preview = items.slice(0, 4).map((item) => {
            const obj = typeof item === 'object' && item;
            const title = obj ? (item.text || item.title || item.label || 'Item') : String(item || 'Item');
            const isDone = !!(obj && item.completed);
            return '<li' + (isDone ? ' class="is-done"' : '') + '><span class="lst-dot" aria-hidden="true"></span><strong>' + escapeHtml(title) + '</strong></li>';
        }).join('');
        const countLabel = items.length === 1 ? '1 item' : items.length + ' items';
        return [
            '<button type="button" class="lst-col lst-a-' + escapeHtml(accent) + '" data-sky-action="query" data-query="' + escapeHtml(listQuery(list)) + '">',
            '<span class="lst-col-head"><strong>' + escapeHtml(listLabel(list)) + '</strong><em>' + escapeHtml(countLabel) + '</em></span>',
            '<span class="lst-col-bar" aria-hidden="true"><i style="width:' + pct + '%"></i></span>',
            preview ? '<ul>' + preview + '</ul>' : '<p class="lst-col-empty">No items yet</p>',
            '</button>'
        ].join('');
    }

    // List-type → identity (glyph + tint class + ring gradient). Warm for
    // shopping/cooking, cool for work/tasks, etc. (§4 colour with intent).
    function listIdentity(accent) {
        const map = {
            shopping:  { glyph: 'cart',  tint: 'warm' },
            household: { glyph: 'home',  tint: 'warm' },
            family:    { glyph: 'home',  tint: 'warm' },
            work:      { glyph: 'check', tint: 'cool' },
            tasks:     { glyph: 'check', tint: 'cool' },
            health:    { glyph: 'heart', tint: 'mint' },
            medical:   { glyph: 'heart', tint: 'mint' },
            routine:   { glyph: 'check', tint: 'cool' },
            social:    { glyph: 'star',  tint: 'violet' },
            personal:  { glyph: 'star',  tint: 'violet' },
            bucket:    { glyph: 'star',  tint: 'violet' }
        };
        return map[accent] || { glyph: 'check', tint: 'neutral' };
    }

    // Card-local filled identity glyphs (filled/dimensional, never line icons §5).
    function listIdentityGlyph(name) {
        const glyphs = {
            cart:  '<path d="M3 4h2.2l2 11.2A2 2 0 0 0 9.2 17h8.2a2 2 0 0 0 2-1.6l1.5-7.4H6.4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="9.5" cy="20.5" r="1.6"/><circle cx="17.5" cy="20.5" r="1.6"/>',
            check: '<path d="M9.6 16.8 5 12.2l-1.7 1.7 6.3 6.3L21.5 8.3 19.8 6.6z"/>',
            home:  '<path d="M12 3 3 10.5V21h6v-6h6v6h6V10.5z"/>',
            heart: '<path d="M12 21s-7.5-4.7-9.7-9C1 9.2 2.4 5.8 5.6 5.2 8 4.8 10.4 6.3 12 8.4c1.6-2.1 4-3.6 6.4-3.2 3.2.6 4.6 4 3.3 6.8C19.5 16.3 12 21 12 21z"/>',
            star:  '<path d="M12 2.5 14.9 9l7 .6-5.3 4.6 1.6 6.9L12 17.5 5.8 21l1.6-6.9L2.1 9.6l7-.6z"/>'
        };
        return '<svg class="lst-id-glyph" viewBox="0 0 24 24" aria-hidden="true">' + (glyphs[name] || glyphs.check) + '</svg>';
    }

    function renderZoeList(props) {
        const items = normalizeListItems(props.items);
        const lists = Array.isArray(props.lists) ? props.lists : [];
        const listType = props.list_type || (lists[0] && lists[0].list_type) || 'all';
        const accent = listAccentClass(listType);
        const selectedId = props.list_id && props.list_id !== 'lists-overview' ? props.list_id : '';
        const visibleItems = items.slice(0, 24);
        const rows = visibleItems.map((item, index) => renderListItemRow(item, index, listType, accent)).join('');
        // Overview = the multi-list catalog, shown only when NO single list is
        // selected. A selected-but-empty list gets the dedicated empty state below.
        const overviewCols = !items.length && !selectedId && lists.length ? '<div class="lst-cols">' + lists.slice(0, 6).map(renderListColumn).join('') + '</div>' : '';
        const empty = [
            '<div class="sky-empty-data lst-empty">',
            '<strong>No items in ' + escapeHtml(props.list_name || 'this list') + '</strong>',
            '<span>Tap “Add item” or ask Zoe to put something on this list.</span>',
            '</div>'
        ].join('');
        const isOverview = !rows && !!overviewCols;
        const ident = listIdentity(accent);

        // Progress is over the WHOLE list (not just the visible slice) so the % is
        // honest even when more than 24 items exist.
        const totalDone = items.filter(it => typeof it === 'object' && it && it.completed).length;
        const openCount = Math.max(0, items.length - totalDone);
        const pct = items.length ? Math.round((totalDone / items.length) * 100) : 0;
        // Slim progress + open-first count line ("3 left · 2 done") — the shopper's
        // view. Sits just under the tab row; the active tab is the title now.
        let progressLabel = '';
        if (isOverview) {
            progressLabel = lists.length === 1 ? '1 list' : lists.length + ' lists';
        } else if (!items.length) {
            progressLabel = 'Empty list';
        } else if (totalDone) {
            progressLabel = openCount + ' left · ' + totalDone + ' done';
        } else {
            progressLabel = items.length + (items.length === 1 ? ' item' : ' items');
        }
        const progressBar = (!isOverview && items.length)
            ? '<div class="lst-progress" role="img" aria-label="' + totalDone + ' of ' + items.length + ' done"><span class="lst-progress-fill" style="width:' + pct + '%"></span></div>'
            : '';
        const meta = '<div class="lst-meta"><span class="lst-meta-count">' + escapeHtml(progressLabel) + '</span>' + progressBar + '</div>';

        // Fallback title chip: only when there are NO tabs to act as the title
        // (e.g. guest sessions with no list catalog). Normally the active tab titles.
        const fallbackTitle = !lists.length
            ? '<div class="lst-solo-title"><span class="lst-id-badge">' + listIdentityGlyph(ident.glyph) + '</span><h3 class="lst-name">' + escapeHtml(props.list_name || 'List') + '</h3></div>'
            : '';

        // "+ Add item" — tapping opens the composer prefilled "add ⟂ to the <type> list"
        // (caret after "add ") so you can type or speak the item. Shown on any
        // single-list view (incl. an empty one); not on the multi-list overview.
        const addRow = !isOverview
            ? '<button type="button" class="lst-row lst-add" data-sky-action="compose"' +
              ' data-compose="add  to the ' + escapeHtml(listType) + ' list" data-compose-caret="4"' +
              ' aria-label="Add an item to this list">' +
              '<span class="lst-box lst-add-plus" aria-hidden="true">+</span>' +
              '<span class="lst-text lst-add-label">Add item</span></button>'
            : '';

        // Orb keep-out: an invisible layout reservation pinned to the bottom-left
        // grid cells so items flow AROUND the Zoe orb + voice pill (never behind
        // them). Explicitly placed, so grid auto-flow fills every other cell.
        const keepOut = '<i class="lst-keepout" aria-hidden="true"></i>';

        let itemsClass;
        let itemsInner;
        let itemsStyle = '';
        if (isOverview) {
            itemsClass = 'lst-items is-overview';
            itemsInner = overviewCols;
        } else if (rows) {
            // Multi-column grid (newspaper flow) so a long list fills the width
            // instead of one tall skinny column, wrapping around the orb. A short
            // list uses only as many rows as it needs (no fixed ~604px reservation)
            // and skips the orb keep-out — it's too short to reach the bottom-left
            // orb zone, so it never overlaps.
            const cells = visibleItems.length + 1; // items + add-row
            const compact = cells <= 6;
            itemsClass = 'lst-items is-grid';
            if (compact) {
                itemsStyle = ' style="--lst-rows:' + Math.max(3, cells) + '"';
                itemsInner = addRow + rows;               // no keep-out needed
            } else {
                itemsInner = keepOut + addRow + rows;      // 9-row wrap around the orb
            }
        } else {
            // Add-row pinned to the top (clear of the bottom-left orb); the empty
            // message fills the remaining space, centered.
            itemsClass = 'lst-items is-empty';
            itemsInner = addRow + empty;
        }

        const body = [
            '<div class="lst-scene lst-a-' + escapeHtml(accent) + ' lst-tint-' + ident.tint + '">',
            '<div class="lst-top">',
            lists.length ? renderListSwitcher(lists, selectedId) : fallbackTitle,
            meta,
            '</div>',
            '<div class="' + itemsClass + '"' + itemsStyle + '>' + itemsInner + '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Lists', icon: 'L' }, props), body, { wide: true, tone: 'zoe-list-card ' + accent, hideHeader: true, hideStatus: true, hideActions: true });
    }

    function initialsFor(name) {
        const parts = String(name || 'Z').trim().split(/\s+/).filter(Boolean);
        return escapeHtml((parts[0] || 'Z').charAt(0).toUpperCase() + (parts[1] || '').charAt(0).toUpperCase());
    }

    function healthPercent(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return 50;
        return Math.max(0, Math.min(100, Math.round(number <= 1 ? number * 100 : number)));
    }

    function personSubline(person) {
        return [person.relationship, person.context, person.circle].filter(Boolean).join(' · ') || 'Contact';
    }

    function personAccentClass(person) {
        return accentClass(person.context || person.circle || 'personal', 'personal');
    }

    // Closeness: inner circle is "your people" — surface them first, then order by
    // connection health so the warmest relationships lead. Returns 0 (inner) | 1.
    function personCircleRank(person) {
        var c = String((person && person.circle) || '').toLowerCase();
        if (c === 'inner' || c === 'close' || c === 'family') return 0;
        return 1;
    }
    // Short, human circle label for the small relationship/closeness tag.
    function personCircleLabel(person) {
        var c = String((person && person.circle) || '').toLowerCase();
        if (c === 'inner' || c === 'close') return 'Inner circle';
        if (c === 'outer' || c === 'wider') return 'Wider circle';
        if (c) return c.charAt(0).toUpperCase() + c.slice(1);
        return personCircleRank(person) === 0 ? 'Inner circle' : 'Wider circle';
    }
    // Avatar contents: the contact's photo over their initials. The photo is a
    // background-image layer, so if the URL is missing or fails to load the
    // initials simply show through (no broken-image icon). Initials stay in the
    // DOM as the accessible/fallback label.
    function personAvatarInner(person) {
        var initials = initialsFor(person && person.name);
        var raw = person && (person.photo || person.photo_url || person.avatar_url || person.image || person.picture);
        var url = raw == null ? '' : String(raw).trim();
        // Scheme allowlist: only https or a root-relative same-origin path. Rejects
        // http (mixed content), file:/// (local-file read in a WebView shell), data:
        // (large inline blobs), protocol-relative //host, and javascript:. A contact
        // photo URL can come from a synced external source, so this stays strict.
        var safeScheme = /^https:\/\//i.test(url) || /^\/[^/]/.test(url);
        if (url && safeScheme) {
            var safe = url.replace(/["'()\\]/g, '');
            return initials + '<span class="people-avatar-img" style="background-image:url(\'' + escapeHtml(safe) + '\')" aria-hidden="true"></span>';
        }
        return initials;
    }
    // Stable closeness order: inner first, then higher connection-health first.
    function peopleByCloseness(list) {
        return list
            .map(function (p, i) { return { p: p, i: i }; })
            .sort(function (a, b) {
                var ra = personCircleRank(a.p), rb = personCircleRank(b.p);
                if (ra !== rb) return ra - rb;
                var ha = healthPercent(a.p.health_score), hb = healthPercent(b.p.health_score);
                if (ha !== hb) return hb - ha;
                return a.i - b.i;
            })
            .map(function (x) { return x.p; });
    }

    function renderPeopleDirectory(props) {
        // Re-skin to docs/architecture/skybridge-design-system.md §8/§11 (people =
        // header + a grid of metric stacks: avatar, name, relationship, accent-
        // gradient health bar). Data contract is unchanged — re-skin only.
        const people = Array.isArray(props.people) ? props.people : [];
        const total = props.count == null ? people.length : props.count;
        const workCount = people.filter(person => personAccentClass(person) === 'work').length;
        const personalCount = people.filter(person => personAccentClass(person) === 'personal').length;

        // Segmented filter chips (count + label). Each chip is a REAL tap target →
        // submitCommand(query) the people resolver actually handles: the directory
        // ("all") or a context filter the backend supports (PEOPLE_CONTEXTS =
        // personal, work). We deliberately do NOT render an "other" chip — there is
        // no backend filter for it, so it would be a silent no-op (same result as
        // "all"). `active` reflects the current filter so the user sees where they are.
        const activeFilter = accentClass(props.circle || props.context || '', '');
        const chip = function (accent, label, value, query, alwaysShow) {
            if (!value && !alwaysShow) return '';
            const active = accent && accent === activeFilter ? ' is-active' : '';
            return [
                '<button type="button" class="people-chip people-accent-' + escapeHtml(accent || 'all') + active + '"',
                ' data-sky-action="query" data-query="' + escapeHtml(query) + '"',
                ' aria-label="' + escapeHtml(label + ', ' + value) + '" aria-pressed="' + (active ? 'true' : 'false') + '">',
                '<i class="people-chip-dot" aria-hidden="true"></i>',
                '<strong>' + escapeHtml(value) + '</strong>',
                '<span>' + escapeHtml(label) + '</span>',
                '</button>'
            ].join('');
        };
        const chips = [
            chip('', 'all', total, 'show people', true),
            chip('personal', 'personal', personalCount, 'show personal contacts', false),
            chip('work', 'work', workCount, 'show work contacts', false)
        ].join('');

        // Order by closeness (inner circle first, warmest connection first) so the
        // card reads as "your people", not an alphabetical contacts dump. The
        // closest few get a larger hero row; the rest flow into a calmer grid.
        const ordered = peopleByCloseness(people).slice(0, 12);
        const innerLead = ordered.filter(p => personCircleRank(p) === 0);
        const heroCount = Math.min(innerLead.length, ordered.length >= 4 ? 3 : 0);
        const heroPeople = ordered.slice(0, heroCount);
        const restPeople = ordered.slice(heroCount);

        // Shared tile builder. `hero` enlarges the avatar/type and shows a richer
        // closeness tag; both variants carry the accent + connection health bar.
        const tileHtml = function (person, hero) {
            const health = healthPercent(person.health_score);
            const accent = personAccentClass(person);
            const name = person.name || 'Person';
            const cls = 'people-tile people-accent-' + escapeHtml(accent) + (hero ? ' people-tile--hero' : '');
            return [
                '<button type="button" class="' + cls + '" data-sky-action="query" data-query="' + escapeHtml('find ' + name) + '" aria-label="' + escapeHtml(name + ', ' + personSubline(person) + ', connection ' + health + ' percent') + '">',
                '<span class="people-tile-tint" aria-hidden="true"></span>',
                '<span class="people-tile-avatar" aria-hidden="true">' + personAvatarInner(person) + '</span>',
                '<span class="people-tile-id">',
                '<span class="people-circle-pill">' + escapeHtml(personCircleLabel(person)) + '</span>',
                '<strong class="people-tile-name">' + escapeHtml(name) + '</strong>',
                '<span class="people-tile-rel">' + escapeHtml(personSubline(person)) + '</span>',
                '</span>',
                '<span class="people-tile-health" aria-hidden="true">',
                '<span class="people-health-pct tnum">' + health + '</span>',
                '<span class="people-health-track"><i class="people-health-fill" style="height:' + health + '%"></i></span>',
                '</span>',
                '</button>'
            ].join('');
        };

        const heroRow = heroPeople.length
            ? '<div class="people-hero"><p class="people-section-label">Closest to you</p><div class="people-hero-row">'
                + heroPeople.map(p => tileHtml(p, true)).join('') + '</div></div>'
            : '';
        const restGrid = restPeople.length
            ? (heroPeople.length ? '<p class="people-section-label people-section-label--rest">More people</p>' : '')
                + '<div class="people-grid">' + restPeople.map(p => tileHtml(p, false)).join('') + '</div>'
            : '';
        const tiles = heroRow + restGrid;

        const empty = [
            '<div class="people-empty">',
            '<span class="people-empty-glyph" aria-hidden="true">' + initialsFor(props.title || 'People') + '</span>',
            '<strong>No matching people</strong>',
            '<span>Zoe did not find contacts for this request.</span>',
            '</div>'
        ].join('');

        const body = [
            '<div class="people-scene">',
            '<header class="people-header">',
            '<div class="people-heading">',
            '<p class="people-eyebrow">People</p>',
            '<h3 class="people-title">' + escapeHtml(props.title || 'People') + '</h3>',
            (props.query || props.context || props.circle)
                ? '<p class="people-context">' + escapeHtml([props.query, props.context, props.circle].filter(Boolean).join(' · ')) + '</p>'
                : '',
            '</div>',
            '<div class="people-chips">' + chips + '</div>',
            '</header>',
            tiles ? ('<div class="people-body">' + tiles + '</div>') : empty,
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'People', icon: 'P' }, props), body, { wide: true, tone: 'people-card', hideHeader: true, hideStatus: true });
    }

    function renderPersonProfile(props) {
        // Sibling of renderPeopleDirectory — same avatar + accent-gradient health
        // language (design-system §11), scaled up to a single-contact hero.
        const person = props.person || {};
        const health = healthPercent(person.health_score);
        const accent = personAccentClass(person);
        const name = person.name || props.title || 'Person';
        const contactRows = [
            ['Phone', person.phone],
            ['Email', person.email],
            ['Birthday', person.birthday],
            ['Last contact', person.last_contacted_at]
        ].filter(pair => pair[1]).map(pair => '<div class="people-detail"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        const body = [
            '<div class="people-scene people-profile">',
            '<header class="people-profile-hero">',
            '<span class="people-tile-tint" aria-hidden="true"></span>',
            '<span class="people-tile-avatar people-profile-avatar" aria-hidden="true">' + personAvatarInner(person) + '</span>',
            '<div class="people-profile-id">',
            '<span class="people-circle-pill">' + escapeHtml(personCircleLabel(person)) + '</span>',
            '<p class="people-eyebrow">' + escapeHtml(personSubline(person)) + '</p>',
            '<h3 class="people-title">' + escapeHtml(name) + '</h3>',
            '<div class="people-tile-health people-profile-healthrow">',
            '<span class="people-health-track"><i class="people-health-fill" style="width:' + health + '%"></i></span>',
            '<span class="people-health-pct">' + health + '</span>',
            '<span class="people-health-label">connection</span>',
            '</div>',
            '</div>',
            '</header>',
            contactRows ? '<div class="people-detail-grid">' + contactRows + '</div>' : '',
            person.notes ? '<p class="people-notes">' + escapeHtml(person.notes) + '</p>' : '',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Person', icon: 'P' }, props), body, { wide: true, tone: 'people-card people-profile-card people-accent-' + accent, hideHeader: true, hideStatus: true });
    }


    function renderPage(props) {
        const fields = [
            ['Surface', props.title || 'Page'],
            ['Best for', props.summary || 'Zoe context'],
            ['Mode', 'Open, summarize, or keep as context']
        ].map(pair => listRowNode(pair[0], pair[1]));
        var cardActions = [
            { label: 'Open page', route: props.route, type: 'open' },
            { label: 'Show related settings', query: props.title + ' settings' }
        ];
        const tree = { component: 'Stack', gap: 'md', children: [
            textNode(props.summary || '', 'body'),
            { component: 'Stack', gap: 'sm', children: fields }
        ] };
        return cardFrame(Object.assign({}, props, { actions: cardActions, status: props.status || 'Surface' }), composedBody(tree, props.summary || ''), { wide: false, tone: 'page-card' });
    }

    function renderSetting(props) {
        const risk = props.risk || 'low';
        const changeLabel = risk === 'critical' ? 'Prepare change' : 'Change setting';
        const fields = [
            ['Risk', risk],
            ['Control area', props.domain || 'settings']
        ].map(pair => listRowNode(pair[0], pair[1]));
        var settingActions = [
            { label: 'Open settings', route: props.route, type: 'open' },
            { label: changeLabel, query: 'change ' + props.title, kind: risk === 'critical' ? 'warn' : 'normal' }
        ];
        const tree = { component: 'Stack', gap: 'md', children: [
            textNode(props.summary || '', 'body'),
            { component: 'Stack', gap: 'sm', children: fields }
        ] };
        return cardFrame(Object.assign({ status: risk }, props, { actions: settingActions }), composedBody(tree, props.summary || ''), { wide: false, tone: 'setting-card' });
    }

    function renderPageGrid(props) {
        const rows = (props.items || []).slice(0, 4).map(item => listRowNode(item.title, item.summary));
        const tree = { component: 'Grid', columns: 2, children: rows };
        return cardFrame(Object.assign({ status: 'Map' }, props), composedBody(tree, props.summary || props.title || ''), { wide: true, tone: 'map-card' });
    }

    function renderSettingsOverview(props) {
        const rows = (props.items || []).slice(0, 4).map(item => {
            const label = item.risk ? item.title + ' · ' + item.risk : item.title;
            return listRowNode(label, item.summary);
        });
        const tree = { component: 'Grid', columns: 2, children: rows };
        return cardFrame(Object.assign({ status: 'Settings' }, props), composedBody(tree, props.summary || props.title || ''), { wide: true, tone: 'map-card' });
    }

    function renderList(props) {
        const rows = (props.items || []).slice(0, 6).map((item, index) => {
            const title = typeof item === 'string' ? item : item.title || item.text || item.label || JSON.stringify(item);
            const detail = typeof item === 'object' && item ? (item.summary || item.description || item.value || '') : '';
            // Preserve the legacy zero-padded position cue (01, 02, ...) — generic
            // lists are often ordered (results, rankings); it rides the detail slot.
            const cue = String(index + 1).padStart(2, '0');
            return listRowNode(title, detail ? cue + ' · ' + detail : cue);
        });
        const tree = { component: 'Stack', gap: 'sm', children: rows };
        return cardFrame(Object.assign({ status: props.status || 'List' }, props), composedBody(tree, props.title || ''), { wide: false, tone: 'list-card' });
    }

    // smart_home cards (contract: content {title, devices, scenes}). Rendered as a
    // room-controls surface: device tiles toggle on tap (data-sky-action="query"
    // re-enters the resolver so tap + voice share one path), scene chips run a
    // routine. Tolerates the executor overlay's entities/items aliases.
    function shDeviceIcon(domain, on) {
        // Filled when on, outline when off — a clear at-a-glance state cue.
        var paths = {
            light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.6.6 1 1.4 1 2.5h6c0-1.1.4-1.9 1-2.5A6 6 0 0 0 12 3z"/>',
            fan: '<path d="M12 12a3 3 0 0 0 0-6c-2 0-3 1.5-3 3.5C9 11 10.5 12 12 12zm0 0a3 3 0 0 1 6 0c0 2-1.5 3-3.5 3C13 15 12 13.5 12 12zm0 0a3 3 0 0 0 0 6c2 0 3-1.5 3-3.5C15 13 13.5 12 12 12z"/>',
            "switch": '<rect x="4" y="7" width="16" height="10" rx="5"/><circle cx="9" cy="12" r="2.6" fill="currentColor" stroke="none"/>'
        };
        var d = paths[domain] || paths.switch;
        return '<svg class="sh-tile-icon" viewBox="0 0 24 24" width="26" height="26" fill="' + (on ? 'currentColor' : 'none') +
            '" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
    }
    function shStatePill(device) {
        if (device.available === false) return '<span class="sh-pill sh-pill-off">Offline</span>';
        var on = !!device.on;
        var pct = (on && device.dimmable && device.brightness != null)
            ? Math.round((Number(device.brightness) / 255) * 100) + '%' : (on ? 'On' : 'Off');
        return '<span class="sh-pill' + (on ? ' sh-pill-on' : ' sh-pill-off') + '">' + escapeHtml(pct) + '</span>';
    }
    function shDeviceTile(device) {
        if (typeof device !== 'object' || !device) return '';
        var name = device.name || device.entity_id || device.title || 'Device';
        var on = !!device.on;
        var domain = (device.domain === 'light' || device.domain === 'fan') ? device.domain : 'switch';
        var query = device.query || ('turn ' + (on ? 'off' : 'on') + ' the ' + name + ' ' + (domain === 'light' ? 'light' : 'switch'));
        var disabled = device.available === false;
        return '<button type="button" class="sh-tile' + (on ? ' is-on' : '') + (disabled ? ' is-off-network' : '') + '"' +
            (disabled ? ' disabled aria-disabled="true"' : ' data-sky-action="query" data-query="' + escapeHtml(query) + '"') +
            ' aria-pressed="' + (on ? 'true' : 'false') + '" aria-label="' + escapeHtml(name + ', ' + (on ? 'on' : 'off') + ', tap to toggle') + '">' +
            '<span class="sh-tile-top">' + shDeviceIcon(domain, on) + shStatePill(device) + '</span>' +
            '<span class="sh-tile-name">' + escapeHtml(name) + '</span>' +
            '</button>';
    }
    function shSceneChip(scene) {
        if (typeof scene !== 'object' || !scene) return '';
        var name = scene.name || scene.title || 'Scene';
        var query = scene.query || ('activate the ' + name + ' scene');
        return '<button type="button" class="sh-scene" data-sky-action="query" data-query="' + escapeHtml(query) + '" aria-label="' + escapeHtml('Activate ' + name) + '">' +
            '<span class="sh-scene-dot" aria-hidden="true"></span><span class="sh-scene-name">' + escapeHtml(name) + '</span></button>';
    }
    function renderSmartHome(props) {
        var devices = props.devices || props.entities || props.items || [];
        var scenes = props.scenes || [];
        var head = '<div class="sh-head"><span class="sh-title">' + escapeHtml(props.title || 'Home') + '</span>' +
            (props.summary ? '<span class="sh-sub">' + escapeHtml(props.summary) + '</span>' : '') + '</div>';
        var body;
        if (props.offline) {
            body = '<div class="sh-empty"><span class="sh-empty-title">Home hub offline</span>' +
                '<span class="sh-empty-sub">I couldn’t reach the home hub. Check it’s powered on and connected.</span></div>';
        } else {
            var tiles = devices.map(shDeviceTile).join('');
            var deviceBlock = tiles
                ? '<div class="sh-grid">' + tiles + '</div>'
                : '<div class="sh-empty"><span class="sh-empty-sub">No lights or switches set up yet.</span></div>';
            var sceneBlock = scenes.length
                ? '<div class="sh-scenes"><span class="sh-section">Scenes</span><div class="sh-scene-row">' +
                    scenes.map(shSceneChip).join('') + '</div></div>'
                : '';
            body = deviceBlock + sceneBlock;
        }
        return cardFrame(Object.assign({ status: props.status || 'Home' }, props), head + body,
            { wide: true, tone: 'smart-home-card', hideHeader: true, hideStatus: true, hideActions: true });
    }

    // media cards (contract: content {title, items}). No live producer emits
    // this card_type yet (panel_show_media uses the executor overlay); items
    // render as ListRows, upgraded to a MediaTile when a same-origin artwork
    // src exists (foreign URLs stay text-only — same policy as zoe-compose).
    function renderMusicSetup(props) {
        // Two modes: 'catalogue' (chips of services to add) or 'qr' (scan-to-connect).
        var body;
        if (props.mode === 'qr' && props.qr_path) {
            var src = String(props.qr_path);
            var safe = src.charAt(0) === '/' && src.charAt(1) !== '/';
            body = '<div class="ms-qr">' +
                (safe ? '<img class="ms-qr-img" src="' + escapeHtml(src) + '" alt="Setup QR code">' : '') +
                '<div class="ms-qr-steps"><span class="ms-step">1 · Point your phone camera at the code</span>' +
                '<span class="ms-step">2 · Open the Zoe link that appears</span>' +
                '<span class="ms-step">3 · Sign in — Zoe does the rest</span></div></div>';
        } else {
            var chips = (props.actions || []).map(function (a) {
                return '<button type="button" class="ms-chip" data-sky-action="query" data-query="' +
                    escapeHtml(a.query || '') + '">' + escapeHtml(a.label || '') + '</button>';
            }).join('');
            body = '<div class="ms-catalogue">' + (chips || '<span class="np-artist">No services to add.</span>') + '</div>';
        }
        var head = '<div class="ms-head"><span class="np-title">' + escapeHtml(props.title || 'Add music') + '</span>' +
            (props.subtitle ? '<span class="ms-sub">' + escapeHtml(props.subtitle) + '</span>' : '') + '</div>';
        return cardFrame(Object.assign({ status: 'Music' }, props), head + body,
            { wide: true, tone: 'now-playing-card music-setup-card', hideHeader: true, hideStatus: true, hideActions: true });
    }

    // Inline media-control glyphs (fill inherits currentColor).
    function npIcon(name) {
        var paths = {
            prev: '<path d="M18 6v12M8 12l10-6v12z"/>',
            next: '<path d="M6 6v12M16 12L6 6v12z"/>',
            play: '<path d="M8 5v14l11-7z"/>',
            pause: '<path d="M8 5h3v14H8zM13 5h3v14h-3z"/>',
            voldown: '<path d="M4 9v6h4l5 4V5L8 9zM17 12h4"/>',
            volup: '<path d="M4 9v6h4l5 4V5L8 9zM17 9h4M19 7v4"/>',
            // Filled music note for the empty-art placeholder (matches dashGlyph music).
            note: '<path d="M9 17V5l10-2v12"/><circle cx="6" cy="17" r="3"/><circle cx="16" cy="15" r="3"/>'
        };
        return '<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.play) + '</svg>';
    }
    // Outline glyphs for the output/hub controls (stroke-only; the airplay
    // triangle is the one filled shape).
    function npGlyph(name) {
        var paths = {
            airplay: '<path d="M5 16H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-1"/><path d="M8 20l4-5 4 5z" fill="currentColor" stroke="none"/>',
            chevron: '<path d="M6 9l6 6 6-6"/>',
            plus: '<path d="M12 5v14M5 12h14"/>'
        };
        return '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || '') + '</svg>';
    }
    function npBtn(icon, query, cls) {
        return '<button type="button" class="np-btn' + (cls ? ' ' + cls : '') + '" data-sky-action="query" data-query="' + escapeHtml(query) + '" aria-label="' + escapeHtml(query) + '">' + npIcon(icon) + '</button>';
    }

    // Stable on-brand ambient wash: hash the track/album to pick a ds1 accent
    // pair so each song gets a consistent tint (and a placeholder that isn't
    // always the same violet). Colors are literal ds1 hexes — safe to inline.
    function npTint(seed) {
        var pairs = [
            ['#9b8cff', '#6aa6ff'], // violet → blue (ds1 default)
            ['#37c0e6', '#5be3b0'], // cyan → mint
            ['#6aa6ff', '#37c0e6'], // blue → cyan
            ['#f5b13c', '#ff6b6b'], // amber → coral
            ['#9b8cff', '#ff6b6b'], // violet → coral
            ['#5be3b0', '#6aa6ff']  // mint → blue
        ];
        var s = String(seed || 'zoe'), h = 0;
        for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) >>> 0; }
        return pairs[h % pairs.length];
    }

    function npTime(secs) {
        secs = Math.max(0, Math.floor(secs));
        var m = Math.floor(secs / 60), s = secs % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function renderNowPlaying(props) {
        // Music Assistant now-playing card — album-art-forward ambient look.
        // The SVG transport row is built here from state; each button carries
        // data-sky-action=query so tap + voice share one resolver path.
        var img = String(props.image || '');
        var sameOrigin = img && ((img.charAt(0) === '/' && img.charAt(1) !== '/') || /^https?:\/\//i.test(img));
        // Strict URL shape (no quotes/parens/backslash/whitespace/angle brackets,
        // which are invalid in URL paths per RFC 3986) so it is safe to drop into a
        // CSS url() inside a style attribute as well as an <img>.
        var safeArt = (sameOrigin && /^(?:\/[^"'()\\\s<>]+|https?:\/\/[^"'()\\\s<>]+)$/i.test(img)) ? img : '';
        var playing = props.state === 'playing';
        var hasTrack = playing || props.state === 'paused';
        var stateBadge = playing ? 'Playing' : (props.state === 'paused' ? 'Paused' : 'Ready');
        var tint = npTint(props.album || props.artist || props.title || 'zoe');

        // Ambient wash filling the whole card (behind the scene). Two accent
        // radials over a deep neutral base; hex+alpha is valid, tokens are hex.
        var ambient = 'background:'
            + 'radial-gradient(120% 120% at 10% -6%,' + tint[0] + 'E6 0%,' + tint[0] + '00 52%),'
            + 'radial-gradient(120% 115% at 104% 28%,' + tint[1] + 'BF 0%,' + tint[1] + '00 55%),'
            + 'radial-gradient(140% 140% at 80% 118%,' + tint[0] + '80 0%,' + tint[0] + '00 55%),'
            + 'linear-gradient(155deg,#0f1220F2 0%,#0a0d16F2 100%)';

        var artInner = safeArt
            ? '<img class="np-art" src="' + escapeHtml(safeArt) + '" alt="" loading="lazy">'
            : '<div class="np-art np-art-empty" style="--np-c1:' + tint[0] + ';--np-c2:' + tint[1] + '">' + npIcon('note') + '</div>';
        // Blurred art backdrop (same-origin guard already applied to safeArt).
        var artBg = safeArt
            ? '<div class="np-art-bg" style="background-image:url(&quot;' + escapeHtml(safeArt) + '&quot;)"></div>'
            : '';

        var eq = playing ? '<span class="np-eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' : '';
        var kicker = '<span class="np-kicker">' + eq + '<span>' + escapeHtml(stateBadge)
            + (props.player_name ? ' · ' + escapeHtml(props.player_name) : '') + '</span></span>';

        // Optional progress — only if the producer sends elapsed + duration.
        var progress = '';
        var elapsed = Number(props.elapsed != null ? props.elapsed : props.position);
        var duration = Number(props.duration != null ? props.duration : props.length);
        if (hasTrack && isFinite(elapsed) && isFinite(duration) && duration > 0 && elapsed >= 0) {
            var frac = Math.max(0, Math.min(1, elapsed / duration));
            progress = '<div class="np-progress"><div class="np-bar"><span style="width:'
                + (frac * 100).toFixed(1) + '%"></span></div>'
                + '<div class="np-times"><span>' + npTime(elapsed) + '</span><span>' + npTime(duration) + '</span></div></div>';
        }

        var transport = hasTrack ? [
            '<div class="np-transport">',
            npBtn('voldown', 'turn the music down'),
            npBtn('prev', 'previous song'),
            playing ? npBtn('pause', 'pause music', 'np-primary') : npBtn('play', 'resume music', 'np-primary'),
            npBtn('next', 'next song'),
            npBtn('volup', 'turn the music up'),
            '</div>'
        ].join('') : '';
        // Browse/suggestion chips (idle card) come through props.actions.
        var chips = (!hasTrack && Array.isArray(props.actions) && props.actions.length)
            ? '<div class="sky-actions np-chips">' + props.actions.map(buttonHtml).join('') + '</div>' : '';

        // Music-hub footer: speaker/output picker + a persistent "Add source"
        // affordance. The output button is client-side (data-music-output → the
        // panel fetches /api/music/players, persists the pick, transfers); the
        // add-source button reuses the existing "add music" resolver flow.
        var outName = props.player_name || 'This speaker';
        var outputRow = [
            '<div class="np-output">',
            '<button type="button" class="np-out-btn" data-music-output data-music-player="' + escapeHtml(props.player_id || '') + '" aria-haspopup="listbox" aria-expanded="false">',
            npGlyph('airplay'),
            '<span class="np-out-name">' + escapeHtml(outName) + '</span>',
            npGlyph('chevron'),
            '</button>',
            '<button type="button" class="np-add-src" data-sky-action="query" data-query="add music" aria-label="Add a music source">',
            npGlyph('plus'), '<span>Add source</span>',
            '</button>',
            '</div>',
            '<div class="np-outputs" data-music-picker hidden role="listbox" aria-label="Choose a speaker"></div>'
        ].join('');

        var body = [
            '<div class="np-ambient" style="' + ambient + '"></div>',
            artBg,
            '<div class="np-scrim"></div>',
            '<div class="np-scene">',
            '<div class="np-art-wrap">' + artInner + '</div>',
            '<div class="np-meta">',
            kicker,
            '<span class="np-title">' + escapeHtml(props.title || 'Nothing playing') + '</span>',
            props.artist ? '<span class="np-artist">' + escapeHtml(props.artist) + '</span>' : '',
            props.album ? '<span class="np-album">' + escapeHtml(props.album) + '</span>' : '',
            progress,
            transport,
            chips,
            outputRow,
            '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Music' }, props), body,
            { wide: true, tone: 'now-playing-card', hideHeader: true, hideStatus: true, hideActions: true });
    }

    function renderMedia(props) {
        const items = props.items || [];
        const children = items.slice(0, 6).map(item => {
            if (typeof item !== 'object' || !item) return listRowNode(String(item || 'Media'), '');
            const title = item.title || item.name || item.track || 'Media';
            const subtitle = item.subtitle || item.artist || item.album || '';
            const src = String(item.artwork || item.album_art || item.image || item.art || '');
            if (src.charAt(0) === '/' && src.charAt(1) !== '/') {
                return { component: 'MediaTile', src: src, title: title, subtitle: subtitle };
            }
            return listRowNode(title, subtitle);
        });
        const tree = { component: 'Stack', gap: 'sm', children: children };
        return cardFrame(Object.assign({ status: props.status || 'Media' }, props), composedBody(tree, props.title || ''), { wide: false, tone: 'media-card' });
    }

    // research_report cards (contract: content {title, sections}). No live
    // producer emits this card_type yet (panel_show_research_report uses the
    // executor overlay); sections render as kicker + body text + ListRows.
    function renderResearchReport(props) {
        const sections = props.sections || [];
        const children = [];
        sections.slice(0, 4).forEach(section => {
            if (typeof section !== 'object' || !section) {
                if (section) children.push(textNode(String(section), 'body'));
                return;
            }
            const heading = section.title || section.heading || '';
            if (heading) children.push(textNode(heading, 'kicker'));
            const bodyText = section.body || section.text || section.summary || '';
            if (bodyText) children.push(textNode(bodyText, 'body'));
            const items = section.items || section.results || section.sources || [];
            items.slice(0, 5).forEach(item => {
                if (typeof item !== 'object' || !item) {
                    children.push(listRowNode(String(item || 'Result'), ''));
                    return;
                }
                children.push(listRowNode(item.title || item.name || 'Result', item.value || item.summary || item.location || ''));
            });
        });
        const tree = { component: 'Stack', gap: 'md', children: children.length ? children : [textNode('No report sections available.', 'caption')] };
        return cardFrame(Object.assign({ status: props.status || 'Research' }, props), composedBody(tree, props.title || ''), { wide: true, tone: 'research-card' });
    }

    function renderActionForm(props) {
        if (props.source === 'list_create') {
            const fieldRows = (props.fields || []).slice(0, 2).map(field => {
                const value = field.value == null || field.value === '' ? 'Not set' : String(field.value);
                return listRowNode(field.label || field.name || 'Field', value);
            });
            const actions = props.actions || [];
            const tree = { component: 'Stack', gap: 'md', children: [
                textNode('New list', 'kicker'),
                textNode('Name this list', 'title'),
                textNode(props.summary || 'What should I name it?', 'body'),
                { component: 'Grid', columns: 2, children: fieldRows }
            ] };
            return cardFrame(Object.assign({}, props, { actions }), composedBody(tree, props.summary || 'What should I name it?'), { wide: true, tone: 'zoe-list-card list-create-card personal', hideHeader: true, hideStatus: true });
        }
        const fieldRows = (props.fields || []).slice(0, 6).map(field => {
            const value = field.value == null || field.value === '' ? 'Not set' : String(field.value);
            return listRowNode(field.label || field.name || 'Field', value);
        });
        const actions = props.actions || [
            { label: 'Review', query: 'review ' + (props.title || 'form') },
            { label: 'Confirm', query: 'confirm ' + (props.form_id || props.title || 'form') }
        ];
        const tree = { component: 'Stack', gap: 'md', children: [
            textNode(props.summary || 'Review the fields before Zoe takes action.', 'body'),
            { component: 'Grid', columns: 2, children: fieldRows }
        ] };
        return cardFrame(Object.assign({ status: 'Form' }, props, { actions }), composedBody(tree, props.summary || ''), { wide: true, tone: 'form-card' });
    }

    function renderUnsupportedContract(props) {
        return cardFrame({
            title: 'Card needs an update',
            kicker: 'Unsupported schema',
            body: 'This Skybridge renderer supports card schema 1.x.',
            status: props.schema_version || 'Unknown'
        }, '<div class="sky-card-body">This card was not rendered because its schema version is newer than the current Skybridge renderer.</div>', { wide: true, tone: 'warn' });
    }

    function renderCompose(props) {
        // Brain-composed card: a server-validated component tree rendered by the
        // shared catalog renderer (window.ZoeCompose from zoe-compose.js), wrapped
        // in the standard card shell. Composition never bypasses the catalog — if
        // the renderer isn't loaded or the tree is missing, degrade to status.
        var tree = props && props.tree;
        if (!tree || !window.ZoeCompose || typeof window.ZoeCompose.render !== 'function') {
            // Minimal escaped-text fallback (deliberately NOT the composed
            // renderStatus: this branch also covers "zoe-compose failed to
            // load", and its output must never depend on the catalog).
            var fallbackTitle = (props && props.title) || 'Zoe';
            var fallbackBody = (props && props.summary) || 'This card could not be displayed.';
            return cardFrame({ title: fallbackTitle }, '<div class="zx-card-body"><p>' + escapeHtml(fallbackBody) + '</p></div>', { tone: 'compose-card' });
        }
        var body = '<div class="zx-card-body">' + window.ZoeCompose.render(tree) + '</div>';
        return cardFrame(Object.assign({ status: 'Composed' }, props), body, {
            wide: !!(props && props.wide),
            tone: 'compose-card',
            hideHeader: !(props && props.title),
            hideStatus: true
        });
    }

    function normalizeCard(card) {
        if (!card) return { component: 'status', props: { title: 'Empty card', body: '' } };
        if (card.component) return { component: card.component, props: card.props || {} };
        if (card.card_type && card.content) {
            if (!rendererAccepts(card.schema_version)) {
                return { component: 'unsupported_contract', props: { schema_version: card.schema_version } };
            }
            return { component: card.card_type, props: Object.assign({ id: card.card_id }, card.content) };
        }
        if (card.type === 'confirmation' || card.type === 'confirm') {
            return { component: 'status', props: { title: card.title || 'Confirm', body: card.message || card.body || '', status: 'Confirm' } };
        }
        return { component: card.type || 'status', props: card };
    }

    // Small filled glyphs for the dashboard shortcut tiles (the shared sprite
    // ships weather only). Filled/dimensional, never thin line icons (§5).
    function dashGlyph(name) {
        const paths = {
            home: '<path d="M12 3 3 10v10h6v-6h6v6h6V10z"/>',
            music: '<path d="M9 17V5l10-2v12"/><circle cx="6" cy="17" r="3"/><circle cx="16" cy="15" r="3"/>',
            user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6z"/>',
            calendar: '<path d="M8 2h2.6v3.6H8zM13.4 2H16v3.6h-2.6z"/><path d="M4 4.6h16a1.4 1.4 0 0 1 1.4 1.4v3H2.6V6A1.4 1.4 0 0 1 4 4.6z"/><path d="M2.6 10.4h18.8V19a2 2 0 0 1-2 2H4.6a2 2 0 0 1-2-2z" opacity=".82"/>',
            list: '<rect x="3" y="4" width="4.4" height="4.4" rx="1.2"/><rect x="3" y="9.8" width="4.4" height="4.4" rx="1.2"/><rect x="3" y="15.6" width="4.4" height="4.4" rx="1.2"/><rect x="9.6" y="5.1" width="11.4" height="2.2" rx="1.1"/><rect x="9.6" y="10.9" width="11.4" height="2.2" rx="1.1"/><rect x="9.6" y="16.7" width="11.4" height="2.2" rx="1.1"/>',
            bulb: '<path d="M12 2a7 7 0 0 0-4.1 12.66c.66.5 1.1 1.4 1.1 2.34h6c0-.94.44-1.84 1.1-2.34A7 7 0 0 0 12 2z"/><rect x="9" y="18.2" width="6" height="2" rx="1"/><rect x="10" y="21.2" width="4" height="1.6" rx=".8"/>',
            timer: '<rect x="9.8" y="1.2" width="4.4" height="2.4" rx="1.2"/><path d="M12 5a8 8 0 1 0 0 16 8 8 0 0 0 0-16zm1 8.42 3 2.16-1 1.4-3.6-2.6V8.6h1.6z" fill-rule="evenodd"/>'
        };
        return '<svg class="dash-ctrl-svg" viewBox="0 0 24 24" aria-hidden="true">' + (paths[name] || paths.home) + '</svg>';
    }

    // The ambient wake dashboard (extends PR #851's Layout B, View Assist cue):
    // LEFT third = big time + date (reuses the ambient clock typography classes
    // and the .sky-live-clock/data-clock-* hooks so the numerals tick live);
    // RIGHT two-thirds = a 2x3 grid of shortcut tiles. Every tile rides the
    // existing data-sky-action="query" delegation, so each one works today.
    function renderDashboard(props) {
        const now = new Date();
        const h24 = now.getHours();
        const hour12 = ((h24 + 11) % 12) + 1;
        const greeting = props.greeting || (h24 < 12 ? 'Good morning' : (h24 < 18 ? 'Good afternoon' : 'Good evening'));
        const name = props.user_name ? ', ' + props.user_name : '';
        const dateText = now.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
        const wx = props.weather && (props.weather.current || props.weather);
        const wxDesc = wx ? (wx.description || wx.condition || '') : '';
        const wxTemp = wx ? formatTemp(weatherValue(wx, ['temp', 'temperature', 'temperature_c', 'temp_c'])) : '';
        const wxSub = wx ? (wxTemp + (wxDesc ? ' · ' + wxDesc : '')) : 'Forecast';
        const tiles = [
            { key: 'weather', label: 'Weather', sub: wxSub, query: 'what is the weather',
              glyph: glyphSvg(weatherGlyphId(wxDesc, panelIsDark()), 40) },
            { key: 'calendar', label: 'Calendar', sub: 'Today', query: 'show my calendar', glyph: dashGlyph('calendar') },
            { key: 'lists', label: 'Lists', sub: 'Shopping', query: 'show my shopping list', glyph: dashGlyph('list') },
            { key: 'music', label: 'Music', sub: 'Play something', query: 'play some music', glyph: dashGlyph('music') },
            { key: 'lights', label: 'Lights', sub: 'This room', query: 'turn on the lights', glyph: dashGlyph('bulb') },
            { key: 'timers', label: 'Timers', sub: 'Running now', query: 'show my timers', glyph: dashGlyph('timer') }
        ].map(tile => [
            '<button type="button" class="dash-ctrl dash-ctrl--' + tile.key + '" data-sky-action="query" data-query="' + escapeHtml(tile.query) + '" aria-label="' + escapeHtml(tile.label) + '">',
            '<span class="dash-ctrl-glyph">' + tile.glyph + '</span>',
            '<span class="dash-ctrl-text"><span class="dash-ctrl-label">' + escapeHtml(tile.label) + '</span>',
            tile.sub ? '<span class="dash-ctrl-sub">' + escapeHtml(tile.sub) + '</span>' : '',
            '</span>',
            '</button>'
        ].join('')).join('');
        // Auth chip: a signed-in panel shows WHO is signed in (tap = switch user
        // via the same auth picker); only true guests see "Sign in".
        const authChip = props.guest !== false
            ? '<button type="button" class="dash-ctrl dash-ctrl-signin" data-sky-action="auth"><span class="dash-ctrl-glyph">' + dashGlyph('user') + '</span><span class="dash-ctrl-label">Sign in</span></button>'
            : '<button type="button" class="dash-ctrl dash-ctrl-signin dash-ctrl-profile" data-sky-action="auth" aria-label="Switch user">' +
              '<span class="dash-ctrl-glyph">' + dashGlyph('user') + '</span>' +
              '<span class="dash-ctrl-text"><span class="dash-ctrl-label">' + escapeHtml(props.user_name || 'Signed in') + '</span>' +
              '<span class="dash-ctrl-sub">Tap to switch</span></span></button>';
        // Sunrise/sunset line (premium detail, same data the clock card surfaces).
        let sunLine = '';
        if (props.sun && props.sun.rise && props.sun.set) {
            const riseT = fmtClockTime(new Date(props.sun.rise));
            const setT = fmtClockTime(new Date(props.sun.set));
            if (riseT && setT) {
                sunLine = '<span class="dash-sun">' + horizonGlyph(true, 'rgba(245,177,60,.92)') + ' ' + escapeHtml(riseT) +
                    '&nbsp;&nbsp;' + horizonGlyph(false, 'rgba(155,140,255,.92)') + ' ' + escapeHtml(setT) + '</span>';
            }
        }
        const body = [
            '<div class="dash-scene">',
            '<div class="dash-clock sky-live-clock">',
            '<span class="dash-greeting">' + escapeHtml(greeting + name) + '</span>',
            '<div class="dash-time sky-ambient-time tnum"><span data-clock-hour>' + hour12 + '</span><i>:</i><span data-clock-minute>' + ('0' + now.getMinutes()).slice(-2) + '</span><b data-clock-meridiem>' + (h24 < 12 ? 'AM' : 'PM') + '</b></div>',
            '<span class="dash-date sky-ambient-date" data-clock-date>' + escapeHtml(dateText) + '</span>',
            sunLine,
            authChip,
            '</div>',
            '<div class="dash-tiles">' + tiles + '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Home' }, props), body, { wide: true, tone: 'dashboard-card', hideHeader: true, hideStatus: true, hideActions: true });
    }

    const renderers = {
        dashboard: renderDashboard,
        status: renderStatus,
        info: renderStatus,
        generic: renderStatus,
        page: renderPage,
        setting: renderSetting,
        page_grid: renderPageGrid,
        settings_overview: renderSettingsOverview,
        list: renderList,
        action_form: renderActionForm,
        form: renderActionForm,
        media: renderMedia,
        smart_home: renderSmartHome,
        research_report: renderResearchReport,
        auth_challenge: renderAuthChallenge,
        timer: renderTimer,
        now_playing: renderNowPlaying,
        music_setup: renderMusicSetup,
        compose: renderCompose,
        stream_text: renderStatus,
        unsupported_contract: renderUnsupportedContract
    };

    function render(card) {
        const normalized = normalizeCard(card);
        const renderer = renderers[normalized.component] || renderStatus;
        return renderer(normalized.props || {});
    }

    window.SkybridgeRenderer = {
        render,
        normalizeCard,
        escapeHtml
    };
})();
