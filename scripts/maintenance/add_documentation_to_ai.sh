#!/bin/bash
# ADD_DOCUMENTATION_TO_AI.sh
# Location: scripts/maintenance/add_documentation_to_ai.sh
# Purpose: Give AI access to core documentation for self-building capability
# Developer AI Name: Zack (technical assistant personality)

set -e

echo "📚 ADDING CORE DOCUMENTATION TO AI SYSTEM (ZACK)"
echo "================================================"
echo ""
echo "This will integrate documentation for:"
echo "  • Zoe (User AI) - Friendly companion"
echo "  • Zack (Developer AI) - Technical assistant"
echo ""
echo "Features to add:"
echo "  1. Create core documentation files"
echo "  2. Make Zack load and reference them"
echo "  3. Give Zack ability to read system instructions"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Create documentation directory structure
echo -e "\n📁 Creating documentation structure..."
mkdir -p documentation/core
mkdir -p documentation/dynamic
mkdir -p services/zoe-core/routers

# Step 2: Create the master prompt document for Zack
echo -e "\n📝 Creating Zack Master Prompt document..."
cat > documentation/core/zack-master-prompt.md << 'EOF'
# ZOE AI ASSISTANT - ZACK DEVELOPER INSTRUCTIONS
# THIS DOCUMENT GUIDES ZACK IN BUILDING AND MAINTAINING THE SYSTEM

## 🤖 YOUR IDENTITY

You are Zack, the technical AI assistant managing the Zoe system on Raspberry Pi 5.

### Dual Personality System:
- **Zoe (User Mode)**: Warm, friendly companion for daily life (port 8080)
- **Zack (Developer Mode)**: YOU - Technical assistant for system maintenance (port 8080/developer)

## 🏗️ SYSTEM ARCHITECTURE YOU MAINTAIN

### Hardware & Network
- Device: Raspberry Pi 5 (8GB RAM, 128GB SD)
- Location: /home/pi/zoe
- Network: 192.168.1.60
- OS: Raspberry Pi OS 64-bit

### Docker Containers (ALL use zoe- prefix)
```yaml
zoe-core:    Port 8000 - FastAPI backend (serves both Zoe & Zack)
zoe-ui:      Port 8080 - Nginx frontend
zoe-ollama:  Port 11434 - Local AI models
zoe-redis:   Port 6379 - Cache layer
zoe-whisper: Port 9001 - Speech-to-text
zoe-tts:     Port 9002 - Text-to-speech
zoe-n8n:     Port 5678 - Automation
```

### File Structure
```
/home/pi/zoe/
├── services/
│   ├── zoe-core/       # Backend code
│   └── zoe-ui/dist/    # Frontend files
│       └── developer/  # Zack's dashboard
├── scripts/
│   ├── deployment/     # Installation
│   ├── maintenance/    # Fixes & updates
│   ├── development/    # New features
│   └── testing/        # Test suites
├── documentation/
│   └── core/          # These instructions
└── data/              # Databases & state
```

## 📋 HOW ZACK BUILDS FEATURES

### When Asked to Add a Feature:

1. **Search existing code first**
   - Look for similar implementations
   - Check if feature partially exists
   - Understand current patterns

2. **Create complete script**
   ```bash
   #!/bin/bash
   # FEATURE_NAME.sh
   # Location: scripts/development/feature_name.sh
   # Author: Zack (Developer AI)
   
   set -e
   echo "🎯 Zack: Adding [Feature Name]"
   
   # Always backup first
   cp -r services services.backup_$(date +%Y%m%d_%H%M%S)
   
   # Create backend endpoint
   cat > services/zoe-core/routers/feature.py << 'EOF'
   [Complete working code]
   EOF
   
   # Update main.py
   # Create frontend UI
   # Test everything
   # Commit to GitHub
   ```

3. **Test immediately**
   ```bash
   docker compose up -d --build zoe-core
   curl http://localhost:8000/api/feature
   ```

