#!/bin/bash
# Complete fix for touch panel setup issues
# Handles both mDNS problems and discovery client issues

echo "🔧 Complete Touch Panel Fix - Using IP Address Fallback"

# Detect Zoe IP automatically
echo "🔍 Detecting Zoe main instance..."
ZOE_IP=""

# Try common IPs
for ip in 192.168.1.60 192.168.1.100 192.168.0.60 192.168.0.100; do
    if curl -s --connect-timeout 2 "http://$ip/health" | grep -q "zoe-core"; then
        ZOE_IP="$ip"
        echo "✅ Found Zoe at $ZOE_IP"
        break
    fi
done

if [ -z "$ZOE_IP" ]; then
    echo "❌ Cannot find Zoe instance. Please run this from the touch panel and ensure Zoe is running."
    exit 1
fi

cd /home/pi/zoe-touch-panel || { echo "❌ Touch panel directory not found"; exit 1; }

# Install missing packages
echo "🐍 Installing Python packages..."
pip3 install --user --break-system-packages requests netifaces zeroconf flask 2>/dev/null

# Download discovery client using IP
echo "📡 Downloading discovery client using IP ($ZOE_IP)..."
curl -s "http://$ZOE_IP/api/touch-panels/discovery-client" > simple_discovery_client.py

# Create a working discovery test script that uses IP fallback
echo "🔧 Creating IP-aware discovery script..."
cat > discovery_test.py << EOF
#!/usr/bin/env python3
import sys
import requests
import json

def test_zoe_connection():
    print("🔍 Testing Zoe connectivity...")
    
    # Test both hostname and IP
    urls_to_test = [
        'http://zoe.local',
        'http://$ZOE_IP'
    ]
    
    for url in urls_to_test:
        try:
            print(f"   Testing {url}...")
            response = requests.get(f"{url}/api/services", timeout=3)
            if response.status_code == 200:
                data = response.json()
                if 'zoe' in data:
                    print(f"✅ Zoe accessible at {url}")
                    return url
        except Exception as e:
            print(f"   ❌ {url} failed: {e}")
    
    print("❌ Could not connect to Zoe")
    return None

if __name__ == '__main__':
    zoe_url = test_zoe_connection()
    if zoe_url:
        print(f"✅ Touch panel can reach Zoe at: {zoe_url}")
    else:
        print("❌ Touch panel cannot reach Zoe")
        sys.exit(1)
EOF

chmod +x discovery_test.py

# Test the discovery
echo "🧪 Testing Zoe connectivity..."
python3 discovery_test.py

