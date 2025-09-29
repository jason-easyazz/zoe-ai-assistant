#!/bin/bash
# Fix Touchscreen Rotation - Portrait to Landscape
# For the working TouchKio-based touchscreen deployment

echo "🔄 Fixing Touchscreen Rotation (Portrait → Landscape)"
echo "=================================================="

# Configuration
ROTATION="${1:-0}"  # 0=landscape, 1=90° clockwise, 2=180°, 3=270° clockwise
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

echo "🎯 Target rotation: Landscape (0°)"

# Check current rotation
echo "🔍 Checking current display rotation..."
current_rotation=$(xrandr --query --verbose | grep "connected primary" | grep -o "left\|right\|inverted\|normal" | head -1)
echo "Current rotation: $current_rotation"

# Fix rotation in multiple ways for reliability

echo "⚙️ Method 1: Boot configuration (permanent)"
# Backup current config
sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.backup.$(date +%Y%m%d_%H%M%S)"

# Remove existing rotation settings
sudo sed -i '/display_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/display_hdmi_rotate/d' "$BOOT_CONFIG"

# Add landscape rotation
echo "" | sudo tee -a "$BOOT_CONFIG"
echo "# Touchscreen Landscape Rotation" | sudo tee -a "$BOOT_CONFIG"
echo "display_rotate=0" | sudo tee -a "$BOOT_CONFIG"
echo "hdmi_force_hotplug=1" | sudo tee -a "$BOOT_CONFIG"
echo "hdmi_drive=2" | sudo tee -a "$BOOT_CONFIG"

echo "✅ Boot configuration updated for landscape"

echo "⚙️ Method 2: Runtime rotation (immediate)"
# Apply rotation immediately
xrandr --output HDMI-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-A-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-2 --rotate normal 2>/dev/null || true

echo "✅ Runtime rotation applied"

echo "⚙️ Method 3: Update TouchKio configuration"
# Update TouchKio config if it exists
for config_file in "/opt/TouchKio/config.json" "/home/pi/TouchKio/config.json" "/home/pi/ZoeKio/config.json"; do
    if [ -f "$config_file" ]; then
        echo "📝 Updating TouchKio config: $config_file"
        sudo cp "$config_file" "${config_file}.backup.$(date +%Y%m%d_%H%M%S)"
        
        # Update rotation in JSON config
        sudo python3 -c "
import json
import sys

config_file = '$config_file'
try:
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    config['rotation'] = 0  # Landscape
    if 'display' not in config:
        config['display'] = {}
    config['display']['rotation'] = 0
    config['display']['orientation'] = 'landscape'
    
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print('✅ TouchKio config updated for landscape')
except Exception as e:
    print(f'⚠️ Could not update TouchKio config: {e}')
"
    fi
done

echo "⚙️ Method 4: Update startup scripts"
# Update any TouchKio startup scripts
for script_file in "/opt/TouchKio/start-zoe-kio.sh" "/home/pi/TouchKio/start-zoe-kio.sh" "/home/pi/ZoeKio/start-zoe-kio.sh"; do
    if [ -f "$script_file" ]; then
        echo "📝 Updating startup script: $script_file"
        sudo cp "$script_file" "${script_file}.backup.$(date +%Y%m%d_%H%M%S)"
        
        # Replace rotation commands
        sudo sed -i 's/--rotate right/--rotate normal/g' "$script_file"
        sudo sed -i 's/--rotate left/--rotate normal/g' "$script_file"
        sudo sed -i 's/--rotate inverted/--rotate normal/g' "$script_file"
        sudo sed -i 's/rotate right/rotate normal/g' "$script_file"
        sudo sed -i 's/rotate left/rotate normal/g' "$script_file"
        sudo sed -i 's/rotate inverted/rotate normal/g' "$script_file"
        
        echo "✅ Startup script updated for landscape"
    fi
done

echo "⚙️ Method 5: Create landscape-specific startup"
# Create a landscape-specific startup script
cat > /home/pi/fix-rotation-startup.sh << 'LANDSCAPE_EOF'
#!/bin/bash
# Force landscape rotation on startup

export DISPLAY=:0

# Wait for display to be ready
sleep 5

# Force landscape rotation
echo "🔄 Applying landscape rotation..."
xrandr --output HDMI-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-A-1 --rotate normal 2>/dev/null || true
xrandr --output HDMI-2 --rotate normal 2>/dev/null || true

# Also try with display names from xrandr
for output in $(xrandr --query | grep " connected" | cut -d' ' -f1); do
    xrandr --output "$output" --rotate normal 2>/dev/null || true
done

echo "✅ Landscape rotation applied"
LANDSCAPE_EOF

chmod +x /home/pi/fix-rotation-startup.sh

# Add to autostart
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/fix-rotation.desktop << 'AUTOSTART_EOF'
[Desktop Entry]
Type=Application
Name=Fix Display Rotation
Exec=/home/pi/fix-rotation-startup.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTOSTART_EOF

echo "✅ Landscape rotation autostart configured"

echo ""
echo "🎯 **Rotation Fix Complete!**"
echo ""
echo "✅ **What was fixed:**"
echo "   • Boot configuration set to landscape (display_rotate=0)"
echo "   • Runtime rotation applied immediately"
echo "   • TouchKio configuration updated"
echo "   • Startup scripts updated"
echo "   • Autostart rotation fix added"
echo ""
echo "🔄 **To apply changes:**"
echo "   1. Reboot the touchscreen: sudo reboot"
echo "   2. Or test immediately: xrandr --output HDMI-1 --rotate normal"
echo ""
echo "🧪 **Test rotation:**"
echo "   xrandr --query --verbose | grep connected"
echo ""
echo "📁 **Backup files created:**"
echo "   • $(ls -t /boot/firmware/config.txt.backup.* 2>/dev/null | head -1 || echo '/boot/config.txt.backup.*')"
echo "   • TouchKio config backups"
echo "   • Startup script backups"
echo ""
echo "💡 **If rotation still doesn't work:**"
echo "   1. Check: xrandr --query"
echo "   2. Try: xrandr --output [DISPLAY_NAME] --rotate normal"
echo "   3. Edit: sudo nano $BOOT_CONFIG"
echo ""


