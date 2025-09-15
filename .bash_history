            } else {
                let html = '';
                if (dayEvents.length > 5) {
                    html += `
                        <div class="events-summary">
                            📅 ${dayEvents.length} events scheduled - scroll to see all
                        </div>
                    `;
                }
                
                dayEvents.forEach(event => {
                    const category = event.category || 'personal';
                    const timeStr = event.start_time || event.time;
                    
                    html += `
                        <div class="event-item" onclick="selectEvent(${event.id})">
                            <div class="event-actions">
                                <button class="action-btn edit-btn" onclick="event.stopPropagation(); editEvent(${event.id})" title="Edit">✏️</button>
                                <button class="action-btn delete-btn" onclick="event.stopPropagation(); deleteEvent(${event.id})" title="Delete">×</button>
                            </div>
                            ${timeStr ? `<div class="event-time">${formatTime(timeStr)}</div>` : ''}
                            <div class="event-title">${event.title}</div>
                            <span class="event-category category-${category}">${category}</span>
                        </div>
                    `;
                });
                eventsListEl.innerHTML = html;
            }
            
            document.getElementById('eventCount').textContent = dayEvents.length;
            document.getElementById('freeHours').textContent = Math.max(0, 8 - dayEvents.length);
        }

        function previousMonth() {
            currentDate.setMonth(currentDate.getMonth() - 1);
            updateCalendar();
        }

        function nextMonth() {
            currentDate.setMonth(currentDate.getMonth() + 1);
            updateCalendar();
        }

        function selectEvent(eventId) {
            const event = allEvents.find(e => e.id === eventId);
            if (event) {
                showNotification(`Selected: ${event.title}`);
            }
        }

        function editEvent(eventId) {
            const event = allEvents.find(e => e.id === eventId);
            if (event) {
                const newTitle = prompt('Edit event title:', event.title);
                if (newTitle !== null && newTitle.trim() !== '') {
                    event.title = newTitle.trim();
                    updateCalendar();
                    if (selectedDate) {
                        updateDayView(selectedDate);
                    }
                    showNotification('Event updated');
                }
            }
        }

        function deleteEvent(eventId) {
            if (confirm('Delete this event?')) {
                allEvents = allEvents.filter(e => e.id !== eventId);
                updateCalendar();
                if (selectedDate) {
                    updateDayView(selectedDate);
                }
                showNotification('Event deleted');
            }
        }

        function addEvent() {
            const title = prompt('Event title:');
            if (!title || title.trim() === '') return;
            
            const date = selectedDate || formatDate(new Date());
            const time = prompt('Event time (optional, format: HH:MM):');
            const category = prompt('Category (personal/work/health/social/family):') || 'personal';
            
            const newEvent = {
                id: Date.now(),
                title: title.trim(),
                start_date: date,
                start_time: time || null,
                category: category
            };
            
            allEvents.push(newEvent);
            updateCalendar();
            if (selectedDate) {
                updateDayView(selectedDate);
            }
            showNotification('Event added');
        }

        function navigateToPage(page) {
            window.location.href = page;
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            selectedDate = formatDate(new Date());
            updateCalendar();
            selectDate(selectedDate);
        });
    </script>
</body>
</html>
EOF

