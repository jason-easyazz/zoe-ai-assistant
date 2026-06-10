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
        const kind = action.kind === 'warn' ? ' warn' : (index === 0 ? ' primary' : '');
        return '<button type="button" class="' + kind.trim() + '" data-sky-action="' + escapeHtml(action.type || 'query') + '" data-query="' + query + '" data-route="' + route + '">' + label + '</button>';
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
        const actions = Array.isArray(props.actions) && props.actions.length
            ? '<div class="sky-actions">' + props.actions.map(buttonHtml).join('') + '</div>'
            : '';
        return [
            '<article class="sky-card' + wide + compact + tone + '" data-card-id="' + escapeHtml(props.id || '') + '">',
            '<div class="sky-widget-top">',
            '<div class="sky-widget-title">',
            props.kicker ? '<p>' + escapeHtml(props.kicker) + '</p>' : '',
            '<h3 class="sky-card-title">' + escapeHtml(props.title || 'Zoe') + '</h3>',
            '</div>',
            '<div class="sky-widget-glyph" aria-hidden="true">' + glyphFor(props) + '</div>',
            '</div>',
            props.status ? '<span class="sky-badge">' + escapeHtml(props.status) + '</span>' : '',
            body,
            actions,
            '</article>'
        ].join('');
    }

    function renderStatus(props) {
        if (props.source === 'calendar_show') return renderCalendar(props);
        if (props.source === 'weather_current' || props.source === 'weather_forecast') return renderWeather(props);
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

    function renderCalendar(props) {
        const events = Array.isArray(props.events) ? props.events : [];
        const rows = events.slice(0, 8).map(item => {
            const title = item.title || item.name || 'Calendar event';
            const detail = [item.location, item.category].filter(Boolean).join(' · ');
            return '<div class="sky-event-row"><span>' + escapeHtml(formatEventTime(item)) + '</span><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</div>';
        }).join('');
        const empty = '<div class="sky-empty-data"><strong>No events ' + escapeHtml(props.qualifier || 'today') + '</strong><span>Your calendar is clear for this range.</span></div>';
        const body = [
            '<div class="sky-widget-metric"><strong>' + escapeHtml(events.length) + '</strong><span>' + escapeHtml(events.length === 1 ? 'event' : 'events') + ' ' + escapeHtml(props.qualifier || '') + '</span></div>',
            '<div class="sky-data-list">' + (rows || empty) + '</div>'
        ].join('');
        return cardFrame(Object.assign({ status: 'Calendar', icon: 'C' }, props), body, { wide: true, tone: 'calendar-card' });
    }

    function formatTemp(value) {
        if (value == null || value === '') return '--';
        const number = Number(value);
        return Number.isFinite(number) ? Math.round(number) + '°' : String(value);
    }

    function renderWeather(props) {
        const current = props.current || {};
        const forecast = props.forecast || {};
        const daily = Array.isArray(forecast.daily) ? forecast.daily : [];
        const hourly = Array.isArray(forecast.hourly) ? forecast.hourly : [];
        const location = props.location || {};
        const metric = props.source === 'weather_forecast'
            ? '<div class="sky-widget-metric"><strong>' + escapeHtml(daily.length || hourly.length) + '</strong><span>forecast points</span></div>'
            : '<div class="sky-widget-metric"><strong>' + escapeHtml(formatTemp(current.temp)) + '</strong><span>' + escapeHtml(current.description || 'Current conditions') + '</span></div>';
        const facts = [
            ['Feels like', formatTemp(current.feels_like)],
            ['Humidity', current.humidity == null ? '--' : current.humidity + '%'],
            ['Wind', current.wind_speed == null ? '--' : current.wind_speed + ' m/s'],
            ['Location', [location.city || current.city, location.country || current.country].filter(Boolean).join(', ')]
        ].map(pair => '<div class="sky-field"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        const tiles = daily.slice(0, 5).map(day => {
            return '<div class="sky-forecast-tile"><span>' + escapeHtml(day.day || '') + '</span><strong>' + escapeHtml(formatTemp(day.high)) + ' / ' + escapeHtml(formatTemp(day.low)) + '</strong><em>' + escapeHtml(day.description || '') + '</em></div>';
        }).join('') || hourly.slice(0, 5).map(hour => {
            return '<div class="sky-forecast-tile"><span>' + escapeHtml(hour.time || '') + '</span><strong>' + escapeHtml(formatTemp(hour.temp)) + '</strong><em>' + escapeHtml(hour.description || '') + '</em></div>';
        }).join('');
        const body = metric + '<div class="sky-widget-strip">' + facts + '</div>' + (tiles ? '<div class="sky-forecast-row">' + tiles + '</div>' : '');
        return cardFrame(Object.assign({ status: 'Weather', icon: 'W' }, props), body, { wide: true, tone: 'weather-card' });
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
