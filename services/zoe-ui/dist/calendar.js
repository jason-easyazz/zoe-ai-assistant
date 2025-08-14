// Enhanced Calendar with Live Event Data Integration
// Phase 3D-A: Real-time sync with Zoe API
// Designed for Raspberry Pi 5 performance optimization

class LiveCalendar {
    constructor() {
        this.currentDate = new Date();
        this.events = [];
        this.isLoading = false;
        this.lastUpdateTime = null;
        this.eventCache = new Map();
        
        console.log('🚀 Initializing Live Calendar v3.1...');
        this.init();
    }
    
    async init() {
        try {
            await this.loadEvents();
            this.renderCalendar();
            this.setupEventListeners();
            this.startEventPolling();
            this.updateMonthDisplay();
            console.log('✅ Live Calendar initialized successfully');
        } catch (error) {
            console.error('❌ Failed to initialize calendar:', error);
            this.showErrorMessage('Failed to initialize calendar');
        }
    }
    
    async loadEvents() {
        this.isLoading = true;
        this.showLoadingIndicator();
        
        try {
            console.log('📡 Fetching events from Zoe API...');
            const response = await fetch('/api/events');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            if (data.events && Array.isArray(data.events)) {
                this.events = data.events;
                this.lastUpdateTime = new Date();
                console.log(`✅ Loaded ${this.events.length} events from API`);
                
                // Update cache
                this.events.forEach(event => {
                    this.eventCache.set(event.id, event);
                });
                
                this.hideLoadingIndicator();
                this.updateLastRefreshDisplay();
                return true;
            } else {
                console.warn('⚠️ Invalid events data received:', data);
                this.events = [];
                return false;
            }
        } catch (error) {
            console.error('❌ Failed to load events:', error);
            this.showErrorMessage(`Failed to load events: ${error.message}`);
            
            // Use cached events as fallback
            if (this.eventCache.size > 0) {
                this.events = Array.from(this.eventCache.values());
                console.log('📋 Using cached events as fallback');
            } else {
                this.loadSampleEvents();
            }
            return false;
        } finally {
            this.isLoading = false;
            this.hideLoadingIndicator();
        }
    }
    
    loadSampleEvents() {
        const today = new Date();
        const tomorrow = new Date(today);
        tomorrow.setDate(today.getDate() + 1);
        
        this.events = [
            {
                id: 'sample-1',
                title: "Calendar Integration Test",
                date: today.toISOString().split('T')[0],
                time: "14:00",
                category: "work",
                priority: "high",
                description: "Testing live calendar integration with Zoe API"
            },
            {
                id: 'sample-2',
                title: "Sample Event",
                date: tomorrow.toISOString().split('T')[0],
                time: "10:30",
                category: "personal",
                priority: "medium",
                description: "Sample event for UI testing"
            }
        ];
        console.log('📝 Loaded sample events as API fallback');
    }
    
    renderCalendar() {
        console.log('🎨 Rendering calendar with live data...');
        
        const calendarGrid = document.getElementById('calendarGrid');
        if (!calendarGrid) {
            console.error('❌ Calendar grid element not found');
            return;
        }
        
        // Preserve headers and clear calendar days
        const existingCells = calendarGrid.querySelectorAll('.calendar-day');
        existingCells.forEach(cell => cell.remove());
        
        // Get calendar data for current month
        const year = this.currentDate.getFullYear();
        const month = this.currentDate.getMonth();
        const firstDay = new Date(year, month, 1);
        const lastDay = new Date(year, month + 1, 0);
        const startDate = new Date(firstDay);
        
        // Start from Sunday of the week containing the first day
        startDate.setDate(startDate.getDate() - firstDay.getDay());
        
        // Render 6 weeks (42 days) to fill calendar grid
        for (let i = 0; i < 42; i++) {
            const cellDate = new Date(startDate);
            cellDate.setDate(startDate.getDate() + i);
            
            const dayElement = this.createDayElement(cellDate, month);
            calendarGrid.appendChild(dayElement);
        }
        
        console.log('✅ Calendar grid rendered');
        this.renderUpcomingEvents();
    }
    
