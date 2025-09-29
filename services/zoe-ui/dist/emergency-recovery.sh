#!/bin/bash
# Emergency Recovery for Touch Panel Boot Issues
echo "🚑 Emergency recovery for touch panel..."

# Remove problematic X11 configurations
echo "🔧 Removing problematic X11 configurations..."
sudo rm -f /etc/X11/xorg.conf.d/99-zoe-touchpanel.conf 2>/dev/null || true
sudo rm -f /etc/X11/xorg.conf.d/99-zoe-display.conf 2>/dev/null || true

# Reset boot config to safe defaults
echo "⚙️ Resetting boot configuration..."
sudo cp /boot/config.txt /boot/config.txt.broken
sudo sed -i '/# TouchKio-Quality Display Configuration/,$d' /boot/config.txt

# Add minimal safe display config
cat << 'EOF' | sudo tee -a /boot/config.txt

# Safe Display Configuration
hdmi_force_hotplug=1
disable_overscan=1
display_rotate=1
gpu_mem=64
EOF

# Reset to console boot temporarily
echo "🖥️ Setting safe boot mode..."
sudo raspi-config nonint do_boot_behaviour B1  # Console autologin

# Create simple recovery kiosk script
echo "📝 Creating recovery kiosk script..."
cat > /home/pi/start-zoe-safe.sh << 'SAFE_EOF'
#!/bin/bash
# Safe Zoe Kiosk Start

export DISPLAY=:0

# Wait for X
while ! xset q &>/dev/null; do
    sleep 1
done

# Basic display settings only
xset s off -dpms s noblank

# Start simple browser
chromium-browser --start-maximized file:///home/pi/zoe-touch-interface/index.html &
SAFE_EOF

chmod +x /home/pi/start-zoe-safe.sh

# Create manual desktop start
echo "🖥️ Creating manual desktop start..."
cat > /home/pi/start-desktop-safe.sh << 'DESKTOP_EOF'
#!/bin/bash
# Start desktop safely

# Start X if not running
if [ -z "$DISPLAY" ]; then
    startx &
    sleep 5
    export DISPLAY=:0
fi

# Start safe kiosk
/home/pi/start-zoe-safe.sh
DESKTOP_EOF

chmod +x /home/pi/start-desktop-safe.sh

echo ""
echo "✅ Emergency recovery complete!"
echo ""
echo "🚑 **Recovery Steps:**"
echo "1. Reboot the Pi: sudo reboot"
echo "2. Log in to console (should auto-login)"
echo "3. Start desktop: startx"
echo "4. Run safe kiosk: /home/pi/start-zoe-safe.sh"
echo ""
echo "🔧 **If still having issues:**"
echo "• Manual desktop: /home/pi/start-desktop-safe.sh"
echo "• Check logs: sudo journalctl -b"
echo "• Safe mode: Add 'single' to boot cmdline"
echo ""
echo "💡 **After recovery:**"
echo "• Test basic functionality first"
echo "• Then reconfigure display gradually"




