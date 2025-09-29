#!/bin/bash
# Unified Zoe Touchscreen Deployment Script
# Clean, consolidated version that works with the main Zoe control system

set -e

echo "🚀 Zoe Touchscreen Deployment System v2.0"
echo "=========================================="

# Configuration
ZOE_MAIN_IP="${ZOE_MAIN_IP:-192.168.1.60}"
ZOE_HOSTNAME="${ZOE_HOSTNAME:-zoe.local}"
DEPLOYMENT_MODE="${1:-full}"  # full, minimal, kiosk

# Detect if running on main Zoe or touch panel
if curl -s --connect-timeout 2 "http://localhost/health" | grep -q "zoe-core"; then
    echo "📍 Running on main Zoe instance - configuring for control mode"
    DEPLOYMENT_MODE="main_zoe"
fi

echo "🎯 Deployment mode: $DEPLOYMENT_MODE"

case $DEPLOYMENT_MODE in
    "main_zoe")
        setup_main_zoe_control
        ;;
    "full")
        deploy_full_touchscreen
        ;;
    "minimal")
        deploy_minimal_touchscreen
        ;;
    "kiosk")
        deploy_kiosk_touchscreen
        ;;
    *)
        echo "❌ Unknown deployment mode: $DEPLOYMENT_MODE"
        echo "Usage: $0 [full|minimal|kiosk]"
        exit 1
        ;;
esac