    createDayElement(date, currentMonth) {
        const dayElement = document.createElement('div');
        dayElement.className = 'calendar-day';
        
        // Style for days outside current month
        if (date.getMonth() !== currentMonth) {
            dayElement.classList.add('other-month');
        }
        
        // Highlight today
        if (this.isToday(date)) {
            dayElement.classList.add('today');
        }
        
        // Add weekend styling
        if (date.getDay() === 0 || date.getDay() === 6) {
            dayElement.classList.add('weekend');
        }
        
        // Day number
        const dayNumber = document.createElement('div');
        dayNumber.className = 'day-number';
        dayNumber.textContent = date.getDate();
        dayElement.appendChild(dayNumber);
        
        // Events for this day
        const dayEvents = this.getEventsForDate(date);
        if (dayEvents.length > 0) {
            dayElement.classList.add('has-events');
            
            // Show up to 3 events, then "X more"
            const maxVisible = 3;
            dayEvents.slice(0, maxVisible).forEach(event => {
                const eventElement = this.createEventBadge(event);
                dayElement.appendChild(eventElement);
            });
            
            if (dayEvents.length > maxVisible) {
                const moreElement = document.createElement('div');
                moreElement.className = 'more-events';
                moreElement.textContent = `+${dayEvents.length - maxVisible} more`;
                moreElement.onclick = (e) => {
                    e.stopPropagation();
                    this.showDayEvents(date, dayEvents);
                };
                dayElement.appendChild(moreElement);
            }
        }
        
        // Click handler for day
        dayElement.onclick = () => this.selectDate(date);
        
        return dayElement;
    }
    
    createEventBadge(event) {
        const eventElement = document.createElement('div');
        eventElement.className = `calendar-event priority-${event.priority} category-${event.category}`;
        
        // Truncate long titles
        const title = event.title.length > 15 ? event.title.substring(0, 12) + '...' : event.title;
        eventElement.textContent = title;
        
        // Tooltip with full details
        eventElement.title = this.formatEventTooltip(event);
        
        // Click handler
        eventElement.onclick = (e) => {
            e.stopPropagation();
            this.showEventDetails(event);
        };
        
        return eventElement;
    }
    
    formatEventTooltip(event) {
        const time = event.time ? this.formatTime(event.time) : 'No time set';
        const description = event.description ? `\n${event.description}` : '';
        return `${event.title}\n${time}\nCategory: ${event.category}\nPriority: ${event.priority}${description}`;
    }
    
    getEventsForDate(date) {
        const dateStr = date.toISOString().split('T')[0];
        return this.events.filter(event => event.date === dateStr)
                          .sort((a, b) => {
                              // Sort by time, then priority
                              if (a.time && b.time) {
                                  return a.time.localeCompare(b.time);
                              }
                              const priorityOrder = { high: 3, medium: 2, low: 1 };
                              return (priorityOrder[b.priority] || 2) - (priorityOrder[a.priority] || 2);
                          });
    }
    
    isToday(date) {
        const today = new Date();
        return date.toDateString() === today.toDateString();
    }
    
    renderUpcomingEvents() {
        const eventsList = document.getElementById('eventsList');
        if (!eventsList) {
            console.warn('⚠️ Events list element not found');
            return;
        }
        
        // Get upcoming events (next 30 days)
        const today = new Date();
        const futureDate = new Date();
        futureDate.setDate(today.getDate() + 30);
        
        const upcomingEvents = this.events
            .filter(event => {
                const eventDate = new Date(event.date);
                return eventDate >= today && eventDate <= futureDate;
            })
            .sort((a, b) => {
                const dateComparison = new Date(a.date) - new Date(b.date);
                if (dateComparison !== 0) return dateComparison;
                
                // If same date, sort by time
                if (a.time && b.time) {
                    return a.time.localeCompare(b.time);
                }
                return a.time ? -1 : (b.time ? 1 : 0);
            })
            .slice(0, 10); // Show max 10 upcoming events
        
        if (upcomingEvents.length === 0) {
            eventsList.innerHTML = this.createNoEventsHTML();
            return;
        }
        
        eventsList.innerHTML = '';
        upcomingEvents.forEach(event => {
            const eventCard = this.createEventCard(event);
            eventsList.appendChild(eventCard);
        });
        
        console.log(`✅ Rendered ${upcomingEvents.length} upcoming events`);
    }
    
