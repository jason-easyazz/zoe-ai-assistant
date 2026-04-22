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
                    <div id="clockTime" class="time-display">
                        <span class="time-digits">--:--</span>
                    </div>
                    <div id="clockDate" class="date-display">Loading...</div>
                    <div id="clockDay" class="day-display"></div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);

        this.updateWidgetSize(element);
        this.applyPrefs(element);
        this.updateTime();

        const resizeObserver = new ResizeObserver(() => {
            this.updateWidgetSize(element);
        });
        resizeObserver.observe(element);

        this._onSettingsUpdate = (e) => {
            if (!e.detail || e.detail.type !== 'time') return;
            if (e.detail.widget && e.detail.widget !== element) return;
            this.applyPrefs(element);
            this.updateTime();
        };
        window.addEventListener('widget-settings:update', this._onSettingsUpdate);
    }

    getPrefs() {
        try {
            const all = JSON.parse(localStorage.getItem('zoe_widget_settings') || '{}');
            return all.time || {};
        } catch(_) { return {}; }
    }

    applyPrefs(element) {
        const p = this.getPrefs();
        const date = element.querySelector('#clockDate');
        const day  = element.querySelector('#clockDay');
        if (date) date.style.display = (p.showDate === false) ? 'none' : '';
        if (day)  day.style.display  = (p.showDay  === false) ? 'none' : '';
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
        const prefs = this.getPrefs();

        if (timeElement) {
            // Use formatToParts so we can style the AM/PM period independently.
            // This prevents the period from consuming the same font-size as the digits.
            const opts = {
                hour: '2-digit',
                minute: '2-digit',
                hour12: prefs.format24 === false,
            };
            if (prefs.showSeconds) opts.second = '2-digit';

            let digits = '';
            let period = '';
            try {
                const parts = new Intl.DateTimeFormat([], opts).formatToParts(now);
                for (const p of parts) {
                    if (p.type === 'dayPeriod') period = p.value;
                    else if (p.type === 'literal' && p.value === ' ') continue; // swallow space between digits and dayPeriod
                    else digits += p.value;
                }
                digits = digits.trim();
            } catch (_) {
                // Fallback: whole string as digits.
                digits = now.toLocaleTimeString([], opts);
            }

            // Keep seconds visually subordinate to hours+minutes when shown.
            // Re-parse out the seconds portion from the digit string when present.
            let digitsMain = digits;
            let digitsSeconds = '';
            if (prefs.showSeconds) {
                const m = digits.match(/^(\d{1,2}:\d{2})[:.](\d{2})$/);
                if (m) { digitsMain = m[1]; digitsSeconds = m[2]; }
            }

            timeElement.innerHTML =
                `<span class="time-digits">${digitsMain}</span>` +
                (digitsSeconds ? `<span class="time-seconds">${digitsSeconds}</span>` : '') +
                (period ? `<span class="time-period">${period}</span>` : '');
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




