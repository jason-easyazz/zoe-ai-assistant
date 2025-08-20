#!/bin/bash
# PHASE 0: System Update
echo "🔄 PHASE 0: System Update"
echo "========================="

# Update system
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove -y
sudo apt autoclean

echo "✅ Phase 0 complete: System updated"
