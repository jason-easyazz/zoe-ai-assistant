#!/bin/bash
# Deploy touch interface update to TouchKio Raspberry Pi
# This script helps you update the remote TouchKio Pi

echo "🚀 Deploying Touch Interface Update to TouchKio Pi"
echo "================================================="
echo ""

# Get the IP address of this Zoe system
ZOE_IP=$(hostname -I | awk '{print $1}')
echo "📍 This Zoe system IP: $ZOE_IP"
echo ""

echo "📋 To update your TouchKio Raspberry Pi:"
echo ""
echo "1. SSH into your TouchKio Pi:"
echo "   ssh pi@<touchkio-pi-ip>"
echo ""
echo "2. Run this command on the TouchKio Pi:"
echo "   curl -s http://$ZOE_IP/update-touchkio-to-touch-interface.sh | bash"
echo ""
echo "3. Or download and run manually:"
echo "   wget http://$ZOE_IP/update-touchkio-to-touch-interface.sh"
echo "   chmod +x update-touchkio-to-touch-interface.sh"
echo "   ./update-touchkio-to-touch-interface.sh"
echo ""
echo "🔍 Alternative: If you know the TouchKio Pi IP, I can try to deploy directly:"
echo ""

# Ask for TouchKio Pi IP
read -p "Enter TouchKio Pi IP address (or press Enter to skip): " TOUCHKIO_IP

if [ -n "$TOUCHKIO_IP" ]; then
    echo "🚀 Attempting to deploy update to $TOUCHKIO_IP..."
    
    # Test connectivity
    if ping -c 1 -W 3 "$TOUCHKIO_IP" >/dev/null 2>&1; then
        echo "✅ TouchKio Pi is reachable"
        
        # Try to deploy the update
        echo "📤 Deploying update script..."
        if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no pi@"$TOUCHKIO_IP" "curl -s http://$ZOE_IP/update-touchkio-to-touch-interface.sh | bash"; then
            echo "🎉 Update deployed successfully!"
            echo "Your TouchKio panel should now show the new touch interface."
        else
            echo "❌ Failed to deploy via SSH. Please run manually:"
            echo "   ssh pi@$TOUCHKIO_IP"
            echo "   curl -s http://$ZOE_IP/update-touchkio-to-touch-interface.sh | bash"
        fi
    else
        echo "❌ Cannot reach TouchKio Pi at $TOUCHKIO_IP"
        echo "Please check the IP address and network connectivity."
    fi
else
    echo "ℹ️  Skipping automatic deployment."
    echo "Please follow the manual steps above to update your TouchKio Pi."
fi

echo ""
echo "✅ Touch interface is ready at: http://$ZOE_IP/touch/"
echo "🔧 Update script available at: http://$ZOE_IP/update-touchkio-to-touch-interface.sh"
