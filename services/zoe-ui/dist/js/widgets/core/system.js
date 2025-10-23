/**
 * System Widget
 * Displays system status and resources
 * Version: 1.0.0
 */

class SystemWidget extends WidgetModule {
    constructor() {
        super('system', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 30000 // Update every 30 seconds
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">💻 System</div>
                <div class="widget-badge" id="systemStatus">Online</div>
            </div>
            <div class="widget-content">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                    <div style="text-align: center; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="cpuUsage">45%</div>
                        <div style="font-size: 10px; color: #666;">CPU</div>
                    </div>
                    <div style="text-align: center; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="memoryUsage">2.1GB</div>
                        <div style="font-size: 10px; color: #666;">Memory</div>
                    </div>
                    <div style="text-align: center; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="diskUsage">156GB</div>
                        <div style="font-size: 10px; color: #666;">Disk</div>
                    </div>
                    <div style="text-align: center; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 8px;">
                        <div style="font-size: 16px; font-weight: 600; color: #7B61FF;" id="uptime">2d 14h</div>
                        <div style="font-size: 10px; color: #666;">Uptime</div>
                    </div>
                </div>
                <div style="margin-top: 16px; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 8px; text-align: center;">
                    <div style="font-size: 12px; color: #666; margin-bottom: 4px;">Services</div>
                    <div style="font-size: 14px; color: #22c55e;">✅ All Systems Operational</div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        // Update immediately
        this.updateSystemStats();
    }
    
    update() {
        this.updateSystemStats();
    }
    
    updateSystemStats() {
        // Mock system stats - in real implementation, these would come from API
        const cpuElement = this.element.querySelector('#cpuUsage');
        const memoryElement = this.element.querySelector('#memoryUsage');
        const diskElement = this.element.querySelector('#diskUsage');
        const uptimeElement = this.element.querySelector('#uptime');
        
        if (cpuElement) {
            cpuElement.textContent = Math.floor(Math.random() * 30 + 30) + '%';
        }
        
        if (memoryElement) {
            memoryElement.textContent = (Math.random() * 2 + 1).toFixed(1) + 'GB';
        }
        
        if (diskElement) {
            diskElement.textContent = Math.floor(Math.random() * 50 + 150) + 'GB';
        }
        
        if (uptimeElement) {
            uptimeElement.textContent = Math.floor(Math.random() * 5 + 1) + 'd ' + Math.floor(Math.random() * 24) + 'h';
        }
    }
}

// Expose to global scope for WidgetManager
window.SystemWidget = SystemWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('system', new SystemWidget());
}




