/**
 * Home Widget
 * Displays smart home controls and status
 * Version: 1.0.0
 */

class HomeWidget extends WidgetModule {
    constructor() {
        super('home', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 60000 // Update every minute
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">🏠 Home</div>
                <div class="widget-badge" id="homeStatus">Online</div>
            </div>
            <div class="widget-content">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px;">
                    <button class="room-btn" onclick="toggleRoom('living_room')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        💡 Living Room
                    </button>
                    <button class="room-btn on" onclick="toggleRoom('kitchen')" style="padding: 12px; border: none; border-radius: 8px; background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); color: white; font-size: 12px; cursor: pointer;">
                        🔆 Kitchen
                    </button>
                    <button class="room-btn" onclick="toggleRoom('bedroom')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        🛏️ Bedroom
                    </button>
                    <button class="room-btn" onclick="toggleRoom('office')" style="padding: 12px; border: none; border-radius: 8px; background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px; cursor: pointer;">
                        💻 Office
                    </button>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                    <div style="text-align: center; padding: 8px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;">⚡ 2.4kW</div>
                        <div style="font-size: 10px; color: #666;">Solar Output</div>
                    </div>
                    <div style="text-align: center; padding: 8px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;">🔋 85%</div>
                        <div style="font-size: 10px; color: #666;">Battery</div>
                    </div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        // Load home status
        this.loadHomeStatus();
    }
    
    update() {
        this.loadHomeStatus();
    }
    
    async loadHomeStatus() {
        try {
            const response = await fetch('/api/homeassistant/states');
            if (response.ok) {
                const data = await response.json();
                this.updateHomeDisplay(data);
            }
        } catch (error) {
            console.error('Failed to load home status:', error);
            // Keep default display
        }
    }
    
    updateHomeDisplay(data) {
        // Update home display based on Home Assistant data
        console.log('Home data:', data);
    }
}

// Expose to global scope for WidgetManager
window.HomeWidget = HomeWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('home', new HomeWidget());
}




