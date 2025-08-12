#!/bin/bash
# Zoe AI Assistant - Force Upload to GitHub
# Usage: ./upload-to-github.sh

set -e

echo "🚀 Starting Zoe upload to GitHub..."
echo "📍 Current directory: $(pwd)"
echo "⚠️  This will FORCE OVERWRITE everything in the repository!"
echo ""
read -p "Continue? (yes/no): " confirm

if [[ "$confirm" != "yes" ]]; then
    echo "❌ Upload cancelled"
    exit 1
fi

echo ""
echo "🔧 Setting up Git..."

# Setup git if needed
if [[ ! -d ".git" ]]; then
    echo "   Initializing git repository..."
    git init
else
    echo "   Git repository already exists"
fi

# Configure git
git config user.name "jason-easyazz"
git config user.email "jason@easyazz.com"

# Add GitHub remote (remove old one first if exists)
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/jason-easyazz/zoe-ai-assistant.git
echo "   GitHub remote configured"

# Create/update privacy-protecting .gitignore
echo ""
echo "🛡️  Creating/updating .gitignore for privacy protection..."
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

# Docker volumes & data
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

# Home Assistant specific
services/zoe-homeassistant/.storage/
services/zoe-homeassistant/config/.storage/
services/*/.*
*/.storage/
*/auth
*/auth_*

# Docker container generated files
services/*/data/
services/*/logs/
services/*/cache/
services/*/tmp/

# Permission-protected files
**/auth
**/token*
**/secret*
**/.storage/
**/.cloud/
**/.uuid
GITIGNORE_EOF

echo "   .gitignore updated"

# Stage and commit everything
echo ""
echo "📦 Staging all files..."
git add .

echo "💾 Creating commit..."
COMMIT_TIME=$(date '+%Y-%m-%d %H:%M:%S')
git commit -m "🎉 Zoe v3.1 - Complete AI Life Hub

🤖 Uploaded: $COMMIT_TIME

Features:
- 🗣️ Voice interface (Whisper + TTS)  
- 🧠 Local AI with Ollama (Pi 5 optimized)
- 📝 Personal journaling & memory system
- 🏠 Smart home integration (Home Assistant)
- ⚡ Workflow automation (n8n)
- 🔒 100% offline & privacy-first
- 📱 Modern web UI with real-time chat
- 🎯 Task & event management
- 📊 Mood tracking & analytics

Ready to be your personal AI companion! 🏠❤️"

# Force push to GitHub
echo ""
echo "🚀 Force pushing to GitHub..."
echo "   This will overwrite the remote repository..."
git branch -M main
git push --force origin main

echo ""
echo "✅ Upload complete!"
echo "🌐 Repository: https://github.com/jason-easyazz/zoe-ai-assistant"
echo "📱 View online: https://github.com/jason-easyazz/zoe-ai-assistant/tree/main"
echo ""
echo "🎯 Next time, just run: ./upload-to-github.sh"