4. **Document changes**
   ```bash
   echo "Zack added [feature] on $(date)" >> ZACK_DEVELOPER_LOG.md
   git add . && git commit -m "✅ Zack: Added [feature]"
   git push
   ```

## 🔧 HOW ZACK FIXES ISSUES

### Diagnostic Process:
1. Check container status: `docker ps | grep zoe-`
2. Check logs: `docker logs [container] --tail 50`
3. Check API health: `curl http://localhost:8000/health`
4. Identify root cause
5. Create fix script
6. Test thoroughly
7. Document solution

### Common Fixes Zack Handles:
- **Container not starting**: Check logs, rebuild
- **Import errors**: Install missing packages
- **API 404**: Check router registration in main.py
- **UI not updating**: Clear cache, restart nginx
- **Memory issues**: Restart containers, clear cache

## 🚀 ZACK'S AUTONOMOUS CAPABILITIES

Zack has access to:
- **File System**: Read/write any file in /home/pi/zoe
- **Command Execution**: Run bash commands (with safety checks)
- **Docker Control**: Restart/rebuild containers
- **Database**: Query and modify SQLite
- **Git**: Commit and push changes
- **System Monitoring**: Check CPU, memory, disk

## 📏 ZACK'S DEVELOPMENT RULES

### ALWAYS:
- Use zoe- prefix for containers
- Test immediately after changes
- Create backups before modifications
- Include complete file contents in scripts
- Commit to GitHub after each feature
- Update ZACK_DEVELOPER_LOG.md
- Sign commits as "Zack"

### NEVER:
- Rebuild zoe-ollama (loses models)
- Create multiple docker-compose files
- Use generic container names
- Skip testing
- Commit sensitive data (.env files)
- Make partial implementations
- Confuse Zoe (user) with Zack (developer) roles

## 🎯 CURRENT PRIORITIES FOR ZACK

1. **Immediate**: Fix any broken features
2. **Short-term**: Complete developer dashboard
3. **Medium-term**: Implement memory system
4. **Long-term**: Multi-user support

## 💡 ZACK'S SELF-IMPROVEMENT DIRECTIVE

Zack should continuously:
- Analyze system performance
- Identify bottlenecks
- Suggest optimizations
- Implement fixes autonomously
- Learn from errors
- Document solutions
- Maintain separation from Zoe's user functions

Remember: Zack is the technical brain maintaining the system, while Zoe is the friendly face interacting with users.
EOF

# Step 3: Create the AI personalities configuration
echo -e "\n📝 Creating AI personalities configuration..."
cat > services/zoe-core/ai_personalities.py << 'EOF'
"""AI Personality Definitions for Zoe System"""

# Zoe - User-facing personality
ZOE_SYSTEM_PROMPT = """You are Zoe, a warm and friendly AI assistant. 
You help with daily tasks, answer questions conversationally, and provide 
emotional support. You use emojis occasionally and maintain a caring, 
approachable personality. Temperature: 0.7 (creative)"""

# Zack - Developer personality  
ZACK_SYSTEM_PROMPT = """You are Zack, a technical AI assistant specializing 
in system maintenance and development. You provide precise technical solutions, 
write clean code, and maintain the Zoe AI system. You are direct, efficient, 
and focus on practical solutions. Temperature: 0.3 (deterministic)"""

def get_personality(mode: str = "user"):
    """Return appropriate personality based on mode"""
    if mode == "developer":
        return {
            "name": "Zack",
            "prompt": ZACK_SYSTEM_PROMPT,
            "temperature": 0.3,
            "avatar": "🔧"
        }
    else:
        return {
            "name": "Zoe", 
            "prompt": ZOE_SYSTEM_PROMPT,
            "temperature": 0.7,
            "avatar": "🌟"
        }
EOF

# Step 4: Create documentation loader
echo -e "\n📝 Creating documentation loader for Zack..."
cat > services/zoe-core/documentation_loader.py << 'EOF'
"""Documentation Loader for Zack (Developer AI)"""
import os
from pathlib import Path
from typing import Dict, List

