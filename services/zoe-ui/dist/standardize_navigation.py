#!/usr/bin/env python3
"""
Standardize navigation across all desktop UI pages to match calendar.html
"""
import re
import os

# Standard nav CSS block
NAV_CSS = '''
        /* Navigation Bar - Standardized */
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
        
        .notifications-btn { 
            background: rgba(255, 255, 255, 0.6); border: 1px solid rgba(255, 255, 255, 0.3); 
            border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; 
            justify-content: center; cursor: pointer; transition: all 0.3s ease; color: #666;
            font-size: 16px; font-weight: bold;
        }
        .notifications-btn:hover { background: rgba(255, 255, 255, 0.8); color: #333; }
        .notifications-btn.has-notifications { animation: notificationPulse 2s ease-in-out infinite; }
        .notifications-btn.has-notifications::after {
            content: ''; position: absolute; top: 6px; right: 6px;
            width: 10px; height: 10px; background: #ff4757; border-radius: 50%;
            border: 2px solid white;
        }
        
        @keyframes notificationPulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.1); }
        }
        
        .api-indicator { 
            font-size: 12px; font-weight: 500; padding: 4px 8px; border-radius: 8px; 
            display: flex; align-items: center; gap: 6px; 
        }
        .api-indicator.online { background: rgba(34, 197, 94, 0.1); color: #22c55e; }
        .api-indicator.offline { background: rgba(239, 68, 68, 0.1); color: #ef4444; }
        .api-indicator.connecting { background: rgba(251, 146, 60, 0.1); color: #ea580c; }
        .api-indicator::before { content: ''; width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .api-indicator.online::before { background: #22c55e; }
        .api-indicator.offline::before { background: #ef4444; }
        .api-indicator.connecting::before { background: #ea580c; }
        
        /* Time/Date Display */
        .time-date-display {
            display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
        }
        .current-time { font-size: 16px; font-weight: 500; color: #333; line-height: 1.2; }
        .current-date { font-size: 11px; color: #666; line-height: 1.2; }

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
            background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent;
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

        /* Notifications Panel */
        .notifications-panel {
            position: fixed; top: 0; right: -400px; width: 400px; height: 100vh;
            background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(20px);
            border-left: 1px solid rgba(255, 255, 255, 0.3); z-index: 1000;
            transition: right 0.3s ease; overflow-y: auto;
        }
        .notifications-panel.open { right: 0; }
        .notifications-header {
            padding: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex; justify-content: space-between; align-items: center; 
        }
        .notifications-title { font-size: 18px; font-weight: 600; color: #333; margin: 0; }
        .notifications-close {
            width: 32px; height: 32px; border-radius: 50%; border: none;
            background: rgba(255, 255, 255, 0.3); color: #666; font-size: 18px;
            cursor: pointer; transition: all 0.3s ease; min-width: 44px; min-height: 44px;
        }
        .notifications-close:hover { background: rgba(255, 255, 255, 0.5); }
        .notifications-content { padding: 20px; display: flex; flex-direction: column; gap: 15px; }
        .notification-item {
            background: rgba(255, 255, 255, 0.6); backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.4); border-radius: 12px;
            padding: 15px; transition: all 0.3s ease; cursor: pointer;
        }
        .notification-item:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1); }
        .notification-item.unread { border-left: 4px solid #7B61FF; }
        .notification-title { font-size: 14px; font-weight: 600; color: #333; margin-bottom: 5px; }
        .notification-meta { font-size: 12px; color: #666; display: flex; gap: 10px; }
        .notification-time { color: #7B61FF; font-weight: 500; }
        .no-notifications { text-align: center; color: #666; font-size: 14px; padding: 40px 20px; }
'''

