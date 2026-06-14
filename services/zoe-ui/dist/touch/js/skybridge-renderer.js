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
        const actions = Array.isArray(props.actions) && props.actions.length
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
        const title = props.title || 'Confirm it is you';
        const body = props.body || props.message || 'Zoe needs to know who is speaking before showing or changing personal data.';
        const domain = props.domain ? String(props.domain) : 'Private data';
        const finalStep = props.final_step || 'Return to Zoe';
        const bodyHtml = [
            '<div class="sky-auth-scene">',
            '<div class="sky-auth-hero">',
            '<div class="sky-auth-orb" aria-hidden="true"><span></span></div>',
            '<div class="sky-auth-copy">',
            '<span>' + escapeHtml(domain) + '</span>',
            '<strong>' + escapeHtml(title) + '</strong>',
            '<p>' + escapeHtml(body) + '</p>',
            '</div>',
            '</div>',
            '<div class="sky-auth-profile-grid" data-auth-profiles aria-label="Choose profile">',
            '<div class="sky-auth-loading"><i></i><span>Finding people for this panel...</span></div>',
            '</div>',
            '<div class="sky-auth-footer"><span>Say a name or tap a profile.</span><span>PIN opens next.</span><span>' + escapeHtml(finalStep) + '</span></div>',
            '</div>'
        ].join('');
        return cardFrame(props, bodyHtml, { wide: true, tone: 'auth-challenge', hideHeader: true, hideStatus: true });
    }

    function renderStatus(props) {
        if (props.source === 'calendar_show') return renderCalendar(props);
        if (props.source === 'weather_current' || props.source === 'weather_forecast') return renderWeather(props);
        if (props.source === 'list_show') return renderZoeList(props);
        if (props.source === 'people_directory') return renderPeopleDirectory(props);
        if (props.source === 'person_profile') return renderPersonProfile(props);
        const body = [
            props.metric ? '<div class="sky-widget-metric"><strong>' + escapeHtml(props.metric) + '</strong><span>' + escapeHtml(props.metric_label || '') + '</span></div>' : '',
            '<div class="sky-card-body">' + escapeHtml(props.body || props.message || '') + '</div>'
        ].join('');
        return cardFrame(props, body, { wide: !!props.wide, tone: props.tone || '' });
    }

    function formatEventTime(item) {
        const start = item.start_time || '';
        const end = item.end_time || '';
        if (item.all_day) return 'All day';
        if (start && end) return start + ' - ' + end;
        return start || item.start_date || 'Scheduled';
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

    function calendarCategoryClass(value) {
        const token = safeClassTokens(String(value || 'general').toLowerCase()) || 'general';
        const known = ['work', 'personal', 'bucket', 'shopping', 'health', 'routine', 'social', 'family', 'general'];
        return known.indexOf(token) >= 0 ? token : 'general';
    }

    function renderCalendar(props) {
        const events = Array.isArray(props.events) ? props.events : [];
        const visibleEvents = events.slice().sort((a, b) => calendarEventSortKey(a) - calendarEventSortKey(b)).slice(0, 8);
        const dateMeta = formatCalendarDate(props.date || props.start_date || (visibleEvents[0] && visibleEvents[0].start_date));
        const rows = visibleEvents.map((item, index) => {
            const title = item.title || item.name || 'Calendar event';
            const category = calendarCategoryClass(item.category);
            const detail = [item.location].filter(Boolean).join(' · ');
            return [
                '<div class="sky-event-row sky-calendar-event ' + escapeHtml(category) + '">',
                '<div class="sky-calendar-time"><span>' + escapeHtml(formatEventTime(item)) + '</span>' + (index === 0 ? '<b>Next</b>' : '') + '</div>',
                '<div class="sky-calendar-event-main"><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</div>',
                '</div>'
            ].join('');
        }).join('');
        const empty = [
            '<div class="sky-empty-data sky-calendar-empty">',
            '<div class="sky-calendar-empty-mark" aria-hidden="true"></div>',
            '<strong>No events ' + escapeHtml(props.qualifier || 'today') + '</strong>',
            '<span>Your calendar is clear for this range.</span>',
            '</div>'
        ].join('');
        const body = [
            '<div class="sky-calendar-scene">',
            '<div class="sky-calendar-summary">',
            '<div class="sky-calendar-date"><span>' + escapeHtml(dateMeta.weekday) + '</span><strong>' + escapeHtml(dateMeta.monthDay) + '</strong></div>',
            '<div class="sky-widget-metric"><strong>' + escapeHtml(events.length) + '</strong><span>' + escapeHtml(events.length === 1 ? 'event' : 'events') + ' ' + escapeHtml(props.qualifier || '') + '</span></div>',
            '</div>',
            '<div class="sky-calendar-agenda"><div class="sky-calendar-rail" aria-hidden="true"></div><div class="sky-data-list">' + (rows || empty) + '</div></div>',
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

    function renderWeather(props) {
        const current = props.current || {};
        const forecast = props.forecast || {};
        const daily = Array.isArray(forecast.daily) ? forecast.daily : [];
        const hourly = Array.isArray(forecast.hourly) ? forecast.hourly : [];
        const location = props.location || {};
        const place = [location.city || current.city || props.city, location.country || current.country || props.country].filter(Boolean).join(', ') || 'Current location';
        const description = current.description || current.condition || props.description || 'Current conditions';
        const dailyRows = daily.slice(0, 5).map((item, index) => {
            const label = formatForecastShort(item.day || item.time || '');
            const high = item.high != null ? formatTemp(item.high) : formatTemp(item.temp);
            const low = item.low != null ? formatTemp(item.low) : '';
            const band = forecastTempBand(item);
            const condition = item.description || item.condition || '';
            return [
                '<div class="sky-weather-day-row' + (index === 0 ? ' is-primary' : '') + '">',
                '<div class="sky-weather-day-label"><span>' + escapeHtml(index === 0 ? 'Today' : label) + '</span><b aria-hidden="true">' + escapeHtml(weatherEmoji(item)) + '</b></div>',
                '<div class="sky-weather-day-main"><strong>' + escapeHtml(condition || description) + '</strong><div class="sky-weather-temp-band" aria-hidden="true"><i style="left: ' + band.left + '%; width: ' + band.width + '%;"></i></div></div>',
                '<div class="sky-weather-day-temp"><strong>' + escapeHtml(high) + '</strong>' + (low ? '<small>' + escapeHtml(low) + '</small>' : '') + '</div>',
                '</div>'
            ].join('');
        }).join('');
        const hourlyTiles = hourly.slice(0, 8).map((item, index) => {
            const label = index === 0 ? 'Now' : formatHourLabel(item.time || item.day || '');
            return [
                '<div class="sky-weather-hour-tile">',
                '<span>' + escapeHtml(label) + '</span>',
                '<b aria-hidden="true">' + escapeHtml(weatherEmoji(item)) + '</b>',
                '<strong>' + escapeHtml(formatTemp(item.temp != null ? item.temp : item.high)) + '</strong>',
                '</div>'
            ].join('');
        }).join('');
        const dayList = dailyRows;
        const meta = [
            ['🌡', 'Feels ' + formatTemp(current.feels_like)],
            ['💧', current.humidity == null ? 'Humidity --' : current.humidity + '% humidity'],
            ['💨', formatWind(current.wind_speed)]
        ].map(pair => '<div class="sky-weather-pill"><span>' + escapeHtml(pair[0]) + '</span>' + escapeHtml(pair[1]) + '</div>').join('');
        const body = [
            '<div class="sky-weather-scene">',
            '<div class="sky-weather-main">',
            '<div class="sky-weather-location">📍 ' + escapeHtml(place) + '</div>',
            '<div class="sky-weather-hero"><div class="sky-weather-temp">' + escapeHtml(formatTemp(current.temp)) + '</div><div class="sky-weather-icon" aria-hidden="true">' + escapeHtml(weatherEmoji(current)) + '</div></div>',
            '<div class="sky-weather-condition">' + escapeHtml(description) + '</div>',
            '<div class="sky-weather-meta">' + meta + '</div>',
            '</div>',
            '<div class="sky-weather-forecast">',
            '<div class="sky-weather-forecast-head"><h4>' + escapeHtml(props.source === 'weather_forecast' ? 'Forecast' : 'Next up') + '</h4><span>' + escapeHtml(daily.length ? daily.length + ' days' : hourly.length + ' hours') + '</span></div>',
            hourlyTiles ? '<div class="sky-weather-hour-strip">' + hourlyTiles + '</div>' : '',
            dayList ? '<div class="sky-weather-day-list">' + dayList + '</div>' : (!hourlyTiles ? '<div class="sky-weather-day-list"><div class="sky-empty-data">No forecast data available yet.</div></div>' : ''),
            '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Weather', icon: 'W' }, props), body, { wide: true, tone: 'weather-card ' + weatherClass(props, current) });
    }

    function normalizeListItems(items) {
        return Array.isArray(items) ? items : [];
    }

    function listAccentClass(value) {
        const token = safeClassTokens(String(value || 'list').toLowerCase()) || 'list';
        const known = ['shopping', 'work', 'personal', 'bucket', 'tasks', 'all'];
        return known.indexOf(token) >= 0 ? token : 'list';
    }

    function renderListItemRow(item, index) {
        const isObject = typeof item === 'object' && item;
        const title = isObject ? (item.text || item.title || item.label || 'List item') : String(item || 'List item');
        const completed = !!(isObject && item.completed);
        const detail = isObject ? [item.quantity, item.category, item.assigned_to].filter(Boolean).join(' · ') : '';
        const priority = isObject && item.priority ? '<b>' + escapeHtml(item.priority) + '</b>' : '';
        return [
            '<div class="sky-list-item-row' + (completed ? ' is-done' : '') + '">',
            '<div class="sky-list-check" aria-hidden="true">' + (completed ? '✓' : '') + '</div>',
            '<div class="sky-list-item-main"><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</div>',
            '<div class="sky-list-item-meta">' + (priority || '<span>' + escapeHtml(String(index + 1).padStart(2, '0')) + '</span>') + '</div>',
            '</div>'
        ].join('');
    }

    function renderZoeList(props) {
        const items = normalizeListItems(props.items);
        const lists = Array.isArray(props.lists) ? props.lists : [];
        const listType = props.list_type || (lists[0] && lists[0].list_type) || 'all';
        const accent = listAccentClass(listType);
        const visibleItems = items.slice(0, 12);
        const rows = visibleItems.map(renderListItemRow).join('');
        const overviewRows = !items.length && lists.length ? lists.slice(0, 6).map(list => {
            const openCount = list.open_count == null ? (list.item_count || 0) : list.open_count;
            return [
                '<div class="sky-list-overview-row ' + escapeHtml(listAccentClass(list.list_type)) + '">',
                '<div><strong>' + escapeHtml(list.name || list.list_name || 'List') + '</strong><em>' + escapeHtml(list.description || list.list_type || '') + '</em></div>',
                '<span>' + escapeHtml(openCount) + ' open</span>',
                '</div>'
            ].join('');
        }).join('') : '';
        const empty = [
            '<div class="sky-empty-data sky-list-empty">',
            '<strong>No items in ' + escapeHtml(props.list_name || 'this list') + '</strong>',
            '<span>Zoe did not find active items for this request.</span>',
            '</div>'
        ].join('');
        const body = [
            '<div class="sky-list-scene ' + escapeHtml(accent) + '">',
            '<div class="sky-list-hero">',
            '<div><span>' + escapeHtml((props.list_type || 'list').toUpperCase()) + '</span><h3>' + escapeHtml(props.list_name || props.title || 'Lists') + '</h3></div>',
            '<div class="sky-list-rings"><strong>' + escapeHtml(props.open_count == null ? (items.length || lists.length) : props.open_count) + '</strong><span>open</span></div>',
            '</div>',
            '<div class="sky-list-stats">',
            '<div><strong>' + escapeHtml(props.item_count == null ? items.length : props.item_count) + '</strong><span>items</span></div>',
            '<div><strong>' + escapeHtml(props.completed_count || 0) + '</strong><span>done</span></div>',
            '<div><strong>' + escapeHtml(lists.length || 1) + '</strong><span>lists</span></div>',
            '</div>',
            '<div class="sky-list-items">' + (rows || overviewRows || empty) + '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Lists', icon: 'L' }, props), body, { wide: true, tone: 'zoe-list-card ' + accent, hideHeader: true, hideStatus: true });
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

    function renderPeopleDirectory(props) {
        const people = Array.isArray(props.people) ? props.people : [];
        const rows = people.slice(0, 8).map(person => {
            const health = healthPercent(person.health_score);
            return [
                '<div class="sky-person-row">',
                '<div class="sky-person-avatar" aria-hidden="true">' + initialsFor(person.name) + '</div>',
                '<div class="sky-person-main"><strong>' + escapeHtml(person.name || 'Person') + '</strong><em>' + escapeHtml(personSubline(person)) + '</em></div>',
                '<div class="sky-person-health"><i style="width:' + health + '%"></i><span>' + health + '</span></div>',
                '</div>'
            ].join('');
        }).join('');
        const empty = '<div class="sky-empty-data sky-people-empty"><strong>No matching people</strong><span>Zoe did not find contacts for this request.</span></div>';
        const body = [
            '<div class="sky-people-scene">',
            '<div class="sky-people-hero"><div><span>PEOPLE</span><h3>' + escapeHtml(props.title || 'People') + '</h3></div><div class="sky-people-count"><strong>' + escapeHtml(props.count == null ? people.length : props.count) + '</strong><span>found</span></div></div>',
            '<div class="sky-people-filter-row">',
            props.query ? '<span>Search ' + escapeHtml(props.query) + '</span>' : '',
            props.context ? '<span>' + escapeHtml(props.context) + '</span>' : '',
            props.circle ? '<span>' + escapeHtml(props.circle) + '</span>' : '',
            '</div>',
            '<div class="sky-people-list">' + (rows || empty) + '</div>',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'People', icon: 'P' }, props), body, { wide: true, tone: 'people-card', hideHeader: true, hideStatus: true });
    }

    function renderPersonProfile(props) {
        const person = props.person || {};
        const health = healthPercent(person.health_score);
        const contactRows = [
            ['Phone', person.phone],
            ['Email', person.email],
            ['Birthday', person.birthday],
            ['Last contact', person.last_contacted_at]
        ].filter(pair => pair[1]).map(pair => '<div><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        const body = [
            '<div class="sky-profile-scene">',
            '<div class="sky-profile-hero">',
            '<div class="sky-profile-avatar" aria-hidden="true">' + initialsFor(person.name || props.title) + '</div>',
            '<div class="sky-profile-title"><span>' + escapeHtml(personSubline(person)) + '</span><h3>' + escapeHtml(person.name || props.title || 'Person') + '</h3></div>',
            '<div class="sky-profile-health"><strong>' + health + '</strong><span>connection</span><i style="height:' + health + '%"></i></div>',
            '</div>',
            contactRows ? '<div class="sky-profile-grid">' + contactRows + '</div>' : '',
            person.notes ? '<p class="sky-profile-notes">' + escapeHtml(person.notes) + '</p>' : '',
            '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Person', icon: 'P' }, props), body, { wide: true, tone: 'person-profile-card', hideHeader: true, hideStatus: true });
    }


    function renderPage(props) {
        const fields = [
            ['Surface', props.title || 'Page'],
            ['Best for', props.summary || 'Zoe context'],
            ['Mode', 'Open, summarize, or keep as context']
        ].map(pair => '<div class="sky-field"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        var cardActions = [
            { label: 'Open page', route: props.route, type: 'open' },
            { label: 'Show related settings', query: props.title + ' settings' }
        ];
        return cardFrame(Object.assign({}, props, { actions: cardActions, status: props.status || 'Surface' }), '<div class="sky-card-body">' + escapeHtml(props.summary || '') + '</div><div class="sky-widget-strip">' + fields + '</div>', { wide: false, tone: 'page-card' });
    }

    function renderSetting(props) {
        const risk = props.risk || 'low';
        const changeLabel = risk === 'critical' ? 'Prepare change' : 'Change setting';
        const fields = [
            ['Risk', risk],
            ['Control area', props.domain || 'settings']
        ].map(pair => '<div class="sky-field"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        var settingActions = [
            { label: 'Open settings', route: props.route, type: 'open' },
            { label: changeLabel, query: 'change ' + props.title, kind: risk === 'critical' ? 'warn' : 'normal' }
        ];
        return cardFrame(Object.assign({ status: risk }, props, { actions: settingActions }), '<div class="sky-card-body">' + escapeHtml(props.summary || '') + '</div><div class="sky-widget-strip">' + fields + '</div>', { wide: false, tone: 'setting-card' });
    }

    function renderPageGrid(props) {
        const items = (props.items || []).slice(0, 4).map(item => {
            return '<div class="sky-field"><span>' + escapeHtml(item.title) + '</span><strong>' + escapeHtml(item.summary) + '</strong></div>';
        }).join('');
        return cardFrame(Object.assign({ status: 'Map' }, props), '<div class="sky-card-grid">' + items + '</div>', { wide: true, tone: 'map-card' });
    }

    function renderSettingsOverview(props) {
        const items = (props.items || []).slice(0, 4).map(item => {
            return '<div class="sky-field"><span>' + escapeHtml(item.title) + ' · ' + escapeHtml(item.risk) + '</span><strong>' + escapeHtml(item.summary) + '</strong></div>';
        }).join('');
        return cardFrame(Object.assign({ status: 'Settings' }, props), '<div class="sky-card-grid">' + items + '</div>', { wide: true, tone: 'map-card' });
    }

    function renderList(props) {
        const rows = (props.items || []).slice(0, 6).map((item, index) => {
            const title = typeof item === 'string' ? item : item.title || item.text || item.label || JSON.stringify(item);
            const detail = typeof item === 'object' && item ? (item.summary || item.description || item.value || '') : '';
            return '<div class="sky-field"><span>' + escapeHtml(String(index + 1).padStart(2, '0')) + '</span><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</div>';
        }).join('');
        return cardFrame(Object.assign({ status: props.status || 'List' }, props), '<div class="sky-card-grid">' + rows + '</div>', { wide: false, tone: 'list-card' });
    }

    function renderActionForm(props) {
        const fields = (props.fields || []).slice(0, 6).map(field => {
            const value = field.value == null || field.value === '' ? 'Not set' : field.value;
            return '<div class="sky-field"><span>' + escapeHtml(field.label || field.name || 'Field') + '</span><strong>' + escapeHtml(value) + '</strong></div>';
        }).join('');
        const actions = props.actions || [
            { label: 'Review', query: 'review ' + (props.title || 'form') },
            { label: 'Confirm', query: 'confirm ' + (props.form_id || props.title || 'form') }
        ];
        return cardFrame(Object.assign({ status: 'Form' }, props, { actions }), '<div class="sky-card-body">' + escapeHtml(props.summary || 'Review the fields before Zoe takes action.') + '</div><div class="sky-card-grid">' + fields + '</div>', { wide: true, tone: 'form-card' });
    }

    function renderUnsupportedContract(props) {
        return cardFrame({
            title: 'Card needs an update',
            kicker: 'Unsupported schema',
            body: 'This Skybridge renderer supports card schema 1.x.',
            status: props.schema_version || 'Unknown'
        }, '<div class="sky-card-body">This card was not rendered because its schema version is newer than the current Skybridge renderer.</div>', { wide: true, tone: 'warn' });
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

    const renderers = {
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
        media: renderList,
        smart_home: renderList,
        research_report: renderList,
        auth_challenge: renderAuthChallenge,
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
