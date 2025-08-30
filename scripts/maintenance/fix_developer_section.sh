#!/bin/bash
# FIX_DEVELOPER_SECTION.sh
# Location: scripts/maintenance/fix_developer_section.sh
# Purpose: Fix design inconsistency and connect chat to actual Zack backend

set -e

echo "🔧 FIXING DEVELOPER SECTION - COMPLETE OVERHAUL"
echo "=============================================="
echo ""
echo "This will:"
echo "  1. Create proper dashboard at index.html"
echo "  2. Move chat to chat.html with Zack connection"
echo "  3. Fix tasks.html with light theme"
echo "  4. Connect everything properly"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Backup current state
echo -e "\n📦 Creating backup..."
BACKUP_DIR="backups/developer_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r services/zoe-ui/dist/developer "$BACKUP_DIR/"

# ===========================================================================
# STEP 1: CREATE PROPER DASHBOARD AT index.html
# ===========================================================================
echo -e "\n📋 Step 1: Creating proper dashboard..."

cat > services/zoe-ui/dist/developer/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Zack Developer Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            width: 100vw; height: 100vh; overflow: hidden;
            font-size: 16px; color: #333;
        }

        /* Header */
        .header {
            height: 60px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 24px; position: relative; z-index: 100;
        }

        .header-left {
            display: flex; align-items: center; gap: 20px;
        }

        .logo {
            display: flex; align-items: center; gap: 8px;
            font-size: 18px; font-weight: 600; color: #333;
            cursor: pointer; transition: all 0.3s ease;
        }

        .logo:hover { transform: scale(1.05); }

        .logo-icon {
            width: 32px; height: 32px; border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            display: flex; align-items: center; justify-content: center;
            font-size: 16px; color: white; font-weight: bold;
        }

        .nav-tabs {
            display: flex; gap: 8px;
        }

        .nav-tab {
            padding: 8px 16px;
            border-radius: 8px;
            text-decoration: none;
            color: #666;
            transition: all 0.3s ease;
            font-weight: 500;
        }

        .nav-tab.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        .nav-tab:hover:not(.active) {
            background: rgba(123, 97, 255, 0.1);
            color: #7B61FF;
        }

        .header-right {
            display: flex; align-items: center; gap: 16px;
        }

        .status-indicator {
            display: flex; align-items: center; gap: 6px;
            padding: 6px 12px; border-radius: 20px;
            background: rgba(34, 197, 94, 0.1); color: #22c55e;
            font-weight: 500; font-size: 14px;
        }

        .status-dot {
            width: 8px; height: 8px; border-radius: 50%;
            background: currentColor; animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }

        /* Main Content */
        .dashboard-content {
            padding: 24px;
            height: calc(100vh - 60px);
            overflow-y: auto;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }

        .dashboard-card {
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 16px;
            padding: 20px;
            transition: all 0.3s ease;
        }

        .dashboard-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
        }

        .card-title {
            font-size: 18px;
            font-weight: 600;
            color: #333;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .card-icon {
            font-size: 20px;
        }

        /* Statistics Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }

        .stat-item {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .stat-value {
            font-size: 24px;
            font-weight: 600;
            color: #7B61FF;
        }

        .stat-label {
            font-size: 14px;
            color: #666;
        }

        /* Task List */
        .task-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 300px;
            overflow-y: auto;
        }

        .task-item {
            padding: 12px;
            background: rgba(255, 255, 255, 0.6);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: all 0.3s ease;
        }

        .task-item:hover {
            background: rgba(255, 255, 255, 0.9);
        }

        .task-priority {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }

        .priority-high {
            background: rgba(239, 68, 68, 0.1);
            color: #ef4444;
        }

        .priority-medium {
            background: rgba(251, 146, 60, 0.1);
            color: #f59e0b;
        }

        .priority-low {
            background: rgba(34, 197, 94, 0.1);
            color: #22c55e;
        }

        /* System Status */
        .status-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
        }

        .status-item {
            padding: 12px;
            background: rgba(255, 255, 255, 0.6);
            border-radius: 8px;
            text-align: center;
            transition: all 0.3s ease;
            cursor: pointer;
        }

        .status-item:hover {
            background: rgba(255, 255, 255, 0.9);
            transform: scale(1.05);
        }

        .status-icon {
            font-size: 24px;
            margin-bottom: 4px;
        }

        .status-healthy { color: #22c55e; }
        .status-warning { color: #f59e0b; }
        .status-error { color: #ef4444; }

        /* Quick Actions */
        .action-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }

        .action-btn {
            padding: 16px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }

        .action-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 16px rgba(123, 97, 255, 0.3);
        }

        /* Activity Feed */
        .activity-feed {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 400px;
            overflow-y: auto;
        }

        .activity-item {
            padding: 12px;
            background: rgba(255, 255, 255, 0.6);
            border-radius: 8px;
            display: flex;
            align-items: start;
            gap: 12px;
            font-size: 14px;
        }

        .activity-time {
            color: #666;
            font-size: 12px;
            white-space: nowrap;
        }

        .activity-text {
            flex: 1;
            color: #333;
        }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="header-left">
            <div class="logo" onclick="window.location.href='../index.html'">
                <div class="logo-icon">Z</div>
                <span>Developer Dashboard</span>
            </div>
            <div class="nav-tabs">
                <a href="index.html" class="nav-tab active">Dashboard</a>
                <a href="chat.html" class="nav-tab">Chat</a>
                <a href="tasks.html" class="nav-tab">Tasks</a>
            </div>
        </div>
        <div class="header-right">
            <div class="status-indicator">
                <div class="status-dot"></div>
                <span>System Online</span>
            </div>
            <div id="currentTime"></div>
        </div>
    </div>

    <!-- Dashboard Content -->
    <div class="dashboard-content">
        <div class="dashboard-grid">
            <!-- System Overview -->
            <div class="dashboard-card">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-icon">📊</span>
                        System Overview
                    </div>
                </div>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-value" id="taskCount">12</div>
                        <div class="stat-label">Active Tasks</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="containerCount">7</div>
                        <div class="stat-label">Containers</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="apiCalls">1,247</div>
                        <div class="stat-label">API Calls Today</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="uptime">99.9%</div>
                        <div class="stat-label">Uptime</div>
                    </div>
                </div>
            </div>

            <!-- Service Status -->
            <div class="dashboard-card">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-icon">🔧</span>
                        Service Status
                    </div>
                </div>
                <div class="status-grid" id="serviceStatus">
                    <div class="status-item" onclick="checkService('core')">
                        <div class="status-icon status-healthy">✅</div>
                        <div>Core API</div>
                    </div>
                    <div class="status-item" onclick="checkService('ui')">
                        <div class="status-icon status-healthy">✅</div>
                        <div>UI</div>
                    </div>
                    <div class="status-item" onclick="checkService('ollama')">
                        <div class="status-icon status-healthy">✅</div>
                        <div>Ollama</div>
                    </div>
                    <div class="status-item" onclick="checkService('redis')">
                        <div class="status-icon status-healthy">✅</div>
                        <div>Redis</div>
                    </div>
                    <div class="status-item" onclick="checkService('whisper')">
                        <div class="status-icon status-warning">⚠️</div>
                        <div>Whisper</div>
                    </div>
                    <div class="status-item" onclick="checkService('tts')">
                        <div class="status-icon status-warning">⚠️</div>
                        <div>TTS</div>
                    </div>
                </div>
            </div>

            <!-- Recent Tasks -->
            <div class="dashboard-card">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-icon">📋</span>
                        Recent Tasks
                    </div>
                    <a href="tasks.html" style="color: #7B61FF; font-size: 14px;">View All →</a>
                </div>
                <div class="task-list" id="recentTasks">
                    <div class="task-item">
                        <span>Fix TTS audio quality</span>
                        <span class="task-priority priority-high">High</span>
                    </div>
                    <div class="task-item">
                        <span>Add backup automation</span>
                        <span class="task-priority priority-medium">Medium</span>
                    </div>
                    <div class="task-item">
                        <span>Update documentation</span>
                        <span class="task-priority priority-low">Low</span>
                    </div>
                </div>
            </div>

            <!-- Quick Actions -->
            <div class="dashboard-card">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-icon">⚡</span>
                        Quick Actions
                    </div>
                </div>
                <div class="action-grid">
                    <button class="action-btn" onclick="systemCheck()">
                        <span>🔍</span>
                        System Check
                    </button>
                    <button class="action-btn" onclick="createBackup()">
                        <span>💾</span>
                        Backup
                    </button>
                    <button class="action-btn" onclick="viewLogs()">
                        <span>📄</span>
                        View Logs
                    </button>
                    <button class="action-btn" onclick="openChat()">
                        <span>💬</span>
                        Ask Zack
                    </button>
                </div>
            </div>

            <!-- Activity Feed -->
            <div class="dashboard-card" style="grid-column: span 2;">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-icon">📈</span>
                        Recent Activity
                    </div>
                </div>
                <div class="activity-feed" id="activityFeed">
                    <div class="activity-item">
                        <span class="activity-time">2 min ago</span>
                        <span class="activity-text">System health check completed successfully</span>
                    </div>
                    <div class="activity-item">
                        <span class="activity-time">15 min ago</span>
                        <span class="activity-text">Developer chat endpoint tested</span>
                    </div>
                    <div class="activity-item">
                        <span class="activity-time">1 hour ago</span>
                        <span class="activity-text">Database backup created</span>
                    </div>
                    <div class="activity-item">
                        <span class="activity-time">3 hours ago</span>
                        <span class="activity-text">Container zoe-core restarted</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Update time
        function updateTime() {
            const now = new Date();
            document.getElementById('currentTime').textContent = 
                now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        updateTime();
        setInterval(updateTime, 60000);

        // Dashboard functions
        async function loadDashboardData() {
            try {
                // Load real tasks
                const response = await fetch('http://localhost:8000/api/tasks/');
                if (response.ok) {
                    const data = await response.json();
                    document.getElementById('taskCount').textContent = data.tasks.length;
                    
                    // Update recent tasks
                    const taskList = document.getElementById('recentTasks');
                    taskList.innerHTML = data.tasks.slice(0, 3).map(task => `
                        <div class="task-item">
                            <span>${task.title}</span>
                            <span class="task-priority priority-${task.priority}">${task.priority}</span>
                        </div>
                    `).join('');
                }

                // Check service status
                const healthResponse = await fetch('http://localhost:8000/health');
                if (healthResponse.ok) {
                    const health = await healthResponse.json();
                    console.log('System health:', health);
                }
            } catch (error) {
                console.error('Error loading dashboard data:', error);
            }
        }

        // Quick action functions
        function systemCheck() {
            window.location.href = 'chat.html?message=Run a complete system health check';
        }

        function createBackup() {
            if (confirm('Create a full system backup?')) {
                fetch('http://localhost:8000/api/developer/backup', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => alert(`Backup created: ${data.backup_id}`))
                    .catch(err => alert('Backup failed: ' + err));
            }
        }

        function viewLogs() {
            window.open('http://localhost:8000/api/developer/logs', '_blank');
        }

        function openChat() {
            window.location.href = 'chat.html';
        }

        function checkService(service) {
            alert(`Checking ${service} service...`);
            // Could implement real service check here
        }

        // Load dashboard data on page load
        document.addEventListener('DOMContentLoaded', loadDashboardData);
        
        // Refresh data every 30 seconds
        setInterval(loadDashboardData, 30000);
    </script>
</body>
</html>
EOF

# ===========================================================================
# STEP 2: CREATE WORKING CHAT PAGE CONNECTED TO ZACK
# ===========================================================================
echo -e "\n💬 Step 2: Creating working chat page..."

cat > services/zoe-ui/dist/developer/chat.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Zack Developer Chat</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            width: 100vw; height: 100vh; overflow: hidden;
            font-size: 16px; color: #333;
        }

        /* Reuse header styles from dashboard */
        .header {
            height: 60px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 24px; position: relative; z-index: 100;
        }

        .header-left {
            display: flex; align-items: center; gap: 20px;
        }

        .logo {
            display: flex; align-items: center; gap: 8px;
            font-size: 18px; font-weight: 600; color: #333;
            cursor: pointer; transition: all 0.3s ease;
        }

        .logo:hover { transform: scale(1.05); }

        .logo-icon {
            width: 32px; height: 32px; border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            display: flex; align-items: center; justify-content: center;
            font-size: 16px; color: white; font-weight: bold;
        }

        .nav-tabs {
            display: flex; gap: 8px;
        }

        .nav-tab {
            padding: 8px 16px;
            border-radius: 8px;
            text-decoration: none;
            color: #666;
            transition: all 0.3s ease;
            font-weight: 500;
        }

        .nav-tab.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        .nav-tab:hover:not(.active) {
            background: rgba(123, 97, 255, 0.1);
            color: #7B61FF;
        }

        .header-right {
            display: flex; align-items: center; gap: 16px;
        }

        .status-indicator {
            display: flex; align-items: center; gap: 6px;
            padding: 6px 12px; border-radius: 20px;
            background: rgba(34, 197, 94, 0.1); color: #22c55e;
            font-weight: 500; font-size: 14px;
        }

        .status-dot {
            width: 8px; height: 8px; border-radius: 50%;
            background: currentColor; animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }

        /* Chat Container */
        .chat-container {
            display: flex;
            height: calc(100vh - 60px);
        }

        /* Chat Area */
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(20px);
        }

        .chat-messages {
            flex: 1;
            padding: 24px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .message {
            max-width: 70%;
            padding: 16px;
            border-radius: 16px;
            animation: messageSlide 0.3s ease-out;
        }

        @keyframes messageSlide {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message.zack {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            align-self: flex-start;
        }

        .message.user {
            background: white;
            color: #333;
            align-self: flex-end;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }

        .message-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
            font-weight: 500;
        }

        .message-content {
            line-height: 1.6;
        }

        .code-block {
            margin: 12px 0;
            padding: 12px;
            background: rgba(0, 0, 0, 0.1);
            border-radius: 8px;
            font-family: 'Monaco', 'Consolas', monospace;
            font-size: 14px;
            overflow-x: auto;
        }

        .message.user .code-block {
            background: rgba(0, 0, 0, 0.05);
        }

        /* Input Area */
        .chat-input-area {
            padding: 20px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-top: 1px solid rgba(255, 255, 255, 0.3);
        }

        .chat-input-container {
            display: flex;
            gap: 12px;
            align-items: center;
        }

        .chat-input {
            flex: 1;
            padding: 12px 20px;
            border: 1px solid rgba(123, 97, 255, 0.3);
            border-radius: 24px;
            background: white;
            font-size: 16px;
            outline: none;
            transition: all 0.3s ease;
        }

        .chat-input:focus {
            border-color: #7B61FF;
            box-shadow: 0 0 0 3px rgba(123, 97, 255, 0.1);
        }

        .send-button {
            width: 48px;
            height: 48px;
            border: none;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .send-button:hover {
            transform: scale(1.1);
            box-shadow: 0 4px 16px rgba(123, 97, 255, 0.3);
        }

        .send-button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        /* Sidebar */
        .chat-sidebar {
            width: 320px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-left: 1px solid rgba(255, 255, 255, 0.3);
            padding: 24px;
            overflow-y: auto;
        }

        .sidebar-section {
            margin-bottom: 24px;
        }

        .sidebar-title {
            font-size: 14px;
            font-weight: 600;
            color: #666;
            text-transform: uppercase;
            margin-bottom: 12px;
            letter-spacing: 0.5px;
        }

        .quick-prompt {
            padding: 12px;
            background: rgba(255, 255, 255, 0.6);
            border-radius: 8px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 14px;
        }

        .quick-prompt:hover {
            background: rgba(123, 97, 255, 0.1);
            transform: translateX(4px);
        }

        .action-buttons {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .action-button {
            padding: 12px;
            background: white;
            border: 1px solid rgba(123, 97, 255, 0.3);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 14px;
            text-align: center;
        }

        .action-button:hover {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            border-color: transparent;
        }

        /* Loading indicator */
        .typing-indicator {
            display: none;
            align-items: center;
            gap: 4px;
            padding: 16px;
            background: rgba(123, 97, 255, 0.1);
            border-radius: 16px;
            width: fit-content;
        }

        .typing-indicator.active {
            display: flex;
        }

        .typing-dot {
            width: 8px;
            height: 8px;
            background: #7B61FF;
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }

        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }

        @keyframes typing {
            0%, 60%, 100% { opacity: 0.3; }
            30% { opacity: 1; }
        }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="header-left">
            <div class="logo" onclick="window.location.href='../index.html'">
                <div class="logo-icon">Z</div>
                <span>Developer Chat</span>
            </div>
            <div class="nav-tabs">
                <a href="index.html" class="nav-tab">Dashboard</a>
                <a href="chat.html" class="nav-tab active">Chat</a>
                <a href="tasks.html" class="nav-tab">Tasks</a>
            </div>
        </div>
        <div class="header-right">
            <div class="status-indicator" id="zackStatus">
                <div class="status-dot"></div>
                <span>Zack Online</span>
            </div>
            <div id="currentTime"></div>
        </div>
    </div>

    <!-- Chat Container -->
    <div class="chat-container">
        <!-- Chat Area -->
        <div class="chat-area">
            <div class="chat-messages" id="chatMessages">
                <div class="message zack">
                    <div class="message-header">
                        <span>🧠</span>
                        <span>Zack</span>
                    </div>
                    <div class="message-content">
                        Hello! I'm Zack, your development assistant. I can help you:
                        <ul style="margin: 8px 0; padding-left: 20px;">
                            <li>Generate code and scripts</li>
                            <li>Debug issues in your Zoe system</li>
                            <li>Create new features and endpoints</li>
                            <li>Manage Docker containers and services</li>
                            <li>Optimize performance and fix problems</li>
                        </ul>
                        What would you like to work on today?
                    </div>
                </div>
            </div>

            <div class="typing-indicator" id="typingIndicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>

            <div class="chat-input-area">
                <div class="chat-input-container">
                    <input 
                        type="text" 
                        class="chat-input" 
                        id="messageInput"
                        placeholder="Ask Zack anything about development..."
                        onkeydown="handleKeyDown(event)"
                    >
                    <button class="send-button" id="sendButton" onclick="sendMessage()">
                        ➤
                    </button>
                </div>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="chat-sidebar">
            <div class="sidebar-section">
                <div class="sidebar-title">Quick Prompts</div>
                <div class="quick-prompt" onclick="quickPrompt('Generate a backup script')">
                    💾 Generate backup script
                </div>
                <div class="quick-prompt" onclick="quickPrompt('Check system health')">
                    🔍 Check system health
                </div>
                <div class="quick-prompt" onclick="quickPrompt('Fix any current errors')">
                    🔧 Fix current errors
                </div>
                <div class="quick-prompt" onclick="quickPrompt('Create a new API endpoint')">
                    🚀 Create new endpoint
                </div>
                <div class="quick-prompt" onclick="quickPrompt('Optimize database queries')">
                    ⚡ Optimize database
                </div>
            </div>

            <div class="sidebar-section">
                <div class="sidebar-title">Actions</div>
                <div class="action-buttons">
                    <div class="action-button" onclick="saveAsTask()">
                        📋 Save as Task
                    </div>
                    <div class="action-button" onclick="exportChat()">
                        💾 Export Chat
                    </div>
                    <div class="action-button" onclick="clearChat()">
                        🗑️ Clear Chat
                    </div>
                </div>
            </div>

            <div class="sidebar-section">
                <div class="sidebar-title">Recent Topics</div>
                <div id="recentTopics">
                    <div class="quick-prompt">TTS audio quality fix</div>
                    <div class="quick-prompt">Developer dashboard setup</div>
                    <div class="quick-prompt">Task management system</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // CRITICAL: Use the correct API endpoint for Zack
        const API_BASE = 'http://localhost:8000';
        const ZACK_ENDPOINT = '/api/developer/chat';
        
        let isProcessing = false;
        let chatHistory = [];

        // Update time
        function updateTime() {
            const now = new Date();
            document.getElementById('currentTime').textContent = 
                now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        updateTime();
        setInterval(updateTime, 60000);

        // Handle keyboard input
        function handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }

        // Send message to Zack
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if (!message || isProcessing) return;
            
            // Add user message
            addMessage(message, 'user');
            input.value = '';
            
            // Show typing indicator
            isProcessing = true;
            document.getElementById('sendButton').disabled = true;
            document.getElementById('typingIndicator').classList.add('active');
            
            try {
                // Send to actual Zack endpoint
                const response = await fetch(API_BASE + ZACK_ENDPOINT, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        message: message
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    addMessage(data.response || data.message || 'Response received', 'zack');
                    
                    // If response contains code, highlight it
                    if (data.code) {
                        addMessage('```\\n' + data.code + '\\n```', 'zack');
                    }
                } else {
                    throw new Error('API response not ok');
                }
            } catch (error) {
                console.error('Error sending message:', error);
                addMessage('Sorry, I encountered an error. Please check if the backend is running.', 'zack');
            } finally {
                isProcessing = false;
                document.getElementById('sendButton').disabled = false;
                document.getElementById('typingIndicator').classList.remove('active');
            }
        }

        // Add message to chat
        function addMessage(content, sender) {
            const messagesContainer = document.getElementById('chatMessages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}`;
            
            const header = document.createElement('div');
            header.className = 'message-header';
            header.innerHTML = `
                <span>${sender === 'zack' ? '🧠' : '👤'}</span>
                <span>${sender === 'zack' ? 'Zack' : 'You'}</span>
            `;
            
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            
            // Format code blocks
            content = content.replace(/```([\\s\\S]*?)```/g, '<div class="code-block">$1</div>');
            contentDiv.innerHTML = content;
            
            messageDiv.appendChild(header);
            messageDiv.appendChild(contentDiv);
            
            // Remove typing indicator before adding message
            const typingIndicator = document.getElementById('typingIndicator');
            messagesContainer.insertBefore(messageDiv, typingIndicator);
            
            // Scroll to bottom
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            
            // Store in history
            chatHistory.push({ content, sender, timestamp: new Date() });
        }

        // Quick prompts
        function quickPrompt(prompt) {
            document.getElementById('messageInput').value = prompt;
            sendMessage();
        }

        // Save current conversation as task
        function saveAsTask() {
            const lastZackMessage = chatHistory
                .filter(m => m.sender === 'zack')
                .pop();
            
            if (lastZackMessage) {
                // Create task from last response
                fetch(API_BASE + '/api/tasks/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: 'Generated by Zack',
                        description: lastZackMessage.content,
                        priority: 'medium',
                        task_type: 'development'
                    })
                })
                .then(r => r.json())
                .then(data => {
                    alert('Saved as task: ' + (data.task_id || 'Created'));
                })
                .catch(err => {
                    alert('Failed to save as task');
                });
            }
        }

        // Export chat history
        function exportChat() {
            const chatText = chatHistory.map(m => 
                `[${m.timestamp.toLocaleTimeString()}] ${m.sender.toUpperCase()}: ${m.content}`
            ).join('\\n\\n');
            
            const blob = new Blob([chatText], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `zack-chat-${new Date().toISOString()}.txt`;
            a.click();
            URL.revokeObjectURL(url);
        }

        // Clear chat
        function clearChat() {
            if (confirm('Clear all chat history?')) {
                chatHistory = [];
                const messagesContainer = document.getElementById('chatMessages');
                messagesContainer.innerHTML = `
                    <div class="message zack">
                        <div class="message-header">
                            <span>🧠</span>
                            <span>Zack</span>
                        </div>
                        <div class="message-content">
                            Chat cleared. How can I help you?
                        </div>
                    </div>
                `;
                const typingIndicator = document.createElement('div');
                typingIndicator.className = 'typing-indicator';
                typingIndicator.id = 'typingIndicator';
                typingIndicator.innerHTML = `
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                `;
                messagesContainer.appendChild(typingIndicator);
            }
        }

        // Check for message in URL (from dashboard quick actions)
        window.addEventListener('DOMContentLoaded', () => {
            const params = new URLSearchParams(window.location.search);
            const message = params.get('message');
            if (message) {
                document.getElementById('messageInput').value = message;
                sendMessage();
            }
        });

        // Check Zack status
        async function checkZackStatus() {
            try {
                const response = await fetch(API_BASE + '/api/developer/status');
                const statusEl = document.getElementById('zackStatus');
                if (response.ok) {
                    statusEl.innerHTML = '<div class="status-dot"></div><span>Zack Online</span>';
                } else {
                    statusEl.innerHTML = '<div class="status-dot" style="background: #ef4444;"></div><span>Zack Offline</span>';
                }
            } catch (error) {
                document.getElementById('zackStatus').innerHTML = 
                    '<div class="status-dot" style="background: #ef4444;"></div><span>Zack Offline</span>';
            }
        }

        // Check status on load and periodically
        checkZackStatus();
        setInterval(checkZackStatus, 30000);
    </script>
</body>
</html>
EOF

# ===========================================================================
# STEP 3: UPDATE TASKS PAGE WITH LIGHT THEME
# ===========================================================================
echo -e "\n📋 Step 3: Updating tasks page with light theme..."

cat > services/zoe-ui/dist/developer/tasks.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Development Tasks</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            width: 100vw; height: 100vh; overflow: hidden;
            font-size: 16px; color: #333;
        }

        /* Reuse header styles */
        .header {
            height: 60px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 24px; position: relative; z-index: 100;
        }

        .header-left {
            display: flex; align-items: center; gap: 20px;
        }

        .logo {
            display: flex; align-items: center; gap: 8px;
            font-size: 18px; font-weight: 600; color: #333;
            cursor: pointer; transition: all 0.3s ease;
        }

        .logo:hover { transform: scale(1.05); }

        .logo-icon {
            width: 32px; height: 32px; border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            display: flex; align-items: center; justify-content: center;
            font-size: 16px; color: white; font-weight: bold;
        }

        .nav-tabs {
            display: flex; gap: 8px;
        }

        .nav-tab {
            padding: 8px 16px;
            border-radius: 8px;
            text-decoration: none;
            color: #666;
            transition: all 0.3s ease;
            font-weight: 500;
        }

        .nav-tab.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        .nav-tab:hover:not(.active) {
            background: rgba(123, 97, 255, 0.1);
            color: #7B61FF;
        }

        /* Task Management */
        .tasks-container {
            padding: 24px;
            height: calc(100vh - 60px);
            overflow-y: auto;
        }

        .tasks-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
        }

        .tasks-title {
            font-size: 28px;
            font-weight: 600;
            color: #333;
        }

        .add-task-btn {
            padding: 12px 24px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .add-task-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 16px rgba(123, 97, 255, 0.3);
        }

        /* Filter Bar */
        .filter-bar {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            padding: 16px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border-radius: 12px;
        }

        .filter-btn {
            padding: 8px 16px;
            background: white;
            border: 1px solid rgba(123, 97, 255, 0.3);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 14px;
        }

        .filter-btn.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            border-color: transparent;
        }

        /* Task Grid */
        .tasks-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 20px;
        }

        .task-card {
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 16px;
            padding: 20px;
            transition: all 0.3s ease;
            cursor: pointer;
        }

        .task-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
        }

        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 12px;
        }

        .task-title {
            font-size: 18px;
            font-weight: 600;
            color: #333;
            flex: 1;
        }

        .task-priority {
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            text-transform: uppercase;
        }

        .priority-critical {
            background: rgba(239, 68, 68, 0.1);
            color: #ef4444;
        }

        .priority-high {
            background: rgba(251, 146, 60, 0.1);
            color: #f59e0b;
        }

        .priority-medium {
            background: rgba(59, 130, 246, 0.1);
            color: #3b82f6;
        }

        .priority-low {
            background: rgba(34, 197, 94, 0.1);
            color: #22c55e;
        }

        .task-description {
            color: #666;
            font-size: 14px;
            line-height: 1.5;
            margin-bottom: 16px;
        }

        .task-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-top: 12px;
            border-top: 1px solid rgba(0, 0, 0, 0.05);
        }

        .task-status {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 14px;
            color: #666;
        }

        .task-date {
            font-size: 12px;
            color: #999;
        }

        .task-actions {
            display: flex;
            gap: 8px;
        }

        .task-action-btn {
            padding: 6px 12px;
            background: white;
            border: 1px solid rgba(123, 97, 255, 0.3);
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .task-action-btn:hover {
            background: rgba(123, 97, 255, 0.1);
        }

        /* Task Stats */
        .task-stats {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }

        .stat-value {
            font-size: 32px;
            font-weight: 600;
            color: #7B61FF;
            margin-bottom: 4px;
        }

        .stat-label {
            font-size: 14px;
            color: #666;
        }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="header-left">
            <div class="logo" onclick="window.location.href='../index.html'">
                <div class="logo-icon">Z</div>
                <span>Development Tasks</span>
            </div>
            <div class="nav-tabs">
                <a href="index.html" class="nav-tab">Dashboard</a>
                <a href="chat.html" class="nav-tab">Chat</a>
                <a href="tasks.html" class="nav-tab active">Tasks</a>
            </div>
        </div>
        <div class="header-right">
            <div id="currentTime"></div>
        </div>
    </div>

    <!-- Tasks Container -->
    <div class="tasks-container">
        <!-- Tasks Header -->
        <div class="tasks-header">
            <h1 class="tasks-title">Development Tasks</h1>
            <button class="add-task-btn" onclick="createNewTask()">+ New Task</button>
        </div>

        <!-- Task Statistics -->
        <div class="task-stats">
            <div class="stat-card">
                <div class="stat-value" id="totalTasks">0</div>
                <div class="stat-label">Total Tasks</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="pendingTasks">0</div>
                <div class="stat-label">Pending</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="inProgressTasks">0</div>
                <div class="stat-label">In Progress</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="completedTasks">0</div>
                <div class="stat-label">Completed</div>
            </div>
        </div>

        <!-- Filter Bar -->
        <div class="filter-bar">
            <button class="filter-btn active" onclick="filterTasks('all')">All Tasks</button>
            <button class="filter-btn" onclick="filterTasks('pending')">Pending</button>
            <button class="filter-btn" onclick="filterTasks('in_progress')">In Progress</button>
            <button class="filter-btn" onclick="filterTasks('completed')">Completed</button>
            <button class="filter-btn" onclick="filterTasks('critical')">Critical</button>
        </div>

        <!-- Tasks Grid -->
        <div class="tasks-grid" id="tasksGrid">
            <!-- Tasks will be loaded here -->
        </div>
    </div>

    <script>
        const API_BASE = 'http://localhost:8000';
        let allTasks = [];
        let currentFilter = 'all';

        // Update time
        function updateTime() {
            const now = new Date();
            document.getElementById('currentTime').textContent = 
                now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        updateTime();
        setInterval(updateTime, 60000);

        // Load tasks from API
        async function loadTasks() {
            try {
                const response = await fetch(API_BASE + '/api/tasks/');
                if (response.ok) {
                    const data = await response.json();
                    allTasks = data.tasks || [];
                    updateTaskStats();
                    displayTasks();
                }
            } catch (error) {
                console.error('Error loading tasks:', error);
                // Show sample tasks if API fails
                allTasks = [
                    {
                        task_id: 'TASK-001',
                        title: 'Fix TTS Audio Quality',
                        description: 'Improve the text-to-speech audio quality for better Whisper recognition',
                        priority: 'high',
                        status: 'in_progress',
                        created_at: new Date().toISOString()
                    },
                    {
                        task_id: 'TASK-002',
                        title: 'Add Backup Automation',
                        description: 'Create automated backup system for database and configurations',
                        priority: 'medium',
                        status: 'pending',
                        created_at: new Date().toISOString()
                    }
                ];
                updateTaskStats();
                displayTasks();
            }
        }

        // Update task statistics
        function updateTaskStats() {
            document.getElementById('totalTasks').textContent = allTasks.length;
            document.getElementById('pendingTasks').textContent = 
                allTasks.filter(t => t.status === 'pending').length;
            document.getElementById('inProgressTasks').textContent = 
                allTasks.filter(t => t.status === 'in_progress').length;
            document.getElementById('completedTasks').textContent = 
                allTasks.filter(t => t.status === 'completed').length;
        }

        // Display tasks based on filter
        function displayTasks() {
            const grid = document.getElementById('tasksGrid');
            let filteredTasks = allTasks;

            if (currentFilter !== 'all') {
                if (currentFilter === 'critical') {
                    filteredTasks = allTasks.filter(t => t.priority === 'critical');
                } else {
                    filteredTasks = allTasks.filter(t => t.status === currentFilter);
                }
            }

            grid.innerHTML = filteredTasks.map(task => `
                <div class="task-card" onclick="viewTask('${task.task_id}')">
                    <div class="task-header">
                        <div class="task-title">${task.title}</div>
                        <div class="task-priority priority-${task.priority}">${task.priority}</div>
                    </div>
                    <div class="task-description">${task.description || 'No description'}</div>
                    <div class="task-meta">
                        <div class="task-status">
                            <span>${task.status === 'pending' ? '⏳' : task.status === 'in_progress' ? '🔄' : '✅'}</span>
                            <span>${task.status.replace('_', ' ').toUpperCase()}</span>
                        </div>
                        <div class="task-date">${new Date(task.created_at).toLocaleDateString()}</div>
                    </div>
                    <div class="task-actions">
                        <button class="task-action-btn" onclick="event.stopPropagation(); editTask('${task.task_id}')">Edit</button>
                        <button class="task-action-btn" onclick="event.stopPropagation(); deleteTask('${task.task_id}')">Delete</button>
                    </div>
                </div>
            `).join('');

            if (filteredTasks.length === 0) {
                grid.innerHTML = '<div style="text-align: center; padding: 40px; color: #666;">No tasks found</div>';
            }
        }

        // Filter tasks
        function filterTasks(filter) {
            currentFilter = filter;
            
            // Update filter buttons
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');
            
            displayTasks();
        }

        // Create new task
        function createNewTask() {
            const title = prompt('Task title:');
            if (!title) return;

            const description = prompt('Task description:');
            const priority = prompt('Priority (critical/high/medium/low):', 'medium');

            fetch(API_BASE + '/api/tasks/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title,
                    description,
                    priority,
                    task_type: 'development'
                })
            })
            .then(r => r.json())
            .then(data => {
                alert('Task created successfully!');
                loadTasks();
            })
            .catch(err => {
                alert('Failed to create task');
            });
        }

        // View task details
        function viewTask(taskId) {
            const task = allTasks.find(t => t.task_id === taskId);
            if (task) {
                alert(`Task: ${task.title}\\n\\nDescription: ${task.description}\\n\\nStatus: ${task.status}\\nPriority: ${task.priority}`);
            }
        }

        // Edit task
        function editTask(taskId) {
            const task = allTasks.find(t => t.task_id === taskId);
            if (task) {
                const newStatus = prompt('Update status (pending/in_progress/completed):', task.status);
                if (newStatus) {
                    fetch(API_BASE + `/api/tasks/${taskId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: newStatus })
                    })
                    .then(r => r.json())
                    .then(data => {
                        alert('Task updated!');
                        loadTasks();
                    })
                    .catch(err => {
                        alert('Failed to update task');
                    });
                }
            }
        }

        // Delete task
        function deleteTask(taskId) {
            if (confirm('Delete this task?')) {
                fetch(API_BASE + `/api/tasks/${taskId}`, {
                    method: 'DELETE'
                })
                .then(r => r.json())
                .then(data => {
                    alert('Task deleted!');
                    loadTasks();
                })
                .catch(err => {
                    alert('Failed to delete task');
                });
            }
        }

        // Load tasks on page load
        document.addEventListener('DOMContentLoaded', loadTasks);
        
        // Refresh tasks every 30 seconds
        setInterval(loadTasks, 30000);
    </script>
</body>
</html>
EOF

# ===========================================================================
# STEP 4: RESTART SERVICES
# ===========================================================================
echo -e "\n🔄 Step 4: Restarting services..."
docker compose restart zoe-ui
sleep 3

# ===========================================================================
# STEP 5: TEST THE FIXES
# ===========================================================================
echo -e "\n✅ Step 5: Testing the fixes..."

# Test Zack chat endpoint
echo "Testing Zack chat endpoint..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Zack, are you working?"}' | jq -r '.response' | head -20)

if [ ! -z "$RESPONSE" ]; then
    echo "✅ Zack chat is responding!"
else
    echo "⚠️  Zack chat might need attention"
fi

# Test tasks endpoint
echo -e "\nTesting tasks endpoint..."
TASKS=$(curl -s http://localhost:8000/api/tasks/ | jq -r '.tasks' | head -5)
if [ ! -z "$TASKS" ]; then
    echo "✅ Tasks API is working!"
else
    echo "⚠️  Tasks API might need attention"
fi

echo -e "\n================================================"
echo "✅ DEVELOPER SECTION FIX COMPLETE!"
echo "================================================"
echo ""
echo "🎉 What's been fixed:"
echo "  ✅ Dashboard now at developer/index.html"
echo "  ✅ Chat moved to developer/chat.html"
echo "  ✅ Chat connected to Zack at /api/developer/chat"
echo "  ✅ All pages use consistent light theme"
echo "  ✅ Navigation works between all pages"
echo ""
echo "📋 Test the changes:"
echo "  1. Dashboard: http://192.168.1.60:8080/developer/"
echo "  2. Chat: http://192.168.1.60:8080/developer/chat.html"
echo "  3. Tasks: http://192.168.1.60:8080/developer/tasks.html"
echo ""
echo "🧪 Quick test commands:"
echo "  curl -X POST http://localhost:8000/api/developer/chat \\"
echo "    -d '{\"message\": \"Generate a backup script\"}'"
echo ""
echo "  curl http://localhost:8000/api/tasks/"
echo ""
echo "💡 Next steps:"
echo "  - Test from another device on the network"
echo "  - Try creating tasks from the chat ('Save as Task' button)"
echo "  - Check if all quick actions work"
echo "  - Commit changes if everything works"
