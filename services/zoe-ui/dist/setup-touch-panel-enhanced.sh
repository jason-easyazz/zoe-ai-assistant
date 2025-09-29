#!/bin/bash
# Enhanced Zoe Touch Panel Setup - TouchKio Quality
# Provides a fully touch-optimized experience

echo "🚀 Setting up Enhanced Zoe Touch Panel (TouchKio Quality)"

# Auto-detect Zoe
echo "🔍 Detecting Zoe..."
ZOE_URL=""
for test_url in "http://zoe.local" "http://192.168.1.60" "http://192.168.1.100"; do
    if curl -s --connect-timeout 2 "$test_url/health" | grep -q "zoe-core"; then
        ZOE_URL="$test_url"
        echo "✅ Found Zoe at $ZOE_URL"
        break
    fi
done

if [ -z "$ZOE_URL" ]; then
    echo "❌ Cannot find Zoe instance"
    exit 1
fi

# Install dependencies without interactive prompts
echo "📦 Installing dependencies..."
export DEBIAN_FRONTEND=noninteractive
sudo apt update -qq
sudo apt install -y --no-install-recommends \
    chromium-browser \
    unclutter \
    xdotool \
    python3-pip \
    python3-flask \
    lightdm \
    openbox \
    plymouth-themes

# Install Python packages
echo "🐍 Installing Python packages..."
pip3 install --user --break-system-packages requests flask netifaces zeroconf

# Create touch panel user and directories
echo "👤 Setting up touch panel environment..."
mkdir -p /home/pi/zoe-touch-panel
mkdir -p /home/pi/.config/openbox
mkdir -p /home/pi/.config/autostart

cd /home/pi/zoe-touch-panel

# Download discovery client
echo "📡 Downloading Zoe discovery client..."
curl -s "$ZOE_URL/api/touch-panels/discovery-client" > simple_discovery_client.py

# Create enhanced touch panel agent
echo "🤖 Creating enhanced touch panel agent..."
cat > touch_panel_agent.py << 'EOF'
#!/usr/bin/env python3
"""
Enhanced Touch Panel Agent - TouchKio Quality
"""

import os
import json
import time
import subprocess
import threading
import requests
from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import socket
import uuid

app = Flask(__name__)

PANEL_ID = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"
ZOE_URLS = ['http://zoe.local/touch/', 'http://192.168.1.60/touch/']

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

def find_zoe():
    for url in ZOE_URLS:
        try:
            response = requests.get(f"{url}/health", timeout=2)
            if response.status_code == 200:
                return url
        except:
            continue
    return None

