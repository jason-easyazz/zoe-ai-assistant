#!/bin/bash

# Zoe v3.1 - Exact Deployment Script for Your Pi
# This script will safely backup and deploy the new interface

echo "🚀 Deploying Zoe v3.1 Interface..."

# Navigate to your zoe directory
cd ~/zoe-v31

# Step 1: Backup current interface
echo "📦 Backing up current interface..."
BACKUP_TIME=$(date +%Y%m%d_%H%M%S)
cp services/zoe-ui/dist/index.html services/zoe-ui/dist/index.html.backup.$BACKUP_TIME
echo "✅ Backup created: index.html.backup.$BACKUP_TIME"

# Step 2: Create the new interface file
echo "🎨 Creating new Zoe v3.1 interface..."

# Create a temporary file with the new interface
cat > /tmp/zoe_new_interface.html << 'INTERFACE_EOF'
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

        .close-btn {
            width: 40px;
            height: 40px;
            border: none;
            background: rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            color: #666;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .close-btn:hover {
            background: rgba(255, 100, 100, 0.2);
            color: #ff6b6b;
        }

        /* Main Container */
        .main-container {
            max-width: 900px;
            margin: 0 auto;
            padding: 100px 40px 60px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            gap: 30px;
        }

        /* Time Display - Top left under nav */
        .time-display {
            position: fixed;
            top: 80px;
            left: 20px;
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

        /* Weather Widget - Top right under nav, minimalistic */
        .weather-widget {
            position: fixed;
            top: 80px;
            right: 20px;
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

        /* Zoe Greeting */
        .zoe-greeting {
            text-align: center;
            margin-bottom: 30px;
        }

        .zoe-greeting h1 {
            font-size: 32px;
            font-weight: 300;
            color: #333;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .zoe-greeting p {
            font-size: 18px;
            color: #666;
            font-weight: 300;
        }

        /* Chat Interface */
        .chat-container {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 25px;
            padding: 25px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.05);
            transition: all 0.3s ease;
        }

        .chat-container:hover {
            background: rgba(255, 255, 255, 0.7);
            box-shadow: 0 25px 80px rgba(0, 0, 0, 0.08);
        }

        .chat-messages {
            min-height: 200px;
            max-height: 400px;
            overflow-y: auto;
            margin-bottom: 20px;
            padding: 10px;
            border-radius: 15px;
        }

        .chat-input-group {
            display: flex;
            gap: 15px;
            align-items: center;
        }

        .chat-input {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            font-size: 16px;
            padding: 15px 20px;
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

        .voice-btn, .send-btn {
            width: 50px;
            height: 50px;
            border: none;
            border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            font-size: 18px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .voice-btn:hover, .send-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 5px 20px rgba(123, 97, 255, 0.4);
        }

        .voice-btn.active {
            animation: pulse 1s ease-in-out infinite;
            box-shadow: 0 0 30px rgba(123, 97, 255, 0.6);
        }

        /* Quick Actions */
        .quick-actions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }

        .quick-action {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(40px);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 15px;
            padding: 15px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            min-height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .quick-action:hover {
            background: rgba(123, 97, 255, 0.1);
            transform: translateY(-1px);
            box-shadow: 0 5px 20px rgba(0, 0, 0, 0.1);
        }

        .quick-action span {
            font-size: 14px;
            color: #333;
            font-weight: 400;
        }

        /* Responsive Design */
        @media (max-width: 768px) {
            .main-container {
                padding: 90px 20px 40px;
                gap: 20px;
            }

            .time-display {
                left: 15px;
                top: 75px;
            }

            .weather-widget {
                right: 15px;
                top: 75px;
            }

            .fluid-orb {
                width: 150px;
                height: 150px;
            }
        }
    </style>
</head>
<body>
    <!-- Orb Mode (Default State) -->
    <div class="orb-mode" id="orbMode">
        <div class="fluid-orb idle" id="fluidOrb" onclick="enterInterface()">
            <div class="fluid-layer"></div>
            <div class="fluid-layer"></div>
            <div class="fluid-layer"></div>
        </div>
        <div class="orb-hint">Touch to interact or just start talking</div>
    </div>

    <!-- Main Interface -->
    <div class="main-interface" id="mainInterface">
        <!-- Navigation -->
        <div class="nav-bar">
            <div class="nav-left">
                <div class="mini-orb" onclick="exitToOrb()">
                    <div class="fluid-layer"></div>
                </div>
            </div>
            <button class="close-btn" onclick="exitToOrb()">×</button>
        </div>

        <div class="main-container">
            <!-- Time Display - Fixed top left -->
            <div class="time-display">
                <div class="current-time" id="currentTime">2:47 PM</div>
                <div class="current-date" id="currentDate">Thursday, August 7</div>
            </div>

            <!-- Weather Widget - Fixed top right -->
            <div class="weather-widget">
                <div class="weather-icon" id="weatherIcon">☀️</div>
                <div class="weather-temp" id="weatherTemp">23°</div>
            </div>

            <!-- Zoe Greeting -->
            <div class="zoe-greeting">
                <h1>Hi, I'm Zoe</h1>
                <p>Your personal AI companion. How can I help you today?</p>
            </div>

            <!-- Chat Interface -->
            <div class="chat-container">
                <div class="chat-messages" id="chatMessages">
                    <!-- Messages will be populated here -->
                </div>
                
                <div class="chat-input-group">
                    <input type="text" class="chat-input" id="chatInput" placeholder="Type your message..." autofocus>
                    <button class="voice-btn" id="voiceBtn" onclick="toggleVoice()">🎤</button>
                    <button class="send-btn" onclick="sendMessage()">→</button>
                </div>
            </div>

            <!-- Quick Actions -->
            <div class="quick-actions">
                <div class="quick-action" onclick="quickAction('journal')">
                    <span>📝 New Journal</span>
                </div>
                <div class="quick-action" onclick="quickAction('task')">
                    <span>✅ Add Task</span>
                </div>
                <div class="quick-action" onclick="quickAction('reminder')">
                    <span>⏰ Set Reminder</span>
                </div>
                <div class="quick-action" onclick="quickAction('weather')">
                    <span>🌤️ Weather</span>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Global state
        let isListening = false;
        let currentMode = 'orb';
        
        // DOM Elements
        const orbMode = document.getElementById('orbMode');
        const mainInterface = document.getElementById('mainInterface');
        const fluidOrb = document.getElementById('fluidOrb');
        const chatMessages = document.getElementById('chatMessages');
        const chatInput = document.getElementById('chatInput');
        const voiceBtn = document.getElementById('voiceBtn');

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            updateTime();
            setInterval(updateTime, 1000);
            updateWeather();
            
            // Focus chat input when interface is active
            mainInterface.addEventListener('transitionend', function() {
                if (mainInterface.classList.contains('active')) {
                    chatInput.focus();
                }
            });
            
            // Voice activation in orb mode (spacebar)
            document.addEventListener('keydown', function(e) {
                if (currentMode === 'orb' && e.code === 'Space') {
                    e.preventDefault();
                    toggleVoiceFromOrb();
                }
            });
            
            // Chat input enter key
            chatInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    sendMessage();
                }
            });
        });

        // Mode switching
        function enterInterface() {
            currentMode = 'interface';
            orbMode.classList.add('hidden');
            mainInterface.classList.add('active');
        }

        function exitToOrb() {
            currentMode = 'orb';
            mainInterface.classList.remove('active');
            orbMode.classList.remove('hidden');
        }

        // Time and Date
        function updateTime() {
            const now = new Date();
            const timeString = now.toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit',
                hour12: true
            });
            const dateString = now.toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric'
            });
            
            document.getElementById('currentTime').textContent = timeString;
            document.getElementById('currentDate').textContent = dateString;
        }

        // Weather (integrate with your backend)
        function updateWeather() {
            fetch('http://localhost:8000/api/weather')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('weatherIcon').textContent = data.icon || '☀️';
                    document.getElementById('weatherTemp').textContent = `${data.temperature || 23}°`;
                })
                .catch(error => {
                    console.log('Weather API not available, using defaults');
                    document.getElementById('weatherIcon').textContent = '☀️';
                    document.getElementById('weatherTemp').textContent = '23°';
                });
        }

        // Voice functionality
        function toggleVoice() {
            isListening = !isListening;
            
            if (isListening) {
                startListening();
            } else {
                stopListening();
            }
        }

        function toggleVoiceFromOrb() {
            isListening = !isListening;
            
            if (isListening) {
                fluidOrb.className = 'fluid-orb listening';
                startListening();
            } else {
                fluidOrb.className = 'fluid-orb idle';
                stopListening();
            }
        }

        function startListening() {
            voiceBtn.classList.add('active');
            console.log('Voice recording started');
        }

        function stopListening() {
            voiceBtn.classList.remove('active');
            console.log('Voice recording stopped');
        }

        // Chat functionality
        function sendMessage() {
            const message = chatInput.value.trim();
            if (!message) return;

            // Add user message to chat
            addMessage(message, 'user');
            chatInput.value = '';

            // Send to your zoe-core backend
            fetch('http://localhost:8000/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: message,
                    context: {
                        mode: currentMode,
                        timestamp: new Date().toISOString()
                    }
                })
            })
            .then(response => response.json())
            .then(data => {
                addMessage(data.response || 'I received your message!', 'assistant');
            })
            .catch(error => {
                console.error('Chat error:', error);
                addMessage('Sorry, I encountered an error. Please try again.', 'assistant');
            });
        }

        function addMessage(content, sender) {
            const messageDiv = document.createElement('div');
            messageDiv.style.cssText = `
                margin-bottom: 12px;
                display: flex;
                gap: 10px;
                ${sender === 'user' ? 'flex-direction: row-reverse;' : ''}
            `;
            
            const avatar = document.createElement('div');
            avatar.style.cssText = `
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
            `;
            avatar.textContent = sender === 'user' ? 'Y' : 'Z';
            
            const messageContent = document.createElement('div');
            messageContent.style.cssText = `
                ${sender === 'user' 
                    ? 'background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); color: white;'
                    : 'background: rgba(255, 255, 255, 0.8);'
                }
                padding: 12px 16px;
                border-radius: 18px;
                max-width: 70%;
                font-size: 14px;
                line-height: 1.4;
            `;
            messageContent.textContent = content;
            
            messageDiv.appendChild(avatar);
            messageDiv.appendChild(messageContent);
            
            chatMessages.appendChild(messageDiv);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        // Quick actions
        function quickAction(action) {
            switch(action) {
                case 'journal':
                    chatInput.value = 'I want to write a journal entry';
                    sendMessage();
                    break;
                    
                case 'task':
                    chatInput.value = 'Create a new task: ';
                    chatInput.focus();
                    break;
                    
                case 'reminder':
                    chatInput.value = 'Set a reminder for: ';
                    chatInput.focus();
                    break;
                    
                case 'weather':
                    chatInput.value = 'What\'s the weather like?';
                    sendMessage();
                    break;
            }
        }
    </script>
</body>
</html>
INTERFACE_EOF

# Step 3: Move the new interface into place
mv /tmp/zoe_new_interface.html services/zoe-ui/dist/index.html
echo "✅ New interface installed"

# Step 4: Restart the UI service
echo "🔄 Restarting zoe-ui service..."
docker-compose restart zoe-ui

# Step 5: Wait a moment and test
echo "⏳ Waiting for service to start..."
sleep 3

# Step 6: Test the deployment
echo "🧪 Testing new interface..."
if curl -s -f http://localhost:8080 > /dev/null; then
    echo "✅ New interface is live at http://localhost:8080"
    echo ""
    echo "🎉 DEPLOYMENT SUCCESSFUL!"
    echo ""
    echo "📱 To open on touchscreen:"
    echo "   DISPLAY=:0 chromium-browser --kiosk http://localhost:8080"
    echo ""
    echo "🔗 Interface features:"
    echo "   • Fluid orb animation (default mode)"
    echo "   • Touch orb to enter full interface"
    echo "   • Live time and weather display"
    echo "   • Chat with your zoe-core backend"
    echo "   • Voice button ready for integration"
    echo "   • Mini-orb to return to orb mode"
else
    echo "❌ Interface not responding. Checking logs..."
    docker-compose logs zoe-ui
    echo ""
    echo "🔧 Try manual restart:"
    echo "   docker-compose restart zoe-ui"
fi

echo ""
echo "📋 Your backups are in: services/zoe-ui/dist/"
ls -la services/zoe-ui/dist/*.backup* 2>/dev/null || echo "   (Previous backups from your system)"