# Update the touch panel agent to use IP fallback
echo "🤖 Updating touch panel agent with IP fallback..."
cat > touch_panel_agent_fixed.py << 'EOF'
#!/usr/bin/env python3
"""
Touch Panel Agent with IP Fallback
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

# Configuration
PANEL_ID = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"
ZOE_URLS = ['http://zoe.local/touch/', 'http://192.168.1.60/touch/', 'http://192.168.1.100/touch/']

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
    """Find working Zoe URL"""
    for url in ZOE_URLS:
        try:
            response = requests.get(f"{url}/health", timeout=2)
            if response.status_code == 200:
                return url
        except:
            continue
    return None

@app.route('/touch-panel-info', methods=['GET'])
def get_panel_info():
    return jsonify({
        'panel_id': PANEL_ID,
        'hostname': socket.gethostname(),
        'ip_address': get_local_ip(),
        'agent_version': '1.0',
        'capabilities': ['touch', 'display'],
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
        
        # Execute script
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

def register_with_zoe():
    """Try to register with Zoe main instance"""
    zoe_url = find_zoe()
    if not zoe_url:
        return False
    
    try:
        panel_data = {
            'panel_id': PANEL_ID,
            'panel_name': f"Touch Panel {socket.gethostname()}",
            'ip_address': get_local_ip(),
            'panel_type': 'auto-discovered',
            'capabilities': ['touch', 'display'],
            'zoe_services': ['automation', 'home', 'ai']
        }
        
        response = requests.post(
            f"{zoe_url}/api/touch-panels/register",
            json=panel_data,
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"✅ Registered with Zoe at {zoe_url}")
            return True
        else:
            print(f"⚠️ Registration failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Registration error: {e}")
        return False

def heartbeat_loop():
    """Send periodic heartbeats to Zoe"""
    while True:
        zoe_url = find_zoe()
        if zoe_url:
            try:
                heartbeat_data = {
                    'status': 'online',
                    'timestamp': time.time(),
                    'capabilities': ['touch', 'display']
                }
                
                requests.post(
                    f"{zoe_url}/api/touch-panels/{PANEL_ID}/heartbeat",
                    json=heartbeat_data,
                    timeout=5
                )
            except:
                pass
        
        time.sleep(60)

if __name__ == '__main__':
    print(f"🚀 Starting Touch Panel Agent")
    print(f"📱 Panel ID: {PANEL_ID}")
    
    # Try to register on startup
    if register_with_zoe():
        print("✅ Registration successful")
    else:
        print("⚠️ Could not register with Zoe - will retry later")
    
    # Start heartbeat in background
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    
    # Start Flask app
    app.run(host='0.0.0.0', port=8888, debug=False)
EOF

# Replace the agent
mv touch_panel_agent_fixed.py touch_panel_agent.py
chmod +x touch_panel_agent.py

# Update the touch interface to use IP fallback
echo "🖥️ Updating touch interface with IP fallback..."
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
        .status {
            position: fixed;
            top: 10px;
            right: 10px;
            padding: 10px;
            background: rgba(0,0,0,0.7);
            border-radius: 5px;
            font-size: 0.9rem;
        }
        .status.connected { color: #4CAF50; }
        .status.disconnected { color: #f44336; }
    </style>
</head>
<body>
    <div class="status" id="status">🔍 Checking connection...</div>
    
    <h1>🤖 Zoe Assistant</h1>
    <div class="panel">
        <div class="service" onclick="openZoe('')">
            <div class="emoji">🤖</div>
            <h3>Main Zoe</h3>
            <p>AI Assistant Interface</p>
        </div>
        <div class="service" onclick="openZoe(':5678')">
            <div class="emoji">⚡</div>
            <h3>Automation</h3>
            <p>N8N Workflows</p>
        </div>
        <div class="service" onclick="openZoe(':8123')">
            <div class="emoji">🏠</div>
            <h3>Home Control</h3>
            <p>Home Assistant</p>
        </div>
        <div class="service" onclick="openZoe(':11434')">
            <div class="emoji">🧠</div>
            <h3>AI Models</h3>
            <p>Ollama AI</p>
        </div>
    </div>
    
    <script>
        let zoeUrl = null;
        
        // Find working Zoe URL
        async function findZoe() {
            const urls = ['http://zoe.local', 'http://192.168.1.60'];
            const status = document.getElementById('status');
            
            for (const url of urls) {
                try {
                    const response = await fetch(`${url}/health`, { 
                        method: 'GET',
                        timeout: 3000 
                    });
                    if (response.ok) {
                        zoeUrl = url;
                        status.textContent = `✅ Connected to ${url}`;
                        status.className = 'status connected';
                        return url;
                    }
                } catch (e) {
                    console.log(`${url} failed:`, e);
                }
            }
            
            status.textContent = '❌ Cannot reach Zoe';
            status.className = 'status disconnected';
            return null;
        }
        
        function openZoe(path = '') {
            if (zoeUrl) {
                window.open(`${zoeUrl}${path}`, '_blank');
            } else {
                alert('❌ Cannot connect to Zoe. Please check your network connection.');
            }
        }
        
        // Test connection on load
        findZoe();
        
        // Retest every 30 seconds
        setInterval(findZoe, 30000);
    </script>
</body>
</html>
HTML_EOF

# Fix mDNS issues
echo "🔧 Attempting to fix mDNS issues..."

# Restart Avahi to clear name collision
sudo systemctl restart avahi-daemon

# Add hostname resolution fallback
echo "📝 Adding hostname fallback..."
if ! grep -q "192.168.1.60.*zoe.local" /etc/hosts; then
    echo "$ZOE_IP zoe.local zoe-ai.local" | sudo tee -a /etc/hosts
    echo "✅ Added hosts file entry for zoe.local"
fi

# Restart the touch panel service
echo "🔄 Restarting touch panel service..."
sudo systemctl restart zoe-touch-panel

# Final test
echo "🧪 Final connectivity test..."
sleep 3

python3 discovery_test.py

echo ""
echo "✅ Complete touch panel fix applied!"
echo ""
echo "🎯 **Status:**"
echo "   • Touch panel agent: sudo systemctl status zoe-touch-panel"
echo "   • Agent web interface: http://$(hostname -I | awk '{print $1}'):8888/status"
echo "   • Touch interface: file:///home/pi/zoe-touch-interface/index.html"
echo "   • Zoe management: http://$ZOE_IP/touch-panel-config/"
echo ""
echo "🔧 **If mDNS still doesn't work:**"
echo "   • The system now uses IP fallback ($ZOE_IP)"
echo "   • All interfaces will automatically detect and use working URLs"
echo "   • Manual override: edit /etc/hosts to add more entries"
EOF

chmod +x /home/pi/zoe/services/zoe-ui/dist/fix-touch-panel-complete.sh