setup_main_zoe_control() {
    echo "🏠 Setting up main Zoe control system..."
    
    # Ensure touch panel management API is active
    echo "📡 Enabling touch panel management API..."
    
    # Create main Zoe control dashboard
    mkdir -p /home/pi/zoe-control-dashboard
    cat > /home/pi/zoe-control-dashboard/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Control Center</title>
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
        .control-panel { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); 
            gap: 20px; 
            max-width: 1200px;
            margin: 0 auto;
        }
        .unit-card { 
            background: rgba(255, 255, 255, 0.1); 
            padding: 25px; 
            border-radius: 15px; 
            border: 1px solid rgba(255, 255, 255, 0.2);
            transition: all 0.3s ease;
        }
        .unit-card:hover { 
            background: rgba(255, 255, 255, 0.2); 
            transform: translateY(-5px);
        }
        .unit-status {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 0.8rem;
            margin-bottom: 10px;
        }
        .status-online { background: #4CAF50; color: white; }
        .status-offline { background: #f44336; color: white; }
        .status-configuring { background: #ff9800; color: white; }
        h1 { text-align: center; margin-bottom: 40px; font-size: 2.5rem; }
        .actions { margin-top: 15px; }
        .btn {
            background: #0f3460;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            margin-right: 10px;
            margin-bottom: 10px;
        }
        .btn:hover { background: #16213e; }
        .discovery-section {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 30px;
        }
    </style>
</head>
<body>
    <h1>🤖 Zoe Control Center</h1>
    
    <div class="discovery-section">
        <h3>🔍 Discover Touch Panels</h3>
        <button class="btn" onclick="discoverPanels()">Scan Network</button>
        <button class="btn" onclick="refreshStatus()">Refresh Status</button>
        <div id="discovery-results"></div>
    </div>
    
    <div class="control-panel" id="units-panel">
        <div class="unit-card">
            <div class="unit-status status-online">Main Zoe</div>
            <h3>🏠 Main Zoe Instance</h3>
            <p>Primary Zoe AI Assistant</p>
            <div class="actions">
                <button class="btn" onclick="window.open('http://localhost', '_blank')">Open Interface</button>
                <button class="btn" onclick="window.open('http://localhost/developer', '_blank')">Developer Tools</button>
                <button class="btn" onclick="window.open('http://localhost:5678', '_blank')">Automation</button>
            </div>
        </div>
    </div>
    
    <script>
        let discoveredPanels = [];
        
        async function discoverPanels() {
            document.getElementById('discovery-results').innerHTML = '🔍 Scanning network...';
            
            try {
                const response = await fetch('/api/touch-panels/discover');
                const data = await response.json();
                discoveredPanels = data.discovered_panels || [];
                
                displayDiscoveredPanels();
            } catch (error) {
                document.getElementById('discovery-results').innerHTML = '❌ Discovery failed: ' + error.message;
            }
        }
        
        function displayDiscoveredPanels() {
            const resultsDiv = document.getElementById('discovery-results');
            
            if (discoveredPanels.length === 0) {
                resultsDiv.innerHTML = '❌ No touch panels found. Ensure they are running the Zoe agent.';
                return;
            }
            
            let html = `<h4>Found ${discoveredPanels.length} touch panel(s):</h4>`;
            
            discoveredPanels.forEach(panel => {
                html += `
                    <div style="background: rgba(255,255,255,0.1); padding: 15px; margin: 10px 0; border-radius: 8px;">
                        <strong>${panel.panel_info.panel_id}</strong><br>
                        IP: ${panel.ip_address}<br>
                        Hostname: ${panel.panel_info.hostname}<br>
                        <button class="btn" onclick="registerPanel('${panel.panel_info.panel_id}', '${panel.ip_address}')">Register & Configure</button>
                        <button class="btn" onclick="testConnection('${panel.ip_address}')">Test Connection</button>
                    </div>
                `;
            });
            
            resultsDiv.innerHTML = html;
        }
        
        async function registerPanel(panelId, ipAddress) {
            try {
                const response = await fetch('/api/touch-panels/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        panel_id: panelId,
                        panel_name: `Touch Panel ${panelId.split('_')[2]}`,
                        ip_address: ipAddress,
                        panel_type: 'touch_panel',
                        capabilities: ['touch', 'display'],
                        applications: ['zoe-touch-interface'],
                        zoe_services: ['main', 'automation', 'home']
                    })
                });
                
                if (response.ok) {
                    alert('✅ Touch panel registered successfully!');
                    refreshStatus();
                } else {
                    alert('❌ Registration failed');
                }
            } catch (error) {
                alert('❌ Registration error: ' + error.message);
            }
        }
        
        async function testConnection(ipAddress) {
            try {
                const response = await fetch(`http://${ipAddress}:8888/status`);
                const data = await response.json();
                alert(`✅ Connection successful!\nPanel ID: ${data.panel_id}\nStatus: ${data.status}`);
            } catch (error) {
                alert('❌ Connection failed: ' + error.message);
            }
        }
        
        async function refreshStatus() {
            try {
                const response = await fetch('/api/touch-panels/panels');
                const data = await response.json();
                
                displayRegisteredPanels(data.panels);
            } catch (error) {
                console.error('Failed to refresh status:', error);
            }
        }
        
        function displayRegisteredPanels(panels) {
            const unitsPanel = document.getElementById('units-panel');
            
            // Keep the main Zoe card
            const mainZoeCard = unitsPanel.firstElementChild;
            unitsPanel.innerHTML = '';
            unitsPanel.appendChild(mainZoeCard);
            
            panels.forEach(panel => {
                const status = panel.status?.status || 'unknown';
                const statusClass = `status-${status}`;
                
                const panelCard = document.createElement('div');
                panelCard.className = 'unit-card';
                panelCard.innerHTML = `
                    <div class="unit-status ${statusClass}">${status}</div>
                    <h3>📱 ${panel.config.panel_name}</h3>
                    <p>ID: ${panel.config.panel_id}</p>
                    <p>IP: ${panel.config.ip_address}</p>
                    <p>Type: ${panel.config.panel_type}</p>
                    <div class="actions">
                        <button class="btn" onclick="configurePanel('${panel.config.panel_id}')">Configure</button>
                        <button class="btn" onclick="openPanel('${panel.config.ip_address}')">Open Panel</button>
                        <button class="btn" onclick="installApp('${panel.config.panel_id}', 'zoe-touch-interface')">Install Interface</button>
                    </div>
                `;
                
                unitsPanel.appendChild(panelCard);
            });
        }
        
        async function configurePanel(panelId) {
            try {
                const response = await fetch(`/api/touch-panels/panels/${panelId}/configure`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    alert('✅ Configuration started! Check task status for progress.');
                } else {
                    alert('❌ Configuration failed to start');
                }
            } catch (error) {
                alert('❌ Configuration error: ' + error.message);
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
                    alert(`✅ Installing ${appName} on panel ${panelId}`);
                } else {
                    alert('❌ Installation failed to start');
                }
            } catch (error) {
                alert('❌ Installation error: ' + error.message);
            }
        }
        
        // Auto-refresh every 30 seconds
        setInterval(refreshStatus, 30000);
        
        // Initial load
        refreshStatus();
    </script>
