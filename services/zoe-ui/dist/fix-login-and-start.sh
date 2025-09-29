#!/bin/bash
# Fix Login Screen and Start Touch Panel
echo "🔐 Fixing login and setting up auto-start..."

# Configure auto-login to desktop
echo "⚙️ Setting up auto-login to desktop..."
sudo raspi-config nonint do_boot_behaviour B4  # Desktop autologin

# Ensure user pi exists and has proper permissions
echo "👤 Checking user configuration..."
sudo usermod -a -G audio,video,input,dialout,plugdev,spi,i2c,gpio pi 2>/dev/null || true

# Create a simple, reliable kiosk script
echo "📝 Creating reliable kiosk script..."
cat > /home/pi/start-zoe-simple.sh << 'SIMPLE_EOF'
#!/bin/bash
# Simple, reliable Zoe kiosk

export DISPLAY=:0

# Wait for desktop to be ready
sleep 10

# Basic display settings
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true

# Hide cursor
unclutter -idle 1 -root &

# Start browser in kiosk mode
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --start-maximized \
    --no-first-run \
    --touch-events=enabled \
    file:///home/pi/zoe-touch-interface/index.html &

echo "Zoe kiosk started"
SIMPLE_EOF

chmod +x /home/pi/start-zoe-simple.sh

# Create autostart directory and file
echo "🚀 Setting up autostart..."
mkdir -p /home/pi/.config/autostart

cat > /home/pi/.config/autostart/zoe-kiosk.desktop << 'AUTO_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Panel
Exec=bash -c 'sleep 15 && /home/pi/start-zoe-simple.sh'
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTO_EOF

# Also add to LXDE autostart
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cat > /home/pi/.config/lxsession/LXDE-pi/autostart << 'LXDE_EOF'
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xscreensaver -no-splash
@bash -c 'sleep 15 && /home/pi/start-zoe-simple.sh'
LXDE_EOF

# Create desktop icon for manual start
echo "🖥️ Creating desktop shortcut..."
cat > /home/pi/Desktop/Start-Zoe.desktop << 'ICON_EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Start Zoe Touch Panel
Comment=Launch Zoe Touch Panel
Exec=/home/pi/start-zoe-simple.sh
Icon=applications-internet
Terminal=false
Categories=Application;
ICON_EOF

chmod +x /home/pi/Desktop/Start-Zoe.desktop

# Fix any ownership issues
echo "🔧 Fixing file permissions..."
sudo chown -R pi:pi /home/pi/.config 2>/dev/null || true
sudo chown -R pi:pi /home/pi/Desktop 2>/dev/null || true

# Test current display if available
if [ -n "$DISPLAY" ] && xset q &>/dev/null 2>&1; then
    echo "🧪 Display available - you can test now with:"
    echo "   /home/pi/start-zoe-simple.sh"
else
    echo "ℹ️ No display session - will work after desktop login"
fi

echo ""
echo "✅ Login and autostart fix complete!"
echo ""
echo "🎯 **What's been fixed:**"
echo "   ✅ Auto-login to desktop enabled"
echo "   ✅ Simple, reliable kiosk script created"
echo "   ✅ Autostart configured (15-second delay)"
echo "   ✅ Desktop shortcut for manual start"
echo "   ✅ File permissions corrected"
echo ""
echo "🚀 **Next steps:**"
echo "1. Reboot: sudo reboot"
echo "2. Should auto-login to desktop"
echo "3. Zoe kiosk will start automatically after 15 seconds"
echo ""
echo "🖥️ **Manual start (if needed):**"
echo "   • Double-click desktop icon"
echo "   • Or run: /home/pi/start-zoe-simple.sh"
echo ""
echo "💡 **If login screen still appears:**"
echo "   • Login manually as 'pi'"
echo "   • Desktop will remember auto-login for next reboot"




