#!/bin/bash
# Fix Display Issues - Screen Blanking, Flashing, and Rotation
echo "🖥️ Fixing display issues for touch panel..."

# Get current screen orientation preference
echo "🔄 Screen rotation configuration..."
echo "Current display configuration:"
if command -v xrandr >/dev/null 2>&1 && [ -n "$DISPLAY" ]; then
    xrandr --current 2>/dev/null | grep "connected" || echo "No display detected"
fi

echo ""
echo "What rotation do you need?"
echo "0) No rotation (0°)"
echo "1) Rotate 90° clockwise"
echo "2) Rotate 180° (upside down)"  
echo "3) Rotate 270° clockwise (90° counter-clockwise)"
echo ""
read -p "Enter choice (0-3) or press Enter for no rotation: " -n 1 rotation_choice
echo ""

# Set rotation value
case $rotation_choice in
    1) ROTATION="1" ;;
    2) ROTATION="2" ;;
    3) ROTATION="3" ;;
    *) ROTATION="0" ;;
esac

echo "Selected rotation: ${ROTATION} (${ROTATION}0° clockwise)"

# Fix boot configuration for display
echo "⚙️ Configuring boot display settings..."

# Backup current config
sudo cp /boot/config.txt /boot/config.txt.backup

# Remove old display settings
sudo sed -i '/hdmi_blanking/d' /boot/config.txt
sudo sed -i '/hdmi_force_hotplug/d' /boot/config.txt
sudo sed -i '/hdmi_group/d' /boot/config.txt
sudo sed -i '/hdmi_mode/d' /boot/config.txt
sudo sed -i '/display_rotate/d' /boot/config.txt
sudo sed -i '/disable_overscan/d' /boot/config.txt

# Add optimized display settings
cat << EOF | sudo tee -a /boot/config.txt

# Zoe Touch Panel Display Configuration
hdmi_force_hotplug=1
hdmi_blanking=2
disable_overscan=1
display_rotate=$ROTATION

# Prevent screen flickering
hdmi_drive=2
config_hdmi_boost=4

# GPU memory for smooth display
gpu_mem=128
EOF

# Fix X11 display configuration
echo "🖥️ Configuring X11 display settings..."

# Create xorg configuration
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/99-zoe-display.conf > /dev/null << XORG_EOF
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection

Section "Extensions"
    Option "DPMS" "Disable"
EndSection
XORG_EOF

# Update the kiosk script with better display management
echo "🔧 Updating kiosk script with display fixes..."
cat > /home/pi/start-zoe-kiosk.sh << 'KIOSK_EOF'
#!/bin/bash
# Enhanced Zoe Kiosk with Display Management

# Set display
export DISPLAY=:0

# Wait for display
echo "Waiting for display to be ready..."
while ! xset q &>/dev/null; do
    sleep 1
done

echo "Configuring display settings..."

# Comprehensive display configuration
xset s off                    # Disable screensaver
xset s noblank               # Disable screen blanking  
xset -dpms                   # Disable power management
xset s 0 0                   # Set screensaver timeout to 0

# Hide cursor immediately and keep it hidden
unclutter -idle 0.1 -root -noevents &

# Kill any existing browser instances
pkill -f chromium-browser 2>/dev/null || true
sleep 2

# Set screen rotation if needed (X11 method as backup)
ROTATION_MAPPING=("normal" "right" "inverted" "left")
if [ -n "$1" ] && [ "$1" -ge 0 ] && [ "$1" -le 3 ]; then
    xrandr --output HDMI-1 --rotate ${ROTATION_MAPPING[$1]} 2>/dev/null || true
    xrandr --output HDMI-A-1 --rotate ${ROTATION_MAPPING[$1]} 2>/dev/null || true
fi

# Start window manager if not running
if ! pgrep -x "openbox" > /dev/null; then
    openbox &
    sleep 2
fi

# Additional display stabilization
sleep 1

echo "Starting Chromium kiosk..."

# Start Chromium with optimized settings for touch panels
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-features=TranslateUI,VizDisplayCompositor \
    --disable-extensions \
    --disable-plugins \
    --disable-web-security \
    --user-data-dir=/tmp/chromium-kiosk \
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
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows \
    --disable-features=TranslateUI \
    --aggressive-cache-discard \
    --memory-pressure-off \
    --max_old_space_size=4096 \
    --js-flags="--max-old-space-size=4096" \
    file:///home/pi/zoe-touch-interface/index.html &

echo "Zoe kiosk started successfully"