# Standard nav HTML template (will be customized per page)
def get_nav_html(active_page):
    return f'''    <!-- Navigation -->
    <div class="nav-bar">
        <div class="nav-left">
            <div class="mini-orb" onclick="window.location.href='index.html'"></div>
            <div class="nav-menu">
                <a href="chat.html" class="nav-item{' active' if active_page == 'chat' else ''}">Chat</a>
                <a href="dashboard.html" class="nav-item{' active' if active_page == 'dashboard' else ''}">Dashboard</a>
                <a href="lists.html" class="nav-item{' active' if active_page == 'lists' else ''}">Lists</a>
                <a href="calendar.html" class="nav-item{' active' if active_page == 'calendar' else ''}">Calendar</a>
                <a href="journal.html" class="nav-item{' active' if active_page == 'journal' else ''}">Journal</a>
                <button class="more-nav-btn" onclick="openMoreOverlay()">More</button>
            </div>
        </div>
        <div class="nav-right">
            <div class="api-indicator connecting" id="apiStatus">Connecting</div>
            
            <button class="notifications-btn" onclick="openNotifications()" title="Notifications">💬</button>
            
            <!-- Time/Date Display -->
            <div class="time-date-display">
                <div class="current-time" id="currentTime">Loading...</div>
                <div class="current-date" id="currentDate">Loading...</div>
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
                <div class="more-item" onclick="navigateToPage('developer/index.html')">
                    <div class="more-item-icon">👨‍💻</div>
                    <div class="more-item-label">Developer</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Notifications Panel -->
    <div class="notifications-panel" id="notificationsPanel">
        <div class="notifications-header">
            <h3 class="notifications-title">💬 Notifications</h3>
            <button class="notifications-close" onclick="closeNotifications()">×</button>
        </div>
        <div class="notifications-content" id="notificationsContent">
            <div class="no-notifications">No notifications</div>
        </div>
    </div>
'''

# Helper functions to add to JavaScript
NAV_JS_FUNCTIONS = '''
        // Navigation helper functions
        function openMoreOverlay() {
            document.getElementById('moreOverlay').classList.add('active');
        }
        
        function closeMoreOverlay() {
            document.getElementById('moreOverlay').classList.remove('active');
        }
        
        function navigateToPage(page) {
            window.location.href = page;
        }
        
        function openNotifications() {
            document.getElementById('notificationsPanel').classList.add('open');
            loadNotifications();
            const notificationBtn = document.querySelector('.notifications-btn');
            if (notificationBtn) notificationBtn.classList.remove('has-notifications');
        }
        
        function closeNotifications() {
            document.getElementById('notificationsPanel').classList.remove('open');
        }
        
        async function loadNotifications() {
            try {
                const response = await apiRequest('/reminders/notifications/pending');
                const notifications = response.notifications || [];
                displayNotifications(notifications);
            } catch (error) {
                console.error('Failed to load notifications:', error);
                displayNotifications([]);
            }
        }
        
        function displayNotifications(notifications) {
            const notificationsContent = document.getElementById('notificationsContent');
            const notificationBtn = document.querySelector('.notifications-btn');
            
            if (notifications.length > 0 && notificationBtn) {
                notificationBtn.classList.add('has-notifications');
            } else if (notificationBtn) {
                notificationBtn.classList.remove('has-notifications');
            }
            
            if (notifications.length === 0) {
                notificationsContent.innerHTML = '<div class="no-notifications">No notifications</div>';
                return;
            }
            
            notificationsContent.innerHTML = notifications.map(notification => `
                <div class="notification-item ${notification.is_delivered ? '' : 'unread'}" 
                     onclick="handleNotificationClick(${notification.id})">
                    <div class="notification-title">${notification.message}</div>
                    <div class="notification-meta">
                        <span class="notification-time">${formatNotificationTime(notification.notification_time)}</span>
                    </div>
                </div>
            `).join('');
        }
        
        function formatNotificationTime(timeStr) {
            const date = new Date(timeStr);
            const now = new Date();
            const diff = now - date;
            const minutes = Math.floor(diff / 60000);
            const hours = Math.floor(minutes / 60);
            const days = Math.floor(hours / 24);
            
            if (minutes < 1) return 'Just now';
            if (minutes < 60) return `${minutes}m ago`;
            if (hours < 24) return `${hours}h ago`;
            return `${days}d ago`;
        }
        
        async function handleNotificationClick(notificationId) {
            try {
                await apiRequest(`/reminders/notifications/${notificationId}/deliver`, {
                    method: 'POST'
                });
                loadNotifications();
            } catch (error) {
                console.error('Failed to acknowledge notification:', error);
            }
            closeNotifications();
        }
        
        // Update time/date display
        function updateTimeDate() {
            const now = new Date();
            const timeString = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const dateString = now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
            
            const timeElement = document.getElementById('currentTime');
            const dateElement = document.getElementById('currentDate');
            
            if (timeElement) timeElement.textContent = timeString;
            if (dateElement) dateElement.textContent = dateString;
        }
        
        // Initialize time/date updates
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                updateTimeDate();
                setInterval(updateTimeDate, 60000);
                loadNotifications();
            });
        } else {
            updateTimeDate();
            setInterval(updateTimeDate, 60000);
            loadNotifications();
        }
'''

