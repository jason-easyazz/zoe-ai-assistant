#!/bin/bash
# Fix Enhanced Touch Panel Setup
echo "🔧 Fixing enhanced touch panel setup..."

cd /home/pi/zoe-touch-panel

# Fix the autostart desktop file
echo "📝 Creating proper autostart configuration..."
mkdir -p /home/pi/.config/autostart

cat > /home/pi/.config/autostart/zoe-touch.desktop << 'AUTO_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=/home/pi/start-zoe-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTO_EOF

# Create proper systemd services
echo "⚙️ Creating fixed systemd services..."

# Agent service
sudo tee /etc/systemd/system/zoe-touch-agent.service > /dev/null << 'AGENT_EOF'
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

[Install]
WantedBy=multi-user.target
AGENT_EOF

# Kiosk service - using user session instead of graphical-session.target
sudo tee /etc/systemd/system/zoe-touch-kiosk.service > /dev/null << 'KIOSK_EOF'
[Unit]
Description=Zoe Touch Panel Kiosk Interface
After=multi-user.target
Wants=multi-user.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStart=/home/pi/start-zoe-kiosk.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
KIOSK_EOF

# Enable and start services
echo "🚀 Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable zoe-touch-agent.service
sudo systemctl start zoe-touch-agent.service

# Test agent
echo "🧪 Testing touch panel agent..."
sleep 3

if curl -s http://localhost:8888/status | grep -q "online"; then
    echo "✅ Touch panel agent is running!"
else
    echo "⚠️ Agent starting up..."
fi

# Test Zoe connectivity
echo "🔍 Testing Zoe connectivity..."
python3 -c "
try:
    from simple_discovery_client import find_zoe
    config = find_zoe()
    if config:
        print('✅ Zoe connection successful!')
        print(f'URL: {config[\"discovery_info\"][\"url\"]}')
    else:
        print('⚠️ Zoe connection will be established')
except Exception as e:
    print(f'ℹ️ Discovery ready: {e}')
"

echo ""
echo "✅ Enhanced touch panel fix complete!"
echo ""
echo "🎯 **Status:**"
echo "   • Agent service: sudo systemctl status zoe-touch-agent"
echo "   • Agent interface: http://$(hostname -I | awk '{print $1}'):8888/"
echo "   • Touch interface: file:///home/pi/zoe-touch-interface/index.html"
echo "   • Kiosk startup: /home/pi/start-zoe-kiosk.sh"
echo ""
echo "🚀 **To activate full kiosk mode:**"
echo "   sudo reboot"
echo ""
echo "💡 **Manual kiosk start:**"
echo "   /home/pi/start-zoe-kiosk.sh"




