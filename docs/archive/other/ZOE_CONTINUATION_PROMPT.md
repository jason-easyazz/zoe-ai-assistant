# 🤖 ZOE AI ASSISTANT - CONTINUATION PROMPT

## 🎯 PROJECT OVERVIEW
You are continuing work on the **Zoe AI Assistant** - a privacy-first AI assistant for Raspberry Pi 5 with local AI, voice interface, smart home integration, and workflow automation.

## 📍 CURRENT SYSTEM STATE

### ✅ WHAT'S WORKING:
- **7 Docker containers** all running (zoe-core, zoe-ui, zoe-ollama, zoe-redis, zoe-whisper, zoe-tts, zoe-n8n)
- **Memory System** fully functional (people, projects, relationships)
- **Voice Services** 90% working (TTS needs audio quality fix)
- **API** all endpoints operational
- **Task Management** with Zack AI developer
- **GitHub** synced at: https://github.com/jason-easyazz/zoe-ai-assistant

### 🔧 WHAT NEEDS WORK:
1. **TTS Audio Quality** - Final fix for Whisper accuracy
2. **Developer Dashboard** - Claude API integration needed
3. **Dashboard Backend** - Connect to real system data
4. **N8N Workflows** - Need configuration

## 🏗️ SYSTEM ARCHITECTURE

### Core Infrastructure
- **Platform**: Raspberry Pi 5 (8GB RAM, ARM64)
- **Location**: `/home/pi/zoe`
- **Network**: 192.168.1.60
- **GitHub**: https://github.com/jason-easyazz/zoe-ai-assistant

### Docker Services (ALL use zoe- prefix)
```yaml
zoe-core:8000      # FastAPI backend
zoe-ui:8080        # Nginx frontend  
zoe-ollama:11434   # Local AI (llama3.2:3b)
zoe-redis:6379     # Cache layer
zoe-whisper:9001   # Speech-to-text
zoe-tts:9002       # Text-to-speech
zoe-n8n:5678       # Automation workflows
```

## 🚀 MANDATORY STARTUP SEQUENCE

```bash
# 1. Check GitHub for latest state
cd /home/pi/zoe
git pull
cat ZOE_CURRENT_STATE.md

# 2. Verify all services running
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# 3. Test API health
curl http://localhost:8000/health | jq '.'
```

## 📋 DEVELOPMENT WORKFLOW

### Before Starting Work:
1. **Check GitHub**: https://github.com/jason-easyazz/zoe-ai-assistant
2. **Read ZOE_CURRENT_STATE.md** for latest status
3. **Run status check**: `docker ps | grep zoe-`

### After Each Step:
1. **Test immediately** after changes
2. **Commit to GitHub**:
   ```bash
   git add .
   git commit -m "✅ [What was done]"
   git push
   ```
3. **Update state**: `bash scripts/permanent/maintenance/update_state.sh`

## 🛠️ KEY FILES TO KNOW

### Core Files (DON'T break these!)
- `services/zoe-core/routers/developer.py` - Zack AI developer
- `services/zoe-core/main.py` - FastAPI app entry
- `services/zoe-core/ai_client.py` - AI response generation
- `services/zoe-ui/dist/developer/index.html` - Developer dashboard
- `docker-compose.yml` - Service configuration

### Current Working Endpoints
- `POST /api/developer/chat` - Zack's intelligent chat
- `GET /api/developer/metrics` - Real-time metrics
- `POST /api/developer/execute` - Command execution
- `GET/POST /api/developer/tasks` - Task management
- `GET /api/developer/status` - System status

## ⚠️ CRITICAL RULES

### NEVER DO:
- ❌ Rebuild zoe-ollama (loses model, takes hours)
- ❌ Commit .env file (contains API keys)
- ❌ Create multiple docker-compose files
- ❌ Use container names without zoe- prefix
- ❌ Skip testing after changes
- ❌ Modify production without backup

### ALWAYS DO:
- ✅ Create timestamped backups before changes
- ✅ Test in development first
- ✅ Use zoe- prefix for all containers
- ✅ Document changes in ZOE_CURRENT_STATE.md
- ✅ Run `git status` before commits
- ✅ Use organized script folders

## 🎯 IMMEDIATE PRIORITIES

### Priority 1: Complete TTS Fix
```bash
# The fix script exists, just needs to run:
./fix_tts_quality.sh
./test_voice_quality.sh
```

### Priority 2: Developer Dashboard Claude Integration
- Template installed at: `/services/zoe-ui/dist/developer/index.html`
- Beautiful glass-morphic UI ready
- Needs: Claude API key configuration
- Needs: Backend endpoints for Claude chat

### Priority 3: Dashboard Features to Add
- [ ] Real-time service health monitoring
- [ ] Claude chat with context awareness
- [ ] Script execution interface
- [ ] Task queue management
- [ ] System logs viewer
- [ ] Memory system browser
- [ ] Voice service controls

## 🔧 QUICK COMMANDS

```bash
# Check status
docker ps | grep zoe-

# Update state for AI
bash scripts/permanent/maintenance/update_state.sh

# Quick sync to GitHub
bash scripts/permanent/maintenance/quick_sync.sh

# Run enhancement menu
bash scripts/permanent/deployment/master_enhancements.sh

# Start new AI chat
cat ZOE_ZOE_CONTINUATION_PROMPT.md
```

## 📚 DOCUMENTATION STRUCTURE

```
/home/pi/zoe/
├── documentation/
│   ├── core/
│   │   └── PROJECT_INSTRUCTIONS.md    # Master reference
│   ├── dynamic/
│   │   ├── current_state.md           # Auto-updated
│   │   ├── proven_solutions.md        # What works
│   │   └── things_to_avoid.md         # What fails
│   └── ZOE_CURRENT_STATE.md          # Current status
├── services/
│   ├── zoe-core/                      # Backend API
│   └── zoe-ui/                        # Frontend
├── scripts/
│   ├── deployment/                    # Install scripts
│   ├── maintenance/                   # Sync scripts
│   └── development/                   # Feature scripts
└── ZOE_ZOE_CONTINUATION_PROMPT.md        # This file
```

## 💡 SUCCESS PATTERNS

### Pattern 1: Real System Analysis
```python
# Instead of generic responses, analyze actual system
analysis = analyze_for_optimization()  # Gets real metrics
response_parts.append(f"CPU Usage: {analysis['metrics']['cpu_percent']}%")  # Real data
```

### Pattern 2: Intelligent Responses
```python
# Check for actual issues before recommending
if memory.percent > 80:
    recommendations.append("Restart memory-intensive containers")
else:
    recommendations.append("Memory usage is healthy")
```

### Pattern 3: Executable Solutions
```python
# Provide actual commands users can run
response_parts.append("```bash")
response_parts.append("docker system prune -a --volumes")
response_parts.append("```")
```

## 🎯 YOUR FIRST RESPONSE SHOULD:

1. Acknowledge you understand this is Zoe's AI Assistant system
2. Confirm you see the current working state
3. Ask which enhancement to implement first:
   - TTS audio quality fix
   - Developer Dashboard Claude integration
   - Real-time monitoring
   - Task management improvements
4. Use the successful patterns from previous sessions
5. Create executable scripts for any changes

---

**CRITICAL:** This is Zoe's system, not Claude's. Always reference Zoe's current state and work within the established patterns that have proven successful.
