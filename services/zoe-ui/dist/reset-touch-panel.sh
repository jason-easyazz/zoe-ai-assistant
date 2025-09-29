#!/bin/bash
# Reset Touch Panel Configuration
# Removes TouchKio and other kiosk software, prepares for clean Zoe setup

echo "🔄 Resetting Touch Panel Configuration..."
echo "⚠️  This will remove existing kiosk software and prepare for Zoe setup"

# Ask for confirmation
read -p "Continue with reset? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Reset cancelled"
    exit 1
fi

echo "🧹 Starting touch panel reset..."

# Stop and disable existing services
echo "🛑 Stopping existing services..."

# Stop common kiosk services
for service in touchkio kiosk chromium-kiosk zoe-touch-panel; do
    if systemctl is-active --quiet $service 2>/dev/null; then
        echo "   Stopping $service..."
        sudo systemctl stop $service 2>/dev/null || true
        sudo systemctl disable $service 2>/dev/null || true
    fi
done

# Remove TouchKio specifically
echo "🗑️  Removing TouchKio..."
if [ -d "/home/pi/TouchKio" ]; then
    echo "   Found TouchKio installation, removing..."
    rm -rf /home/pi/TouchKio
fi

if [ -d "/opt/TouchKio" ]; then
    echo "   Found TouchKio in /opt, removing..."
    sudo rm -rf /opt/TouchKio
fi

# Remove TouchKio service files
sudo rm -f /etc/systemd/system/touchkio.service
sudo rm -f /etc/systemd/system/kiosk.service

# Clean up autostart entries
echo "🧽 Cleaning autostart entries..."
if [ -d "/home/pi/.config/autostart" ]; then
    sudo rm -f /home/pi/.config/autostart/touchkio*
    sudo rm -f /home/pi/.config/autostart/kiosk*
    sudo rm -f /home/pi/.config/autostart/chromium*
fi

# Remove system-wide autostart
sudo rm -f /etc/xdg/autostart/touchkio*
sudo rm -f /etc/xdg/autostart/kiosk*

# Clean up previous Zoe installations
echo "🔧 Cleaning previous Zoe touch panel setup..."
rm -rf /home/pi/zoe-touch-panel
rm -rf /home/pi/zoe-touch-interface
rm -f /home/pi/.zoe-touch-panel.json

# Remove Zoe touch panel service
sudo systemctl stop zoe-touch-panel 2>/dev/null || true
sudo systemctl disable zoe-touch-panel 2>/dev/null || true
sudo rm -f /etc/systemd/system/zoe-touch-panel.service

# Clean up X11 and display configurations
echo "🖥️  Resetting display configuration..."

# Remove kiosk display settings
sudo rm -f /home/pi/.xsession
sudo rm -f /home/pi/.xinitrc

# Reset boot behavior to desktop
echo "🔄 Resetting boot behavior..."
sudo raspi-config nonint do_boot_behaviour B4 # Desktop autologin

# Clean up browser configurations
echo "🌐 Cleaning browser configurations..."
rm -rf /home/pi/.config/chromium/Default/Preferences
rm -rf /home/pi/.config/chromium/Default/Sessions

# Remove any kiosk-related packages (optional)
echo "📦 Checking for kiosk packages..."
if command -v unclutter >/dev/null 2>&1; then
    echo "   Unclutter found (cursor hiding tool)"
fi

# Clean up any custom boot configurations
echo "⚙️  Checking boot configurations..."
if grep -q "kiosk\|touchkio" /boot/config.txt 2>/dev/null; then
    echo "   Found kiosk settings in /boot/config.txt"
    echo "   Manual review may be needed"
fi

# Reload systemd
sudo systemctl daemon-reload

# Clean Python environment
echo "🐍 Cleaning Python environment..."
pip3 uninstall -y touchkio 2>/dev/null || true

# Reset network configuration if needed
echo "🌐 Checking network configuration..."
if grep -q "touchkio\|kiosk" /etc/hosts 2>/dev/null; then
    echo "   Found kiosk entries in /etc/hosts"
    sudo sed -i '/touchkio\|kiosk/d' /etc/hosts
fi

# Update package lists
echo "📦 Updating package lists..."
sudo apt update -qq

echo ""
echo "✅ Touch panel reset complete!"
echo ""
echo "🎯 **What was cleaned:**"
echo "   ✅ TouchKio installation removed"
echo "   ✅ Kiosk services stopped and disabled"  
echo "   ✅ Autostart entries cleaned"
echo "   ✅ Previous Zoe configuration removed"
echo "   ✅ Display settings reset"
echo "   ✅ Boot behavior reset to desktop"
echo ""
echo "🚀 **Ready for fresh Zoe setup:**"
echo "   Run: curl -s http://192.168.1.60/setup-touch-panel.sh | bash"
echo ""
echo "⚠️  **Manual checks recommended:**"
echo "   • Review /boot/config.txt for custom display settings"
echo "   • Check ~/.bashrc for custom startup commands"
echo "   • Verify no custom cron jobs: crontab -l"
echo ""
echo "🔄 **Reboot recommended** to ensure clean state"




