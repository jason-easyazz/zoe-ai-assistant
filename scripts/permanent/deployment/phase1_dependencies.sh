#!/bin/bash
# PHASE 1: Install Dependencies
echo "📦 PHASE 1: Installing Dependencies"
echo "==================================="

# Install essential tools
sudo apt install -y \
    git curl wget jq sqlite3 \
    python3-pip python3-venv \
    build-essential nginx \
    redis-tools htop net-tools \
    samba samba-common-bin \
    tree

# Install Docker
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
fi

# Install Docker Compose
sudo apt install -y docker-compose

echo "✅ Phase 1 complete: Dependencies installed"
echo "⚠️ NOTE: Logout and login for docker group to take effect"
