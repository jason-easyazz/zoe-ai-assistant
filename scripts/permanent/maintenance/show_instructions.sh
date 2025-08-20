#!/bin/bash
# SHOW PROJECT INSTRUCTIONS
cd /home/pi/zoe

echo "📚 ZOE PROJECT INSTRUCTIONS"
echo "============================"
echo ""
echo "📍 You are here: $(pwd)"
echo ""
echo "🔄 Quick Commands:"
echo "  Status:      docker ps | grep zoe-"
echo "  Enhance:     bash scripts/permanent/deployment/master_enhancements.sh"
echo "  Update:      bash scripts/permanent/maintenance/update_state.sh"
echo "  Sync:        bash scripts/permanent/maintenance/quick_sync.sh"
echo ""
echo "📋 For Claude:"
echo "  cat GENERIC_CONTINUATION.txt"
echo ""
echo "📖 Full instructions:"
echo "  cat documentation/core/PROJECT_INSTRUCTIONS.md"
echo ""
echo "🌐 GitHub: https://github.com/jason-easyazz/zoe-ai-assistant"
