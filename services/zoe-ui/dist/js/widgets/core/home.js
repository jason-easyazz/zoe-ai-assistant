/**
 * Home Widget
 * Displays Home Assistant status and quick room toggles.
 *
 * zoe-data exposes HA through /api/ha/* (entities, state, control) — the
 * previous /api/homeassistant/states path is a legacy/zoe-core URL that
 * never resolves, so the widget fell back to a hard-coded cosmetic state.
 * Version: 1.1.0
 */

class HomeWidget extends WidgetModule {
    constructor() {
        super('home', {
            version: '1.1.0',
            defaultSize: 'size-small',
            updateInterval: 60000
        });

        // Friendly room id -> HA entity id. Users with different entity
        // naming can override via localStorage['zoe_home_rooms'] (JSON).
        this.rooms = {
            living_room: 'light.living_room',
            kitchen:     'light.kitchen',
            bedroom:     'light.bedroom',
            office:      'light.office',
        };
        try {
            const override = JSON.parse(localStorage.getItem('zoe_home_rooms') || 'null');
            if (override && typeof override === 'object') {
                this.rooms = { ...this.rooms, ...override };
            }
        } catch (_) { /* ignore bad JSON */ }

        this._entityState = {};
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">🏠 Home</div>
                <div class="widget-badge" id="homeStatus">…</div>
            </div>
            <div class="widget-content">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px;">
                    <button class="room-btn" data-room="living_room" onclick="HomeWidget.toggleRoom('living_room')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        💡 Living Room
                    </button>
                    <button class="room-btn" data-room="kitchen" onclick="HomeWidget.toggleRoom('kitchen')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        🔆 Kitchen
                    </button>
                    <button class="room-btn" data-room="bedroom" onclick="HomeWidget.toggleRoom('bedroom')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        🛏️ Bedroom
                    </button>
                    <button class="room-btn" data-room="office" onclick="HomeWidget.toggleRoom('office')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        💻 Office
                    </button>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;" id="homeStats">
                    <div style="text-align: center; padding: 8px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="homeSolar">—</div>
                        <div style="font-size: 10px; color: #666;">Solar Output</div>
                    </div>
                    <div style="text-align: center; padding: 8px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="homeBattery">—</div>
                        <div style="font-size: 10px; color: #666;">Battery</div>
                    </div>
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);
        this.element = element;
        this.loadHomeStatus();
    }

    update() {
        this.loadHomeStatus();
    }

    async loadHomeStatus() {
        const badge = this.element ? this.element.querySelector('#homeStatus') : null;
        try {
            const response = await fetch('/api/ha/entities');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const entities = Array.isArray(data) ? data : (data.entities || []);
            this.updateHomeDisplay(entities);
            if (badge) {
                badge.textContent = 'Online';
                badge.style.color = '';
            }
        } catch (error) {
            console.warn('Home widget: HA bridge unreachable', error);
            if (badge) {
                badge.textContent = 'Offline';
                badge.style.color = '#b91c1c';
            }
        }
    }

    updateHomeDisplay(entities) {
        if (!this.element || !Array.isArray(entities)) return;

        // Build a quick entity_id -> state map and cache it.
        const byId = {};
        entities.forEach(e => {
            if (e && e.entity_id) byId[e.entity_id] = e;
        });
        this._entityState = byId;

        // Reflect each room button's on/off state.
        const onGradient = 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)';
        const offBg = 'rgba(123, 97, 255, 0.1)';
        Object.keys(this.rooms).forEach(room => {
            const entityId = this.rooms[room];
            const ent = byId[entityId];
            const isOn = ent && String(ent.state || '').toLowerCase() === 'on';
            const btn = this.element.querySelector(`.room-btn[data-room="${room}"]`);
            if (!btn) return;
            btn.classList.toggle('on', !!isOn);
            btn.style.background = isOn ? onGradient : offBg;
            btn.style.color = isOn ? 'white' : '#7B61FF';
        });

        // Optional energy sensors — use them if the HA install exposes them.
        const solar = byId['sensor.solar_power'] || byId['sensor.solar_output'];
        const battery = byId['sensor.home_battery'] || byId['sensor.battery_level'];
        const solarEl = this.element.querySelector('#homeSolar');
        const batteryEl = this.element.querySelector('#homeBattery');
        if (solarEl) {
            if (solar && solar.state && solar.state !== 'unavailable') {
                const unit = (solar.attributes && solar.attributes.unit_of_measurement) || 'kW';
                solarEl.textContent = `⚡ ${solar.state} ${unit}`;
            } else {
                solarEl.textContent = '—';
            }
        }
        if (batteryEl) {
            if (battery && battery.state && battery.state !== 'unavailable') {
                batteryEl.textContent = `🔋 ${battery.state}%`;
            } else {
                batteryEl.textContent = '—';
            }
        }
    }

    static async toggleRoom(room) {
        const inst = window.__zoeHomeWidget || null;
        const entityId = inst && inst.rooms ? inst.rooms[room] : null;
        if (!entityId) {
            console.warn('HomeWidget.toggleRoom: no entity mapped for', room);
            return;
        }
        try {
            const response = await fetch('/api/ha/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entity_id: entityId, action: 'toggle' })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            if (inst) await inst.loadHomeStatus();
        } catch (error) {
            console.error('HomeWidget.toggleRoom failed:', error);
        }
    }
}

// Expose to global scope for WidgetManager
window.HomeWidget = HomeWidget;

// Keep a global toggleRoom shim so any legacy onclick="toggleRoom(...)"
// handlers elsewhere in the UI still work.
if (typeof window.toggleRoom !== 'function') {
    window.toggleRoom = function(room) { return HomeWidget.toggleRoom(room); };
}

// Register widget + keep the live instance reachable for onclick handlers.
if (typeof WidgetRegistry !== 'undefined') {
    const homeInstance = new HomeWidget();
    window.__zoeHomeWidget = homeInstance;
    WidgetRegistry.register('home', homeInstance);
}
