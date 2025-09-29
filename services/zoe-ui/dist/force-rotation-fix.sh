#!/bin/bash
# Force Display Rotation - Direct File Editing
# Simple, direct approach to force landscape rotation

echo "🔧 FORCE ROTATION FIX - Direct File Editing"
echo "=========================================="

# Method 1: Force TouchKio Config (if it exists)
echo "📝 Method 1: Force TouchKio Configuration"
for config_file in "/opt/TouchKio/config.json" "/home/pi/TouchKio/config.json" "/home/pi/ZoeKio/config.json"; do
    if [ -f "$config_file" ]; then
        echo "🔧 Editing: $config_file"
        sudo sed -i 's/"rotation": [0-9]/"rotation": 0/g' "$config_file"
        sudo sed -i 's/"display_rotation": [0-9]/"display_rotation": 0/g' "$config_file"
        echo "✅ Updated: $config_file"
    fi
done

# Method 2: Force Boot Configuration
echo ""
echo "📝 Method 2: Force Boot Configuration"
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

echo "🔧 Editing: $BOOT_CONFIG"

# Remove ALL rotation settings
sudo sed -i '/display_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/display_hdmi_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/lcd_rotate/d' "$BOOT_CONFIG"

# Force add landscape rotation at the end
echo "" | sudo tee -a "$BOOT_CONFIG"
echo "# FORCE LANDSCAPE ROTATION" | sudo tee -a "$BOOT_CONFIG"
echo "display_rotate=0" | sudo tee -a "$BOOT_CONFIG"

echo "✅ Boot config forced to landscape"

# Method 3: Force LXDE Autostart
echo ""
echo "📝 Method 3: Force LXDE Autostart"
sudo mkdir -p /etc/xdg/lxsession/LXDE-pi

# Clear any existing rotation commands
sudo sed -i '/xrandr.*rotate/d' /etc/xdg/lxsession/LXDE-pi/autostart 2>/dev/null || true

# Force add landscape rotation
echo "@xrandr --output HDMI-1 --rotate normal" | sudo tee -a /etc/xdg/lxsession/LXDE-pi/autostart
echo "@xrandr --output HDMI-A-1 --rotate normal" | sudo tee -a /etc/xdg/lxsession/LXDE-pi/autostart

echo "✅ LXDE autostart forced to landscape"

# Method 4: Force Runtime Rotation
echo ""
echo "📝 Method 4: Force Runtime Rotation"
export DISPLAY=:0

# Force rotate all possible displays
xrandr --output HDMI-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-A-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-2 --rotate normal 2>/dev/null || true
xrandr --output eDP-1 --rotate normal 2>/dev/null || true

# Try to get actual display names and rotate them
for output in $(xrandr --query | grep " connected" | cut -d' ' -f1); do
    echo "🔄 Forcing rotation on: $output"
    xrandr --output "$output" --rotate normal 2>/dev/null || true
done

echo "✅ Runtime rotation forced"

# Method 5: Create Force Script
echo ""
echo "📝 Method 5: Create Force Rotation Script"
cat > /home/pi/force-rotate.sh << 'FORCE_EOF'
#!/bin/bash
# Force rotation script
export DISPLAY=:0
sleep 2
xrandr --output HDMI-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-A-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-2 --rotate normal 2>/dev/null || true
for output in $(xrandr --query | grep " connected" | cut -d' ' -f1); do
    xrandr --output "$output" --rotate normal 2>/dev/null || true
done
echo "Forced rotation applied"
FORCE_EOF

chmod +x /home/pi/force-rotate.sh

# Add to autostart
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/force-rotate.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Force Rotation
Exec=/home/pi/force-rotate.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
DESKTOP_EOF

echo "✅ Force rotation script created and added to autostart"

# Method 6: Manual Commands for Immediate Effect
echo ""
echo "📝 Method 6: Manual Commands (Run These Now)"
echo "============================================="
echo ""
echo "Run these commands manually on your touchscreen:"
echo ""
echo "# Check current displays:"
echo "xrandr --query"
echo ""
echo "# Force rotate (replace HDMI-1 with your actual display):"
echo "xrandr --output HDMI-1 --rotate normal"
echo ""
echo "# Or try all displays:"
echo "for output in \$(xrandr --query | grep ' connected' | cut -d' ' -f1); do"
echo "    xrandr --output \$output --rotate normal"
echo "done"
echo ""

# Show current status
echo "📊 Current Status:"
echo "=================="
echo "Boot config rotation setting:"
grep "display_rotate" "$BOOT_CONFIG" || echo "Not found in boot config"
echo ""
echo "Current display status:"
xrandr --query --verbose | grep "connected" | head -3

echo ""
echo "🎯 **FORCE ROTATION COMPLETE!**"
echo ""
echo "✅ **Files Modified:**"
echo "   • TouchKio configs: rotation forced to 0"
echo "   • Boot config: display_rotate=0 added"
echo "   • LXDE autostart: landscape rotation added"
echo "   • Force script: /home/pi/force-rotate.sh created"
echo "   • Runtime rotation: applied immediately"
echo ""
echo "🔄 **Next Steps:**"
echo "   1. Run manual commands above"
echo "   2. Or reboot: sudo reboot"
echo "   3. Or run: /home/pi/force-rotate.sh"
echo ""
echo "💡 **If still not working:**"
echo "   1. Check: xrandr --query"
echo "   2. Find your display name"
echo "   3. Run: xrandr --output [DISPLAY_NAME] --rotate normal"
echo ""
