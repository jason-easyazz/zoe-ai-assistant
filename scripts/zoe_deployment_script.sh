#!/bin/bash

# Zoe v3.1 - Complete Deployment Script
# This script deploys the new interface and backend updates

set -e  # Exit on any error

echo "🚀 Starting Zoe v3.1 deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Check we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    error "Please run this script from your zoe-v31 directory (~/zoe-v31)"
fi

log "Found docker-compose.yml, proceeding with deployment"

# Step 1: Backup existing files
log "📦 Creating backups..."
BACKUP_TIME=$(date +%Y%m%d_%H%M%S)

# Backup frontend
if [ -f "services/zoe-ui/dist/index.html" ]; then
    cp services/zoe-ui/dist/index.html "services/zoe-ui/dist/index.html.backup.$BACKUP_TIME"
    log "✅ Frontend backed up"
else
    warn "No existing frontend found, creating directory structure"
    mkdir -p services/zoe-ui/dist
fi

# Backup backend
if [ -f "services/zoe-core/main.py" ]; then
    cp services/zoe-core/main.py "services/zoe-core/main.py.backup.$BACKUP_TIME"
    log "✅ Backend backed up"
else
    warn "No existing backend main.py found"
fi

# Step 2: Deploy new frontend
log "🎨 Deploying new frontend interface..."

