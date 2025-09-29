#!/bin/bash
# Update existing TouchKio setup to use new Zoe touch interface
# Run this on the TouchKio Raspberry Pi

echo "🔄 Updating TouchKio to use new Zoe touch interface..."
echo "=================================================="

# Find existing TouchKio configuration
TOUCHKIO_DIR=""
CONFIG_FILE=""

if [ -f "/opt/TouchKio/zoe-config.json" ]; then
    TOUCHKIO_DIR="/opt/TouchKio"
    CONFIG_FILE="/opt/TouchKio/zoe-config.json"
elif [ -f "/home/pi/TouchKio/zoe-config.json" ]; then
    TOUCHKIO_DIR="/home/pi/TouchKio"
    CONFIG_FILE="/home/pi/TouchKio/zoe-config.json"
elif [ -f "/home/pi/ZoeKio/zoe-config.json" ]; then
    TOUCHKIO_DIR="/home/pi/ZoeKio"
    CONFIG_FILE="/home/pi/ZoeKio/zoe-config.json"
else
    echo "❌ No existing TouchKio configuration found!"
    echo "Please run the full TouchKio setup first."
    exit 1
fi

echo "📂 Found TouchKio directory: $TOUCHKIO_DIR"
echo "📄 Found config file: $CONFIG_FILE"

# Backup existing config
echo "💾 Backing up existing configuration..."
cp "$CONFIG_FILE" "$CONFIG_FILE.backup.$(date +%Y%m%d_%H%M%S)"

# Update the configuration to use touch interface
echo "🔧 Updating configuration to use touch interface..."

# Get current Zoe URL from config
CURRENT_URL=$(grep -o '"url":[^,]*' "$CONFIG_FILE" | cut -d'"' -f4)
CURRENT_FALLBACK=$(grep -o '"fallback_url":[^,]*' "$CONFIG_FILE" | cut -d'"' -f4)

echo "Current URL: $CURRENT_URL"
echo "Current Fallback: $CURRENT_FALLBACK"

# Update URLs to point to touch interface
NEW_URL="${CURRENT_URL%/}/touch/"
NEW_FALLBACK="${CURRENT_FALLBACK%/}/touch/"

echo "New URL: $NEW_URL"
echo "New Fallback: $NEW_FALLBACK"

# Update the config file
sed -i "s|\"url\": \"$CURRENT_URL\"|\"url\": \"$NEW_URL\"|g" "$CONFIG_FILE"
sed -i "s|\"fallback_url\": \"$CURRENT_FALLBACK\"|\"fallback_url\": \"$NEW_FALLBACK\"|g" "$CONFIG_FILE"

echo "✅ Configuration updated!"

# Test the new URLs
echo "🧪 Testing new URLs..."
if curl -s --connect-timeout 5 "$NEW_URL" >/dev/null 2>&1; then
    echo "✅ Primary URL ($NEW_URL) is accessible"
else
    echo "⚠️  Primary URL ($NEW_URL) not accessible, will use fallback"
fi

if curl -s --connect-timeout 5 "$NEW_FALLBACK" >/dev/null 2>&1; then
    echo "✅ Fallback URL ($NEW_FALLBACK) is accessible"
else
    echo "⚠️  Fallback URL ($NEW_FALLBACK) not accessible"
fi

# Restart TouchKio service if it exists
echo "🔄 Restarting TouchKio service..."
if systemctl is-active --quiet zoe-touchkio; then
    echo "Restarting zoe-touchkio service..."
    sudo systemctl restart zoe-touchkio
    echo "✅ Service restarted"
elif systemctl is-active --quiet touchkio; then
    echo "Restarting touchkio service..."
    sudo systemctl restart touchkio
    echo "✅ Service restarted"
else
    echo "ℹ️  No TouchKio service found, restarting browser manually..."
    # Kill existing browser processes
    pkill -f chromium-browser 2>/dev/null || true
    pkill -f chrome 2>/dev/null || true
    
    # Start new browser with updated config
    if [ -f "$TOUCHKIO_DIR/start-zoe-kio.sh" ]; then
        echo "Starting updated TouchKio..."
        nohup "$TOUCHKIO_DIR/start-zoe-kio.sh" > /dev/null 2>&1 &
        echo "✅ Browser restarted with new configuration"
    else
        echo "⚠️  No startup script found, please restart manually"
    fi
fi

echo ""
echo "🎉 TouchKio update complete!"
echo "=========================="
echo "Your TouchKio panel should now display the new Zoe touch interface."
echo ""
echo "If you need to revert changes:"
echo "  sudo cp $CONFIG_FILE.backup.* $CONFIG_FILE"
echo "  sudo systemctl restart zoe-touchkio"
echo ""
echo "To check status:"
echo "  sudo systemctl status zoe-touchkio"
echo "  curl -s $NEW_URL | head -20"