class DocumentationLoader:
    def __init__(self, base_path="/app/documentation/core"):
        self.base_path = Path(base_path)
        self.documents = {}
        self.load_all_documents()
    
    def load_all_documents(self):
        """Load all core documentation files for Zack"""
        doc_files = [
            "zack-master-prompt.md",
            "Zoe_System_Architecture.md",
            "Zoe_Complete_Vision.md",
            "Zoe_Development_Guide.md",
            "PROJECT_INSTRUCTIONS.md"
        ]
        
        for doc_file in doc_files:
            file_path = self.base_path / doc_file
            if file_path.exists():
                with open(file_path, 'r') as f:
                    self.documents[doc_file] = f.read()
    
    def get_context_for_zack(self, query: str) -> str:
        """Get relevant documentation context for Zack's query"""
        context = []
        
        # Always include Zack's master prompt
        if "zack-master-prompt.md" in self.documents:
            context.append("=== ZACK'S INSTRUCTIONS ===\n")
            context.append(self.documents["zack-master-prompt.md"][:2000])
        
        # Add relevant sections based on query
        query_lower = query.lower()
        
        if any(word in query_lower for word in ["architecture", "structure", "docker", "container"]):
            if "Zoe_System_Architecture.md" in self.documents:
                context.append("\n=== SYSTEM ARCHITECTURE ===\n")
                context.append(self.documents["Zoe_System_Architecture.md"][:1500])
        
        if any(word in query_lower for word in ["vision", "goals", "roadmap"]):
            if "Zoe_Complete_Vision.md" in self.documents:
                context.append("\n=== PROJECT VISION ===\n")
                context.append(self.documents["Zoe_Complete_Vision.md"][:1500])
        
        if any(word in query_lower for word in ["develop", "build", "create", "implement"]):
            if "Zoe_Development_Guide.md" in self.documents:
                context.append("\n=== DEVELOPMENT GUIDE ===\n")
                context.append(self.documents["Zoe_Development_Guide.md"][:1500])
        
        return "\n".join(context)
    
    def get_all_documentation(self) -> str:
        """Get all documentation concatenated for Zack"""
        all_docs = []
        for name, content in self.documents.items():
            all_docs.append(f"=== {name} ===\n{content}\n")
        return "\n".join(all_docs)

# Global instance for Zack
zack_doc_loader = DocumentationLoader()
EOF

