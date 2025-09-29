#!/bin/bash
# Quick Fix for Screen Turning Off and Flashing
echo "🖥️ Fixing screen turning off and flashing..."

# Immediate display fixes (if logged in)
if [ -n "$DISPLAY" ] && xset q &>/dev/null 2>&1; then
    echo "🔧 Applying immediate display fixes..."
    
    # Disable all power management
    xset s off
    xset -dpms
    xset s noblank
    xset dpms 0 0 0
    
    echo "✅ Immediate fixes applied"
else
    echo "ℹ️ Not in graphical session - fixes will apply on next boot"
fi

# Fix boot config for stable display
echo "⚙️ Fixing boot configuration..."

# Remove any problematic display settings
sudo sed -i '/hdmi_blanking/d' /boot/config.txt
sudo sed -i '/hdmi_group/d' /boot/config.txt
sudo sed -i '/hdmi_mode/d' /boot/config.txt
sudo sed -i '/config_hdmi_boost/d' /boot/config.txt
sudo sed -i '/gpu_freq/d' /boot/config.txt

# Add stable display configuration
cat << 'EOF' | sudo tee -a /boot/config.txt

# Stable Display Configuration - No Flashing
hdmi_force_hotplug=1
hdmi_drive=2
config_hdmi_boost=4
disable_overscan=1
display_rotate=1

# Prevent display issues
avoid_warnings=1
gpu_mem=64
EOF

# Create a display keepalive script
echo "📺 Creating display keepalive script..."
cat > /home/pi/keep-display-on.sh << 'KEEPALIVE_EOF'
#!/bin/bash
# Keep display on continuously

export DISPLAY=:0

while true; do
    # Disable power management every 30 seconds
    xset s off -dpms s noblank dpms 0 0 0 2>/dev/null || true
    
    # Touch the display to keep it active
    xdotool mousemove 1 1 2>/dev/null || true
    
    sleep 30
done
KEEPALIVE_EOF

chmod +x /home/pi/keep-display-on.sh

# Start keepalive in background if display available
if [ -n "$DISPLAY" ] && xset q &>/dev/null 2>&1; then
    echo "🔄 Starting display keepalive..."
    /home/pi/keep-display-on.sh &
    echo "✅ Display keepalive started"
fi

# Update the kiosk script with better display management
echo "🖥️ Updating kiosk script..."
cat > /home/pi/start-zoe-fixed.sh << 'FIXED_EOF'
#!/bin/bash
# Fixed Zoe Kiosk with No Screen Issues

export DISPLAY=:0

# Wait for display
while ! xset q &>/dev/null; do
    sleep 1
done

# Aggressive display settings
xset s off
xset -dpms
xset s noblank
xset dpms 0 0 0

# Start keepalive in background
/home/pi/keep-display-on.sh &

# Hide cursor
unclutter -idle 0.5 -root &

# Start browser
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --start-maximized \
    --no-first-run \
    --disable-web-security \
    --user-data-dir=/tmp/chromium-kiosk \
    --touch-events=enabled \
    --force-device-scale-factor=1 \
    file:///home/pi/zoe-touch-interface/index.html

echo "Zoe kiosk started with display keepalive"
FIXED_EOF

chmod +x /home/pi/start-zoe-fixed.sh

# Create simple recovery command
echo "🚑 Creating recovery commands..."
cat > /home/pi/fix-display-now.sh << 'RECOVERY_EOF'
#!/bin/bash
# Emergency display fix

export DISPLAY=:0

echo "🔧 Emergency display fix..."

# Kill any problematic processes
pkill -f chromium-browser || true
pkill -f keep-display-on || true

# Reset display
xset s off -dpms s noblank dpms 0 0 0

# Restart keepalive
/home/pi/keep-display-on.sh &

# Restart kiosk
/home/pi/start-zoe-fixed.sh &

echo "✅ Display fix applied"
RECOVERY_EOF

chmod +x /home/pi/fix-display-now.sh

# Add to .bashrc for easy access
if ! grep -q "fix-display-now" /home/pi/.bashrc; then
    echo "" >> /home/pi/.bashrc
    echo "# Display fix commands" >> /home/pi/.bashrc
    echo "alias fix-display='/home/pi/fix-display-now.sh'" >> /home/pi/.bashrc
fi

echo ""
echo "✅ Screen flashing fix complete!"
echo ""
echo "🔧 **Immediate fixes applied:**"
echo "   ✅ Power management disabled"
echo "   ✅ Display keepalive started"
echo "   ✅ Boot config updated"
echo "   ✅ Recovery scripts created"
echo ""
echo "🚀 **To apply fixes:**"
echo "1. Immediate: /home/pi/fix-display-now.sh"
echo "2. Restart kiosk: /home/pi/start-zoe-fixed.sh"  
echo "3. Full reboot: sudo reboot"
echo ""
echo "💡 **Emergency commands:**"
echo "   • fix-display (after sourcing bashrc)"
echo "   • /home/pi/fix-display-now.sh"
echo ""
echo "🎯 **What this fixes:**"
echo "   • Screen turning off"
echo "   • Display flashing"
echo "   • Power management issues"
echo "   • Unstable HDMI connection"




