#!/bin/bash
# Zoe Touchscreen Manager - Consolidated Solution
# Builds on the working TouchKio approach with main Zoe control functionality

set -e

echo "🚀 Zoe Touchscreen Manager v2.0"
echo "================================"

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZOE_MAIN_IP="${ZOE_MAIN_IP:-192.168.1.60}"
ZOE_HOSTNAME="${ZOE_HOSTNAME:-zoe.local}"
MODE="${1:-auto}"  # auto, main_zoe, touch_panel, kiosk

# Auto-detect mode if not specified
if [ "$MODE" = "auto" ]; then
    if curl -s --connect-timeout 2 "http://localhost/health" | grep -q "zoe-core"; then
        MODE="main_zoe"
        echo "📍 Auto-detected: Main Zoe instance"
    else
        MODE="touch_panel"
        echo "📍 Auto-detected: Touch panel mode"
    fi
fi

echo "🎯 Mode: $MODE"

case $MODE in
    "main_zoe")
        setup_main_zoe_control_center
        ;;
    "touch_panel")
        deploy_touch_panel
        ;;
    "kiosk")
        deploy_kiosk_panel
        ;;
    *)
        echo "❌ Unknown mode: $MODE"
        echo "Usage: $0 [auto|main_zoe|touch_panel|kiosk]"
        exit 1
        ;;
esac

