#!/bin/bash
# SSH Fix and Fresh Setup Guide

echo "🔑 SSH Host Key Fix Instructions"
echo "================================"
echo ""
echo "The SSH warning is normal - you flashed fresh Pi OS!"
echo ""
echo "🛠️ **Fix SSH from your Mac:**"
echo ""
echo "ssh-keygen -R 192.168.1.61"
echo ""
echo "Then connect normally:"
echo "ssh pi@192.168.1.61"
echo ""
echo "🚀 **Once connected, run the fresh setup:**"
echo ""
echo "curl -s http://192.168.1.60/fresh-touchkio-zoe-setup.sh | bash"
echo ""
echo "💡 **Or copy/paste this complete sequence:**"
echo ""
cat << 'COMPLETE_EOF'
# From your Mac terminal:
ssh-keygen -R 192.168.1.61
ssh pi@192.168.1.61

# Then on the Pi:
curl -s http://192.168.1.60/fresh-touchkio-zoe-setup.sh | bash

# After setup completes:
sudo reboot
COMPLETE_EOF
echo ""
echo "✅ **That's it! Much simpler than our over-engineered approach.**"




