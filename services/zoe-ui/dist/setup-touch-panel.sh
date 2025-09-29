#!/bin/bash
# Zoe Touch Panel Quick Setup Script
# Run this on any Raspberry Pi to configure it as a Zoe touch panel

echo "🚀 Setting up Zoe Touch Panel..."

# Detect Zoe IP automatically  
echo "🔍 Detecting Zoe main instance..."
ZOE_IP=""
ZOE_URL=""

# Try both hostname and common IPs
for test_url in "http://zoe.local" "http://192.168.1.60" "http://192.168.1.100" "http://192.168.0.60"; do
    if curl -s --connect-timeout 2 "$test_url/health" | grep -q "zoe-core"; then
        ZOE_URL="$test_url"
        ZOE_IP=$(echo $test_url | sed 's|http://||')
        echo "✅ Found Zoe at $ZOE_URL"
        break
    fi
done

if [ -z "$ZOE_URL" ]; then
    echo "❌ Cannot find Zoe instance. Please ensure Zoe is running and accessible."
    echo "💡 You may need to run this script from the same network as Zoe."
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
sudo apt update -qq
sudo apt install -y python3-pip python3-flask chromium-browser unclutter

# Install Python packages
echo "🐍 Installing Python packages..."
pip3 install --user --break-system-packages requests flask netifaces zeroconf

# Create touch panel directory
mkdir -p /home/pi/zoe-touch-panel
cd /home/pi/zoe-touch-panel

# Download discovery client using detected URL
echo "📡 Downloading Zoe discovery client from $ZOE_URL..."
curl -s "$ZOE_URL/api/touch-panels/discovery-client" > simple_discovery_client.py

# Download touch panel agent
echo "🤖 Downloading touch panel agent..."
cat > touch_panel_agent.py << 'EOF'
#!/usr/bin/env python3
"""
Lightweight Touch Panel Agent for Zoe
"""

import os
import json
import time
import subprocess
import threading
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import socket
import uuid

app = Flask(__name__)

# Get panel info
PANEL_ID = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

@app.route('/touch-panel-info', methods=['GET'])
def get_panel_info():
    return jsonify({
        'panel_id': PANEL_ID,
        'hostname': socket.gethostname(),
        'ip_address': get_local_ip(),
        'agent_version': '1.0',
        'capabilities': ['touch', 'display'],
        'zoe_configured': True,
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
        
        # Execute script
        script_file = f'/tmp/config_script_{task_id}.sh'
        with open(script_file, 'w') as f:
            f.write(script)
        
        os.chmod(script_file, 0o755)
        
        result = subprocess.run(['bash', script_file], 
                              capture_output=True, text=True)
        
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
    return jsonify({
        'panel_id': PANEL_ID,
        'status': 'online',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    print(f"🚀 Starting Touch Panel Agent on port 8888")
    print(f"📱 Panel ID: {PANEL_ID}")
    app.run(host='0.0.0.0', port=8888, debug=False)
EOF

chmod +x touch_panel_agent.py

# Create systemd service for touch panel agent
echo "⚙️ Creating systemd service..."
sudo tee /etc/systemd/system/zoe-touch-panel.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Zoe Touch Panel Agent
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/zoe-touch-panel
ExecStart=/usr/bin/python3 /home/pi/zoe-touch-panel/touch_panel_agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable zoe-touch-panel.service
sudo systemctl start zoe-touch-panel.service

# Test Zoe discovery
echo "🧪 Testing Zoe discovery..."
python3 -c "
try:
    import sys
    sys.path.append('/home/pi/zoe-touch-panel')
    from simple_discovery_client import find_zoe
    config = find_zoe()
    if config:
        print('✅ Successfully connected to Zoe!')
        print(f'Zoe URL: {config[\"discovery_info\"][\"url\"]}')
    else:
        print('❌ Could not find Zoe instance')
except Exception as e:
    print(f'❌ Discovery error: {e}')
    print('⚠️ Manual configuration may be needed')
"

# Create simple touch interface
echo "🖥️ Creating touch interface..."
mkdir -p /home/pi/zoe-touch-interface

cat > /home/pi/zoe-touch-interface/index.html << 'HTML_EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Touch Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { 
            margin: 0; 
            padding: 20px; 
            font-family: Arial, sans-serif; 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white; 
            min-height: 100vh;
        }
        .panel { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
            gap: 20px; 
            max-width: 800px;
            margin: 0 auto;
        }
        .service { 
            background: rgba(255, 255, 255, 0.1); 
            padding: 30px; 
            border-radius: 15px; 
            text-align: center; 
            cursor: pointer;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        .service:hover { 
            background: rgba(255, 255, 255, 0.2); 
            transform: translateY(-5px);
        }
        h1 { 
            text-align: center; 
            margin-bottom: 40px;
            font-size: 2.5rem;
        }
        h3 {
            font-size: 1.5rem;
            margin-bottom: 10px;
        }
        .emoji {
            font-size: 3rem;
            margin-bottom: 15px;
        }
    </style>
</head>
<body>
    <h1>🤖 Zoe Assistant</h1>
    <div class="panel">
        <div class="service" onclick="window.open('http://zoe.local', '_blank')">
            <div class="emoji">🤖</div>
            <h3>Main Zoe</h3>
            <p>AI Assistant Interface</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:5678', '_blank')">
            <div class="emoji">⚡</div>
            <h3>Automation</h3>
            <p>N8N Workflows</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:8123', '_blank')">
            <div class="emoji">🏠</div>
            <h3>Home Control</h3>
            <p>Home Assistant</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:11434', '_blank')">
            <div class="emoji">🧠</div>
            <h3>AI Models</h3>
            <p>Ollama AI</p>
        </div>
    </div>
    
    <script>
        // Auto-refresh every hour
        setTimeout(() => location.reload(), 3600000);
        
        // Test Zoe connectivity on load
        fetch('http://zoe.local/health')
            .then(response => response.ok ? console.log('✅ Zoe connected') : console.log('⚠️ Zoe connection issue'))
            .catch(() => console.log('❌ Cannot reach Zoe'));
    </script>
</body>
</html>
HTML_EOF

# Create autostart for touch interface (optional)
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/zoe-touch.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=chromium-browser --kiosk --disable-infobars file:///home/pi/zoe-touch-interface/index.html
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
DESKTOP_EOF

echo ""
echo "✅ Zoe Touch Panel setup complete!"
echo ""
echo "🎯 **What's been configured:**"
echo "   ✅ Touch panel agent running on port 8888"
echo "   ✅ Automatic Zoe discovery"
echo "   ✅ Touch interface available"
echo "   ✅ Auto-start on boot configured"
echo ""
echo "🌐 **Access points:**"
echo "   • Touch Interface: file:///home/pi/zoe-touch-interface/index.html"
echo "   • Agent Status: http://$(hostname -I | awk '{print $1}'):8888/status"
echo "   • Main Zoe: http://zoe.local"
echo ""
echo "🔧 **Management:**"
echo "   • Service: sudo systemctl status zoe-touch-panel"
echo "   • Logs: sudo journalctl -u zoe-touch-panel -f"
echo "   • Configure: http://zoe.local/touch-panel-config/"
echo ""
echo "🚀 Touch panel is ready! It should appear in the Zoe management interface shortly."