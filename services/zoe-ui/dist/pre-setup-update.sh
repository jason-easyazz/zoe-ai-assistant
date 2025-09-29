#!/bin/bash
# Pre-Setup System Update
echo "📦 Pre-Setup System Update"
echo "=========================="
echo "Running essential updates before TouchKio setup..."
echo ""

# Update package lists
echo "🔄 Updating package lists..."
sudo apt update

# Upgrade existing packages
echo "⬆️ Upgrading existing packages..."
sudo apt upgrade -y

# Install basic essentials
echo "📦 Installing basic essentials..."
sudo apt install -y curl wget git

echo ""
echo "✅ System updated and ready!"
echo ""
echo "🚀 **Now run the main setup:**"
echo "curl -s http://192.168.1.60/fresh-touchkio-zoe-setup.sh | bash"