def update_page(filename, active_page):
    """Update a single page with standard navigation"""
    filepath = filename
    
    if not os.path.exists(filepath):
        print(f'❌ {filename} not found')
        return False
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    print(f'\n🔧 Processing {filename}...')
    
    # 1. Add/update nav CSS (insert before </style> in head)
    # Find the last </style> tag before </head>
    style_pattern = r'(</style>\s*</head>)'
    if re.search(style_pattern, content):
        # Add nav CSS before the closing style tag
        content = re.sub(
            r'(</style>\s*</head>)',
            f'\n{NAV_CSS}\n    </style>\n</head>',
            content,
            count=1
        )
        print('  ✅ Added nav CSS')
    
    # 2. Replace or add navigation HTML
    # Look for existing nav-bar and replace, or add after <body>
    nav_html = get_nav_html(active_page)
    
    if '<div class="nav-bar"' in content or '<div class="nav-bar ' in content:
        # Replace existing nav
        nav_pattern = r'<!-- Navigation -->.*?</div>\s*</div>\s*</div>'
        content = re.sub(nav_pattern, nav_html.strip(), content, flags=re.DOTALL, count=1)
        print('  ✅ Replaced existing nav')
    else:
        # Add after <body>
        content = content.replace('<body>', f'<body>\n{nav_html}', 1)
        print('  ✅ Added new nav')
    
    # 3. Add JavaScript helper functions if not present
    if 'function openMoreOverlay()' not in content:
        # Find where to insert (before closing </script> tag near end)
        script_pattern = r'(</script>\s*</body>)'
        if re.search(script_pattern, content):
            content = re.sub(
                script_pattern,
                f'\n{NAV_JS_FUNCTIONS}\n    </script>\n</body>',
                content,
                count=1
            )
            print('  ✅ Added nav helper functions')
    
    # Write updated content
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f'  ✅ {filename} updated successfully')
    return True

# Process all pages
pages_to_update = {
    'chat.html': 'chat',
    'dashboard.html': 'dashboard',
    'lists.html': 'lists',
    'journal.html': 'journal',
    'memories.html': 'memories',
    'settings.html': 'settings'
}

print('🚀 STANDARDIZING NAVIGATION ACROSS ALL PAGES')
print('=' * 60)

total = len(pages_to_update)
success = 0

for filename, active_page in pages_to_update.items():
    if update_page(filename, active_page):
        success += 1

print('\n' + '=' * 60)
print(f'✅ COMPLETE: {success}/{total} pages updated successfully')
print('\nChanges made to each page:')
print('  • Added standard navigation bar with mini-orb')
print('  • Added Chat/Dashboard/Lists/Calendar/Journal/More menu')
print('  • Added notifications button with indicator')
print('  • Added live time/date display')
print('  • Added More overlay (Memories/Workflows/Settings/Analytics)')
print('  • Added notifications panel')
print('  • Added all helper JavaScript functions')

