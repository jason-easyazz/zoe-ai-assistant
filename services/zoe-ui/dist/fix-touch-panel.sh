#!/bin/bash
# Quick fix script for touch panels that had setup issues

echo "🔧 Fixing touch panel setup issues..."

cd /home/pi/zoe-touch-panel

# Fix Python packages
echo "🐍 Installing missing Python packages..."
pip3 install --user --break-system-packages requests netifaces zeroconf 2>/dev/null || echo "Packages already installed"

# Download working discovery client
echo "📡 Downloading fixed discovery client..."
curl -s http://zoe.local/api/touch-panels/discovery-client > simple_discovery_client.py

# Test discovery
echo "🧪 Testing Zoe discovery..."
python3 -c "
try:
    from simple_discovery_client import find_zoe
    config = find_zoe()
    if config:
        print('✅ Zoe discovery now working!')
        print(f'Zoe URL: {config[\"discovery_info\"][\"url\"]}')
    else:
        print('❌ Still cannot find Zoe - check network')
except Exception as e:
    print(f'❌ Error: {e}')
"

# Restart the touch panel service
echo "🔄 Restarting touch panel service..."
sudo systemctl restart zoe-touch-panel

echo "✅ Touch panel fixes complete!"