cat > services/zoe-ui/dist/index.html << 'FRONTEND_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe - Personal AI Companion</title>
    <style>
        /* Reset and Base Styles */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            overflow: hidden;
            height: 100vh;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
        }

        /* Orb Mode - Default State */
        .orb-mode {
            position: fixed;
            inset: 0;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            z-index: 1000;
            opacity: 1;
            transition: opacity 0.8s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .orb-mode.hidden {
            opacity: 0;
            pointer-events: none;
        }

        /* Fluid Orb Animation */
        .fluid-orb {
            position: relative;
            width: 200px;
            height: 200px;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            cursor: pointer;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(123, 97, 255, 0.3);
            transition: all 0.3s ease;
        }

        .fluid-orb:hover {
            transform: scale(1.05);
            box-shadow: 0 25px 80px rgba(123, 97, 255, 0.4);
        }

        .fluid-layer {
            position: absolute;
            inset: 0;
            border-radius: 50%;
            background: radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.3) 0%, transparent 70%);
        }

        .fluid-layer:nth-child(1) {
            animation: breathe 3s ease-in-out infinite;
        }

        .fluid-layer:nth-child(2) {
            animation: breathe 3s ease-in-out infinite 1s;
            opacity: 0.6;
        }

        .fluid-layer:nth-child(3) {
            animation: breathe 3s ease-in-out infinite 2s;
            opacity: 0.3;
        }

        @keyframes breathe {
            0%, 100% { transform: scale(1) rotate(0deg); opacity: 0.8; }
            50% { transform: scale(1.1) rotate(180deg); opacity: 1; }
        }

        /* Animation States */
        .fluid-orb.listening .fluid-layer {
            animation: pulse 0.6s ease-in-out infinite;
        }

        .fluid-orb.speaking .fluid-layer {
            animation: vibrate 0.3s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 0.8; }
            50% { transform: scale(1.2); opacity: 1; }
        }

        @keyframes vibrate {
            0%, 100% { transform: translateX(0) scale(1); }
            25% { transform: translateX(-2px) scale(1.05); }
            75% { transform: translateX(2px) scale(0.95); }
        }

        .orb-hint {
            margin-top: 30px;
            color: #666;
            font-size: 18px;
            font-weight: 300;
            text-align: center;
            opacity: 0.8;
        }

        /* Main Interface */
        .main-interface {
            position: fixed;
            inset: 0;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.8s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 100;
        }

        .main-interface.active {
            opacity: 1;
            pointer-events: all;
        }

        /* Navigation Bar */
        .nav-bar {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 70px;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(40px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px;
            z-index: 110;
        }

        .nav-left {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .mini-orb {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            cursor: pointer;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s ease;
        }

        .mini-orb:hover {
            transform: scale(1.1);
        }

        .mini-orb .fluid-layer {
            animation: breathe 2s ease-in-out infinite;
        }

        .nav-menu {
            display: flex;
            gap: 5px;
        }

        .nav-item {
            padding: 8px 16px;
            color: #666;
            font-size: 14px;
            font-weight: 400;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }

        .nav-item:hover {
            background: rgba(123, 97, 255, 0.1);
            color: #7B61FF;
        }

        .nav-item.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        .close-btn {
            width: 40px;
            height: 40px;
            border: none;
            background: rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            color: #666;
            font-size: 18px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .close-btn:hover {
            background: rgba(123, 97, 255, 0.2);
            color: #7B61FF;
            transform: scale(1.05);
        }

        /* Main Container - More centered layout */
        .main-container {
            max-width: 900px;
            margin: 0 auto;
            padding: 120px 40px 60px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 40px;
        }

        /* Time Display - Top left under nav */
        .time-display {
            position: fixed;
            top: 85px;
            left: 25px;
            z-index: 90;
        }

        .current-time {
            font-size: 24px;
            font-weight: 300;
            color: #333;
            margin-bottom: 2px;
        }

        .current-date {
            font-size: 14px;
            color: #666;
        }

        /* Weather Widget - Top right under nav */
        .weather-widget {
            position: fixed;
            top: 85px;
            right: 25px;
            z-index: 90;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .weather-icon {
            font-size: 24px;
        }

        .weather-temp {
            font-size: 24px;
            font-weight: 300;
            color: #333;
        }

        /* Zoe Greeting - More space and centered */
        .zoe-greeting {
            text-align: center;
            margin: 60px 0 80px 0;
        }

        .zoe-greeting h1 {
            font-size: 36px;
            font-weight: 300;
            color: #333;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .zoe-greeting p {
            font-size: 20px;
            color: #666;
            font-weight: 300;
            line-height: 1.4;
        }

        /* Chat Interface - Centered and spaced */
        .chat-container {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 25px;
            padding: 30px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.05);
            transition: all 0.3s ease;
            margin: 0 auto;
            max-width: 600px;
            width: 100%;
        }

        .chat-container:hover {
            background: rgba(255, 255, 255, 0.7);
            box-shadow: 0 25px 80px rgba(0, 0, 0, 0.08);
        }

        .chat-input-group {
            display: flex;
            gap: 20px;
            align-items: center;
            margin-bottom: 25px;
        }

        .chat-input {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            font-size: 18px;
            padding: 18px 25px;
            border-radius: 25px;
            background: rgba(255, 255, 255, 0.5);
            transition: all 0.2s ease;
        }

        .chat-input:focus {
            background: rgba(255, 255, 255, 0.8);
            box-shadow: 0 0 0 2px rgba(123, 97, 255, 0.3);
        }

        .chat-input::placeholder {
            color: #999;
        }

        .voice-btn {
            width: 55px;
            height: 55px;
            border: none;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .voice-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 5px 20px rgba(123, 97, 255, 0.4);
        }

        .voice-btn.active {
            animation: pulse 1s ease-in-out infinite;
            box-shadow: 0 0 30px rgba(123, 97, 255, 0.6);
        }

        /* Quick Actions - Better spacing */
        .quick-actions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 15px;
        }

        .quick-action {
            background: rgba(255, 255, 255, 0.4);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 15px;
            padding: 16px 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            min-height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .quick-action:hover {
            background: rgba(123, 97, 255, 0.1);
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        }

        .quick-action span {
            font-size: 15px;
            color: #333;
            font-weight: 400;
        }

        /* Content Rows - More space */
        .content-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 40px;
            margin-top: 60px;
        }

        .content-card {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 20px;
            padding: 25px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.05);
            transition: all 0.3s ease;
        }

        .content-card:hover {
            background: rgba(255, 255, 255, 0.7);
            transform: translateY(-2px);
            box-shadow: 0 15px 60px rgba(0, 0, 0, 0.08);
        }

        .content-card h3 {
            font-size: 18px;
            font-weight: 500;
            color: #333;
            margin-bottom: 15px;
        }

        /* Task Items */
        .task-item, .event-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 0;
            border-bottom: 1px solid rgba(0, 0, 0, 0.05);
            transition: all 0.2s ease;
        }

        .task-item:last-child, .event-item:last-child {
            border-bottom: none;
        }

        .task-item:hover, .event-item:hover {
            background: rgba(123, 97, 255, 0.05);
            border-radius: 8px;
            padding-left: 15px;
            padding-right: 15px;
        }

        .task-checkbox {
            width: 20px;
            height: 20px;
            border: 2px solid #ddd;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .task-checkbox.checked {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            border-color: #7B61FF;
        }

        .task-checkbox.checked::after {
            content: "✓";
            color: white;
            font-size: 12px;
            font-weight: bold;
        }

        .task-text {
            flex: 1;
            color: #333;
            font-size: 14px;
        }

        .task-text.completed {
            text-decoration: line-through;
            color: #999;
        }

        .event-time {
            color: #7B61FF;
            font-size: 12px;
            font-weight: 500;
        }

        .event-text {
            flex: 1;
            color: #333;
            font-size: 14px;
        }

        /* Status Indicator */
        .status-indicator {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: rgba(255, 255, 255, 0.9);
            padding: 8px 12px;
            border-radius: 20px;
            font-size: 12px;
            color: #666;
            backdrop-filter: blur(10px);
            z-index: 200;
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .status-indicator.show {
            opacity: 1;
        }

        /* Chat Overlay */
        .chat-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(8px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
            opacity: 0;
            transition: all 0.3s ease;
        }

        .chat-overlay.active {
            display: flex;
            opacity: 1;
        }

        .chat-window {
            background: rgba(255, 255, 255, 0.9);
            backdrop-filter: blur(60px);
            border: 1px solid rgba(255, 255, 255, 0.5);
            border-radius: 20px;
            width: 85%;
            max-width: 450px;
            height: 70%;
            max-height: 600px;
            display: flex;
            flex-direction: column;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
        }

        .chat-header {
            padding: 20px 20px 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-shrink: 0;
        }

        .chat-title {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 18px;
            font-weight: 500;
            color: #333;
        }

        .mini-orb-chat {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            position: relative;
            overflow: hidden;
        }

        .close-chat-btn {
            background: rgba(255, 255, 255, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            width: 28px;
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            color: #666;
            font-size: 16px;
        }

        .close-chat-btn:hover {
            background: rgba(255, 0, 0, 0.1);
            color: #ff4444;
        }

        .chat-window .chat-messages {
            display: flex;
            flex-direction: column;
            gap: 12px;
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            min-height: 0;
        }

        .message {
            margin-bottom: 12px;
            display: flex;
            gap: 10px;
        }

        .message.user {
            flex-direction: row-reverse;
        }

        .message-content {
            background: rgba(255, 255, 255, 0.8);
            padding: 12px 16px;
            border-radius: 18px;
            max-width: 70%;
            font-size: 14px;
            line-height: 1.4;
        }

        .message.user .message-content {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
        }

        .message-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 12px;
            font-weight: 500;
            flex-shrink: 0;
        }

        .typing-indicator {
            display: flex;
            gap: 4px;
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.8);
            border-radius: 18px;
            width: fit-content;
        }

        .typing-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #999;
            animation: typing 1.4s infinite ease-in-out;
        }

        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }

        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
            30% { transform: translateY(-10px); opacity: 1; }
        }

        .chat-input-container {
            padding: 15px 20px 20px;
            border-top: 1px solid rgba(255, 255, 255, 0.3);
            flex-shrink: 0;
        }

        .chat-window .chat-input-group {
            background: rgba(255, 255, 255, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 15px;
            padding: 10px 15px;
            margin-bottom: 0;
        }

        .chat-window .chat-input {
            background: transparent;
            border: none;
            padding: 8px 0;
            font-size: 14px;
        }

        .chat-window .voice-btn {
            width: 36px;
            height: 36px;
            font-size: 14px;
        }

        /* Shopping List Overlay - abbreviated for script length */
        .shopping-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(8px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
            opacity: 0;
            transition: all 0.3s ease;
        }

        .shopping-overlay.active {
            display: flex;
            opacity: 1;
        }

        /* Responsive Design - Better mobile spacing */
        @media (max-width: 768px) {
            .main-container {
                padding: 100px 20px 40px;
                gap: 30px;
                justify-content: flex-start;
            }

            .nav-menu {
                display: none;
            }

            .fluid-orb {
                width: 150px;
                height: 150px;
            }
        }
    </style>
</head>
<body>
    <div class="status-indicator" id="statusIndicator">Ready</div>

    <div class="orb-mode" id="orbMode">
        <div class="fluid-orb idle" id="fluidOrb" onclick="enterInterface()">
            <div class="fluid-layer"></div>
            <div class="fluid-layer"></div>
            <div class="fluid-layer"></div>
        </div>
        <div class="orb-hint">Touch to interact or press spacebar to talk</div>
    </div>

    <div class="main-interface" id="mainInterface">
        <div class="nav-bar">
            <div class="nav-left">
                <div class="mini-orb" onclick="exitToOrb()">
                    <div class="fluid-layer"></div>
                </div>
                <div class="nav-menu">
                    <div class="nav-item active" onclick="switchPanel('dashboard')">Dashboard</div>
                    <div class="nav-item" onclick="switchPanel('tasks')">Tasks</div>
                    <div class="nav-item" onclick="switchPanel('calendar')">Calendar</div>
                    <div class="nav-item" onclick="switchPanel('journal')">Journal</div>
                    <div class="nav-item" onclick="switchPanel('shopping')">Shopping</div>
                </div>
            </div>
            <button class="close-btn" onclick="switchPanel('settings')" title="Settings">⚙️</button>
        </div>

        <div class="main-container">
            <div class="time-display">
                <div class="current-time" id="currentTime">Loading...</div>
                <div class="current-date" id="currentDate">Loading...</div>
            </div>

            <div class="weather-widget">
                <div class="weather-icon" id="weatherIcon">🌤️</div>
                <div class="weather-temp" id="weatherTemp">--°</div>
            </div>

            <div class="zoe-greeting">
                <h1>Hi, I'm Zoe</h1>
                <p>Your personal AI companion. How can I help you today?</p>
            </div>

            <div class="chat-container">
                <div class="chat-input-group">
                    <input type="text" class="chat-input" id="chatInput" placeholder="Type your message..." autofocus onclick="openChatWindow()">
                    <button class="voice-btn" id="voiceBtn" onclick="toggleVoice()" title="Voice input">🎤</button>
                </div>

                <div class="quick-actions">
                    <div class="quick-action" onclick="quickAction('journal')">
                        <span>📝 Journal</span>
                    </div>
                    <div class="quick-action" onclick="quickAction('event')">
                        <span>📅 Event</span>
                    </div>
                    <div class="quick-action" onclick="quickAction('task')">
                        <span>✅ Task</span>
                    </div>
                    <div class="quick-action" onclick="quickAction('shopping')">
                        <span>🛒 Shopping</span>
                    </div>
                </div>
            </div>

            <div class="content-row">
                <div class="content-card">
                    <h3>Today's Tasks</h3>
                    <div id="taskList">
                        <div class="task-item">
                            <div class="task-checkbox" onclick="toggleTask(this)"></div>
                            <div class="task-text">Review morning schedule</div>
                        </div>
                    </div>
                </div>

                <div class="content-card">
                    <h3>Upcoming Events</h3>
                    <div id="eventList">
                        <div class="event-item">
                            <div class="event-time">Later</div>
                            <div class="event-text">Backend integration testing</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="chat-overlay" id="chatOverlay">
        <div class="chat-window">
            <div class="chat-header">
                <div class="chat-title">
                    <div class="mini-orb-chat">
                        <div class="fluid-layer"></div>
                    </div>
                    <span>Chat with Zoe</span>
                </div>
                <button class="close-chat-btn" onclick="closeChatWindow()">×</button>
            </div>
            
            <div class="chat-messages" id="chatMessagesOverlay">
                <div class="message assistant">
                    <div class="message-avatar">Z</div>
                    <div class="