#!/bin/bash
# INSTALL SAMBA FOR ZOE PROJECT
# Run this immediately to set up network file access

set -e

echo "🎯 Installing Samba for Zoe Project"
echo "===================================="
echo "Starting at: $(date)"

# Create directories if needed
cd /home/pi
mkdir -p zoe/scripts/deployment
cd zoe

# Install Samba
echo -e "\n📦 Installing Samba packages..."
sudo apt update
sudo apt install -y samba samba-common-bin

# Configure Samba share
echo -e "\n🔧 Configuring Zoe network share..."
sudo tee -a /etc/samba/smb.conf > /dev/null << 'EOF'

# Zoe AI Assistant Share
[zoe]
   comment = Zoe AI Assistant Project
   path = /home/pi/zoe
   browseable = yes
   read only = no
   writeable = yes
   guest ok = yes
   create mask = 0777
   directory mask = 0777
   force user = pi
   force group = pi
EOF

# Set Samba password
echo -e "\n🔐 Setting Samba password for user 'pi'"
echo "Enter a password for network access (can be same as Pi password):"
sudo smbpasswd -a pi

# Enable and start services
echo -e "\n🚀 Starting Samba services..."
sudo systemctl restart smbd
sudo systemctl restart nmbd
sudo systemctl enable smbd
sudo systemctl enable nmbd

# Get network info
IP_ADDR=$(hostname -I | awk '{print $1}')
HOSTNAME=$(hostname)

# Test configuration
echo -e "\n✅ Testing configuration..."
sudo testparm -s 2>/dev/null | grep -A 10 "\[zoe\]"

# Save this script for future reference
cp $0 /home/pi/zoe/scripts/deployment/setup_samba.sh 2>/dev/null || true

# Display connection info
clear
echo "✅ SAMBA SUCCESSFULLY INSTALLED!"
echo "================================="
echo ""
echo "📂 ACCESS YOUR ZOE PROJECT FROM:"
echo ""
echo "🖥️ Windows:"
echo "   File Explorer Address Bar: \\${IP_ADDR}\\zoe"
echo "   Or: \\${HOSTNAME}\\zoe"
echo ""
echo "🍎 Mac:"
echo "   Finder → Go → Connect to Server"
echo "   Enter: smb://${IP_ADDR}/zoe"
echo ""
echo "🐧 Linux:"
echo "   File Manager: smb://${IP_ADDR}/zoe"
echo ""
echo "📝 Credentials (if prompted):"
echo "   Username: pi"
echo "   Password: [what you just set]"
echo "   * Guest access also enabled"
echo ""
echo "🎯 You can now:"
echo "   • Edit files directly from your main computer"
echo "   • Drag & drop files to the Pi"
echo "   • Use VS Code or any editor remotely"
echo "   • Access templates and scripts easily"
echo ""
echo "🔧 Test from this Pi:"
echo "   smbclient //localhost/zoe -U pi"
echo ""
echo "📁 Project location: /home/pi/zoe"