cp /home/pi/zoe/services/zoe-ui/dist/dashboard.html /home/pi/zoe/services/zoe-ui/dist/dashboard.html.backup
cat > /home/pi/zoe/services/zoe-ui/dist/dashboard.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Zoe - Dashboard</title>
    <style>
        * { 
            margin: 0; padding: 0; box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            touch-action: manipulation;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            min-height: 100vh; color: #333;
            font-size: clamp(14px, 1.6vw, 16px);
        }
        
        /* Navigation */
        .nav-bar {
            position: fixed; top: 0; left: 0; right: 0; background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px); border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            padding: 10px 15px; z-index: 100; display: flex; justify-content: space-between;
            align-items: center; height: 60px;
        }
        .nav-left { display: flex; align-items: center; gap: 15px; }
        .mini-orb { 
            width: 32px; height: 32px; border-radius: 50%; 
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            cursor: pointer; transition: all 0.3s ease; min-width: 44px; min-height: 44px;
        }
        .mini-orb:hover { transform: scale(1.1); }
        .nav-menu { display: flex; gap: 20px; }
        .nav-item { 
            color: #666; text-decoration: none; font-size: 13px; font-weight: 400; 
            transition: all 0.3s ease; padding: 8px 12px; border-radius: 6px;
            min-height: 44px; display: flex; align-items: center;
        }
        .nav-item:hover, .nav-item.active { color: #7B61FF; background: rgba(123, 97, 255, 0.1); }
        .nav-right { display: flex; align-items: center; gap: 10px; }
        
        .more-nav-btn { 
            color: #666; text-decoration: none; font-size: 13px; font-weight: 400; 
            transition: all 0.3s ease; padding: 8px 12px; border-radius: 6px;
            min-height: 44px; display: flex; align-items: center; cursor: pointer;
            background: none; border: none;
        }
        .more-nav-btn:hover { color: #7B61FF; background: rgba(123, 97, 255, 0.1); }
        
        .settings-btn { 
            background: rgba(255, 255, 255, 0.6); border: 1px solid rgba(255, 255, 255, 0.3); 
            border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; 
            justify-content: center; cursor: pointer; transition: all 0.3s ease; color: #666;
            font-size: 16px; font-weight: bold;
        }
        .settings-btn:hover { background: rgba(255, 255, 255, 0.8); color: #333; }
        
        .api-indicator { font-size: 12px; padding: 4px 8px; border-radius: 8px; font-weight: 500; }
        .api-indicator.online { background: rgba(34, 197, 94, 0.1); color: #22c55e; }
        .api-indicator.offline { background: rgba(239, 68, 68, 0.1); color: #ef4444; }
        .api-indicator.warning { background: rgba(251, 146, 60, 0.1); color: #ea580c; }

        /* Main Layout */
        .main-container { padding: 70px 20px 20px; }
        .top-info-bar { 
            display: flex; justify-content: space-between; align-items: center; 
            margin-bottom: 20px; padding: 0 10px;
        }
        .time-display { display: flex; flex-direction: column; }
        .current-time { font-size: 18px; font-weight: 300; color: #333; }
        .current-date { font-size: 11px; color: #666; margin-top: 2px; }
        .weather-widget { display: flex; align-items: center; gap: 6px; }

        /* Dashboard Layout */
        .dashboard-layout { 
            display: grid; 
            grid-template-columns: 1fr 1fr 1fr; 
            gap: 20px; 
            max-width: 1400px; 
            margin: 0 auto;
        }

        /* Dashboard Cards */
        .dashboard-card { 
            background: rgba(255, 255, 255, 0.6); backdrop-filter: blur(40px); 
            border: 1px solid rgba(255, 255, 255, 0.4); border-radius: 16px; 
            padding: 20px; transition: all 0.3s ease; min-height: 500px;
            display: flex; flex-direction: column;
        }
        .dashboard-card:hover { 
            transform: translateY(-2px); background: rgba(255, 255, 255, 0.7); 
        }

        /* Card Headers */
        .card-header { 
            display: flex; justify-content: space-between; align-items: center; 
            margin-bottom: 15px; padding-bottom: 10px; 
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
        }
        .card-title { 
            font-size: clamp(16px, 2vw, 18px); font-weight: 500; color: #333;
        }
        .card-subtitle { 
            font-size: clamp(10px, 1.2vw, 12px); color: #666; 
            text-transform: uppercase; letter-spacing: 0.5px;
        }

        /* Home Assistant Card - Split Layout */
        .home-card { 
            display: flex; flex-direction: column; gap: 15px;
        }
        .room-controls { 
            flex: 1; padding: 15px; background: rgba(255, 255, 255, 0.3);
            border-radius: 12px; margin-bottom: 10px;
        }
        .house-stats { 
            flex: 1; padding: 15px; background: rgba(255, 255, 255, 0.3);
            border-radius: 12px;
        }

        /* Events & Tasks Lists */
        .content-list { 
            flex: 1; overflow-y: auto; max-height: 400px;
        }
        .list-item { 
            padding: 12px; margin-bottom: 8px; background: rgba(255, 255, 255, 0.4);
            border-radius: 8px; cursor: pointer; transition: all 0.2s ease;
            min-height: 60px; display: flex; align-items: center; justify-content: space-between;
        }
        .list-item:hover { 
            background: rgba(255, 255, 255, 0.6); transform: translateX(4px);
        }

        /* Home Stats Grid */
        .stats-grid { 
            display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px;
        }
        .stat-item { 
            text-align: center; padding: 12px; background: rgba(255, 255, 255, 0.4);
            border-radius: 8px;
        }
        .stat-value { 
            font-size: 18px; font-weight: 600; color: #7B61FF; margin-bottom: 4px;
        }
        .stat-label { 
            font-size: 10px; color: #666; text-transform: uppercase;
        }

        /* Room Control Buttons */
        .room-grid { 
            display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px;
        }
        .room-btn { 
            padding: 12px; border: none; border-radius: 8px; cursor: pointer;
            background: rgba(123, 97, 255, 0.1); color: #7B61FF; font-size: 12px;
            font-weight: 500; transition: all 0.2s ease; min-height: 44px;
        }
        .room-btn:hover { 
            background: rgba(123, 97, 255, 0.2); transform: scale(1.02);
        }
        .room-btn.on { 
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        /* Quick Add Buttons */
        .quick-add { 
            padding: 10px 0; border-top: 1px solid rgba(255, 255, 255, 0.3);
            margin-top: auto; display: flex; justify-content: center;
        }
        .add-btn { 
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            border: none; border-radius: 20px; color: white; padding: 8px 16px;
            font-size: 12px; font-weight: 500; cursor: pointer; 
            transition: all 0.2s ease; min-height: 36px;
        }
        .add-btn:hover { 
            transform: scale(1.05); box-shadow: 0 4px 12px rgba(123, 97, 255, 0.3);
        }

        /* Task Completion Styling */
        .task-completed {
            opacity: 0.6; text-decoration: line-through;
        }
        .task-checkbox {
            margin-right: 8px; cursor: pointer; transform: scale(1.2);
        }
        .floating-add-btn { 
            position: fixed; bottom: 30px; right: 30px; width: 64px; height: 64px; 
            border-radius: 50%; background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            border: none; color: white; cursor: pointer; display: flex; align-items: center; 
            justify-content: center; font-size: 28px; transition: all 0.3s ease; 
            z-index: 1000; box-shadow: 0 4px 12px rgba(123, 97, 255, 0.3);
        }
        .floating-add-btn:hover { transform: scale(1.1); box-shadow: 0 6px 16px rgba(123, 97, 255, 0.4); }

        /* More Overlay */
        .more-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0, 0, 0, 0.4); backdrop-filter: blur(8px);
            display: none; align-items: center; justify-content: center; z-index: 3000;
            opacity: 0; transition: all 0.3s ease;
        }
        .more-overlay.active { display: flex; opacity: 1; }
        .more-content {
            background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(60px);
            border: 1px solid rgba(255, 255, 255, 0.5); border-radius: 20px;
            padding: 40px; max-width: 500px; width: 90%; position: relative;
            transform: scale(0.8); transition: transform 0.3s ease;
        }
        .more-overlay.active .more-content { transform: scale(1); }
        .more-header { text-align: center; margin-bottom: 30px; }
        .more-title {
            font-size: 24px; font-weight: 300; color: #333; margin-bottom: 10px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .more-close {
            position: absolute; top: 15px; right: 15px; 
            background: rgba(255, 255, 255, 0.6); border: none;
            border-radius: 50%; width: 36px; height: 36px;
            font-size: 18px; cursor: pointer; color: #666;
        }
        .more-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
        .more-item {
            background: rgba(255, 255, 255, 0.8); border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 16px; padding: 24px; text-align: center; cursor: pointer;
            transition: all 0.3s ease; min-height: 120px; display: flex;
            flex-direction: column; align-items: center; justify-content: center;
        }
        .more-item:hover {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white; transform: translateY(-4px);
        }
        .more-item-icon { font-size: 36px; margin-bottom: 12px; }
        .more-item-label { font-size: 15px; font-weight: 500; }

        @media (max-width: 900px) {
            .nav-menu { display: none; }
            .dashboard-layout { grid-template-columns: 1fr; }
        }
        
        @media (max-width: 420px) {
            .main-container { padding: 70px 10px 20px; }
            .dashboard-layout { gap: 15px; }
            .room-grid { grid-template-columns: 1fr; }
            .stats-grid { grid-template-columns: 1fr; }
            .floating-add-btn { bottom: 20px; right: 20px; width: 56px; height: 56px; font-size: 24px; }
        }
    </style>
</head>
<body>
    <!-- Navigation -->
    <div class="nav-bar">
        <div class="nav-left">
            <div class="mini-orb" onclick="window.location.href='index.html'"></div>
            <div class="nav-menu">
                <a href="index.html" class="nav-item">Chat</a>
                <a href="dashboard.html" class="nav-item active">Dashboard</a>
                <a href="lists.html" class="nav-item">Lists</a>
                <a href="calendar.html" class="nav-item">Calendar</a>
                <a href="journal.html" class="nav-item">Journal</a>
                <button class="more-nav-btn" onclick="openMoreOverlay()">More</button>
            </div>
        </div>
        <div class="nav-right">
            <div class="api-indicator connecting" id="apiStatus">🔄 Connecting</div>
            <button class="settings-btn" onclick="window.location.href='settings.html'" title="Settings">⚙️</button>
        </div>
    </div>

    <!-- Main Content -->
    <div class="main-container">
        <!-- Top Info Bar -->
        <div class="top-info-bar">
            <div class="time-display">
                <div class="current-time" id="currentTime">Loading...</div>
                <div class="current-date" id="currentDate">Loading...</div>
            </div>
            <div class="weather-widget">
                <div>☀️</div>
                <div>23°</div>
            </div>
        </div>

        <!-- Dashboard Layout -->
        <div class="dashboard-layout">
            <!-- TILE 1: Events -->
            <div class="dashboard-card">
                <div class="card-header">
                    <h2 class="card-title">📅 Events</h2>
                    <span class="card-subtitle" id="eventCount">Loading...</span>
                </div>
                <div class="content-list" id="eventsList">
                    <div class="list-item">
                        <div>
                            <div style="font-size: 12px; color: #7B61FF; font-weight: 600;">9:00 AM</div>
                            <div style="font-size: 14px; color: #333;">Team Standup Meeting</div>
                        </div>
                        <div style="font-size: 12px; color: #666;">Work</div>
                    </div>
                    <div class="list-item">
                        <div>
                            <div style="font-size: 12px; color: #7B61FF; font-weight: 600;">2:30 PM</div>
                            <div style="font-size: 14px; color: #333;">Dentist Appointment</div>
                        </div>
                        <div style="font-size: 12px; color: #666;">Health</div>
                    </div>
                    <div class="list-item">
                        <div>
                            <div style="font-size: 12px; color: #7B61FF; font-weight: 600;">6:00 PM</div>
                            <div style="font-size: 14px; color: #333;">Dinner with Sarah</div>
                        </div>
                        <div style="font-size: 12px; color: #666;">Social</div>
                    </div>
                </div>
                <div class="quick-add">
                    <button class="add-btn" onclick="navigateToPage('calendar.html')">+ Add Event</button>
                </div>
            </div>

            <!-- TILE 2: Tasks -->
            <div class="dashboard-card">
                <div class="card-header">
                    <h2 class="card-title">✅ Tasks</h2>
                    <span class="card-subtitle" id="taskCount">Loading...</span>
                </div>
                <div class="content-list" id="tasksList">
                    <div class="list-item">
                        <div style="display: flex; align-items: center; width: 100%;">
                            <input type="checkbox" class="task-checkbox" onchange="toggleTask(this, 1)">
                            <div style="flex: 1;">
                                <div style="font-size: 14px; color: #333;">Review quarterly reports</div>
                                <div style="font-size: 11px; color: #666;">Due: Today 5:00 PM</div>
                            </div>
                            <div style="font-size: 12px; color: #dc2626;">High</div>
                        </div>
                    </div>
                    <div class="list-item">
                        <div style="display: flex; align-items: center; width: 100%;">
                            <input type="checkbox" class="task-checkbox" onchange="toggleTask(this, 2)">
                            <div style="flex: 1;">
                                <div style="font-size: 14px; color: #333;">Call insurance company</div>
                                <div style="font-size: 11px; color: #666;">Due: Today</div>
                            </div>
                            <div style="font-size: 12px; color: #ea580c;">Medium</div>
                        </div>
                    </div>
                    <div class="list-item">
                        <div style="display: flex; align-items: center; width: 100%;">
                            <input type="checkbox" class="task-checkbox" onchange="toggleTask(this, 3)" checked>
                            <div style="flex: 1;" class="task-completed">
                                <div style="font-size: 14px; color: #333;">Water plants</div>
                                <div style="font-size: 11px; color: #666;">Daily task</div>
                            </div>
                            <div style="font-size: 12px; color: #22c55e;">Low</div>
                        </div>
                    </div>
                </div>
                <div class="quick-add">
                    <button class="add-btn" onclick="navigateToPage('lists.html')">+ Add Task</button>
                </div>
            </div>

            <!-- TILE 3: Home -->
            <div class="dashboard-card home-card">
                <div class="card-header">
                    <h2 class="card-title">🏠 Home</h2>
                    <span class="card-subtitle" id="homeStatus">Loading...</span>
                </div>
                
                <!-- Room Controls (Top Half) -->
                <div class="room-controls">
                    <h3 style="font-size: 14px; margin-bottom: 10px; color: #333;">Room Controls</h3>
                    <div class="room-grid">
                        <button class="room-btn" onclick="toggleRoom('living_room')">
                            💡 Living Room
                        </button>
                        <button class="room-btn on" onclick="toggleRoom('kitchen')">
                            🔆 Kitchen
                        </button>
                        <button class="room-btn" onclick="toggleRoom('bedroom')">
                            🛏️ Bedroom
                        </button>
                        <button class="room-btn" onclick="toggleRoom('office')">
                            💻 Office
                        </button>
                        <button class="room-btn on" onclick="toggleRoom('outdoor')">
                            🌿 Outdoor
                        </button>
                        <button class="room-btn" onclick="toggleRoom('garage')">
                            🚗 Garage
                        </button>
                    </div>
                </div>

                <!-- House Stats (Bottom Half) -->
                <div class="house-stats">
                    <h3 style="font-size: 14px; margin-bottom: 10px; color: #333;">House Stats</h3>
                    <div class="stats-grid">
                        <div class="stat-item">
                            <div class="stat-value">⚡ 2.4kW</div>
                            <div class="stat-label">Solar Output</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value">🔋 85%</div>
                            <div class="stat-label">Battery</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value">🔒 Secure</div>
                            <div class="stat-label">Security</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value">🌡️ 22°C</div>
                            <div class="stat-label">Inside Temp</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- More Overlay -->
    <div class="more-overlay" id="moreOverlay">
        <div class="more-content">
            <button class="more-close" onclick="closeMoreOverlay()">×</button>
            <div class="more-header">
                <h2 class="more-title">More Options</h2>
            </div>
            <div class="more-grid">
                <div class="more-item" onclick="navigateToPage('memories.html')">
                    <div class="more-item-icon">🧠</div>
                    <div class="more-item-label">Memories</div>
                </div>
                <div class="more-item" onclick="navigateToPage('workflows.html')">
                    <div class="more-item-icon">⚡</div>
                    <div class="more-item-label">Workflows</div>
                </div>
                <div class="more-item" onclick="navigateToPage('settings.html')">
                    <div class="more-item-icon">⚙️</div>
                    <div class="more-item-label">Settings</div>
                </div>
                <div class="more-item" onclick="alert('Coming soon!')">
                    <div class="more-item-icon">📊</div>
                    <div class="more-item-label">Analytics</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Floating Add Button for Chat -->
    <button class="floating-add-btn" onclick="navigateToPage('index.html')" title="Open Chat">+</button>

    <script src="js/common.js"></script>
    <script>
        let apiConnected = false;
        
        async function loadDashboardData() {
            try {
                // Check API connectivity
                const healthResponse = await fetch(`${API_BASE}/health`);
                apiConnected = healthResponse.ok;
                
                if (apiConnected) {
                    document.getElementById('apiStatus').className = 'api-indicator online';
                    document.getElementById('apiStatus').textContent = '✅ Connected';
                    
                    // Load real data
                    await Promise.all([
                        loadTodaysEvents(),
                        loadTodaysTasks(),
                        loadHomeStatus()
                    ]);
                } else {
                    throw new Error('API not available');
                }
            } catch (error) {
                console.error('API connection failed:', error);
                document.getElementById('apiStatus').className = 'api-indicator offline';
                document.getElementById('apiStatus').textContent = '❌ Offline';
                
                // Keep demo data
                document.getElementById('eventCount').textContent = '3 events today';
                updateTaskCount();
                document.getElementById('homeStatus').textContent = 'All systems normal';
            }
        }

        async function loadTodaysEvents() {
            try {
                const response = await fetch(`${API_BASE}/calendar/events`);
                if (response.ok) {
                    const data = await response.json();
                    const events = Array.isArray(data) ? data : data.events || [];
                    
                    // Filter for today's events
                    const today = new Date().toISOString().split('T')[0];
                    const todaysEvents = events.filter(event => {
                        const eventDate = event.start_date || event.date;
                        return eventDate === today;
                    });
                    
                    displayEvents(todaysEvents);
                    document.getElementById('eventCount').textContent = `${todaysEvents.length} events today`;
                } else {
                    console.log('Events API not responding, checking /api/events...');
                    const fallbackResponse = await fetch(`${API_BASE}/events`);
                    if (fallbackResponse.ok) {
                        const fallbackData = await fallbackResponse.json();
                        const events = Array.isArray(fallbackData) ? fallbackData : fallbackData.events || [];
                        
                        const today = new Date().toISOString().split('T')[0];
                        const todaysEvents = events.filter(event => {
                            const eventDate = event.start_date || event.date;
                            return eventDate === today;
                        });
                        
                        displayEvents(todaysEvents);
                        document.getElementById('eventCount').textContent = `${todaysEvents.length} events today`;
                    }
                }
            } catch (error) {
                console.error('Failed to load events:', error);
                document.getElementById('eventCount').textContent = '3 events today';
            }
        }

        async function loadTodaysTasks() {
            try {
                const response = await fetch(`${API_BASE}/tasks/today`);
                if (response.ok) {
                    const tasks = await response.json();
                    displayTasks(tasks);
                    updateTaskCountFromAPI(tasks);
                }
            } catch (error) {
                console.error('Failed to load tasks:', error);
            }
        }

        async function loadHomeStatus() {
            try {
                const response = await fetch(`${API_BASE}/homeassistant/states`);
                if (response.ok) {
                    const homeData = await response.json();
                    updateHomeControls(homeData);
                    document.getElementById('homeStatus').textContent = 'Connected';
                }
            } catch (error) {
                console.error('Failed to load home status:', error);
                document.getElementById('homeStatus').textContent = 'Offline';
            }
        }

        function displayEvents(events) {
            const eventsList = document.getElementById('eventsList');
            if (events.length === 0) {
                eventsList.innerHTML = '<div class="list-item" style="justify-content: center; color: #666; font-style: italic;">No events today</div>';
                return;
            }
            
            eventsList.innerHTML = events.map(event => `
                <div class="list-item">
                    <div>
                        <div style="font-size: 12px; color: #7B61FF; font-weight: 600;">${formatTime(event.start_time || event.time)}</div>
                        <div style="font-size: 14px; color: #333;">${event.title}</div>
                    </div>
                    <div style="font-size: 12px; color: #666;">${event.category || 'Personal'}</div>
                </div>
            `).join('');
        }

        function displayTasks(tasks) {
            const tasksList = document.getElementById('tasksList');
            if (tasks.length === 0) {
                tasksList.innerHTML = '<div class="list-item" style="justify-content: center; color: #666; font-style: italic;">No tasks today</div>';
                return;
            }
            
            tasksList.innerHTML = tasks.map(task => `
                <div class="list-item">
                    <div style="display: flex; align-items: center; width: 100%;">
                        <input type="checkbox" class="task-checkbox" ${task.status === 'completed' ? 'checked' : ''} 
                               onchange="toggleTask(this, ${task.id})">
                        <div style="flex: 1;" class="${task.status === 'completed' ? 'task-completed' : ''}">
                            <div style="font-size: 14px; color: #333;">${task.title}</div>
                            <div style="font-size: 11px; color: #666;">${task.due_date ? 'Due: ' + formatDate(task.due_date) : 'No due date'}</div>
                        </div>
                        <div style="font-size: 12px; color: ${getPriorityColor(task.priority)}">${task.priority || 'Medium'}</div>
                    </div>
                </div>
            `).join('');
        }

        function updateHomeControls(homeData) {
            if (homeData.states && homeData.states.lights) {
                homeData.states.lights.forEach(light => {
                    const roomName = light.entity_id.split('.')[1].replace('_', ' ');
                    const isOn = light.state === 'on';
                    updateRoomButton(roomName, isOn);
                });
            }
        }

        function updateRoomButton(roomName, isOn) {
            const buttons = document.querySelectorAll('.room-btn');
            buttons.forEach(btn => {
                if (btn.textContent.toLowerCase().includes(roomName.toLowerCase())) {
                    if (isOn) {
                        btn.classList.add('on');
                    } else {
                        btn.classList.remove('on');
                    }
                }
            });
        }

        function formatTime(timeStr) {
            if (!timeStr) return '';
            const [hours, minutes] = timeStr.split(':');
            const hour12 = hours % 12 || 12;
            const ampm = hours >= 12 ? 'PM' : 'AM';
            return `${hour12}:${minutes} ${ampm}`;
        }

        function formatDate(dateStr) {
            if (!dateStr) return '';
            const date = new Date(dateStr);
            return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        }

        function getPriorityColor(priority) {
            switch(priority?.toLowerCase()) {
                case 'high': return '#dc2626';
                case 'medium': return '#ea580c';
                case 'low': return '#22c55e';
                default: return '#666';
            }
        }

        function toggleTask(checkbox, taskId) {
            const isCompleted = checkbox.checked;
            const taskContent = checkbox.parentElement.querySelector('div[style*="flex: 1"]');
            
            if (isCompleted) {
                taskContent.classList.add('task-completed');
            } else {
                taskContent.classList.remove('task-completed');
            }
            
            if (apiConnected) {
                fetch(`${API_BASE}/tasks/${taskId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        status: isCompleted ? 'completed' : 'pending' 
                    })
                }).then(response => {
                    if (!response.ok) {
                        checkbox.checked = !isCompleted;
                        taskContent.classList.toggle('task-completed');
                        console.error('Failed to update task status');
                    }
                }).catch(error => {
                    console.error('Task update error:', error);
                    checkbox.checked = !isCompleted;
                    taskContent.classList.toggle('task-completed');
                });
            }
            
            updateTaskCount();
        }

        function toggleRoom(room) {
            const btn = event.target;
            const isCurrentlyOn = btn.classList.contains('on');
            
            btn.classList.toggle('on');
            
            if (apiConnected) {
                const service = isCurrentlyOn ? 'light.turn_off' : 'light.turn_on';
                const entityId = `light.${room}`;
                
                fetch(`${API_BASE}/homeassistant/service`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        service: service,
                        entity_id: entityId
                    })
                }).then(response => {
                    if (!response.ok) {
                        btn.classList.toggle('on');
                        console.error('Failed to control home device');
                    }
                }).catch(error => {
                    console.error('Home control error:', error);
                    btn.classList.toggle('on');
                });
            }
            
            console.log(`Toggling ${room}`);
        }

        function updateTaskCount() {
            const checkboxes = document.querySelectorAll('#tasksList .task-checkbox');
            const completed = document.querySelectorAll('#tasksList .task-checkbox:checked').length;
            const pending = checkboxes.length - completed;
            document.getElementById('taskCount').textContent = `${pending} pending`;
        }

        function updateTaskCountFromAPI(tasks) {
            const pending = tasks.filter(task => !task.completed).length;
            document.getElementById('taskCount').textContent = `${pending} pending`;
        }

        function navigateToPage(page) {
            window.location.href = page;
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            loadDashboardData();
            
            // Refresh dashboard every 5 minutes
            setInterval(loadDashboardData, 5 * 60 * 1000);
        });
    </script>
</body>
</html>
EOF

cp /home/pi/zoe/services/zoe-ui/dist/journal.html /home/pi/zoe/services/zoe-ui/dist/journal.html.backup
cat > /home/pi/zoe/services/zoe-ui/dist/journal.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Zoe - Journal</title>
    <style>
        * { 
            margin: 0; padding: 0; box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            touch-action: manipulation;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            min-height: 100vh; color: #333;
            font-size: clamp(14px, 1.6vw, 16px);
        }
        
        /* Navigation */
        .nav-bar {
            position: fixed; top: 0; left: 0; right: 0; background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px); border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            padding: 10px 15px; z-index: 100; display: flex; justify-content: space-between;
            align-items: center; height: 60px;
        }
        .nav-left { display: flex; align-items: center; gap: 15px; }
        .mini-orb { 
            width: 32px; height: 32px; border-radius: 50%; 
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            cursor: pointer; transition: all 0.3s ease; min-width: 44px; min-height: 44px;
        }
        .mini-orb:hover { transform: scale(1.1); }
        .nav-menu { display: flex; gap: 20px; }
        .nav-item { 
            color: #666; text-decoration: none; font-size: 13px; font-weight: 400; 
            transition: all 0.3s ease; padding: 8px 12px; border-radius: 6px;
            min-height: 44px; display: flex; align-items: center;
        }
        .nav-item:hover, .nav-item.active { color: #7B61FF; background: rgba(123, 97, 255, 0.1); }
        .nav-right { display: flex; align-items: center; gap: 10px; }
        
        .more-nav-btn { 
            color: #666; text-decoration: none; font-size: 13px; font-weight: 400; 
            transition: all 0.3s ease; padding: 8px 12px; border-radius: 6px;
            min-height: 44px; display: flex; align-items: center; cursor: pointer;
            background: none; border: none;
        }
        .more-nav-btn:hover { color: #7B61FF; background: rgba(123, 97, 255, 0.1); }
        
        .settings-btn { 
            background: rgba(255, 255, 255, 0.6); border: 1px solid rgba(255, 255, 255, 0.3); 
            border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; 
            justify-content: center; cursor: pointer; transition: all 0.3s ease; color: #666;
            font-size: 16px; font-weight: bold;
        }
        .settings-btn:hover { background: rgba(255, 255, 255, 0.8); color: #333; }
        
        .api-indicator { font-size: 12px; padding: 4px 8px; border-radius: 8px; font-weight: 500; }
        .api-indicator.online { background: rgba(34, 197, 94, 0.1); color: #22c55e; }
        .api-indicator.offline { background: rgba(239, 68, 68, 0.1); color: #ef4444; }
        .api-indicator.warning { background: rgba(251, 146, 60, 0.1); color: #ea580c; }

        /* Main Layout */
        .main-container { padding: 70px 20px 20px; }
        .top-info-bar { 
            display: flex; justify-content: space-between; align-items: center; 
            margin-bottom: 20px; padding: 0 10px;
        }
        .time-display { display: flex; flex-direction: column; }
        .current-time { font-size: 18px; font-weight: 300; color: #333; }
        .current-date { font-size: 11px; color: #666; margin-top: 2px; }
        .weather-widget { display: flex; align-items: center; gap: 6px; }

        /* Journal Layout */
        .journal-layout {
            display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
            max-width: 1200px; margin: 0 auto; height: calc(100vh - 150px);
        }

        /* Entries List */
        .entries-panel {
            background: rgba(255, 255, 255, 0.6); backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4); border-radius: 16px;
            padding: 20px; overflow-y: auto;
        }
        .entries-header {
            margin-bottom: 20px; padding-bottom: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
        }
        .entries-title {
            font-size: 18px; font-weight: 500; color: #333;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .entries-search {
            margin-top: 12px;
        }
        .search-input {
            width: 100%; padding: 12px 16px; border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 8px; background: rgba(255, 255, 255, 0.9);
            font-size: 14px; outline: none; transition: all 0.3s ease;
            min-height: 48px;
        }
        .search-input:focus {
            border-color: #7B61FF; box-shadow: 0 0 0 2px rgba(123, 97, 255, 0.1);
        }

        .entry-item {
            padding: 16px; margin-bottom: 12px; background: rgba(255, 255, 255, 0.4);
            border-radius: 12px; cursor: pointer; transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.3);
        }
        .entry-item:hover {
            background: rgba(255, 255, 255, 0.6); transform: translateX(4px);
        }
        .entry-item.selected {
            background: rgba(123, 97, 255, 0.1); border-color: #7B61FF;
        }
        .entry-date {
            font-size: 12px; color: #666; margin-bottom: 8px;
            text-transform: uppercase; letter-spacing: 0.5px;
        }
        .entry-title {
            font-size: 16px; font-weight: 600; color: #333; margin-bottom: 8px;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .entry-preview {
            font-size: 14px; color: #666; line-height: 1.4;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .entry-mood {
            display: inline-block; font-size: 20px; margin-right: 8px;
        }

        /* Editor Panel */
        .editor-panel {
            background: rgba(255, 255, 255, 0.6); backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4); border-radius: 16px;
            padding: 20px; display: flex; flex-direction: column;
        }
        .editor-header {
            margin-bottom: 20px; padding-bottom: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
        }
        .editor-title {
            font-size: 18px; font-weight: 500; color: #333;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }

        .editor-form {
            flex: 1; display: flex; flex-direction: column;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-label {
            display: block; font-size: 13px; font-weight: 500; color: #666;
            margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .form-input, .form-textarea {
            width: 100%; padding: 16px 20px; border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 12px; background: rgba(255, 255, 255, 0.9);
            font-size: 16px; outline: none; transition: all 0.3s ease;
            min-height: 56px; font-family: inherit;
        }
        .form-textarea {
            flex: 1; min-height: 300px; resize: vertical;
        }
        .form-input:focus, .form-textarea:focus {
            border-color: #7B61FF; box-shadow: 0 0 0 2px rgba(123, 97, 255, 0.1);
        }

        .mood-selector {
            display: flex; gap: 12px; margin-bottom: 20px;
        }
        .mood-btn {
            width: 48px; height: 48px; border-radius: 50%; border: 2px solid transparent;
            background: rgba(255, 255, 255, 0.6); cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            font-size: 24px; transition: all 0.3s ease;
        }
        .mood-btn:hover {
            background: rgba(255, 255, 255, 0.8); transform: scale(1.1);
        }
        .mood-btn.selected {
            border-color: #7B61FF; background: rgba(123, 97, 255, 0.1);
        }

        .form-actions {
            display: flex; gap: 12px; justify-content: flex-end; margin-top: 20px;
        }
        .form-btn {
            padding: 16px 24px; border: none; border-radius: 12px; font-size: 16px;
            font-weight: 500; cursor: pointer; transition: all 0.3s ease; min-height: 56px;
            min-width: 120px;
        }
        .form-btn.primary {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }
        .form-btn.secondary {
            background: rgba(255, 255, 255, 0.8); color: #666;
        }
        .form-btn.danger {
            background: #ef4444; color: white;
        }

        .floating-add-btn { 
            position: fixed; bottom: 30px; right: 30px; width: 64px; height: 64px; 
            border-radius: 50%; background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            border: none; color: white; cursor: pointer; display: flex; align-items: center; 
            justify-content: center; font-size: 28px; transition: all 0.3s ease; 
            z-index: 1000; box-shadow: 0 4px 12px rgba(123, 97, 255, 0.3);
        }
        .floating-add-btn:hover { transform: scale(1.1); box-shadow: 0 6px 16px rgba(123, 97, 255, 0.4); }

        /* Empty State */
        .empty-state {
            text-align: center; padding: 60px 20px; color: #666;
        }
        .empty-icon { font-size: 48px; margin-bottom: 16px; }
        .empty-title { font-size: 18px; font-weight: 500; margin-bottom: 8px; }
        .empty-subtitle { font-size: 14px; }

        /* More Overlay */
        .more-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0, 0, 0, 0.4); backdrop-filter: blur(8px);
            display: none; align-items: center; justify-content: center; z-index: 3000;
            opacity: 0; transition: all 0.3s ease;
        }
        .more-overlay.active { display: flex; opacity: 1; }
        .more-content {
            background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(60px);
            border: 1px solid rgba(255, 255, 255, 0.5); border-radius: 20px;
            padding: 40px; max-width: 500px; width: 90%; position: relative;
            transform: scale(0.8); transition: transform 0.3s ease;
        }
        .more-overlay.active .more-content { transform: scale(1); }
        .more-header { text-align: center; margin-bottom: 30px; }
        .more-title {
            font-size: 24px; font-weight: 300; color: #333; margin-bottom: 10px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .more-close {
            position: absolute; top: 15px; right: 15px; 
            background: rgba(255, 255, 255, 0.6); border: none;
            border-radius: 50%; width: 36px; height: 36px;
            font-size: 18px; cursor: pointer; color: #666;
        }
        .more-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
        .more-item {
            background: rgba(255, 255, 255, 0.8); border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 16px; padding: 24px; text-align: center; cursor: pointer;
            transition: all 0.3s ease; min-height: 120px; display: flex;
            flex-direction: column; align-items: center; justify-content: center;
        }
        .more-item:hover {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white; transform: translateY(-4px);
        }
        .more-item-icon { font-size: 36px; margin-bottom: 12px; }
        .more-item-label { font-size: 15px; font-weight: 500; }

        /* Mobile Responsive */
        @media (max-width: 900px) {
            .nav-menu { display: none; }
            .journal-layout { grid-template-columns: 1fr; gap: 15px; }
            .entries-panel { order: 2; }
            .editor-panel { order: 1; }
            .more-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <!-- Navigation -->
    <div class="nav-bar">
        <div class="nav-left">
            <div class="mini-orb" onclick="window.location.href='index.html'"></div>
            <div class="nav-menu">
                <a href="index.html" class="nav-item">Chat</a>
                <a href="dashboard.html" class="nav-item">Dashboard</a>
                <a href="lists.html" class="nav-item">Lists</a>
                <a href="calendar.html" class="nav-item">Calendar</a>
                <a href="journal.html" class="nav-item active">Journal</a>
                <button class="more-nav-btn" onclick="openMoreOverlay()">More</button>
            </div>
        </div>
        <div class="nav-right">
            <div class="api-indicator connecting" id="apiStatus">🔄 Connecting</div>
            <button class="settings-btn" onclick="window.location.href='settings.html'" title="Settings">⚙️</button>
        </div>
    </div>

    <!-- Main Content -->
    <div class="main-container">
        <!-- Top Info Bar -->
        <div class="top-info-bar">
            <div class="time-display">
                <div class="current-time" id="currentTime">Loading...</div>
                <div class="current-date" id="currentDate">Loading...</div>
            </div>
            <div class="weather-widget">
                <div>☀️</div>
                <div>23°</div>
            </div>
        </div>

        <!-- Journal Layout -->
        <div class="journal-layout">
            <!-- Entries List -->
            <div class="entries-panel">
                <div class="entries-header">
                    <h2 class="entries-title">Journal Entries</h2>
                    <div class="entries-search">
                        <input type="text" class="search-input" id="searchInput" placeholder="Search entries...">
                    </div>
                </div>
                <div class="entries-list" id="entriesList">
                    <!-- Entries will be populated here -->
                </div>
            </div>

            <!-- Editor Panel -->
            <div class="editor-panel">
                <div class="editor-header">
                    <h2 class="editor-title" id="editorTitle">New Entry</h2>
                </div>
                
                <form class="editor-form" id="journalForm">
                    <div class="form-group">
                        <label class="form-label">Title</label>
                        <input type="text" class="form-input" id="entryTitle" placeholder="What's on your mind?">
                    </div>
                    
                    <div class="form-group">
                        <label class="form-label">Mood</label>
                        <div class="mood-selector">
                            <button type="button" class="mood-btn" data-mood="😊" onclick="selectMood(this)">😊</button>
                            <button type="button" class="mood-btn" data-mood="😐" onclick="selectMood(this)">😐</button>
                            <button type="button" class="mood-btn" data-mood="😔" onclick="selectMood(this)">😔</button>
                            <button type="button" class="mood-btn" data-mood="😴" onclick="selectMood(this)">😴</button>
                            <button type="button" class="mood-btn" data-mood="🤔" onclick="selectMood(this)">🤔</button>
                            <button type="button" class="mood-btn" data-mood="😄" onclick="selectMood(this)">😄</button>
                        </div>
                    </div>
                    
                    <div class="form-group" style="flex: 1; display: flex; flex-direction: column;">
                        <label class="form-label">Content</label>
                        <textarea class="form-textarea" id="entryContent" placeholder="Write your thoughts here..."></textarea>
                    </div>
                    
                    <div class="form-actions">
                        <button type="button" class="form-btn secondary" onclick="clearForm()">Clear</button>
                        <button type="button" class="form-btn danger" onclick="deleteEntry()" id="deleteBtn" style="display: none;">Delete</button>
                        <button type="submit" class="form-btn primary">Save Entry</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- More Overlay -->
    <div class="more-overlay" id="moreOverlay">
        <div class="more-content">
            <button class="more-close" onclick="closeMoreOverlay()">×</button>
            <div class="more-header">
                <h2 class="more-title">More Options</h2>
            </div>
            <div class="more-grid">
                <div class="more-item" onclick="navigateToPage('memories.html')">
                    <div class="more-item-icon">🧠</div>
                    <div class="more-item-label">Memories</div>
                </div>
                <div class="more-item" onclick="navigateToPage('workflows.html')">
                    <div class="more-item-icon">⚡</div>
                    <div class="more-item-label">Workflows</div>
                </div>
                <div class="more-item" onclick="navigateToPage('settings.html')">
                    <div class="more-item-icon">⚙️</div>
                    <div class="more-item-label">Settings</div>
                </div>
                <div class="more-item" onclick="alert('Coming soon!')">
                    <div class="more-item-icon">📊</div>
                    <div class="more-item-label">Analytics</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Floating Add Button -->
    <button class="floating-add-btn" onclick="newEntry()" title="New Entry">+</button>

    <script src="js/common.js"></script>
    <script>
        let allEntries = [];
        let currentEntry = null;
        let selectedMood = null;

        // Load journal entries
        async function loadEntries() {
            try {
                const response = await apiRequest('/journal');
                allEntries = response.entries || [];
                displayEntries();
            } catch (error) {
                console.error('Failed to load entries:', error);
                allEntries = [];
                displayEntries();
            }
        }

        // Display entries
        function displayEntries() {
            const container = document.getElementById('entriesList');
            
            if (allEntries.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon">📝</div>
                        <div class="empty-title">No entries yet</div>
                        <div class="empty-subtitle">Start writing your first journal entry</div>
                    </div>
                `;
                return;
            }
            
            // Sort entries by date (newest first)
            const sortedEntries = allEntries.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            
            container.innerHTML = sortedEntries.map(entry => `
                <div class="entry-item" onclick="selectEntry(${entry.id})">
                    <div class="entry-date">${formatDate(entry.created_at)}</div>
                    <div class="entry-title">${entry.title}</div>
                    <div class="entry-preview">${entry.content.substring(0, 100)}${entry.content.length > 100 ? '...' : ''}</div>
                    ${entry.mood ? `<div class="entry-mood">${entry.mood}</div>` : ''}
                </div>
            `).join('');
        }

        // Select entry
        function selectEntry(entryId) {
            const entry = allEntries.find(e => e.id === entryId);
            if (!entry) return;
            
            currentEntry = entry;
            
            // Update UI
            document.querySelectorAll('.entry-item').forEach(item => {
                item.classList.remove('selected');
            });
            event.target.closest('.entry-item').classList.add('selected');
            
            // Populate form
            document.getElementById('entryTitle').value = entry.title;
            document.getElementById('entryContent').value = entry.content;
            document.getElementById('editorTitle').textContent = 'Edit Entry';
            document.getElementById('deleteBtn').style.display = 'block';
            
            // Select mood
            if (entry.mood) {
                document.querySelectorAll('.mood-btn').forEach(btn => {
                    btn.classList.remove('selected');
                    if (btn.dataset.mood === entry.mood) {
                        btn.classList.add('selected');
                        selectedMood = entry.mood;
                    }
                });
            }
        }

        // Select mood
        function selectMood(btn) {
            document.querySelectorAll('.mood-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            selectedMood = btn.dataset.mood;
        }

        // New entry
        function newEntry() {
            currentEntry = null;
            clearForm();
            document.getElementById('editorTitle').textContent = 'New Entry';
            document.getElementById('deleteBtn').style.display = 'none';
            
            // Clear selection
            document.querySelectorAll('.entry-item').forEach(item => {
                item.classList.remove('selected');
            });
        }

        // Clear form
        function clearForm() {
            document.getElementById('journalForm').reset();
            document.querySelectorAll('.mood-btn').forEach(btn => {
                btn.classList.remove('selected');
            });
            selectedMood = null;
        }

        // Delete entry
        async function deleteEntry() {
            if (!currentEntry) return;
            
            if (!confirm('Delete this entry?')) return;
            
            try {
                await apiRequest(`/journal/${currentEntry.id}`, {
                    method: 'DELETE'
                });
                
                allEntries = allEntries.filter(e => e.id !== currentEntry.id);
                displayEntries();
                clearForm();
                document.getElementById('editorTitle').textContent = 'New Entry';
                document.getElementById('deleteBtn').style.display = 'none';
                showNotification('Entry deleted', 'success');
            } catch (error) {
                console.error('Failed to delete entry:', error);
                showNotification('Failed to delete entry', 'error');
            }
        }

        // Handle form submission
        document.getElementById('journalForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const title = document.getElementById('entryTitle').value.trim();
            const content = document.getElementById('entryContent').value.trim();
            
            if (!title || !content) {
                showNotification('Please enter both title and content', 'error');
                return;
            }
            
            const entryData = {
                title: title,
                content: content,
                mood: selectedMood
            };
            
            try {
                let response;
                if (currentEntry) {
                    // Update existing entry
                    response = await apiRequest(`/journal/${currentEntry.id}`, {
                        method: 'PUT',
                        body: JSON.stringify(entryData)
                    });
                } else {
                    // Create new entry
                    response = await apiRequest('/journal', {
                        method: 'POST',
                        body: JSON.stringify(entryData)
                    });
                }
                
                if (currentEntry) {
                    // Update in local array
                    const index = allEntries.findIndex(e => e.id === currentEntry.id);
                    allEntries[index] = response.entry;
                } else {
                    // Add to local array
                    allEntries.push(response.entry);
                }
                
                displayEntries();
                clearForm();
                document.getElementById('editorTitle').textContent = 'New Entry';
                document.getElementById('deleteBtn').style.display = 'none';
                showNotification('Entry saved', 'success');
            } catch (error) {
                console.error('Failed to save entry:', error);
                showNotification('Failed to save entry', 'error');
            }
        });

        // Search functionality
        document.getElementById('searchInput').addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase();
            const entries = document.querySelectorAll('.entry-item');
            
            entries.forEach(entry => {
                const title = entry.querySelector('.entry-title').textContent.toLowerCase();
                const content = entry.querySelector('.entry-preview').textContent.toLowerCase();
                
                if (title.includes(query) || content.includes(query)) {
                    entry.style.display = 'block';
                } else {
                    entry.style.display = 'none';
                }
            });
        });

        function navigateToPage(page) {
            window.location.href = page;
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            loadEntries();
        });
    </script>
</body>
</html>
EOF

find /home/pi/zoe -name "*.py" -type f | head -10
find /home/pi/zoe -name "main.py" -o -name "app.py" -o -name "server.py" | head -5
find /home/pi/zoe -path "*/services/*" -name "*.py" | grep -E "(main|app|server)" | head -10
find /home/pi/zoe/services/zoe-core/routers -name "*.py" | head -10
ls -la /home/pi/zoe/services/zoe-core/routers/ | grep -E "(calendar|lists|memory|settings|journal|workflows)"
grep -r "calendar\|journal\|workflows" /home/pi/zoe/services/zoe-core/routers/ | head -10
cd /home/pi/zoe/services/zoe-core && python -c "from main import app; print('✅ All routers loaded successfully')"
ps aux | grep python | grep -v grep
curl -s http://192.168.1.60:8000/health
curl -s http://192.168.1.60:8000/ | head -5
curl -v http://192.168.1.60:8000/ 2>&1 | head -10
curl -s http://192.168.1.60:8000/ | jq .
curl -s http://192.168.1.60:8000/health | jq .
netstat -tlnp | grep :8080
curl -s http://192.168.1.60:8080/ | head -5
curl -s http://192.168.1.60:8000/api/weather | jq .
curl -v http://192.168.1.60:8000/api/weather 2>&1 | head -10
curl -L -s http://192.168.1.60:8000/api/weather | jq .
ps aux | grep "main:app" | grep -v grep
pkill -f "main:app"
curl -s http://192.168.1.60:8000/api/weather/location | jq .
curl -s http://192.168.1.60:8000/api/weather | jq .
curl -v http://192.168.1.60:8000/api/weather 2>&1 | head -15
curl -L -s http://192.168.1.60:8000/api/weather | jq .
curl -H "Origin: http://192.168.1.60:8080" -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: content-type" -X OPTIONS http://192.168.1.60:8000/api/weather/ -v
curl -H "Origin: http://192.168.1.60:8080" http://192.168.1.60:8000/api/weather/ | jq .
curl -s http://192.168.1.60:8000/api/chat -X POST -H "Content-Type: application/json" -d '{"message": "Hello"}' | jq .
curl -s http://192.168.1.60:8000/api/chat -X POST -H "Content-Type: application/json" -d '{"message": "Hello", "context": "main_chat"}' | jq .
curl -s http://192.168.1.60:8000/api/chat -X POST -H "Content-Type: application/json" -d '{"message": "Hello", "context": {"mode": "main_chat"}}' | jq .
curl -s http://192.168.1.60:8080/css/glass.css | head -5
grep -n "apiRequest" /home/pi/zoe/services/zoe-ui/dist/index.html
curl -s http://192.168.1.60:8080/js/common.js | head -5
curl -s http://192.168.1.60:8000/api/chat -X POST -H "Content-Type: application/json" -H "Origin: http://192.168.1.60:8080" -d '{"message": "Hello Zoe", "context": {"mode": "main_chat"}}' | jq .
curl -s http://192.168.1.60:8000/api/weather/ -H "Origin: http://192.168.1.60:8080" | jq .
curl -s http://192.168.1.60:8000/health | jq .
curl -s http://192.168.1.60:8000/api/ | jq .
curl -s http://192.168.1.60:8000/api/health | jq .
curl -s http://192.168.1.60:8000/api/health -H "Origin: http://192.168.1.60:8080" | jq .
curl -H "Origin: http://192.168.1.60:8080" -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: content-type" -X OPTIONS http://192.168.1.60:8000/api/health -v
. "/home/pi/.cursor-server/bin/2f2737de9aa376933d975ae30290447c910fdf40/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh"
cd /home/pi/zoe && docker-compose ps
cd /home/pi/zoe && sqlite3 data/developer_tasks.db "SELECT id, title, status, created_at, last_executed_at, execution_count FROM dynamic_tasks ORDER BY created_at DESC LIMIT 10;"
cd /home/pi/zoe && curl -s http://localhost:8000/api/developer/tasks/list | jq .
cd /home/pi/zoe && curl -s http://localhost:8000/api/developer/tasks/ | jq .
cd /home/pi/zoe && curl -s http://localhost:8000/docs | grep -i "developer.*tasks" | head -5
cd /home/pi/zoe && docker logs zoe-core --tail 20
cd /home/pi/zoe && curl -s http://localhost:8000/api/developer/tasks/list
cd /home/pi/zoe && docker exec zoe-core python -c "from routers import developer_tasks; print('Router imported successfully')"
cd /home/pi/zoe && docker exec zoe-core python -c "import sys; sys.path.append('/app'); from routers.developer_tasks import router; print('Router prefix:', router.prefix); print('Routes:', [route.path for route in router.routes])"
cd /home/pi/zoe && docker restart zoe-core
cd /home/pi/zoe && sleep 10 && curl -s http://localhost:8000/api/developer/tasks/list | jq .
cd /home/pi/zoe && docker logs zoe-core --tail 30
cd /home/pi/zoe && docker restart zoe-core
cd /home/pi/zoe && sleep 10 && curl -s http://localhost:8000/api/developer/tasks/list | jq .
cd /home/pi/zoe && curl -s "http://localhost:8080/developer/tasks.html" | grep -i "completed" | head -3
cd /home/pi/zoe && sqlite3 data/developer_tasks.db "SELECT title, objective, status FROM dynamic_tasks WHERE status = 'completed' AND created_at >= '2025-09-09 12:00:00' ORDER BY created_at DESC;"
cd /home/pi/zoe && curl -s http://localhost:8000/api/developer/status | jq .
cd /home/pi/zoe && curl -s "http://localhost:8080/developer/settings.html" | grep -i "guardrails\|resources\|learning\|aider\|n8n\|wakeword\|sessions\|metrics" | head -5
cd /home/pi && git status
cd /home/pi && git add .
cd /home/pi && git commit -m "Update project documentation and development scripts

- Updated documentation structure and content
- Enhanced development scripts for autonomous system
- Added new documentation files including README and current state
- Cleaned up cursor server cache and history files
- Updated project instructions and context documentation"
cd /home/pi && git push origin main
cd /home/pi && git remote -v
cd /home/pi && git log --oneline -5
cd /home/pi && /home/pi/zoe/scripts/utilities/push_to_github.sh
. "/home/pi/.cursor-server/bin/2f2737de9aa376933d975ae30290447c910fdf40/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh"
. "/home/pi/.cursor-server/bin/2f2737de9aa376933d975ae30290447c910fdf40/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh"
cd /home/pi/zoe && python scripts/add_enhanced_tasks.py
cd /home/pi/zoe && python -c "
import sqlite3
conn = sqlite3.connect('data/developer_tasks.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM dynamic_tasks')
count = cursor.fetchone()[0]
print(f'Total tasks in database: {count}')
cursor.execute('SELECT title, priority FROM dynamic_tasks ORDER BY created_at DESC LIMIT 10')
recent = cursor.fetchall()
print('\nRecent tasks:')
for title, priority in recent:
    print(f'  - {title} ({priority})')
conn.close()
"
find /home/pi/zoe/services/zoe-ui/dist/developer -name "*.html" -exec grep -l "redirect" {} \;
find /home/pi/zoe -name ".htaccess" -o -name "nginx.conf" -o -name "apache.conf" 2>/dev/null
ls -la /usr/share/nginx/html/developer/ 2>/dev/null || echo "Directory not found"
find /home/pi -name "tools.html" -type f 2>/dev/null
docker ps | grep zoe
docker inspect zoe-ui | grep -A 10 -B 10 "Mounts"
docker inspect zoe-ui | grep -A 20 "Mounts"
docker restart zoe-ui
find /home/pi/zoe/services/zoe-ui/dist -name "*.html" -exec grep -l "redirect" {} \;
curl -s http://localhost:8080/developer/tools.html | head -20
curl -s http://localhost:8080/developer/monitor.html | head -20
curl -s http://localhost:8080/developer/backups.html | head -20
curl -s http://localhost:8080/developer/tools.html | grep -i "redirect\|meta.*refresh" | head -5
curl -s http://localhost:8080/developer/monitor.html | grep -i "redirect\|meta.*refresh" | head -5
curl -s http://localhost:8080/developer/backups.html | grep -i "redirect\|meta.*refresh" | head -5
curl -s http://localhost:8080/developer/tools.html | wc -l
find /home/pi/zoe -name "tools.html" -type f
ls -la /home/pi/zoe/services/zoe-ui/dist/developer/tools.html
docker logs zoe-ui --tail 20
curl -s http://localhost:8080/developer/ | grep -i "script" | head -5
curl -s http://localhost:8080/developer/test-nav.html | head -10
find /home/pi/zoe -name "*.html" -exec grep -l "meta.*refresh\|window\.location\|document\.location" {} \; 2>/dev/null | grep -E "(tools|monitor|backup)" | head -10
curl -I http://localhost:8080/developer/tools.html
docker exec zoe-ui nginx -s reload
curl -s http://localhost:8080/developer/aider.html | head -20
curl -s http://localhost:8080/developer/aider.html | grep -i "task-context\|task-list" | head -5
curl -s http://localhost:8080/developer/tasks.html | grep -i "aider-btn" | head -3
curl -s http://localhost:8080/developer/tasks.html | grep -i "taskDetailsModal\|showTaskDetails" | head -3
curl -s http://localhost:8080/developer/aider.html | grep -i "task-context\|loadTasks" | head -3
curl -s http://localhost:8080/developer/aider.html | grep -n "Developer</span>\|API_BASE_CANDIDATES\|/api/aider/sessions\|/api/developer/tasks" | cat
curl -s http://localhost:8080/developer/aider.html | grep -n "/api/developer/tasks\|/api/aider/chat\|/api/aider/session\|/api/aider/sessions" | cat
curl -s http://localhost:8080/developer/aider.html | grep -n "/api/aider/chat" | cat
curl -s -o /dev/null -w "%{http_code}" http://192.168.1.60:8000/api/developer/tasks
curl -s -w "%{http_code}" http://192.168.1.60:8000/api/developer/tasks | tail -1
curl -I http://192.168.1.60:8000/api/developer/tasks 2>/dev/null | head -1
curl -I http://192.168.1.60:8000/api/aider/sessions 2>/dev/null | head -1
curl -s http://localhost:8080/developer/aider.html | grep -n "Developer</span>\|credentials.*include\|demo response" | head -5
curl -s http://192.168.1.60:8000/api/developer/tasks/ | head -20
curl -s http://192.168.1.60:8000/api/developer/tasks/list | head -10
curl -s http://localhost:8080/developer/aider.html | grep -n "/api/developer/tasks/list" | head -3
curl -s -w "%{http_code}" http://192.168.1.60:8000/api/aider/sessions
curl -I http://192.168.1.60:8000/api/aider/sessions 2>/dev/null | head -1
curl -s http://localhost:8080/developer/aider.html | grep -n "dynamically discovers\|Aider API not available" | head -3
curl -s http://localhost:8080/test-api-discovery.html | grep -A 5 -B 5 "192.168.1.60\|localhost"
curl -s http://192.168.1.60:8000/api/developer/tasks/ | grep -o '"endpoints":[^}]*' | head -1
curl -s http://localhost:8080/developer/aider.html | grep -n "task-search\|task-list-container\|filterTasks" | head -5
. "/home/pi/.cursor-server/bin/2f2737de9aa376933d975ae30290447c910fdf40/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh"
curl -X POST http://192.168.1.60:8000/api/creator/create -H "Content-Type: application/json" -d '{"request": "test page"}' -v
curl http://192.168.1.60:8000/ -v
curl http://192.168.1.60:8000/ | cat
curl http://192.168.1.60:8000/ --silent
ps aux | grep -E "(python|uvicorn|fastapi)" | grep -v grep
ps aux | grep "uvicorn main:app --host 0.0.0.0 --port 8000" | grep -v grep
curl -X POST http://192.168.1.60:8000/api/creator/create -H "Content-Type: application/json" -d '{"request": "test calendar page"}' | cat
curl http://192.168.1.60:8000/api/creator/status | cat
curl http://192.168.1.60:8000/api/creator/status --silent
curl -s http://192.168.1.60:8000/api/creator/status
cd /home/pi/zoe/services/zoe-core && python -c "import requests; print(requests.get('http://192.168.1.60:8000/api/creator/status').text)"
ls -la /app/generated/ 2>/dev/null || echo "Generated directory not found on local system"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Test the creator endpoint
response = requests.post('http://192.168.1.60:8000/api/creator/create', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps({'request': 'Calendar Page Test'}))
print('Response status:', response.status_code)
print('Response:', response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests

# Test the creator status endpoint
response = requests.get('http://192.168.1.60:8000/api/creator/status')
print('Status response:', response.json())
"
curl -s http://192.168.1.60:8000/api/calendar/events
curl -s http://192.168.1.60:8000/api/calendar/status
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Test creating a new calendar event
event_data = {
    'title': 'Test Calendar Event',
    'start_date': '2025-09-12',
    'start_time': '14:00',
    'category': 'personal'
}

response = requests.post('http://192.168.1.60:8000/api/calendar/events', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(event_data))
print('Create event response:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Test updating the event
update_data = {
    'title': 'Updated Calendar Event',
    'start_date': '2025-09-12',
    'start_time': '15:00',
    'category': 'work'
}

response = requests.put('http://192.168.1.60:8000/api/calendar/events/5', 
                       headers={'Content-Type': 'application/json'},
                       data=json.dumps(update_data))
print('Update event response:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests

# Test deleting the event
response = requests.delete('http://192.168.1.60:8000/api/calendar/events/5')
print('Delete event response:', response.status_code, response.json())
"
curl -s http://192.168.1.60:8000/api/health
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
response = requests.get('http://192.168.1.60:8000/api/health')
print('Health check response:', response.status_code, response.json())
"
curl -s http://192.168.1.60:8000/api/lists | head -20
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
try:
    response = requests.get('http://192.168.1.60:8000/api/lists')
    print('Lists API response:', response.status_code)
    if response.status_code == 200:
        print('Lists data:', response.json())
    else:
        print('Response:', response.text)
except Exception as e:
    print('Error:', e)
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
try:
    response = requests.get('http://192.168.1.60:8000/api/lists/tasks')
    print('Tasks API response:', response.status_code)
    if response.status_code == 200:
        data = response.json()
        print('Tasks data:', data)
    else:
        print('Response:', response.text)
except Exception as e:
    print('Error:', e)
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Create a sample task list
task_data = {
    'list_type': 'tasks',
    'category': 'personal',
    'name': 'Daily Tasks',
    'items': [
        {'text': 'Review project proposal', 'priority': 'high', 'completed': False},
        {'text': 'Buy groceries', 'priority': 'medium', 'completed': False},
        {'text': 'Call dentist', 'priority': 'low', 'completed': False},
        {'text': 'Finish presentation', 'priority': 'high', 'completed': False},
        {'text': 'Exercise for 30 minutes', 'priority': 'medium', 'completed': False}
    ]
}

response = requests.post('http://192.168.1.60:8000/api/lists/tasks', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(task_data))
print('Created task list:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
response = requests.get('http://192.168.1.60:8000/api/lists/tasks')
print('Tasks loaded:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Task lists:', data)
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Test calendar settings API
response = requests.get('http://192.168.1.60:8000/api/settings/calendar')
print('Calendar settings response:', response.status_code)
if response.status_code == 200:
    print('Settings:', response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Test saving calendar settings
settings_data = {
    'settings': {
        'workHours': {'start': '08:00', 'end': '18:00'},
        'defaultView': 'day',
        'showWorkTasks': True,
        'showPersonalTasks': False,
        'showAllDayEvents': True,
        'timeSlotInterval': 30,
        'syncFrequency': 'realtime'
    }
}

response = requests.post('http://192.168.1.60:8000/api/settings/calendar', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(settings_data))
print('Save settings response:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests

# Verify settings were saved
response = requests.get('http://192.168.1.60:8000/api/settings/calendar')
print('Updated settings:', response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Create a work task list
work_task_data = {
    'list_type': 'tasks',
    'category': 'work',
    'name': 'Work Tasks',
    'items': [
        {'text': 'Review quarterly reports', 'priority': 'high', 'completed': False},
        {'text': 'Prepare presentation slides', 'priority': 'medium', 'completed': False},
        {'text': 'Team meeting preparation', 'priority': 'high', 'completed': False},
        {'text': 'Update project documentation', 'priority': 'low', 'completed': False}
    ]
}

response = requests.post('http://192.168.1.60:8000/api/lists/tasks', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(work_task_data))
print('Created work task list:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
response = requests.get('http://192.168.1.60:8000/api/lists/tasks')
print('All task lists:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Task lists count:', data['count'])
    for list_item in data['lists']:
        print(f\"- {list_item['name']} ({list_item['category']}): {len(list_item['items'])} tasks\")
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Create an all-day event
event_data = {
    'title': 'Team Meeting Day',
    'start_date': '2025-09-12',
    'start_time': None,
    'all_day': True,
    'category': 'work'
}

response = requests.post('http://192.168.1.60:8000/api/calendar/events', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(event_data))
print('Created all-day event:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Create a regular timed event
event_data = {
    'title': 'Client Call',
    'start_date': '2025-09-12',
    'start_time': '14:00',
    'all_day': False,
    'category': 'work'
}

response = requests.post('http://192.168.1.60:8000/api/calendar/events', 
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(event_data))
print('Created timed event:', response.status_code, response.json())
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Check if calendar events are loading
response = requests.get('http://192.168.1.60:8000/api/calendar/events')
print('Calendar events:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Events count:', data['count'])
    for event in data['events']:
        print(f'- {event[\"title\"]} at {event[\"start_time\"] or \"All Day\"} on {event[\"start_date\"]}')

print()

# Check if tasks are loading
response = requests.get('http://192.168.1.60:8000/api/lists/tasks')
print('Tasks:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Task lists count:', data['count'])
    for list_item in data['lists']:
        print(f'- {list_item[\"name\"]}: {len(list_item[\"items\"])} tasks')
"
cd /home/pi/zoe/services/zoe-core && python -c "
import requests
import json

# Check calendar events response structure
response = requests.get('http://192.168.1.60:8000/api/calendar/events')
print('Calendar events:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Response keys:', list(data.keys()))
    print('Events:', len(data.get('events', [])))
    for event in data.get('events', [])[:3]:  # Show first 3 events
        print(f'- {event[\"title\"]} at {event.get(\"start_time\", \"All Day\")} on {event[\"start_date\"]}')

print()

# Check tasks response structure  
response = requests.get('http://192.168.1.60:8000/api/lists/tasks')
print('Tasks:', response.status_code)
if response.status_code == 200:
    data = response.json()
    print('Response keys:', list(data.keys()))
    print('Lists count:', data.get('count', 0))
    for list_item in data.get('lists', [])[:2]:  # Show first 2 lists
        print(f'- {list_item[\"name\"]}: {len(list_item.get(\"items\", []))} tasks')
"
cd /home/pi/zoe/services/zoe-core && python -c "
# Test current time functionality
from datetime import datetime
import json

now = datetime.now()
current_hour = now.hour
current_minute = now.minute

print(f'Current time: {current_hour:02d}:{current_minute:02d}')
print(f'Previous hour: {current_hour - 1:02d}:00')
print(f'Next hour: {current_hour + 1:02d}:00')

# Test calendar settings
settings = {
    'workHours': {'start': '09:00', 'end': '17:00'},
    'timeSlotInterval': 60,
    'defaultView': 'day',
    'showWorkTasks': True,
    'showPersonalTasks': True,
    'showAllDayEvents': True
}

print('\\nCalendar settings:')
print(json.dumps(settings, indent=2))
"
free -h | cat
ps -eo pid,ppid,comm,%mem,%cpu,rss --sort=-%mem | head -n 25 | cat
ollama ps | cat
ps -p 1665 -o pid,cmd --no-headers | cat
ps -p 1845 -o pid,cmd --no-headers | cat
ps -p 60363 -o pid,cmd --no-headers | cat
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | cat && echo '---- PORTS ----' && ss -tulpen | awk '($5 ~ /:(9001|9002|5678|11434)$/){print}' | cat && echo '---- HTTP PROBES ----' && (curl -s -o /dev/null -w '11434:%{http_code}\n' http://127.0.0.1:11434/ || echo '11434:unreachable') && (curl -s -o /dev/null -w '5678:%{http_code}\n' http://127.0.0.1:5678/ || echo '5678:unreachable') && (curl -s -o /dev/null -w '9001:%{http_code}\n' http://127.0.0.1:9001/ || echo '9001:unreachable') && (curl -s -o /dev/null -w '9002:%{http_code}\n' http://127.0.0.1:9002/ || echo '9002:unreachable')
docker restart zoe-core | cat
curl -s http://localhost:8000/api/developer/health | jq . || echo "Health endpoint failed"
docker restart zoe-core | cat
curl -s http://localhost:8000/api/developer/health | jq . || echo "Health check failed"
docker restart zoe-core | cat
curl -s http://localhost:8000/api/developer/health | jq . || echo "Health check failed"
curl -s http://localhost:8000/api/developer/activity | jq . || echo "Activity endpoint failed"
docker restart zoe-core | cat
curl -s http://localhost:8000/api/developer/health | jq .
curl -s http://localhost:8000/api/developer/health | jq '.services | to_entries[] | {key: .key, status: .value.status}' | cat
timeout 5 curl -s http://localhost:8000/api/developer/health | head -5
docker ps | grep zoe-core
docker logs zoe-core --tail 10
docker restart zoe-core | cat
timeout 3 curl -s http://localhost:8000/api/developer/health | jq '.status' || echo "Still timing out"
cd /home/pi/zoe && docker-compose ps
curl -s http://localhost:8000/api/aider/health | jq .
curl -s http://localhost:8000/api/aider/models | jq .
docker-compose restart zoe-core
sleep 5 && curl -s http://localhost:8000/api/aider/sessions | jq .
curl -s -X POST http://localhost:8000/api/aider/session -H "Content-Type: application/json" -d '{"task_id": null, "files": []}' | jq .
curl -s http://localhost:8000/api/developer/aider/health | jq .
which aider
ls -la /home/pi/zoe/aider_env/bin/ | grep aider
/home/pi/zoe/aider_env/bin/aider --version
docker exec zoe-core which aider
docker exec zoe-core ls -la /home/pi/zoe/aider_env/bin/ | grep aider
docker-compose restart zoe-core
sleep 5 && curl -s http://localhost:8000/api/developer/aider/health | jq .
docker exec zoe-core find /home -name "aider" -type f 2>/dev/null
docker exec zoe-core ls -la /home/pi/zoe/aider_env/bin/
docker exec zoe-core /home/pi/zoe/aider_env/bin/aider --version
docker exec zoe-core /home/pi/zoe/tools/aider-env/bin/aider --version
docker exec zoe-core file /home/pi/zoe/aider_env/bin/aider
docker exec zoe-core head -5 /home/pi/zoe/aider_env/bin/aider
docker exec zoe-core which python3
docker exec zoe-core python3 /home/pi/zoe/aider_env/bin/aider --version
docker exec zoe-core pip3 list | grep aider
docker exec zoe-core pip3 install aider
docker exec zoe-core pip3 uninstall aider -y && docker exec zoe-core pip3 install aider==0.86.1
docker exec zoe-core pip3 install aider
docker exec zoe-core aider --version
docker exec zoe-core python3 -m aider --version
docker-compose restart zoe-core
sleep 5 && curl -s http://localhost:8000/api/developer/aider/health | jq .
curl -s http://localhost:8000/api/developer/aider/models | jq .
curl -s -X POST http://localhost:8000/api/aider/session -H "Content-Type: application/json" -d '{"task_id": null, "files": []}' | jq .
curl -s http://localhost:8000/api/aider/sessions | jq .
top -bn1 | head -20
free -h
ps aux --sort=-%mem | head -10
ps aux --sort=-%cpu | head -10
ps aux | grep ollama
ollama list
sudo ls -la /root/.ollama/models/blobs/
sudo find /root/.ollama -name "*.json" -exec cat {} \;
sudo ls -la /root/.ollama/
sudo find /root -name "*ollama*" -type d 2>/dev/null
sudo cat /proc/145736/cmdline | tr '\0' ' '
which ollama
sudo find /usr -name "*ollama*" 2>/dev/null
sudo find /var -name "*ollama*" 2>/dev/null
docker ps
sudo ls -la /var/lib/docker/volumes/zoe_zoe_ollama_data/_data/models/
sudo find /var/lib/docker/volumes/zoe_zoe_ollama_data/_data/models/manifests -name "*.json" -exec cat {} \;
docker exec zoe-ollama ollama list
docker exec zoe-ollama ps aux
docker logs zoe-ollama --tail 20
docker exec zoe-ollama ls -la /root/.ollama/models/blobs/ | grep 74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45
docker exec zoe-ollama find /root/.ollama -name "*74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45*" -exec ls -la {} \;
docker exec zoe-ollama find /root/.ollama -name "*.json" -exec grep -l "74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45" {} \;
docker exec zoe-ollama find /root/.ollama -name "*.json" -exec cat {} \; | grep -A5 -B5 "74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45"
docker exec zoe-ollama ls -lh /root/.ollama/models/blobs/sha256-74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45
docker exec zoe-ollama ollama ps
. "/home/pi/.cursor-server/bin/2f2737de9aa376933d975ae30290447c910fdf40/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh"
