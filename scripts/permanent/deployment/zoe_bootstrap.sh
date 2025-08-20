#!/bin/bash
# ZOE AI ASSISTANT - COMPLETE BOOTSTRAP INSTALLER
# Creates all installation scripts including GitHub sync
# Save as: zoe_bootstrap.sh
# Run with: bash ~/zoe_bootstrap.sh

echo "🚀 ZOE AI ASSISTANT - BOOTSTRAP INSTALLER"
echo "========================================="
echo "Version: 1.1 (with GitHub sync)"
echo "Date: $(date)"

# Create the directory structure FIRST
echo -e "\n📁 Creating Zoe directory structure..."
mkdir -p /home/pi/zoe/scripts/permanent/deployment
mkdir -p /home/pi/zoe/scripts/permanent/backup
mkdir -p /home/pi/zoe/scripts/permanent/maintenance
mkdir -p /home/pi/zoe/scripts/temporary
mkdir -p /home/pi/zoe/documentation/dynamic
mkdir -p /home/pi/zoe/documentation/core
mkdir -p /home/pi/zoe/configs
mkdir -p /home/pi/zoe/services
mkdir -p /home/pi/zoe/data
mkdir -p /home/pi/zoe/logs
mkdir -p /home/pi/zoe/models
mkdir -p /home/pi/zoe/checkpoints

# Save THIS bootstrap script
cp "$0" /home/pi/zoe/scripts/permanent/deployment/zoe_bootstrap.sh 2>/dev/null || true

# Create Phase 0 - System Update
cat > /home/pi/zoe/scripts/permanent/deployment/phase0_system_update.sh << 'PHASE0_EOF'
#!/bin/bash
# PHASE 0: System Update
echo "🔄 PHASE 0: System Update"
echo "========================="

# Update system
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove -y
sudo apt autoclean

echo "✅ Phase 0 complete: System updated"
PHASE0_EOF

# Create Phase 1 - Core Dependencies
cat > /home/pi/zoe/scripts/permanent/deployment/phase1_dependencies.sh << 'PHASE1_EOF'
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
PHASE1_EOF

# Create Phase 2 - Directory Structure
cat > /home/pi/zoe/scripts/permanent/deployment/phase2_directories.sh << 'PHASE2_EOF'
#!/bin/bash
# PHASE 2: Complete Directory Structure
echo "📁 PHASE 2: Creating Directory Structure"
echo "========================================"

cd /home/pi/zoe

# Create all directories
mkdir -p services/zoe-core/routers
mkdir -p services/zoe-ui/dist/developer
mkdir -p data/billing
mkdir -p documentation/core
mkdir -p documentation/dynamic
mkdir -p scripts/archive
mkdir -p checkpoints
mkdir -p models
mkdir -p configs
mkdir -p logs

# Create .gitkeep files to preserve empty directories
touch services/zoe-core/routers/.gitkeep
touch services/zoe-ui/dist/developer/.gitkeep
touch data/billing/.gitkeep
touch scripts/temporary/.gitkeep
touch scripts/archive/.gitkeep
touch checkpoints/.gitkeep
touch models/.gitkeep
touch logs/.gitkeep

echo "✅ Phase 2 complete: Directory structure created"
PHASE2_EOF

# Create Phase 3 - Samba Configuration
cat > /home/pi/zoe/scripts/permanent/deployment/phase3_samba.sh << 'PHASE3_EOF'
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
PHASE3_EOF

# Create Phase 4 - Cloudflare (placeholder)
cat > /home/pi/zoe/scripts/permanent/deployment/phase4_cloudflare.sh << 'PHASE4_EOF'
#!/bin/bash
# PHASE 4: Cloudflare Tunnel Setup
echo "🌐 PHASE 4: Cloudflare Tunnel Setup"
echo "===================================="

# Check if cloudflared is installed
if command -v cloudflared &> /dev/null; then
    echo "✅ Cloudflare tunnel already installed"
    sudo systemctl status cloudflared --no-pager || true
else
    echo "📝 Cloudflare not installed"
    echo "To install later, run:"
    echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb"
    echo "  sudo dpkg -i cloudflared.deb"
fi

echo "✅ Phase 4 complete: Cloudflare check done"
PHASE4_EOF

# Create Phase 5 - GitHub Setup
cat > /home/pi/zoe/scripts/permanent/deployment/phase5_github.sh << 'PHASE5_EOF'
#!/bin/bash
# PHASE 5: GitHub Repository Setup
echo "🐙 PHASE 5: GitHub Repository Setup"
echo "===================================="

