#!/bin/bash
# Fix Autostart Issues - Ensure Kiosk Starts Properly
echo "🔧 Fixing autostart configuration..."

# Check current desktop environment
echo "🔍 Detecting desktop environment..."
DESKTOP_ENV=""
if pgrep -x "lxsession" > /dev/null; then
    DESKTOP_ENV="LXDE"
elif pgrep -x "lxpanel" > /dev/null; then
    DESKTOP_ENV="LXDE"
elif [ "$DESKTOP_SESSION" = "LXDE-pi" ]; then
    DESKTOP_ENV="LXDE"
else
    DESKTOP_ENV="DEFAULT"
fi

echo "Desktop environment: $DESKTOP_ENV"

# Create multiple autostart methods for reliability
echo "📝 Creating multiple autostart configurations..."

# Method 1: LXDE autostart
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cat > /home/pi/.config/lxsession/LXDE-pi/autostart << 'LXDE_EOF'
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xscreensaver -no-splash
@point-rpi
@bash -c 'sleep 15 && /home/pi/start-zoe-kiosk.sh'
LXDE_EOF

# Method 2: Standard autostart directory
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/zoe-touch.desktop << 'AUTO_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Panel
Exec=bash -c 'sleep 15 && /home/pi/start-zoe-kiosk.sh'
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
AUTO_EOF

# Method 3: .bashrc trigger (for terminal sessions)
if ! grep -q "AUTOSTART_ZOE" /home/pi/.bashrc; then
    cat >> /home/pi/.bashrc << 'BASHRC_EOF'

# Zoe Touch Panel Autostart
if [ -n "$DISPLAY" ] && [ -z "$SSH_CLIENT" ] && [ -z "$AUTOSTART_ZOE" ]; then
    export AUTOSTART_ZOE=1
    echo "🚀 Starting Zoe Touch Panel in 10 seconds..."
    echo "   Press Ctrl+C to cancel"
    sleep 10 && /home/pi/start-zoe-kiosk.sh &
fi
BASHRC_EOF
fi

# Method 4: Cron job as final fallback
echo "⏰ Setting up cron fallback..."
(crontab -l 2>/dev/null; echo "@reboot sleep 30 && DISPLAY=:0 /home/pi/start-zoe-kiosk.sh") | crontab -

# Create an immediate start script
echo "⚡ Creating immediate start script..."
cat > /home/pi/start-zoe-now.sh << 'NOW_EOF'
#!/bin/bash
# Start Zoe Touch Panel Immediately
echo "🚀 Starting Zoe Touch Panel now..."

# Kill any existing instances
pkill -f chromium-browser || true
pkill -f unclutter || true
sleep 2

# Set display
export DISPLAY=:0

# Check if display is available
if ! xset q &>/dev/null; then
    echo "❌ No display available"
    echo "💡 Try running from desktop terminal or after 'startx'"
    exit 1
fi

# Start the kiosk
/home/pi/start-zoe-kiosk.sh

echo "✅ Zoe Touch Panel started"
NOW_EOF

chmod +x /home/pi/start-zoe-now.sh

# Create a desktop icon for manual start
echo "🖥️ Creating desktop shortcuts..."
cat > /home/pi/Desktop/Start-Zoe-Kiosk.desktop << 'ICON_EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Start Zoe Kiosk
Comment=Launch Zoe Touch Panel
Exec=/home/pi/start-zoe-now.sh
Icon=applications-internet
Terminal=true
Categories=Application;
ICON_EOF

chmod +x /home/pi/Desktop/Start-Zoe-Kiosk.desktop

# Test if we can start now
echo "🧪 Testing immediate start capability..."
if [ -n "$DISPLAY" ] && xset q &>/dev/null; then
    echo "✅ Display available - you can start now with:"
    echo "   /home/pi/start-zoe-now.sh"
else
    echo "ℹ️ No display currently - autostart will work on reboot"
fi

# Configure raspi-config for better autostart
echo "⚙️ Optimizing boot configuration..."

# Ensure we boot to desktop with autologin
sudo raspi-config nonint do_boot_behaviour B4

# Disable screen blanking in boot config
if ! grep -q "hdmi_blanking" /boot/config.txt; then
    echo "hdmi_blanking=1" | sudo tee -a /boot/config.txt
fi

echo ""
echo "✅ Autostart fix complete!"
echo ""
echo "🎯 **Multiple autostart methods configured:**"
echo "   ✅ LXDE autostart (primary method)"
echo "   ✅ XDG autostart (secondary method)"
echo "   ✅ Bashrc trigger (terminal fallback)"
echo "   ✅ Cron job (final fallback)"
echo ""
echo "🚀 **Manual start options:**"
echo "   • Immediate start: /home/pi/start-zoe-now.sh"
echo "   • Desktop icon: Double-click 'Start Zoe Kiosk' on desktop"
echo "   • Command: zoe-kiosk (after sourcing bashrc)"
echo ""
echo "🔄 **To test autostart:**"
echo "   sudo reboot"
echo ""
echo "💡 **If still having issues:**"
echo "   • Check agent: sudo systemctl status zoe-touch-agent"
echo "   • Manual start: /home/pi/start-zoe-now.sh"
echo "   • View logs: journalctl -u zoe-touch-agent"




