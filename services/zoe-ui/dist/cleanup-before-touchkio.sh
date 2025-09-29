#!/bin/bash
# Cleanup Over-Engineered Solutions Before TouchKio
echo "🧹 Cleaning up our over-engineered solutions..."

# Stop all our custom services
echo "🛑 Stopping custom services..."
sudo systemctl stop bulletproof-display 2>/dev/null || true
sudo systemctl stop anti-blink-display 2>/dev/null || true
sudo systemctl stop zoe-power-management 2>/dev/null || true
sudo systemctl stop zoe-touch-agent 2>/dev/null || true
sudo systemctl stop zoe-touch-kiosk 2>/dev/null || true

# Disable all our custom services
echo "❌ Disabling custom services..."
sudo systemctl disable bulletproof-display 2>/dev/null || true
sudo systemctl disable anti-blink-display 2>/dev/null || true
sudo systemctl disable zoe-power-management 2>/dev/null || true
sudo systemctl disable zoe-touch-agent 2>/dev/null || true
sudo systemctl disable zoe-touch-kiosk 2>/dev/null || true

# Remove custom service files
echo "🗑️ Removing custom service files..."
sudo rm -f /etc/systemd/system/bulletproof-display.service
sudo rm -f /etc/systemd/system/anti-blink-display.service
sudo rm -f /etc/systemd/system/zoe-power-management.service
sudo rm -f /etc/systemd/system/zoe-touch-agent.service
sudo rm -f /etc/systemd/system/zoe-touch-kiosk.service

# Kill our running processes
echo "🔫 Killing over-engineered processes..."
pkill -f bulletproof-display 2>/dev/null || true
pkill -f anti-blink-display 2>/dev/null || true
pkill -f keep-display-on 2>/dev/null || true
pkill -f touch_panel_agent 2>/dev/null || true
pkill -f chromium-browser 2>/dev/null || true

# Clean up autostart files
echo "🧽 Cleaning autostart configurations..."
rm -f /home/pi/.config/autostart/zoe-touch.desktop 2>/dev/null || true
rm -f /home/pi/.config/autostart/zoe-kiosk.desktop 2>/dev/null || true
rm -f /home/pi/.config/autostart/zoe-final.desktop 2>/dev/null || true

# Clean up LXDE autostart
rm -f /home/pi/.config/lxsession/LXDE-pi/autostart 2>/dev/null || true

# Remove our over-engineered scripts (but keep them for reference)
echo "📦 Moving over-engineered scripts to backup..."
mkdir -p /home/pi/zoe-overengineered-backup
mv /home/pi/start-zoe-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/bulletproof-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/anti-blink-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/keep-display-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/fix-display-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/hardware-display-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/protected-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true
mv /home/pi/final-* /home/pi/zoe-overengineered-backup/ 2>/dev/null || true

# Clean up desktop shortcuts
echo "🖥️ Cleaning desktop shortcuts..."
rm -f /home/pi/Desktop/Start-Zoe* 2>/dev/null || true
rm -f /home/pi/Desktop/Zoe-* 2>/dev/null || true

# Clean up our touch panel directories
echo "📂 Cleaning touch panel directories..."
rm -rf /home/pi/zoe-touch-panel 2>/dev/null || true

# Reset boot config to sensible defaults
echo "⚙️ Resetting boot config to sensible defaults..."

# Handle both old and new boot config locations
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.overengineered-backup"

# Remove our crazy boot settings
sudo sed -i '/# NUCLEAR DISPLAY FIX/,$d' "$BOOT_CONFIG"
sudo sed -i '/# TouchKio-Quality Display Configuration/,$d' "$BOOT_CONFIG"
sudo sed -i '/# Stable Display Configuration/,$d' "$BOOT_CONFIG"
sudo sed -i '/# Anti-Blink Settings/,$d' "$BOOT_CONFIG"
sudo sed -i '/# Sensible Display Configuration for TouchKio/,$d' "$BOOT_CONFIG"

# Remove individual overengineered settings
sudo sed -i '/hdmi_ignore_edid/d' "$BOOT_CONFIG"
sudo sed -i '/hdmi_force_mode/d' "$BOOT_CONFIG"
sudo sed -i '/hdmi_timeout/d' "$BOOT_CONFIG"
sudo sed -i '/framebuffer_width/d' "$BOOT_CONFIG"
sudo sed -i '/framebuffer_height/d' "$BOOT_CONFIG"
sudo sed -i '/core_freq/d' "$BOOT_CONFIG"
sudo sed -i '/gpu_freq/d' "$BOOT_CONFIG"
sudo sed -i '/config_hdmi_boost=10/d' "$BOOT_CONFIG"
sudo sed -i '/disable_splash/d' "$BOOT_CONFIG"
sudo sed -i '/display_rotate/d' "$BOOT_CONFIG"

# Add minimal, sensible display config with 90° clockwise rotation
cat << 'SENSIBLE_EOF' | sudo tee -a "$BOOT_CONFIG"

# Sensible Display Configuration for TouchKio
hdmi_force_hotplug=1
hdmi_drive=2
config_hdmi_boost=4
disable_overscan=1
display_rotate=1
gpu_mem=64
SENSIBLE_EOF

# Reload systemd
sudo systemctl daemon-reload

# Clean up any remaining processes
sleep 2
pkill -f unclutter 2>/dev/null || true

echo ""
echo "✅ CLEANUP COMPLETE!"
echo ""
echo "🧹 **What was cleaned up:**"
echo "   ✅ All over-engineered services stopped and disabled"
echo "   ✅ Custom scripts moved to backup folder"
echo "   ✅ Autostart configurations cleaned"
echo "   ✅ Boot config reset to sensible defaults"
echo "   ✅ Desktop shortcuts removed"
echo "   ✅ Running processes killed"
echo ""
echo "📦 **Backup location:** /home/pi/zoe-overengineered-backup/"
echo "📋 **Boot config backup:** /boot/config.txt.overengineered-backup"
echo ""
echo "✨ **System is now clean and ready for TouchKio approach!"
echo ""
echo "🎯 **Next step:** Run the TouchKio modification script"
echo "   curl -s http://192.168.1.60/touchkio-zoe-mod.sh | bash"
echo ""
echo "💡 **Much cleaner foundation for the smart approach!**"