</body>
</html>
EOF
    
    echo "✅ Main Zoe control system configured"
    echo "🌐 Access control dashboard at: http://$ZOE_HOSTNAME/zoe-control-dashboard/"
}

deploy_full_touchscreen() {
    echo "📱 Deploying full touchscreen setup..."
    
    # Auto-detect main Zoe
    detect_main_zoe
    
    # Install dependencies
    install_touchscreen_dependencies
    
    # Setup touch panel agent
    setup_touch_panel_agent
    
    # Create touch interface
    create_touch_interface
    
    # Configure auto-start
    configure_autostart
    
    echo "✅ Full touchscreen deployment complete!"
    show_access_info
}

deploy_minimal_touchscreen() {
    echo "📱 Deploying minimal touchscreen setup..."
    
    # Auto-detect main Zoe
    detect_main_zoe
    
    # Install minimal dependencies
    sudo apt update -qq
    sudo apt install -y python3-pip chromium-browser
    
    # Setup basic agent
    setup_basic_agent
    
    echo "✅ Minimal touchscreen deployment complete!"
}

deploy_kiosk_touchscreen() {
    echo "🖥️ Deploying kiosk touchscreen setup..."
    
    # Auto-detect main Zoe
    detect_main_zoe
    
    # Install kiosk dependencies
    sudo apt update -qq
    sudo apt install -y chromium-browser unclutter
    
    # Configure auto-login and kiosk mode
    sudo raspi-config nonint do_boot_behaviour B4
    
    # Setup kiosk interface
    create_kiosk_interface
    
    echo "✅ Kiosk touchscreen deployment complete!"
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

install_touchscreen_dependencies() {
    echo "📦 Installing touchscreen dependencies..."
    
    sudo apt update -qq
    sudo apt install -y python3-pip python3-flask chromium-browser unclutter net-tools
    
    # Install Python packages
    pip3 install --user --break-system-packages requests flask netifaces zeroconf
}

setup_touch_panel_agent() {
    echo "🤖 Setting up touch panel agent..."
    
    # Create agent directory
    mkdir -p /home/pi/zoe-touch-panel
    cd /home/pi/zoe-touch-panel
    
    # Download agent from main Zoe
    curl -s "http://$ZOE_HOSTNAME/api/touch-panels/agent-script" > touch_panel_agent.py
    chmod +x touch_panel_agent.py
    
    # Create systemd service
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

    # Enable and start service
    sudo systemctl daemon-reload
    sudo systemctl enable zoe-touch-panel.service
    sudo systemctl start zoe-touch-panel.service
    
    echo "✅ Touch panel agent configured and started"
}

setup_basic_agent() {
    echo "🤖 Setting up basic agent..."
    
    mkdir -p /home/pi/zoe-touch-panel
    cd /home/pi/zoe-touch-panel
    
    # Create minimal agent
    cat > touch_panel_agent.py << 'EOF'
#!/usr/bin/env python3
import os, json, time, socket, uuid
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
PANEL_ID = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"

@app.route('/touch-panel-info', methods=['GET'])
def get_panel_info():
    return jsonify({
        'panel_id': PANEL_ID,
        'hostname': socket.gethostname(),
        'ip_address': socket.gethostbyname(socket.gethostname()),
        'agent_version': '2.0-minimal',
        'capabilities': ['touch', 'display'],
        'zoe_configured': True,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'panel_id': PANEL_ID,
        'status': 'online',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    print(f"🚀 Starting Basic Touch Panel Agent - Panel ID: {PANEL_ID}")
    app.run(host='0.0.0.0', port=8888, debug=False)
EOF

    chmod +x touch_panel_agent.py
    
    # Start in background
    nohup python3 touch_panel_agent.py > /dev/null 2>&1 &
    echo "✅ Basic agent started"
}

create_touch_interface() {
    echo "🖥️ Creating touch interface..."
    
    mkdir -p /home/pi/zoe-touch-interface
    
    cat > /home/pi/zoe-touch-interface/index.html << 'EOF'
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
        h1 { text-align: center; margin-bottom: 40px; font-size: 2.5rem; }
        h3 { font-size: 1.5rem; margin-bottom: 10px; }
        .emoji { font-size: 3rem; margin-bottom: 15px; }
        .status { text-align: center; margin-bottom: 20px; }
        .status.online { color: #4CAF50; }
        .status.offline { color: #f44336; }
    </style>
</head>
<body>
    <h1>🤖 Zoe Assistant</h1>
    <div class="status" id="connection-status">🔍 Checking connection...</div>
    
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
    </div>
    
    <script>
        let zoeUrl = 'http://zoe.local';
        
        // Test Zoe connectivity
        async function checkConnection() {
            try {
                const response = await fetch(`${zoeUrl}/health`);
                if (response.ok) {
                    document.getElementById('connection-status').innerHTML = '✅ Connected to Zoe';
                    document.getElementById('connection-status').className = 'status online';
                } else {
                    throw new Error('Health check failed');
                }
            } catch (error) {
                document.getElementById('connection-status').innerHTML = '❌ Cannot reach Zoe';
                document.getElementById('connection-status').className = 'status offline';
            }
        }
        
        function openService(service) {
            const urls = {
                main: `${zoeUrl}`,
                automation: `${zoeUrl}:5678`,
                home: `${zoeUrl}:8123`,
                ai: `${zoeUrl}:11434`
            };
            
            if (urls[service]) {
                window.open(urls[service], '_blank');
            }
        }
        
        // Check connection on load and every 5 minutes
        checkConnection();
        setInterval(checkConnection, 300000);
        
        // Auto-refresh every hour
        setTimeout(() => location.reload(), 3600000);
    </script>
</body>
</html>
EOF
    
    echo "✅ Touch interface created"
}

create_kiosk_interface() {
    echo "🖥️ Creating kiosk interface..."
    
    # Create kiosk startup script
    cat > /home/pi/start-zoe-kiosk.sh << 'EOF'
#!/bin/bash
# Hide cursor
unclutter -idle 0.5 -root &

# Start Zoe in kiosk mode
chromium-browser --noerrdialogs --disable-infobars --kiosk http://zoe.local
EOF

    chmod +x /home/pi/start-zoe-kiosk.sh
    echo "✅ Kiosk interface created"
}

configure_autostart() {
    echo "⚙️ Configuring auto-start..."
    
    mkdir -p /home/pi/.config/autostart
    
    if [ "$DEPLOYMENT_MODE" = "kiosk" ]; then
        # Kiosk mode autostart
        cat > /home/pi/.config/autostart/zoe-kiosk.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Zoe Kiosk
Exec=/home/pi/start-zoe-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
    else
        # Touch interface autostart
        cat > /home/pi/.config/autostart/zoe-touch.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=chromium-browser --kiosk --disable-infobars file:///home/pi/zoe-touch-interface/index.html
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
    fi
    
    echo "✅ Auto-start configured"
}

show_access_info() {
    echo ""
    echo "✅ Zoe Touchscreen Deployment Complete!"
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
    echo "   • Main Zoe: http://$ZOE_HOSTNAME"
    echo "   • Control Center: http://$ZOE_HOSTNAME/zoe-control-dashboard/"
    echo ""
    echo "🔧 **Management:**"
    echo "   • Service: sudo systemctl status zoe-touch-panel"
    echo "   • Logs: sudo journalctl -u zoe-touch-panel -f"
    echo "   • Restart: sudo systemctl restart zoe-touch-panel"
    echo ""
    echo "🚀 Touch panel is ready and should appear in the Zoe control center!"
}

# Main execution
echo "Starting deployment..."


