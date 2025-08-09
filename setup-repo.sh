#!/bin/bash
# Quick Zoe Repository Setup

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')] $1${NC}"
}

info() {
    echo -e "${BLUE}$1${NC}"
}

PROJECT_DIR="$HOME/zoe-v31"
REPO_URL="https://github.com/jason-easyazz/zoe-ai-assistant.git"

log "Setting up Zoe repository..."

cd "$PROJECT_DIR"

# Create .gitignore to protect personal data
log "Creating .gitignore..."
cat > .gitignore << 'GITIGNORE_EOF'
# Zoe AI Assistant - Privacy Protection

# Personal data (NEVER commit)
data/database/
data/profiles/
data/journals/
data/conversations/
data/chat_history/
*.db
*.sqlite*

# Secrets & credentials
.env
.env.local
.env.production
secrets/
credentials/
certificates/
*.key
*.pem
*.crt

# Runtime data
logs/
*.log
tmp/
temp/
cache/
backups/
*.backup
*.bak
*.tar.gz

# Docker volumes
data/ollama/
data/whisper/
data/tts/
data/n8n/
data/homeassistant/
data/matrix/

# Large model files
*.bin
*.gguf
*.safetensors
models/

# Development files
__pycache__/
*.pyc
node_modules/
.vscode/
.idea/
*.swp
.DS_Store
GITIGNORE_EOF

# Create README.md
log "Creating README.md..."
cat > README.md << 'README_EOF'
# 🤖 Zoe - Local AI Best Friend & Life Hub

<div align="center">

![Zoe AI Assistant](https://img.shields.io/badge/Zoe-AI%20Assistant-blue?style=for-the-badge&logo=robot)
![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi%205-red?style=for-the-badge&logo=raspberry-pi)
![Privacy](https://img.shields.io/badge/Privacy-100%25%20Offline-green?style=for-the-badge&logo=shield)

*Your personal AI companion that stays home* 🏠❤️

</div>

## ✨ What Makes Zoe Special

Zoe is a **fully offline, privacy-first AI assistant** designed specifically for Raspberry Pi 5. She's not just a chatbot - she's your personal companion that evolves into a central brain for your life.

### 🎯 Core Features

- **🔒 100% Privacy** - All AI runs locally, no data leaves your network
- **🗣️ Natural Voice** - Whisper STT + Coqui TTS for real conversations  
- **🧠 Personal Memory** - Remembers your routines, moods, and preferences
- **🏠 Smart Home** - Home Assistant integration for voice control
- **⚡ Automation** - n8n workflows for proactive assistance
- **📝 Life Management** - Journaling, tasks, and intelligent organization

## 🚀 Quick Start

```bash
# Clone and setup
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git
cd zoe-ai-assistant

# Run setup
chmod +x scripts/zoe-core.sh
./scripts/zoe-core.sh

# Start Zoe
docker compose up -d

# Access interface
open http://your-pi-ip:8080
```

## 📁 Project Structure

```
zoe/
├── 🐳 docker-compose.yml    # Main orchestration
├── 🔧 scripts/             # Setup & maintenance
├── 🏗️ services/            # All service containers
├── 📊 data/                # Persistent data (not in git)
└── 📚 docs/                # Documentation
```

## 🛠️ Key Commands

```bash
# System status
docker compose ps

# View logs
docker compose logs -f zoe-core

# Create backup
./scripts/backup-system.sh full

# Monitor system
./scripts/monitoring.sh
```

## 🎭 What Makes Zoe Unique

- **Learns your personality** and adapts responses
- **Contextual awareness** from journal entries and conversations
- **Proactive suggestions** based on patterns and habits
- **Modular architecture** - easily extend and customize
- **Privacy-first design** - your data never leaves home

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License

MIT License - feel free to customize for your needs!

---

**Built with ❤️ for the Raspberry Pi community**

*Zoe - Your AI companion that truly stays home* 🏠🤖
README_EOF

# Create .env.example
log "Creating .env.example..."
cat > .env.example << 'ENV_EOF'
# Zoe AI Assistant Configuration

# Core Settings
ZOE_VERSION=3.1
TIMEZONE=Australia/Perth
UI_PORT=8080
API_PORT=8000

# AI Configuration
OLLAMA_MODEL=llama3.2:3b
OLLAMA_HOST=zoe-ollama:11434

# Voice Settings
VOICE_ENABLED=true
STT_MODEL=whisper-base
TTS_VOICE=female

# Integration Settings
HOMEASSISTANT_ENABLED=false
HOMEASSISTANT_URL=http://homeassistant:8123
HOMEASSISTANT_TOKEN=your_token_here

N8N_ENABLED=false
N8N_WEBHOOK_URL=http://n8n:5678

MATRIX_ENABLED=false
MATRIX_HOMESERVER=matrix.org
MATRIX_USERNAME=@zoe:matrix.org

# Security
JWT_SECRET=change_this_in_production
ENV_EOF

# Initialize Git if needed
if [ ! -d ".git" ]; then
    log "Initializing Git..."
    git init
fi

# Configure Git
log "Configuring Git..."
git config user.name "jason-easyazz" 2>/dev/null || true
git config user.email "jason@easyazz.com" 2>/dev/null || true

# Add remote
if ! git remote get-url origin &>/dev/null; then
    git remote add origin "$REPO_URL"
    log "GitHub remote added"
fi

# Stage and commit
log "Staging files..."
git add .

log "Creating initial commit..."
git commit -m "🎉 Initial Zoe v3.1 AI Assistant Setup

🤖 Complete offline AI life hub featuring:
- 🗣️ Voice interface (Whisper + TTS)
- 🧠 Local AI with Ollama
- 📝 Personal journaling & memory
- 🏠 Smart home integration
- ⚡ Workflow automation
- 🔒 Privacy-first design

Ready to be your personal AI companion! 🏠❤️" || log "No changes to commit"

# Push to GitHub
log "Pushing to GitHub..."
git branch -M main
git push -u origin main

log "✅ Repository setup complete!"
info "🌐 Repository: https://github.com/jason-easyazz/zoe-ai-assistant"
info "🔧 Continue with: docker compose up -d"
