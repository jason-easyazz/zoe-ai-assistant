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