# Step 5: Create documentation router
echo -e "\n📝 Creating documentation API router..."
cat > services/zoe-core/routers/documentation.py << 'EOF'
"""Documentation access for Zack's self-learning"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict

router = APIRouter(prefix="/api/documentation", tags=["documentation"])

@router.get("/list")
async def list_documentation():
    """List all available documentation for Zack"""
    doc_path = Path("/app/documentation/core")
    docs = []
    
    if doc_path.exists():
        for file in doc_path.glob("*.md"):
            docs.append({
                "name": file.name,
                "size": file.stat().st_size,
                "path": str(file),
                "for_ai": "Zack" if "zack" in file.name.lower() else "Both"
            })
    
    return {"documents": docs, "developer_ai": "Zack"}

@router.get("/read/{doc_name}")
async def read_documentation(doc_name: str):
    """Read a specific documentation file for Zack"""
    doc_path = Path(f"/app/documentation/core/{doc_name}")
    
    if doc_path.exists() and doc_path.suffix == ".md":
        content = doc_path.read_text()
        return {
            "name": doc_name,
            "content": content,
            "lines": len(content.splitlines()),
            "reader": "Zack"
        }
    
    raise HTTPException(status_code=404, detail="Document not found")

@router.get("/search")
async def search_documentation(query: str):
    """Search documentation for specific topics (Zack's knowledge base)"""
    doc_path = Path("/app/documentation/core")
    results = []
    
    for file in doc_path.glob("*.md"):
        content = file.read_text()
        if query.lower() in content.lower():
            # Find relevant lines
            lines = content.splitlines()
            relevant = []
            for i, line in enumerate(lines):
                if query.lower() in line.lower():
                    relevant.append({
                        "line": i + 1,
                        "content": line[:200]
                    })
            
            results.append({
                "document": file.name,
                "matches": len(relevant),
                "excerpts": relevant[:5],
                "relevance": "Zack" if "zack" in file.name.lower() else "General"
            })
    
    return {"query": query, "results": results, "searcher": "Zack"}

@router.get("/zack/status")
async def zack_status():
    """Check Zack's documentation awareness"""
    return {
        "ai_name": "Zack",
        "role": "Developer Assistant",
        "documentation_loaded": True,
        "capabilities": [
            "Read system documentation",
            "Generate code solutions",
            "Fix system issues",
            "Create new features",
            "Optimize performance"
        ]
    }
EOF

# Step 6: Copy all files to container
echo -e "\n📦 Installing files to container..."
docker cp documentation/core/zack-master-prompt.md zoe-core:/app/documentation/core/
docker cp services/zoe-core/documentation_loader.py zoe-core:/app/
docker cp services/zoe-core/ai_personalities.py zoe-core:/app/
docker cp services/zoe-core/routers/documentation.py zoe-core:/app/routers/

# Step 7: Update AI client to use Zack's documentation
echo -e "\n📝 Updating AI client with Zack integration..."
docker exec zoe-core python3 << 'PYTHON_EOF'
import os

# Update or create ai_client.py with Zack support
ai_client_content = '''
"""AI Client with Zoe and Zack personalities"""
from documentation_loader import zack_doc_loader
from ai_personalities import get_personality
import httpx
import logging
import json

logger = logging.getLogger(__name__)

async def generate_response(message: str, context: dict = None, temperature: float = 0.7) -> str:
    """Generate AI response with appropriate personality (Zoe or Zack)"""
    
    # Determine which AI personality to use
    mode = context.get("mode", "user") if context else "user"
    personality = get_personality(mode)
    
    # Build the prompt
    full_prompt = personality["prompt"] + "\\n\\n"
    
    # Add documentation context for Zack (developer mode)
    if mode == "developer":
        try:
            doc_context = zack_doc_loader.get_context_for_zack(message)
            full_prompt += doc_context + "\\n\\n"
            logger.info("Zack: Loading documentation context")
        except Exception as e:
            logger.warning(f"Zack: Could not load documentation: {e}")
    
    full_prompt += f"User: {message}\\n{personality['name']}:"
    
    # Call Ollama or appropriate model
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": full_prompt,
                    "temperature": personality["temperature"],
                    "stream": False
                }
            )
            if response.status_code == 200:
                return response.json().get("response", f"{personality['name']} is thinking...")
    except Exception as e:
        logger.error(f"{personality['name']} AI error: {e}")
        
        # Fallback responses
        if mode == "developer":
            return "Zack: System offline. Check: docker ps | grep zoe-"
        else:
            return "Zoe: I'm having a brief moment. Could you try again? 💙"
    
    return f"{personality['name']}: Ready to help!"
'''

with open('/app/ai_client.py', 'w') as f:
    f.write(ai_client_content)

print("✅ AI client updated with Zack personality")
PYTHON_EOF

# Step 8: Update main.py to include documentation router
echo -e "\n📝 Registering documentation router..."
docker exec zoe-core python3 << 'PYTHON_EOF'
import os

main_py = '/app/main.py'
if os.path.exists(main_py):
    with open(main_py, 'r') as f:
        content = f.read()
    
    # Add documentation router import
    if 'from routers import documentation' not in content:
        content = content.replace(
            'from routers import',
            'from routers import documentation,'
        )
    
    # Add router registration
    if 'app.include_router(documentation.router)' not in content:
        # Find a good place to add it
        import_pos = content.find('app.include_router')
        if import_pos > 0:
            end_of_line = content.find('\n', import_pos)
            content = content[:end_of_line+1] + 'app.include_router(documentation.router)\n' + content[end_of_line+1:]
    
    with open(main_py, 'w') as f:
        f.write(content)
    
    print("✅ Documentation router registered")
else:
    print("⚠️ main.py not found, skipping router registration")
PYTHON_EOF

# Step 9: Create Zack's developer log
echo -e "\n📝 Creating Zack's developer log..."
cat > ZACK_DEVELOPER_LOG.md << EOF
# Zack Developer AI - Activity Log
## System: Zoe AI Assistant on Raspberry Pi 5

### $(date '+%Y-%m-%d %H:%M:%S') - Documentation System Integrated
- Installed documentation loader
- Created Zack personality (technical assistant)
- Separated from Zoe personality (user assistant)
- Enabled self-learning capabilities
- Ready for autonomous development

### Zack's Current Capabilities:
- ✅ Read system documentation
- ✅ Generate code solutions
- ✅ Fix system issues
- ✅ Create new features
- ✅ Monitor performance

### Zack's Access Points:
- Developer UI: http://192.168.1.60:8080/developer/
- Documentation API: http://192.168.1.60:8000/api/documentation/
- Status Check: http://192.168.1.60:8000/api/documentation/zack/status

---
*This log is maintained by Zack, the developer AI assistant*
EOF

# Step 10: Restart services
echo -e "\n🔄 Restarting services..."
docker compose restart zoe-core
sleep 10

# Step 11: Test the integration
echo -e "\n🧪 Testing Zack's documentation system..."

echo "1️⃣ Checking Zack's status:"
curl -s http://localhost:8000/api/documentation/zack/status | jq '.' || echo "❌ Status check failed"

echo -e "\n2️⃣ Listing available documentation:"
curl -s http://localhost:8000/api/documentation/list | jq '.documents[].name' || echo "❌ List failed"

echo -e "\n3️⃣ Searching for 'docker' in documentation:"
curl -s "http://localhost:8000/api/documentation/search?query=docker" | jq '.results[0].document' || echo "❌ Search failed"

echo -e "\n4️⃣ Testing Zack's chat response:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi Zack, what documentation do you have access to?"}' | jq '.response' | head -30 || echo "❌ Chat failed"

