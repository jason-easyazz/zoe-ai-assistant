/**
 * System Widget - the box's real CPU, memory, disk, uptime and service health
 * (Wave 4b, 2026-10-19).
 *
 * Reads GET /api/system/stats (member-only; services/zoe-data/routers/system_stats.py).
 * It used to show Math.random() numbers and a permanent "All Systems Operational".
 * Now nothing is shown that the server did not report: a loading state until the
 * first answer, "unavailable" when there is none, "—" for any single reading the
 * server could not take, and the last good numbers marked stale when a refresh fails.
 * Rendering is a pure function of the state (SystemWidget.render).
 * Version: 2.0.0
 */

class SystemWidget extends WidgetModule {
    constructor() {
        super('system', {
            version: '2.0.0',
            defaultSize: 'size-small',
            updateInterval: 30000
        });
        this.state = { kind: 'loading' };
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">💻 System</div>
                <div class="widget-badge" data-role="system-badge">…</div>
            </div>
            <div class="widget-content zw-body" data-role="system-body" aria-live="polite" aria-busy="true">
                ${SystemWidget.render({ kind: 'loading' })}
            </div>
        `;
    }

    init(element) {
        super.init(element);
        this.element = element;
        this.show({ kind: 'loading' });
        this.load();
    }

    update() {
        this.load();
    }

    async load() {
        let next;
        try {
            const r = await fetch('/api/system/stats', { credentials: 'same-origin' });
            if (r.status === 401 || r.status === 403) {
                next = { kind: 'denied' };
            } else if (!r.ok) {
                throw new Error('HTTP ' + r.status);
            } else {
                next = { kind: 'data', data: await r.json() };
            }
        } catch (error) {
            const prev = this.state;
            next = (prev && prev.data) ? { kind: 'data', data: prev.data, stale: true } : { kind: 'unavailable' };
        }
        this.show(next);
    }

    show(state) {
        this.state = state;
        if (!this.element) return;
        const body = this.element.querySelector('[data-role="system-body"]');
        const badge = this.element.querySelector('[data-role="system-badge"]');
        if (body) {
            body.innerHTML = SystemWidget.render(state);
            body.setAttribute('aria-busy', state.kind === 'loading' ? 'true' : 'false');
        }
        if (badge) badge.textContent = SystemWidget.badge(state);
    }

    static esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    static num(v) {
        return (typeof v === 'number' && isFinite(v)) ? v : null;
    }

    static bytes(n) {
        n = SystemWidget.num(n);
        if (n === null) return '—';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
        return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + units[i];
    }

    static uptime(seconds) {
        const s = SystemWidget.num(seconds);
        if (s === null) return '—';
        const d = Math.floor(s / 86400);
        const h = Math.floor((s % 86400) / 3600);
        const m = Math.floor((s % 3600) / 60);
        if (d) return d + 'd ' + h + 'h';
        if (h) return h + 'h ' + m + 'm';
        return m + 'm';
    }

    static pct(v) {
        const n = SystemWidget.num(v);
        return n === null ? '—' : Math.round(n) + '%';
    }

    static badge(state) {
        if (state.kind === 'loading') return '…';
        if (state.kind !== 'data') return 'Offline';
        if (state.stale) return 'Stale';
        const svcs = Array.isArray(state.data.services) ? state.data.services : [];
        const bad = svcs.filter(s => s && s.status !== 'ok').length;
        if (!svcs.length) return '—';
        return bad ? bad + ' need' + (bad === 1 ? 's' : '') + ' attention' : 'All good';
    }

    static stat(label, value, detail, percent) {
        const esc = SystemWidget.esc;
        const p = SystemWidget.num(percent);
        const bar = p === null ? '' :
            `<span class="zw-bar" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, Math.round(p)))}%"></span></span>`;
        return `<div class="zw-stat"><div class="zw-stat-value">${esc(value)}</div>` +
            `<div class="zw-stat-label">${esc(label)}</div>${bar}` +
            (detail ? `<div class="zw-stat-detail">${esc(detail)}</div>` : '') + '</div>';
    }

    /** HTML for a state. Every interpolated value goes through esc(). */
    static render(state) {
        const esc = SystemWidget.esc;
        if (state.kind === 'loading') return '<div class="zw-state zw-loading" role="status">Loading system status…</div>';
        if (state.kind === 'denied') return '<div class="zw-state" role="status">Sign in to see system status.</div>';
        if (state.kind !== 'data' || !state.data) {
            return '<div class="zw-state zw-error" role="status">System status is unavailable right now.</div>';
        }
        const d = state.data;
        const cpu = d.cpu || {};
        const mem = d.memory || {};
        const disk = d.disk || {};
        const stats = [
            SystemWidget.stat('CPU', SystemWidget.pct(cpu.percent),
                SystemWidget.num(cpu.cores) !== null ? cpu.cores + ' cores' : '', cpu.percent),
            SystemWidget.stat('Memory', SystemWidget.pct(mem.percent),
                SystemWidget.num(mem.total_bytes) !== null ? SystemWidget.bytes(mem.used_bytes) + ' of ' + SystemWidget.bytes(mem.total_bytes) : '',
                mem.percent),
            SystemWidget.stat('Disk', SystemWidget.pct(disk.percent),
                SystemWidget.num(disk.free_bytes) !== null ? SystemWidget.bytes(disk.free_bytes) + ' free' : '', disk.percent),
            SystemWidget.stat('Uptime', SystemWidget.uptime(d.uptime_seconds), '', null)
        ].join('');
        const svcs = Array.isArray(d.services) ? d.services : [];
        const LABEL = { ok: 'OK', degraded: 'Degraded', down: 'Down' };
        const services = svcs.length ? '<ul class="zw-services" aria-label="Services">' + svcs.map(s => {
            const status = LABEL[s && s.status] ? s.status : 'down';
            return `<li class="zw-svc zw-svc--${status}"><span class="zw-dot" aria-hidden="true"></span>` +
                `<span class="zw-svc-name">${esc(s && s.name)}</span><span class="zw-svc-state">${LABEL[status]}</span></li>`;
        }).join('') + '</ul>' : '';
        const stale = state.stale ? '<div class="zw-note" role="status">Couldn’t refresh - showing the last reading.</div>' : '';
        return stale + `<div class="zw-stats">${stats}</div>` + services;
    }
}

// Expose to global scope for WidgetManager
window.SystemWidget = SystemWidget;
