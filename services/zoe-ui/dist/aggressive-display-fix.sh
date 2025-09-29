#!/bin/bash
# Aggressive Display Fix - Hardware Level
echo "🔧 Applying aggressive display fixes..."

# First, let's diagnose what's happening
echo "📊 Display Diagnosis:"
if [ -n "$DISPLAY" ] && xset q &>/dev/null 2>&1; then
    echo "✅ X11 session active"
    xrandr --current 2>/dev/null | head -5 || echo "❌ xrandr failed"
else
    echo "❌ No X11 session detected"
fi

# Check current boot config
echo "📋 Current display config:"
grep -E "(hdmi|display|gpu)" /boot/config.txt | tail -10

# Nuclear option - completely rebuild boot config
echo "💥 Applying nuclear display fix..."

# Backup current config
sudo cp /boot/config.txt /boot/config.txt.$(date +%Y%m%d_%H%M%S)

# Remove ALL display-related settings
sudo sed -i '/hdmi_/d' /boot/config.txt
sudo sed -i '/display_/d' /boot/config.txt
sudo sed -i '/gpu_/d' /boot/config.txt
sudo sed -i '/config_hdmi/d' /boot/config.txt
sudo sed -i '/disable_overscan/d' /boot/config.txt
sudo sed -i '/avoid_warnings/d' /boot/config.txt

# Add rock-solid display configuration
cat << 'NUCLEAR_EOF' | sudo tee -a /boot/config.txt

# NUCLEAR DISPLAY FIX - Maximum Stability
# Force HDMI output
hdmi_force_hotplug=1
hdmi_ignore_edid=0xa5000080
hdmi_drive=2

# Aggressive HDMI settings  
config_hdmi_boost=10
hdmi_blanking=1

# Stable resolution
hdmi_group=2
hdmi_mode=82
framebuffer_width=1920
framebuffer_height=1080

# 90-degree rotation
display_rotate=1

# Power and stability
disable_overscan=1
gpu_mem=128
gpu_freq=400
core_freq=400

# Prevent any power saving
hdmi_force_mode=1
avoid_warnings=1
NUCLEAR_EOF

# Create bulletproof display startup script
echo "🛡️ Creating bulletproof display script..."
cat > /home/pi/bulletproof-display.sh << 'BULLETPROOF_EOF'
#!/bin/bash
# Bulletproof Display Management

export DISPLAY=:0

# Function to force display on
force_display_on() {
    echo "🔋 Forcing display on..."
    
    # Multiple methods to prevent display sleep
    xset s off 2>/dev/null || true
    xset s noblank 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset dpms 0 0 0 2>/dev/null || true
    xset s reset 2>/dev/null || true
    
    # Force HDMI on via kernel
    echo "on" | sudo tee /sys/class/drm/card*/card*/enabled >/dev/null 2>&1 || true
    
    # Additional low-level fixes
    sudo sh -c 'echo 0 > /sys/class/backlight/*/bl_power' 2>/dev/null || true
    sudo sh -c 'echo 255 > /sys/class/backlight/*/brightness' 2>/dev/null || true
}

# Continuous monitoring loop
while true; do
    # Wait for X to be available
    while ! xset q &>/dev/null; do
        sleep 1
    done
    
    # Apply display fixes
    force_display_on
    
    # Create fake activity every 10 seconds
    xdotool mousemove 1 1 2>/dev/null || true
    xdotool key shift 2>/dev/null || true
    
    sleep 10
done
BULLETPROOF_EOF

chmod +x /home/pi/bulletproof-display.sh

# Create emergency display service
echo "⚙️ Creating emergency display service..."
sudo tee /etc/systemd/system/bulletproof-display.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Bulletproof Display Keep-Alive
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStart=/home/pi/bulletproof-display.sh
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
SERVICE_EOF

# Enable the service
sudo systemctl daemon-reload
sudo systemctl enable bulletproof-display.service
sudo systemctl start bulletproof-display.service

# Create ultimate kiosk script with display protection
echo "🖥️ Creating ultimate protected kiosk..."
cat > /home/pi/protected-kiosk.sh << 'PROTECTED_EOF'
#!/bin/bash
# Protected Kiosk with Display Safety

