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
        this.userId = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📅 Today's Events</div>
                <div class="widget-badge" id="eventsCount">0</div>
            </div>
            <div class="widget-content events-widget-content">
                <div id="eventsContent" class="loading-widget">
                    <div class="spinner"></div>
                    Loading events...
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        // Ensure widget has proper height for flex layout
        element.style.height = '100%';
        element.style.display = 'flex';
        element.style.flexDirection = 'column';
        
        // Get user session
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        // Load events immediately after initialization
        this.loadEvents();
    }
    
    update() {
        this.loadEvents();
    }
    
    async loadEvents() {
        try {
            const response = await fetch(`/api/calendar/events?user_id=${this.userId}`);
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
            
            // Ensure content fills available space
            content.style.flex = '1';
            content.style.overflow = 'auto';
            content.style.minHeight = '0';
            
            if (events.length === 0) {
                content.innerHTML = '<div style="text-align: center; color: #666; font-style: italic; padding: 20px;">No events today</div>';
                return;
            }
            
            // Create events list with proper spacing
            const eventsHTML = events.map(event => `
                <div class="calendar-event-item ${event.category || 'personal'}" style="padding: 12px; border-radius: 8px; cursor: pointer; transition: all 0.3s; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1); border-left: 4px solid; width: 100%; box-sizing: border-box; margin-bottom: 8px;">
                    <div style="font-size: 12px; font-weight: 600; margin-bottom: 4px;">${this.formatTime(event.start_time || event.time)}</div>
                    <div style="font-size: 14px; font-weight: 500;">${event.title}</div>
                </div>
            `).join('');
            
            content.innerHTML = eventsHTML;
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