# Keep the script running and monitor for crashes
BROWSER_PID=$!
while kill -0 $BROWSER_PID 2>/dev/null; do
    # Refresh display settings every 30 seconds
    xset s off -dpms s noblank 2>/dev/null || true
    sleep 30
done

echo "Browser process ended, restarting in 5 seconds..."
sleep 5
exec $0 "$@"
KIOSK_EOF

chmod +x /home/pi/start-zoe-kiosk.sh

# Create a rotation-aware launcher
echo "🔄 Creating rotation-aware launcher..."
cat > /home/pi/start-zoe-rotated.sh << ROTATED_EOF
#!/bin/bash
# Start Zoe with specific rotation
/home/pi/start-zoe-kiosk.sh $ROTATION
ROTATED_EOF

chmod +x /home/pi/start-zoe-rotated.sh

# Update autostart to use rotation
echo "⚙️ Updating autostart with rotation..."
cat > /home/pi/.config/autostart/zoe-touch.desktop << AUTO_EOF
[Desktop Entry]
Type=Application
Name=Zoe Touch Panel
Exec=bash -c 'sleep 15 && /home/pi/start-zoe-rotated.sh'
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
AUTO_EOF

# Update LXDE autostart
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cat > /home/pi/.config/lxsession/LXDE-pi/autostart << LXDE_EOF
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xscreensaver -no-splash
@point-rpi
@bash -c 'sleep 15 && /home/pi/start-zoe-rotated.sh'
LXDE_EOF

# Create immediate test with rotation
echo "🧪 Creating immediate test script..."
cat > /home/pi/test-zoe-rotated.sh << TEST_EOF
#!/bin/bash
echo "🧪 Testing Zoe with rotation $ROTATION..."

# Apply current session rotation if display available
if [ -n "\$DISPLAY" ] && xset q &>/dev/null; then
    echo "Applying rotation..."
    ROTATION_MAPPING=("normal" "right" "inverted" "left")
    xrandr --output HDMI-1 --rotate \${ROTATION_MAPPING[$ROTATION]} 2>/dev/null || true
    xrandr --output HDMI-A-1 --rotate \${ROTATION_MAPPING[$ROTATION]} 2>/dev/null || true
    
    # Apply display settings
    xset s off -dpms s noblank
    
    echo "Starting Zoe interface..."
    /home/pi/start-zoe-kiosk.sh $ROTATION
else
    echo "No display available - settings will apply on reboot"
fi
TEST_EOF

chmod +x /home/pi/test-zoe-rotated.sh

# Create desktop icon with rotation
cat > /home/pi/Desktop/Zoe-Touch-Rotated.desktop << ICON_EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Zoe Touch (Rotated)
Comment=Start Zoe Touch Panel with rotation
Exec=/home/pi/test-zoe-rotated.sh
Icon=applications-internet
Terminal=true
Categories=Application;
ICON_EOF

chmod +x /home/pi/Desktop/Zoe-Touch-Rotated.desktop

# Apply current session fixes if in X
if [ -n "$DISPLAY" ] && xset q &>/dev/null; then
    echo "🔧 Applying immediate display fixes..."
    
    # Apply rotation now
    ROTATION_MAPPING=("normal" "right" "inverted" "left")
    xrandr --output HDMI-1 --rotate ${ROTATION_MAPPING[$ROTATION]} 2>/dev/null || true
    xrandr --output HDMI-A-1 --rotate ${ROTATION_MAPPING[$ROTATION]} 2>/dev/null || true
    
    # Fix screen blanking immediately
    xset s off
    xset -dpms  
    xset s noblank
    
    echo "✅ Display settings applied to current session"
else
    echo "ℹ️ Display settings will apply on next boot/session"
fi

echo ""
echo "✅ Display issues fix complete!"
echo ""
echo "🖥️ **Fixed issues:**"
echo "   ✅ Screen blanking disabled"
echo "   ✅ Screen flashing reduced"
echo "   ✅ Display rotation: ${ROTATION}0° clockwise"
echo "   ✅ Power management disabled"
echo "   ✅ Cursor auto-hiding enhanced"
echo ""
echo "🚀 **To apply all changes:**"
echo "   sudo reboot"
echo ""
echo "🧪 **To test now (if in desktop):**"
echo "   /home/pi/test-zoe-rotated.sh"
echo ""
echo "💡 **Settings configured:**"
echo "   • Boot rotation: ${ROTATION}0° clockwise"
echo "   • Screen blanking: Disabled" 
echo "   • Power management: Disabled"
echo "   • Auto-restart: Enabled"