REPO_URL="https://github.com/jason-easyazz/zoe-ai-assistant.git"
PROJECT_DIR="/home/pi/zoe"

# Install git if needed
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    sudo apt install -y git
fi

# Configure Git
cd "$PROJECT_DIR"
git config --global user.name "jason-easyazz"
git config --global user.email "jason@easyazz.com"

# Create comprehensive .gitignore
cat > .gitignore << 'GITIGNORE'
# Data and databases
data/*.db
data/*.sqlite
data/billing/
data/logs/

# Docker volumes
volumes/

# Environment files with secrets
.env
config/.env
*.key
*.pem

# Temporary files
*.tmp
*.backup_*
scripts/temporary/*
!scripts/temporary/.gitkeep

# IDE files
.vscode/
.idea/
*.swp

# Python
__pycache__/
*.py[cod]
*$py.class
.Python
venv/
env/

# Logs
logs/
*.log

# OS files
.DS_Store
Thumbs.db

# Keep structure files
!.gitkeep
!README.md
GITIGNORE

# Initialize repository if not exists
if [ ! -d ".git" ]; then
    echo "Initializing Git repository..."
    git init
    git branch -M main
fi

# Add remote if not exists
if ! git remote get-url origin &>/dev/null 2>&1; then
    echo "Adding GitHub remote..."
    git remote add origin "$REPO_URL"
fi

# Create README
cat > README.md << 'README'
# Zoe AI Assistant

🤖 Privacy-first AI assistant for Raspberry Pi 5

## Features
- Local AI with Ollama
- Voice interface (Whisper + TTS)
- Smart home integration
- Personal journaling & memory
- Workflow automation

## Installation
See scripts/permanent/deployment/ for installation scripts

## Structure
- `/services` - Docker service definitions
- `/scripts` - Management and deployment scripts
- `/data` - User data and databases
- `/documentation` - Project documentation
README

# Create initial commit
echo "Creating initial commit..."
git add .
git commit -m "🤖 Zoe AI Assistant - Fresh Installation

- Raspberry Pi 5 optimized
- Docker-based architecture  
- Privacy-first design
- Local AI with Ollama
- Complete directory structure" || echo "No changes to commit"

echo ""
echo "📝 To push to GitHub, run:"
echo "   git push -u origin main"
echo ""
echo "You may need to:"
echo "1. Create a personal access token at github.com"
echo "2. Use token as password when prompted"

echo "✅ Phase 5 complete: Git repository configured"
PHASE5_EOF

# Create helper script for GitHub sync
cat > /home/pi/zoe/scripts/permanent/maintenance/sync_github.sh << 'SYNC_EOF'
#!/bin/bash
# Quick GitHub sync script

cd /home/pi/zoe

# Add all changes
git add .

# Commit with timestamp or custom message
if [ $# -eq 0 ]; then
    TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
    git commit -m "🔄 Auto-sync: $TIMESTAMP" || echo "No changes to commit"
else
    git commit -m "$1" || echo "No changes to commit"
fi

# Push to GitHub
git push origin main || echo "Push failed - check authentication"

echo "✅ GitHub sync complete"
SYNC_EOF

chmod +x /home/pi/zoe/scripts/permanent/maintenance/sync_github.sh

# Create MASTER INSTALLER
cat > /home/pi/zoe/scripts/permanent/deployment/install_zoe.sh << 'MASTER_EOF'
#!/bin/bash
# ZOE AI ASSISTANT - MASTER INSTALLER

echo "🤖 ZOE AI ASSISTANT - MASTER INSTALLER"
echo "======================================"
echo "Version: 1.1"
echo ""

SCRIPT_DIR="/home/pi/zoe/scripts/permanent/deployment"

# Function to run a phase
run_phase() {
    local phase_script=$1
    local phase_name=$2
    
    echo -e "\n🚀 Running ${phase_name}..."
    echo "----------------------------------------"
    
    if [ -f "${SCRIPT_DIR}/${phase_script}" ]; then
        bash "${SCRIPT_DIR}/${phase_script}"
        if [ $? -eq 0 ]; then
            echo "✅ ${phase_name} completed successfully"
            echo "$(date): ${phase_name} completed" >> /home/pi/zoe/documentation/dynamic/install_log.txt
        else
            echo "❌ ${phase_name} failed"
            echo "$(date): ${phase_name} FAILED" >> /home/pi/zoe/documentation/dynamic/install_log.txt
            exit 1
        fi
    else
        echo "⚠️ Script not found: ${phase_script}"
    fi
    
    sleep 2
}

# Check if running with required permissions
if [ "$EUID" -eq 0 ]; then 
   echo "❌ Please do not run as root (no sudo)"
   exit 1
fi

# Run all phases
run_phase "phase0_system_update.sh" "Phase 0: System Update"
run_phase "phase1_dependencies.sh" "Phase 1: Dependencies"
run_phase "phase2_directories.sh" "Phase 2: Directory Structure"
run_phase "phase3_samba.sh" "Phase 3: Samba Configuration"
run_phase "phase4_cloudflare.sh" "Phase 4: Cloudflare Check"
run_phase "phase5_github.sh" "Phase 5: GitHub Setup"

echo -e "\n======================================"
echo "✅ ZOE INSTALLATION COMPLETE!"
echo "======================================"

IP_ADDR=$(hostname -I | awk '{print $1}')
echo ""
echo "📂 Network share: \\\\${IP_ADDR}\\zoe"
echo "🐙 GitHub: https://github.com/jason-easyazz/zoe-ai-assistant"
echo ""
echo "⚠️ Logout and login for Docker permissions to take effect"
MASTER_EOF

# Make all scripts executable
chmod +x /home/pi/zoe/scripts/permanent/deployment/*.sh

# Create initial documentation
cat > /home/pi/zoe/documentation/dynamic/install_log.txt << EOF
# Zoe Installation Log
$(date): Bootstrap installer created
$(date): All phase scripts generated
EOF

cat > /home/pi/zoe/documentation/dynamic/proven_solutions.md << 'EOF'
# Proven Solutions

## Installation
- ✅ Phased installation approach works well
- ✅ Samba with guest access for easy file editing
- ✅ GitHub sync for code backup

## Docker
- ✅ Always use zoe- prefix for containers
- ✅ Single docker-compose.yml file
- ✅ Use --build flag for Python changes
EOF

cat > /home/pi/zoe/documentation/dynamic/things_to_avoid.md << 'EOF'
# Things to Avoid

## Docker
- ❌ Never rebuild zoe-ollama (re-downloads models)
- ❌ Multiple docker-compose files (causes conflicts)
- ❌ Generic container names (port conflicts)

## Development
- ❌ Making changes without backups
- ❌ Skipping immediate testing
- ❌ Large changes all at once
EOF

# Create status check script
cat > /home/pi/zoe/scripts/permanent/maintenance/check_status.sh << 'STATUS_EOF'
#!/bin/bash
echo "🔍 Zoe System Status"
echo "===================="
echo "Docker: $(docker --version 2>/dev/null || echo 'Not installed')"
echo "Docker Compose: $(docker-compose --version 2>/dev/null || echo 'Not installed')"
echo "Git: $(git --version 2>/dev/null || echo 'Not installed')"
echo "Samba: $(systemctl is-active smbd)"
echo "IP: $(hostname -I | awk '{print $1}')"
echo "Scripts: $(ls -1 /home/pi/zoe/scripts/permanent/deployment/*.sh 2>/dev/null | wc -l) deployment scripts"
echo "Free Space: $(df -h / | tail -1 | awk '{print $4}')"
STATUS_EOF

chmod +x /home/pi/zoe/scripts/permanent/maintenance/check_status.sh

echo -e "\n========================================="
echo "✅ BOOTSTRAP COMPLETE!"
echo "========================================="
echo ""
echo "📁 Created at: /home/pi/zoe"
echo "📝 Generated: $(ls -1 /home/pi/zoe/scripts/permanent/deployment/*.sh | wc -l) installation scripts"
echo ""
echo "RUN OPTIONS:"
echo ""
echo "1️⃣ FULL INSTALL (recommended):"
echo "   bash /home/pi/zoe/scripts/permanent/deployment/install_zoe.sh"
echo ""
echo "2️⃣ PHASE BY PHASE:"
echo "   bash /home/pi/zoe/scripts/permanent/deployment/phase0_system_update.sh"
echo "   (then phase1, phase2, etc...)"
echo ""
echo "3️⃣ CHECK STATUS:"
echo "   bash /home/pi/zoe/scripts/permanent/maintenance/check_status.sh"
echo ""
echo "All scripts saved in: /home/pi/zoe/scripts/"
