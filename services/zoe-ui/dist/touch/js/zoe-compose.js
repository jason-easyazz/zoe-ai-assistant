/*
 * zoe-compose — the shared tree renderer for Zoe's composable UI catalog.
 *
 * Renders a server-validated component tree (see services/zoe-data/ui_catalog.py,
 * the single source of truth) into HTML. Used by BOTH the touch panel
 * (skybridge) and chat, so the two surfaces can never drift.
 *
 * Contract:
 *  - Only catalog components render; anything unknown becomes an inert notice.
 *  - ALL text passes through escapeHtml here — one escaping choke-point.
 *  - Actions are never serialized as code: buttons carry data-sky-action="query"
 *    + data-query, the same delegation contract the touch action loop already
 *    handles (chat wires the same attributes to its send path).
 *  - No dependencies; safe to load standalone on any page.
 */
(function () {
    'use strict';

    var MAX_DEPTH = 8; // defensive; server validates depth <= 6 before we ever see it

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function tokenClass(prefix, value, allowed, fallback) {
        var v = String(value || fallback);
        return allowed.indexOf(v) >= 0 ? prefix + v : prefix + fallback;
    }

    // Small self-contained glyph set (stroke inherits currentColor).
    var GLYPHS = {
        calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 9h18M8 3v4M16 3v4"/>',
        list: '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
        weather: '<circle cx="12" cy="10" r="4"/><path d="M12 2v2M12 16v2M4 10h2M18 10h2M6 4l1.5 1.5M16.5 14.5L18 16M18 4l-1.5 1.5M7.5 14.5L6 16"/>',
        person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/>',
        clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
        home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
        music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
        camera: '<rect x="2" y="7" width="15" height="12" rx="2"/><path d="M17 11l5-3v8l-5-3"/>',
        timer: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/>',
        note: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
        droplet: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
        heart: '<path d="M12 20s-7-4.5-9-9a4.5 4.5 0 0 1 9-2 4.5 4.5 0 0 1 9 2c-2 4.5-9 9-9 9z"/>',
        check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
        star: '<path d="M12 3l2.6 5.6 6 .8-4.4 4.2 1.1 6L12 17.8 6.7 19.6l1.1-6L3.4 9.4l6-.8z"/>',
        dollar: '<circle cx="12" cy="12" r="9"/><path d="M15 9a3 3 0 0 0-3-2c-1.7 0-3 1-3 2.3 0 3 6 1.7 6 4.7 0 1.4-1.3 2.3-3 2.3a3 3 0 0 1-3-2M12 5.5v13"/>',
        chart: '<path d="M4 20V4M4 20h16M8 16v-4M13 16V8M18 16v-7"/>',
        flame: '<path d="M12 3c1 3 4 4.5 4 8a4 4 0 0 1-8 0c0-1.5.5-2.5 1.2-3.3C9.6 8.5 11 7 12 3z"/>',
        leaf: '<path d="M5 19c0-8 6-13 14-13 0 8-5 14-13 14-.5 0-1 0-1-1z"/><path d="M5 19c3-4 6-6 9-7"/>'
    };

    function glyphSvg(name, size) {
        var path = GLYPHS[name] || GLYPHS.note;
        var s = Number(size) > 0 ? Number(size) : 20;
        return '<svg class="zx-glyph-svg" width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" ' +
            'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            path + '</svg>';
    }

    function actionButton(node) {
        var action = node.action || {};
        var label = escapeHtml(action.label || 'Action');
        // Query actions only ever reach the DOM; an intent-only action degrades
        // to its label as a natural-language query (server re-validates on fire).
        var query = escapeHtml(action.query || action.label || '');
        var kind = action.kind === 'primary' ? ' zx-primary' : (action.kind === 'warn' ? ' zx-warn' : '');
        return '<button type="button" class="zx-action' + kind + '" data-sky-action="query" data-query="' + query + '">' + label + '</button>';
    }

    var TONES = ['neutral', 'cool', 'warm', 'mint', 'violet', 'sunny'];
    var GLYPH_SIZES = { sm: 18, md: 24, lg: 40, xl: 64 };

    function toneClass(tone) {
        return TONES.indexOf(String(tone)) > 0 ? ' zx-t-' + tone : '';
    }

    var BUILDERS = {
        Stack: function (n, kids) {
            return '<div class="zx-stack ' + tokenClass('zx-gap-', n.gap, ['sm', 'md', 'lg'], 'md') + toneClass(n.tone) + '">' + kids + '</div>';
        },
        Row: function (n, kids) {
            var align = tokenClass('zx-align-', n.align, ['start', 'center', 'end', 'between'], 'center');
            return '<div class="zx-row ' + tokenClass('zx-gap-', n.gap, ['sm', 'md', 'lg'], 'md') + ' ' + align + toneClass(n.tone) + '">' + kids + '</div>';
        },
        Grid: function (n, kids) {
            var cols = Math.min(4, Math.max(1, parseInt(n.columns, 10) || 2));
            return '<div class="zx-grid zx-cols-' + cols + toneClass(n.tone) + '">' + kids + '</div>';
        },
        Text: function (n) {
            var role = tokenClass('zx-text-', n.role, ['title', 'body', 'caption', 'kicker'], 'body');
            return '<p class="zx-text ' + role + '">' + escapeHtml(n.text) + '</p>';
        },
        Hero: function (n) {
            return '<div class="zx-hero' + toneClass(n.tone) + '">' +
                (n.glyph ? '<span class="zx-hero-glyph">' + glyphSvg(n.glyph, 52) + '</span>' : '') +
                '<div class="zx-hero-figure"><span class="zx-hero-value">' + escapeHtml(n.value) + '</span>' +
                (n.unit ? '<span class="zx-hero-unit">' + escapeHtml(n.unit) + '</span>' : '') + '</div>' +
                (n.caption ? '<span class="zx-hero-caption">' + escapeHtml(n.caption) + '</span>' : '') + '</div>';
        },
        Stat: function (n) {
            return '<div class="zx-stat"><strong>' + escapeHtml(n.value) + '</strong>' +
                (n.label ? '<span>' + escapeHtml(n.label) + '</span>' : '') + '</div>';
        },
        Badge: function (n) {
            var tone = tokenClass('zx-tone-', n.tone, ['neutral', 'accent', 'warn', 'success'], 'neutral');
            return '<span class="zx-badge ' + tone + '">' + escapeHtml(n.text) + '</span>';
        },
        ListRow: function (n) {
            var check = n.variant === 'check';
            var icon = (n.icon && !check) ? '<span class="zx-listrow-icon" aria-hidden="true">' + glyphSvg(n.icon, 22) + '</span>' : '';
            return '<div class="zx-listrow' + (n.checked ? ' zx-checked' : '') + '">' +
                (check ? '<span class="zx-check" aria-hidden="true">' + (n.checked ? '✓' : '') + '</span>' : icon) +
                '<span class="zx-listrow-main"><strong>' + escapeHtml(n.title) + '</strong>' +
                (n.detail ? '<em>' + escapeHtml(n.detail) + '</em>' : '') + '</span></div>';
        },
        Progress: function (n) {
            var v = Math.max(0, Math.min(100, Number(n.value) || 0));
            return '<div class="zx-progress"' + (n.label ? '' : ' aria-label="progress"') + '>' +
                (n.label ? '<span class="zx-progress-label">' + escapeHtml(n.label) + '</span>' : '') +
                '<span class="zx-progress-track" role="progressbar" aria-valuenow="' + v + '" aria-valuemin="0" aria-valuemax="100">' +
                '<i style="width:' + v + '%"></i></span></div>';
        },
        Glyph: function (n) {
            var size = GLYPH_SIZES[String(n.size)] || 24;
            return '<span class="zx-glyph' + toneClass(n.tone) + '">' + glyphSvg(n.name, size) + '</span>';
        },
        Image: function (n) {
            var src = String(n.src || '');
            if (src.charAt(0) !== '/' || src.charAt(1) === '/') return ''; // same-origin only (server enforces too)
            return '<img class="zx-image" src="' + escapeHtml(src) + '" alt="' + escapeHtml(n.alt || '') + '" loading="lazy">';
        },
        Divider: function () { return '<hr class="zx-divider">'; },
        Spacer: function (n) {
            return '<span class="zx-spacer ' + tokenClass('zx-gap-', n.size, ['sm', 'md', 'lg'], 'md') + '" aria-hidden="true"></span>';
        },
        ActionButton: actionButton,
        MediaTile: function (n) {
            var src = String(n.src || '');
            if (src.charAt(0) !== '/' || src.charAt(1) === '/') return '';
            return '<figure class="zx-mediatile"><img src="' + escapeHtml(src) + '" alt="' + escapeHtml(n.title || '') + '" loading="lazy">' +
                (n.title ? '<figcaption><strong>' + escapeHtml(n.title) + '</strong>' +
                    (n.subtitle ? '<span>' + escapeHtml(n.subtitle) + '</span>' : '') + '</figcaption>' : '') + '</figure>';
        }
    };

    function renderNode(node, depth) {
        if (!node || typeof node !== 'object' || depth > MAX_DEPTH) return '';
        var builder = BUILDERS[node.component];
        if (!builder) {
            return '<span class="zx-unknown">' + escapeHtml(String(node.component || 'component')) + '</span>';
        }
        var kids = '';
        if (Object.prototype.hasOwnProperty.call(node, 'children') && Array.isArray(node.children)) {
            for (var i = 0; i < node.children.length; i++) kids += renderNode(node.children[i], depth + 1);
        }
        return builder(node, kids);
    }

    function render(tree) {
        // A tone on the root container themes the whole card (accent + gradient wash).
        var rootTone = (tree && typeof tree === 'object') ? toneClass(tree.tone) : '';
        return '<div class="zx-root' + rootTone + '">' + renderNode(tree, 1) + '</div>';
    }

    window.ZoeCompose = {
        render: render,
        escapeHtml: escapeHtml,
        components: Object.keys(BUILDERS)
    };
})();