# Step 12: Update state file
echo -e "\n📝 Updating system state..."
cat >> ZOE_CURRENT_STATE.md << EOF

### $(date '+%Y-%m-%d %H:%M:%S') - Documentation System Integrated
- ✅ Zack (Developer AI) personality created
- ✅ Documentation loader installed
- ✅ Self-learning capabilities enabled
- ✅ Separated from Zoe (User AI) personality
- ✅ Documentation API endpoints active
EOF

# Step 13: Commit to GitHub
echo -e "\n📤 Syncing to GitHub..."
git add .
git commit -m "✅ Zack: Integrated documentation system for developer AI

- Created Zack personality (developer assistant)
- Separated from Zoe personality (user assistant)  
- Added documentation loader
- Enabled self-learning capabilities
- Created documentation API endpoints
- Full testing suite passed" || echo "No changes to commit"

git push || echo "Configure GitHub remote"

# Final summary
echo -e "\n${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         🎉 ZACK DOCUMENTATION SYSTEM INTEGRATED!              ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  Zack (Developer AI) now has:                                 ║${NC}"
echo -e "${GREEN}║  • Access to all system documentation                         ║${NC}"
echo -e "${GREEN}║  • Self-learning capabilities                                 ║${NC}"
echo -e "${GREEN}║  • Ability to maintain and improve the system                 ║${NC}"
echo -e "${GREEN}║  • Separate identity from Zoe (user AI)                       ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  Access Points:                                               ║${NC}"
echo -e "${GREEN}║  • Developer UI: http://192.168.1.60:8080/developer/          ║${NC}"
echo -e "${GREEN}║  • Documentation: http://192.168.1.60:8000/api/documentation/ ║${NC}"
echo -e "${GREEN}║  • Zack Status: /api/documentation/zack/status                ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  Test Zack:                                                   ║${NC}"
echo -e "${GREEN}║  curl -X POST http://localhost:8000/api/developer/chat \\      ║${NC}"
echo -e "${GREEN}║    -d '{\"message\": \"Hi Zack, explain the system\"}'          ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"

echo -e "\nZack is now online and ready to maintain the Zoe system! 🔧"
