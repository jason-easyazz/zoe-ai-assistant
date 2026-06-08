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

    function cardFrame(props, body, options) {
        const wide = options && options.wide ? ' wide' : '';
        const compact = options && options.compact ? ' compact' : '';
        const actions = Array.isArray(props.actions) && props.actions.length
            ? '<div class="sky-actions">' + props.actions.map(buttonHtml).join('') + '</div>'
            : '';
        return [
            '<article class="sky-card' + wide + compact + '" data-card-id="' + escapeHtml(props.id || '') + '">',
            '<div class="sky-card-header">',
            '<div>',
            props.kicker ? '<p class="sky-card-kicker">' + escapeHtml(props.kicker) + '</p>' : '',
            '<h3 class="sky-card-title">' + escapeHtml(props.title || 'Zoe') + '</h3>',
            '</div>',
            props.status ? '<span class="sky-badge">' + escapeHtml(props.status) + '</span>' : '',
            '</div>',
            body,
            actions,
            '</article>'
        ].join('');
    }

    function renderStatus(props) {
        return cardFrame(props, '<div class="sky-card-body">' + escapeHtml(props.body || props.message || '') + '</div>', { wide: !!props.wide });
    }

    function renderPage(props) {
        const fields = [
            ['Route', props.route || ''],
            ['Kind', props.kind || 'page'],
            ['Actions', (props.actions || []).join(', ')]
        ].map(pair => '<div class="sky-field"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        return cardFrame(Object.assign({
            actions: [
                { label: 'Open page', route: props.route, type: 'open' },
                { label: 'Show related settings', query: props.title + ' settings' }
            ]
        }, props), '<div class="sky-card-body">' + escapeHtml(props.summary || '') + '</div><div class="sky-card-grid">' + fields + '</div>', { wide: true });
    }

    function renderSetting(props) {
        const risk = props.risk || 'low';
        const changeLabel = risk === 'critical' ? 'Prepare change' : 'Change setting';
        const fields = [
            ['Risk', risk],
            ['Domain', props.domain || 'settings'],
            ['Section', props.id || '']
        ].map(pair => '<div class="sky-field"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>').join('');
        return cardFrame(Object.assign({
            status: risk,
            actions: [
                { label: 'Open settings', route: props.route, type: 'open' },
                { label: changeLabel, query: 'change ' + props.title, kind: risk === 'critical' ? 'warn' : 'normal' }
            ]
        }, props), '<div class="sky-card-body">' + escapeHtml(props.summary || '') + '</div><div class="sky-card-grid">' + fields + '</div>', { wide: true });
    }

    function renderPageGrid(props) {
        const items = (props.items || []).map(item => {
            return '<div class="sky-field"><span>' + escapeHtml(item.title) + '</span><strong>' + escapeHtml(item.summary) + '</strong></div>';
        }).join('');
        return cardFrame(props, '<div class="sky-card-grid">' + items + '</div>', { wide: true });
    }

    function renderSettingsOverview(props) {
        const items = (props.items || []).map(item => {
            return '<div class="sky-field"><span>' + escapeHtml(item.title) + ' · ' + escapeHtml(item.risk) + '</span><strong>' + escapeHtml(item.summary) + '</strong></div>';
        }).join('');
        return cardFrame(props, '<div class="sky-card-grid">' + items + '</div>', { wide: true });
    }

    function renderList(props) {
        const rows = (props.items || []).map(item => '<div class="sky-field"><strong>' + escapeHtml(typeof item === 'string' ? item : item.title || item.text || JSON.stringify(item)) + '</strong></div>').join('');
        return cardFrame(props, '<div class="sky-card-grid">' + rows + '</div>', { wide: true });
    }

    function normalizeCard(card) {
        if (!card) return { component: 'status', props: { title: 'Empty card', body: '' } };
        if (card.component) return { component: card.component, props: card.props || {} };
        if (card.card_type && card.content) {
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
        action_form: renderStatus,
        form: renderStatus,
        media: renderList,
        smart_home: renderList,
        research_report: renderList,
        stream_text: renderStatus
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
