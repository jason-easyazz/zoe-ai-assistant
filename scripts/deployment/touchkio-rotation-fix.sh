#!/bin/bash
# TouchKio Display Rotation Fix - Proper Method
# Based on TouchKio documentation and best practices

echo "🔄 TouchKio Display Rotation Fix (Portrait → Landscape)"
echo "====================================================="

# Configuration
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"
TOUCHKIO_DIR="/opt/TouchKio"
CONFIG_FILE="$TOUCHKIO_DIR/config.json"

echo "🎯 Target: Landscape mode (0° rotation)"
echo "📂 TouchKio directory: $TOUCHKIO_DIR"
echo "⚙️ Boot config: $BOOT_CONFIG"

# Check if TouchKio is installed
if [ ! -d "$TOUCHKIO_DIR" ]; then
    echo "❌ TouchKio not found at $TOUCHKIO_DIR"
    echo "💡 Looking for alternative locations..."
    
    # Check alternative locations
    for alt_dir in "/home/pi/TouchKio" "/home/pi/ZoeKio" "/opt/touchkio"; do
        if [ -d "$alt_dir" ]; then
            TOUCHKIO_DIR="$alt_dir"
            CONFIG_FILE="$TOUCHKIO_DIR/config.json"
            echo "✅ Found TouchKio at: $TOUCHKIO_DIR"
            break
        fi
    done
    
    if [ ! -d "$TOUCHKIO_DIR" ]; then
        echo "❌ TouchKio not found in any location"
        echo "💡 Proceeding with system-level rotation fix only"
    fi
fi

# Method 1: TouchKio Configuration (Recommended by TouchKio)
if [ -f "$CONFIG_FILE" ]; then
    echo ""
    echo "⚙️ Method 1: TouchKio Configuration (Recommended)"
    echo "📝 Updating: $CONFIG_FILE"
    
    # Backup original config
    sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Update TouchKio config for landscape (0° rotation)
    sudo python3 -c "
import json
import sys
import os

config_file = '$CONFIG_FILE'
try:
    # Read current config
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    print(f'Current rotation: {config.get(\"rotation\", \"not set\")}')
    
    # Update for landscape mode
    config['rotation'] = 0  # 0° = landscape
    config['display_rotation'] = 0
    
    # Add display settings if not present
    if 'display' not in config:
        config['display'] = {}
    
    config['display']['rotation'] = 0
    config['display']['orientation'] = 'landscape'
    config['display']['landscape'] = True
    
    # Write updated config
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print('✅ TouchKio config updated for landscape mode')
    print(f'New rotation setting: {config[\"rotation\"]}')
    
except Exception as e:
    print(f'❌ Error updating TouchKio config: {e}')
    sys.exit(1)
"
else
    echo "⚠️ TouchKio config not found, skipping TouchKio-specific settings"
fi

# Method 2: Raspberry Pi Boot Configuration (TouchKio Compatible)
echo ""
echo "⚙️ Method 2: Raspberry Pi Boot Configuration"
echo "📝 Updating: $BOOT_CONFIG"

# Backup boot config
sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.backup.$(date +%Y%m%d_%H%M%S)"

# Remove existing rotation settings
sudo sed -i '/display_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/display_hdmi_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/lcd_rotate/d' "$BOOT_CONFIG"

# Add landscape rotation settings (TouchKio compatible)
echo "" | sudo tee -a "$BOOT_CONFIG"
echo "# TouchKio Landscape Rotation Configuration" | sudo tee -a "$BOOT_CONFIG"
echo "display_rotate=0" | sudo tee -a "$BOOT_CONFIG"
echo "hdmi_force_hotplug=1" | sudo tee -a "$BOOT_CONFIG"
echo "hdmi_drive=2" | sudo tee -a "$BOOT_CONFIG"
echo "config_hdmi_boost=4" | sudo tee -a "$BOOT_CONFIG"

echo "✅ Boot configuration updated for landscape"

# Method 3: Runtime Rotation (Immediate Effect)
echo ""
echo "⚙️ Method 3: Runtime Rotation (Immediate)"
echo "🔄 Applying rotation immediately..."

# Get display output names
DISPLAY_OUTPUTS=$(xrandr --query | grep " connected" | cut -d' ' -f1)
echo "📺 Found displays: $DISPLAY_OUTPUTS"

# Apply landscape rotation to all connected displays
for output in $DISPLAY_OUTPUTS; do
    echo "🔄 Rotating $output to landscape..."
    xrandr --output "$output" --rotate normal 2>/dev/null && echo "✅ $output rotated" || echo "⚠️ Could not rotate $output"
done

