#!/bin/bash
# Zoe Master Enhancement Menu

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

show_menu() {
    clear
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     ZOE AI MASTER ENHANCEMENT MENU     ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
    echo "1) View System Status"
    echo "2) Test All Features"
    echo "3) View Container Logs"
    echo "4) Backup System"
    echo "5) Push to GitHub"
    echo "6) View Developer Dashboard"
    echo "7) Restart Services"
    echo "8) Run Health Checks"
    echo "9) Exit"
    echo ""
    echo -n "Choose option: "
}

while true; do
    show_menu
    read -r choice
    
    case $choice in
        1) docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe- ;;
        2) curl http://localhost:8000/health | jq '.' ;;
        3) docker logs zoe-core --tail 50 ;;
        4) tar -czf backup_$(date +%Y%m%d_%H%M%S).tar.gz services/ data/ ;;
        5) git add . && git commit -m "✅ Enhancement update" && git push ;;
        6) echo "Open: http://192.168.1.60:8080/developer/" ;;
        7) docker compose restart ;;
        8) ./scripts/health_check.sh ;;
        9) exit 0 ;;
        *) echo "Invalid option" ;;
    esac
    
    echo ""
    echo "Press Enter to continue..."
    read
done