export DISPLAY=:0

# Wait for display
echo "Waiting for display..."
while ! xset q &>/dev/null; do
    sleep 1
done

# Start bulletproof display protection
/home/pi/bulletproof-display.sh &
BULLETPROOF_PID=$!

# Kill any existing browsers
pkill -f chromium-browser || true
sleep 3

# Apply immediate display fixes
xset s off -dpms s noblank dpms 0 0 0

# Hide cursor aggressively
unclutter -idle 0.1 -root -noevents &

echo "Starting protected browser..."

# Start browser with maximum stability settings
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-extensions \
    --disable-plugins \
    --disable-web-security \
    --user-data-dir=/tmp/chromium-protected \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-software-rasterizer \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows \
    --aggressive-cache-discard \
    --memory-pressure-off \
    --start-maximized \
    --touch-events=enabled \
    --force-device-scale-factor=1 \
    file:///home/pi/zoe-touch-interface/index.html &

BROWSER_PID=$!

# Monitor and restart if needed
while true; do
    # Check if browser is running
    if ! kill -0 $BROWSER_PID 2>/dev/null; then
        echo "Browser crashed, restarting..."
        sleep 5
        exec $0
    fi
    
    # Check if bulletproof display is running
    if ! kill -0 $BULLETPROOF_PID 2>/dev/null; then
        echo "Display protection crashed, restarting..."
        /home/pi/bulletproof-display.sh &
        BULLETPROOF_PID=$!
    fi
    
    sleep 30
done
PROTECTED_EOF

chmod +x /home/pi/protected-kiosk.sh

# Apply immediate fixes if in graphical session
if [ -n "$DISPLAY" ] && xset q &>/dev/null 2>&1; then
    echo "🔧 Applying immediate emergency fixes..."
    
    # Start bulletproof display protection
    /home/pi/bulletproof-display.sh &
    
    # Kill problematic processes
    pkill -f chromium-browser || true
    pkill -f unclutter || true
    
    sleep 2
    
    # Restart protected kiosk
    /home/pi/protected-kiosk.sh &
    
    echo "✅ Emergency fixes applied"
fi

# Create hardware reset option
echo "🔄 Creating hardware reset option..."
cat > /home/pi/hardware-display-reset.sh << 'RESET_EOF'
#!/bin/bash
# Hardware Display Reset

echo "🔄 Hardware display reset..."

# Reset graphics drivers
sudo modprobe -r vc4
sudo modprobe vc4

# Reset HDMI
echo "Resetting HDMI..."
sudo sh -c 'echo 0 > /sys/class/drm/card*/card*/enabled'
sleep 2
sudo sh -c 'echo 1 > /sys/class/drm/card*/card*/enabled'

# Force display on
xset s off -dpms s noblank dpms 0 0 0 2>/dev/null || true

echo "✅ Hardware reset complete"
RESET_EOF

chmod +x /home/pi/hardware-display-reset.sh

echo ""
echo "💥 NUCLEAR DISPLAY FIX APPLIED!"
echo ""
echo "🎯 **What was applied:**"
echo "   ✅ Nuclear boot config with maximum HDMI stability"
echo "   ✅ Bulletproof display keep-alive service"
echo "   ✅ Protected kiosk with crash recovery"
echo "   ✅ Hardware-level display management"
echo "   ✅ Continuous fake activity generation"
echo ""
echo "🚀 **Immediate actions:**"
echo "1. Test now: /home/pi/protected-kiosk.sh"
echo "2. Hardware reset: /home/pi/hardware-display-reset.sh"
echo "3. Full reboot: sudo reboot (RECOMMENDED)"
echo ""
echo "⚡ **Emergency commands:**"
echo "   • /home/pi/bulletproof-display.sh &"
echo "   • /home/pi/hardware-display-reset.sh"
echo "   • systemctl status bulletproof-display"
echo ""
echo "🔧 **If still issues after reboot:**"
echo "   • Try different HDMI cable"
echo "   • Try different HDMI port on display"
echo "   • Check display's power saving settings"
echo "   • Consider display hardware issue"




