/**
 * Week Planner Widget
 * Shows calendar events, list items, and transactions for the week
 * Integrates with Week Planner Transactions System
 * Version: 1.0.0
 */

class WeekPlannerWidget extends WidgetModule {
    constructor() {
        super('week-planner', {
            version: '1.0.0',
            defaultSize: 'size-large',
            updateInterval: 300000 // Update every 5 minutes
        });
        this.currentWeekStart = null;
    }
    
    getTemplate() {
        return `
            <style>
                .week-planner-content {
                    --primary-purple: #7B61FF;
                    --primary-teal: #5AE0E0;
                    --primary-gradient: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    --glass-bg: rgba(255, 255, 255, 0.6);
                    --glass-bg-light: rgba(255, 255, 255, 0.4);
                    --glass-border: rgba(255, 255, 255, 0.4);
                    --glass-border-light: rgba(255, 255, 255, 0.2);
                    width: 100%;
                    height: 100%;
                    display: flex;
                    flex-direction: column;
                    overflow-y: auto;
                }
                
                .week-nav {
                    display: flex;
                    gap: 4px;
                }
                
                .nav-btn {
                    width: 32px;
                    height: 32px;
                    background: var(--glass-bg-light);
                    border: 1px solid var(--glass-border-light);
                    border-radius: 8px;
                    color: var(--primary-purple);
                    font-size: 1.2rem;
                    cursor: pointer;
                    transition: all 0.3s;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .nav-btn:hover {
                    background: var(--glass-bg);
                    transform: scale(1.05);
                    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
                }
                
                .week-totals {
                    display: grid;
                    grid-template-columns: repeat(2, 1fr);
                    gap: 6px;
                    margin-bottom: 12px;
                    font-size: 0.75rem;
                }
                
                .total-badge {
                    padding: 4px 8px;
                    border-radius: 6px;
                    font-weight: 600;
                    text-align: center;
                    font-size: 0.7rem;
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                }
                
                .total-badge-label {
                    font-size: 0.65rem;
                    opacity: 0.8;
                }
                
                .total-badge-amount {
                    font-size: 0.85rem;
                    font-weight: 700;
                }
                
                .total-income { background: rgba(16, 185, 129, 0.15); color: #059669; }
                .total-received { background: rgba(5, 150, 105, 0.2); color: #047857; }
                .total-expense { background: rgba(239, 68, 68, 0.15); color: #dc2626; }
                .total-paid { background: rgba(220, 38, 38, 0.2); color: #b91c1c; }
                
                .days-container {
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                    flex: 1;
                    overflow-y: auto;
                }
                
                .day-column {
                    background: var(--glass-bg-light);
                    backdrop-filter: blur(20px);
                    border: 1px solid var(--glass-border-light);
                    border-radius: 12px;
                    padding: 12px;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
                    transition: all 0.3s ease;
                }
                
                .day-column:hover {
                    background: var(--glass-bg);
                    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
                    transform: translateY(-2px);
                }
                
                .day-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 8px;
                    padding-bottom: 8px;
                    border-bottom: 1px solid var(--glass-border-light);
                }
                
                .day-header h4 {
                    font-weight: 700;
                    font-size: 0.9rem;
                    text-transform: uppercase;
                    color: #333;
                }
                
                .day-date {
                    width: 32px;
                    height: 32px;
                    border-radius: 50%;
                    background: var(--primary-gradient);
                    color: white;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-weight: 700;
                    font-size: 0.9rem;
                }
                
                .day-events {
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }
                
                .event-item {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px;
                    background: rgba(255, 255, 255, 0.8);
                    border: 1px solid var(--glass-border-light);
                    border-radius: 8px;
                    font-size: 0.85rem;
                    transition: all 0.3s;
                }
                
                .event-item:hover {
                    background: white;
                    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
                    transform: translateX(4px);
                }
                
                .event-icon {
                    font-size: 1.2rem;
                    min-width: 24px;
                }
                
                .event-content {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                }
                
                .event-time {
                    font-size: 0.75rem;
                    color: #666;
                }
                
                .event-budget {
                    display: inline-flex;
                    align-items: center;
                    gap: 4px;
                    padding: 2px 8px;
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 0.8rem;
                }
                
                .budget-income { background: rgba(16, 185, 129, 0.15); color: #059669; }
                .budget-expense { background: rgba(239, 68, 68, 0.15); color: #dc2626; }
                
                .payment-btn {
                    width: 28px;
                    height: 28px;
                    background: var(--glass-bg-light);
                    border: 1px solid var(--glass-border-light);
                    border-radius: 6px;
                    cursor: pointer;
                    transition: all 0.3s;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 0.9rem;
                }
                
                .payment-btn:hover { transform: scale(1.1); }
                .payment-btn.confirmed {
                    background: rgba(16, 185, 129, 0.2);
                    border-color: #059669;
                    color: #059669;
                }
                
                .empty-day {
                    text-align: center;
                    padding: 20px;
                    color: #999;
                    font-style: italic;
                    font-size: 0.85rem;
                }
                
                .nl-input {
                    width: 100%;
                    padding: 8px;
                    background: rgba(255, 255, 255, 0.5);
                    border: 2px dashed var(--glass-border-light);
                    border-radius: 8px;
                    font-family: inherit;
                    font-size: 0.85rem;
                    transition: all 0.3s;
                }
                
                .nl-input:focus {
                    outline: none;
                    border-color: var(--primary-purple);
                    border-style: solid;
                    background: white;
                    box-shadow: 0 0 0 3px rgba(123, 97, 255, 0.1);
                }
            </style>
            <div class="week-planner-content">
                <div class="widget-header">
                    <div class="widget-title">📅 Week Planner</div>
                    <div class="week-nav">
                        <button class="nav-btn prev-week">‹</button>
                        <button class="nav-btn next-week">›</button>
                    </div>
                </div>
                <div class="week-totals"></div>
                <div class="days-container"></div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        this.currentWeekStart = this.getMondayDate(new Date());
        
        // Load week data
        this.loadWeekData();
        
        // Bind events
        this.bindEvents();
    }
    
    getMondayDate(date) {
        const d = new Date(date);
        const day = d.getDay();
        const diff = d.getDate() - day + (day === 0 ? -6 : 1); // Adjust when day is Sunday
        return new Date(d.setDate(diff));
    }
    
    async loadWeekData() {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const sessionId = session?.session_id || '';
            
            console.log('Loading week data with session:', sessionId);
            
            const dateStr = this.currentWeekStart.toISOString().split('T')[0];
            const url = `/api/calendar/week?start_date=${dateStr}`;
            console.log('Fetching:', url);
            
            const response = await fetch(url, {
                headers: sessionId ? { 'X-Session-ID': sessionId } : {}
            });
            
            if (!response.ok) {
                console.error('Failed to load week data:', response.status);
                const text = await response.text();
                console.error('Response:', text);
                return;
            }
            
            const data = await response.json();
            console.log('Week data received:', data);
            
            // Add pending events back
            if (this.pendingEvents && this.pendingEvents.length > 0) {
                this.pendingEvents.forEach(pending => {
                    this.addOptimisticEvent(pending.title, '', pending.time, pending.day);
                });
            }
            
            this.renderWeek(data);
        } catch (error) {
            console.error('Error loading week data:', error);
        }
    }
    
    renderWeek(data) {
        const titleEl = this.element.querySelector('.widget-title');
        const start = new Date(data.week_start);
        const end = new Date(data.week_end);
        if (titleEl) {
            titleEl.textContent = `📅 Week ${this.formatDate(start)} - ${this.formatDate(end)}`;
        }
        
        // Render totals
        this.renderTotals(data.totals);
        
        // Render days
        this.renderDays(data.days);
    }
    
    renderTotals(totals) {
        const totalsEl = this.element.querySelector('.week-totals');
        totalsEl.innerHTML = `
            <div class="total-badge total-income">
                <span class="total-badge-label">💰 Expected</span>
                <span class="total-badge-amount">$${totals.income_expected.toFixed(2)}</span>
            </div>
            <div class="total-badge total-received">
                <span class="total-badge-label">✅ Received</span>
                <span class="total-badge-amount">$${totals.income_received.toFixed(2)}</span>
            </div>
            <div class="total-badge total-expense">
                <span class="total-badge-label">💳 Due</span>
                <span class="total-badge-amount">$${totals.expense_due.toFixed(2)}</span>
            </div>
            <div class="total-badge total-paid">
                <span class="total-badge-label">✓ Paid</span>
                <span class="total-badge-amount">$${totals.expense_paid.toFixed(2)}</span>
            </div>
        `;
    }
    
    renderDays(days) {
        const containerEl = this.element.querySelector('.days-container');
        const dayNames = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
        
        containerEl.innerHTML = dayNames.map(dayName => {
            const dayData = days[dayName] || [];
            const dayNumber = this.getDayNumber(dayName);
            const displayName = dayName.substring(0, 3).toUpperCase(); // MON, TUE, etc.
            
            return `
                <div class="day-column" data-day="${dayName}">
                    <div class="day-header">
                        <h4>${displayName}</h4>
                        <span class="day-date">${this.getDayDateString(dayNumber)}</span>
                    </div>
                    <div class="day-events">
                        ${dayData.length > 0 
                            ? dayData.map(item => this.renderItem(item)).join('') 
                            : '<div class="empty-day">No items</div>'}
                    </div>
                    <input type="text" class="day-nl-input" placeholder="Add item..." data-day="${dayName}" style="width: 100%; padding: 6px; margin-top: 8px; border: 1px dashed var(--glass-border-light); border-radius: 6px; font-size: 0.8rem;" />
                </div>
            `;
        }).join('');
        
        // Bind item events
        this.bindItemEvents();
        
        // Bind per-day input events
        this.element.querySelectorAll('.day-nl-input').forEach(input => {
            input.addEventListener('keypress', async (e) => {
                if (e.key === 'Enter') {
                    const text = e.target.value.trim();
                    const day = e.target.getAttribute('data-day');
                    if (text) {
                        await this.processDayNLInput(text, day);
                        e.target.value = '';
                    }
                }
            });
        });
    }
    
    async processDayNLInput(text, day) {
        try {
            console.log('Processing NL input:', text, 'for day:', day);
            
            // Parse the input to determine what to create
            const parsed = this.parseNLInput(text);
            console.log('Parsed:', parsed);
            
            // Get the date for this day
            const dayNumber = this.getDayNumber(day);
            const targetDate = new Date(this.currentWeekStart);
            targetDate.setDate(this.currentWeekStart.getDate() + dayNumber);
            const dateStr = targetDate.toISOString().split('T')[0];
            
            // Parse time if present - handle "2pm", "10:30am", "3:00pm", etc.
            const timeMatch = text.match(/\b(\d{1,2})[:\.]?(\d{0,2})\s*(am|pm)\b/i);
            let timeStr = null;
            if (timeMatch) {
                let hours = parseInt(timeMatch[1]);
                const minutes = timeMatch[2] || '00';
                const period = timeMatch[3]?.toLowerCase();
                
                if (period === 'pm' && hours !== 12) hours += 12;
                if (period === 'am' && hours === 12) hours = 0;
                
                timeStr = `${hours.toString().padStart(2, '0')}:${minutes.padStart(2, '0')}`;
            }
            
            console.log('Date:', dateStr, 'Time:', timeStr);
            
            // For now, just create calendar events (simplest approach)
            // The time parsing is working now with the updated regex
            if (parsed.hasTime) {
                console.log('Creating calendar event');
                const result = await this.createEvent(text, dateStr, timeStr);
                
                // Show immediate feedback
                if (result) {
                    // Optimistic update - add to the UI immediately
                    this.addOptimisticEvent(text, dateStr, timeStr, day);
                    
                    // Store it as pending so reload doesn't wipe it
                    this.pendingEvents = this.pendingEvents || [];
                    this.pendingEvents.push({day, title: text, time: timeStr});
                }
            } else {
                // No time specified, create as basic list item reminder (just show it)
                console.log('No time specified, creating reminder text');
                // For now, just create a simple text note that shows in the widget
                await this.createNote(text, dateStr);
            }
            
            // Reload week data after a longer delay to let server process
            setTimeout(() => this.loadWeekData(), 2000);
        } catch (error) {
            console.error('Error processing day NL input:', error);
        }
    }
    
    parseNLInput(text) {
        const hasAmount = /\$(\d+)/.exec(text);
        // Updated regex to match times like "2pm", "10:30am", "3:00pm", etc.
        const hasTime = /\b(\d{1,2})[:\.]?(\d{0,2})\s*(am|pm)\b/i.test(text);
        const isExpense = /\b(owe|debt|pay|spent|cost|bill)\b/i.test(text);
        
        return {
            hasAmount: !!hasAmount,
            amount: hasAmount ? parseFloat(hasAmount[1]) : null,
            hasTime: hasTime,
            isExpense: isExpense
        };
    }
    
    async createEvent(title, date, time) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const sessionId = session?.session_id || '';
            
            const response = await fetch('/api/calendar/events', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(sessionId ? { 'X-Session-ID': sessionId } : {})
                },
                body: JSON.stringify({
                    title: title,
                    start_date: date,
                    start_time: time,
                    category: 'personal'
                })
            });
            
            if (!response.ok) {
                console.error('Failed to create event:', await response.text());
                return false;
            }
            
            return true;
        } catch (error) {
            console.error('Error creating event:', error);
            return false;
        }
    }
    
    addOptimisticEvent(title, date, timeStr, day) {
        // Immediately add the event to the UI before reload
        const dayElement = document.querySelector(`.day-column[data-day="${day}"]`);
        if (!dayElement) return;
        
        // Remove "No items" message if present
        const noItemsMsg = dayElement.querySelector('.empty-day');
        if (noItemsMsg) {
            noItemsMsg.remove();
        }
        
        const eventItem = document.createElement('div');
        eventItem.className = 'event-item';
        eventItem.innerHTML = `
            <div class="event-icon">📅</div>
            <div class="event-content">
                <div class="event-title">${title}</div>
                <div class="event-time">${timeStr}</div>
            </div>
        `;
        
        // Add to the events container
        const eventsContainer = dayElement.querySelector('.day-events');
        if (eventsContainer) {
            eventsContainer.appendChild(eventItem);
            // Add a subtle animation
            eventItem.style.opacity = '0';
            setTimeout(() => {
                eventItem.style.transition = 'opacity 0.3s';
                eventItem.style.opacity = '1';
            }, 10);
        }
    }
    
    async createTransaction(description, date, amount, isExpense) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const sessionId = session?.session_id || '';
            
            const response = await fetch('/api/transactions', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(sessionId ? { 'X-Session-ID': sessionId } : {})
                },
                body: JSON.stringify({
                    description: description,
                    amount: amount,
                    type: isExpense ? 'expense' : 'income',
                    transaction_date: date
                })
            });
            
            if (!response.ok) {
                console.error('Failed to create transaction:', await response.text());
            }
        } catch (error) {
            console.error('Error creating transaction:', error);
        }
    }
    
    async createEventWithTransaction(title, date, time, amount, isExpense) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const sessionId = session?.session_id || '';
            
            // Create event first
            const eventResponse = await fetch('/api/calendar/events', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(sessionId ? { 'X-Session-ID': sessionId } : {})
                },
                body: JSON.stringify({
                    title: title,
                    start_date: date,
                    start_time: time,
                    category: 'personal'
                })
            });
            
            if (!eventResponse.ok) return;
            
            const eventData = await eventResponse.json();
            const eventId = eventData.event?.id;
            
            // Create linked transaction
            const transactionResponse = await fetch('/api/transactions', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(sessionId ? { 'X-Session-ID': sessionId } : {})
                },
                body: JSON.stringify({
                    description: title,
                    amount: amount,
                    type: isExpense ? 'expense' : 'income',
                    transaction_date: date,
                    calendar_event_id: eventId
                })
            });
            
            if (!transactionResponse.ok) {
                console.error('Failed to create transaction:', await transactionResponse.text());
            }
        } catch (error) {
            console.error('Error creating event with transaction:', error);
        }
    }
    
    async createNote(text, date) {
        // For now, notes without times are just displayed temporarily
        // In a real implementation, you'd create them in the journal or as reminders
        console.log('Note created (not persisted):', text, date);
        // We'll just reload to show the effect immediately
    }
    
    renderItem(item) {
        let html = '';
        
        switch(item.type) {
            case 'calendar_event':
                html = `
                    <div class="event-item" data-type="calendar_event" data-id="${item.id}">
                        <span class="drag-handle">☰</span>
                        <span class="event-icon">${item.icon || '📅'}</span>
                        <div class="event-content">
                            <span>${item.title}</span>
                            ${item.time ? `<span class="event-time">${item.time}</span>` : ''}
                        </div>
                        ${item.transaction ? `
                            <div class="event-budget ${item.transaction.type === 'income' ? 'budget-income' : 'budget-expense'}">
                                ${item.transaction.type === 'income' ? '💰' : '💳'} $${item.transaction.amount}
                            </div>
                            <button class="payment-btn ${item.transaction.status === 'completed' ? 'confirmed' : ''}" 
                                    data-transaction-id="${item.transaction_id}">
                                ${item.transaction.status === 'completed' ? '✓' : '💰'}
                            </button>
                        ` : ''}
                    </div>
                `;
                break;
                
            case 'transaction':
                html = `
                    <div class="event-item" data-type="transaction" data-id="${item.id}">
                        <span class="event-icon">${item.type === 'income' ? '💰' : '💳'}</span>
                        <div class="event-content">
                            <span>${item.description}</span>
                            ${item.person ? `<span class="person-name">${item.person.name}</span>` : ''}
                        </div>
                        <div class="event-budget ${item.type === 'income' ? 'budget-income' : 'budget-expense'}">
                            $${item.amount}
                        </div>
                        <button class="payment-btn ${item.status === 'completed' ? 'confirmed' : ''}" 
                                data-transaction-id="${item.id}">
                            ${item.status === 'completed' ? '✓' : '💰'}
                        </button>
                    </div>
                `;
                break;
                
            case 'list_item':
                html = `
                    <div class="event-item" data-type="list_item" data-id="${item.id}">
                        <span class="drag-handle">☰</span>
                        <span class="event-icon">${item.icon || '✓'}</span>
                        <div class="event-content">
                            <span>${item.text}</span>
                            <span class="list-name">${item.list_name}</span>
                        </div>
                        ${item.transaction ? `
                            <div class="event-budget ${item.transaction.type === 'income' ? 'budget-income' : 'budget-expense'}">
                                ${item.transaction.type === 'income' ? '💰' : '💳'} $${item.transaction.amount}
                            </div>
                        ` : ''}
                    </div>
                `;
                break;
        }
        
        return html;
    }
    
    getDayNumber(dayName) {
        const dayMap = { monday: 0, tuesday: 1, wednesday: 2, thursday: 3, friday: 4, saturday: 5, sunday: 6 };
        return dayMap[dayName];
    }
    
    getDayDateString(dayNumber) {
        const date = new Date(this.currentWeekStart);
        date.setDate(this.currentWeekStart.getDate() + dayNumber);
        return date.getDate();
    }
    
    formatDate(date) {
        const month = date.toLocaleString('default', { month: 'short' });
        const day = date.getDate();
        return `${month} ${day}`;
    }
    
    bindEvents() {
        // Week navigation
        this.element.querySelector('.prev-week')?.addEventListener('click', () => {
            this.currentWeekStart.setDate(this.currentWeekStart.getDate() - 7);
            this.loadWeekData();
        });
        
        this.element.querySelector('.next-week')?.addEventListener('click', () => {
            this.currentWeekStart.setDate(this.currentWeekStart.getDate() + 7);
            this.loadWeekData();
        });
        
        // Natural language input
        const nlInput = this.element.querySelector('.nl-input');
        nlInput?.addEventListener('keypress', async (e) => {
            if (e.key === 'Enter') {
                const text = e.target.value.trim();
                if (text) {
                    await this.processNLInput(text);
                    e.target.value = '';
                }
            }
        });
    }
    
    bindItemEvents() {
        // Payment button clicks
        this.element.querySelectorAll('.payment-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const transactionId = btn.getAttribute('data-transaction-id');
                if (transactionId) {
                    await this.togglePaymentStatus(transactionId);
                }
            });
        });
    }
    
    async processNLInput(text) {
        try {
            // Send to chat API for parsing
            const response = await fetch('/api/chat/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    user_id: 'default',
                    context: {}
                })
            });
            
            const data = await response.json();
            
            // Reload week data to show new item
            setTimeout(() => this.loadWeekData(), 1000);
        } catch (error) {
            console.error('Error processing NL input:', error);
        }
    }
    
    async togglePaymentStatus(transactionId) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const sessionId = session?.session_id || '';
            
            const response = await fetch(`/api/transactions/${transactionId}/status`, {
                method: 'PATCH',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(sessionId ? { 'X-Session-ID': sessionId } : {})
                }
            });
            
            if (response.ok) {
                // Reload to show updated status
                await this.loadWeekData();
            }
        } catch (error) {
            console.error('Error toggling payment status:', error);
        }
    }
    
    destroy() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
        }
    }
}

// Export for use by widget system
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WeekPlannerWidget;
}

// Make available globally for widget system
window.WeekPlannerWidget = WeekPlannerWidget;

