#!/bin/bash
# Zoe v3.1 - Master Enhancement Menu
set -e

cd /home/pi/zoe

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

clear
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     🤖 ZOE ENHANCEMENT MENU 🤖        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo "Choose an enhancement to install:"
echo ""
echo -e "${GREEN}QUICK WINS (30 minutes):${NC}"
echo "  1) 🎨 Enhanced Glass-Morphic UI"
echo "  2) 📊 System Status Dashboard"
echo "  3) ⚡ Quick Actions Panel"
echo ""
echo -e "${GREEN}POWER FEATURES (1-2 hours):${NC}"
echo "  4) 📅 Natural Language Calendar"
echo "  5) 🛠️ Developer Dashboard (/developer/)"
echo "  6) 🧠 Memory System"
echo ""
echo -e "${GREEN}ADVANCED:${NC}"
echo "  7) 🎙️ Voice Integration (Whisper + TTS)"
echo "  8) 🏠 Home Assistant Integration"
echo "  9) 📝 Advanced Task Management"
echo ""
echo -e "${YELLOW}UTILITIES:${NC}"
echo "  S) 📊 Show Current Status"
echo "  B) 💾 Create Backup"
echo "  G) 🐙 Sync to GitHub"
echo "  Q) 🚪 Quit"
echo ""
read -p "Enter choice [1-9,S,B,G,Q]: " choice

case $choice in
    1)
        echo "🎨 Installing Enhanced UI..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shenhance_ui.sh
        ;;
    2)
        echo "📊 Installing Status Dashboard..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shstatus_dashboard.sh
        ;;
    3)
        echo "⚡ Installing Quick Actions..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shquick_actions.sh
        ;;
    4)
        echo "📅 Installing Natural Language Calendar..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shcalendar_nlp.sh
        ;;
    5)
        echo "🛠️ Installing Developer Dashboard..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shdeveloper_dashboard.sh
        ;;
    6)
        echo "🧠 Installing Memory System..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shmemory_system.sh
        ;;
    7)
        echo "🎙️ Installing Voice Integration..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shvoice_integration.sh
        ;;
    8)
        echo "🏠 Installing Home Assistant..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shhome_assistant.sh
        ;;
    9)
        echo "📝 Installing Task Management..."
        bash scripts/enhancements/ && bash scripts/permanent/maintenance/quick_sync.shtask_management.sh
        ;;
    S|s)
        echo "📊 Current Status:"
        docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-
        echo ""
        echo "Latest conversations:"
        sqlite3 data/zoe.db "SELECT datetime(timestamp, 'localtime'), user_message FROM conversations ORDER BY id DESC LIMIT 3;"
        ;;
    B|b)
        echo "💾 Creating backup..."
        BACKUP_NAME="backup_$(date +%Y%m%d_%H%M%S)"
        mkdir -p backups
        cp -r services data docker-compose.yml backups/$BACKUP_NAME/
        echo "✅ Backup created: backups/$BACKUP_NAME/"
        ;;
    G|g)
        echo "🐙 Syncing to GitHub..."
        git add .
        git commit -m "🔄 Update: $(date)" || true
        git push
        echo "✅ GitHub synced"
        ;;
    Q|q)
        echo "👋 Goodbye!"
        exit 0
        ;;
    *)
        echo "❌ Invalid choice"
        ;;
esac

echo ""
echo "Press Enter to return to menu..."
read
exec "$0"