@app.route('/')
def touch_panel_status():
    """Web-based status interface"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Zoe Touch Panel Status</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial; padding: 20px; background: #1a1a2e; color: white; }
            .status { padding: 20px; background: rgba(255,255,255,0.1); border-radius: 10px; }
            .online { color: #4CAF50; }
            .offline { color: #f44336; }
        </style>
    </head>
    <body>
        <h1>🤖 Zoe Touch Panel Agent</h1>
        <div class="status">
            <h3>Panel ID: {{ panel_id }}</h3>
            <h3>Status: <span class="{{ status_class }}">{{ status }}</span></h3>
            <h3>Zoe URL: {{ zoe_url or 'Not connected' }}</h3>
            <h3>Last Update: {{ timestamp }}</h3>
        </div>
    </body>
    </html>
    """
    zoe_url = find_zoe()
    return render_template_string(html,
        panel_id=PANEL_ID,
        status="Online" if zoe_url else "Offline",
        status_class="online" if zoe_url else "offline",
        zoe_url=zoe_url,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@app.route('/touch-panel-info', methods=['GET'])
def get_panel_info():
    return jsonify({
        'panel_id': PANEL_ID,
        'hostname': socket.gethostname(),
        'ip_address': get_local_ip(),
        'agent_version': '2.0',
        'capabilities': ['touch', 'display', 'kiosk'],
        'zoe_configured': find_zoe() is not None,
        'zoe_url': find_zoe(),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/execute-config', methods=['POST'])
def execute_configuration():
    try:
        data = request.get_json()
        script = data.get('script', '')
        task_id = data.get('task_id', 'unknown')
        
        if not script:
            return jsonify({'success': False, 'error': 'No script provided'})
        
        script_file = f'/tmp/config_script_{task_id}.sh'
        with open(script_file, 'w') as f:
            f.write(script)
        
        os.chmod(script_file, 0o755)
        
        result = subprocess.run(['bash', script_file], 
                              capture_output=True, text=True, timeout=300)
        
        success = result.returncode == 0
        logs = result.stdout.split('\n') + result.stderr.split('\n')
        
        os.remove(script_file)
        
        return jsonify({
            'success': success,
            'logs': [l for l in logs if l.strip()],
            'task_id': task_id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/status', methods=['GET'])
def get_status():
    zoe_url = find_zoe()
    return jsonify({
        'panel_id': PANEL_ID,
        'status': 'online',
        'zoe_connected': zoe_url is not None,
        'zoe_url': zoe_url,
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    print(f"🚀 Enhanced Touch Panel Agent v2.0")
    print(f"📱 Panel ID: {PANEL_ID}")
    app.run(host='0.0.0.0', port=8888, debug=False)
EOF

chmod +x touch_panel_agent.py

# Create TouchKio-quality touch interface
echo "🖥️ Creating enhanced touch interface..."
mkdir -p /home/pi/zoe-touch-interface

cat > /home/pi/zoe-touch-interface/index.html << 'HTML_EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Touch Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <style>
        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
        }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white; 
            height: 100vh;
            overflow: hidden;
            cursor: none;
        }
        
        .container {
            height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 20px;
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .header h1 { 
            font-size: clamp(2rem, 5vw, 3.5rem);
            font-weight: 300;
            margin-bottom: 10px;
        }
        
        .status {
            font-size: clamp(0.9rem, 2vw, 1.1rem);
            opacity: 0.8;
        }
        
        .panel { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); 
            gap: 25px; 
            flex: 1;
            align-content: center;
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
        }
        
        .service { 
            background: rgba(255, 255, 255, 0.1); 
            padding: 40px 20px; 
            border-radius: 20px; 
            text-align: center; 
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 2px solid rgba(255, 255, 255, 0.2);
            backdrop-filter: blur(10px);
            position: relative;
            overflow: hidden;
        }
        
        .service::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
            transition: left 0.5s;
        }
        
        .service:hover::before {
            left: 100%;
        }
        
        .service:active { 
            transform: scale(0.95);
            background: rgba(255, 255, 255, 0.2);
        }
        
        .service:hover { 
            background: rgba(255, 255, 255, 0.15); 
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        
        .emoji {
            font-size: clamp(3rem, 8vw, 4.5rem);
            margin-bottom: 15px;
            display: block;
        }
        
        .service h3 {
            font-size: clamp(1.2rem, 3vw, 1.8rem);
            margin-bottom: 10px;
            font-weight: 500;
        }
        
        .service p {
            font-size: clamp(0.9rem, 2vw, 1.1rem);
            opacity: 0.8;
            line-height: 1.4;
        }
        
        .connection-status {
            position: fixed;
            top: 15px;
            right: 15px;
            padding: 10px 15px;
            background: rgba(0,0,0,0.7);
            border-radius: 20px;
            font-size: 0.9rem;
            backdrop-filter: blur(5px);
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .connection-status.connected { 
            color: #4CAF50; 
            border-color: #4CAF50;
        }
        
        .connection-status.disconnected { 
            color: #f44336; 
            border-color: #f44336;
        }
        
        .settings-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 60px;
            height: 60px;
            background: rgba(255,255,255,0.1);
            border: none;
            border-radius: 50%;
            color: white;
            font-size: 1.5rem;
            cursor: pointer;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }
        
        .settings-btn:hover {
            background: rgba(255,255,255,0.2);
            transform: rotate(90deg);
        }
        
        /* Touch-friendly animations */
        .ripple {
            position: relative;
            overflow: hidden;
        }
        
        .ripple::after {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 0;
            height: 0;
            background: rgba(255,255,255,0.3);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            transition: width 0.3s, height 0.3s;
        }
        
        .ripple:active::after {
            width: 300px;
            height: 300px;
        }
        
        @media (max-width: 768px) {
            .panel {
                grid-template-columns: 1fr 1fr;
                gap: 20px;
            }
            
            .service {
                padding: 30px 15px;
            }
        }
        
        @media (max-width: 480px) {
            .panel {
                grid-template-columns: 1fr;
                gap: 15px;
            }
        }
    </style>
</head>
<body>
    <div class="connection-status" id="status">🔍 Connecting...</div>
    
    <div class="container">
        <div class="header">
            <h1>🤖 Zoe Assistant</h1>
            <div class="status" id="subtitle">Touch-Optimized Interface</div>
        </div>
        
        <div class="panel">
            <div class="service ripple" onclick="openZoe('', 'Main Interface')">
                <div class="emoji">🤖</div>
                <h3>Main Zoe</h3>
                <p>AI Assistant Interface</p>
            </div>
            <div class="service ripple" onclick="openZoe(':5678', 'Automation')">
                <div class="emoji">⚡</div>
                <h3>Automation</h3>
                <p>N8N Workflows</p>
            </div>
            <div class="service ripple" onclick="openZoe(':8123', 'Home Control')">
                <div class="emoji">🏠</div>
                <h3>Home Control</h3>
                <p>Home Assistant</p>
            </div>
            <div class="service ripple" onclick="openZoe(':11434', 'AI Models')">
                <div class="emoji">🧠</div>
                <h3>AI Models</h3>
                <p>Ollama AI</p>
            </div>
        </div>
    </div>
    
    <button class="settings-btn" onclick="showSettings()" title="Settings">⚙️</button>
    
    <script>
        let zoeUrl = null;
        let connectionStatus = 'checking';
        
        // Find working Zoe URL
        async function findZoe() {
            const urls = ['http://zoe.local', 'http://192.168.1.60'];
            const status = document.getElementById('status');
            const subtitle = document.getElementById('subtitle');
            
            status.textContent = '🔍 Connecting...';
            status.className = 'connection-status';
            
            for (const url of urls) {
                try {
                    const controller = new AbortController();
                    const timeoutId = setTimeout(() => controller.abort(), 3000);
                    
                    const response = await fetch(`${url}/health`, { 
                        method: 'GET',
                        signal: controller.signal
                    });
                    
                    clearTimeout(timeoutId);
                    
                    if (response.ok) {
                        zoeUrl = url;
                        connectionStatus = 'connected';
                        status.textContent = `✅ Connected`;
                        status.className = 'connection-status connected';
                        subtitle.textContent = `Connected to ${url}`;
                        return url;
                    }
                } catch (e) {
                    console.log(`${url} failed:`, e.name);
                }
            }
            
            connectionStatus = 'disconnected';
            status.textContent = '❌ Offline';
            status.className = 'connection-status disconnected';
            subtitle.textContent = 'Cannot reach Zoe - Check network';
            return null;
        }
        
        function openZoe(path = '', serviceName = '') {
            if (connectionStatus === 'connected' && zoeUrl) {
                // Show loading feedback
                const status = document.getElementById('status');
                status.textContent = `🚀 Opening ${serviceName}...`;
                
                window.open(`${zoeUrl}${path}`, '_blank');
                
                // Reset status after delay
                setTimeout(() => {
                    status.textContent = '✅ Connected';
                }, 2000);
            } else {
                // Show error feedback
                const status = document.getElementById('status');
                status.textContent = '❌ No connection';
                status.className = 'connection-status disconnected';
                
                // Try to reconnect
                setTimeout(findZoe, 1000);
            }
        }
        
        function showSettings() {
            if (connectionStatus === 'connected') {
                window.open(`${zoeUrl}/touch-panel-config/`, '_blank');
            } else {
                window.open('http://192.168.1.61:8888/', '_blank');
            }
        }
        
        // Test connection on load
        findZoe();
        
        // Retest connection every 30 seconds
        setInterval(findZoe, 30000);
        
        // Prevent context menu on long press
        document.addEventListener('contextmenu', e => e.preventDefault());
        
        // Prevent zoom
        document.addEventListener('gesturestart', e => e.preventDefault());
        document.addEventListener('gesturechange', e => e.preventDefault());
        
        // Wake screen on touch
        document.addEventListener('touchstart', () => {
            if (document.body.style.filter === 'brightness(0.3)') {
                document.body.style.filter = 'brightness(1)';
            }
        });
        
        // Auto-dim after inactivity (optional)
        let dimTimer;
        function resetDimTimer() {
            clearTimeout(dimTimer);
            document.body.style.filter = 'brightness(1)';
            dimTimer = setTimeout(() => {
                document.body.style.filter = 'brightness(0.3)';
            }, 300000); // 5 minutes
        }
        
        document.addEventListener('touchstart', resetDimTimer);
        resetDimTimer();
    </script>
</body>
</html>
HTML_EOF

# Create proper kiosk configuration
echo "🖥️ Setting up kiosk mode..."

# Create openbox configuration
cat > /home/pi/.config/openbox/rc.xml << 'XML_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config>
  <applications>
    <application name="chromium*">
      <maximized>true</maximized>
      <decor>no</decor>
    </application>
  </applications>
</openbox_config>
XML_EOF

# Create kiosk startup script
cat > /home/pi/start-zoe-kiosk.sh << 'KIOSK_EOF'
#!/bin/bash
# Enhanced Zoe Kiosk Startup

# Hide cursor
unclutter -idle 0.1 -root &

# Disable screen blanking
xset s off
xset -dpms
xset s noblank

# Start window manager
openbox &

# Wait for X to be ready
sleep 2

# Start Chromium in kiosk mode
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-features=TranslateUI \
    --disable-extensions \
    --disable-plugins \
    --disable-web-security \
    --disable-features=VizDisplayCompositor \
    --start-maximized \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --no-first-run \
    --fast \
    --fast-start \
    --disable-default-apps \
    --disable-popup-blocking \
    --allow-running-insecure-content \
    --touch-events=enabled \
    file:///home/pi/zoe-touch-interface/index.html
KIOSK_EOF

chmod +x /home/pi/start-zoe-kiosk.sh

# Create systemd service
echo "⚙️ Creating enhanced services..."
sudo tee /etc/systemd/system/zoe-touch-agent.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Zoe Enhanced Touch Panel Agent
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/zoe-touch-panel
ExecStart=/usr/bin/python3 /home/pi/zoe-touch-panel/touch_panel_agent.py
Restart=always
RestartSec=10
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo tee /etc/systemd/system/zoe-touch-kiosk.service > /dev/null << 'KIOSK_SERVICE_EOF'
[Unit]
Description=Zoe Touch Panel Kiosk Interface
After=graphical-session.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStart=/home/pi/start-zoe-kiosk.sh
Restart=always
RestartSec=10

[Install]
WantedBy=graphical-session.target
KIOSK_SERVICE_EOF

# Enable auto-login and services
echo "🔐 Configuring auto-login..."
sudo raspi-config nonint do_boot_behaviour B2  # Desktop autologin

# Enable services
sudo systemctl daemon-reload
sudo systemctl enable zoe-touch-agent.service
sudo systemctl enable zoe-touch-kiosk.service

# Start agent service
sudo systemctl start zoe-touch-agent.service

# Create desktop entry for manual start
mkdir -p /home/pi/.local/share/applications
cat > /home/pi/.local/share/applications/zoe-touch.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=/home/pi/start-zoe-kiosk.sh
Icon=applications-internet
Categories=Network;
DESKTOP_EOF

# Add to autostart
cat > /home/pi/.config/autostart/zoe-touch.desktop << 'AUTO_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=/home/pi/start-zoe-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

# Test connectivity
echo "🧪 Testing enhanced setup..."
sleep 3

python3 -c "
try:
    import sys
    sys.path.append('/home/pi/zoe-touch-panel')
    from simple_discovery_client import find_zoe
    config = find_zoe()
    if config:
        print('✅ Enhanced touch panel connected to Zoe!')
        print(f'Zoe URL: {config[\"discovery_info\"][\"url\"]}')
    else:
        print('⚠️ Zoe connection will be established on first boot')
except Exception as e:
    print(f'ℹ️ Discovery will work after reboot: {e}')
"

echo ""
echo "✅ Enhanced Zoe Touch Panel Setup Complete!"
echo ""
echo "🎯 **TouchKio-Quality Features Added:**"
echo "   ✅ Full kiosk mode with touch optimization"
echo "   ✅ Automatic cursor hiding and screen management"
echo "   ✅ Touch-friendly interface with proper feedback"
echo "   ✅ Auto-start and crash recovery"
echo "   ✅ Connection status and auto-reconnect"
echo "   ✅ No keyboard dependencies"
echo "   ✅ Professional touch interface"
echo ""
echo "🌐 **Access Points:**"
echo "   • Touch Interface: Auto-starts on boot"
echo "   • Agent Status: http://$(hostname -I | awk '{print $1}'):8888/"
echo "   • Manual Start: /home/pi/start-zoe-kiosk.sh"
echo ""
echo "🔧 **Management:**"
echo "   • Agent: sudo systemctl status zoe-touch-agent"
echo "   • Kiosk: sudo systemctl status zoe-touch-kiosk" 
echo "   • Configuration: $ZOE_URL/touch-panel-config/"
echo ""
echo "🚀 **Ready for production use!**"
echo "    Reboot to activate full kiosk mode: sudo reboot"
EOF

chmod +x /home/pi/zoe/services/zoe-ui/dist/setup-touch-panel-enhanced.sh