    createNoEventsHTML() {
        return `
            <div class="no-events">
                <div class="no-events-icon">📅</div>
                <div class="no-events-text">No upcoming events</div>
                <div class="no-events-subtitle">
                    Create events by chatting with Zoe!<br>
                    Try: "Meeting tomorrow at 2pm"
                </div>
            </div>
        `;
    }
    
    createEventCard(event) {
        const eventCard = document.createElement('div');
        eventCard.className = `event-card priority-${event.priority}`;
        eventCard.onclick = () => this.showEventDetails(event);
        
        const eventDate = new Date(event.date);
        const dateStr = this.formatEventDate(eventDate);
        const timeStr = event.time ? this.formatTime(event.time) : 'No time set';
        
        eventCard.innerHTML = `
            <div class="event-header">
                <div class="event-main">
                    <div class="event-title">${this.escapeHtml(event.title)}</div>
                    <div class="event-date">${dateStr} • ${timeStr}</div>
                </div>
                <div class="event-meta">
                    <div class="event-category-badge category-${event.category}">
                        ${event.category}
                    </div>
                    <div class="event-priority-indicator priority-${event.priority}">
                        ${event.priority}
                    </div>
                </div>
            </div>
            ${event.description ? `<div class="event-description">${this.escapeHtml(event.description)}</div>` : ''}
        `;
        
        return eventCard;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    formatEventDate(date) {
        const today = new Date();
        const tomorrow = new Date(today);
        tomorrow.setDate(today.getDate() + 1);
        const yesterday = new Date(today);
        yesterday.setDate(today.getDate() - 1);
        
        if (date.toDateString() === today.toDateString()) {
            return 'Today';
        } else if (date.toDateString() === tomorrow.toDateString()) {
            return 'Tomorrow';
        } else if (date.toDateString() === yesterday.toDateString()) {
            return 'Yesterday';
        } else {
            return date.toLocaleDateString('en-US', { 
                weekday: 'short', 
                month: 'short', 
                day: 'numeric' 
            });
        }
    }
    
    formatTime(timeStr) {
        if (!timeStr) return '';
        
        try {
            const [hours, minutes] = timeStr.split(':');
            const hour = parseInt(hours);
            const min = minutes || '00';
            
            if (hour === 0) return `12:${min} AM`;
            else if (hour < 12) return `${hour}:${min} AM`;
            else if (hour === 12) return `12:${min} PM`;
            else return `${hour - 12}:${min} PM`;
        } catch (error) {
            console.warn('⚠️ Invalid time format:', timeStr);
            return timeStr;
        }
    }
    
    showEventDetails(event) {
        const timeStr = event.time ? this.formatTime(event.time) : 'No time set';
        const locationStr = event.location ? `\nLocation: ${event.location}` : '';
        
        const details = `Event: ${event.title}\nDate: ${event.date}\nTime: ${timeStr}\nCategory: ${event.category}\nPriority: ${event.priority}${locationStr}\n\n${event.description || 'No description available'}`;
        
        alert(details);
        // TODO: Replace with modal dialog in future enhancement
    }
    
    showDayEvents(date, events) {
        const dateStr = date.toLocaleDateString('en-US', { 
            weekday: 'long', 
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
        
        let eventsList = events.map(event => {
            const time = event.time ? this.formatTime(event.time) : 'No time';
            return `• ${time} - ${event.title}`;
        }).join('\n');
        
        alert(`Events for ${dateStr}:\n\n${eventsList}`);
        // TODO: Replace with modal dialog in future enhancement
    }
    
    selectDate(date) {
        // Future: Open day view or event creation for this date
        console.log('📅 Selected date:', date.toISOString().split('T')[0]);
    }
    
    setupEventListeners() {
        console.log('🔧 Setting up event listeners...');
        
        // Month navigation
        const prevBtn = document.getElementById('prevMonth');
        const nextBtn = document.getElementById('nextMonth');
        
        if (prevBtn) {
            prevBtn.onclick = () => this.navigateMonth(-1);
        }
        
        if (nextBtn) {
            nextBtn.onclick = () => this.navigateMonth(1);
        }
        
        // Refresh button
        const refreshBtn = document.getElementById('refreshEvents');
        if (refreshBtn) {
            refreshBtn.onclick = () => this.refreshEvents();
        }
        
        // Today button
        const todayBtn = document.getElementById('todayBtn');
        if (todayBtn) {
            todayBtn.onclick = () => this.goToToday();
        }
    }
    
    navigateMonth(direction) {
        this.currentDate.setMonth(this.currentDate.getMonth() + direction);
        this.updateMonthDisplay();
        this.renderCalendar();
        console.log(`📅 Navigated to ${this.currentDate.toLocaleDateString('en-US', {year: 'numeric', month: 'long'})}`);
    }
    
    goToToday() {
        this.currentDate = new Date();
        this.updateMonthDisplay();
        this.renderCalendar();
        console.log('📅 Navigated to current month');
    }
    
    updateMonthDisplay() {
        const monthDisplay = document.getElementById('currentMonth');
        if (monthDisplay) {
            monthDisplay.textContent = this.currentDate.toLocaleDateString('en-US', {
                year: 'numeric',
                month: 'long'
            });
        }
    }
    
    async refreshEvents() {
        console.log('🔄 Manually refreshing events...');
        const success = await this.loadEvents();
        if (success) {
            this.renderCalendar();
            this.showSuccessMessage('Events refreshed successfully!');
        } else {
            this.showErrorMessage('Failed to refresh events');
        }
    }
    
    startEventPolling() {
        // Poll for new events every 30 seconds
        const pollInterval = 30000; // 30 seconds
        
        setInterval(async () => {
            if (!this.isLoading && document.visibilityState === 'visible') {
                console.log('🔄 Auto-polling for event updates...');
                await this.loadEvents();
                this.renderCalendar();
            }
        }, pollInterval);
        
        console.log(`✅ Event polling started (${pollInterval/1000}s interval)`);
        
        // Also poll when page becomes visible
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && !this.isLoading) {
                setTimeout(() => this.loadEvents().then(() => this.renderCalendar()), 1000);
            }
        });
    }
    
