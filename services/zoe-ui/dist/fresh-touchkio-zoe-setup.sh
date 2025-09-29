#!/bin/bash
# Fresh TouchKio + Zoe Setup - Clean Start Approach
# Run this on a fresh Raspberry Pi OS installation

echo "🚀 Fresh TouchKio + Zoe Setup"
echo "=============================="
echo "This will set up a clean TouchKio installation pointing to Zoe"
echo ""

# Auto-detect Zoe IP
echo "🔍 Detecting Zoe instance..."
ZOE_IP=""
for ip in 192.168.1.{1..254}; do
    if timeout 2 curl -s "$ip/api/services" >/dev/null 2>&1; then
        ZOE_IP="$ip"
        echo "✅ Found Zoe at: $ip"
        break
    fi
done

if [ -z "$ZOE_IP" ]; then
    echo "❌ Could not auto-detect Zoe. Please enter Zoe IP manually:"
    read -p "Zoe IP address: " ZOE_IP
fi

ZOE_URL="http://$ZOE_IP/touch/"
echo "🎯 Using Zoe URL: $ZOE_URL"

# Update system
echo "📦 Updating system..."
sudo apt update
sudo apt upgrade -y

# Install required packages
echo "📦 Installing required packages..."
sudo apt install -y \
    chromium-browser \
    unclutter \
    xdotool \
    git \
    python3-pip \
    lightdm \
    openbox \
    x11-xserver-utils \
    curl \
    wget

# Install TouchKio (simplified approach)
echo "📱 Setting up TouchKio foundation..."
sudo mkdir -p /opt/TouchKio
cd /tmp

# Create TouchKio-style configuration
sudo tee /opt/TouchKio/config.json > /dev/null << CONFIG_EOF
{
  "name": "Zoe Touch Panel",
  "url": "$ZOE_URL",
  "rotation": 90,
  "fullscreen": true,
  "hide_cursor": true,
  "disable_screensaver": true,
  "kiosk_mode": true,
  "auto_restart": true,
  "display": {
    "prevent_sleep": true,
    "force_display_on": true
  }
}
CONFIG_EOF

# Create clean startup script
sudo tee /opt/TouchKio/start-kiosk.sh > /dev/null << 'KIOSK_EOF'
#!/bin/bash
# Clean TouchKio-style Kiosk Startup

export DISPLAY=:0

# Load config
CONFIG="/opt/TouchKio/config.json"
ZOE_URL=$(grep -o '"url":[^,]*' "$CONFIG" | cut -d'"' -f4)

echo "🚀 Starting Zoe Kiosk: $ZOE_URL"

# Wait for X server
while ! xset q &>/dev/null; do
    echo "⏳ Waiting for X server..."
    sleep 2
done

# TouchKio-style display setup
xset s off
xset -dpms
xset s noblank

# Apply rotation (90 degrees clockwise)
xrandr --output HDMI-1 --rotate right 2>/dev/null || \
xrandr --output HDMI-A-1 --rotate right 2>/dev/null || \
echo "⚠️ Could not apply X11 rotation"

# Hide cursor
unclutter -idle 0.1 -root &

# Wait a moment for settings to apply
sleep 3

# Start browser in kiosk mode
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
    --user-data-dir=/tmp/chromium-kiosk \
    "$ZOE_URL"
KIOSK_EOF

sudo chmod +x /opt/TouchKio/start-kiosk.sh

# Create systemd service
echo "⚙️ Creating systemd service..."
sudo tee /etc/systemd/system/zoe-kiosk.service > /dev/null << SERVICE_EOF
[Unit]
Description=Zoe Kiosk Display
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
User=pi
Group=pi
Environment=DISPLAY=:0
ExecStart=/opt/TouchKio/start-kiosk.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=graphical.target
SERVICE_EOF

# Configure auto-login
echo "🔐 Configuring auto-login..."
sudo raspi-config nonint do_boot_behaviour B4

# Create autostart entry
echo "🚀 Setting up autostart..."
mkdir -p /home/pi/.config/autostart
tee /home/pi/.config/autostart/zoe-kiosk.desktop > /dev/null << AUTOSTART_EOF
[Desktop Entry]
Type=Application
Name=Zoe Kiosk
Exec=/bin/bash -c 'sleep 5 && /opt/TouchKio/start-kiosk.sh'
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTOSTART_EOF

# Configure boot settings for rotation
echo "🔄 Configuring display rotation..."

# Handle both old and new boot config locations
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

# Backup original config
sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.backup-$(date +%Y%m%d)"

# Add minimal display configuration
if ! grep -q "# Zoe TouchKio Display Config" "$BOOT_CONFIG"; then
    cat << 'BOOT_EOF' | sudo tee -a "$BOOT_CONFIG"

# Zoe TouchKio Display Config
hdmi_force_hotplug=1
hdmi_drive=2
disable_overscan=1
gpu_mem=128
display_rotate=1
BOOT_EOF
    echo "✅ Boot configuration updated"
fi

# Enable and start service
echo "🔧 Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable zoe-kiosk.service

# Create desktop shortcut for manual start
echo "🖥️ Creating desktop shortcut..."
mkdir -p /home/pi/Desktop
tee /home/pi/Desktop/Start-Zoe-Kiosk.desktop > /dev/null << DESKTOP_EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Start Zoe Kiosk
Comment=Start Zoe Touch Interface
Exec=/opt/TouchKio/start-kiosk.sh
Icon=chromium-browser
Terminal=false
Categories=Application;
DESKTOP_EOF

chmod +x /home/pi/Desktop/Start-Zoe-Kiosk.desktop

# Test connectivity
echo "🧪 Testing Zoe connectivity..."
if curl -s --connect-timeout 5 "$ZOE_URL/health" >/dev/null 2>&1; then
    echo "✅ Zoe connection successful"
else
    echo "⚠️ Could not reach Zoe, but setup complete"
fi

echo ""
echo "🎉 FRESH TOUCHKIO + ZOE SETUP COMPLETE!"
echo "======================================"
echo ""
echo "✅ **What was installed:**"
echo "   • TouchKio-style kiosk foundation"
echo "   • Clean systemd service"
echo "   • Auto-login configuration"
echo "   • 90° clockwise rotation"
echo "   • Desktop shortcut"
echo ""
echo "🎯 **Configuration:**"
echo "   • Zoe URL: $ZOE_URL"
echo "   • Config: /opt/TouchKio/config.json"
echo "   • Start script: /opt/TouchKio/start-kiosk.sh"
echo "   • Service: zoe-kiosk.service"
echo ""
echo "🔄 **To activate:**"
echo "   sudo reboot"
echo ""
echo "🖥️ **Manual start:**"
echo "   /opt/TouchKio/start-kiosk.sh"
echo "   OR click desktop shortcut"
echo ""
echo "📋 **Check status:**"
echo "   sudo systemctl status zoe-kiosk"
echo ""
echo "💡 **Clean, simple, and reliable - no over-engineering!**"




