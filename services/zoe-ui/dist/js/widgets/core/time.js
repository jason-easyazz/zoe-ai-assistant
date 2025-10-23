/**
 * Time Widget
 * Displays current time, date, and day of week
 * Version: 2.0.0
 */

class TimeWidget extends WidgetModule {
    constructor() {
        super('time', {
            version: '2.0.0',
            defaultSize: 'size-large',
            updateInterval: 1000 // Update every second
        });
    }
    
    getTemplate() {
        return `
            <div class="time-widget-content">
                <div class="time-gradient-bg"></div>
                <div class="time-main">
                    <div id="clockTime" class="time-display">--:--</div>
                    <div id="clockDate" class="date-display">Loading...</div>
                    <div id="clockDay" class="day-display"></div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        // Detect widget size and add appropriate class
        this.updateWidgetSize(element);
        
        // Update immediately
        this.updateTime();
        
        // Watch for resize
        const resizeObserver = new ResizeObserver(() => {
            this.updateWidgetSize(element);
        });
        resizeObserver.observe(element);
    }
    
    updateWidgetSize(element) {
        const width = element.offsetWidth;
        const height = element.offsetHeight;
        
        // Remove all size classes
        element.classList.remove('time-compact', 'time-normal', 'time-spacious');
        
        // Add appropriate size class
        if (width < 250 || height < 200) {
            element.classList.add('time-compact');
        } else if (width >= 400 || height >= 300) {
            element.classList.add('time-spacious');
        } else {
            element.classList.add('time-normal');
        }
    }
    
    update() {
        this.updateTime();
    }
    
    updateTime() {
        const now = new Date();
        const timeElement = this.element.querySelector('#clockTime');
        const dateElement = this.element.querySelector('#clockDate');
        const dayElement = this.element.querySelector('#clockDay');
        
        if (timeElement) {
            timeElement.textContent = now.toLocaleTimeString([], { 
                hour: '2-digit', 
                minute: '2-digit', 
                hour12: false 
            });
        }
        
        if (dateElement) {
            dateElement.textContent = now.toLocaleDateString([], { 
                day: 'numeric', 
                month: 'long',
                year: 'numeric'
            });
        }
        
        if (dayElement) {
            dayElement.textContent = now.toLocaleDateString([], { 
                weekday: 'long'
            });
        }
    }
}

// Expose to global scope for WidgetManager
window.TimeWidget = TimeWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('time', new TimeWidget());
}




