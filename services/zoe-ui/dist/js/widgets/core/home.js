/**
 * Home Widget - this house's real rooms and switchable devices (Wave 4b, 2026-10-19).
 *
 * Reads the same two routes the estate's Rooms surface reads (touch/home.html):
 *   GET /api/rooms        Zoe's own rooms, each device resolved against live HA state
 *   GET /api/ha/entities  the live entity list (lights / switches / input_booleans / fans)
 * and toggles through POST /api/ha/control {entity_id, action: 'toggle'}.
 * Both reads need a member or the panel; the desktop is members-only.
 *
 * It used to draw four hard-coded rooms (Living Room / Kitchen / Bedroom / Office)
 * mapped to light.<room> ids whatever the house had. Now every state is honest:
 * loading, the house, an empty house, or unavailable. Rendering is a pure
 * function of the state (HomeWidget.render) so the harness can check every
 * state without a browser.
 * Version: 2.0.0
 */

class HomeWidget extends WidgetModule {
    constructor() {
        super('home', {
            version: '2.0.0',
            defaultSize: 'size-small',
            updateInterval: 60000
        });
        this.state = { kind: 'loading' };
        this._onClick = null;
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">🏠 Home</div>
                <div class="widget-badge" data-role="home-badge">…</div>
            </div>
            <div class="widget-content zw-body" data-role="home-body" aria-live="polite" aria-busy="true">
                ${HomeWidget.render({ kind: 'loading' })}
            </div>
        `;
    }

    init(element) {
        super.init(element);
        this.element = element;
        this._onClick = (ev) => {
            const btn = ev.target && ev.target.closest ? ev.target.closest('button[data-entity-id]') : null;
            if (btn && element.contains(btn)) {
                ev.stopPropagation();
                this.toggle(btn);
            }
        };
        element.addEventListener('click', this._onClick);
        this.show({ kind: 'loading' });
        this.load();
    }

    update() {
        this.load();
    }

    destroy() {
        super.destroy();
        if (this.element && this._onClick) this.element.removeEventListener('click', this._onClick);
        this._onClick = null;
    }

    static async _getJson(url) {
        const r = await fetch(url, { credentials: 'same-origin' });
        if (r.status === 401 || r.status === 403) return { denied: true };
        if (!r.ok) return { failed: true };
        return { data: await r.json() };
    }

    async load() {
        const [rooms, ents] = await Promise.all([
            HomeWidget._getJson('/api/rooms').catch(() => ({ failed: true })),
            HomeWidget._getJson('/api/ha/entities').catch(() => ({ failed: true }))
        ]);
        this.show(HomeWidget.stateFrom(rooms, ents));
    }

    /** Fold the two reads into one render state. Pure. */
    static stateFrom(rooms, ents) {
        if (rooms.denied && ents.denied) return { kind: 'denied' };
        const roomList = (rooms.data && Array.isArray(rooms.data.rooms)) ? rooms.data.rooms : null;
        let entList = null;
        if (ents.data) {
            if (Array.isArray(ents.data)) entList = ents.data;
            else if (Array.isArray(ents.data.entities)) entList = ents.data.entities;
        }
        if (roomList === null && entList === null) return { kind: 'unavailable' };
        const devices = HomeWidget.switchable(entList || []);
        const haDown = entList === null || !!(rooms.data && rooms.data.ha_available === false);
        if (!(roomList || []).length && !devices.length) {
            return haDown ? { kind: 'unavailable' } : { kind: 'empty' };
        }
        return { kind: 'data', rooms: roomList || [], devices, haDown };
    }

    static switchable(entities) {
        const DOMAINS = ['light', 'switch', 'input_boolean', 'fan'];
        return entities
            .filter(e => e && typeof e.entity_id === 'string' && DOMAINS.includes(e.entity_id.split('.')[0]))
            .filter(e => e.state !== 'unavailable' && e.state !== 'unknown')
            .map(e => ({
                entity_id: e.entity_id,
                name: String((e.attributes && e.attributes.friendly_name) || (e.entity_id.split('.')[1] || '').replace(/_/g, ' ')),
                on: String(e.state).toLowerCase() === 'on'
            }))
            .sort((a, b) => a.name.localeCompare(b.name))
            .slice(0, 8);
    }

    show(state) {
        this.state = state;
        if (!this.element) return;
        const body = this.element.querySelector('[data-role="home-body"]');
        const badge = this.element.querySelector('[data-role="home-badge"]');
        if (body) {
            body.innerHTML = HomeWidget.render(state);
            body.setAttribute('aria-busy', state.kind === 'loading' ? 'true' : 'false');
        }
        if (badge) badge.textContent = HomeWidget.badge(state);
    }

    static badge(state) {
        if (state.kind === 'loading') return '…';
        if (state.kind === 'data') {
            const on = state.devices.filter(d => d.on).length;
            return on ? on + ' on' : 'All off';
        }
        if (state.kind === 'empty') return '—';
        return 'Offline';
    }

    static esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** HTML for a state. Every interpolated value goes through esc(). */
    static render(state) {
        const esc = HomeWidget.esc;
        switch (state.kind) {
        case 'loading':
            return '<div class="zw-state zw-loading" role="status">Loading your home…</div>';
        case 'denied':
            return '<div class="zw-state" role="status">Sign in to see your home.</div>';
        case 'unavailable':
            return '<div class="zw-state zw-error" role="status">Home Assistant isn’t answering right now.</div>';
        case 'empty':
            return '<div class="zw-state zw-empty" role="status">No rooms or devices yet. Once Home Assistant has devices, they show up here.</div>';
        default: break;
        }
        const parts = [];
        if (state.haDown) {
            parts.push('<div class="zw-note" role="status">Home Assistant isn’t answering - showing your rooms only.</div>');
        }
        if (state.rooms.length) {
            parts.push('<ul class="zw-rooms" aria-label="Rooms">' + state.rooms.map(room => {
                const devs = Array.isArray(room.devices) ? room.devices : [];
                const on = devs.filter(d => d && String(d.state).toLowerCase() === 'on').length;
                let summary = 'No devices';
                if (devs.length) summary = on ? `${on} of ${devs.length} on` : `${devs.length} device${devs.length === 1 ? '' : 's'}`;
                return `<li class="zw-room"><span class="zw-room-name">${esc(room.name)}</span><span class="zw-room-meta">${esc(summary)}</span></li>`;
            }).join('') + '</ul>');
        }
        if (state.devices.length) {
            parts.push('<div class="zw-devices" role="group" aria-label="Lights and switches">' + state.devices.map(d =>
                `<button type="button" class="zw-device${d.on ? ' is-on' : ''}" data-entity-id="${esc(d.entity_id)}" aria-pressed="${d.on ? 'true' : 'false'}">` +
                `<span class="zw-device-name">${esc(d.name)}</span><span class="zw-device-state">${d.on ? 'On' : 'Off'}</span></button>`
            ).join('') + '</div>');
        }
        return parts.join('');
    }

    async toggle(btn) {
        if (btn.disabled) return;
        const entityId = btn.getAttribute('data-entity-id');
        btn.disabled = true;
        try {
            const r = await fetch('/api/ha/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ entity_id: entityId, action: 'toggle' })
            });
            if (!r.ok) throw new Error('HTTP ' + r.status);
        } catch (error) {
            console.warn('Home widget: toggle failed', error);
        } finally {
            btn.disabled = false;
            this.load();
        }
    }
}

// Expose to global scope for WidgetManager
window.HomeWidget = HomeWidget;