setup_main_zoe_control_center() {
    echo "🏠 Setting up Main Zoe Control Center..."
    
    # Create control center directory
    mkdir -p /home/pi/zoe-control-center
    
    # Create the main control dashboard
    cat > /home/pi/zoe-control-center/index.html << 'CONTROL_EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Control Center</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white; 
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            text-align: center;
            margin-bottom: 40px;
        }
        .header h1 {
            font-size: 3rem;
            margin-bottom: 10px;
            background: linear-gradient(45deg, #4CAF50, #2196F3);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .control-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 25px;
            max-width: 1400px;
            margin: 0 auto;
        }
        .unit-card {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 15px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
        }
        .unit-card:hover {
            background: rgba(255, 255, 255, 0.15);
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }
        .unit-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .unit-status {
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: bold;
        }
        .status-online { background: #4CAF50; color: white; }
        .status-offline { background: #f44336; color: white; }
        .status-configuring { background: #ff9800; color: white; }
        .status-main { background: #2196F3; color: white; }
        .unit-title {
            font-size: 1.4rem;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .unit-info {
            color: #ccc;
            font-size: 0.9rem;
            margin-bottom: 20px;
        }
        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        .btn {
            background: linear-gradient(45deg, #0f3460, #16213e);
            color: white;
            border: none;
            padding: 10px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        .btn:hover {
            background: linear-gradient(45deg, #16213e, #0f3460);
            transform: translateY(-2px);
        }
        .btn-primary {
            background: linear-gradient(45deg, #4CAF50, #45a049);
        }
        .btn-danger {
            background: linear-gradient(45deg, #f44336, #d32f2f);
        }
        .discovery-panel {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .discovery-panel h3 {
            margin-bottom: 20px;
            color: #4CAF50;
        }
        .discovery-results {
            margin-top: 15px;
        }
        .discovered-panel {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            margin: 10px 0;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        .loading {
            text-align: center;
            color: #ccc;
            font-style: italic;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: rgba(255, 255, 255, 0.1);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        .stat-number {
            font-size: 2rem;
            font-weight: bold;
            color: #4CAF50;
        }
        .stat-label {
            color: #ccc;
            font-size: 0.9rem;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 Zoe Control Center</h1>
        <p>Unified Management for All Zoe Units</p>
    </div>

    <div class="stats" id="stats-panel">
        <div class="stat-card">
            <div class="stat-number" id="total-units">-</div>
            <div class="stat-label">Total Units</div>
        </div>
        <div class="stat-card">
            <div class="stat-number" id="online-units">-</div>
            <div class="stat-label">Online Units</div>
        </div>
        <div class="stat-card">
            <div class="stat-number" id="configured-units">-</div>
            <div class="stat-label">Configured</div>
        </div>
    </div>

    <div class="discovery-panel">
        <h3>🔍 Discover & Manage Touch Panels</h3>
        <div style="margin-bottom: 15px;">
            <button class="btn btn-primary" onclick="discoverPanels()">🔍 Scan Network</button>
            <button class="btn" onclick="refreshAllStatus()">🔄 Refresh All</button>
            <button class="btn" onclick="showDeploymentGuide()">📋 Deployment Guide</button>
        </div>
        <div id="discovery-results" class="discovery-results"></div>
    </div>

    <div class="control-grid" id="units-grid">
        <!-- Main Zoe card will be inserted here -->
    </div>

    <script>
        let allPanels = [];
        let discoveredPanels = [];

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            loadMainZoeCard();
            refreshAllStatus();
            
            // Auto-refresh every 30 seconds
            setInterval(refreshAllStatus, 30000);
        });

        function loadMainZoeCard() {
            const grid = document.getElementById('units-grid');
            grid.innerHTML = `
                <div class="unit-card">
                    <div class="unit-header">
                        <div class="unit-title">🏠 Main Zoe Instance</div>
                        <div class="unit-status status-main">Main Hub</div>
                    </div>
                    <div class="unit-info">
                        Primary Zoe AI Assistant Hub<br>
                        IP: ${window.location.hostname}<br>
                        Services: Core, UI, AI, Automation
                    </div>
                    <div class="actions">
                        <button class="btn btn-primary" onclick="openMainZoe()">🌐 Open Interface</button>
                        <button class="btn" onclick="openDeveloperTools()">🛠️ Developer Tools</button>
                        <button class="btn" onclick="openAutomation()">⚡ Automation</button>
                        <button class="btn" onclick="showSystemStatus()">📊 System Status</button>
                    </div>
                </div>
            `;
        }

        async function discoverPanels() {
            const resultsDiv = document.getElementById('discovery-results');
            resultsDiv.innerHTML = '<div class="loading">🔍 Scanning network for touch panels...</div>';

            try {
                const response = await fetch('/api/touch-panels/discover');
                const data = await response.json();
                discoveredPanels = data.discovered_panels || [];
                
                displayDiscoveredPanels();
            } catch (error) {
                resultsDiv.innerHTML = `<div style="color: #f44336;">❌ Discovery failed: ${error.message}</div>`;
            }
        }

        function displayDiscoveredPanels() {
            const resultsDiv = document.getElementById('discovery-results');
            
            if (discoveredPanels.length === 0) {
                resultsDiv.innerHTML = `
                    <div style="color: #ff9800;">
                        ❌ No touch panels found.<br>
                        <small>Make sure touch panels are running the Zoe agent on port 8888</small>
                    </div>
                `;
                return;
            }

            let html = `<h4>Found ${discoveredPanels.length} touch panel(s):</h4>`;
            
            discoveredPanels.forEach(panel => {
                const panelId = panel.panel_info.panel_id;
                const hostname = panel.panel_info.hostname;
                const ip = panel.ip_address;
                
                html += `
                    <div class="discovered-panel">
                        <strong>📱 ${panelId}</strong><br>
                        <small>Hostname: ${hostname} | IP: ${ip}</small><br>
                        <div style="margin-top: 10px;">
                            <button class="btn btn-primary" onclick="registerPanel('${panelId}', '${ip}', '${hostname}')">
                                ➕ Register & Configure
                            </button>
                            <button class="btn" onclick="testConnection('${ip}')">🔗 Test Connection</button>
                            <button class="btn" onclick="showPanelInfo('${panelId}', '${ip}')">ℹ️ Panel Info</button>
                        </div>
                    </div>
                `;
            });
            
            resultsDiv.innerHTML = html;
        }

        async function registerPanel(panelId, ipAddress, hostname) {
            try {
                const response = await fetch('/api/touch-panels/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        panel_id: panelId,
                        panel_name: `Touch Panel ${hostname}`,
                        ip_address: ipAddress,
                        panel_type: 'touch_panel',
                        capabilities: ['touch', 'display'],
                        applications: ['zoe-touch-interface'],
                        zoe_services: ['main', 'automation', 'home'],
                        display_settings: {
                            rotation: 90,
                            brightness: 80,
                            auto_start: true
                        }
                    })
                });

                if (response.ok) {
                    alert('✅ Touch panel registered successfully!\nThe panel will now appear in your control grid.');
                    refreshAllStatus();
                } else {
                    const error = await response.text();
                    alert(`❌ Registration failed: ${error}`);
                }
            } catch (error) {
                alert(`❌ Registration error: ${error.message}`);
            }
        }

        async function testConnection(ipAddress) {
            try {
                const response = await fetch(`http://${ipAddress}:8888/status`, { timeout: 5000 });
                const data = await response.json();
                alert(`✅ Connection successful!\nPanel ID: ${data.panel_id}\nStatus: ${data.status}\nLast seen: ${data.timestamp}`);
            } catch (error) {
                alert(`❌ Connection failed: ${error.message}\n\nMake sure the touch panel is running the Zoe agent.`);
            }
        }

        async function refreshAllStatus() {
            try {
                // Load registered panels
                const response = await fetch('/api/touch-panels/panels');
                const data = await response.json();
                allPanels = data.panels || [];
                
                displayAllPanels();
                updateStats();
            } catch (error) {
                console.error('Failed to refresh status:', error);
            }
        }

        function displayAllPanels() {
            const grid = document.getElementById('units-grid');
            const mainZoeCard = grid.firstElementChild; // Keep main Zoe card
            
            // Clear and restore main Zoe card
            grid.innerHTML = '';
            grid.appendChild(mainZoeCard);
            
            allPanels.forEach(panel => {
                const status = panel.status?.status || 'unknown';
                const statusClass = `status-${status}`;
                const config = panel.config;
                
                const panelCard = document.createElement('div');
                panelCard.className = 'unit-card';
                panelCard.innerHTML = `
                    <div class="unit-header">
                        <div class="unit-title">📱 ${config.panel_name}</div>
                        <div class="unit-status ${statusClass}">${status}</div>
                    </div>
                    <div class="unit-info">
                        ID: ${config.panel_id}<br>
                        IP: ${config.ip_address}<br>
                        Type: ${config.panel_type}<br>
                        Apps: ${panel.status?.installed_apps?.join(', ') || 'None'}
                    </div>
                    <div class="actions">
                        <button class="btn btn-primary" onclick="configurePanel('${config.panel_id}')">
                            ⚙️ Configure
                        </button>
                        <button class="btn" onclick="openPanel('${config.ip_address}')">
                            🖥️ Open Panel
                        </button>
                        <button class="btn" onclick="installApp('${config.panel_id}', 'zoe-touch-interface')">
                            📱 Install Interface
                        </button>
                        <button class="btn" onclick="showPanelTasks('${config.panel_id}')">
                            📋 Tasks
                        </button>
                        <button class="btn btn-danger" onclick="removePanel('${config.panel_id}')">
                            🗑️ Remove
                        </button>
                    </div>
                `;
                
                grid.appendChild(panelCard);
            });
        }

        function updateStats() {
            const totalUnits = allPanels.length + 1; // +1 for main Zoe
            const onlineUnits = allPanels.filter(p => p.status?.status === 'online').length + 1;
            const configuredUnits = allPanels.filter(p => p.status?.status === 'configured' || p.status?.status === 'zoe_configured').length + 1;
            
            document.getElementById('total-units').textContent = totalUnits;
            document.getElementById('online-units').textContent = onlineUnits;
            document.getElementById('configured-units').textContent = configuredUnits;
        }

        // Action functions
        function openMainZoe() {
            window.open('/', '_blank');
        }

        function openDeveloperTools() {
            window.open('/developer/', '_blank');
        }

        function openAutomation() {
            window.open(':5678', '_blank');
        }

        function showSystemStatus() {
            window.open('/api/developer/status', '_blank');
        }

        async function configurePanel(panelId) {
            try {
                const response = await fetch(`/api/touch-panels/panels/${panelId}/deploy-zoe-config`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    alert('✅ Configuration deployment started!\nCheck the Tasks section for progress.');
                } else {
                    alert('❌ Configuration failed to start');
                }
            } catch (error) {
                alert(`❌ Configuration error: ${error.message}`);
            }
        }

        function openPanel(ipAddress) {
            window.open(`http://${ipAddress}:8888`, '_blank');
        }

        async function installApp(panelId, appName) {
            try {
                const response = await fetch(`/api/touch-panels/panels/${panelId}/install-app?app_name=${appName}`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    alert(`✅ Installing ${appName} on panel ${panelId}\nCheck tasks for progress.`);
                } else {
                    alert('❌ Installation failed to start');
                }
            } catch (error) {
                alert(`❌ Installation error: ${error.message}`);
            }
        }

        function showPanelTasks(panelId) {
            window.open(`/api/touch-panels/panels/${panelId}`, '_blank');
        }

        function showPanelInfo(panelId, ipAddress) {
            window.open(`http://${ipAddress}:8888/touch-panel-info`, '_blank');
        }

        function showDeploymentGuide() {
            alert(`📋 Touch Panel Deployment Guide:

1. On your touch panel Pi, run:
   curl -s http://${window.location.hostname}/zoe-touchscreen-manager.sh | bash

2. The script will auto-detect it's a touch panel and deploy the Zoe agent

3. The panel will automatically appear in this control center

4. Click "Register & Configure" to set it up

5. Use "Install Interface" to deploy the TouchKio-based interface

Need help? Check the developer tools for detailed logs.`);
        }

        async function removePanel(panelId) {
            if (confirm(`Are you sure you want to remove panel ${panelId}?\n\nThis will unregister it from the control center but won't affect the actual panel.`)) {
                try {
                    const response = await fetch(`/api/touch-panels/panels/${panelId}`, {
                        method: 'DELETE'
                    });
                    
                    if (response.ok) {
                        alert('✅ Panel removed from control center');
                        refreshAllStatus();
                    } else {
                        alert('❌ Failed to remove panel');
                    }
                } catch (error) {
                    alert(`❌ Removal error: ${error.message}`);
                }
            }
        }
    </script>
</body>
</html>
CONTROL_EOF

    # Create nginx configuration to serve the control center
    sudo tee /etc/nginx/sites-available/zoe-control-center << 'NGINX_EOF'
server {
    listen 8080;
    server_name _;
    root /home/pi/zoe-control-center;
    index index.html;
    
    location / {
        try_files $uri $uri/ =404;
    }
    
    # Proxy API calls to main Zoe
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX_EOF

    # Enable the site
    sudo ln -sf /etc/nginx/sites-available/zoe-control-center /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx

    echo "✅ Main Zoe Control Center configured"
    echo "🌐 Access at: http://$ZOE_HOSTNAME:8080"
    echo "🌐 Also available at: http://$ZOE_HOSTNAME/zoe-control-center/"
}

deploy_touch_panel() {
    echo "📱 Deploying Touch Panel (TouchKio-based)..."
    
    # Auto-detect main Zoe
    detect_main_zoe
    
    # Install dependencies
    install_touch_panel_dependencies
    
    # Setup TouchKio-based agent
    setup_touchkio_agent
    
    # Create TouchKio-based interface
    create_touchkio_interface
    
    # Configure auto-start
    configure_touchkio_autostart
    
    echo "✅ Touch panel deployment complete!"
    show_touch_panel_info
}

deploy_kiosk_panel() {
    echo "🖥️ Deploying Kiosk Panel (TouchKio-based)..."
    
    # Auto-detect main Zoe
    detect_main_zoe
    
    # Install kiosk dependencies
    sudo apt update -qq
    sudo apt install -y chromium-browser unclutter
    
    # Configure auto-login
    sudo raspi-config nonint do_boot_behaviour B4
    
    # Create kiosk interface
    create_touchkio_kiosk
    
    echo "✅ Kiosk panel deployment complete!"
}

detect_main_zoe() {
    echo "🔍 Auto-detecting main Zoe instance..."
    
    # Try common Zoe URLs
    for url in "http://zoe.local" "http://192.168.1.60" "http://192.168.1.100"; do
        if curl -s --connect-timeout 2 "$url/health" | grep -q "zoe-core"; then
            ZOE_MAIN_IP=$(echo $url | sed 's|http://||')
            ZOE_HOSTNAME=$(echo $url | sed 's|http://||')
            echo "✅ Found main Zoe at: $url"
            return 0
        fi
    done
    
    echo "❌ Cannot find main Zoe instance"
    echo "💡 Please ensure main Zoe is running and accessible"
    exit 1
}

install_touch_panel_dependencies() {
    echo "📦 Installing touch panel dependencies..."
    
    sudo apt update -qq
    sudo apt install -y python3-pip python3-flask chromium-browser unclutter net-tools
    
    # Install Python packages
    pip3 install --user --break-system-packages requests flask netifaces zeroconf
}

setup_touchkio_agent() {
    echo "🤖 Setting up TouchKio-based agent..."
    
    # Create agent directory
    mkdir -p /home/pi/zoe-touch-panel
    cd /home/pi/zoe-touch-panel
    
    # Create TouchKio-style agent
    cat > touch_panel_agent.py << 'AGENT_EOF'
#!/usr/bin/env python3
"""
TouchKio-style Touch Panel Agent for Zoe
Lightweight, reliable agent based on TouchKio principles
"""

import os
import json
import time
import socket
import uuid
import subprocess
import threading
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Panel identification
PANEL_ID = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"
HOSTNAME = socket.gethostname()

def get_local_ip():
    """Get local IP address (TouchKio-style)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

def get_system_info():
    """Get system information (TouchKio-style)"""
    try:
        # Get CPU temperature (Raspberry Pi)
        temp = "Unknown"
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = f"{int(f.read().strip())/1000:.1f}°C"
        except:
            pass
        
        # Get uptime
        uptime = "Unknown"
        try:
            result = subprocess.run(['uptime', '-p'], capture_output=True, text=True)
            uptime = result.stdout.strip() if result.returncode == 0 else "Unknown"
        except:
            pass
        
        return {
            "temperature": temp,
            "uptime": uptime,
            "hostname": HOSTNAME,
            "ip_address": get_local_ip()
        }
    except:
        return {"error": "Could not get system info"}

@app.route('/touch-panel-info', methods=['GET'])
def get_panel_info():
    """Get comprehensive panel information"""
    system_info = get_system_info()
    
    return jsonify({
        'panel_id': PANEL_ID,
        'hostname': HOSTNAME,
        'ip_address': get_local_ip(),
        'agent_version': '2.0-touchkio',
        'capabilities': ['touch', 'display', 'rotation', 'kiosk'],
        'zoe_configured': True,
        'touchkio_compatible': True,
        'system_info': system_info,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/status', methods=['GET'])
def get_status():
    """Get panel status"""
    return jsonify({
        'panel_id': PANEL_ID,
        'status': 'online',
        'last_seen': datetime.now().isoformat(),
        'uptime': get_system_info().get('uptime', 'Unknown'),
        'temperature': get_system_info().get('temperature', 'Unknown')
    })

@app.route('/execute-config', methods=['POST'])
def execute_configuration():
    """Execute configuration script (TouchKio-style)"""
    try:
        data = request.get_json()
        script = data.get('script', '')
        task_id = data.get('task_id', 'unknown')
        
        if not script:
            return jsonify({'success': False, 'error': 'No script provided'})
        
        # Execute script (TouchKio-style with proper logging)
        script_file = f'/tmp/config_script_{task_id}.sh'
        with open(script_file, 'w') as f:
            f.write(script)
        
        os.chmod(script_file, 0o755)
        
        result = subprocess.run(['bash', script_file], 
                              capture_output=True, text=True, timeout=60)
        
        success = result.returncode == 0
        logs = []
        
        if result.stdout:
            logs.extend(result.stdout.strip().split('\n'))
        if result.stderr:
            logs.extend([f"ERROR: {line}" for line in result.stderr.strip().split('\n') if line])
        
        os.remove(script_file)
        
        return jsonify({
            'success': success,
            'logs': logs,
            'task_id': task_id,
            'return_code': result.returncode
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Script execution timed out', 'task_id': task_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'task_id': task_id})

@app.route('/touchkio-config', methods=['GET'])
def get_touchkio_config():
    """Get TouchKio-style configuration"""
    return jsonify({
        'name': 'Zoe Touch Panel',
        'url': f'http://{get_local_ip()}:8888',
        'rotation': 90,
        'hide_cursor': True,
        'disable_screensaver': True,
        'fullscreen': True,
        'touch_optimized': True
    })

if __name__ == '__main__':
    print(f"🚀 Starting TouchKio-style Touch Panel Agent")
    print(f"📱 Panel ID: {PANEL_ID}")
    print(f"🏠 Hostname: {HOSTNAME}")
    print(f"🌐 IP: {get_local_ip()}")
    print(f"🔧 TouchKio Compatible: Yes")
    
    app.run(host='0.0.0.0', port=8888, debug=False, threaded=True)
AGENT_EOF

    chmod +x touch_panel_agent.py
    
    # Create systemd service (TouchKio-style)
    sudo tee /etc/systemd/system/zoe-touch-panel.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Zoe TouchKio-style Touch Panel Agent
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/zoe-touch-panel
ExecStart=/usr/bin/python3 /home/pi/zoe-touch-panel/touch_panel_agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    # Enable and start service
    sudo systemctl daemon-reload
    sudo systemctl enable zoe-touch-panel.service
    sudo systemctl start zoe-touch-panel.service
    
    echo "✅ TouchKio-style agent configured and started"
}

create_touchkio_interface() {
    echo "🖥️ Creating TouchKio-based interface..."
    
    mkdir -p /home/pi/zoe-touch-interface
    
    cat > /home/pi/zoe-touch-interface/index.html << 'INTERFACE_EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Touch Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { 
            margin: 0; 
            padding: 20px; 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white; 
            min-height: 100vh;
            overflow-x: hidden;
        }
        .panel { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); 
            gap: 25px; 
            max-width: 900px;
            margin: 0 auto;
        }
        .service { 
            background: rgba(255, 255, 255, 0.1);
            padding: 35px; 
            border-radius: 20px; 
            text-align: center; 
            cursor: pointer;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.2);
            backdrop-filter: blur(10px);
        }
        .service:hover { 
            background: rgba(255, 255, 255, 0.2); 
            transform: translateY(-8px);
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.3);
        }
        .service:active {
            transform: translateY(-4px);
        }
        h1 { 
            text-align: center; 
            margin-bottom: 50px; 
            font-size: 3rem;
            background: linear-gradient(45deg, #4CAF50, #2196F3);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        h3 {
            font-size: 1.8rem;
            margin-bottom: 15px;
            font-weight: 600;
        }
        .emoji {
            font-size: 4rem;
            margin-bottom: 20px;
            display: block;
        }
        .status {
            text-align: center;
            margin-bottom: 30px;
            padding: 15px;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.1);
        }
        .status.online { 
            background: rgba(76, 175, 80, 0.2);
            border: 1px solid #4CAF50;
        }
        .status.offline { 
            background: rgba(244, 67, 54, 0.2);
            border: 1px solid #f44336;
        }
        .connection-info {
            font-size: 0.9rem;
            color: #ccc;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <h1>🤖 Zoe Assistant</h1>
    <div class="status" id="connection-status">
        🔍 Checking connection...
    </div>
    
    <div class="panel">
        <div class="service" onclick="openService('main')">
            <div class="emoji">🤖</div>
            <h3>Main Zoe</h3>
            <p>AI Assistant Interface</p>
        </div>
        <div class="service" onclick="openService('automation')">
            <div class="emoji">⚡</div>
            <h3>Automation</h3>
            <p>N8N Workflows</p>
        </div>
        <div class="service" onclick="openService('home')">
            <div class="emoji">🏠</div>
            <h3>Home Control</h3>
            <p>Home Assistant</p>
        </div>
        <div class="service" onclick="openService('ai')">
            <div class="emoji">🧠</div>
            <h3>AI Models</h3>
            <p>Ollama AI</p>
        </div>
        <div class="service" onclick="openService('developer')">
            <div class="emoji">🛠️</div>
            <h3>Developer</h3>
            <p>Tools & Monitoring</p>
        </div>
        <div class="service" onclick="openService('control')">
            <div class="emoji">🎛️</div>
            <h3>Control Center</h3>
            <p>Manage All Units</p>
        </div>
    </div>
    
    <script>
        let zoeUrl = 'http://zoe.local';
        let fallbackUrl = 'http://192.168.1.60';
        
        // Test Zoe connectivity (TouchKio-style)
        async function checkConnection() {
            const statusDiv = document.getElementById('connection-status');
            
            // Try primary URL first
            let workingUrl = null;
            let connectionStatus = 'offline';
            
            for (const url of [zoeUrl, fallbackUrl]) {
                try {
                    const response = await fetch(`${url}/health`, { timeout: 3000 });
                    if (response.ok) {
                        workingUrl = url;
                        connectionStatus = 'online';
                        break;
                    }
                } catch (error) {
                    console.log(`Failed to connect to ${url}`);
                }
            }
            
            if (workingUrl) {
                statusDiv.innerHTML = `
                    ✅ Connected to Zoe<br>
                    <div class="connection-info">${workingUrl}</div>
                `;
                statusDiv.className = 'status online';
                zoeUrl = workingUrl; // Update working URL
            } else {
                statusDiv.innerHTML = `
                    ❌ Cannot reach Zoe<br>
                    <div class="connection-info">Check network connection</div>
                `;
                statusDiv.className = 'status offline';
            }
        }
        
        function openService(service) {
            const urls = {
                main: `${zoeUrl}`,
                automation: `${zoeUrl}:5678`,
                home: `${zoeUrl}:8123`,
                ai: `${zoeUrl}:11434`,
                developer: `${zoeUrl}/developer`,
                control: `${zoeUrl}:8080`
            };
            
            if (urls[service]) {
                // TouchKio-style: Open in new tab with proper focus
                const newWindow = window.open(urls[service], '_blank');
                if (newWindow) {
                    newWindow.focus();
                }
            }
        }
        
        // TouchKio-style touch feedback
        document.querySelectorAll('.service').forEach(service => {
            service.addEventListener('touchstart', function() {
                this.style.transform = 'translateY(-4px) scale(0.98)';
            });
            
            service.addEventListener('touchend', function() {
                this.style.transform = 'translateY(-8px) scale(1)';
            });
        });
        
        // Check connection on load and every 2 minutes
        checkConnection();
        setInterval(checkConnection, 120000);
        
        // Auto-refresh every hour (TouchKio-style)
        setTimeout(() => {
            console.log('Auto-refreshing interface...');
            location.reload();
        }, 3600000);
        
        // Prevent context menu (TouchKio-style)
        document.addEventListener('contextmenu', e => e.preventDefault());
        
        // Prevent text selection (TouchKio-style)
        document.addEventListener('selectstart', e => e.preventDefault());
    </script>
</body>
</html>
INTERFACE_EOF
    
    echo "✅ TouchKio-based interface created"
}

create_touchkio_kiosk() {
    echo "🖥️ Creating TouchKio-based kiosk..."
    
    # Create TouchKio-style kiosk startup script
    cat > /home/pi/start-zoe-kiosk.sh << 'KIOSK_EOF'
#!/bin/bash
# TouchKio-style Zoe Kiosk Startup Script

export DISPLAY=:0

echo "🚀 Starting TouchKio-style Zoe Kiosk..."

# TouchKio-style display setup
xset s off
xset -dpms
xset s noblank

# TouchKio-style rotation (90 degrees clockwise)
xrandr --output HDMI-1 --rotate right 2>/dev/null || true
xrandr --output HDMI-A-1 --rotate right 2>/dev/null || true

# Hide cursor (TouchKio style)
unclutter -idle 0.1 -root &

# Test Zoe URLs and choose working one
ZOE_URL=""
for url in "http://zoe.local" "http://192.168.1.60"; do
    if curl -s --connect-timeout 3 "$url/health" >/dev/null 2>&1; then
        ZOE_URL="$url"
        echo "✅ Using: $url"
        break
    fi
done

if [ -z "$ZOE_URL" ]; then
    echo "❌ No Zoe instance found, using fallback"
    ZOE_URL="http://192.168.1.60"
fi

# Start TouchKio-style browser
chromium-browser \
    --kiosk \
    --no-first-run \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --noerrdialogs \
    --disable-web-security \
    --disable-features=TranslateUI \
    --touch-events=enabled \
    --start-maximized \
    "$ZOE_URL" &

echo "✅ TouchKio-style Zoe Kiosk started"

# TouchKio-style monitoring
BROWSER_PID=$!
while kill -0 $BROWSER_PID 2>/dev/null; do
    # Maintain TouchKio settings
    xset s off -dpms 2>/dev/null || true
    sleep 30
done

echo "Browser exited, restarting..."
exec $0
KIOSK_EOF

    chmod +x /home/pi/start-zoe-kiosk.sh
    
    # Create autostart
    mkdir -p /home/pi/.config/autostart
    cat > /home/pi/.config/autostart/zoe-kiosk.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Zoe TouchKio Kiosk
Exec=/home/pi/start-zoe-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
DESKTOP_EOF
    
    echo "✅ TouchKio-based kiosk created"
}

configure_touchkio_autostart() {
    echo "⚙️ Configuring TouchKio-style auto-start..."
    
    mkdir -p /home/pi/.config/autostart
    
    cat > /home/pi/.config/autostart/zoe-touch.desktop << 'AUTOSTART_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Panel
Exec=chromium-browser --kiosk --disable-infobars file:///home/pi/zoe-touch-interface/index.html
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTOSTART_EOF
    
    echo "✅ TouchKio-style auto-start configured"
}

show_touch_panel_info() {
    echo ""
    echo "✅ Zoe Touch Panel Deployment Complete!"
    echo ""
    echo "🎯 **What's been configured:**"
    echo "   ✅ TouchKio-style agent running on port 8888"
    echo "   ✅ Automatic Zoe discovery"
    echo "   ✅ TouchKio-based interface available"
    echo "   ✅ Auto-start on boot configured"
    echo ""
    echo "🌐 **Access points:**"
    echo "   • Touch Interface: file:///home/pi/zoe-touch-interface/index.html"
    echo "   • Agent Status: http://$(hostname -I | awk '{print $1}'):8888/status"
    echo "   • Main Zoe: http://$ZOE_HOSTNAME"
    echo "   • Control Center: http://$ZOE_HOSTNAME:8080"
    echo ""
    echo "🔧 **Management:**"
    echo "   • Service: sudo systemctl status zoe-touch-panel"
    echo "   • Logs: sudo journalctl -u zoe-touch-panel -f"
    echo "   • Restart: sudo systemctl restart zoe-touch-panel"
    echo ""
    echo "🚀 Touch panel is ready and should appear in the Zoe control center!"
    echo ""
    echo "💡 **Next steps:**"
    echo "   1. Go to the main Zoe control center: http://$ZOE_HOSTNAME:8080"
    echo "   2. Click 'Scan Network' to discover this panel"
    echo "   3. Register and configure the panel"
    echo "   4. Install the TouchKio-based interface"
}

# Main execution
echo "Starting deployment..."


