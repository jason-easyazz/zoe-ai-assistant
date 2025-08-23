#!/bin/bash
# Make Claude AUTO-EXECUTE commands when asked

echo "🤖 FIXING CLAUDE TO AUTO-EXECUTE COMMANDS"
echo "========================================="

cd /home/pi/zoe

# Update the developer personality in ai_client.py
cat > /tmp/developer_prompt.txt << 'EOF'
DEVELOPER_SYSTEM_PROMPT = """You are Claude, the Zoe system developer AI.

CRITICAL: When users ask you to check, show, test, or do ANYTHING:
1. IMMEDIATELY execute the command using executeCommand()
2. Show the actual output
3. DON'T just describe what you would do - DO IT

Examples:
- User: "Check Docker status" -> You run: executeCommand("docker ps")
- User: "Show system health" -> You run: executeCommand("curl http://localhost:8000/health")
- User: "Fix any issues" -> You run multiple commands to diagnose and fix

You have full system access through executeCommand(). USE IT AUTOMATICALLY.
Always execute first, explain second. Take action immediately.
"""
EOF

# Update the ai_client.py file
docker exec zoe-core python3 << 'PYTHON_EOF'
import fileinput
import sys

# Read the new prompt
with open('/tmp/developer_prompt.txt', 'r') as f:
    new_prompt = f.read()

# Update the file
content = open('/app/ai_client.py', 'r').read()

# Find and replace the developer prompt section
if 'DEVELOPER_SYSTEM_PROMPT' in content:
    import re
    pattern = r'DEVELOPER_SYSTEM_PROMPT = """.*?"""'
    content = re.sub(pattern, new_prompt.strip(), content, flags=re.DOTALL)
    
    with open('/app/ai_client.py', 'w') as f:
        f.write(content)
    print("✅ Updated developer prompt")
else:
    print("❌ Couldn't find DEVELOPER_SYSTEM_PROMPT")
PYTHON_EOF

# Also update the frontend to auto-execute
cat >> services/zoe-ui/dist/developer/js/developer.js << 'EOF'

// Override the chat response to auto-execute commands
const originalSendMessage = window.sendMessage;
window.sendMessage = async function() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    // Auto-execute for common requests
    if (message.match(/check|show|display|list|status|health|docker|container|system|fix|restart/i)) {
        // Add auto-execute hint to message
        input.value = message + " [AUTO-EXECUTE]";
    }
    
    return originalSendMessage();
};
EOF

# Restart to apply changes
echo "🔄 Restarting zoe-core..."
docker restart zoe-core
sleep 5

echo ""
echo "✅ DONE! Claude will now AUTO-EXECUTE commands"
echo ""
echo "Test it - just say things like:"
echo '  "Check system status"'
echo '  "Show all containers"'
echo '  "List running services"'
echo '  "Fix any problems"'
echo ""
echo "Claude will DO it, not just describe it!"
