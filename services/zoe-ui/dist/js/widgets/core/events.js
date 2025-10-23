/**
 * Events Widget
 * Displays today's calendar events
 * Version: 1.0.0
 */

class EventsWidget extends WidgetModule {
    constructor() {
        super('events', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: 30000 // Update every 30 seconds
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📅 Today's Events</div>
                <div class="widget-badge" id="eventsCount">0</div>
            </div>
            <div class="widget-content">
                <div id="eventsContent" class="loading-widget">
                    <div class="spinner"></div>
                    Loading events...
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        // Load events immediately after initialization
        this.loadEvents();
    }
    
    update() {
        this.loadEvents();
    }
    
    async loadEvents() {
        try {
            const response = await fetch('/api/calendar/events');
            if (response.ok) {
                const data = await response.json();
                const events = Array.isArray(data) ? data : data.events || [];
                
                const today = new Date().toISOString().split('T')[0];
                const todaysEvents = events.filter(event => {
                    const eventDate = event.start_date || event.date;
                    return eventDate === today;
                });
                
                // Transform events to match expected format
                const transformedEvents = todaysEvents.map(event => ({
                    title: event.title,
                    start_time: event.start_time,
                    category: event.category || 'personal'
                }));
                
                this.updateEvents(transformedEvents);
            } else {
                this.updateEvents([]);
            }
        } catch (error) {
            console.error('Failed to load events:', error);
            this.updateEvents([]);
        }
    }
    
    updateEvents(events) {
        const content = this.element.querySelector('#eventsContent');
        const count = this.element.querySelector('#eventsCount');
        
        if (count) {
            count.textContent = events.length;
        }
        
        if (content) {
            // Remove loading widget class
            content.classList.remove('loading-widget');
            
            if (events.length === 0) {
                content.innerHTML = '<div style="text-align: center; color: #666; font-style: italic;">No events today</div>';
                return;
            }
            
            content.innerHTML = events.map(event => `
                <div class="calendar-event-item ${event.category || 'personal'}" style="padding: 12px; border-radius: 8px; margin-bottom: 8px; cursor: pointer; transition: all 0.3s; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1); border-left: 4px solid;">
                    <div style="font-size: 12px; font-weight: 600; margin-bottom: 4px;">${this.formatTime(event.start_time || event.time)}</div>
                    <div style="font-size: 14px; font-weight: 500;">${event.title}</div>
                </div>
            `).join('');
        }
    }
    
    formatTime(timeStr) {
        if (!timeStr) return 'All Day';
        const [hours, minutes] = timeStr.split(':');
        const hour12 = hours % 12 || 12;
        const ampm = hours >= 12 ? 'PM' : 'AM';
        return `${hour12}:${minutes} ${ampm}`;
    }
}

// Expose to global scope for WidgetManager
window.EventsWidget = EventsWidget;

// Register widget (legacy)
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('events', new EventsWidget());
}




