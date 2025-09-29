#!/bin/bash
# Emergency Display Recovery Script
echo "🚨 EMERGENCY DISPLAY RECOVERY"
echo "================================"

# Handle both old and new boot config locations
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

echo "📋 Using boot config: $BOOT_CONFIG"

# Backup current config
echo "💾 Creating backup..."
sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.emergency-backup-$(date +%Y%m%d-%H%M%S)"

# Remove ALL display rotation settings
echo "🔄 Removing display rotation..."
sudo sed -i '/display_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/display_hdmi_rotate/d' "$BOOT_CONFIG"
sudo sed -i '/display_lcd_rotate/d' "$BOOT_CONFIG"

# Remove any problematic display settings we might have added
echo "🧹 Removing problematic display settings..."
sudo sed -i '/# TouchKio Optimization for Zoe/,$d' "$BOOT_CONFIG"
sudo sed -i '/# Sensible Display Configuration for TouchKio/,$d' "$BOOT_CONFIG"

# Add safe, minimal display config
echo "✅ Adding safe display configuration..."
cat << 'SAFE_EOF' | sudo tee -a "$BOOT_CONFIG"

# Emergency Safe Display Configuration
hdmi_force_hotplug=1
hdmi_drive=2
disable_overscan=1
gpu_mem=64
SAFE_EOF

# Kill any running display-related processes
echo "🛑 Stopping display processes..."
sudo pkill -f chromium-browser 2>/dev/null || true
sudo pkill -f unclutter 2>/dev/null || true
sudo pkill -f start-zoe 2>/dev/null || true

# Stop our services
echo "⏹️ Stopping our services..."
sudo systemctl stop zoe-touchkio 2>/dev/null || true
sudo systemctl stop bulletproof-display 2>/dev/null || true
sudo systemctl stop anti-blink-display 2>/dev/null || true

# Reset X11 display settings if we can
if [ -n "$DISPLAY" ] && command -v xrandr >/dev/null 2>&1; then
    echo "🖥️ Resetting X11 display..."
    xrandr --output HDMI-1 --auto --rotate normal 2>/dev/null || true
    xrandr --output HDMI-A-1 --auto --rotate normal 2>/dev/null || true
    xrandr --output HDMI-2 --auto --rotate normal 2>/dev/null || true
    xrandr --output HDMI-A-2 --auto --rotate normal 2>/dev/null || true
    
    # Enable display power
    xset +dpms 2>/dev/null || true
    xset s on 2>/dev/null || true
fi

# Remove any X11 config files we might have created
echo "🗑️ Removing X11 configs..."
sudo rm -f /etc/X11/xorg.conf.d/99-*.conf 2>/dev/null || true

# Check HDMI connection
echo "🔌 Checking HDMI status..."
if command -v tvservice >/dev/null 2>&1; then
    tvservice -s
    echo "💡 Forcing HDMI on..."
    tvservice -p 2>/dev/null || true
    sleep 2
    sudo systemctl restart lightdm 2>/dev/null || true
fi

echo ""
echo "🚨 EMERGENCY RECOVERY COMPLETE!"
echo ""
echo "✅ **What was fixed:**"
echo "   • Removed display rotation"
echo "   • Reset to safe display config"
echo "   • Stopped problematic services"
echo "   • Reset X11 display settings"
echo "   • Forced HDMI detection"
echo ""
echo "🔄 **Next steps:**"
echo "   1. Reboot the Pi: sudo reboot"
echo "   2. Display should work normally"
echo "   3. Then we can try rotation again more carefully"
echo ""
echo "📋 **Backup created:** ${BOOT_CONFIG}.emergency-backup-$(date +%Y%m%d-%H%M%S)"
echo ""
echo "💡 **If still no display after reboot:**"
echo "   • Connect via SSH"
echo "   • Check: sudo tvservice -s"
echo "   • Force HDMI: sudo tvservice -p"
echo "   • Restart display: sudo systemctl restart lightdm"




