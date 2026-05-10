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
                <div class="widget-title">📅 Upcoming Events</div>
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
        // Re-fetch user ID in case session loaded after init
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        this.loadEvents();
    }
    
    async loadEvents() {
        try {
            const response = await fetch(`/api/calendar/events?user_id=${this.userId}`);
            if (response.ok) {
                const data = await response.json();
                const events = Array.isArray(data) ? data : data.events || [];

                // Use local date to avoid UTC timezone mismatch (e.g. UTC+8 would show yesterday's date if using toISOString)
                const now = new Date();
                const todayLocal = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
                const nowTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

                // Show today's remaining events and all future events
                const upcomingEvents = events.filter(event => {
                    const eventDate = event.start_date || event.date || '';
                    if (!eventDate) return false;
                    if (eventDate > todayLocal) return true;
                    if (eventDate === todayLocal) {
                        // For today, skip events that have already ended
                        const endTime = event.end_time || event.start_time || event.time || '23:59';
                        return endTime >= nowTime;
                    }
                    return false;
                });

                // Sort chronologically by date then time
                upcomingEvents.sort((a, b) => {
                    const da = (a.start_date || a.date || '') + ' ' + (a.start_time || a.time || '00:00');
                    const db = (b.start_date || b.date || '') + ' ' + (b.start_time || b.time || '00:00');
                    return da.localeCompare(db);
                });

                const transformedEvents = upcomingEvents.map(event => ({
                    title: event.title,
                    start_time: event.start_time || event.time,
                    date: event.start_date || event.date,
                    category: event.category || 'personal'
                }));

                this.updateEvents(transformedEvents, todayLocal);
            } else {
                this.updateEvents([], '');
            }
        } catch (error) {
            console.error('Failed to load events:', error);
            this.updateEvents([], '');
        }
    }
    
    updateEvents(events, todayLocal) {
        const content = this.element.querySelector('#eventsContent');
        const count = this.element.querySelector('#eventsCount');
        const isDark = document.documentElement.classList.contains('dark-mode');
        const textPrimary = isDark ? 'rgba(255,255,255,0.92)' : '#1f2937';
        const textSecondary = isDark ? 'rgba(255,255,255,0.68)' : '#666';
        const cardBg = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.92)';
        const cardBorder = isDark ? 'rgba(255,255,255,0.10)' : 'rgba(0, 0, 0, 0.08)';

        if (count) {
            count.textContent = events.length;
        }

        if (content) {
            content.classList.remove('loading-widget');
            content.style.flex = '1';
            content.style.overflow = 'auto';
            content.style.minHeight = '0';

            if (events.length === 0) {
                content.innerHTML = `<div style="text-align: center; color: ${textSecondary}; font-style: italic; padding: 20px;">No upcoming events</div>`;
                return;
            }

            const eventsHTML = events.map(event => {
                const isToday = event.date === todayLocal;
                const dateLabel = isToday ? '' : `<div style="font-size: 11px; font-weight: 500; color: ${textSecondary}; margin-bottom: 2px;">${this.formatDate(event.date)}</div>`;
                return `
                <div class="calendar-event-item ${event.category || 'personal'}" style="padding: 12px; border-radius: 8px; cursor: pointer; transition: all 0.3s; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.10); border: 1px solid ${cardBorder}; background: ${cardBg}; border-left: 4px solid; width: 100%; box-sizing: border-box; margin-bottom: 8px; color: ${textPrimary};">
                    ${dateLabel}
                    <div style="font-size: 12px; font-weight: 600; margin-bottom: 4px; color: ${textSecondary};">${this.formatTime(event.start_time || event.time)}</div>
                    <div style="font-size: 14px; font-weight: 500; color: ${textPrimary};">${event.title}</div>
                </div>`;
            }).join('');

            content.innerHTML = eventsHTML;
        }
    }

    formatDate(dateStr) {
        if (!dateStr) return '';
        const [year, month, day] = dateStr.split('-');
        const d = new Date(+year, +month - 1, +day);
        return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
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




