#!/bin/bash
# Fix Kiosk Display Issues - Proper X11 Integration
echo "🖥️ Fixing kiosk display configuration..."

# Create improved kiosk startup script
echo "📝 Creating improved kiosk script..."
cat > /home/pi/start-zoe-kiosk.sh << 'KIOSK_EOF'
#!/bin/bash
# Enhanced Zoe Kiosk Startup with Display Detection

# Wait for display to be available
echo "Waiting for display..."
while [ -z "$DISPLAY" ]; do
    export DISPLAY=:0
    sleep 1
done

# Check if X is running
if ! xset q &>/dev/null; then
    echo "X server not available, starting in desktop mode"
    # Launch via desktop if X not available
    if [ -f "/usr/bin/lxsession" ]; then
        DISPLAY=:0 lxsession &
        sleep 5
    fi
fi

# Wait for X to be ready
while ! xset q &>/dev/null; do
    sleep 1
done

echo "Display ready, starting kiosk..."

# Hide cursor
DISPLAY=:0 unclutter -idle 0.1 -root &

# Disable screen blanking
DISPLAY=:0 xset s off
DISPLAY=:0 xset -dpms
DISPLAY=:0 xset s noblank

# Start window manager if not running
if ! pgrep -x "openbox" > /dev/null; then
    DISPLAY=:0 openbox &
    sleep 2
fi

# Start Chromium in kiosk mode
DISPLAY=:0 chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-features=TranslateUI \
    --disable-extensions \
    --disable-plugins \
    --disable-web-security \
    --user-data-dir=/tmp/chromium-kiosk \
    --disable-features=VizDisplayCompositor \
    --start-maximized \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --no-first-run \
    --fast \
    --fast-start \
    --disable-default-apps \
    --disable-popup-blocking \
    --allow-running-insecure-content \
    --touch-events=enabled \
    --force-device-scale-factor=1 \
    file:///home/pi/zoe-touch-interface/index.html &

echo "Kiosk started successfully"
KIOSK_EOF

chmod +x /home/pi/start-zoe-kiosk.sh

# Create a desktop entry for the kiosk
echo "🖥️ Creating desktop launcher..."
mkdir -p /home/pi/Desktop
cat > /home/pi/Desktop/zoe-kiosk.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Zoe Touch Panel
Comment=Start Zoe Touch Panel Kiosk
Exec=/home/pi/start-zoe-kiosk.sh
Icon=applications-internet
Terminal=false
Categories=Application;Network;
DESKTOP_EOF

chmod +x /home/pi/Desktop/zoe-kiosk.desktop

# Update autostart to wait for desktop
echo "⚙️ Updating autostart configuration..."
cat > /home/pi/.config/autostart/zoe-touch.desktop << 'AUTO_EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Interface
Exec=bash -c 'sleep 10 && /home/pi/start-zoe-kiosk.sh'
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTO_EOF

# Create a simple manual launcher for testing
echo "🧪 Creating test launcher..."
cat > /home/pi/test-zoe-interface.sh << 'TEST_EOF'
#!/bin/bash
# Simple test of Zoe interface in browser
echo "🧪 Testing Zoe interface in browser..."

# Just open in regular browser window
DISPLAY=:0 chromium-browser \
    --new-window \
    --start-maximized \
    file:///home/pi/zoe-touch-interface/index.html &

echo "✅ Zoe interface opened in browser"
echo "   For full kiosk mode, reboot the system"
TEST_EOF

chmod +x /home/pi/test-zoe-interface.sh

# Add to .bashrc for easy access
echo "📝 Adding convenience commands..."
if ! grep -q "alias zoe-kiosk" /home/pi/.bashrc; then
    echo "" >> /home/pi/.bashrc
    echo "# Zoe Touch Panel Commands" >> /home/pi/.bashrc
    echo "alias zoe-kiosk='/home/pi/start-zoe-kiosk.sh'" >> /home/pi/.bashrc
    echo "alias zoe-test='/home/pi/test-zoe-interface.sh'" >> /home/pi/.bashrc
    echo "alias zoe-status='sudo systemctl status zoe-touch-agent'" >> /home/pi/.bashrc
fi

# Check if we're in a desktop session
echo "🔍 Checking current session..."
if [ -n "$DISPLAY" ] && xset q &>/dev/null; then
    echo "✅ Desktop session detected"
    echo "🧪 You can test with: /home/pi/test-zoe-interface.sh"
else
    echo "ℹ️ No desktop session - kiosk will work after reboot"
fi

echo ""
echo "✅ Kiosk display fix complete!"
echo ""
echo "🎯 **Available commands:**"
echo "   • Test interface: /home/pi/test-zoe-interface.sh"
echo "   • Start kiosk: /home/pi/start-zoe-kiosk.sh (needs desktop)"
echo "   • Check agent: sudo systemctl status zoe-touch-agent"
echo ""
echo "🖥️ **Desktop integration:**"
echo "   • Desktop launcher created on desktop"
echo "   • Auto-start configured for reboot"
echo "   • Convenience aliases added to .bashrc"
echo ""
echo "🚀 **For full kiosk experience:**"
echo "   sudo reboot"
echo ""
echo "🧪 **To test now (if in desktop):**"
echo "   /home/pi/test-zoe-interface.sh"




