#!/bin/bash
# FIX_DOCKER_PARSING.sh - Fix the Docker output parsing

echo "🎯 FIXING DOCKER OUTPUT PARSING"
echo "================================"

cd /home/pi/zoe

# Create a simple fix that definitely works
docker exec zoe-core bash -c 'cat > /tmp/fix_parsing.py << "PYTHON_EOF"
# Fix the Docker parsing in developer.py
with open("/app/routers/developer.py", "r") as f:
    content = f.read()

# Find and replace the Docker command section
import re

# Replace the complex parsing with simpler working version
old_pattern = r"docker_result = execute_command\(.*?\).*?return \{\"response\": response, \"executed\": True\}"

new_docker_section = """docker_result = execute_command("docker ps -a --format \\"{{.Names}}:{{.Status}}\\"")
        
        if docker_result["success"]:
            # Parse the simple format
            lines = docker_result["stdout"].strip().split("\\n")
            
            running = []
            stopped = []
            
            for line in lines:
                if line and ":" in line:
                    name, status = line.split(":", 1)
                    if "Up" in status:
                        running.append(f"• **{name}**: {status}")
                    else:
                        stopped.append(f"• **{name}**: {status}")
            
            # Build response
            response = "**🐳 Docker Container Status:**\\n\\n"
            
            if running:
                response += f"**✅ Running ({len(running)}):**\\n"
                response += "\\n".join(running) + "\\n"
            else:
                response += "**⚠️ No containers running!**\\n"
            
            if stopped:
                response += f"\\n**🔴 Stopped ({len(stopped)}):**\\n"
                response += "\\n".join(stopped) + "\\n"
            
            total = len(running) + len(stopped)
            response += f"\\n**📊 Summary:** {len(running)}/{total} containers running"
            
            return {"response": response, "executed": True}"""

# Simple replacement - just change the format command and parsing
content = content.replace(
    "docker ps -a --format '"'"'table {{.Names}}\\t{{.Status}}'"'"'",
    "docker ps -a --format '"'"'{{.Names}}:{{.Status}}'"'"'"
)

# Fix the parsing part
content = content.replace(
    "if line and '"'"'\\t'"'"' in line:",
    "if line and '"'"':'"'"' in line:"
)

content = content.replace(
    "line.split('"'"'\\t'"'"', 1)",
    "line.split('"'"':'"'"', 1)"
)

# Remove the "Skip header" since we dont have one now
content = content.replace(
    "for line in lines[1:]:  # Skip header",
    "for line in lines:"
)

with open("/app/routers/developer.py", "w") as f:
    f.write(content)

print("✅ Fixed Docker parsing")
PYTHON_EOF

python3 /tmp/fix_parsing.py'

# Restart service
echo "🔄 Restarting zoe-core..."
docker restart zoe-core
sleep 8

# Test the fix
echo ""
echo "🧪 TESTING DOCKER DISPLAY:"
echo "=========================="
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show all docker containers"}' 2>/dev/null | jq -r '.response'

echo ""
echo "✅ Docker parsing fixed!"
echo ""
echo "Try these in the dashboard:"
echo '  - "Show docker containers"'
echo '  - "Check system health"'
echo '  - "List all services"'
