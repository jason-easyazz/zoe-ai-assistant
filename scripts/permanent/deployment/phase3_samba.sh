#!/bin/bash
# PHASE 3: Configure Samba
echo "📂 PHASE 3: Configuring Samba"
echo "============================="

# Backup existing config
sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup_$(date +%Y%m%d) 2>/dev/null || true

# Check if zoe share already exists
if ! grep -q "\[zoe\]" /etc/samba/smb.conf; then
    # Add Zoe share
    sudo tee -a /etc/samba/smb.conf > /dev/null << 'EOF'

[zoe]
   comment = Zoe AI Assistant Project
   path = /home/pi/zoe
   browseable = yes
   writeable = yes
   guest ok = yes
   create mask = 0777
   directory mask = 0777
   public = yes
EOF
fi

# Restart Samba
sudo systemctl restart smbd
sudo systemctl enable smbd

IP_ADDR=$(hostname -I | awk '{print $1}')
echo "📂 Samba share available at:"
echo "   Windows: \\\\${IP_ADDR}\\zoe"
echo "   Mac/Linux: smb://${IP_ADDR}/zoe"

echo "✅ Phase 3 complete: Samba configured"
