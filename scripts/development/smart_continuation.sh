#!/bin/bash
# SMART CONTINUATION PROMPT GENERATOR
# This creates a prompt with your current state embedded

cd /home/pi/zoe

echo "🤖 Generating Continuation Prompt with Current State..."
echo "======================================================"

# Gather current state
CONTAINERS=$(docker ps --format "{{.Names}}" | grep zoe- | tr '\n' ', ' | sed 's/,$//')
LAST_COMMIT=$(git log --oneline -1)
DB_CONVERSATIONS=$(sqlite3 data/zoe.db "SELECT COUNT(*) FROM conversations;" 2>/dev/null || echo "0")
DB_EVENTS=$(sqlite3 data/zoe.db "SELECT COUNT(*) FROM events;" 2>/dev/null || echo "0")
IP_ADDR=$(hostname -I | awk '{print $1}')

# Create the prompt with embedded state
cat > SMART_CONTINUATION_PROMPT.txt << EOF
I need to continue working on my Zoe AI Assistant.

Current state from my system:
\`\`\`
📍 Location: /home/pi/zoe
🐙 GitHub: https://github.com/jason-easyazz/zoe-ai-assistant
🐳 Containers Running: ${CONTAINERS}
🧠 AI Model: llama3.2:3b (loaded in zoe-ollama)
🌐 Web UI: http://${IP_ADDR}:8080 (working)
🔌 API: http://${IP_ADDR}:8000 (working)
💾 Database Stats: ${DB_CONVERSATIONS} conversations, ${DB_EVENTS} events
📝 Last Commit: ${LAST_COMMIT}
⏰ State Generated: $(date)
\`\`\`

System Architecture:
- Docker containers with zoe- prefix
- Single docker-compose.yml
- SQLite database at data/zoe.db
- Services: FastAPI backend, Nginx frontend, Ollama AI, Redis cache

Please:
1. Load the Zoe project documents from your knowledge
2. Acknowledge the current state above
3. Show available enhancement options
4. Provide complete scripts with GitHub auto-sync

All scripts should:
- Start with: cd /home/pi/zoe
- End with: git push
- Include full file contents
- Test immediately after changes

What would you like me to work on next?
EOF

echo "✅ Smart prompt created!"
echo ""
echo "📋 TO START NEW CHAT:"
echo "1. Copy this command:"
echo "   cat /home/pi/zoe/SMART_CONTINUATION_PROMPT.txt"
echo ""
echo "2. Paste the output to Claude"
echo ""
echo "3. Claude will have your EXACT current state!"