    updateLastRefreshDisplay() {
        const refreshDisplay = document.getElementById('lastRefresh');
        if (refreshDisplay && this.lastUpdateTime) {
            const timeStr = this.lastUpdateTime.toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
            refreshDisplay.textContent = `Last updated: ${timeStr}`;
        }
    }
    
    showLoadingIndicator() {
        const indicator = document.getElementById('loadingIndicator');
        if (indicator) {
            indicator.style.display = 'block';
        }
        
        const refreshBtn = document.getElementById('refreshEvents');
        if (refreshBtn) {
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Loading...';
        }
    }
    
    hideLoadingIndicator() {
        const indicator = document.getElementById('loadingIndicator');
        if (indicator) {
            indicator.style.display = 'none';
        }
        
        const refreshBtn = document.getElementById('refreshEvents');
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<span>🔄</span><span>Refresh</span>';
        }
    }
    
    showErrorMessage(message) {
        console.error('📢 Error:', message);
        
        // Show error in UI if element exists
        const errorDiv = document.getElementById('errorMessage');
        if (errorDiv) {
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            setTimeout(() => {
                errorDiv.style.display = 'none';
            }, 5000);
        }
    }
    
    showSuccessMessage(message) {
        console.log('📢 Success:', message);
        
        // Show success in UI if element exists
        const successDiv = document.getElementById('successMessage');
        if (successDiv) {
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            setTimeout(() => {
                successDiv.style.display = 'none';
            }, 3000);
        }
    }
}

// Initialize calendar when page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('🌟 Starting Live Calendar System initialization...');
    
    // Ensure DOM is ready
    setTimeout(() => {
        try {
            window.liveCalendar = new LiveCalendar();
        } catch (error) {
            console.error('❌ Failed to initialize Live Calendar:', error);
        }
    }, 500);
});

// Export for global access
window.LiveCalendar = LiveCalendar;

// Export functions for HTML integration
window.calendarFunctions = {
    refreshEvents: () => window.liveCalendar?.refreshEvents(),
    goToToday: () => window.liveCalendar?.goToToday(),
    navigateMonth: (direction) => window.liveCalendar?.navigateMonth(direction)
};

console.log('📦 Enhanced Calendar JavaScript loaded successfully');