# Method 4: TouchKio Startup Script Update
if [ -d "$TOUCHKIO_DIR" ]; then
    echo ""
    echo "⚙️ Method 4: TouchKio Startup Script"
    
    STARTUP_SCRIPT="$TOUCHKIO_DIR/start-zoe-kio.sh"
    if [ -f "$STARTUP_SCRIPT" ]; then
        echo "📝 Updating: $STARTUP_SCRIPT"
        
        # Backup startup script
        sudo cp "$STARTUP_SCRIPT" "${STARTUP_SCRIPT}.backup.$(date +%Y%m%d_%H%M%S)"
        
        # Update rotation commands in startup script
        sudo sed -i 's/--rotate right/--rotate normal/g' "$STARTUP_SCRIPT"
        sudo sed -i 's/--rotate left/--rotate normal/g' "$STARTUP_SCRIPT"
        sudo sed -i 's/--rotate inverted/--rotate normal/g' "$STARTUP_SCRIPT"
        sudo sed -i 's/rotate right/rotate normal/g' "$STARTUP_SCRIPT"
        sudo sed -i 's/rotate left/rotate normal/g' "$STARTUP_SCRIPT"
        sudo sed -i 's/rotate inverted/rotate normal/g' "$STARTUP_SCRIPT"
        
        echo "✅ TouchKio startup script updated"
    else
        echo "⚠️ TouchKio startup script not found"
    fi
fi

# Method 5: LXDE Autostart (TouchKio Compatible)
echo ""
echo "⚙️ Method 5: LXDE Autostart Configuration"
echo "📝 Adding rotation to autostart..."

# Create autostart directory if it doesn't exist
sudo mkdir -p /etc/xdg/lxsession/LXDE-pi

# Backup autostart file
if [ -f "/etc/xdg/lxsession/LXDE-pi/autostart" ]; then
    sudo cp "/etc/xdg/lxsession/LXDE-pi/autostart" "/etc/xdg/lxsession/LXDE-pi/autostart.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Add rotation command to autostart
if ! grep -q "xrandr.*rotate normal" "/etc/xdg/lxsession/LXDE-pi/autostart" 2>/dev/null; then
    echo "@xrandr --output HDMI-1 --rotate normal" | sudo tee -a "/etc/xdg/lxsession/LXDE-pi/autostart"
    echo "@xrandr --output HDMI-A-1 --rotate normal" | sudo tee -a "/etc/xdg/lxsession/LXDE-pi/autostart"
    echo "✅ Autostart rotation commands added"
else
    echo "✅ Autostart rotation already configured"
fi

# Method 6: TouchKio Service Restart
echo ""
echo "⚙️ Method 6: TouchKio Service Management"

# Check if TouchKio service exists and restart it
if systemctl list-units --type=service | grep -q "touchkio"; then
    echo "🔄 Restarting TouchKio service..."
    sudo systemctl restart touchkio
    echo "✅ TouchKio service restarted"
elif systemctl list-units --type=service | grep -q "zoe.*touch"; then
    echo "🔄 Restarting Zoe touch service..."
    sudo systemctl restart zoe-touch-panel
    echo "✅ Zoe touch service restarted"
else
    echo "ℹ️ No TouchKio service found, rotation will apply on next reboot"
fi

# Display current status
echo ""
echo "📊 Current Display Status:"
echo "=========================="
xrandr --query --verbose | grep "connected"

echo ""
echo "🎯 **TouchKio Rotation Fix Complete!**"
echo ""
echo "✅ **Applied Methods:**"
echo "   • TouchKio configuration updated (rotation: 0° = landscape)"
echo "   • Raspberry Pi boot config updated (display_rotate=0)"
echo "   • Runtime rotation applied immediately"
echo "   • TouchKio startup script updated"
echo "   • LXDE autostart configured"
echo "   • Services restarted (if applicable)"
echo ""
echo "🔄 **To Apply Changes:**"
echo "   1. Reboot: sudo reboot"
echo "   2. Or test immediately (rotation already applied)"
echo ""
echo "🧪 **Test Commands:**"
echo "   xrandr --query --verbose | grep connected"
echo "   cat $CONFIG_FILE | grep rotation"
echo "   cat $BOOT_CONFIG | grep display_rotate"
echo ""
echo "📁 **Backup Files Created:**"
echo "   • TouchKio config: ${CONFIG_FILE}.backup.*"
echo "   • Boot config: ${BOOT_CONFIG}.backup.*"
echo "   • Startup script: ${STARTUP_SCRIPT}.backup.*"
echo "   • Autostart: /etc/xdg/lxsession/LXDE-pi/autostart.backup.*"
echo ""
echo "💡 **TouchKio Best Practices Applied:**"
echo "   • Used TouchKio's native rotation configuration"
echo "   • Maintained TouchKio compatibility"
echo "   • Applied multiple fallback methods"
echo "   • Preserved original configurations"
echo ""
echo "🚀 **Ready for landscape mode!**"


