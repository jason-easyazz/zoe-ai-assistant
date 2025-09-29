#!/bin/bash
# Simple Touch Panel Reset Script
# Removes TouchKio and prepares for clean Zoe setup

echo "🔄 Touch Panel Reset - Removing TouchKio and kiosk software"
echo ""

# Non-interactive mode - proceed automatically
echo "🧹 Starting reset process..."

# Stop and disable existing services
echo "🛑 Stopping services..."
sudo systemctl stop touchkio 2>/dev/null || true
sudo systemctl stop kiosk 2>/dev/null || true
sudo systemctl stop chromium-kiosk 2>/dev/null || true
sudo systemctl stop zoe-touch-panel 2>/dev/null || true

sudo systemctl disable touchkio 2>/dev/null || true
sudo systemctl disable kiosk 2>/dev/null || true
sudo systemctl disable chromium-kiosk 2>/dev/null || true
sudo systemctl disable zoe-touch-panel 2>/dev/null || true

# Remove TouchKio
echo "🗑️  Removing TouchKio..."
rm -rf /home/pi/TouchKio 2>/dev/null || true
sudo rm -rf /opt/TouchKio 2>/dev/null || true

# Remove service files
sudo rm -f /etc/systemd/system/touchkio.service
sudo rm -f /etc/systemd/system/kiosk.service
sudo rm -f /etc/systemd/system/zoe-touch-panel.service

# Clean autostart
echo "🧽 Cleaning autostart..."
rm -f /home/pi/.config/autostart/touchkio* 2>/dev/null || true
rm -f /home/pi/.config/autostart/kiosk* 2>/dev/null || true
rm -f /home/pi/.config/autostart/chromium* 2>/dev/null || true
rm -f /home/pi/.config/autostart/zoe-touch* 2>/dev/null || true

sudo rm -f /etc/xdg/autostart/touchkio* 2>/dev/null || true
sudo rm -f /etc/xdg/autostart/kiosk* 2>/dev/null || true

# Clean Zoe installations
echo "🔧 Cleaning previous Zoe setup..."
rm -rf /home/pi/zoe-touch-panel 2>/dev/null || true
rm -rf /home/pi/zoe-touch-interface 2>/dev/null || true
rm -f /home/pi/.zoe-touch-panel.json 2>/dev/null || true

# Reset display settings
echo "🖥️  Resetting display..."
rm -f /home/pi/.xsession 2>/dev/null || true
rm -f /home/pi/.xinitrc 2>/dev/null || true

# Reset boot to desktop
echo "🔄 Setting boot to desktop..."
sudo raspi-config nonint do_boot_behaviour B4

# Clean browser config
echo "🌐 Cleaning browser..."
rm -rf /home/pi/.config/chromium/Default/Preferences 2>/dev/null || true

# Reload systemd
sudo systemctl daemon-reload

# Clean hosts file of any old entries
echo "🌐 Cleaning hosts file..."
sudo sed -i '/zoe\.local/d' /etc/hosts 2>/dev/null || true

echo ""
echo "✅ Reset complete!"
echo ""
echo "🎯 What was cleaned:"
echo "   ✅ TouchKio removed"
echo "   ✅ Kiosk services disabled"
echo "   ✅ Autostart entries cleaned"
echo "   ✅ Zoe installations removed"
echo "   ✅ Boot reset to desktop"
echo ""
echo "🚀 Ready for fresh Zoe setup:"
echo "   curl -s http://192.168.1.60/setup-touch-panel.sh | bash"
echo ""
echo "💡 Reboot recommended: sudo reboot"




